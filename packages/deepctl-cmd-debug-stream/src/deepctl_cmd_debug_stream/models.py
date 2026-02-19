"""Data models for stream debug command."""

from deepctl_core import BaseResult
from pydantic import BaseModel, Field


class AudioFormatReport(BaseModel):
    """Audio format information from ffprobe analysis."""

    codec: str | None = None
    sample_rate: str | None = None
    channels: int | None = None
    bit_rate: str | None = None
    duration: float | None = None
    format_name: str | None = None


class ConnectionStats(BaseModel):
    """Statistics for a single proxy connection."""

    connection_id: str
    stream_type: str = "unknown"  # "stt", "tts", "agent", "unknown"
    path: str = ""
    bytes_sent: int = 0
    bytes_received: int = 0
    frames_sent: int = 0
    frames_received: int = 0
    text_frames_sent: int = 0
    text_frames_received: int = 0
    duration_seconds: float | None = None
    sent_audio_format: AudioFormatReport | None = None
    received_audio_format: AudioFormatReport | None = None
    sent_audio_buffer: bytes = Field(default=b"", exclude=True, repr=False)
    received_audio_buffer: bytes = Field(default=b"", exclude=True, repr=False)


class StreamDebugResult(BaseResult):
    """Result from stream debug command execution."""

    port: int = 0
    upstream_host: str = ""
    connections: list[ConnectionStats] = Field(default_factory=list)
    duration_seconds: float | None = None
