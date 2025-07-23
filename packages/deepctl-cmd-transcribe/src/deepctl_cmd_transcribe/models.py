"""Models for transcribe command."""

from typing import Any

from deepctl_core import BaseResult


class TranscribeResult(BaseResult):
    source: str
    model: str
    language: str
    transcript: str
    saved_to: str | None = None
    full_result: dict[str, Any]
