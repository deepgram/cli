"""Models for models command."""

from __future__ import annotations

from deepctl_core import BaseResult
from pydantic import BaseModel, Field


class ModelInfo(BaseModel):
    model_id: str = ""
    name: str = ""
    version: str = ""
    language: str = ""
    model_type: str = ""  # "stt" or "tts"


class ModelsResult(BaseResult):
    models: list[ModelInfo] = Field(default_factory=list)
    count: int = 0
