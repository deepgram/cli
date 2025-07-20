"""Models for transcribe command."""

from typing import Dict, Any, Optional
from deepctl_core import BaseResult


class TranscribeResult(BaseResult):
    source: str
    model: str
    language: str
    transcript: str
    saved_to: Optional[str] = None
    full_result: Dict[str, Any]
