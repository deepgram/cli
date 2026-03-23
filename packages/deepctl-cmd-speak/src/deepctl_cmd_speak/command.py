"""Speak (text-to-speech) command for deepctl."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from deepctl_core import (
    AuthManager,
    BaseCommand,
    BaseResult,
    Config,
    DeepgramClient,
)
from rich.console import Console

from .models import SpeakResult

console = Console(stderr=True)


class SpeakCommand(BaseCommand):
    """Command for generating speech from text using Deepgram TTS."""

    name = "speak"
    help = "Convert text to speech using Deepgram TTS"
    short_help = "Text-to-speech"

    requires_auth = True
    requires_project = False
    ci_friendly = True

    examples = [
        'dg speak "Hello world"',
        'dg speak "Hello world" -o hello.mp3',
        "dg speak --file message.txt -o output.mp3",
        'echo "Hello" | dg speak -o hello.mp3',
        'dg speak "Hello" | ffplay -nodisp -',
        'dg speak "Hello" -m aura-2-luna-en -o hello.wav --encoding linear16 --container wav',
    ]
    agent_help = (
        "Convert text to speech using Deepgram's TTS API. "
        "Text can be provided as an argument, from a file, or piped via stdin. "
        "Audio is written to a file (--output) or stdout for piping. "
        "Supports model selection and audio format options."
    )

    def get_arguments(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "text",
                "help": "Text to convert to speech",
                "required": False,
                "default": None,
            },
            {
                "names": ["--output", "-o"],
                "help": "Output file path (required if stdout is a terminal)",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--model", "-m"],
                "help": "TTS model (default: aura-2-asteria-en)",
                "type": str,
                "is_option": True,
                "default": "aura-2-asteria-en",
            },
            {
                "names": ["--encoding"],
                "help": "Audio encoding (mp3, linear16, flac, mulaw, alaw, opus, aac)",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--container"],
                "help": "Audio container (none, wav, ogg)",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--sample-rate"],
                "help": "Audio sample rate in Hz",
                "type": float,
                "is_option": True,
            },
            {
                "names": ["--file", "-f"],
                "help": "Read text from file",
                "type": str,
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
        text = kwargs.get("text")
        output_path = kwargs.get("output")
        model = kwargs.get("model") or "aura-2-asteria-en"
        encoding = kwargs.get("encoding")
        container = kwargs.get("container")
        sample_rate = kwargs.get("sample_rate")
        file_path = kwargs.get("file")

        # Resolve text input: arg > --file > stdin
        if not text and file_path:
            path = Path(file_path)
            if not path.exists():
                return BaseResult(
                    status="error", message=f"File not found: {file_path}"
                )
            text = path.read_text().strip()
        elif not text and not sys.stdin.isatty():
            text = sys.stdin.read().strip()

        if not text:
            return BaseResult(
                status="error",
                message="No text provided. Pass text as argument, use --file, or pipe via stdin.",
            )

        # If stdout is a TTY and no output file, require --output
        stdout_is_tty = sys.stdout.isatty()
        if not output_path and stdout_is_tty:
            return BaseResult(
                status="error",
                message="No output specified. Use -o/--output to save to file, or pipe stdout.",
            )

        try:
            console.print(f"[blue]Generating speech with {model}...[/blue]")

            audio_iter = client.speak_text(
                text=text,
                model=model,
                encoding=encoding,
                container=container,
                sample_rate=sample_rate,
            )

            total_bytes = 0

            if output_path:
                # Write to file
                out = Path(output_path)
                with open(out, "wb") as f:
                    for chunk in audio_iter:
                        f.write(chunk)
                        total_bytes += len(chunk)

                console.print(
                    f"[green]Audio saved to {output_path}[/green] ({total_bytes:,} bytes)"
                )

                return SpeakResult(
                    status="success",
                    message=f"Audio saved to {output_path}",
                    output_path=output_path,
                    model=model,
                    bytes_written=total_bytes,
                )
            else:
                # Write to stdout (piping)
                stdout_buffer = sys.stdout.buffer
                for chunk in audio_iter:
                    stdout_buffer.write(chunk)
                    total_bytes += len(chunk)
                stdout_buffer.flush()

                return SpeakResult(
                    status="success",
                    message=f"Wrote {total_bytes:,} bytes to stdout",
                    model=model,
                    bytes_written=total_bytes,
                )

        except Exception as e:
            console.print(f"[red]Error generating speech:[/red] {e}")
            return BaseResult(status="error", message=str(e))
