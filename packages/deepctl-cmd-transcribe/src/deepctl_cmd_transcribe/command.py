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
from deepctl_shared_utils import validate_audio_file, validate_url
from rich.console import Console

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

        # Check if source is provided
        if not source:
            return BaseResult(
                status="error", message="No audio source provided"
            )

        # Validate input if not skipped
        if not no_validate and not self._validate_source(source):
            return BaseResult(status="error", message="Invalid audio source")

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
                result = client.transcribe_url(source, options)
            else:
                console.print("[dim]Processing file...[/dim]")
                result = client.transcribe_file(source, options)

            # Convert SDK response object to dict
            result_dict = result
            if hasattr(result, "to_dict"):
                result_dict = result.to_dict()
            elif hasattr(result, "dict"):
                result_dict = result.dict()
            elif hasattr(result, "__dict__"):
                result_dict = result.__dict__

            # Extract transcript text
            transcript = self._extract_transcript(result_dict)

            # Save to file if requested
            if save_to:
                self._save_transcript(transcript, save_to)
                console.print(
                    f"[green]✓[/green] Transcript saved to: {save_to}"
                )

            # Return structured result
            return TranscribeResult(
                status="success",
                source=source,
                model=model,
                language=language,
                transcript=transcript,
                full_result=result_dict,
                saved_to=save_to,
            )

        except Exception as e:
            console.print(f"[red]Transcription failed:[/red] {e}")
            return BaseResult(status="error", message=str(e))

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
            # Handle different response formats
            if "results" in result and "channels" in result["results"]:
                # Standard format
                channels = result["results"]["channels"]
                if channels and "alternatives" in channels[0]:
                    return str(channels[0]["alternatives"][0]["transcript"])

            # Fallback to looking for transcript in other locations
            if "transcript" in result:
                return str(result["transcript"])

            return "No transcript found in response"

        except Exception as e:
            console.print(
                f"[yellow]Warning:[/yellow] Could not extract transcript: {e}"
            )
            return str(result)

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
