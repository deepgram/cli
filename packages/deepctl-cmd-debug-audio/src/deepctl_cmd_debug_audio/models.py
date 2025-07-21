"""Data models for audio debug command."""

from typing import Optional, Dict, Any, List
from pydantic import BaseModel
from deepctl_core import BaseResult


class AudioStream(BaseModel):
    """Audio stream information from ffprobe."""

    codec_name: Optional[str] = None
    codec_long_name: Optional[str] = None
    sample_rate: Optional[str] = None
    channels: Optional[int] = None
    channel_layout: Optional[str] = None
    duration: Optional[float] = None
    bit_rate: Optional[str] = None
    bits_per_sample: Optional[int] = None


class AudioFormat(BaseModel):
    """Audio format information from ffprobe."""

    filename: str
    format_name: Optional[str] = None
    format_long_name: Optional[str] = None
    duration: Optional[float] = None
    size: Optional[int] = None
    bit_rate: Optional[str] = None
    nb_streams: Optional[int] = None


class AudioInfo(BaseModel):
    """Detailed audio file information."""

    format: Optional[AudioFormat] = None
    streams: List[AudioStream] = []
    raw_data: Optional[Dict[str, Any]] = None  # For verbose output


class AudioDebugResult(BaseResult):
    """Result from audio debug command execution."""

    message: str
    audio_file: Optional[str] = None
    audio_info: Optional[AudioInfo] = None
    ffmpeg_installed: bool = True
    error_details: Optional[str] = None
