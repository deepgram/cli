"""Data models for debug command group."""

from deepctl_core import BaseResult


class DebugGroupResult(BaseResult):
    """Result from debug group command execution."""

    subcommands: dict[str, str] | None = None
    message: str | None = None
