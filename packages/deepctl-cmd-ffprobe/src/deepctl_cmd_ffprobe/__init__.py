"""FFprobe configuration command for deepctl."""

from .command import FfprobeCommand
from .models import FfprobeResult

__all__ = ["FfprobeCommand", "FfprobeResult"]
