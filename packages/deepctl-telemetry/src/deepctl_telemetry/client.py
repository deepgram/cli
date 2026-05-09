"""Sentry-backed telemetry client with strict opt-out."""

from __future__ import annotations

import atexit
import os
import platform
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from deepctl_core import Config
    from sentry_sdk.types import Event, Hint


SENTRY_DSN = (
    "https://d7a2aabbf772218e3bbe89266999af70"
    "@o206115.ingest.us.sentry.io/4510993603362816"
)

DSN_ENV_VAR = "DEEPCTL_TELEMETRY_DSN"
DISABLE_ENV_VAR = "DEEPCTL_TELEMETRY_DISABLED"

_initialized = False


def is_enabled(config: Config) -> bool:
    """Whether telemetry should phone home for this invocation.

    Resolution order: env override (DEEPCTL_TELEMETRY_DISABLED=1 wins),
    then the user's config (`telemetry.enabled`, default `True`).
    """
    if os.environ.get(DISABLE_ENV_VAR, "").lower() in {"1", "true", "yes"}:
        return False
    return bool(config.get("telemetry.enabled", True))


def init_telemetry(config: Config) -> bool:
    """Initialize the Sentry SDK if telemetry is enabled.

    Idempotent — safe to call multiple times. Returns whether init ran.
    """
    global _initialized
    if _initialized:
        return True
    if not is_enabled(config):
        return False

    dsn = os.environ.get(DSN_ENV_VAR) or SENTRY_DSN

    try:
        import sentry_sdk
    except ImportError:
        return False

    try:
        cli_version = _read_cli_version()
    except Exception:
        cli_version = "unknown"

    sentry_sdk.init(
        dsn=dsn,
        release=f"deepctl@{cli_version}",
        environment="production",
        send_default_pii=False,
        auto_session_tracking=True,
        traces_sample_rate=1.0,
        profiles_sample_rate=1.0,
        enable_logs=True,
        attach_stacktrace=True,
        max_breadcrumbs=100,
        before_send=_scrub_event,
    )

    sentry_sdk.set_tag("cli.os", platform.system().lower())
    sentry_sdk.set_tag("cli.arch", platform.machine().lower())
    sentry_sdk.set_tag(
        "cli.python",
        f"{sys.version_info.major}.{sys.version_info.minor}",
    )
    sentry_sdk.set_tag("cli.version", cli_version)

    sentry_sdk.start_session()
    atexit.register(_flush_on_exit)

    _initialized = True
    return True


def _flush_on_exit() -> None:
    """Flush queued envelopes before process exit (best-effort, 2s budget)."""
    try:
        import sentry_sdk

        sentry_sdk.flush(timeout=2.0)
    except Exception:
        pass


def _read_cli_version() -> str:
    import importlib.metadata

    return importlib.metadata.version("deepctl")


def _scrub_event(event: Event, _hint: Hint) -> Event | None:
    """Drop request bodies, headers, and any user-identifying data.

    Sentry SDK already filters most PII via send_default_pii=False, but
    Auth tokens, project IDs, and file paths can still leak through
    breadcrumbs and exception messages. This is a defense-in-depth scrub.
    """
    request: dict[str, Any] = event.get("request") or {}
    if "headers" in request:
        request["headers"] = {}
    if "cookies" in request:
        request["cookies"] = {}
    if "data" in request:
        request["data"] = "[Filtered]"

    user: dict[str, Any] = event.get("user") or {}
    user.pop("email", None)
    user.pop("ip_address", None)
    user.pop("username", None)

    return event
