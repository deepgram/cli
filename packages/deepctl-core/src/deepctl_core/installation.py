"""Installation detection for deepctl."""

import os
import site
import subprocess
import sys
from enum import Enum
from pathlib import Path

from pydantic import BaseModel


class InstallMethod(str, Enum):
    """Supported installation methods."""

    PIP = "pip"
    PIPX = "pipx"
    UV = "uv"
    SYSTEM = "system"
    DEVELOPMENT = "development"
    UNKNOWN = "unknown"


class InstallationInfo(BaseModel):
    """Information about the current installation."""

    method: InstallMethod
    path: str
    virtual_env: bool
    editable: bool
    python_executable: str
    package_location: str | None = None


class InstallationDetector:
    """Detects how deepctl was installed."""

    def __init__(self) -> None:
        """Initialize the detector."""
        self.executable_path = sys.executable
        self.base_prefix = getattr(sys, "base_prefix", sys.prefix)
        self.prefix = sys.prefix

    def detect(self) -> InstallationInfo:
        """Detect installation method and gather info.

        Returns:
            Installation information
        """
        # Check if we're in a virtual environment
        in_venv = self._in_virtual_env()

        # Get package location
        package_location = self._get_package_location()

        # Check for editable installation
        is_editable = self._is_editable_install(package_location)

        # Detect installation method
        method = self._detect_method(package_location, in_venv, is_editable)

        return InstallationInfo(
            method=method,
            path=str(package_location or sys.prefix),
            virtual_env=in_venv,
            editable=is_editable,
            python_executable=self.executable_path,
            package_location=(
                str(package_location) if package_location else None
            ),
        )

    def get_update_command(self, method: InstallMethod) -> str | None:
        """Get appropriate update command for installation method.

        Args:
            method: The installation method

        Returns:
            Update command or None if not applicable
        """
        commands = {
            InstallMethod.PIP: "pip install --upgrade deepctl",
            InstallMethod.PIPX: "pipx upgrade deepctl",
            InstallMethod.UV: "uv pip install --upgrade deepctl",
            InstallMethod.SYSTEM: None,  # Handled separately
            InstallMethod.DEVELOPMENT: None,  # Can't auto-update
            InstallMethod.UNKNOWN: None,
        }
        return commands.get(method)

    def get_update_instructions(self, info: InstallationInfo) -> str:
        """Get detailed update instructions based on installation info.

        Args:
            info: Installation information

        Returns:
            Human-readable update instructions
        """
        if info.method == InstallMethod.SYSTEM:
            # Try to detect the system package manager
            if sys.platform.startswith("darwin"):
                return "Please update using Homebrew: brew upgrade deepctl"
            elif sys.platform.startswith("linux"):
                # Check for common package managers
                if Path("/etc/debian_version").exists():
                    return "Please update using apt: sudo apt update && sudo apt upgrade deepctl"
                elif Path("/etc/redhat-release").exists():
                    return (
                        "Please update using yum/dnf: sudo dnf upgrade deepctl"
                    )
            return "Please use your system package manager to update deepctl"

        elif info.method == InstallMethod.DEVELOPMENT:
            return (
                "Development installation detected. "
                "Please pull the latest changes from the repository:\n"
                "git pull origin main"
            )

        elif info.method == InstallMethod.UNKNOWN:
            return (
                "Unable to detect installation method. "
                "Please update deepctl using the same method you used to install it."
            )

        # For pip, pipx, uv - get the command
        command = self.get_update_command(info.method)
        if command:
            if info.virtual_env and info.method == InstallMethod.PIP:
                return (
                    f"You are in a virtual environment. "
                    f"Make sure it's activated and run:\n{command}"
                )
            return f"Run: {command}"

        return "Update method not available for this installation type"

    def _in_virtual_env(self) -> bool:
        """Check if we're running in a virtual environment.

        Returns:
            True if in virtual environment
        """
        # Check multiple indicators
        return (
            hasattr(sys, "real_prefix")  # virtualenv
            or (
                hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix
            )  # venv
            or os.environ.get("VIRTUAL_ENV") is not None  # Both
            or os.environ.get("CONDA_DEFAULT_ENV") is not None  # Conda
        )

    def _get_package_location(self) -> Path | None:
        """Get the location of the deepctl package.

        Returns:
            Path to package or None
        """
        try:
            import deepctl

            package_file = deepctl.__file__
            if package_file:
                return Path(package_file).parent.parent
        except ImportError:
            pass

        # Try to find in site-packages
        for site_dir in [*site.getsitepackages(), site.getusersitepackages()]:
            if site_dir:
                deepctl_path = Path(site_dir) / "deepctl"
                if deepctl_path.exists():
                    return deepctl_path.parent

        return None

    def _is_editable_install(self, package_location: Path | None) -> bool:
        """Check if this is an editable installation.

        Args:
            package_location: Path to package

        Returns:
            True if editable installation
        """
        if not package_location:
            return False

        # Check for .egg-link file (pip editable installs)
        for site_dir in [*site.getsitepackages(), site.getusersitepackages()]:
            if site_dir:
                egg_link = Path(site_dir) / "deepctl.egg-link"
                if egg_link.exists():
                    return True

        # Check for __editable_install__ marker
        editable_marker = package_location / "__editable_install__"
        if editable_marker.exists():
            return True

        # Check if package location is outside site-packages (likely dev)
        site_packages_paths = [Path(p) for p in site.getsitepackages()]
        if not any(
            package_location.is_relative_to(p) for p in site_packages_paths
        ):
            # Check if it's in a git repository
            git_dir = package_location
            while git_dir.parent != git_dir:
                if (git_dir / ".git").exists():
                    return True
                git_dir = git_dir.parent

        return False

    def _detect_method(
        self,
        package_location: Path | None,
        in_venv: bool,
        is_editable: bool,
    ) -> InstallMethod:
        """Detect the installation method.

        Args:
            package_location: Path to package
            in_venv: Whether in virtual environment
            is_editable: Whether editable install

        Returns:
            Detected installation method
        """
        # Development installation
        if is_editable:
            return InstallMethod.DEVELOPMENT

        # Check for pipx
        if self._is_pipx_install():
            return InstallMethod.PIPX

        # Check for uv
        if self._is_uv_install():
            return InstallMethod.UV

        # Check for system installation
        if not in_venv and self._is_system_install(package_location):
            return InstallMethod.SYSTEM

        # Default to pip if in venv or user site-packages
        if in_venv or (
            package_location
            and Path(site.getusersitepackages()) in package_location.parents
        ):
            return InstallMethod.PIP

        return InstallMethod.UNKNOWN

    def _is_pipx_install(self) -> bool:
        """Check if installed via pipx.

        Returns:
            True if pipx installation
        """
        # Check for pipx environment variables
        if "PIPX_HOME" in os.environ or "PIPX_BIN_DIR" in os.environ:
            return True

        # Check if executable is in a pipx-managed location
        exe_path = Path(sys.executable)
        return (
            "pipx" in str(exe_path)
            or (exe_path.parent.parent / ".pipx").exists()
        )

    def _is_uv_install(self) -> bool:
        """Check if installed via uv.

        Returns:
            True if uv installation
        """
        # Check for uv markers
        if "UV_PROJECT" in os.environ:
            return True

        # Check if uv is managing the environment
        try:
            result = subprocess.run(
                ["uv", "--version"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                # uv is available, check if it manages this env
                exe_path = Path(sys.executable)
                if ".uv" in str(exe_path) or "uv-python" in str(exe_path):
                    return True
        except (subprocess.SubprocessError, FileNotFoundError):
            pass

        return False

    def _is_system_install(self, package_location: Path | None) -> bool:
        """Check if this is a system installation.

        Args:
            package_location: Path to package

        Returns:
            True if system installation
        """
        if not package_location:
            return False

        # Common system paths
        system_paths = [
            Path("/usr/lib"),
            Path("/usr/local/lib"),
            Path("/opt"),
            Path("/System/Library"),  # macOS
        ]

        return any(
            package_location.is_relative_to(p)
            for p in system_paths
            if p.exists()
        )
