"""Models for ffprobe command."""

from deepctl_core import BaseResult


class FfprobeResult(BaseResult):
    stored_path: str | None = None
    detected_path: str | None = None
    version: str | None = None
    available: bool = False
