"""Background plugin update check for deepctl.

Discovers community plugins (excluding first-party packages) and checks
PyPI for newer versions.  Runs in a daemon thread alongside the core
update check and prints notifications to stderr after CLI execution.
"""

from __future__ import annotations

import importlib.metadata
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .startup_check import _get_check_interval_seconds, _is_ci, _is_oneshot

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CACHE_DIR = Path.home() / ".cache" / "deepctl"
_PLUGIN_CACHE_FILE = _CACHE_DIR / "last_plugin_version_check"

# First-party packages are version-locked to deepctl — never check these.
_EXCLUDED_PREFIXES = ("deepctl-cmd-", "deepctl-core", "deepctl-shared-")
_EXCLUDED_EXACT = frozenset({"deepctl", "deepctl-plugin-example"})

# Hard cap on the number of plugins to check (avoid slow startups).
_MAX_PLUGINS_TO_CHECK = 10

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_excluded(name: str) -> bool:
    """Return True if *name* is a first-party package that should be skipped."""
    if name in _EXCLUDED_EXACT:
        return True
    return any(name.startswith(prefix) for prefix in _EXCLUDED_PREFIXES)


def _read_plugin_cache_timestamp() -> float:
    """Read the last plugin-check timestamp from the cache file.

    Returns:
        Unix timestamp of the last check, or ``0.0`` if not available.
    """
    try:
        return float(_PLUGIN_CACHE_FILE.read_text().strip())
    except (FileNotFoundError, ValueError, OSError):
        return 0.0


def _write_plugin_cache_timestamp() -> None:
    """Persist the current time to the plugin cache file."""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _PLUGIN_CACHE_FILE.write_text(str(time.time()))
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _discover_community_plugins() -> dict[str, str]:
    """Return ``{package_name: installed_version}`` for community plugins.

    Sources:

    1. ``importlib.metadata.distributions()`` — packages on ``sys.path``
       that expose ``deepctl.plugins`` entry points.
    2. ``plugins.json`` — isolated-venv plugins tracked by the plugin
       manager but not necessarily on ``sys.path``.
    """
    plugins: dict[str, str] = {}

    # --- Source 1: entry-point-based discovery ---
    try:
        for dist in importlib.metadata.distributions():
            # Check if this distribution provides deepctl.plugins entry points
            eps = dist.metadata.get_all("Provides-Extra") or []
            dist_name = dist.metadata["Name"]
            if dist_name is None:
                continue

            # Normalise for comparison
            normalised = dist_name.lower().replace("_", "-")

            # Check for deepctl.plugins entry points via the distribution
            try:
                dist_eps = dist.entry_points
                has_plugin_ep = any(
                    ep.group == "deepctl.plugins" for ep in dist_eps
                )
            except Exception:
                has_plugin_ep = False

            if has_plugin_ep and not _is_excluded(normalised):
                try:
                    plugins[normalised] = dist.metadata["Version"] or "0.0.0"
                except Exception:
                    pass
    except Exception:
        pass

    # --- Source 2: plugins.json for isolated-venv plugins ---
    try:
        from deepctl_core.plugin_env import get_plugin_state

        state = get_plugin_state()
        for pkg_name, info in state.get("plugins", {}).items():
            normalised = pkg_name.lower().replace("_", "-")
            if normalised not in plugins and not _is_excluded(normalised):
                version = (
                    info.get("version", "0.0.0") if isinstance(info, dict) else "0.0.0"
                )
                plugins[normalised] = version
    except Exception:
        pass

    return plugins


# ---------------------------------------------------------------------------
# PyPI check
# ---------------------------------------------------------------------------


def _check_pypi_versions(
    plugins: dict[str, str],
) -> list[dict[str, str]]:
    """Query PyPI for each plugin and return those with available updates.

    Returns:
        List of ``{"name": ..., "current": ..., "latest": ...}`` dicts
        for plugins where a newer version exists on PyPI.
    """
    if not plugins:
        return []

    updates: list[dict[str, str]] = []

    try:
        import httpx
        from packaging import version as pkg_version

        with httpx.Client(timeout=5.0) as client:
            for name, current_ver in list(plugins.items())[: _MAX_PLUGINS_TO_CHECK]:
                try:
                    resp = client.get(f"https://pypi.org/pypi/{name}/json")
                    resp.raise_for_status()
                    data = resp.json()
                    latest_ver: str = data["info"]["version"]

                    if pkg_version.parse(latest_ver) > pkg_version.parse(current_ver):
                        updates.append(
                            {
                                "name": name,
                                "current": current_ver,
                                "latest": latest_ver,
                            }
                        )
                except Exception:
                    continue
    except Exception:
        pass

    return updates


# ---------------------------------------------------------------------------
# Background thread
# ---------------------------------------------------------------------------


def _background_plugin_check(result: dict[str, Any]) -> None:
    """Run plugin discovery + PyPI check (called inside a daemon thread)."""
    try:
        plugins = _discover_community_plugins()
        if not plugins:
            return

        updates = _check_pypi_versions(plugins)
        if updates:
            result["updates"] = updates

        _write_plugin_cache_timestamp()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_thread: threading.Thread | None = None
_result: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_plugins_and_notify(quiet: bool = False) -> None:
    """Start a background plugin version check.

    Call this **before** CLI execution.  It spawns a daemon thread that
    runs concurrently.  Call :func:`print_pending_plugin_notifications`
    after CLI execution to display the results.

    Args:
        quiet: If True, suppress the check entirely.
    """
    global _thread, _result
    _result = {}

    if quiet or _is_ci() or _is_oneshot():
        return

    interval = _get_check_interval_seconds(quiet)
    if interval is None:
        return

    last_check = _read_plugin_cache_timestamp()
    if time.time() - last_check < interval:
        return

    _thread = threading.Thread(
        target=_background_plugin_check,
        args=(_result,),
        daemon=True,
    )
    _thread.start()


def print_pending_plugin_notifications() -> None:
    """Print plugin update notifications if any are pending.

    Call this **after** CLI execution completes.  It joins the background
    thread (with a short timeout) and, if updates were found, prints
    ANSI-colored lines to stderr.
    """
    global _thread

    if _thread is None:
        return

    _thread.join(timeout=3.0)
    _thread = None

    updates = _result.get("updates")
    if not updates:
        return

    if len(updates) == 1:
        u = updates[0]
        sys.stderr.write(
            f"\033[33mPlugin update available: {u['name']} {u['current']} → {u['latest']}"
            f"  —  run 'deepctl plugin update {u['name']}' to upgrade\033[0m\n"
        )
    else:
        sys.stderr.write("\033[33mPlugin updates available:\033[0m\n")
        for u in updates:
            sys.stderr.write(
                f"\033[33m  {u['name']} {u['current']} → {u['latest']}\033[0m\n"
            )
        sys.stderr.write(
            "\033[33mRun 'deepctl plugin update <name>' to upgrade\033[0m\n"
        )
