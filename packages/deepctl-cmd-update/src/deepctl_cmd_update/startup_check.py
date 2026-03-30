"""Background startup update check for deepctl.

Runs a non-intrusive version check in a daemon thread during normal CLI
execution, then prints a one-line notification to stderr *after* the
command output completes.
"""

from __future__ import annotations

import importlib.metadata
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

# Sentinel used when the background check is suppressed or has no result
_NO_RESULT: dict[str, Any] = {}

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

# Cache file for last-check timestamp (separate from YAML config to
# avoid race conditions)
_CACHE_DIR = Path.home() / ".cache" / "deepctl"
_CACHE_FILE = _CACHE_DIR / "last_version_check"


def _is_ci() -> bool:
    """Return True if running inside a CI environment."""
    return any(os.environ.get(v) for v in _CI_ENV_VARS)


def _is_oneshot() -> bool:
    """Return True if running in an ephemeral one-shot environment."""
    # uvx
    if "UV_INTERNAL__PARENT_INTERPRETER" in os.environ:
        return True
    # pipx run
    exe_path = str(Path(sys.executable).resolve())
    pipx_home = os.environ.get("PIPX_HOME", str(Path.home() / ".local" / "pipx"))
    pipx_cache = str(Path(pipx_home) / ".cache")
    return bool(exe_path.startswith(pipx_cache))


def _read_cache_timestamp() -> float:
    """Read the last-check timestamp from the cache file.

    Returns:
        Unix timestamp of the last check, or ``0.0`` if not available.
    """
    try:
        return float(_CACHE_FILE.read_text().strip())
    except (FileNotFoundError, ValueError, OSError):
        return 0.0


def _write_cache_timestamp() -> None:
    """Persist the current time to the cache file."""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(str(time.time()))
    except OSError:
        pass


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
    """Run the version check synchronously (called inside a thread)."""
    try:
        import httpx

        response = httpx.get(
            "https://pypi.org/pypi/deepctl/json",
            timeout=5.0,
        )
        response.raise_for_status()
        data = response.json()
        latest_version: str = data["info"]["version"]

        from packaging import version as pkg_version

        current = pkg_version.parse(current_version)
        latest = pkg_version.parse(latest_version)

        if latest > current:
            result["latest"] = latest_version
            result["current"] = current_version

        _write_cache_timestamp()
    except Exception:
        # Silently swallow — this is best-effort
        pass


# Module-level state for the background thread
_thread: threading.Thread | None = None
_result: dict[str, Any] = {}


def check_and_notify(
    current_version: str | None = None,
    quiet: bool = False,
) -> None:
    """Start a background version check.

    Call this **before** CLI execution. It spawns a daemon thread that
    runs concurrently. Call :func:`print_pending_notification` after CLI
    execution to display the result.

    Args:
        current_version: The running version string. If ``None``,
            detected automatically from package metadata.
        quiet: If True, suppress the check entirely.
    """
    global _thread, _result
    _result = {}

    # Suppress in quiet mode, CI, or one-shot execution
    if quiet or _is_ci() or _is_oneshot():
        return

    # Check interval
    interval = _get_check_interval_seconds(quiet)
    if interval is None:
        return

    last_check = _read_cache_timestamp()
    if time.time() - last_check < interval:
        return

    # Resolve current version
    if current_version is None:
        try:
            current_version = importlib.metadata.version("deepctl")
        except importlib.metadata.PackageNotFoundError:
            return

    _thread = threading.Thread(
        target=_background_check,
        args=(current_version, _result),
        daemon=True,
    )
    _thread.start()


def print_pending_notification() -> None:
    """Print the update notification if one is pending.

    Call this **after** CLI execution completes. It joins the background
    thread (with a short timeout) and, if an update was found, prints a
    single ANSI-colored line to stderr.
    """
    global _thread

    if _thread is None:
        return

    _thread.join(timeout=2.0)
    _thread = None

    latest = _result.get("latest")
    current = _result.get("current")
    if latest and current:
        sys.stderr.write(
            f"\n\033[33m  Update available:  {current} → {latest}"
            f"  —  run \033[1mdg update\033[0m\033[33m to upgrade\033[0m\n\n"
        )
