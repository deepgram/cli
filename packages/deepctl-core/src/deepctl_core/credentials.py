"""One place that decides which API key and project ID a run uses.

:func:`resolve` returns a :class:`Resolved`: the key, the project ID, where
each came from, the profile and why that profile, and whether the user
config file's key was left unused because the file isn't trusted. Every
``AuthManager`` question (``get_api_key``, ``get_project_id``,
``get_credential_source``, ``is_authenticated``, ``has_profile_credentials``)
is answered from it, so ``mcp``, ``init``, ``debug toolkit`` and ``login``
share one precedence.

The 0.5.0 precedence is 0.4.2's, unchanged: ``--api-key`` > the profile's
keyring entry > the profile in the config file > ``DEEPGRAM_API_KEY``. The
config file layer already holds ``DEEPGRAM_API_KEY`` when it is set
(``Config`` merges the environment into the profile), so a file key equal to
the environment value is reported as the environment's. The 1.0 order
comes later.

Under a hard AI-agent signal, ``Config`` drops the key of a user file that
others can write or that someone else owns;
``Config.api_key_untrusted`` says so and the result carries
``untrusted_source``. Such a file's ``dg_token_expires_at`` never deletes
the keyring entry.

The keyring is imported only when a key is needed and no ``--api-key`` was
given. Off a terminal (and so under MCP), a read gets
:data:`KEYRING_BOUND_S` seconds in a daemon thread; when it doesn't answer,
the run goes on without it, says so once (``W-KEYRING-TIMEOUT``) and doesn't
ask again.

A value that is still an unexpanded ``${...}`` placeholder counts as unset
(``W-ENV-PLACEHOLDER``, once per variable).

This module imports only the standard library at import time.
"""

from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import Config

# ``key_source`` values: `dg whoami`'s words, plus the flag.
KEY_SOURCE_FLAG = "--api-key"
KEY_SOURCE_KEYRING = "system keyring"
KEY_SOURCE_FILE = "config file"
KEY_SOURCE_ENV = "DEEPGRAM_API_KEY (env)"
KEY_SOURCE_NONE = "not set"

# ``profile_source`` values (DX-S15).
PROFILE_SOURCE_NONE = "none"
PROFILE_SOURCE_FLAG = "--profile"
PROFILE_SOURCE_ENV = "DEEPGRAM_PROFILE"
PROFILE_SOURCE_FILE = "user-file default"

# Keyring reads off a terminal get this long before the run goes on without
# the keyring.
KEYRING_BOUND_S = 2.0

# The env-vs-stored mismatch warning. Off in 0.5.0: login doesn't
# write fingerprints yet. A later release turns it on.
WARN_ENV_MISMATCH = False

# The environment variables a placeholder can't stand in for.
PLACEHOLDER_VARIABLES = (
    "DEEPGRAM_API_KEY",
    "DEEPGRAM_PROFILE",
    "DEEPGRAM_PROJECT_ID",
    "DEEPGRAM_BASE_URL",
)

# Catalogue text (error_codes.json "strings"). Filled in with
# str.replace: the text itself holds braces.
W_ENV_PLACEHOLDER = (
    "{prog} ignored {variable}: it holds a placeholder that was never filled in (${…})."
)
W_KEYRING_TIMEOUT = (
    "The system keyring didn't answer within 2 s, so {prog} didn't use the key "
    "stored there."
)

_EXPIRY_INVALID = "Your Deepgram credential expiry is invalid. Run `dg login` again."
_EXPIRY_PAST = "Your Deepgram credential has expired. Run `dg login` again."


@dataclass(frozen=True)
class Resolved:
    """The credentials one run uses, and where they came from."""

    api_key: str | None
    project_id: str | None
    key_source: str
    profile: str
    profile_source: str
    # The user config file whose key wasn't used (display path), or None.
    untrusted_source: str | None = None
    # Why a stored credential was unusable when nothing else was set.
    expiry_error: str | None = None
    # 0.4.2's has_profile_credentials(): a usable keyring entry, or a key in
    # the profile as Config holds it (which includes DEEPGRAM_API_KEY).
    profile_has_key: bool = False
    profile_has_project: bool = False
    project_source: str = "none"  # "flag", "profile", "env" or "none"
    # DEEPGRAM_API_KEY is set and differs from the stored key that won.
    # Always False while WARN_ENV_MISMATCH is off.
    env_mismatch: bool = False
    # The keyring entry an expired credential in a trusted file leaves behind
    # (``api-key.<profile>``); removed only by a resolution with prune=True.
    prune_target: str | None = None


# ── Placeholders ─────────────────────────────────────────────────────

_lock = threading.Lock()
# variable -> the placeholder text it held (to recognise it inside Config).
_dropped: dict[str, str] = {}
_warned: set[str] = set()


def is_placeholder(value: object) -> bool:
    """True for a value that still holds an unexpanded ``${...}``."""
    return isinstance(value, str) and "${" in value


def drop_env_placeholders() -> list[str]:
    """Remove placeholder values of :data:`PLACEHOLDER_VARIABLES` from the
    environment, so ``Config`` and everything after it see them as unset.

    Returns the variables removed by this call. Safe to call more than once.
    """
    removed = []
    with _lock:
        for name in PLACEHOLDER_VARIABLES:
            value = os.environ.get(name)
            if is_placeholder(value):
                _dropped[name] = str(value)
                del os.environ[name]
                removed.append(name)
    return removed


def env_value(name: str) -> str | None:
    """``os.environ[name]``, with an empty value or a placeholder treated as
    unset."""
    return _env(name)


def _env(name: str) -> str | None:
    """``os.environ[name]``, with a placeholder treated as unset."""
    value = os.environ.get(name)
    if is_placeholder(value):
        with _lock:
            _dropped.setdefault(name, str(value))
        return None
    return value or None


def _was_placeholder(name: str, value: object) -> bool:
    """True when ``value`` is the placeholder ``name`` holds or held (merged
    by a Config built before :func:`drop_env_placeholders` ran)."""
    if not is_placeholder(value):
        return False
    if os.environ.get(name) == value:
        with _lock:
            _dropped.setdefault(name, str(value))
        return True
    with _lock:
        return _dropped.get(name) == value


def flush_warnings() -> None:
    """Print ``W-ENV-PLACEHOLDER`` once per ignored variable."""
    with _lock:
        names = [n for n in PLACEHOLDER_VARIABLES if n in _dropped and n not in _warned]
        _warned.update(names)
    for name in names:
        _warn(W_ENV_PLACEHOLDER.replace("{variable}", name))


def _warn(text: str) -> None:
    from rich.markup import escape

    from . import output, transport

    message = text.replace("{prog}", transport.prog() or "dg")
    output.print_warning(escape(message), stderr=True, ignore_quiet=True)


# ── Keyring ──────────────────────────────────────────────────────────

_keyring_timed_out = False


def _interactive() -> bool:
    """A person at a terminal (where a keyring may ask to be unlocked)."""
    try:
        return bool(sys.stdin and sys.stdin.isatty() and sys.stderr.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def keyring_timed_out() -> bool:
    """True once a keyring read in this process didn't answer in time."""
    return _keyring_timed_out


def _keyring_call(name: str, *args: str) -> Any:
    """``keyring.<name>(*args)``, bounded off a terminal.

    Raises what the keyring raises. Returns ``None`` without calling it once
    a read has timed out in this process.
    """
    global _keyring_timed_out
    if _keyring_timed_out:
        return None
    from . import auth

    def call() -> Any:
        # Through deepctl_core.auth, so tests that patch auth.keyring apply;
        # the import itself runs here too, inside the bound.
        module = vars(auth).get("keyring") or auth._bind_module("keyring")
        return getattr(module, name)(*args)

    if _interactive():
        return call()
    box: dict[str, Any] = {}

    def work() -> None:
        try:
            box["value"] = call()
        except BaseException as exc:  # handed to the caller below
            box["error"] = exc

    thread = threading.Thread(target=work, name="deepctl-keyring", daemon=True)
    thread.start()
    thread.join(KEYRING_BOUND_S)
    if thread.is_alive():
        with _lock:
            first = not _keyring_timed_out
            _keyring_timed_out = True
        if first:
            _warn(W_KEYRING_TIMEOUT)
        return None
    if "error" in box:
        raise box["error"]
    return box.get("value")


class KeyringTimeout(Exception):
    """The keyring didn't answer within :data:`KEYRING_BOUND_S` (this read or
    an earlier one in this process)."""


def keyring_read(username: str) -> str | None:
    """The keyring's value for ``username``, bounded like every resolver read.

    Raises what the keyring raises, and :class:`KeyringTimeout` when it
    doesn't answer in time, so callers that fall back on an error keep doing
    so. Returns ``None`` when there's no entry.
    """
    from .auth import KEYRING_SERVICE

    if _keyring_timed_out:
        raise KeyringTimeout()
    value = _keyring_call("get_password", KEYRING_SERVICE, username)
    if _keyring_timed_out:
        raise KeyringTimeout()
    return value if isinstance(value, str) and value else None


def keyring_get(username: str) -> str | None:
    """The keyring's value for ``username``; ``None`` if absent, failing or
    timed out."""
    try:
        return keyring_read(username)
    except Exception:
        return None


def keyring_delete(username: str) -> None:
    """Remove a keyring entry when present, without masking keyring failures."""
    from .auth import KEYRING_SERVICE, console

    try:
        _keyring_call("delete_password", KEYRING_SERVICE, username)
    except Exception as exc:
        from keyring.errors import PasswordDeleteError

        if isinstance(exc, PasswordDeleteError):
            return
        console.print(
            f"[yellow]Warning:[/yellow] Could not remove legacy credential: {exc}"
        )


# ── Resolution ───────────────────────────────────────────────────────


def credential_expiry(config: Config, profile_name: str) -> tuple[bool, str | None]:
    """Whether the profile's finite credential is unusable, and why."""
    expires_at = config.get_profile(profile_name).dg_token_expires_at
    if not isinstance(expires_at, str):
        return False, None
    try:
        parsed = datetime.fromisoformat(expires_at)
        if parsed.tzinfo is None:
            raise ValueError
    except (TypeError, ValueError):
        return True, _EXPIRY_INVALID
    if parsed <= datetime.now(timezone.utc):
        return True, _EXPIRY_PAST
    return False, None


def _user_value(config: Config, *path: str) -> Any:
    data: Any = getattr(config, "_user_data", None)
    for part in path:
        if not isinstance(data, dict):
            return None
        data = data.get(part)
    return data


def profile_name(config: Config) -> tuple[str, str]:
    """The profile this run uses and why (``profile_source``)."""
    name = config.profile or "default"
    explicit = getattr(config, "_explicit_profile", None)
    env_profile = os.environ.get("DEEPGRAM_PROFILE")
    if explicit and name == explicit:
        return name, PROFILE_SOURCE_FLAG
    if is_placeholder(name) and _was_placeholder("DEEPGRAM_PROFILE", name):
        # Config took the placeholder as the profile name: use the one the
        # file names instead.
        name = (
            _user_value(config, "active_profile")
            or _user_value(config, "default_profile")
            or "default"
        )
        name = str(name)
        source = PROFILE_SOURCE_FILE if name != "default" else PROFILE_SOURCE_NONE
        return name, source
    if config.get("active_profile") == name:
        return name, PROFILE_SOURCE_FILE
    if env_profile and name == env_profile:
        return name, PROFILE_SOURCE_ENV
    if _user_value(config, "default_profile") == name:
        return name, PROFILE_SOURCE_FILE
    return name, PROFILE_SOURCE_NONE


def _profile_key(config: Config, name: str) -> str | None:
    """The profile's key as Config holds it (file, or the merged env value)."""
    key = config.get_profile(name).api_key
    if not key:
        return None
    if is_placeholder(key):
        if not _was_placeholder("DEEPGRAM_API_KEY", key):
            return None
        # Config merged the placeholder over the file's key: use the file's.
        if getattr(config, "api_key_untrusted", False) is True:
            return None
        stored = _user_value(config, "profiles", name, "api_key")
        return (
            stored if isinstance(stored, str) and not is_placeholder(stored) else None
        )
    return key


def _profile_project(config: Config, name: str) -> str | None:
    project = config.get_profile(name).project_id
    if not project:
        return None
    if is_placeholder(project):
        if not _was_placeholder("DEEPGRAM_PROJECT_ID", project):
            return None
        stored = _user_value(config, "profiles", name, "project_id")
        return (
            stored if isinstance(stored, str) and not is_placeholder(stored) else None
        )
    return project


def resolve(
    ctx: Any,
    *,
    profile: str | None = None,
    ignore_env: bool = False,
    need_key: bool = True,
    prune: bool = True,
) -> Resolved:
    """The credentials for ``ctx``: anything with ``config``,
    ``explicit_api_key`` and ``explicit_project_id`` (an ``AuthManager``).

    ``profile`` asks about another profile than the run's. ``ignore_env``
    leaves ``DEEPGRAM_API_KEY`` and ``DEEPGRAM_PROJECT_ID`` out.
    ``need_key=False`` answers project questions without reading the keyring.
    ``prune=False`` keeps the answer read-only: an expired credential's
    keyring entry stays (whoami and the other introspection, as in 0.4.2);
    commands that use the key prune it, as 0.4.2's ``get_api_key`` did.
    Raises nothing; ``expiry_error`` says why a stored credential wasn't used.
    """
    try:
        resolved = _resolve(ctx, profile, ignore_env, need_key)
        if prune:
            prune_expired(resolved)
        return resolved
    finally:
        flush_warnings()


def prune_expired(resolved: Resolved) -> None:
    """Remove the keyring entry of an expired credential (trusted file only)."""
    if resolved.prune_target:
        keyring_delete(resolved.prune_target)


def _resolve(
    ctx: Any, profile: str | None, ignore_env: bool, need_key: bool
) -> Resolved:
    config: Config = ctx.config
    if profile:
        name, profile_source = profile, PROFILE_SOURCE_FLAG
    else:
        name, profile_source = profile_name(config)
    untrusted = getattr(config, "api_key_untrusted", False) is True
    untrusted_source = None
    if untrusted:
        from . import messages

        untrusted_source = messages.display_path(config.config_path)

    env_key = None if ignore_env else _env("DEEPGRAM_API_KEY")
    env_project = None if ignore_env else _env("DEEPGRAM_PROJECT_ID")

    # Project ID: flag > profile (as Config holds it) > env.
    project_file = _profile_project(config, name)
    raw_env_project = _env("DEEPGRAM_PROJECT_ID")
    if ctx.explicit_project_id:
        project_id, project_source = ctx.explicit_project_id, "flag"
    elif project_file:
        project_id = project_file
        project_source = (
            "env" if raw_env_project and project_file == raw_env_project else "profile"
        )
    elif env_project:
        project_id, project_source = env_project, "env"
    else:
        project_id, project_source = None, "none"

    def result(
        key: str | None,
        source: str,
        *,
        has_key: bool = False,
        expiry_error: str | None = None,
        env_mismatch: bool = False,
    ) -> Resolved:
        # Only a trusted file's expiry may remove the keyring entry.
        prune_target = f"api-key.{name}" if expired and not untrusted else None
        return Resolved(
            api_key=key,
            project_id=project_id,
            key_source=source,
            profile=name,
            profile_source=profile_source,
            untrusted_source=untrusted_source,
            expiry_error=expiry_error,
            profile_has_key=has_key,
            profile_has_project=bool(project_file),
            project_source=project_source,
            env_mismatch=env_mismatch,
            prune_target=prune_target,
        )

    expired = False
    if ctx.explicit_api_key:
        return result(ctx.explicit_api_key, KEY_SOURCE_FLAG)
    if not need_key:
        return result(None, KEY_SOURCE_NONE)

    file_key = _profile_key(config, name)
    raw_env_key = _env("DEEPGRAM_API_KEY")
    expired, expiry_error = credential_expiry(config, name)
    if not expired:
        stored = keyring_get(f"api-key.{name}")
        if stored:
            mismatch = _mismatch(stored, env_key)
            return result(
                stored, KEY_SOURCE_KEYRING, has_key=True, env_mismatch=mismatch
            )
        if file_key:
            source = (
                KEY_SOURCE_ENV
                if raw_env_key and file_key == raw_env_key
                else KEY_SOURCE_FILE
            )
            # As in 0.4.2, ignore_env doesn't skip the env value Config
            # merged into the profile; it skips only the last fallback.
            mismatch = source == KEY_SOURCE_FILE and _mismatch(file_key, env_key)
            return result(file_key, source, has_key=True, env_mismatch=mismatch)

    if env_key:
        return result(env_key, KEY_SOURCE_ENV, expiry_error=expiry_error)
    return result(None, KEY_SOURCE_NONE, expiry_error=expiry_error)


# ── Env-vs-stored mismatch ───────────────────────────────────────────


def fingerprint(key: str) -> str:
    """A short, non-reversible fingerprint of a key, for comparisons."""
    import hashlib

    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _mismatch(stored: str, env_key: str | None) -> bool:
    """True when DEEPGRAM_API_KEY is set and isn't the stored key that won.

    Compares fingerprints, never keys, so a later release can compare with the
    fingerprint login will store. Behind :data:`WARN_ENV_MISMATCH`, which is
    off in 0.5.0: login doesn't write fingerprints yet, and a later release
    adds the warning text and turns it on.
    """
    if not WARN_ENV_MISMATCH or not env_key:
        return False
    return fingerprint(stored) != fingerprint(env_key)


def _reset_for_tests() -> None:
    """Forget the per-process state (placeholders, warnings, keyring timeout)."""
    global _keyring_timed_out
    with _lock:
        _dropped.clear()
        _warned.clear()
        _keyring_timed_out = False
