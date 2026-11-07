"""Where ``dg speak`` writes an audio file (``--out-file``, or the deprecated
``-o/--output``).

Two halves:

- :func:`plan_target` runs **before the request**. It ``lstat``s the path
  and decides how the file will be written, or why it can't be: an
  existing regular file or symlink without ``--force`` (``exists``); a
  directory, or a parent that is missing or unwritable (``unwritable``).
- :class:`AudioFile` opens the file **when the first chunk arrives**, so a
  failed request or an empty stream leaves nothing behind, and removes
  whatever it created when the run fails.

How a target is written (``Target.kind``):

``new``      ``O_CREAT | O_EXCL``, mode 0666 & ~umask. ``O_EXCL`` is the
             race guard behind the ``lstat`` check.
``replace``  ``--force`` (or ``-o``) over an existing file or symlink: a
             0600 temp file in the same directory, given the old file's
             permission bits (never setuid, setgid or sticky; 0666 & ~umask
             in place of a symlink) and ``os.replace``d
             over the path at the end. A symlink is replaced, never written
             through; a failed run leaves the old file. The result is a new
             inode, so hard links to the old file keep the old bytes.
``special``  a character device or FIFO (``/dev/null``, a named pipe):
             no ``O_EXCL``, no ``--force``. It is opened **before** the
             request, non-blocking (a FIFO with no reader is refused, not
             waited on), and the opened file must be the one that was
             checked, so a path swapped for a regular file is never
             written. Block devices and sockets are refused.
``stdout``   the same file as stdout (``--out-file /dev/stdout``, final
             check): the audio goes to fd 1 and nothing else does. On
             the first chunk fd 1 must still be the file that was checked;
             the path is not opened again.

Also refused before the request: the same file as stderr, where dg's own
messages go, and ``--force`` over a /dev/fd or /proc/<pid>/fd entry.
"""

from __future__ import annotations

import errno
import os
import re
import signal
import stat
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass(frozen=True)
class Target:
    path: str
    kind: str  # "new" | "replace" | "special" | "stdout"
    #: ``replace`` over a regular file: its permission bits, kept.
    mode: int | None = None
    #: ``special``: the descriptor opened and checked before the request.
    fd: int | None = None
    #: ``stdout``: (st_dev, st_ino) of the file that was checked.
    ident: tuple[int, int] | None = None


@dataclass(frozen=True)
class Refusal:
    """Why a target can't be written, decided before the request."""

    reason: str  # "exists" | "unwritable"
    error: OSError | None = None


class Exists(Exception):
    """The file appeared between the check and the open (``O_EXCL``)."""


class WriteFailed(Exception):
    """Writing the file (or stdout) failed; ``.error`` says why."""

    def __init__(self, error: OSError, *, stdout: bool) -> None:
        super().__init__(error)
        self.error = error
        self.stdout = stdout


class ReaderGone(Exception):
    """The reader of a pipe went away (EPIPE)."""


def _stderr_stat() -> os.stat_result | None:
    try:
        return os.fstat(sys.stderr.fileno())
    except Exception:
        return None


def stdout_stat() -> os.stat_result | None:
    """``fstat`` of stdout, or ``None`` when it can't be read."""
    try:
        return os.fstat(sys.stdout.fileno())
    except Exception:
        return None


def same_file(a: os.stat_result, b: os.stat_result) -> bool:
    """``os.path.samestat``, except that a file with no index is never the
    same as another: Windows reports index 0 for every pipe, console and
    device, so two of them would always match."""
    return a.st_ino != 0 and os.path.samestat(a, b)


def stdout_takes_audio() -> bool:
    """True when stdout is a terminal, a regular file or the null device:
    the destinations where agent mode still writes audio."""
    out = stdout_stat()
    if out is None:
        return False
    if stat.S_ISREG(out.st_mode):
        return True
    if out.st_ino == 0:
        # Windows: a pipe, a console or NUL, with nothing to compare. The
        # console and NUL are character devices; a pipe is not.
        return stat.S_ISCHR(out.st_mode)
    try:
        if same_file(out, os.stat(os.devnull)):
            return True
        return os.isatty(sys.stdout.fileno())
    except Exception:
        return False


def _oserror(code: int, path: str) -> OSError:
    return OSError(code, os.strerror(code), path)


def _read_umask() -> int:
    """The process umask, read once when this module is imported.

    Linux publishes it in /proc. Elsewhere (macOS) the only call that reads
    it also sets it, so ``os.umask`` is used only while this is the sole
    thread, which it is when the command module loads. If another thread
    exists, the umask is measured instead: a throwaway file is created
    with mode 0o777 and the bits the kernel removed are the umask.
    """
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("Umask:"):
                    return int(line.split()[1], 8)
    except (OSError, ValueError, IndexError):
        pass
    if threading.active_count() == 1:
        mask = os.umask(0o022)
        os.umask(mask)
        return mask
    import tempfile

    probe = os.path.join(tempfile.gettempdir(), f".deepctl-umask-{os.getpid()}")
    try:
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o777)
        try:
            return 0o777 & ~stat.S_IMODE(os.fstat(fd).st_mode)
        finally:
            os.close(fd)
            os.unlink(probe)
    except OSError:
        return 0o022


_UMASK = _read_umask()

# A new file: O_EXCL is the race guard behind the lstat check. O_BINARY
# (Windows) so a 0x0A in the audio is never written as 0x0D 0x0A.
_NEW_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)

# The file types written as they are: character devices and FIFOs.
# These follow "Couldn't write the output to FILE: " (details.reason).
_NOT_SPECIAL = "it isn't a file, a pipe or a device like /dev/null"
_CHANGED = "something replaced it before writing started"
_STDERR = "it's stderr, which carries this command's messages"
_FD_PATH = "--force can't replace a file descriptor path"
_NO_READER = "no program is reading from that pipe"

# Directories whose entries are open file descriptors: /dev/fd and
# /proc/<pid>/fd. A temp file can't be created there, so --force can't
# replace what they name.
_FD_DIR = re.compile(r"/proc/(?:self|thread-self|\d+)(?:/task/\d+)?/fd")

# ── Critical sections ───────────────────────────────────────────────────
# Creating, committing and removing the file must not be cut short by a
# signal, or a temp file or a 0-byte file could be left behind. Inside a
# critical section (main thread only):
#
# - SIGINT, SIGTERM and SIGHUP are blocked for this thread with
#   pthread_sigmask where it exists;
# - a handler that knows about sections (marked ``_deepctl_defers``, like
#   speak's signal guard) defers them through ``defer_signal``; any other
#   handler, including the default action, is swapped for a recorder for
#   the duration, since another thread can take the signal and Python then
#   runs the handler on the main thread;
# - when the outermost section ends, the most severe signal that arrived
#   (SIGTERM, then SIGHUP, then SIGINT; the rest are dropped) is raised
#   again with ``signal.raise_signal``, which never kills the process the
#   way ``os.kill`` on our own pid does on Windows.
#
# A signal ignored with SIG_IGN, or held by a handler Python can't put
# back (``getsignal`` returns None), is left alone.

_PRIORITY = tuple(
    sig
    for sig in (
        getattr(signal, "SIGTERM", None),
        getattr(signal, "SIGHUP", None),
        getattr(signal, "SIGINT", None),
    )
    if sig is not None
)
_critical = 0
_deferred: list[int] = []


def _record(signum: int, _frame: object) -> None:
    _deferred.append(signum)


_record._deepctl_defers = True  # type: ignore[attr-defined]


def most_severe(signals: list[int]) -> int | None:
    """The signal to act on from several: SIGTERM > SIGHUP > SIGINT."""
    for sig in _PRIORITY:
        if sig in signals:
            return int(sig)
    return signals[0] if signals else None


def _set_mask(how: int) -> set[int] | None:
    if not hasattr(signal, "pthread_sigmask"):
        return None
    try:
        return set(signal.pthread_sigmask(how, _PRIORITY))
    except (OSError, ValueError):
        return None


@contextmanager
def critical_section() -> Iterator[None]:
    global _critical
    main = threading.current_thread() is threading.main_thread()
    # Count first: a pending handler that runs inside the calls below then
    # defers instead of raising, so the cleanup in ``finally`` always runs.
    _critical += 1
    blocked: set[int] | None = None
    swapped: dict[int, Any] = {}
    try:
        if main and _critical == 1:
            blocked = _set_mask(signal.SIG_BLOCK)
            for sig in _PRIORITY:
                current = signal.getsignal(sig)
                if current is signal.SIG_IGN or current is None:
                    continue
                if getattr(current, "_deepctl_defers", False):
                    continue
                signal.signal(sig, _record)
                swapped[sig] = current
        yield
    finally:
        try:
            restore_handlers(swapped)
        finally:
            try:
                if blocked is not None:
                    # Restored while still inside the section, so a handler
                    # that knows about sections defers rather than raising
                    # past this. The call sets the mask, then runs pending
                    # handlers; one that raises still leaves the mask set.
                    signal.pthread_sigmask(signal.SIG_SETMASK, blocked)
            finally:
                _critical -= 1
                if not _critical and _deferred:
                    signum = most_severe(_deferred)
                    _deferred.clear()
                    if signum is not None and main:
                        signal.raise_signal(signum)


def restore_handlers(saved: dict[int, Any]) -> None:
    """Put every saved handler back. ``signal.signal`` runs a pending
    handler before it switches; if that handler raises, the switch didn't
    happen, so it is retried, and the exception (that signal, delivered)
    is raised once every handler is back."""
    caught: BaseException | None = None
    for sig, previous in saved.items():
        for _ in range(3):
            try:
                signal.signal(sig, previous)
                break
            except BaseException as exc:
                caught = caught or exc
    if caught is not None:
        raise caught


def defer_signal(signum: int) -> bool:
    """For a signal handler: inside a critical section, remember the signal
    (the most severe one is raised again when the section ends) and return
    True."""
    if not _critical:
        return False
    _deferred.append(signum)
    return True


def _is_special(mode: int) -> bool:
    return stat.S_ISCHR(mode) or stat.S_ISFIFO(mode)


def _open_special(path: str, checked: os.stat_result) -> Target | Refusal:
    """Open a device or FIFO now, before the request, and make sure the
    opened file is the one that was checked.

    Non-blocking, so a FIFO with no reader fails with ENXIO instead of
    hanging; blocking mode is restored for the writes.
    """
    flags = (
        os.O_WRONLY
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOCTTY", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno == errno.ENXIO and stat.S_ISFIFO(checked.st_mode):
            # The OS text ("No such device or address") is misleading here.
            exc = OSError(errno.ENXIO, _NO_READER, path)
        return Refusal("unwritable", exc)
    try:
        opened = os.fstat(fd)
        if not os.path.samestat(opened, checked) or not _is_special(opened.st_mode):
            raise OSError(errno.EEXIST, _CHANGED, path)
        if getattr(os, "O_NONBLOCK", 0):
            # Only where it was opened non-blocking: on Windows there is no
            # O_NONBLOCK, and set_blocking works only on pipes (and not at
            # all before Python 3.12).
            os.set_blocking(fd, True)
    except OSError as exc:
        os.close(fd)
        return Refusal("unwritable", exc)
    return Target(path, "special", fd=fd)


def _parent_refusal(path: str) -> Refusal | None:
    """A missing, non-directory or unwritable parent directory."""
    parent = os.path.dirname(os.path.abspath(path))
    try:
        st = os.stat(parent)
    except OSError as exc:
        return Refusal("unwritable", exc)
    if not stat.S_ISDIR(st.st_mode):
        return Refusal("unwritable", _oserror(errno.ENOTDIR, parent))
    if not os.access(parent, os.W_OK | os.X_OK):
        return Refusal("unwritable", _oserror(errno.EACCES, parent))
    return None


def plan_target(path: str, *, overwrite: bool) -> Target | Refusal:
    """Decide, before the request, how ``path`` will be written.

    ``overwrite`` is ``--force`` for ``--out-file`` and always true for the
    deprecated ``-o``, which keeps 0.4.0's overwrite.
    """
    try:
        followed: os.stat_result | None = os.stat(path)
    except OSError:
        followed = None
    if followed is not None:
        err = _stderr_stat()
        if err is not None and same_file(followed, err):
            # Audio would be mixed with dg's own messages.
            return Refusal("unwritable", OSError(errno.EINVAL, _STDERR, path))
        out = stdout_stat()
        if out is not None and same_file(followed, out):
            return Target(path, "stdout", ident=(followed.st_dev, followed.st_ino))

    try:
        st = os.lstat(path)
    except FileNotFoundError:
        refusal = _parent_refusal(path)
        return refusal or Target(path, "new")
    except OSError as exc:
        return Refusal("unwritable", exc)

    # What the path names: the link's target for a symlink.
    named = followed if stat.S_ISLNK(st.st_mode) else st
    if named is not None and stat.S_ISDIR(named.st_mode):
        return Refusal("unwritable", _oserror(errno.EISDIR, path))
    if named is not None and _is_special(named.st_mode):
        # /dev/null, a named pipe, or a link to one (/dev/stdout).
        return _open_special(path, named)
    if named is not None and not stat.S_ISREG(named.st_mode):
        # A block device or a socket.
        return Refusal("unwritable", OSError(errno.EINVAL, _NOT_SPECIAL, path))

    # An existing regular file, or a symlink to one (or to nothing).
    if not overwrite:
        return Refusal("exists")
    parent = os.path.dirname(os.path.abspath(path))
    if parent == "/dev/fd" or _FD_DIR.fullmatch(os.path.realpath(parent)):
        return Refusal("unwritable", OSError(errno.EINVAL, _FD_PATH, path))
    # Permission bits only: never setuid, setgid or sticky.
    keep = None if stat.S_ISLNK(st.st_mode) else stat.S_IMODE(st.st_mode) & 0o777
    return _parent_refusal(path) or Target(path, "replace", mode=keep)


class AudioFile:
    """An audio file opened on the first chunk and removed on failure.

    ``write`` raises :class:`WriteFailed` (or :class:`ReaderGone` for EPIPE)
    so a failed write can't be mistaken for a failed request.
    """

    def __init__(self, target: Target) -> None:
        self.target = target
        self.bytes_written = 0
        # A device or FIFO was opened (and checked) before the request.
        self._fd: int | None = target.fd
        self._tmp: str | None = None
        self._created = False
        self._done = False

    @property
    def path(self) -> str:
        return self.target.path

    @property
    def is_stdout(self) -> bool:
        return self.target.kind == "stdout"

    @property
    def opened(self) -> bool:
        return self._fd is not None or (self.is_stdout and self._created)

    def open(self) -> None:
        """Create or open the file. Idempotent."""
        if self.opened:
            return
        kind, path = self.target.kind, self.target.path
        try:
            if kind == "new":
                with critical_section():
                    try:
                        fd = os.open(path, _NEW_FLAGS, 0o666)
                    except FileExistsError as exc:
                        raise Exists(path) from exc
                    self._fd, self._created = fd, True
            elif kind == "replace":
                import tempfile

                directory, name = os.path.split(os.path.abspath(path))
                with critical_section():
                    self._fd, self._tmp = tempfile.mkstemp(
                        dir=directory, prefix=f".{name}.", suffix=".part"
                    )
            elif kind == "special":
                raise OSError(errno.EBADF, _CHANGED, path)  # opened in plan
            else:
                self._check_stdout()
        except (Exists, WriteFailed):
            raise
        except OSError as exc:
            raise WriteFailed(exc, stdout=False) from exc

    def _check_stdout(self) -> None:
        """stdout must still be the file that was checked. The audio
        goes to fd 1 itself; the path is never opened again."""
        out = stdout_stat()
        if out is None or (out.st_dev, out.st_ino) != self.target.ident:
            # It changed since the check; don't write into it unasked.
            raise Exists(self.target.path)
        self._created = True

    def write(self, data: bytes) -> None:
        """Write ``data`` in full (opening the file first if needed)."""
        if not data:
            return
        self.open()
        if self.is_stdout:
            out = sys.stdout.buffer
            try:
                out.write(data)
                out.flush()
            except BrokenPipeError as exc:
                raise ReaderGone from exc
            except OSError as exc:
                raise WriteFailed(exc, stdout=True) from exc
            self.bytes_written += len(data)
            return
        assert self._fd is not None
        view = memoryview(data)
        try:
            while view:
                n = os.write(self._fd, view)
                view = view[n:]
        except BrokenPipeError as exc:
            raise ReaderGone from exc
        except OSError as exc:
            raise WriteFailed(exc, stdout=False) from exc
        self.bytes_written += len(data)

    def commit(self) -> None:
        """Finish the file: close it, and put a temp file in place. Not
        done until the rename succeeds; a failure removes what we made."""
        if self._done:
            return
        with critical_section():
            fd, self._fd = self._fd, None
            try:
                if fd is not None:
                    try:
                        if self._tmp is not None and hasattr(os, "fchmod"):
                            # An existing file keeps its permission bits;
                            # a new file or a replaced symlink gets 0666 &
                            # ~umask, as open() would. (No fchmod on
                            # Windows before Python 3.13: the temp file's
                            # attributes stay.)
                            mode = self.target.mode
                            os.fchmod(fd, 0o666 & ~_UMASK if mode is None else mode)
                    finally:
                        os.close(fd)
                if self._tmp is not None:
                    os.replace(self._tmp, self.target.path)
                    self._tmp = None
            except OSError as exc:
                self.abort()
                raise WriteFailed(exc, stdout=False) from exc
            self._done = True

    def abort(self) -> None:
        """Close and remove whatever this run created. Never raises; safe
        to call again, and a temp file is always removed."""
        if self._done:
            return
        with critical_section():
            fd, self._fd = self._fd, None
            ours: os.stat_result | None = None
            if fd is not None:
                try:
                    ours = os.fstat(fd)
                except OSError:
                    pass
                try:
                    os.close(fd)
                except OSError:
                    pass
            if self._tmp is not None:
                try:
                    os.unlink(self._tmp)
                except OSError:
                    pass
                self._tmp = None
            elif self._created and self.target.kind == "new" and ours is not None:
                # Only the file this run created: if the path now names
                # another file (renamed or replaced meanwhile), leave it
                # alone.
                try:
                    if same_file(os.lstat(self.target.path), ours):
                        os.unlink(self.target.path)
                except OSError:
                    pass
            self._done = True
