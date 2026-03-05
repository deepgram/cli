"""Data models for debug probe command."""

from __future__ import annotations

from deepctl_core import BaseResult
from deepctl_shared_utils.ffprobe_models import AudioProbeResult  # noqa: TC002
from pydantic import BaseModel, Field


class ProbeSnapshot(BaseModel):
    """A single ffprobe snapshot taken during streaming."""

    timestamp: float = 0.0
    bytes_at_probe: int = 0
    result: AudioProbeResult | None = None


class ProbeConnectionStats(BaseModel):
    """Statistics for a single probe proxy connection."""

    connection_id: str
    stream_type: str = "unknown"
    path: str = ""
    bytes_sent: int = 0
    bytes_received: int = 0
    frames_sent: int = 0
    frames_received: int = 0
    text_frames_sent: int = 0
    text_frames_received: int = 0
    duration_seconds: float | None = None
    snapshots: list[ProbeSnapshot] = Field(default_factory=list)
    transcripts: list[str] = Field(default_factory=list)
    audio_buffer: bytes = Field(default=b"", exclude=True, repr=False)


class ProbeDebugResult(BaseResult):
    """Result from debug probe command execution."""

    port: int = 0
    upstream_host: str = ""
    connections: list[ProbeConnectionStats] = Field(default_factory=list)
    duration_seconds: float | None = None
