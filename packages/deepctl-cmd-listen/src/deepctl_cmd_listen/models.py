"""Models for the unified listen/transcribe command."""

from __future__ import annotations

from typing import Any

from deepctl_core import BaseResult, CodedErrorResult
from pydantic import PrivateAttr


class ListenResult(BaseResult):
    # How audio was provided
    source: str = ""  # "file" | "url" | "mic" | "stdin"
    source_path: str = ""  # actual path or URL
    mode: str = ""  # "prerecorded" | "live"

    # What was requested
    model: str = ""
    language: str = ""
    diarized: bool = False

    # Transcript content
    transcript: str = ""  # formatted text (speaker labels if diarized)
    saved_to: str | None = None

    # Caption output (WebVTT / SRT)
    captions: str | None = None  # generated caption string
    caption_format: str | None = None  # "webvtt" | "srt" | None

    # Prerecorded extras
    full_result: dict[str, Any] | None = None  # raw Deepgram API response
    probe_info: dict[str, Any] | None = None
    duration_seconds: float = 0.0

    # The server's response body, as read, when it is printed as-is
    # (``-o json``). Not part of the model's fields or its dump.
    _body: bytes | None = PrivateAttr(default=None)


class ListenCodedError(CodedErrorResult):
    """A coded error from ``dg listen``, keys in the catalogue's order.

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
