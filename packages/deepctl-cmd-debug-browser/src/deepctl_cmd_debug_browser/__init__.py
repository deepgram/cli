"""Browser debug subcommand for deepctl."""

from .command import BrowserCommand
from .models import BrowserDebugResult, BrowserCapabilities, BrowserCapability

__all__ = ["BrowserCommand", "BrowserDebugResult",
           "BrowserCapabilities", "BrowserCapability"]
