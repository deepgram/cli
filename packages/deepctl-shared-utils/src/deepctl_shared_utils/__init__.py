"""Shared utilities for deepctl."""

from .ffprobe import (
    check_ffprobe,
    get_ffprobe_path,
    print_ffprobe_install_instructions,
    probe_buffer,
    probe_file,
    require_ffprobe,
)
from .ffprobe_models import AudioFormatInfo, AudioProbeResult, AudioStreamInfo
from .models import FileInfo
from .validation import validate_audio_file, validate_date_format, validate_url

__all__ = [
    "AudioFormatInfo",
    "AudioProbeResult",
    "AudioStreamInfo",
    "FileInfo",
    "check_ffprobe",
    "get_ffprobe_path",
    "print_ffprobe_install_instructions",
    "probe_buffer",
    "probe_file",
    "require_ffprobe",
    "validate_audio_file",
    "validate_date_format",
    "validate_url",
]

__version__ = "0.1.11"  # x-release-please-version
