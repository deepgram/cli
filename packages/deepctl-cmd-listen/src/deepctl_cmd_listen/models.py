"""Models for listen command."""

from __future__ import annotations

from deepctl_core import BaseResult


class ListenResult(BaseResult):
    transcript: str = ""
    duration_seconds: float = 0.0
    source: str = ""  # "mic" or "stdin"
