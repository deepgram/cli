"""Update command for deepctl."""

from .command import UpdateCommand
from .installation import InstallationDetector, InstallationInfo, InstallMethod
from .models import UpdateResult
from .version_check import VersionChecker, VersionInfo, format_version_message

__all__ = [
    "UpdateCommand",
    "UpdateResult",
    "InstallationDetector",
    "InstallationInfo",
    "InstallMethod",
    "VersionChecker",
    "VersionInfo",
    "format_version_message",
]
