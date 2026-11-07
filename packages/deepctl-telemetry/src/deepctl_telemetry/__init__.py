"""Error reporting for deepctl. This version sends no telemetry."""

from .client import disable_for_run, init_telemetry, is_enabled, report_exception
from .notice import install_help_notice, render_notice

__all__ = [
    "disable_for_run",
    "init_telemetry",
    "install_help_notice",
    "is_enabled",
    "render_notice",
    "report_exception",
]

__version__ = "0.0.7"  # x-release-please-version
