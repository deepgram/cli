"""Models for the unified listen/transcribe command."""

from __future__ import annotations

from typing import Any

from deepctl_core import BaseResult


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
