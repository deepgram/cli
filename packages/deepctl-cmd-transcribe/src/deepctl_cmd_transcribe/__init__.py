"""Transcribe command package for deepctl."""

from .command import TranscribeCommand
from .models import TranscribeResult

__all__ = ["TranscribeCommand", "TranscribeResult"]
