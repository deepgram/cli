"""Pydantic models for ffprobe audio analysis results."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AudioStreamInfo(BaseModel):
    """Information about a single audio stream from ffprobe."""

    codec_name: str | None = None
    codec_long_name: str | None = None
    sample_rate: str | None = None
    channels: int | None = None
    channel_layout: str | None = None
    bit_rate: str | None = None
    bits_per_sample: int | None = None
    duration: float | None = None


class AudioFormatInfo(BaseModel):
    """Format-level information from ffprobe."""

    format_name: str | None = None
    format_long_name: str | None = None
    duration: float | None = None
    size: int | None = None
    bit_rate: str | None = None


class AudioProbeResult(BaseModel):
    """Complete ffprobe analysis result."""

    format: AudioFormatInfo | None = None
    streams: list[AudioStreamInfo] = Field(default_factory=list)
    raw_data: dict[str, Any] | None = None
