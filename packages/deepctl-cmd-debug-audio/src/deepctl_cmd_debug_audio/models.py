"""Data models for audio debug command."""

from typing import Optional
from deepctl_core import BaseResult


class AudioDebugResult(BaseResult):
    """Result from audio debug command execution."""

    message: str
    audio_file: Optional[str] = None
