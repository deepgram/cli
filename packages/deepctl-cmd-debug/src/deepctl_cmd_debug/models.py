"""Data models for debug command group."""

from typing import Optional, Dict

from deepctl_core import BaseResult


class DebugGroupResult(BaseResult):
    """Result from debug group command execution."""

    subcommands: Optional[Dict[str, str]] = None
    message: Optional[str] = None
