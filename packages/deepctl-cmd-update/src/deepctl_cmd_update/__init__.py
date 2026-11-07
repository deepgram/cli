"""Update command for deepctl.

The names below load on first use (PEP 562), so importing
``deepctl_cmd_update.startup_check``, which ``dg`` does on every run, doesn't
also import the update command and its dependencies.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .command import UpdateCommand
    from .installation import InstallationDetector, InstallationInfo, InstallMethod
    from .models import UpdateResult
    from .plugin_update_check import (
        check_plugins_and_notify,
        print_pending_plugin_notifications,
    )
    from .startup_check import check_and_notify, print_pending_notification
    from .version_check import VersionChecker, VersionInfo, format_version_message

_LAZY = {
    "UpdateCommand": ".command",
    "InstallationDetector": ".installation",
    "InstallationInfo": ".installation",
    "InstallMethod": ".installation",
    "UpdateResult": ".models",
    "check_plugins_and_notify": ".plugin_update_check",
    "print_pending_plugin_notifications": ".plugin_update_check",
    "check_and_notify": ".startup_check",
    "print_pending_notification": ".startup_check",
    "VersionChecker": ".version_check",
    "VersionInfo": ".version_check",
    "format_version_message": ".version_check",
}

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


if not TYPE_CHECKING:

    def __getattr__(name: str) -> Any:
        module = _LAZY.get(name)
        if module is None:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        value = getattr(importlib.import_module(module, __name__), name)
        globals()[name] = value
        return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
