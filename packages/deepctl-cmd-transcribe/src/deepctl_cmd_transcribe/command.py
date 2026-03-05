"""Transcribe command for deepctl."""

from pathlib import Path
from typing import Any

from deepctl_core import (
    AuthManager,
    BaseCommand,
    BaseResult,
    Config,
    DeepgramClient,
)
from deepctl_shared_utils import (
    probe_file,
    require_ffprobe,
    validate_audio_file,
    validate_url,
)
from rich.console import Console
from rich.table import Table

from .models import TranscribeResult

console = Console()


class TranscribeCommand(BaseCommand):
    """Command for transcribing audio files and URLs."""

    name = "transcribe"
    help = "Transcribe audio files or URLs using Deepgram"
    short_help = "Transcribe audio"

    # Transcription requires authentication
    requires_auth = True
    requires_project = False  # Project ID is optional for transcription
    ci_friendly = True

    examples = [
        "dg transcribe recording.wav",
        "dg transcribe https://example.com/audio.mp3 --model nova-3",
        "dg transcribe call.wav --diarize --language en-US",
        "dg transcribe meeting.mp3 --output json --save-to transcript.json",
    ]
    agent_help = (
        "Transcribe audio files or URLs using Deepgram's speech-to-text API. "
        "Accepts local file paths or HTTP URLs. Supports model selection, "
        "language codes, speaker diarization, and multiple output formats. "
        "Requires authentication via 'dg login' or DEEPGRAM_API_KEY env var."
    )

    def get_arguments(self) -> list[dict[str, Any]]:
        """Get command arguments and options."""
        return [
            {
                "name": "source",
                "help": "Audio file path or URL to transcribe",
                "type": str,
                "required": True,
                "nargs": 1,
            },
            {
                "names": ["--model", "-m"],
                "help": "Deepgram model to use",
                "type": str,
                "default": "nova-2",
                "is_option": True,
            },
            {
                "names": ["--language", "-l"],
                "help": "Language code (e.g., en-US, es-ES)",
                "type": str,
                "default": "en-US",
                "is_option": True,
            },
            {
                "names": ["--smart-format"],
                "help": "Enable smart formatting",
                "is_flag": True,
                "default": True,
                "is_option": True,
            },
            {
                "names": ["--punctuate"],
                "help": "Enable punctuation",
                "is_flag": True,
                "default": True,
                "is_option": True,
            },
            {
                "names": ["--diarize"],
                "help": "Enable speaker diarization",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--summarize"],
                "help": "Enable summarization",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--detect-topics"],
                "help": "Enable topic detection",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--save-to", "-s"],
                "help": "Save transcription to file",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--no-validate"],
                "help": "Skip input validation",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--probe"],
                "help": "Analyze audio file before transcribing",
                "is_flag": True,
                "is_option": True,
            },
        ]

    def handle(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> BaseResult:
        """Handle transcribe command."""
        source = kwargs.get("source")
        model = kwargs.get("model", "nova-2")
        language = kwargs.get("language", "en-US")
        smart_format = kwargs.get("smart_format", True)
        punctuate = kwargs.get("punctuate", True)
        diarize = kwargs.get("diarize", False)
        summarize = kwargs.get("summarize", False)
        detect_topics = kwargs.get("detect_topics", False)
        save_to = kwargs.get("save_to")
        no_validate = kwargs.get("no_validate", False)
        probe = kwargs.get("probe", False)

        # Check if source is provided
        if not source:
            return BaseResult(status="error", message="No audio source provided")

        # Validate input if not skipped
        if not no_validate and not self._validate_source(source):
            return BaseResult(status="error", message="Invalid audio source")

        # Run ffprobe analysis if requested
        probe_info = None
        if probe:
            if not require_ffprobe(config):
                return BaseResult(
                    status="error",
                    message="ffprobe is required but not found",
                )

            if self._is_url(source):
                console.print(
                    "[yellow]Warning:[/yellow] Cannot probe remote URLs "
                    "— skipping audio analysis"
                )
            else:
                probe_result = probe_file(source, config)
                if probe_result:
                    probe_info = probe_result.model_dump(exclude_none=True)
                    self._print_probe_table(probe_result)
                else:
                    console.print(
                        "[yellow]Warning:[/yellow] Audio analysis "
                        "failed — continuing with transcription"
                    )

        # Build transcription options
        options = {
            "model": model,
            "language": language,
            "smart_format": str(smart_format).lower(),
            "punctuate": str(punctuate).lower(),
        }

        # Only add optional features if explicitly enabled
        if diarize:
            options["diarize"] = "true"
        if summarize:
            options["summarize"] = "true"
        if detect_topics:
            options["detect_topics"] = "true"

        try:
            console.print(f"[blue]Transcribing:[/blue] {source}")
            console.print(f"[dim]Model:[/dim] {model}")
            console.print(f"[dim]Language:[/dim] {language}")

            # Determine if source is file or URL
            if self._is_url(source):
                console.print("[dim]Processing URL...[/dim]")
                result_dict = client.transcribe_url(source, options)
            else:
                console.print("[dim]Processing file...[/dim]")
                result_dict = client.transcribe_file(source, options)

            # Extract transcript text
            transcript = self._extract_transcript(result_dict)

            # Save to file if requested
            if save_to:
                self._save_transcript(transcript, save_to)
                console.print(f"[green]✓[/green] Transcript saved to: {save_to}")

            # Return structured result
            return TranscribeResult(
                status="success",
                source=source,
                model=model,
                language=language,
                transcript=transcript,
                full_result=result_dict,
                saved_to=save_to,
                probe_info=probe_info,
            )

        except Exception as e:
            console.print(f"[red]Transcription failed:[/red] {e}")
            return BaseResult(status="error", message=str(e))

    def output_result(self, result: Any, config: Config) -> None:
        """Custom output for transcribe results."""
        import json
        from datetime import date, datetime

        def _default(obj: Any) -> str:
            if isinstance(obj, (datetime, date)):
                return obj.isoformat()
            return str(obj)

        from deepctl_core.output import get_output_format

        output_format = get_output_format()

        # --output json: return raw Deepgram API response
        if output_format == "json":
            if isinstance(result, TranscribeResult) and result.full_result:
                console.print_json(
                    json.dumps(result.full_result, indent=2, default=_default)
                )
            else:
                # Fallback for error results
                super().output_result(result, config)
            return

        # Default / table / other formats: metadata table + transcript text
        if isinstance(result, TranscribeResult) and result.status == "success":
            metadata = result.full_result.get("metadata", {})
            model_info = metadata.get("model_info", {})
            model_name = None
            model_version = None
            for info in model_info.values():
                model_name = info.get("name")
                model_version = info.get("version")
                break

            # Build metadata table
            table = Table(show_header=False, box=None, padding=(0, 2))
            table.add_column("Key", style="dim")
            table.add_column("Value")

            table.add_row("Request ID", metadata.get("request_id", "—"))
            table.add_row("Source", result.source)
            table.add_row("Model", model_name or result.model)
            if model_version:
                table.add_row("Version", model_version)
            duration = metadata.get("duration")
            if duration is not None:
                mins, secs = divmod(float(duration), 60)
                table.add_row("Duration", f"{int(mins)}:{secs:05.2f}")
            channels = metadata.get("channels")
            if channels is not None:
                table.add_row("Channels", str(int(channels)))
            if result.saved_to:
                table.add_row("Saved to", result.saved_to)

            console.print(table)
            console.print()

            # Print transcript text
            transcript = result.transcript
            if transcript and transcript != "No transcript found in response":
                console.print(transcript.strip())
            else:
                # Try paragraphs transcript for cleaner output
                paragraphs_text = self._extract_paragraphs_transcript(
                    result.full_result
                )
                if paragraphs_text:
                    console.print(paragraphs_text.strip())
                else:
                    console.print("[yellow]No transcript found in response[/yellow]")
        else:
            super().output_result(result, config)

    def _print_probe_table(self, probe_result: object) -> None:
        """Print ffprobe analysis results as a Rich table."""
        from deepctl_shared_utils.ffprobe_models import AudioProbeResult

        if not isinstance(probe_result, AudioProbeResult):
            return

        table = Table(
            title="Audio File Analysis",
            show_header=False,
            padding=(0, 2),
        )
        table.add_column("Property", style="cyan")
        table.add_column("Value")

        if probe_result.format:
            fmt = probe_result.format
            if fmt.format_name:
                table.add_row("Format", fmt.format_name)
            if fmt.duration is not None:
                mins, secs = divmod(fmt.duration, 60)
                table.add_row("Duration", f"{int(mins)}:{secs:05.2f}")
            if fmt.size is not None:
                size_mb = fmt.size / (1024 * 1024)
                table.add_row("Size", f"{size_mb:.2f} MB")
            if fmt.bit_rate:
                kbps = int(fmt.bit_rate) // 1000
                table.add_row("Bit Rate", f"{kbps} kbps")

        if probe_result.streams:
            stream = probe_result.streams[0]
            if stream.codec_name:
                label = stream.codec_name
                if stream.codec_long_name:
                    label = f"{stream.codec_name} ({stream.codec_long_name})"
                table.add_row("Codec", label)
            if stream.sample_rate:
                table.add_row("Sample Rate", f"{stream.sample_rate} Hz")
            if stream.channels is not None:
                layout = f" ({stream.channel_layout})" if stream.channel_layout else ""
                table.add_row("Channels", f"{stream.channels}{layout}")
            if stream.bits_per_sample:
                table.add_row("Bit Depth", f"{stream.bits_per_sample}-bit")

        console.print(table)
        console.print()

    def _validate_source(self, source: str) -> bool:
        """Validate audio source (file or URL)."""
        if self._is_url(source):
            return validate_url(source, check_accessibility=True)
        else:
            return validate_audio_file(source)

    def _is_url(self, source: str) -> bool:
        """Check if source is a URL."""
        return source.startswith(("http://", "https://"))

    def _extract_transcript(self, result: dict[str, Any]) -> str:
        """Extract transcript text from API result."""
        try:
            # Try paragraphs transcript first (best formatted)
            paragraphs = self._extract_paragraphs_transcript(result)
            if paragraphs:
                return paragraphs

            # Fall back to alternatives transcript
            if "results" in result and "channels" in result["results"]:
                channels = result["results"]["channels"]
                if channels and "alternatives" in channels[0]:
                    return str(channels[0]["alternatives"][0].get("transcript", ""))

            # Fallback to top-level transcript
            if "transcript" in result:
                return str(result["transcript"])

            return ""

        except Exception as e:
            console.print(
                f"[yellow]Warning:[/yellow] Could not extract transcript: {e}"
            )
            return str(result)

    def _extract_paragraphs_transcript(self, result: dict[str, Any]) -> str:
        """Extract formatted paragraphs transcript from API result."""
        try:
            channels = result.get("results", {}).get("channels", [])
            if channels:
                alternatives = channels[0].get("alternatives", [])
                if alternatives:
                    paragraphs = alternatives[0].get("paragraphs", {})
                    if paragraphs and "transcript" in paragraphs:
                        return str(paragraphs["transcript"])
        except Exception:
            pass
        return ""

    def _save_transcript(self, transcript: str, file_path: str) -> None:
        """Save transcript to file."""
        try:
            path = Path(file_path)

            # Create directory if it doesn't exist
            path.parent.mkdir(parents=True, exist_ok=True)

            # Write transcript
            with open(path, "w", encoding="utf-8") as f:
                f.write(transcript)

        except Exception as e:
            console.print(f"[red]Error saving transcript:[/red] {e}")
            raise
