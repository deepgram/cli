"""Data models for audio debug command."""

from typing import Any

from deepctl_core import BaseResult
from pydantic import BaseModel


class AudioStream(BaseModel):
    """Audio stream information from ffprobe."""

    codec_name: str | None = None
    codec_long_name: str | None = None
    sample_rate: str | None = None
    channels: int | None = None
    channel_layout: str | None = None
    duration: float | None = None
    bit_rate: str | None = None
    bits_per_sample: int | None = None


class AudioFormat(BaseModel):
    """Audio format information from ffprobe."""

    filename: str
    format_name: str | None = None
    format_long_name: str | None = None
    duration: float | None = None
    size: int | None = None
    bit_rate: str | None = None
    nb_streams: int | None = None


class AudioInfo(BaseModel):
    """Detailed audio file information."""

    format: AudioFormat | None = None
    streams: list[AudioStream] = []
    raw_data: dict[str, Any] | None = None  # For verbose output


class AudioDebugResult(BaseResult):
    """Result from audio debug command execution."""

    message: str
    audio_file: str | None = None
    audio_info: AudioInfo | None = None
    ffmpeg_installed: bool = True
    error_details: str | None = None
