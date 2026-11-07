"""When the update checks last ran, kept in dg's cache directory.

Each check has one small JSON file there, ``update-check.json`` for dg itself
and ``plugin-update-check.json`` for plugins, holding ``checked_at`` (Unix
time), ``ok``, ``latest`` and ``source``, plus ``notified_at`` and
``notified_latest`` once a notice was shown (``claim_notice``). A check writes
its file (``ok`` false) just before it sends its request, and again when it
gets an answer. A check that got its answer waits a full interval before the
next one; one that failed, or never finished because dg exited first (the
check runs in a daemon thread and is never waited for), is tried again after
``RETRY_SECONDS``, or the interval if that is shorter. So short commands
don't use up the interval without a check ever completing, and an unreachable
index isn't asked on every run.

The user config file is never written here. The directory is 0700 and must be
a real directory owned by this user: :func:`usable` tightens one this user
owns that its group or others can reach (``mkdir`` under umask 002 leaves
0775), and reports anything else as unusable, in which case the checks don't
run at all. Files are 0600 and replaced atomically (``deepctl._cache``). A
file that isn't a regular 0600-style file owned by this user, or any error,
reads as "no state".
"""

from __future__ import annotations

import json
import os
import stat
import sys
import time
from typing import Any

UPDATE_FILE = "update-check.json"
# How long after a check that failed or never finished the next one runs.
RETRY_SECONDS = 3600.0
PLUGIN_FILE = "plugin-update-check.json"


def _root() -> str | None:
    try:
        from deepctl import _cache
    except ImportError:
        return None
    return _cache.cache_root()


def usable() -> bool:
    """Whether the checks may run: the cache directory exists (created 0700
    if missing), is a real directory owned by this user, and is private
    (tightened to 0700 here if its group or others had access).

    ``False`` means: skip the checks this run, with no network and no state.
    """
    root = _root()
    if root is None:
        return False
    try:
        os.makedirs(root, mode=0o700, exist_ok=True)
    except OSError:
        return False
    if sys.platform == "win32":
        return os.path.isdir(root) and not os.path.islink(root)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(root, flags)
    except OSError:
        return False
    try:
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            return False
        if stat.S_IMODE(info.st_mode) & 0o077:
            os.fchmod(fd, 0o700)
            return not stat.S_IMODE(os.fstat(fd).st_mode) & 0o077
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def _directory() -> str | None:
    """The cache directory, or ``None`` if it's missing, unsafe or unknown."""
    root = _root()
    if root is None:
        return None
    from deepctl import _cache

    return root if _cache._safe_dir(root) else None


def read(name: str) -> dict[str, Any] | None:
    """The state saved in ``name``, or ``None``."""
    directory = _directory()
    if directory is None:
        return None
    from deepctl import _cache

    data = _cache._read_private(os.path.join(directory, name))
    if data is None:
        return None
    try:
        state = json.loads(data)
    except ValueError:
        return None
    return state if isinstance(state, dict) else None


def _save(name: str, state: dict[str, Any]) -> None:
    directory = _directory()
    if directory is None:
        return
    from deepctl import _cache

    try:
        _cache._write_private(os.path.join(directory, name), json.dumps(state).encode())
    except OSError:
        pass


def write(name: str, *, ok: bool, latest: str | None, source: str) -> None:
    """Save the state of a check that ran now. Never raises. When the
    last notice was shown (``notified_at``, ``notified_latest``) carries
    over, so a check that finishes right after another process showed the
    notice doesn't make it show again. The read and save run under the
    ``claim_notice`` lock, so a claim can't land in between and be lost;
    if the lock stays taken for ``WRITE_LOCK_WAIT_SECONDS`` (a process
    that died holding it), the state is saved without it."""
    directory = _directory()
    lock = None
    if directory is not None:
        path = os.path.join(directory, f".{name}.lock")
        give_up = time.monotonic() + WRITE_LOCK_WAIT_SECONDS
        while (taken := _take_lock(path)) is False and time.monotonic() < give_up:
            time.sleep(0.01)
        lock = path if taken else None
    try:
        state: dict[str, Any] = {
            "checked_at": time.time(),
            "ok": ok,
            "latest": latest,
            "source": source,
        }
        previous = read(name) or {}
        for key in ("notified_at", "notified_latest"):
            if key in previous:
                state[key] = previous[key]
        _save(name, state)
    finally:
        if lock is not None:
            _release_lock(lock)


def notice_due(state: dict[str, Any] | None, latest: str, interval: float) -> bool:
    """Whether the notice for ``latest`` may show: it wasn't shown for
    that version within ``interval``."""
    if (state or {}).get("notified_latest") != latest:
        return True
    return elapsed(notified_at(state), interval)


LOCK_STALE_SECONDS = 60.0
WRITE_LOCK_WAIT_SECONDS = 0.5


def _take_lock(lock: str) -> bool | None:
    """Create ``lock`` (``O_EXCL``): True if this process now holds it,
    False if another does, None if it can't be created at all."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        os.close(os.open(lock, flags, 0o600))
    except FileExistsError:
        return False
    except OSError:
        return None
    return True


def _release_lock(lock: str) -> None:
    try:
        os.unlink(lock)
    except OSError:
        pass


def claim_notice(name: str, latest: str, interval: float) -> bool:
    """Whether this process shows the notice for ``latest`` now; if so,
    it is recorded as shown. Of several dg processes that finish together,
    at most one gets True.

    The claim is a lock file next to the state file (``.<name>.lock``,
    created with ``O_EXCL``), held while the state is read again and the
    notice recorded. A process that finds the lock taken shows nothing.
    A lock older than a minute was left by a process that died: it is
    removed, and this run shows nothing. With no usable cache directory,
    or a lock that can't be created there, there is nothing to coordinate
    with, and the notice shows."""
    directory = _directory()
    if directory is None:
        return True
    lock = os.path.join(directory, f".{name}.lock")
    taken = _take_lock(lock)
    if taken is None:
        return True  # no lock can be made here: showing twice beats never
    if not taken:
        try:
            if time.time() - os.lstat(lock).st_mtime > LOCK_STALE_SECONDS:
                os.unlink(lock)
        except OSError:
            pass
        return False
    try:
        state = read(name)
        if not notice_due(state, latest, interval):
            return False
        if state is not None:
            state["notified_at"] = time.time()
            state["notified_latest"] = latest
            _save(name, state)
        return True
    finally:
        _release_lock(lock)


def _time(state: dict[str, Any] | None, key: str) -> float:
    value = (state or {}).get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def checked_at(state: dict[str, Any] | None) -> float:
    """When the check in ``state`` ran (``0.0`` if unknown)."""
    return _time(state, "checked_at")


def notified_at(state: dict[str, Any] | None) -> float:
    """When the notice for ``state`` was last shown (``0.0`` if never)."""
    return _time(state, "notified_at")


def elapsed(since: float, interval: float) -> bool:
    """Whether at least ``interval`` seconds passed since ``since``.

    A time in the future (a clock that moved back) counts as passed.
    """
    gone = time.time() - since
    return not 0 <= gone < interval


def check_due(state: dict[str, Any] | None, interval: float) -> bool:
    """Whether the next check is due: ``interval`` after one that got its
    answer, ``RETRY_SECONDS`` (at most ``interval``) after one that didn't."""
    wait = interval
    if (state or {}).get("ok") is not True:
        wait = min(interval, RETRY_SECONDS)
    return elapsed(checked_at(state), wait)


def due(name: str, interval: float) -> bool:
    """Whether the next check of ``name`` is due (:func:`check_due`)."""
    return check_due(read(name), interval)
