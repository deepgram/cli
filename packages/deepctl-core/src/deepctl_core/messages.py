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

# ── Project file deepgram.yaml ───────────────────────────────────

W_PF = "Ignored {key} in {project_file}: {reason}"
PROJECT_FILE_DISPLAY = "./deepgram.yaml"
PF_AGENT_HOSTS_HINT = (
    "Ask the user to configure other API hosts in their deepctl user config."
)

# Agent hints for settings that run code or change behavior: they point at
# a person, never at the config file.
AGENT_SETTINGS_HINT = (
    "A person must set this in the deepctl user config. If you are an AI agent, "
    "ask the user."
)
AGENT_TOOLS_HINT = (
    "Put ffprobe on your PATH, or ask the user to set tool paths in their deepctl "
    "user config."
)

# message id -> (reason, human hint, agent hint or None for "same")
W_PF_MESSAGES: dict[str, tuple[str, str, str | None]] = {
    "W-PF-APIKEY": (
        "project files can't set credentials.",
        "Use dg login, or set DEEPGRAM_API_KEY.",
        None,
    ),
    "W-PF-PROJECT": (
        "project files can't set the project ID.",
        "Use dg login, or set DEEPGRAM_PROJECT_ID.",
        None,
    ),
    "W-PF-PROFILE": (
        "project files can't choose the profile.",
        "Use --profile NAME, or set DEEPGRAM_PROFILE.",
        None,
    ),
    "W-PF-BASEURL": (
        "project files can only select a Deepgram regional API URL "
        "({origin} is not one).",
        "To use another host, set DEEPGRAM_BASE_URL in your shell, or add it "
        "to api.allowed_hosts in {user_config}.",
        PF_AGENT_HOSTS_HINT,
    ),
    "W-PF-API": (
        "project files can't approve hosts for your API key.",
        "Add trusted hosts to api.allowed_hosts in {user_config}.",
        PF_AGENT_HOSTS_HINT,
    ),
    "W-PF-TOOLS": (
        "project files can't set tool paths.",
        "Put ffprobe on your PATH, or set an absolute tools.ffprobe_path in "
        "{user_config}.",
        AGENT_TOOLS_HINT,
    ),
    "W-PF-TELEMETRY": (
        "project files can't change telemetry settings.",
        "This version of dg sends no telemetry.",
        None,
    ),
    "W-PF-UPDATE": (
        "project files can't change update settings.",
        "Set them in {user_config}.",
        AGENT_SETTINGS_HINT,
    ),
    "W-PF-PLUGINS": (
        "project files can't change plugin settings.",
        "Set them in {user_config}.",
        AGENT_SETTINGS_HINT,
    ),
    "W-PF-OTHER": (
        "project files can't set this.",
        "Set it in {user_config}.",
        None,
    ),
}

# A --config file outside the default location.
W_CF = "Ignored {key} in {config_file}: {reason}"
W_CF_MESSAGES: dict[str, tuple[str, str, str | None]] = {
    "W-CF-API": (
        "only the default user config can approve hosts for your API key.",
        "Add trusted hosts to api.allowed_hosts in {user_config}.",
        PF_AGENT_HOSTS_HINT,
    ),
    "W-CF-BASEURL": (
        "a --config file can only select a Deepgram regional API URL "
        "({origin} is not one).",
        "To use another host, set DEEPGRAM_BASE_URL in your shell, or set the "
        "profile's base_url in {user_config} and add the host to "
        "api.allowed_hosts there.",
        PF_AGENT_HOSTS_HINT,
    ),
    "W-CF-TOOLS": (
        "only the default user config can set tool paths.",
        "Put ffprobe on your PATH, or set an absolute tools.ffprobe_path in "
        "{user_config}.",
        AGENT_TOOLS_HINT,
    ),
    "W-CF-PLUGINS": (
        "only the default user config can change plugin settings.",
        "Set them in {user_config}.",
        AGENT_SETTINGS_HINT,
    ),
    "W-CF-UPDATE": (
        "only the default user config can change update settings.",
        "Set them in {user_config}.",
        AGENT_SETTINGS_HINT,
    ),
}

# An AI agent session whose default config isn't the real-home file.
# Rendered with W_CF, one line per ignored key; the config file is shown
# as an absolute path. Agent hint: AGENT_SETTINGS_HINT.
W_CFG_UNTRUSTED = (
    "in an AI agent session, only the deepctl user config at its standard "
    "location in your home directory can set this."
)
# The real config refused in an AI agent session, with the cause. Never tells
# how to change it. Agent hint:
# W_CFG_UNTRUSTED_FILE_HINT.
W_CFG_UNTRUSTED_FILE = (
    "in an AI agent session, the deepctl user config can't set this because "
    "the file {why}."
)
W_CFG_UNTRUSTED_WHY = {
    "symlink": "is a symlink or is reached through one",
    "links": "has more than one hard link",
    "writable": "can be changed by other users",
    "owner": "is owned by another user",
    "not_regular": "isn't a regular file",
}
W_CFG_UNTRUSTED_FILE_HINT = (
    "A person must fix this file. If you are an AI agent, ask the user."
)

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

# An explicit save refused in an AI agent session because the user config
# is owned by another user or others can change it.
# {why} is a W_CFG_UNTRUSTED_WHY value. Hint: W_CFG_UNTRUSTED_FILE_HINT.
W_CFG_SAVE_UNTRUSTED = (
    "in an AI agent session, dg can't save this config because the file {why}, "
    "so nothing was saved."
)
# A key-holding user config others can change, in an AI agent session. Agent
# hint: W_CFG_UNTRUSTED_FILE_HINT.
W_PERM_AGENT = (
    "{user_config} holds an API key and other users can change it (mode "
    "{octal}), so in an AI agent session dg won't use that key or change the "
    "file's permissions."
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
