"""Core components for deepctl."""

from .auth import AuthenticationError, AuthManager
from .base_command import BaseCommand
from .base_group_command import BaseGroupCommand
from .client import DeepgramClient
from .config import Config
from .installation import InstallationDetector, InstallationInfo, InstallMethod
from .models import (
    BaseResult,
    ErrorResult,
    PluginInfo,
    ProfileInfo,
    ProfilesResult,
)
from .output import (
    OutputFormatter,
    get_console,
    print_error,
    print_info,
    print_output,
    print_success,
    print_warning,
    setup_output,
)
from .plugin_manager import PluginManager
from .version_check import VersionChecker, VersionInfo, format_version_message

__all__ = [
    "AuthManager",
    "AuthenticationError",
    "BaseCommand",
    "BaseGroupCommand",
    "BaseResult",
    "Config",
    "DeepgramClient",
    "ErrorResult",
    "InstallMethod",
    "InstallationDetector",
    "InstallationInfo",
    "OutputFormatter",
    "PluginInfo",
    "PluginManager",
    "ProfileInfo",
    "ProfilesResult",
    "VersionChecker",
    "VersionInfo",
    "format_version_message",
    "get_console",
    "print_error",
    "print_info",
    "print_output",
    "print_success",
    "print_warning",
    "setup_output",
]
