"""Background plugin update check for deepctl.

Discovers community plugins (excluding first-party packages) and checks
PyPI for newer versions.  Runs in a daemon thread alongside the core
update check, under the same rules: skipped when the core check is,
at most once per interval with its state in
``<cache>/plugin-update-check.json``, and its notice prints after CLI
execution only if the thread already finished (dg never waits for it).
"""

from __future__ import annotations

import importlib.metadata
import sys
import threading
from typing import Any

from . import check_cache
from .startup_check import _get_check_interval_seconds, suppressed

THREAD_NAME = "deepctl-plugin-update-check"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

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
            dist_name = dist.metadata["Name"]
            if dist_name is None:
                continue

            # Normalise for comparison
            normalised = dist_name.lower().replace("_", "-")

            # Check for deepctl.plugins entry points via the distribution
            try:
                dist_eps = dist.entry_points
                has_plugin_ep = any(ep.group == "deepctl.plugins" for ep in dist_eps)
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
    record: bool = False,
) -> list[dict[str, str]]:
    """Query PyPI for each plugin and return those with available updates.

    With ``record``, the attempt is saved once the client is built, just
    before the first request goes out.

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
            if record:
                check_cache.write(
                    check_cache.PLUGIN_FILE, ok=False, latest=None, source="pypi"
                )
            for name, current_ver in list(plugins.items())[:_MAX_PLUGINS_TO_CHECK]:
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
    """Run plugin discovery + PyPI check (called inside a daemon thread).

    The attempt is saved just before the first request goes out (see
    :func:`_check_pypi_versions`), so one that fails or never finishes is
    tried again after ``check_cache.RETRY_SECONDS``, while a run that ends
    before then doesn't count at all.
    """
    try:
        plugins = _discover_community_plugins()
        updates = _check_pypi_versions(plugins, record=True) if plugins else []
    except Exception:
        return
    check_cache.write(check_cache.PLUGIN_FILE, ok=True, latest=None, source="pypi")
    if updates:
        result["updates"] = updates


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
    _thread = None
    _result = {}

    if suppressed(quiet):
        return

    interval = _get_check_interval_seconds(quiet)
    if interval is None:
        return

    # A cache directory that can't be made private: no check, no state.
    if not check_cache.usable():
        return

    if not check_cache.due(check_cache.PLUGIN_FILE, interval):
        return

    _thread = threading.Thread(
        target=_background_plugin_check,
        args=(_result,),
        name=THREAD_NAME,
        daemon=True,
    )
    _thread.start()


def print_pending_plugin_notifications() -> None:
    """Print plugin update notifications if any are pending.

    Call this **after** CLI execution completes.  It never waits: if the
    thread already finished and found updates, it prints ANSI-colored
    lines to stderr; a check that is still running is dropped.
    """
    global _thread

    thread, _thread = _thread, None
    if thread is None or thread.is_alive():
        return

    updates = _result.get("updates")
    if not updates:
        return

    from deepctl_core.output import invoked_prog, stderr_color

    prog = invoked_prog()
    if len(updates) == 1:
        u = updates[0]
        lines = [
            f"Plugin update available: {u['name']} {u['current']} → {u['latest']}"
            f"  —  run {prog} plugin update {u['name']} to upgrade"
        ]
    else:
        lines = ["Plugin updates available:"]
        lines += [f"  {u['name']} {u['current']} → {u['latest']}" for u in updates]
        lines.append(f"Run {prog} plugin update <name> to upgrade")
    if stderr_color():
        lines = [f"\033[33m{line}\033[0m" for line in lines]
    try:
        sys.stderr.write("".join(f"{line}\n" for line in lines))
    except (BrokenPipeError, OSError, ValueError):
        # The output stream closed (e.g. an MCP host disconnected stdio
        # after `dg mcp` finished). These notifications are purely advisory.
        pass
