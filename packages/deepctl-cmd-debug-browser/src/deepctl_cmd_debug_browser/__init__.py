"""Browser debug subcommand for deepctl."""

from .command import BrowserCommand
from .models import BrowserCapabilities, BrowserCapability, BrowserDebugResult

__all__ = [
    "BrowserCapabilities",
    "BrowserCapability",
    "BrowserCommand",
    "BrowserDebugResult",
]
