"""Install-method-aware plugin installation strategies.

Each strategy knows how to install and uninstall plugins for a specific
deepctl installation method (pip, pipx, uv tool, Homebrew/system, etc.).
"""

from __future__ import annotations

import subprocess
import sys
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from deepctl_cmd_update.installation import InstallMethod
from deepctl_core.output import print_info, print_warning

if TYPE_CHECKING:
    from .models import PluginInstallOptions


class PluginInstallStrategy(ABC):
    """Base class for plugin installation strategies."""

    @abstractmethod
    def install(self, options: PluginInstallOptions) -> tuple[bool, str]:
        """Install a plugin.

        Args:
            options: Installation options.

        Returns:
            Tuple of (success, error_or_output_message).
        """

    @abstractmethod
    def uninstall(self, package: str) -> tuple[bool, str]:
        """Uninstall a plugin.

        Args:
            package: Package name to remove.

        Returns:
            Tuple of (success, error_or_output_message).
        """

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _build_package_spec(options: PluginInstallOptions) -> str:
        """Build the pip-compatible package specifier string."""
        if options.git_url:
            return options.git_url
        if options.version:
            return f"{options.package}=={options.version}"
        return options.package

    @staticmethod
    def _run(cmd: list[str]) -> tuple[bool, str]:
        """Run a subprocess, returning (success, stderr_or_stdout)."""
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return True, result.stdout
        except subprocess.CalledProcessError as e:
            return False, e.stderr or str(e)
        except FileNotFoundError:
            return False, f"Command not found: {cmd[0]}"


# ── Concrete strategies ──────────────────────────────────────────────────


class PipStrategy(PluginInstallStrategy):
    """Strategy for standard pip (venv) installations.

    Installs directly into the current Python environment.
    """

    def install(self, options: PluginInstallOptions) -> tuple[bool, str]:
        cmd = [sys.executable, "-m", "pip", "install"]
        cmd.extend(self._extra_flags(options))
        cmd.append(self._build_package_spec(options))
        return self._run(cmd)

    def uninstall(self, package: str) -> tuple[bool, str]:
        return self._run([sys.executable, "-m", "pip", "uninstall", "-y", package])

    @staticmethod
    def _extra_flags(options: PluginInstallOptions) -> list[str]:
        flags: list[str] = []
        if options.upgrade:
            flags.append("--upgrade")
        if options.pre:
            flags.append("--pre")
        if options.force_reinstall:
            flags.append("--force-reinstall")
        if options.index_url:
            flags.extend(["--index-url", options.index_url])
        if options.extra_index_url:
            flags.extend(["--extra-index-url", options.extra_index_url])
        if options.editable:
            flags.append("--editable")
        return flags


class PipxStrategy(PluginInstallStrategy):
    """Strategy for pipx-managed installations.

    Uses ``pipx inject deepctl <plugin>`` to add the plugin into
    pipx's managed venv for deepctl.
    """

    def install(self, options: PluginInstallOptions) -> tuple[bool, str]:
        spec = self._build_package_spec(options)
        # Try ``pipx inject`` first (pipx >= 1.0)
        ok, out = self._run(["pipx", "inject", "deepctl", spec])
        if ok:
            return ok, out
        # Fallback for older pipx: runpip
        print_info("Falling back to pipx runpip...")
        return self._run(["pipx", "runpip", "deepctl", "install", spec])

    def uninstall(self, package: str) -> tuple[bool, str]:
        ok, out = self._run(["pipx", "runpip", "deepctl", "uninstall", "-y", package])
        if ok:
            return ok, out
        # Try uninject (pipx >= 1.2)
        return self._run(["pipx", "uninject", "deepctl", package])


class UvToolStrategy(PluginInstallStrategy):
    """Strategy for ``uv tool install`` installations.

    Re-installs deepctl with the extra plugin as ``--with`` dependency.
    """

    def install(self, options: PluginInstallOptions) -> tuple[bool, str]:
        spec = self._build_package_spec(options)
        return self._run(["uv", "tool", "install", "deepctl", "--with", spec])

    def uninstall(self, package: str) -> tuple[bool, str]:
        # uv tool doesn't have a direct "remove dependency" — reinstall
        # without the plugin.  For now, use pip inside uv's managed venv.
        return self._run(
            [
                "uv",
                "tool",
                "run",
                "--from",
                "deepctl",
                "pip",
                "uninstall",
                "-y",
                package,
            ]
        )


class IsolatedVenvStrategy(PluginInstallStrategy):
    """Strategy for Homebrew, system, frozen binary, and unknown installs.

    Uses an isolated venv at ``~/.deepctl/plugins/venv/``.
    """

    def __init__(self, venv_python: str) -> None:
        self._venv_python = venv_python

    def install(self, options: PluginInstallOptions) -> tuple[bool, str]:
        cmd = [self._venv_python, "-m", "pip", "install"]
        if options.upgrade:
            cmd.append("--upgrade")
        if options.pre:
            cmd.append("--pre")
        if options.force_reinstall:
            cmd.append("--force-reinstall")
        if options.index_url:
            cmd.extend(["--index-url", options.index_url])
        if options.extra_index_url:
            cmd.extend(["--extra-index-url", options.extra_index_url])
        if options.editable:
            cmd.append("--editable")
        cmd.append(self._build_package_spec(options))
        return self._run(cmd)

    def uninstall(self, package: str) -> tuple[bool, str]:
        return self._run([self._venv_python, "-m", "pip", "uninstall", "-y", package])


class DevelopmentStrategy(PluginInstallStrategy):
    """Strategy for development (editable) installations.

    Installs directly into the current environment just like PipStrategy,
    but recognises that the CLI is editable-installed.
    """

    def install(self, options: PluginInstallOptions) -> tuple[bool, str]:
        cmd = [sys.executable, "-m", "pip", "install"]
        if options.editable:
            cmd.append("--editable")
        cmd.append(self._build_package_spec(options))
        return self._run(cmd)

    def uninstall(self, package: str) -> tuple[bool, str]:
        return self._run([sys.executable, "-m", "pip", "uninstall", "-y", package])


class EphemeralStrategy(PluginInstallStrategy):
    """Strategy for ephemeral execution (uvx / pipx run).

    Plugins cannot persist in ephemeral environments, so this strategy
    always warns and bails.
    """

    def __init__(self, method: InstallMethod) -> None:
        self._method = method

    def install(self, options: PluginInstallOptions) -> tuple[bool, str]:
        if self._method == InstallMethod.UVX:
            suggestion = "uv tool install deepctl"
        else:
            suggestion = "pipx install deepctl"

        msg = (
            f"Plugins cannot persist in ephemeral ({self._method.value}) "
            f"environments.  Install deepctl permanently first:\n"
            f"  {suggestion}\n"
            f"Then run: deepctl plugin install {options.package}"
        )
        print_warning(msg)
        return False, msg

    def uninstall(self, package: str) -> tuple[bool, str]:
        msg = (
            f"Plugins cannot be managed in ephemeral ({self._method.value}) "
            "environments."
        )
        print_warning(msg)
        return False, msg


# ── Factory ──────────────────────────────────────────────────────────────


def get_strategy(
    method: InstallMethod,
    venv_python: str | None = None,
) -> PluginInstallStrategy:
    """Return the correct strategy for the detected installation method.

    Args:
        method: How deepctl was installed.
        venv_python: Python executable inside the plugin venv (required
            for :class:`IsolatedVenvStrategy`).

    Returns:
        A :class:`PluginInstallStrategy` instance.
    """
    if method == InstallMethod.PIP:
        return PipStrategy()

    if method == InstallMethod.UV:
        return PipStrategy()  # uv venv acts like pip

    if method == InstallMethod.PIPX:
        return PipxStrategy()

    if method == InstallMethod.UV_TOOL:
        return UvToolStrategy()

    if method == InstallMethod.DEVELOPMENT:
        return DevelopmentStrategy()

    if method in (InstallMethod.UVX, InstallMethod.PIPX_RUN):
        return EphemeralStrategy(method)

    # HOMEBREW, SYSTEM, UNKNOWN → isolated venv
    if venv_python is None:
        raise ValueError(
            f"venv_python is required for IsolatedVenvStrategy (method={method.value})"
        )
    return IsolatedVenvStrategy(venv_python)
