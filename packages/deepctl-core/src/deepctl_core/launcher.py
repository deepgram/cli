"""Which wrapper, if any, started deepctl.

The ``@deepgram/cli`` npm package runs deepctl from a private venv or through
``uv tool run`` and says so in ``DEEPCTL_LAUNCHER``: ``npm`` for a global
install, ``npx`` for ``npx``/``npm exec``. Nothing else sets it.

The variable is read once and removed from ``os.environ``, so it describes
this process only. Anything deepctl spawns -- a starter app, a shell, another
``dg`` -- must not inherit a claim about how *it* was installed.
"""

from __future__ import annotations

import os

LAUNCHER_ENV_VAR = "DEEPCTL_LAUNCHER"
NPM_LAUNCHERS = frozenset({"npm", "npx"})

_captured = False
_launcher: str | None = None


def capture_launcher() -> str | None:
    """Read and remove ``DEEPCTL_LAUNCHER`` (first call only); return its value."""
    global _captured, _launcher
    if not _captured:
        _launcher = os.environ.pop(LAUNCHER_ENV_VAR, None) or None
        _captured = True
    return _launcher


def get_launcher() -> str | None:
    """Return the launcher that started deepctl, or ``None``."""
    return capture_launcher()


def launched_via_npm() -> bool:
    """Return True if the npm package (global install or npx) started deepctl."""
    return get_launcher() in NPM_LAUNCHERS


def npm_upgrade_command() -> str:
    """Return the command that upgrades an npm-launched deepctl."""
    if get_launcher() == "npx":
        return "npx @deepgram/cli@latest"
    return "npm install -g @deepgram/cli@latest"


def reset_launcher_for_tests() -> None:
    """Forget the captured value so the next call re-reads the environment."""
    global _captured, _launcher
    _captured = False
    _launcher = None
