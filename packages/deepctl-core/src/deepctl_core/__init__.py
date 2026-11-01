"""Core components for deepctl.

The public names below load lazily (PEP 562): ``import deepctl_core`` costs
almost nothing, and ``from deepctl_core import Config`` imports only the
submodule that defines ``Config``. ``from deepctl_core import *`` still
resolves every name in ``__all__``, and the ``TYPE_CHECKING`` block gives
type checkers and documentation tools the same names as eager imports.
Submodules (``deepctl_core.config``, ``deepctl_core.output``, ...) can be
reached as attributes too.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .auth import AuthenticationError, AuthManager
    from .base_command import BaseCommand
    from .base_group_command import BaseGroupCommand
    from .client import DeepgramClient
    from .config import Config
    from .hosts import CredentialHostError
    from .models import (
        BaseResult,
        CodedErrorResult,
        ErrorResult,
        PluginInfo,
        ProfileInfo,
        ProfilesResult,
    )
    from .output import (
        OutputFormatter,
        agent_mode,
        get_console,
        get_output_format,
        get_status_console,
        is_agentic,
        is_ai_agent,
        print_error,
        print_info,
        print_output,
        print_success,
        print_warning,
        setup_output,
        update_output,
    )
    from .plugin_manager import PluginManager
    from .timing import (
        TimingContext,
        enable_timing,
        get_timing_summary,
        is_timing_enabled,
        print_timing_summary,
    )

# Public name -> the submodule that defines it.
_LAZY_NAMES: dict[str, str] = {
    "AuthManager": "auth",
    "AuthenticationError": "auth",
    "BaseCommand": "base_command",
    "BaseGroupCommand": "base_group_command",
    "BaseResult": "models",
    "CodedErrorResult": "models",
    "Config": "config",
    "CredentialHostError": "hosts",
    "DeepgramClient": "client",
    "ErrorResult": "models",
    "OutputFormatter": "output",
    "PluginInfo": "models",
    "PluginManager": "plugin_manager",
    "ProfileInfo": "models",
    "ProfilesResult": "models",
    "TimingContext": "timing",
    "agent_mode": "output",
    "enable_timing": "timing",
    "get_console": "output",
    "get_output_format": "output",
    "get_status_console": "output",
    "get_timing_summary": "timing",
    "is_agentic": "output",
    "is_ai_agent": "output",
    "is_timing_enabled": "timing",
    "print_error": "output",
    "print_info": "output",
    "print_output": "output",
    "print_success": "output",
    "print_timing_summary": "timing",
    "print_warning": "output",
    "setup_output": "output",
    "update_output": "output",
}

# Submodules reachable as attributes before anything imported them, as they
# were when this package imported them all eagerly.
_SUBMODULES = frozenset(
    {
        "auth",
        "base_command",
        "base_group_command",
        "client",
        "config",
        "hosts",
        "messages",
        "models",
        "output",
        "plugin_env",
        "plugin_manager",
        "safe_text",
        "skill_bundle",
        "skill_generator",
        "telemetry_context",
        "timing",
        "tools",
    }
)

__all__ = [
    "AuthManager",
    "AuthenticationError",
    "BaseCommand",
    "BaseGroupCommand",
    "BaseResult",
    "CodedErrorResult",
    "Config",
    "CredentialHostError",
    "DeepgramClient",
    "ErrorResult",
    "OutputFormatter",
    "PluginInfo",
    "PluginManager",
    "ProfileInfo",
    "ProfilesResult",
    "TimingContext",
    "agent_mode",
    "enable_timing",
    "get_console",
    "get_output_format",
    "get_status_console",
    "get_timing_summary",
    "is_agentic",
    "is_ai_agent",
    "is_timing_enabled",
    "print_error",
    "print_info",
    "print_output",
    "print_success",
    "print_timing_summary",
    "print_warning",
    "setup_output",
    "update_output",
]


# Only at run time: type checkers see the TYPE_CHECKING imports above and
# flag a misspelled name instead of typing it Any.
if not TYPE_CHECKING:

    def __getattr__(name: str) -> Any:
        """Import a public name's submodule on first use (PEP 562)."""
        submodule = _LAZY_NAMES.get(name)
        if submodule is not None:
            value = getattr(importlib.import_module(f".{submodule}", __name__), name)
        elif name in _SUBMODULES:
            value = importlib.import_module(f".{name}", __name__)
        else:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        # Cache it, so the next lookup is a plain attribute (and mock.patch works).
        globals()[name] = value
        return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__) | _SUBMODULES)
