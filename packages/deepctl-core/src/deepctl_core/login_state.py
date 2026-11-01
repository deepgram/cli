"""dg's state directory and the pending device-login store.

``state_dir()`` is the one helper every state file goes through:
``~/.deepctl`` in 0.5.0; a later release repoints it.

``dg login`` in an agent session asks for a device code, saves it here and
returns at once; ``dg login --complete`` polls once with it. The file holds
the device code, its user code, link, poll interval, expiry, the identity
host and the profile. Never a key.
"""

from __future__ import annotations

import dataclasses
import errno
import json
import math
import os
import secrets
import stat
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

PENDING_NAME = "login-pending.json"
# The shortest poll interval dg uses, whatever the server says: the server allows
# 60 token requests per 5 minutes per address, and 6 s leaves headroom.
MIN_POLL_INTERVAL = 6
_MAX_PENDING_BYTES = 8192
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_O_BINARY = getattr(os, "O_BINARY", 0)

# Why a pending file fails the agent-session trust test; the
# values are msg.W_CFG_UNTRUSTED_WHY keys.
WHY_SYMLINK = "symlink"
WHY_OWNER = "owner"
WHY_WRITABLE = "writable"
WHY_NOT_REGULAR = "not_regular"
WHY_LINKS = "links"


def state_dir() -> Path:
    """dg's state directory (``~/.deepctl`` in 0.5.0)."""
    return Path.home() / ".deepctl"


class StateDirError(OSError):
    """The state directory can't hold a pending login (``state_unwritable``)."""


def unwritable_dir(directory: Path, file: Path | None = None) -> Path | None:
    """``directory`` when dg couldn't create or write a file in it, else None.

    Checks without creating anything: the nearest existing ancestor must be
    a directory this process may write and search. ``file`` (an existing
    file in ``directory`` that dg would rewrite in place) also counts as
    writable.
    """
    if file is not None:
        try:
            if os.path.isfile(file) and os.access(file, os.W_OK):
                return None
        except OSError:
            pass
    probe = directory
    while True:
        try:
            st = os.stat(probe)
        except FileNotFoundError:
            parent = probe.parent
            if parent == probe:
                return directory
            probe = parent
            continue
        except OSError:
            return directory
        if not stat.S_ISDIR(st.st_mode):
            return directory
        return None if os.access(probe, os.W_OK | os.X_OK) else directory


@dataclass(frozen=True)
class PendingLogin:
    """A device code waiting for approval."""

    device_code: str
    user_code: str
    verification_uri: str
    interval: int
    expires_at: float
    host: str
    profile: str

    def expired(self, now: float | None = None) -> bool:
        return (time.time() if now is None else now) >= self.expires_at

    def expires_in(self, now: float | None = None) -> int:
        left = self.expires_at - (time.time() if now is None else now)
        return max(0, int(left))


def _from_json(data: object) -> PendingLogin | None:
    if not isinstance(data, dict):
        return None
    try:
        pending = PendingLogin(
            device_code=data["device_code"],
            user_code=data["user_code"],
            verification_uri=data["verification_uri"],
            interval=data["interval"],
            expires_at=data["expires_at"],
            host=data["host"],
            profile=data["profile"],
        )
    except (KeyError, TypeError):
        return None
    strings = (
        pending.device_code,
        pending.user_code,
        pending.verification_uri,
        pending.host,
        pending.profile,
    )
    if not all(isinstance(value, str) and value for value in strings):
        return None
    interval = pending.interval
    if (
        not isinstance(interval, (int, float))
        or isinstance(interval, bool)
        or interval != interval  # NaN
        or interval < 0
        or interval == float("inf")
    ):
        return None  # not a usable interval: no pending login
    pending = dataclasses.replace(
        pending, interval=max(MIN_POLL_INTERVAL, math.ceil(interval))
    )
    if not isinstance(pending.expires_at, (int, float)) or isinstance(
        pending.expires_at, bool
    ):
        return None
    return pending


def _open_dir(directory: Path, *, create: bool) -> int:
    """Open ``directory`` (made 0700 when ``create``) and check it.

    It must be a directory owned by this user; one the user can't keep
    private is refused. Group or other bits on the user's own directory are
    removed, so the pending file is in a 0700 directory.
    """
    if create:
        try:
            os.makedirs(directory.parent, exist_ok=True)
            os.mkdir(directory, 0o700)
        except FileExistsError:
            pass
    fd = os.open(directory, os.O_RDONLY | _O_DIRECTORY)
    try:
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode):
            raise StateDirError(errno.ENOTDIR, "not a directory", str(directory))
        if sys.platform != "win32":
            if st.st_uid != os.geteuid():
                raise StateDirError(
                    errno.EPERM, "owned by another user", str(directory)
                )
            if create and stat.S_IMODE(st.st_mode) & 0o077:
                os.fchmod(fd, 0o700)
    except BaseException:
        os.close(fd)
        raise
    return fd


def save_pending(pending: PendingLogin) -> Path:
    """Write ``pending`` to ``{state_dir}/login-pending.json`` at 0600.

    The state directory is created 0700. The new content goes to a fresh
    0600 file (``O_CREAT|O_EXCL|O_NOFOLLOW``) next to the target and
    replaces it atomically, so a reader never sees half a file and a
    planted symlink is never followed. Raises OSError (StateDirError for a
    directory dg can't use).
    """
    directory = state_dir()
    target = directory / PENDING_NAME
    data = (json.dumps(asdict(pending), indent=2) + "\n").encode("utf-8")
    tmp_name = f".{PENDING_NAME}.{secrets.token_hex(8)}.tmp"
    dir_fd = _open_dir(directory, create=True)
    use_dir_fd = sys.platform != "win32" and os.open in os.supports_dir_fd
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_BINARY
        if use_dir_fd:
            fd = os.open(tmp_name, flags, 0o600, dir_fd=dir_fd)
        else:
            fd = os.open(directory / tmp_name, flags, 0o600)
        try:
            try:
                if hasattr(os, "fchmod"):
                    os.fchmod(fd, 0o600)
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view) :]
                os.fsync(fd)
            finally:
                os.close(fd)
            if use_dir_fd:
                os.replace(tmp_name, PENDING_NAME, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            else:
                os.replace(directory / tmp_name, target)
        except BaseException:
            try:
                if use_dir_fd:
                    os.unlink(tmp_name, dir_fd=dir_fd)
                else:
                    os.unlink(directory / tmp_name)
            except OSError:
                pass
            raise
    finally:
        os.close(dir_fd)
    return target


@dataclass(frozen=True)
class LoadedPending:
    """What ``load_pending`` found: the login, or why the file was ignored."""

    pending: PendingLogin | None
    path: Path
    ignored_why: str | None = None
    identity: tuple[int, int] | None = None


def load_pending(*, agent: bool) -> LoadedPending:
    """Read the pending login, if there is a usable one.

    The file is opened without following a symlink and must be a regular
    file. In an AI agent session (``agent``) it must also be owned by this
    user, with one link and no group or other write bit;
    otherwise it is ignored (``ignored_why`` says why) and left in place.
    A missing or malformed file is simply absent.
    """
    path = state_dir() / PENDING_NAME
    try:
        fd = os.open(path, os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK | _O_BINARY)
    except FileNotFoundError:
        return LoadedPending(None, path)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            return LoadedPending(None, path, WHY_SYMLINK if agent else None)
        if agent and sys.platform != "win32":
            # e.g. EACCES: say so when the file belongs to someone else.
            try:
                if os.lstat(path).st_uid != os.geteuid():
                    return LoadedPending(None, path, WHY_OWNER)
            except OSError:
                pass
        return LoadedPending(None, path)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return LoadedPending(None, path, WHY_NOT_REGULAR if agent else None)
        if agent and sys.platform != "win32":
            why = None
            if st.st_uid != os.geteuid():
                why = WHY_OWNER
            elif stat.S_IMODE(st.st_mode) & 0o022:
                why = WHY_WRITABLE
            elif st.st_nlink != 1:
                why = WHY_LINKS
            if why is not None:
                return LoadedPending(None, path, why)
        raw = os.read(fd, _MAX_PENDING_BYTES + 1)
    finally:
        os.close(fd)
    identity = (st.st_dev, st.st_ino)
    if len(raw) > _MAX_PENDING_BYTES:
        return LoadedPending(None, path, identity=identity)
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        return LoadedPending(None, path, identity=identity)
    return LoadedPending(_from_json(data), path, identity=identity)


def remove_pending(loaded: LoadedPending) -> None:
    """Delete the pending file ``loaded`` came from, if it is still that file.

    A ``dg login`` replacing the file between the check and the unlink loses
    its new code; the cost is one more ``dg login``.
    """
    if loaded.identity is None:
        return
    try:
        st = os.lstat(loaded.path)
    except OSError:
        return
    if (st.st_dev, st.st_ino) != loaded.identity:
        return  # replaced by a newer dg login meanwhile: keep it
    try:
        os.unlink(loaded.path)
    except FileNotFoundError:
        pass
