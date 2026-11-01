"""Background startup update check for deepctl.

Runs a version check in a daemon thread while the command runs. When the
command is done, a one-line notice goes to stderr only if the thread has
already finished; dg never waits for it. A check that hasn't finished is
dropped, and the interpreter stops the thread at exit.

The check runs at most once per interval (daily by default). Its state lives
in ``<cache>/update-check.json`` (``check_cache``), written just before the
request goes out and again with the answer, so a failing or slow index is
tried again only after a full interval. When no check is due and the saved
answer names a newer version, the notice prints from it, at most once per
interval (``notified_at``), so an answer that came in too late for its own
run still reaches the person. The user config file is never written. When
the cache directory can't be made private, the check doesn't run at all.

The check is skipped entirely under ``--quiet``, in CI, in an AI agent or
other non-interactive session, under ``dg mcp``, when stderr isn't a
terminal, for one-shot runs (uvx, pipx run, npx) and when
``DEEPCTL_NO_UPDATE_CHECK`` is set.
"""

from __future__ import annotations

import importlib.metadata
import os
import sys
import threading
from typing import Any

from . import channel, check_cache

PYPI_URL = "https://pypi.org/pypi/deepctl/json"
NO_UPDATE_CHECK_ENV_VAR = "DEEPCTL_NO_UPDATE_CHECK"
THREAD_NAME = "deepctl-update-check"

# Environment variables that indicate a CI environment
_CI_ENV_VARS = (
    "CI",
    "GITHUB_ACTIONS",
    "GITLAB_CI",
    "TRAVIS",
    "JENKINS_URL",
    "CIRCLECI",
    "BUILDKITE",
    "TF_BUILD",
    "CODEBUILD_BUILD_ID",
)


def _is_ci() -> bool:
    """Return True if running inside a CI environment."""
    return any(os.environ.get(v) for v in _CI_ENV_VARS)


def _is_oneshot() -> bool:
    """Return True for a one-shot run (uvx, pipx run, npx): nothing to update."""
    return channel.is_oneshot()


def _opted_out() -> bool:
    """``DEEPCTL_NO_UPDATE_CHECK`` is set to anything but empty, 0 or false."""
    value = os.environ.get(NO_UPDATE_CHECK_ENV_VAR, "").strip().lower()
    return value not in ("", "0", "false", "no")


def _stderr_is_terminal() -> bool:
    try:
        return bool(sys.stderr is not None and sys.stderr.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def _is_agent() -> bool:
    """An AI agent or another non-interactive session (deepctl_core's rules)."""
    try:
        from deepctl_core.output import is_agentic, is_ai_agent
    except ImportError:
        return False
    return is_agentic() or is_ai_agent()


def _subcommand() -> str | None:
    """The command this run executes (``mcp`` for ``dg -q mcp``), if known."""
    cli = sys.modules.get("deepctl._cli")
    args = getattr(cli, "_run_args", None)
    if args is None:
        args = sys.argv[1:]
    try:
        flag_opts = cli._root_flag_opts() if cli is not None else set()
        i = 0
        while i < len(args) and args[i].startswith("-"):
            takes_value = cli is not None and cli._takes_next_token(args[i], flag_opts)
            i += 2 if takes_value else 1
    except Exception:
        return None
    return args[i] if i < len(args) else None


def suppressed(quiet: bool) -> bool:
    """Whether this run must not check for updates at all.

    Shared by the plugin-update check. Cheap: no config or network access.
    """
    return (
        quiet
        or _opted_out()
        or _is_ci()
        or not _stderr_is_terminal()
        or _is_agent()
        or _is_oneshot()
        or _subcommand() == "mcp"
    )


def _get_check_interval_seconds(quiet: bool) -> float | None:
    """Determine how many seconds must pass before the next check.

    Returns ``None`` when checks are disabled.
    """
    # Try to read check_frequency from config — but avoid importing
    # heavy modules if possible.
    try:
        from deepctl_core import Config

        config = Config()
        frequency: str = config.get("update.check_frequency", "daily")
        if not config.get("update.check_enabled", True):
            return None
    except Exception:
        frequency = "daily"

    mapping: dict[str, float | None] = {
        "daily": 86400.0,
        "weekly": 604800.0,
        "never": None,
    }
    return mapping.get(frequency, 86400.0)


def _background_check(current_version: str, result: dict[str, Any]) -> None:
    """Run the version check synchronously (called inside a thread).

    The attempt is saved just before the request goes out, so one that
    fails or never finishes still counts as this interval's check, while a
    run that ends before the request (imports take a while) doesn't.
    """
    try:
        # httpx loads here, in the thread, never on the main path. Building
        # the client (CA bundle, proxy settings) also comes before the record.
        import httpx
        from packaging import version as pkg_version

        client = httpx.Client(timeout=5.0)
    except Exception:
        return
    with client:
        check_cache.write(check_cache.UPDATE_FILE, ok=False, latest=None, source="pypi")
        try:
            response = client.get(PYPI_URL)
            response.raise_for_status()
            latest_version = response.json()["info"]["version"]
            if not isinstance(latest_version, str):
                return
            newer = pkg_version.parse(latest_version) > pkg_version.parse(
                current_version
            )
        except Exception:
            # Best effort: the failure is already recorded above.
            return

    check_cache.write(
        check_cache.UPDATE_FILE, ok=True, latest=latest_version, source="pypi"
    )
    if newer:
        result["latest"] = latest_version
        result["current"] = current_version


def _newer_saved(
    state: dict[str, Any] | None, current_version: str, interval: float
) -> tuple[str, str] | None:
    """``(current, latest)`` when the saved answer names a newer version
    whose notice wasn't shown within ``interval``."""
    if not state or state.get("ok") is not True:
        return None
    latest = state.get("latest")
    if not isinstance(latest, str):
        return None
    if not check_cache.notice_due(state, latest, interval):
        return None
    try:
        from packaging import version as pkg_version

        if pkg_version.parse(latest) <= pkg_version.parse(current_version):
            return None
    except Exception:
        return None
    return current_version, latest


# Module-level state for the background thread
_thread: threading.Thread | None = None
_result: dict[str, Any] = {}
# A notice from the saved answer, when no check ran this time.
_saved_notice: tuple[str, str] | None = None
# The check interval this run used: a notice shows at most once per interval.
_interval: float = 86400.0


def check_and_notify(
    current_version: str | None = None,
    quiet: bool = False,
) -> None:
    """Start a background version check, unless this run skips it.

    Call this **before** CLI execution. It spawns a daemon thread that
    runs concurrently. Call :func:`print_pending_notification` after CLI
    execution to display the result.

    Args:
        current_version: The running version string. If ``None``,
            detected automatically from package metadata.
        quiet: If True, suppress the check entirely.
    """
    global _thread, _result, _saved_notice, _interval
    _thread = None
    _result = {}
    _saved_notice = None

    if suppressed(quiet):
        return

    interval = _get_check_interval_seconds(quiet)
    if interval is None:
        return
    _interval = interval

    # A cache directory that can't be made private: no check, no state.
    if not check_cache.usable():
        return

    # Resolve current version
    if current_version is None:
        try:
            current_version = importlib.metadata.version("deepctl")
        except importlib.metadata.PackageNotFoundError:
            return

    state = check_cache.read(check_cache.UPDATE_FILE)
    if not check_cache.elapsed(check_cache.checked_at(state), interval):
        _saved_notice = _newer_saved(state, current_version, interval)
        return

    _thread = threading.Thread(
        target=_background_check,
        args=(current_version, _result),
        name=THREAD_NAME,
        daemon=True,
    )
    _thread.start()


def print_pending_notification() -> None:
    """Print the update notice if the check already found one.

    Call this **after** CLI execution completes. It never waits: a check
    that is still running is dropped (its answer, once saved, gives the
    notice on a later run). Without a check this run, a saved answer that
    names a newer version prints instead, at most once per interval.
    """
    global _thread, _saved_notice

    thread, _thread = _thread, None
    notice, _saved_notice = _saved_notice, None
    if thread is not None:
        if thread.is_alive():
            return
        latest = _result.get("latest")
        current = _result.get("current")
        notice = (current, latest) if latest and current else None
    if notice is None:
        return

    current, latest = notice
    # Several dg processes can finish together: only one shows it.
    if not check_cache.claim_notice(check_cache.UPDATE_FILE, latest, _interval):
        return
    from deepctl_core.output import invoked_prog, stderr_color

    command = f"{invoked_prog()} update"
    if stderr_color():
        line = (
            f"\033[33m  Update available:  {current} → {latest}"
            f"  —  run \033[1m{command}\033[0m\033[33m to upgrade\033[0m"
        )
    else:
        line = f"  Update available:  {current} → {latest}  —  run {command} to upgrade"
    try:
        sys.stderr.write(f"\n{line}\n\n")
    except (BrokenPipeError, OSError, ValueError):
        # The output stream closed (e.g. an MCP host disconnected stdio
        # after `dg mcp` finished). Nothing useful to do — the notification
        # is purely advisory.
        return
