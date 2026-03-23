"""Models for speak command."""

from __future__ import annotations

from deepctl_core import BaseResult


class SpeakResult(BaseResult):
    output_path: str = ""
    model: str = ""
    bytes_written: int = 0
