"""Core components for deepctl."""

from .config import Config
from .auth import AuthManager, AuthenticationError
from .client import DeepgramClient
from .plugin_manager import PluginManager
from .models import ProfileInfo, ProfilesResult, PluginInfo, ErrorResult, BaseResult
from .base_command import BaseCommand
from .output import (
    setup_output,
    OutputFormatter,
    print_output,
    print_success,
    print_error,
    print_warning,
    print_info,
    get_console
)

__all__ = [
    "Config",
    "AuthManager",
    "AuthenticationError",
    "DeepgramClient",
    "PluginManager",
    "ProfileInfo",
    "ProfilesResult",
    "PluginInfo",
    "ErrorResult",
    "BaseResult",
    "BaseCommand",
    # Output utilities
    "setup_output",
    "OutputFormatter",
    "print_output",
    "print_success",
    "print_error",
    "print_warning",
    "print_info",
    "get_console"
]
