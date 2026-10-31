"""Models for speak command."""

from __future__ import annotations

from typing import Any

from deepctl_core import BaseResult, CodedErrorResult
from pydantic import BaseModel, Field


class SpeakResult(BaseResult):
    output_path: str = ""
    model: str = ""
    bytes_written: int = 0
    played: bool = False


class VoiceInfo(BaseModel):
    name: str = ""
    voice_type: str = ""  # "aura" or "flux"
    language: str = ""  # joined display string, e.g. "fr, fr-FR"
    languages: list[str] = Field(default_factory=list)


class SpeakVoicesResult(BaseResult):
    voices: list[VoiceInfo] = Field(default_factory=list)
    count: int = 0


class SpeakCodedError(CodedErrorResult):
    """A coded error from ``dg speak``, keys in the catalogue's order.

    ``details`` is left out of the payload when there are none.
    """

    next_step: str | None = None
    schema_version: int = 1
    details: dict[str, Any] | None = None

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        data = super().model_dump(**kwargs)
        if data.get("details") is None:
            data.pop("details", None)
        return data
