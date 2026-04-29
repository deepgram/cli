"""Centralized plugin environment logic shared between PluginManager and PluginCommand."""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# --- Path constants ---
PLUGIN_DIR = Path.home() / ".deepctl" / "plugins"
PLUGIN_VENV = PLUGIN_DIR / "venv"
PLUGIN_STATE_FILE = PLUGIN_DIR / "plugins.json"


def is_frozen() -> bool:
    """Detect if running inside a PyInstaller (or similar) frozen binary.

    PyInstaller sets ``sys.frozen = True`` on the bundled interpreter,
    which means ``sys.executable`` points to the binary—not a usable
    Python interpreter—so we can't call ``sys.executable -m venv``.
    """
    return getattr(sys, "frozen", False)


def find_system_python(min_version: tuple[int, int] = (3, 10)) -> str | None:
    """Search PATH and common locations for a Python >= *min_version*.

    Returns the absolute path to the first suitable interpreter found,
    or ``None`` if nothing qualifies.
    """
    candidates = [
        "python3",
        "python",
    ]

    # Add version-specific candidates (python3.10, python3.11, ...)
    for minor in range(min_version[1], 20):
        candidates.append(f"python{min_version[0]}.{minor}")

    # Extra Homebrew-managed paths on macOS
    homebrew_prefixes = [
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
    ]

    search_paths: list[str] = []
    # PATH entries first
    for candidate in candidates:
        full = shutil.which(candidate)
        if full and full not in search_paths:
            search_paths.append(full)

    # Homebrew-specific paths
    for prefix in homebrew_prefixes:
        for candidate in candidates:
            full_path = prefix / candidate
            if full_path.exists() and str(full_path) not in search_paths:
                search_paths.append(str(full_path))

    # Check each candidate
    for python_path in search_paths:
        try:
            result = subprocess.run(
                [
                    python_path,
                    "-c",
                    "import sys; print(sys.version_info.major, sys.version_info.minor)",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split()
                if len(parts) == 2:
                    major, minor = int(parts[0]), int(parts[1])
                    if (major, minor) >= min_version:
                        return python_path
        except (subprocess.TimeoutExpired, OSError, ValueError):
            continue

    return None


def get_venv_python() -> str | None:
    """Return the Python executable inside the plugin venv, or ``None``."""
    if not PLUGIN_VENV.exists():
        return None

    if sys.platform == "win32":
        python = PLUGIN_VENV / "Scripts" / "python.exe"
    else:
        python = PLUGIN_VENV / "bin" / "python"

    return str(python) if python.exists() else None


def get_venv_python_version() -> tuple[int, int] | None:
    """Read the (major, minor) Python version that built the plugin venv.

    Reads ``pyvenv.cfg`` from :data:`PLUGIN_VENV`. Both ``version`` (stdlib
    ``python -m venv``) and ``version_info`` (uv-created venvs) are accepted.

    Returns ``None`` when the venv doesn't exist, the cfg file is missing,
    or the version line can't be parsed — callers should treat ``None`` as
    "unknown" and skip ABI-mismatch checks rather than failing loudly.
    """
    if not PLUGIN_VENV.exists():
        return None

    cfg = PLUGIN_VENV / "pyvenv.cfg"
    if not cfg.exists():
        return None

    try:
        text = cfg.read_text()
    except OSError:
        return None

    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        if key.strip() not in ("version", "version_info"):
            continue
        parts = value.strip().split(".")
        if len(parts) < 2:
            continue
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            continue

    return None


def get_venv_site_packages() -> Path | None:
    """Return the ``site-packages`` directory inside the plugin venv.

    On Unix this is typically ``lib/pythonX.Y/site-packages``.
    On Windows it's ``Lib/site-packages``.

    Returns ``None`` if the venv doesn't exist or the path can't be
    determined.
    """
    if not PLUGIN_VENV.exists():
        return None

    if sys.platform == "win32":
        sp = PLUGIN_VENV / "Lib" / "site-packages"
        return sp if sp.exists() else None

    # Unix: lib/pythonX.Y/site-packages — find the actual version dir
    lib_dir = PLUGIN_VENV / "lib"
    if not lib_dir.exists():
        return None

    for child in sorted(lib_dir.iterdir()):
        if child.name.startswith("python") and child.is_dir():
            sp = child / "site-packages"
            if sp.exists():
                return sp

    return None


def get_plugin_state() -> dict[str, Any]:
    """Read and return the plugin state from ``plugins.json``.

    Returns a dict with at least ``{"plugins": {}}``.
    """
    if PLUGIN_STATE_FILE.exists():
        try:
            result: dict[str, Any] = json.loads(PLUGIN_STATE_FILE.read_text())
            return result
        except Exception:
            return {"plugins": {}}
    return {"plugins": {}}


def save_plugin_state(state: dict[str, Any]) -> None:
    """Persist the plugin state to ``plugins.json``."""
    PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
    PLUGIN_STATE_FILE.write_text(json.dumps(state, indent=2))
