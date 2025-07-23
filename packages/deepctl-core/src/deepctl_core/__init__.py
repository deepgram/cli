"""Core components for deepctl."""

from .auth import AuthenticationError, AuthManager
from .base_command import BaseCommand
from .base_group_command import BaseGroupCommand
from .client import DeepgramClient
from .config import Config
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

__all__ = [
    "AuthManager",
    "AuthenticationError",
    "BaseCommand",
    "BaseGroupCommand",
    "BaseResult",
    "Config",
    "DeepgramClient",
    "ErrorResult",
    "OutputFormatter",
    "PluginInfo",
    "PluginManager",
    "ProfileInfo",
    "ProfilesResult",
    "get_console",
    "print_error",
    "print_info",
    "print_output",
    "print_success",
    "print_warning",
    # Output utilities
    "setup_output",
]
