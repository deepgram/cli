"""Centralized plugin environment logic shared between PluginManager and PluginCommand."""

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple

from ._plugin_trust import agent_plugin_venv, is_ai_agent, real_home_plugin_venv

# --- Path constants ---
PLUGIN_DIR = Path.home() / ".deepctl" / "plugins"
PLUGIN_VENV = PLUGIN_DIR / "venv"
PLUGIN_STATE_FILE = PLUGIN_DIR / "plugins.json"


class PluginPaths(NamedTuple):
    """Where the plugin environment lives for this run."""

    dir: Path
    venv: Path
    state_file: Path


def plugin_paths() -> PluginPaths | None:
    """The plugin directory, venv and state file to use for this run.

    Without an AI-agent signal these are the paths under ``~/.deepctl``.
    Under one, they come from the account's real home directory
    (``agent_plugin_venv``), never from ``$HOME``; ``None`` means the
    plugin environment must not be used at all in this run.
    """
    if is_ai_agent():
        venv = agent_plugin_venv()
        if venv is None or plugin_dir_problem(venv) is not None:
            return None
        return PluginPaths(venv.parent, venv, venv.parent / "plugins.json")
    return PluginPaths(PLUGIN_DIR, PLUGIN_VENV, PLUGIN_STATE_FILE)


def _owned_plugin_dirs(venv: Path) -> tuple[Path, Path, Path]:
    """dg's own directories on the way to the venv: ``.deepctl``,
    ``.deepctl/plugins`` and the venv itself."""
    return venv.parent.parent, venv.parent, venv


def plugin_dir_problem(venv: Path) -> tuple[Path, str] | None:
    """``(directory, why)`` for the first of dg's plugin directories (see
    :func:`_owned_plugin_dirs`) that another user owns or that the group or
    others can write, or ``None`` when there is none.

    ``why`` is a key of ``messages.W_CFG_UNTRUSTED_WHY`` (``owner`` or
    ``writable``). The venv root being private keeps everything inside it
    out of other users' reach. Directories that don't exist yet, and every
    directory on Windows, are no problem.
    """
    if os.name != "posix":
        return None
    for directory in _owned_plugin_dirs(venv):
        try:
            st = os.stat(directory)
        except OSError:
            return None
        if st.st_uid != os.getuid():
            return directory, "owner"
        if stat.S_IMODE(st.st_mode) & 0o022:
            return directory, "writable"
    return None


def make_plugin_dirs_private(venv: Path) -> None:
    """Create ``.deepctl/plugins`` owner-only (0700, whatever the umask) and
    narrow dg's own plugin directories that already exist: no group or
    other access to ``plugins`` and the venv, and no group or other write
    on ``.deepctl``."""
    dot, plugins, _ = _owned_plugin_dirs(venv)
    plugins.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "posix":
        return
    for directory, keep in ((dot, 0o7755), (plugins, 0o700), (venv, 0o700)):
        try:
            st = os.stat(directory)
            if st.st_uid == os.getuid() and stat.S_IMODE(st.st_mode) & ~keep:
                os.chmod(directory, stat.S_IMODE(st.st_mode) & keep)
        except OSError:
            continue


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


def skipped_plugin_venvs() -> list[Path]:
    """The real-home plugin venv when this run skips it (AI-agent runs only).

    It counts only when this run refuses it (:func:`plugin_paths` is
    ``None``) and its ``plugins.json``, read defensively, lists plugins. A
    venv found through ``$HOME`` is never read and never counts.
    """
    if not is_ai_agent() or plugin_paths() is not None:
        return []
    venv = real_home_plugin_venv()
    if venv is None:
        return []
    if not read_plugin_state_file(venv.parent / "plugins.json")["plugins"]:
        return []
    return [venv]


def get_venv_python(venv: Path | None = None) -> str | None:
    """Return the Python executable inside the plugin venv, or ``None``.

    Without ``venv``, the venv comes from :func:`plugin_paths`.
    """
    if venv is None:
        paths = plugin_paths()
        if paths is None:
            return None
        venv = paths.venv
    if not venv.exists():
        return None

    if sys.platform == "win32":
        python = venv / "Scripts" / "python.exe"
    else:
        python = venv / "bin" / "python"

    return str(python) if python.exists() else None


def get_venv_python_version(venv: Path | None = None) -> tuple[int, int] | None:
    """Read the (major, minor) Python version that built the plugin venv.

    Reads ``pyvenv.cfg`` from :data:`PLUGIN_VENV`. Both ``version`` (stdlib
    ``python -m venv``) and ``version_info`` (uv-created venvs) are accepted.

    Returns ``None`` when the venv doesn't exist, the cfg file is missing,
    or the version line can't be parsed — callers should treat ``None`` as
    "unknown" and skip ABI-mismatch checks rather than failing loudly.
    """
    venv = PLUGIN_VENV if venv is None else venv
    if not venv.exists():
        return None

    cfg = venv / "pyvenv.cfg"
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


def get_venv_site_packages(venv: Path | None = None) -> Path | None:
    """Return the ``site-packages`` directory inside the plugin venv.

    On Unix this is typically ``lib/pythonX.Y/site-packages``.
    On Windows it's ``Lib/site-packages``.

    Returns ``None`` if the venv doesn't exist or the path can't be
    determined.
    """
    venv = PLUGIN_VENV if venv is None else venv
    if not venv.exists():
        return None

    if sys.platform == "win32":
        sp = venv / "Lib" / "site-packages"
        return sp if sp.exists() else None

    # Unix: lib/pythonX.Y/site-packages — find the actual version dir
    lib_dir = venv / "lib"
    if not lib_dir.exists():
        return None

    for child in sorted(lib_dir.iterdir()):
        if child.name.startswith("python") and child.is_dir():
            sp = child / "site-packages"
            if sp.exists():
                return sp

    return None


def get_plugin_state(state_file: Path | None = None) -> dict[str, Any]:
    """Read and return the plugin state from ``plugins.json``.

    Without ``state_file``, the file comes from :func:`plugin_paths`.
    Returns a dict with at least ``{"plugins": {}}``.
    """
    if state_file is None:
        paths = plugin_paths()
        if paths is None:
            return {"plugins": {}}
        state_file = paths.state_file
    return read_plugin_state_file(state_file)


# The largest plugins.json read; a bigger file is treated as empty.
MAX_STATE_FILE_BYTES = 1024 * 1024


def read_plugin_state_file(path: Path) -> dict[str, Any]:
    """Read a ``plugins.json`` defensively.

    The file is opened without following a final symlink and without
    blocking (a FIFO doesn't hang the read), must be a regular file of at
    most :data:`MAX_STATE_FILE_BYTES`, and must hold a JSON object whose
    ``plugins`` value is an object. Anything else reads as no plugins.
    """
    empty: dict[str, Any] = {"plugins": {}}
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except (OSError, ValueError):
        return empty
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_STATE_FILE_BYTES:
            return empty
        chunks: list[bytes] = []
        size = 0
        while size <= MAX_STATE_FILE_BYTES:
            chunk = os.read(fd, MAX_STATE_FILE_BYTES + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        data = b"".join(chunks)
    except OSError:
        return empty
    finally:
        os.close(fd)
    if len(data) > MAX_STATE_FILE_BYTES:
        return empty
    try:
        result = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return empty
    if not isinstance(result, dict) or not isinstance(result.get("plugins"), dict):
        return empty
    return result


def save_plugin_state(state: dict[str, Any], paths: PluginPaths | None = None) -> None:
    """Persist the plugin state to ``plugins.json``.

    Without ``paths``, they come from :func:`plugin_paths`. Raises
    ``RuntimeError`` when the plugin environment can't be used in this run.
    """
    paths = plugin_paths() if paths is None else paths
    if paths is None:
        raise RuntimeError("the plugin environment is not available in this run")
    make_plugin_dirs_private(paths.venv)
    paths.state_file.write_text(json.dumps(state, indent=2))
