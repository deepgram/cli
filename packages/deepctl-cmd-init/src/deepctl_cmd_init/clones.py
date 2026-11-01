"""dg init's record of the clones it made, in dg's own state directory.

A rerun of ``dg init`` resumes in a directory only when this record names
it: nothing inside the directory (which anyone could have packed into an
archive) is trusted for that. Each record is one owner-only file under
``{state_dir}/init-clones/`` (0700), named after the directory's real path,
holding that path, the template, its repository, the commit dg cloned and
the phases done, and the clone directory's device and inode, so a
directory deleted and made again at the same path is not taken for it.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from dataclasses import asdict, dataclass
from typing import Any

from deepctl_core.login_state import StateDirError, _open_dir, state_dir

DIR_NAME = "init-clones"
_MAX_BYTES = 16 * 1024
_COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_BINARY = getattr(os, "O_BINARY", 0)


@dataclass(frozen=True)
class CloneRecord:
    path: str
    template: str
    url: str
    commit: str
    phases: list[str]
    dev: int
    ino: int


def _name(real_path: str) -> str:
    return hashlib.sha256(real_path.encode("utf-8", "surrogateescape")).hexdigest()


def _use_dir_fd() -> bool:
    return sys.platform != "win32" and os.open in os.supports_dir_fd


def _open_records(*, create: bool) -> int:
    """Open the records directory inside the state directory, both checked:
    real directories owned by this user, the records directory 0700."""
    parent = _open_dir(state_dir(), create=create)
    try:
        if create:
            try:
                if _use_dir_fd():
                    os.mkdir(DIR_NAME, 0o700, dir_fd=parent)
                else:
                    os.mkdir(state_dir() / DIR_NAME, 0o700)
            except FileExistsError:
                pass
        flags = os.O_RDONLY | _DIRECTORY | _NOFOLLOW
        if _use_dir_fd():
            fd = os.open(DIR_NAME, flags, dir_fd=parent)
        else:
            fd = os.open(state_dir() / DIR_NAME, flags)
    finally:
        os.close(parent)
    try:
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode):
            raise StateDirError(errno.ENOTDIR, "not a directory", DIR_NAME)
        if sys.platform != "win32":
            if st.st_uid != os.geteuid():
                raise StateDirError(errno.EPERM, "owned by another user", DIR_NAME)
            if stat.S_IMODE(st.st_mode) & 0o077:
                if not create:
                    raise StateDirError(errno.EPERM, "not private", DIR_NAME)
                os.fchmod(fd, 0o700)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _valid(data: Any, real_path: str) -> CloneRecord | None:
    if not isinstance(data, dict) or data.get("path") != real_path:
        return None
    template, url, commit = data.get("template"), data.get("url"), data.get("commit")
    phases = data.get("phases")
    if not (isinstance(template, str) and isinstance(url, str)):
        return None
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        return None
    if not isinstance(phases, list) or not all(isinstance(p, str) for p in phases):
        return None
    dev, ino = data.get("dev"), data.get("ino")
    if not isinstance(dev, int) or isinstance(dev, bool):
        return None
    if not isinstance(ino, int) or isinstance(ino, bool):
        return None
    return CloneRecord(real_path, template, url, commit, list(phases), dev, ino)


def load(real_path: str) -> CloneRecord | None:
    """The record for ``real_path``, or None when there is no usable one.

    Only an owner-only regular file with one link, opened without following
    a link or blocking on a FIFO, in the checked records directory.
    """
    try:
        dir_fd = _open_records(create=False)
    except OSError:
        return None
    name = _name(real_path) + ".json"
    try:
        flags = os.O_RDONLY | _NOFOLLOW | _NONBLOCK | _BINARY
        if _use_dir_fd():
            fd = os.open(name, flags, dir_fd=dir_fd)
        else:
            fd = os.open(state_dir() / DIR_NAME / name, flags)
    except OSError:
        os.close(dir_fd)
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > _MAX_BYTES:
            return None
        if sys.platform != "win32" and (
            st.st_uid != os.geteuid()
            or stat.S_IMODE(st.st_mode) & 0o077
            or st.st_nlink != 1
        ):
            return None
        raw = os.read(fd, _MAX_BYTES + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
        os.close(dir_fd)
    try:
        return _valid(json.loads(raw.decode("utf-8")), real_path)
    except ValueError:
        return None


def save(record: CloneRecord) -> None:
    """Write ``record`` atomically: a new 0600 file (``O_CREAT|O_EXCL|
    O_NOFOLLOW``) next to the target, then renamed over it. Raises OSError
    (StateDirError for a state directory dg can't use)."""
    data = (json.dumps(asdict(record), indent=2) + "\n").encode("utf-8")
    name = _name(record.path) + ".json"
    tmp = f".{name}.{secrets.token_hex(8)}.tmp"
    dir_fd = _open_records(create=True)
    base = state_dir() / DIR_NAME
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _BINARY
        if _use_dir_fd():
            fd = os.open(tmp, flags, 0o600, dir_fd=dir_fd)
        else:
            fd = os.open(base / tmp, flags, 0o600)
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
            if _use_dir_fd():
                os.replace(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            else:
                os.replace(base / tmp, base / name)
        except BaseException:
            try:
                if _use_dir_fd():
                    os.unlink(tmp, dir_fd=dir_fd)
                else:
                    os.unlink(base / tmp)
            except OSError:
                pass
            raise
    finally:
        os.close(dir_fd)
