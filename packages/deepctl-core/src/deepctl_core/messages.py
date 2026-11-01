"""User-facing strings for config files.

Keyed by message ID (E-*, W-*) so every string lives in one place.
Placeholders: ``{host}`` is a hostname only,
``{origin}`` is ``scheme://host[:port]``, ``{user_config}`` is the user
config path with ``$HOME`` shown as ``~``. Every value substituted into
these strings must go through :func:`display` first, and a composed line
must be passed through ``rich.markup.escape`` before it is printed.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

# ── Config file permissions ──────────────────────────────────────

I_PERM_FIXED = (
    "Restricted {user_config} to owner-only access (0600) because it holds an API key."
)
W_PERM_FAILED = (
    "{user_config} holds an API key and other users can read it (mode "
    "{octal}). Fix it with: chmod 600 {user_config}"
)
W_PERM_FAILED_FOREIGN = (
    "{user_config} holds an API key and other users can read it (mode "
    "{octal}), and dg can't change that file. Remove the key from it and set "
    "DEEPGRAM_API_KEY instead."
)

# ── Rendering helpers ────────────────────────────────────────────

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_MAX_DISPLAY = 200


def display(value: object) -> str:
    """Make an untrusted value safe to show.

    Strips C0/C1 control characters (including ESC) and truncates to 200
    characters. Rich markup is escaped at print time, not here, so the same
    text can also go into a JSON payload unchanged.
    """
    return _CONTROL_CHARS.sub("", str(value))[:_MAX_DISPLAY]


def display_path(path: Path | str) -> str:
    """Show a path with the home directory abbreviated to ``~``."""
    text = str(path)
    home = os.path.expanduser("~")
    if home and home != "~" and (text == home or text.startswith(home + os.sep)):
        text = "~" + text[len(home) :]
    return display(text)
