"""Where a result too large to print in an agent session is kept.

``save()`` writes the full payload and its plain transcript as a pair of
files, ``{UTC %Y%m%dT%H%M%SZ}-{8 hex}.json`` and ``.txt``, mode 0600,
created with ``O_CREAT|O_EXCL`` and fsynced before it returns. The pair
goes in ``{state_dir}/results/`` (0700). If that can't be written, it goes
in ``{tempdir}/deepctl-{euid}/results/``: that directory is used only when
it is a real directory owned by this user that nobody else can write (a
group- or other-readable one is tightened to 0700); otherwise a fresh
``mkdtemp`` directory is used for this run and one warning names the
refused path. The same goes for a ``results`` entry there that isn't such
a directory: a FIFO, a device or a symlink is never opened for reading or
followed. Files are opened relative to the checked directory with
``O_NOFOLLOW``. A partial file is removed before the next location is
tried. Each write prunes its directory: pairs older than 24 hours, then
the oldest while the directory holds more than 500 MB.

On Windows the pair goes in ``{state_dir}/results/``, else in
``{tempdir}/deepctl/results/`` (the temp directory is already per-user);
a directory that is a symlink or junction is refused there too.
"""

from __future__ import annotations

import os
import re
import secrets
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

RESULTS = "results"
# The ctx.meta key the root --full-output sets; dg listen reads it.
FULL_OUTPUT_META = "deepctl.full_output"
MAX_AGE_S = 24 * 3600
MAX_TOTAL_BYTES = 500 * 1000 * 1000

_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_BINARY = getattr(os, "O_BINARY", 0)
# A FIFO opened for reading waits for a writer; with this it doesn't.
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_DIR_FLAGS = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_NONBLOCK
_NAME = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}\.(json|txt)$")


class ResultStoreError(OSError):
    """Neither the state directory nor the temp directory took the files."""

    def __init__(self, directory: Path) -> None:
        super().__init__(f"couldn't save the result in {directory}")
        self.directory = directory


@dataclass(frozen=True)
class Saved:
    """Absolute paths of the files ``save()`` wrote."""

    full_result_path: Path
    transcript_path: Path


def _posix() -> bool:
    return os.name != "nt" and os.open in os.supports_dir_fd


def _stem() -> str:
    return f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{secrets.token_hex(4)}"


# ── POSIX: everything relative to a checked directory descriptor ─────


def _checked_dir_fd(path: Path, *, create: bool, tighten: bool = True) -> int | None:
    """An fd for ``path`` when it is a real directory owned by this user
    that nobody else can write (tightened to 0700 unless ``tighten`` is
    false); else ``None``."""
    if create:
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass
        except OSError:
            return None
    try:
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            return None
        fd = os.open(path, _DIR_FLAGS)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if (
            not stat.S_ISDIR(st.st_mode)
            or st.st_uid != os.geteuid()
            or st.st_mode & 0o022
        ):
            os.close(fd)
            return None
        if tighten and st.st_mode & 0o077:
            os.fchmod(fd, 0o700)
        return fd
    except OSError:
        os.close(fd)
        return None


def _results_fd(parent_fd: int) -> int | None:
    try:
        os.mkdir(RESULTS, 0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    except OSError:
        return None
    try:
        # A FIFO, device or symlink here is refused before it is opened.
        st = os.stat(RESULTS, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(st.st_mode):
            return None
        fd = os.open(RESULTS, _DIR_FLAGS, dir_fd=parent_fd)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if (
            not stat.S_ISDIR(st.st_mode)
            or st.st_uid != os.geteuid()
            or st.st_mode & 0o022
        ):
            os.close(fd)
            return None
        if st.st_mode & 0o077:
            os.fchmod(fd, 0o700)
        return fd
    except OSError:
        os.close(fd)
        return None


def _write_all(fd: int, data: bytes) -> None:
    """Write all of ``data`` (``os.write`` may take part of it) and fsync."""
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]
    os.fsync(fd)


def _write_at(dir_fd: int, name: str, data: bytes) -> None:
    fd = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_BINARY,
        0o600,
        dir_fd=dir_fd,
    )
    try:
        _write_all(fd, data)
    finally:
        os.close(fd)


def _save_at(dir_fd: int, full: bytes, text: bytes) -> str | None:
    stem = _stem()
    # Names this call created: a partial file is removed, while one that
    # was already there (O_EXCL refused it) is never touched.
    created: list[str] = []
    try:
        for suffix, data in ((".json", full), (".txt", text)):
            name = stem + suffix
            created.append(name)
            try:
                _write_at(dir_fd, name, data)
            except FileExistsError:
                created.pop()
                raise
    except OSError:
        for name in created:
            try:
                os.unlink(name, dir_fd=dir_fd)
            except OSError:
                pass
        return None
    try:
        os.fsync(dir_fd)
    except OSError:
        pass  # not every file system syncs a directory
    _prune_at(dir_fd, keep=stem)
    return stem


def _prune_at(dir_fd: int, keep: str) -> None:
    _prune(
        lambda: os.listdir(dir_fd),
        lambda name: os.stat(name, dir_fd=dir_fd, follow_symlinks=False),
        lambda name: os.unlink(name, dir_fd=dir_fd),
        keep,
    )


def _prune(
    listdir: Callable[[], list[str]],
    lstat: Callable[[str], os.stat_result],
    unlink: Callable[[str], None],
    keep: str,
) -> None:
    """Drop pairs older than ``MAX_AGE_S``, then the oldest while the
    directory holds more than ``MAX_TOTAL_BYTES``; never ``keep``. Only
    regular files with dg's own names are touched."""
    try:
        names = [n for n in listdir() if _NAME.match(n)]
    except OSError:
        return
    pairs: dict[str, list[tuple[str, os.stat_result]]] = {}
    for name in names:
        try:
            st = lstat(name)
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode):
            pairs.setdefault(name.rsplit(".", 1)[0], []).append((name, st))
    now = time.time()

    def drop(stem: str) -> None:
        for name, _ in pairs.pop(stem, []):
            try:
                unlink(name)
            except OSError:
                pass

    for stem in list(pairs):
        if stem != keep and all(now - st.st_mtime > MAX_AGE_S for _, st in pairs[stem]):
            drop(stem)
    oldest = sorted(
        (s for s in pairs if s != keep),
        key=lambda s: min(st.st_mtime for _, st in pairs[s]),
    )
    total = sum(st.st_size for files in pairs.values() for _, st in files)
    for stem in oldest:
        if total <= MAX_TOTAL_BYTES:
            break
        total -= sum(st.st_size for _, st in pairs[stem])
        drop(stem)


class _Refused(Exception):
    """The directory, or its ``results`` entry, isn't a private directory."""

    def __init__(self, path: Path) -> None:
        super().__init__(str(path))
        self.path = path


def _save_under(
    base: Path, base_fd: int | None, full: bytes, text: bytes
) -> Saved | None:
    """Save the pair in ``base/results``; ``None`` when a write failed.

    Raises :class:`_Refused` naming what was refused: ``base`` when
    ``base_fd`` is ``None``, else a ``results`` entry that isn't a private
    directory (a FIFO, say).
    """
    if base_fd is None:
        raise _Refused(base)
    try:
        fd = _results_fd(base_fd)
    finally:
        os.close(base_fd)
    if fd is None:
        raise _Refused(base / RESULTS)
    try:
        stem = _save_at(fd, full, text)
    finally:
        os.close(fd)
    if stem is None:
        return None
    results = base.resolve() / RESULTS
    return Saved(results / f"{stem}.json", results / f"{stem}.txt")


def _save_posix(
    state: Path, full: bytes, text: bytes, warn: Callable[[str], None]
) -> Saved:
    import tempfile

    # 1. {state_dir}/results/
    try:
        os.makedirs(state, 0o700, exist_ok=True)
    except OSError:
        pass
    try:
        saved = _save_under(
            state, _checked_dir_fd(state, create=False, tighten=False), full, text
        )
    except _Refused:
        saved = None
    if saved is not None:
        return saved
    # 2. {tempdir}/deepctl-{euid}/results/, or a fresh mkdtemp directory
    # when that (or its results entry) is refused.
    tmp = Path(tempfile.gettempdir()) / f"deepctl-{os.geteuid()}"
    try:
        saved = _save_under(tmp, _checked_dir_fd(tmp, create=True), full, text)
    except _Refused as refused:
        try:
            fresh = Path(tempfile.mkdtemp(prefix=f"deepctl-{os.geteuid()}-"))
        except OSError:
            raise ResultStoreError(state) from None
        warn(str(refused.path))
        try:
            saved = _save_under(fresh, _checked_dir_fd(fresh, create=False), full, text)
        except _Refused:
            saved = None
    if saved is not None:
        return saved
    raise ResultStoreError(state)


# ── Windows: the temp directory is already per-user ──────────────────


def _is_plain_dir(path: Path) -> bool:
    """A directory that is not a symlink, junction or other reparse point."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    attrs = getattr(st, "st_file_attributes", 0)
    reparse = attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode) and not reparse


def _save_paths(directory: Path, full: bytes, text: bytes) -> Saved | None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    if not _is_plain_dir(directory):
        return None
    stem = _stem()
    paths = [directory / f"{stem}.json", directory / f"{stem}.txt"]
    created: list[Path] = []  # as in _save_at
    try:
        for path, data in zip(paths, (full, text), strict=True):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_BINARY, 0o600)
            created.append(path)
            try:
                _write_all(fd, data)
            finally:
                os.close(fd)
    except OSError:
        for path in created:
            try:
                path.unlink()
            except OSError:
                pass
        return None
    _prune(
        lambda: os.listdir(directory),
        lambda name: os.lstat(directory / name),
        lambda name: os.unlink(directory / name),
        stem,
    )
    return Saved(paths[0].resolve(), paths[1].resolve())


def save(
    full: bytes,
    text: bytes,
    *,
    state: Path,
    warn: Callable[[str], None] | None = None,
) -> Saved:
    """Write ``full`` (the payload stdout would have had) and ``text`` (the
    plain transcript) as a pair; raise :class:`ResultStoreError` naming
    ``state`` when no location takes them. ``warn`` gets the path of a temp
    directory that was refused (another user's, or a symlink)."""
    if _posix():
        return _save_posix(state, full, text, warn or (lambda _refused: None))
    import tempfile

    for directory in (
        state / RESULTS,
        Path(tempfile.gettempdir()) / "deepctl" / RESULTS,
    ):
        saved = _save_paths(directory, full, text)
        if saved is not None:
            return saved
    raise ResultStoreError(state)


def stdout_is_file_or_null() -> bool:
    """True when stdout is a regular file or the null device: a result
    redirected there is never compacted."""
    try:
        st = os.fstat(sys.stdout.fileno())
    except (AttributeError, OSError, ValueError):
        return False
    if stat.S_ISREG(st.st_mode):
        return True
    if stat.S_ISCHR(st.st_mode):
        if sys.platform == "win32":
            # Every character device reports device number 0 there: NUL
            # is the one that isn't a console.
            return not _windows_console(sys.stdout.fileno())
        try:
            return st.st_rdev == os.stat(os.devnull).st_rdev
        except OSError:
            return False
    return False


def _windows_console(fd: int) -> bool:
    """Whether ``fd`` is a Windows console (GetConsoleMode succeeds on a
    console and fails on NUL). True when it can't be told."""
    try:
        import ctypes
        import msvcrt

        handle = msvcrt.get_osfhandle(fd)  # type: ignore[attr-defined,unused-ignore]
        mode = ctypes.c_uint32()
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
        return bool(kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    except Exception:
        return True
