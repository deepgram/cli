"""Audio debug command for deepctl."""

import json
import os
import shutil
import subprocess
import tempfile
from typing import Any
from urllib.parse import urlparse

import ffmpeg  # type: ignore[import-untyped]
import httpx
from deepctl_core import AuthManager, BaseCommand, Config, DeepgramClient
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .models import AudioDebugResult, AudioFormat, AudioInfo, AudioStream

console = Console()


class AudioCommand(BaseCommand):
    """Debug audio file issues."""

    name = "audio"
    help = "Debug audio file issues for Deepgram transcription"
    short_help = "Debug audio issues"

    # Audio debug doesn't require auth
    requires_auth = False
    requires_project = False
    ci_friendly = True

    def get_arguments(self) -> list[dict[str, Any]]:
        """Get command arguments and options."""
        return [
            {
                "names": ["--file", "-f"],
                "help": "Path to audio file or URL to debug",
                "type": str,
                "is_option": True,
                "required": True,
            },
            {
                "names": ["--verbose", "-v"],
                "help": "Show detailed diagnostic information",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--extra-verbose", "-vv"],
                "help": "Show raw ffprobe output",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--ffprobe-args"],
                "help": (
                    "Custom ffprobe arguments (e.g., '-show_streams -show_format')"
                ),
                "type": str,
                "is_option": True,
            },
        ]

    def check_ffmpeg_installed(self) -> bool:
        """Check if ffmpeg/ffprobe is installed."""
        return shutil.which("ffprobe") is not None

    def _is_url(self, path: str) -> bool:
        """Check if the path is a URL."""
        parsed = urlparse(path)
        return parsed.scheme in ("http", "https")

    def _download_url(self, url: str) -> str:
        """Download a URL to a temporary file and return the path."""
        console.print(f"[blue]Downloading:[/blue] {url}")

        parsed = urlparse(url)
        ext = os.path.splitext(parsed.path)[1] or ".audio"

        with tempfile.NamedTemporaryFile(
            suffix=ext, prefix="deepctl_audio_", delete=False
        ) as tmp:
            tmp_name = tmp.name
            try:
                with httpx.Client(
                    timeout=60.0, follow_redirects=True
                ) as client, client.stream("GET", url) as response:
                    response.raise_for_status()
                    total = 0
                    for chunk in response.iter_bytes(chunk_size=8192):
                        tmp.write(chunk)
                        total += len(chunk)
                size_mb = total / (1024 * 1024)
                console.print(
                    f"[green]Downloaded[/green] {size_mb:.2f} MB → {tmp_name}"
                )
                return tmp_name
            except Exception:
                os.unlink(tmp_name)
                raise

    def _suggest_encoding(self, audio_info: AudioInfo) -> None:
        """Suggest optimal Deepgram settings based on audio analysis."""
        if not audio_info.streams:
            return

        console.print("\n[bold]Encoding Suggestions:[/bold]")

        stream = audio_info.streams[0]
        suggestions = []

        # Sample rate suggestions
        if stream.sample_rate:
            rate = int(stream.sample_rate)
            if rate == 8000:
                suggestions.append(
                    "Telephony audio detected (8kHz) — "
                    "use `encoding=mulaw` or `encoding=linear16` "
                    "with `sample_rate=8000`"
                )
            elif rate <= 16000:
                suggestions.append(
                    f"Sample rate {rate}Hz — suitable for speech. "
                    f"Consider `encoding=linear16` with "
                    f"`sample_rate={rate}`"
                )
            elif rate >= 44100:
                suggestions.append(
                    f"High sample rate ({rate}Hz) — "
                    f"downsampling to 16kHz is recommended for "
                    f"faster processing without quality loss"
                )

        # Codec suggestions
        if stream.codec_name:
            codec = stream.codec_name.lower()
            if codec == "opus":
                suggestions.append(
                    "Opus codec detected — ideal for web streaming. "
                    "Use `encoding=opus`"
                )
            elif codec in ("pcm_s16le", "pcm_s16be"):
                suggestions.append(
                    "Linear PCM detected — use `encoding=linear16`"
                )
            elif codec in ("pcm_mulaw", "pcm_alaw"):
                enc = "mulaw" if "mulaw" in codec else "alaw"
                suggestions.append(
                    f"{codec} detected — use `encoding={enc}`"
                )
            elif codec == "mp3":
                suggestions.append(
                    "MP3 detected — Deepgram handles MP3 natively. "
                    "No encoding parameter needed for pre-recorded."
                )
            elif codec in ("flac", "vorbis", "aac"):
                suggestions.append(
                    f"{codec.upper()} detected — Deepgram handles "
                    f"this natively for pre-recorded. For streaming, "
                    f"consider converting to linear16 or opus."
                )

        # Channel suggestions
        if stream.channels and stream.channels > 1:
            suggestions.append(
                f"Multi-channel ({stream.channels}ch) — use "
                f"`channels={stream.channels}` and consider "
                f"`multichannel=true` for speaker separation"
            )
        elif stream.channels == 1:
            suggestions.append(
                "Mono audio — optimal for single-speaker transcription"
            )

        if suggestions:
            for suggestion in suggestions:
                console.print(f"  [dim]→[/dim] {suggestion}")
        else:
            console.print("  [green]No specific encoding changes needed[/green]")

    def run_ffprobe(
        self, file_path: str, custom_args: str | None = None
    ) -> dict[str, Any]:
        """Run ffprobe on the given file."""
        try:
            if custom_args:
                # Parse custom arguments and run ffprobe directly
                cmd = ["ffprobe", "-v", "quiet", "-print_format", "json"]
                cmd.extend(custom_args.split())
                cmd.append(file_path)

                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    raise Exception(f"ffprobe failed: {result.stderr}")

                return dict(json.loads(result.stdout))
            else:
                # Use python-ffmpeg for standard probe
                probe = ffmpeg.probe(file_path)
                return dict(probe)
        except Exception as e:
            raise Exception(f"Failed to probe file: {e!s}")

    def parse_audio_info(self, probe_data: dict[str, Any]) -> AudioInfo:
        """Parse ffprobe output into AudioInfo model."""
        audio_info = AudioInfo(raw_data=probe_data)

        # Parse format information
        if "format" in probe_data:
            format_data = probe_data["format"]
            audio_info.format = AudioFormat(
                filename=format_data.get("filename", ""),
                format_name=format_data.get("format_name"),
                format_long_name=format_data.get("format_long_name"),
                duration=(
                    float(format_data.get("duration", 0))
                    if format_data.get("duration")
                    else None
                ),
                size=(
                    int(format_data.get("size", 0)) if format_data.get("size") else None
                ),
                bit_rate=format_data.get("bit_rate"),
                nb_streams=(
                    int(format_data.get("nb_streams", 0))
                    if format_data.get("nb_streams")
                    else None
                ),
            )

        # Parse audio streams
        if "streams" in probe_data:
            for stream in probe_data["streams"]:
                if stream.get("codec_type") == "audio":
                    audio_stream = AudioStream(
                        codec_name=stream.get("codec_name"),
                        codec_long_name=stream.get("codec_long_name"),
                        sample_rate=stream.get("sample_rate"),
                        channels=stream.get("channels"),
                        channel_layout=stream.get("channel_layout"),
                        duration=(
                            float(stream.get("duration", 0))
                            if stream.get("duration")
                            else None
                        ),
                        bit_rate=stream.get("bit_rate"),
                        bits_per_sample=stream.get("bits_per_sample"),
                    )
                    audio_info.streams.append(audio_stream)

        return audio_info

    def display_basic_info(self, audio_info: AudioInfo) -> None:
        """Display basic audio information."""
        console.print("\n[green]✓[/green] Audio File Analysis Complete\n")

        # Format info
        if audio_info.format:
            console.print("[bold]File Information:[/bold]")
            format_name = (
                audio_info.format.format_long_name
                or audio_info.format.format_name
                or "Unknown"
            )
            console.print(f"  • Format: {format_name}")

            if audio_info.format.duration:
                minutes, seconds = divmod(audio_info.format.duration, 60)
                console.print(f"  • Duration: {int(minutes):02d}:{seconds:06.3f}")

            if audio_info.format.size:
                size_mb = audio_info.format.size / (1024 * 1024)
                console.print(f"  • Size: {size_mb:.2f} MB")

            if audio_info.format.bit_rate:
                bit_rate_kbps = int(audio_info.format.bit_rate) / 1000
                console.print(f"  • Bit Rate: {bit_rate_kbps:.0f} kbps")

        # Stream info
        if audio_info.streams:
            console.print("\n[bold]Audio Stream Information:[/bold]")
            for i, stream in enumerate(audio_info.streams):
                if len(audio_info.streams) > 1:
                    console.print(f"\n  Stream {i + 1}:")
                codec_name = stream.codec_long_name or stream.codec_name or "Unknown"
                console.print(f"  • Codec: {codec_name}")
                if stream.sample_rate:
                    console.print(f"  • Sample Rate: {stream.sample_rate} Hz")
                if stream.channels:
                    console.print(
                        f"  • Channels: {stream.channels} "
                        f"({stream.channel_layout or 'Unknown layout'})"
                    )
                if stream.bit_rate:
                    bit_rate_kbps = int(stream.bit_rate) / 1000
                    console.print(f"  • Stream Bit Rate: {bit_rate_kbps:.0f} kbps")

    def display_verbose_info(self, audio_info: AudioInfo) -> None:
        """Display detailed audio information in table format."""
        console.print("\n[green]✓[/green] Detailed Audio File Analysis\n")

        # Format table
        if audio_info.format:
            format_table = Table(title="Format Information", box=box.ROUNDED)
            format_table.add_column("Property", style="cyan")
            format_table.add_column("Value", style="white")

            format_data = audio_info.format.model_dump()
            for key, value in format_data.items():
                if value is not None:
                    display_key = key.replace("_", " ").title()
                    display_value = str(value)

                    if key == "duration" and value:
                        minutes, seconds = divmod(value, 60)
                        display_value = f"{int(minutes):02d}:{seconds:06.3f}"
                    elif key == "size" and value:
                        size_mb = value / (1024 * 1024)
                        display_value = f"{size_mb:.2f} MB ({value:,} bytes)"
                    elif key == "bit_rate" and value:
                        bit_rate_kbps = int(value) / 1000
                        display_value = f"{bit_rate_kbps:.0f} kbps"

                    format_table.add_row(display_key, display_value)

            console.print(format_table)

        # Streams table
        if audio_info.streams:
            for i, stream in enumerate(audio_info.streams):
                stream_table = Table(
                    title=(
                        f"Audio Stream {i + 1}"
                        if len(audio_info.streams) > 1
                        else "Audio Stream"
                    ),
                    box=box.ROUNDED,
                )
                stream_table.add_column("Property", style="cyan")
                stream_table.add_column("Value", style="white")

                stream_data = stream.model_dump()
                for key, value in stream_data.items():
                    if value is not None:
                        display_key = key.replace("_", " ").title()
                        display_value = str(value)

                        if key == "sample_rate" and value:
                            display_value = f"{value} Hz"
                        elif key == "bit_rate" and value:
                            bit_rate_kbps = int(value) / 1000
                            display_value = f"{bit_rate_kbps:.0f} kbps"
                        elif key == "duration" and value:
                            minutes, seconds = divmod(value, 60)
                            display_value = f"{int(minutes):02d}:{seconds:06.3f}"

                        stream_table.add_row(display_key, display_value)

                console.print(stream_table)
                if i < len(audio_info.streams) - 1:
                    console.print()

    def display_extra_verbose_info(self, audio_info: AudioInfo) -> None:
        """Display raw ffprobe output."""
        console.print("\n[green]✓[/green] Raw FFprobe Output\n")

        if audio_info.raw_data:
            import json

            from rich.syntax import Syntax

            json_str = json.dumps(audio_info.raw_data, indent=2)
            syntax = Syntax(json_str, "json", theme="monokai", line_numbers=True)
            console.print(syntax)

    def handle(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> Any:
        """Handle audio debug command execution."""
        audio_file = kwargs.get("file")
        verbose = kwargs.get("verbose", False)
        extra_verbose = kwargs.get("extra_verbose", False)
        ffprobe_args = kwargs.get("ffprobe_args")

        # Validate audio_file
        if not audio_file:
            return AudioDebugResult(
                status="error",
                message="No audio file specified",
                audio_file="",
                ffmpeg_installed=self.check_ffmpeg_installed(),
            )

        # Check if ffmpeg is installed
        if not self.check_ffmpeg_installed():
            console.print(
                Panel(
                    "[red]✗ FFmpeg not found![/red]\n\n"
                    "The audio debug command requires FFmpeg to be installed "
                    "on your system.\n\n"
                    "[bold]To install FFmpeg:[/bold]\n"
                    "• Visit: [link]https://ffmpeg.org/download.html[/link]\n"
                    "• Or use your package manager:\n"
                    "  - macOS: [dim]brew install ffmpeg[/dim]\n"
                    "  - Ubuntu/Debian: [dim]sudo apt install ffmpeg[/dim]\n"
                    "  - Windows: [dim]Download from ffmpeg.org[/dim]",
                    title="FFmpeg Required",
                    border_style="red",
                )
            )

            return AudioDebugResult(
                status="error",
                message="FFmpeg is not installed",
                audio_file=audio_file,
                ffmpeg_installed=False,
            )

        # Handle URL downloads
        downloaded_file = None
        file_to_analyze = str(audio_file)

        if self._is_url(audio_file):
            try:
                downloaded_file = self._download_url(audio_file)
                file_to_analyze = downloaded_file
            except Exception as e:
                console.print(
                    Panel(
                        f"[red]✗ Failed to download URL[/red]\n\n"
                        f"[dim]{e!s}[/dim]",
                        title="Download Failed",
                        border_style="red",
                    )
                )
                return AudioDebugResult(
                    status="error",
                    message="Failed to download audio URL",
                    audio_file=audio_file,
                    error_details=str(e),
                )

        # Process the audio file
        try:
            console.print(f"[blue]Analyzing audio file:[/blue] {file_to_analyze}")

            # Run ffprobe
            probe_data = self.run_ffprobe(file_to_analyze, ffprobe_args)

            # Parse the data
            audio_info = self.parse_audio_info(probe_data)

            # Display results based on verbosity
            if extra_verbose or ffprobe_args:
                self.display_extra_verbose_info(audio_info)
            elif verbose:
                self.display_verbose_info(audio_info)
            else:
                self.display_basic_info(audio_info)

            # Check for Deepgram compatibility
            console.print("\n[bold]Deepgram Compatibility Check:[/bold]")
            compatibility_issues = []

            if audio_info.streams:
                for stream in audio_info.streams:
                    # Check sample rate
                    if stream.sample_rate and int(stream.sample_rate) < 8000:
                        compatibility_issues.append(
                            f"⚠️  Low sample rate ({stream.sample_rate} Hz) - "
                            f"Deepgram works best with 8kHz or higher"
                        )

                    # Check channels
                    if stream.channels and stream.channels > 2:
                        compatibility_issues.append(
                            f"⚠️  Multi-channel audio ({stream.channels} "
                            f"channels) - Consider converting to mono or "
                            f"stereo"
                        )

            if compatibility_issues:
                for issue in compatibility_issues:
                    console.print(f"  {issue}")
            else:
                console.print(
                    "  [green]✓[/green] Audio appears to be compatible with Deepgram"
                )

            # Encoding suggestions
            self._suggest_encoding(audio_info)

            return AudioDebugResult(
                status="success",
                message="Audio file analyzed successfully",
                audio_file=audio_file,
                audio_info=audio_info,
            )

        except Exception as e:
            console.print(
                Panel(
                    f"[red]✗ Error analyzing audio file[/red]\n\n[dim]{e!s}[/dim]",
                    title="Analysis Failed",
                    border_style="red",
                )
            )

            return AudioDebugResult(
                status="error",
                message="Failed to analyze audio file",
                audio_file=audio_file,
                error_details=str(e),
            )
        finally:
            # Clean up downloaded file
            if downloaded_file:
                try:
                    os.unlink(downloaded_file)
                except OSError:
                    pass
