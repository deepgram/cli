"""Which wrapper, if any, started deepctl (internal; not a public API).

The ``@deepgram/cli`` npm package runs deepctl from a private venv and says
so in ``DEEPCTL_LAUNCHER``: ``npm`` for an installed package (global, or a
project dependency: the launcher can't tell them apart), ``npx`` for
``npx``/``npm exec``. Nothing else sets it.

The variable is read once and removed from ``os.environ``, so it describes
this process only. Anything deepctl spawns -- a starter app, a shell, another
``dg`` -- must not inherit a claim about how *it* was installed.
"""

from __future__ import annotations

import os
import sys

LAUNCHER_ENV_VAR = "DEEPCTL_LAUNCHER"
NPM_LAUNCHERS = frozenset({"npm", "npx"})

_captured = False
_launcher: str | None = None


def capture_launcher() -> str | None:
    """The launcher that started deepctl, captured on the first call.

    Reads and removes ``DEEPCTL_LAUNCHER``, unless ``deepctl.main`` already
    did and says so with ``LAUNCHER_CAPTURED``: then its ``LAUNCHER`` is the
    value. From 0.5.0 (#165), ``deepctl.main`` is stdlib-only and removes the
    variable before deepctl_core is imported, so it is gone from
    ``os.environ`` by the time this runs. A ``deepctl.main`` that calls this
    function itself sets no flag, and the check falls through.
    """
    global _captured, _launcher
    if not _captured:
        entry = sys.modules.get("deepctl.main")
        if entry is not None and getattr(entry, "LAUNCHER_CAPTURED", False):
            _launcher = entry.LAUNCHER
        else:
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
