"""Update command for deepctl."""

from .command import UpdateCommand
from .installation import InstallationDetector, InstallationInfo, InstallMethod
from .models import UpdateResult
from .plugin_update_check import (
    check_plugins_and_notify,
    print_pending_plugin_notifications,
)
from .startup_check import check_and_notify, print_pending_notification
from .version_check import VersionChecker, VersionInfo, format_version_message

__all__ = [
    "InstallMethod",
    "InstallationDetector",
    "InstallationInfo",
    "UpdateCommand",
    "UpdateResult",
    "VersionChecker",
    "VersionInfo",
    "check_and_notify",
    "check_plugins_and_notify",
    "format_version_message",
    "print_pending_notification",
    "print_pending_plugin_notifications",
]
