"""User-facing strings for API hosts, credentials and config files.

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

# ── Base URL and credentials ─────────────────────────────────────

E_FH_FLAG = "Not sending your API key to {host}: it is not a Deepgram API host."
E_FH_FLAG_HINT = (
    "If you trust {host}, add it to api.allowed_hosts in {user_config}, "
    "or export DEEPGRAM_BASE_URL in your shell."
)
E_FH_PROFILE = (
    'Not sending your API key to {host}: profile "{profile}" points to a '
    "host that is not a Deepgram API host."
)
# The command comes last, with no trailing period, so it can be copied.
E_FH_PROFILE_HINT = (
    "If you trust {host}, add it to api.allowed_hosts in {user_config}. To "
    "switch back to Deepgram, run: dg --base-url https://api.deepgram.com "
    "login --profile {profile} --save-base-url --force-write"
)
# Agent-mode hint shared by every foreign_host error. Never names a flag,
# an environment variable or a file path.
E_FH_AGENT_HINT = (
    "A person must approve {host} in the deepctl user config. If you are an AI "
    "agent, ask the user."
)

E_INSECURE = (
    "Refusing to use {origin}: API URLs must use https (plain http is allowed "
    "only for localhost, 127.0.0.0/8 and [::1])."
)
E_INSECURE_HINT = "Use an https:// URL. This one came from {source_label}."
# Agent-mode hints never name a key, variable or flag an agent could use to
# approve its own host.
E_INSECURE_AGENT_HINT = (
    "API URLs must use https. Ask the user to fix the configured URL."
)

# W-ENV-* and E-FH-IDENTITY.
W_ENV_BASEURL = (
    "Sending your API key to {host} (from {variable}), which is not a "
    "Deepgram API host."
)
W_ENV_BASEURL_HINT = (
    "If you trust {host}, add it to api.allowed_hosts in {user_config} to "
    "stop this warning."
)
W_ENV_BASEURL_AGENT_HINT = (
    "To stop this warning, a person can approve {host} in the deepctl user "
    "config. If you are an AI agent, ask the user."
)
W_ENV_IDENTITY = (
    "Logging in through {host} (from DEEPGRAM_CLI_BASE_URL), which is not a "
    "Deepgram login host. dg will save the API key it returns."
)
E_FH_IDENTITY = (
    "Not logging in through {host} (from DEEPGRAM_CLI_BASE_URL): it is not a "
    "Deepgram login host."
)
E_FH_IDENTITY_HINT = (
    "If you trust {host}, add it to api.allowed_hosts in {user_config}."
)

W_INSECURE_AGENT = (
    "Ignoring DEEPGRAM_CLI_INSECURE in an AI agent session: TLS certificates "
    "are verified."
)

W_INSECURE_REMOTE = (
    "DEEPGRAM_CLI_INSECURE applies only to localhost: TLS certificates for "
    "{host} are verified."
)
W_INSECURE_REMOTE_HINT = (
    "To test against a server with a self-signed certificate, run it on localhost."
)

SOURCE_LABELS = {
    "flag": "--base-url",
    "parent_env": "DEEPGRAM_BASE_URL",
    "user_config": 'profile "{profile}" in {user_config}',
}

CODE_FOREIGN_HOST = "foreign_host"
CODE_INSECURE_BASE_URL = "insecure_base_url"
CODE_CONFIRMATION_REQUIRED = "confirmation_required"

# ── Login ────────────────────────────────────────────────────────

I_LOGIN_NOT_SAVED = (
    'Used {origin} for this login only. Profile "{profile}" still uses {saved_origin}.'
)
I_LOGIN_NOT_SAVED_HINT = "To save it, run: dg --base-url {origin} login --save-base-url"
P_SAVE_BASEURL_FOREIGN = (
    "{host} is not a Deepgram API host. Saving it also adds it to api.allowed_hosts."
)
P_SAVE_BASEURL = (
    'Save {origin} as the API URL for profile "{profile}"? dg will send your '
    "API key there on every command."
)
S_SAVE_BASEURL = 'Saved {origin} as the API URL for profile "{profile}".'
S_SAVE_BASEURL_ADDED = " Added {host} to api.allowed_hosts."
E_SAVE_DECLINED = "Not saved. Nothing was sent to {host}."
E_SAVE_NOTTY = (
    "--save-base-url needs a person to confirm in a terminal. Nothing was "
    "saved or sent."
)
E_SAVE_NOTTY_HINT = "Run the command in an interactive terminal."
E_SAVE_NOTTY_AGENT_HINT = "A person must run this command in an interactive terminal."
E_SAVE_NOURL = "--save-base-url needs --base-url URL."
# Refused only in an AI agent session (hard AI-agent signals only, never a
# missing TTY or CI).
E_PLUGIN_AGENT = (
    "dg plugin {action} doesn't run in an AI agent session. Nothing was changed."
)
E_TOOL_PATH_AGENT = (
    "dg ffprobe --path doesn't run in an AI agent session. Nothing was saved."
)
E_PERSON_ONLY_AGENT_HINT = (
    "A person must run this command. If you are an AI agent, ask the user."
)
# A login prompt that can't be shown (catalogue strings). Hints:
# H = person, A = AI agent, C = automation (CI, cron); *_NULL when next_step
# is null. {prog} is the running executable.
E_CONFIRM_ENVKEY = (
    "DEEPGRAM_API_KEY is set, and logging in saves a profile that {prog} will "
    "use instead of it."
)
A_CONFIRM_ENVKEY = (
    "Run next_step only if the user agrees: the saved login will then take "
    "precedence over DEEPGRAM_API_KEY."
)
A_CONFIRM_ENVKEY_NULL = (
    "Ask the user whether to save a login profile; {prog} will then use it "
    "instead of DEEPGRAM_API_KEY. Never put a key on the command line."
)
C_CONFIRM_ENVKEY_NULL = (
    "This job's DEEPGRAM_API_KEY is used without a login. Drop the login step, "
    "or add --yes if a saved profile is intended."
)
E_CONFIRM_RELOGIN = 'Profile "{profile}" already has a saved login.'
H_CONFIRM_YES = "To go ahead, run the command again with --yes."
A_CONFIRM_YES = "Run the command in next_step only if the user agrees."
A_CONFIRM_YES_NULL = (
    "Ask the user to rerun the command with --yes in their own terminal if they agree."
)
C_CONFIRM_YES_NULL = (
    "Add --yes to this command in the job if replacing the saved login is intended."
)
E_SAVE_CUSTOM_CONFIG = (
    "--save-base-url can't be used with --config: only the default user "
    "config ({user_config}) can approve API hosts. Run the command without "
    "--config."
)

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
    "unreadable": "couldn't be opened safely",
}
# The real config refused before it was read, with the cause ({why}, a
# W_CFG_UNTRUSTED_WHY value). Hint: W_CFG_UNTRUSTED_FILE_HINT.
W_CFG_UNTRUSTED_SKIPPED = (
    "Ignored {config_file}: in an AI agent session, dg doesn't read the deepctl "
    "user config when the file {why}."
)
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
# No API key once the user file's key was left unused.
# Hint: W_CFG_UNTRUSTED_FILE_HINT.
E_KEY_UNTRUSTED = (
    "dg didn't use the API key in {user_config} (see the warning above), and no "
    "other API key is set."
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
