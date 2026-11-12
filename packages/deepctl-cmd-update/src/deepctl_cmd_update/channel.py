"""Which tool installed this copy of deepctl, judged from its own layout.

Stdlib only: the startup check uses these on every run.

Each test looks at ``sys.prefix``, the environment deepctl runs in, not at
the interpreter it links to. A pipx or ``uv tool`` environment built on
Homebrew's Python resolves its interpreter into Homebrew's Cellar, so the
interpreter can't tell those installs from Homebrew's own ``deepgram``
formula.
"""

from __future__ import annotations

import os
import re
import sys
from itertools import pairwise
from pathlib import Path

LAUNCHER_ENV_VAR = "DEEPCTL_LAUNCHER"
NPM_LAUNCHERS = ("npm", "npx")

_UV_ARCHIVE = re.compile(r"archive-v\d+")


def launcher() -> str | None:
    """``npm`` or ``npx`` when the @deepgram/cli npm package started dg.

    ``deepctl.main`` removes ``DEEPCTL_LAUNCHER`` from the environment at
    startup and keeps its value; the environment is the fallback when dg
    didn't start through ``deepctl.main``.
    """
    main = sys.modules.get("deepctl.main")
    if main is not None and getattr(main, "LAUNCHER_CAPTURED", False):
        value = getattr(main, "LAUNCHER", None)
    else:
        value = os.environ.get(LAUNCHER_ENV_VAR)
    value = (value or "").strip().lower()
    return value if value in NPM_LAUNCHERS else None


def _home() -> Path | None:
    try:
        return Path.home()
    except (KeyError, RuntimeError):
        return None


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value) if value else None


def _prefixes() -> list[Path]:
    """``sys.prefix`` as given and with links resolved."""
    prefix = Path(sys.prefix)
    paths = [prefix]
    try:
        resolved = prefix.resolve()
    except OSError:
        return paths
    if resolved != prefix:
        paths.append(resolved)
    return paths


def _parts_below(base: Path) -> tuple[str, ...] | None:
    """The parts of ``sys.prefix`` below ``base``, or ``None`` if it isn't
    inside ``base`` (each compared as given and with links resolved)."""
    bases = [base]
    try:
        resolved = base.resolve()
    except OSError:
        resolved = base
    if resolved != base:
        bases.append(resolved)
    for prefix in _prefixes():
        for candidate in bases:
            try:
                return prefix.relative_to(candidate).parts
            except ValueError:
                continue
    return None


def _first(paths: list[Path | None]) -> list[Path]:
    """The given paths without ``None`` and duplicates, in order."""
    seen: list[Path] = []
    for path in paths:
        if path is not None and path not in seen:
            seen.append(path)
    return seen


def is_brew() -> bool:
    """Homebrew's ``deepgram`` formula: ``sys.prefix`` under
    ``Cellar/deepgram/``."""
    for prefix in _prefixes():
        parts = prefix.parts
        if any(pair == ("Cellar", "deepgram") for pair in pairwise(parts)):
            return True
    return False


def uv_cache_dirs() -> list[Path]:
    """Where uv keeps its cache: ``UV_CACHE_DIR``, else its defaults."""
    home = _home()
    xdg = _env_path("XDG_CACHE_HOME")
    local = _env_path("LOCALAPPDATA")
    return _first(
        [
            _env_path("UV_CACHE_DIR"),
            xdg / "uv" if xdg else None,
            home / ".cache" / "uv" if home else None,
            local / "uv" / "cache" if local and sys.platform == "win32" else None,
        ]
    )


def is_uvx() -> bool:
    """``uvx deepctl``: an environment in uv's cache (``archive-v*``)."""
    for cache in uv_cache_dirs():
        parts = _parts_below(cache)
        if parts and _UV_ARCHIVE.fullmatch(parts[0]):
            return True
    return False


def uv_tool_dirs() -> list[Path]:
    """Where ``uv tool install`` puts tools: ``UV_TOOL_DIR``, else its
    defaults."""
    home = _home()
    xdg = _env_path("XDG_DATA_HOME")
    appdata = _env_path("APPDATA")
    return _first(
        [
            _env_path("UV_TOOL_DIR"),
            xdg / "uv" / "tools" if xdg else None,
            home / ".local" / "share" / "uv" / "tools" if home else None,
            appdata / "uv" / "data" / "tools"
            if appdata and sys.platform == "win32"
            else None,
        ]
    )


def is_uv_tool() -> bool:
    """``uv tool install deepctl``: ``sys.prefix`` is ``<tool dir>/<name>``."""
    return any(len(_parts_below(d) or ()) == 1 for d in uv_tool_dirs())


def pipx_homes() -> list[Path]:
    """pipx's home: ``PIPX_HOME``, else the locations pipx versions use."""
    home = _home()
    xdg = _env_path("XDG_DATA_HOME")
    return _first(
        [
            _env_path("PIPX_HOME"),
            xdg / "pipx" if xdg else None,
            home / ".local" / "share" / "pipx" if home else None,
            home / ".local" / "pipx" if home else None,
            home / "Library" / "Application Support" / "pipx" if home else None,
            home / "pipx" if home else None,
        ]
    )


def _in_pipx(area: str) -> bool:
    return any(
        len(parts) == 2 and parts[0] == area
        for parts in (_parts_below(home) for home in pipx_homes())
        if parts
    )


def is_pipx() -> bool:
    """``pipx install deepctl``: ``sys.prefix`` is ``<pipx home>/venvs/<name>``."""
    return _in_pipx("venvs")


def is_pipx_run() -> bool:
    """``pipx run deepctl``: ``sys.prefix`` is ``<pipx home>/.cache/<hash>``."""
    return _in_pipx(".cache")


def is_oneshot() -> bool:
    """A run that fetches deepctl for one use: uvx, pipx run or npx."""
    return is_uvx() or is_pipx_run() or launcher() == "npx"
