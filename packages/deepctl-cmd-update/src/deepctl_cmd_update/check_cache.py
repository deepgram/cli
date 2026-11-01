"""When the update checks last ran, kept in dg's cache directory.

Each check has one small JSON file there, ``update-check.json`` for dg itself
and ``plugin-update-check.json`` for plugins, holding ``checked_at`` (Unix
time), ``ok``, ``latest`` and ``source``, plus ``notified_at`` once a notice
was shown. A check writes its file just before it sends its request and again
when it gets an answer, so a check that fails or never finishes still waits a
full interval before the next attempt, while a run that ends before the
request goes out doesn't use up the interval.

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
    """Save the state of a check that ran now. Never raises."""
    _save(
        name,
        {"checked_at": time.time(), "ok": ok, "latest": latest, "source": source},
    )


def mark_notified(name: str) -> None:
    """Record that the notice for the saved state was shown now."""
    state = read(name)
    if state is not None:
        state["notified_at"] = time.time()
        _save(name, state)


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


def due(name: str, interval: float) -> bool:
    """Whether at least ``interval`` seconds passed since the last check."""
    return elapsed(checked_at(read(name)), interval)
