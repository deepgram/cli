"""Locate external tools (ffprobe) without picking one up from the
current directory."""

from __future__ import annotations

import os
import sys
from typing import Any


def find_executable(name: str) -> str | None:
    """Absolute path of ``name`` from PATH, or ``None``.

    Unlike ``shutil.which``, relative and empty PATH entries are skipped, and
    so is the current directory itself, on every OS (Windows would otherwise
    search the current directory first).
    """
    try:
        cwd = os.path.realpath(os.getcwd())
    except OSError:
        cwd = None
    names = [name]
    if sys.platform == "win32" and not os.path.splitext(name)[1]:
        exts = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(os.pathsep)
        names = [name + ext for ext in exts if ext]
    for entry in os.environ.get("PATH", os.defpath).split(os.pathsep):
        if not entry or not os.path.isabs(entry):
            continue
        if cwd is not None and os.path.realpath(entry) == cwd:
            continue
        for candidate in names:
            path = os.path.join(entry, candidate)
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
    return None


def resolve_tool(config: Any, name: str, key: str) -> str | None:
    """A tool path from the user config (absolute only), else from PATH."""
    stored = config.get(key) if config is not None else None
    if (
        isinstance(stored, str)
        and os.path.isabs(stored)
        and os.path.isfile(stored)
        and os.access(stored, os.X_OK)
    ):
        return stored
    return find_executable(name)
