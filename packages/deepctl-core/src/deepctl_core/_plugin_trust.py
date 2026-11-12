"""Trusted locations for plugin settings and the plugin venv.

Built on the config module's real-home trust helpers: the account's home
comes from the OS (``HOME`` aside, the known-folder API on Windows), and in
an AI agent session the user config is read only through the checked open
of the trust root (no symlinked component, owned by this user, one link, no
group or other write).
"""

import os
from pathlib import Path

import yaml

from . import config as _config
from .output import is_ai_agent

__all__ = [
    "agent_plugin_venv",
    "allow_override_names",
    "contained_in",
    "is_ai_agent",
    "real_home_plugin_venv",
    "trusted_user_config_file",
]


def _agent_trust_root() -> Path | None:
    """The trust root's config path, unless it lies inside the working
    directory (and that directory isn't the home itself)."""
    location = _config._trust_root_location()
    home = _config._real_home()
    if location is None or home is None:
        return None
    real = location[1]
    try:
        resolved = os.path.realpath(real)
        cwd = os.path.realpath(os.getcwd())
        at_home = cwd == os.path.realpath(home)
    except (OSError, ValueError):
        return None
    if not at_home and _under(resolved, cwd):
        return None
    return real


def trusted_user_config_file() -> Path | None:
    """The user config file at its default location. Under an AI-agent
    signal, only the one derived from the real home directory."""
    if is_ai_agent():
        return _agent_trust_root()
    return _config._default_config_file()


def allow_override_names() -> frozenset[str]:
    """Command paths listed in ``plugins.allow_override`` of the trusted file.

    Never read from a ``--config`` file, ``deepgram.yaml`` or the environment.
    Under an AI-agent signal the file must also pass the trust-root checks.
    Any read or parse problem yields an empty set.
    """
    try:
        if is_ai_agent():
            if _agent_trust_root() is None:
                return frozenset()
            root = _config._open_trust_root()
            data = root[1] if root is not None else None
        else:
            _, data = _config._read_yaml_fd(_config._default_config_file())
    except (OSError, ValueError, yaml.YAMLError):
        return frozenset()
    plugins = data.get("plugins") if isinstance(data, dict) else None
    names = plugins.get("allow_override") if isinstance(plugins, dict) else None
    if not isinstance(names, list):
        return frozenset()
    return frozenset(" ".join(n.split()) for n in names if isinstance(n, str))


def _under(path: str, directory: str) -> bool:
    return path == directory or path.startswith(directory.rstrip(os.sep) + os.sep)


def real_home_plugin_venv() -> Path | None:
    """``.deepctl/plugins/venv`` under the real home directory, unchecked."""
    home = _config._real_home()
    return None if home is None else Path(home, ".deepctl", "plugins", "venv")


def agent_plugin_venv() -> Path | None:
    """The plugin venv to load under an AI-agent signal, or ``None``.

    It is ``.deepctl/plugins/venv`` under the account's real home directory
    (``HOME`` aside). ``None`` when the home can't be read, when a directory
    below the home on the way to the venv is a symlink, or when the venv lies
    inside the working directory and that directory isn't the home itself.
    """
    home = _config._real_home()
    if home is None:
        return None
    try:
        real_home = os.path.realpath(home)
        venv = os.path.join(real_home, ".deepctl", "plugins", "venv")
        if os.path.realpath(venv) != venv:
            return None
        cwd = os.path.realpath(os.getcwd())
    except (OSError, ValueError):
        return None
    if cwd != real_home and _under(venv, cwd):
        return None
    return Path(venv)


def contained_in(path: Path, directory: Path) -> bool:
    """True when ``path`` resolves inside ``directory`` (resolved)."""
    try:
        return _under(os.path.realpath(path), os.path.realpath(directory))
    except (OSError, ValueError):
        return False
