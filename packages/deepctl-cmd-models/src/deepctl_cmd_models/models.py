"""Models for models command."""

from __future__ import annotations

from deepctl_core import BaseResult
from pydantic import BaseModel, Field


class ModelInfo(BaseModel):
    model_id: str = ""
    name: str = ""
    canonical_name: str = ""  # the value to pass as the `model` parameter
    architecture: str = ""
    version: str = ""
    language: str = ""  # primary language (first entry of `languages`)
    languages: list[str] = Field(default_factory=list)
    model_type: str = ""  # "stt" or "tts"
    deprecated: bool = False
    deprecation_note: str = ""


class ModelsResult(BaseResult):
    models: list[ModelInfo] = Field(default_factory=list)
    count: int = 0
