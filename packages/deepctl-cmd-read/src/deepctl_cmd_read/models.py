"""Models for read command."""

from __future__ import annotations

from typing import Any

from deepctl_core import BaseResult
from pydantic import Field


class ReadResult(BaseResult):
    summary: str = ""
    sentiment: str = ""
    sentiment_score: float = 0.0
    topics: list[dict[str, Any]] = Field(default_factory=list)
    intents: list[dict[str, Any]] = Field(default_factory=list)
