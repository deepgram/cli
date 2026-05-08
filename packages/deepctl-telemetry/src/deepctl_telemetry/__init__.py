"""Opt-out phone-home telemetry for deepctl."""

from .client import init_telemetry, is_enabled
from .notice import install_help_notice, render_notice

__all__ = [
    "init_telemetry",
    "install_help_notice",
    "is_enabled",
    "render_notice",
]

__version__ = "0.0.3"  # x-release-please-version
