"""Shared ffprobe wrapper for audio file analysis."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import tempfile
from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel

from .ffprobe_models import AudioFormatInfo, AudioProbeResult, AudioStreamInfo

if TYPE_CHECKING:
    from deepctl_core import Config

console = Console()


def get_ffprobe_path(config: Config | None = None) -> str | None:
    """Get the ffprobe binary path.

    Checks stored config path first, then falls back to auto-detection
    via shutil.which().
    """
    if config is not None:
        stored_path = config.get("tools.ffprobe_path")
        if (
            stored_path
            and os.path.isfile(stored_path)
            and os.access(stored_path, os.X_OK)
        ):
            return str(stored_path)

    return shutil.which("ffprobe")


def check_ffprobe(config: Config | None = None) -> bool:
    """Check if ffprobe is available."""
    return get_ffprobe_path(config) is not None


def print_ffprobe_install_instructions() -> None:
    """Print platform-specific ffprobe installation instructions."""
    system = platform.system()

    if system == "Darwin":
        instructions = (
            "[bold]Install FFmpeg (includes ffprobe):[/bold]\n\n"
            "  [cyan]brew install ffmpeg[/cyan]\n"
        )
    elif system == "Linux":
        instructions = (
            "[bold]Install FFmpeg (includes ffprobe):[/bold]\n\n"
            "  [cyan]sudo apt install ffmpeg[/cyan]      # Debian/Ubuntu\n"
            "  [cyan]sudo dnf install ffmpeg[/cyan]      # Fedora\n"
            "  [cyan]sudo pacman -S ffmpeg[/cyan]        # Arch\n"
        )
    elif system == "Windows":
        instructions = (
            "[bold]Install FFmpeg (includes ffprobe):[/bold]\n\n"
            "  [cyan]winget install FFmpeg[/cyan]\n"
            "  [cyan]choco install ffmpeg[/cyan]         # Chocolatey\n"
            "  [cyan]scoop install ffmpeg[/cyan]         # Scoop\n"
        )
    else:
        instructions = (
            "[bold]Install FFmpeg (includes ffprobe):[/bold]\n\n"
            "  Download from [cyan]https://ffmpeg.org/download.html[/cyan]\n"
        )

    instructions += (
        "\nAfter installing, you can set a custom path with:\n"
        "  [dim]dg ffprobe --path /path/to/ffprobe[/dim]"
    )

    console.print(
        Panel(
            instructions,
            title="[red]ffprobe not found[/red]",
            border_style="red",
        )
    )


def require_ffprobe(config: Config | None = None) -> bool:
    """Check for ffprobe and print install instructions if missing.

    Returns True if ffprobe is available, False otherwise.
    """
    if check_ffprobe(config):
        return True

    print_ffprobe_install_instructions()
    return False


def _parse_probe_data(data: dict[str, object]) -> AudioProbeResult:
    """Parse raw ffprobe JSON output into an AudioProbeResult."""
    result = AudioProbeResult(raw_data=data)

    fmt = data.get("format")
    if isinstance(fmt, dict):
        duration_str = fmt.get("duration")
        size_str = fmt.get("size")
        result.format = AudioFormatInfo(
            format_name=fmt.get("format_name"),
            format_long_name=fmt.get("format_long_name"),
            duration=float(duration_str) if duration_str else None,
            size=int(size_str) if size_str else None,
            bit_rate=fmt.get("bit_rate"),
        )

    streams_data = data.get("streams")
    if isinstance(streams_data, list):
        for stream in streams_data:
            if not isinstance(stream, dict):
                continue
            if stream.get("codec_type") != "audio":
                continue
            duration_str = stream.get("duration")
            result.streams.append(
                AudioStreamInfo(
                    codec_name=stream.get("codec_name"),
                    codec_long_name=stream.get("codec_long_name"),
                    sample_rate=stream.get("sample_rate"),
                    channels=stream.get("channels"),
                    channel_layout=stream.get("channel_layout"),
                    bit_rate=stream.get("bit_rate"),
                    bits_per_sample=stream.get("bits_per_sample"),
                    duration=float(duration_str) if duration_str else None,
                )
            )

    return result


def probe_file(file_path: str, config: Config | None = None) -> AudioProbeResult | None:
    """Probe an audio file using ffprobe.

    Returns AudioProbeResult on success, None on failure.
    """
    ffprobe_bin = get_ffprobe_path(config)
    if not ffprobe_bin:
        return None

    try:
        cmd = [
            ffprobe_bin,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            file_path,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)
        return _parse_probe_data(data)

    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None


def probe_buffer(
    audio_bytes: bytes,
    label: str = "audio",
    config: Config | None = None,
) -> AudioProbeResult | None:
    """Probe audio bytes by writing to a temp file and running ffprobe.

    Returns AudioProbeResult on success, None on failure.
    """
    if not audio_bytes:
        return None

    ffprobe_bin = get_ffprobe_path(config)
    if not ffprobe_bin:
        return None

    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".raw", delete=False, prefix=f"deepctl_{label}_"
        ) as tmp_file:
            tmp_name = tmp_file.name
            tmp_file.write(audio_bytes)

        cmd = [
            ffprobe_bin,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            tmp_name,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)
        return _parse_probe_data(data)

    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None
    finally:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
