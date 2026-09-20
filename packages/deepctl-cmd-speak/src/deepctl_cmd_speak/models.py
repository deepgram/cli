"""Models for speak command."""

from __future__ import annotations

from deepctl_core import BaseResult
from pydantic import BaseModel, Field


class SpeakResult(BaseResult):
    output_path: str = ""
    model: str = ""
    bytes_written: int = 0
    played: bool = False


class VoiceInfo(BaseModel):
    name: str = ""
    voice_type: str = ""  # "aura" or "flux"
    language: str = ""


class SpeakVoicesResult(BaseResult):
    voices: list[VoiceInfo] = Field(default_factory=list)
    count: int = 0
