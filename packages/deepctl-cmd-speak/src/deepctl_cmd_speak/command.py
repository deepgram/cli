"""Speak (text-to-speech) command for deepctl."""

from __future__ import annotations

import io
import sys
import wave
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


def _pcm_to_wav(
    pcm: bytes, *, sample_rate: int, channels: int = 1, sample_width: int = 2
) -> bytes:
    """Wrap raw signed 16-bit little-endian PCM in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(sample_width)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buf.getvalue()


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
        # Flux TTS — WebSocket streaming (flux-* models), streaming by default:
        'dg speak "Hello from Flux" -m flux-alexis-en -o hello.wav',
        'dg speak "Hello from Flux" -m flux-alexis-en | ffplay -nodisp -',
    ]
    agent_help = (
        "Convert text to speech using Deepgram's TTS API. "
        "Text can be provided as an argument, from a file, or piped via stdin. "
        "Audio is written to a file (--output) or stdout for piping. "
        "aura-* models use Speak v1 (batch REST). flux-* (Flux TTS) models use "
        "Speak v2, streaming over WebSocket by default and emitting raw audio; "
        "linear16 output is wrapped in a WAV container so it is directly playable. "
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
                "help": (
                    "TTS model. aura-* = Speak v1 (REST batch; default "
                    "aura-2-asteria-en); flux-* = Flux TTS / Speak v2 (WebSocket "
                    "streaming, e.g. flux-alexis-en)."
                ),
                "type": str,
                "is_option": True,
                "default": "aura-2-asteria-en",
            },
            {
                "names": ["--encoding"],
                "help": (
                    "Audio encoding. Aura (v1): mp3, linear16, flac, mulaw, alaw, "
                    "opus, aac. Flux (v2) streaming is raw only: linear16 "
                    "(default), mulaw, alaw."
                ),
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--container"],
                "help": (
                    "Audio container (none, wav, ogg). Aura (v1) only; ignored for "
                    "flux-* (linear16 is auto-wrapped in WAV)."
                ),
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

        # Flux models stream over the WebSocket (speak.v2); Aura uses REST (speak.v1).
        is_flux = model.lower().startswith("flux")

        try:
            if is_flux:
                # WebSocket streaming path. Streaming output is raw audio, so
                # default to linear16 @ 24kHz and wrap it in WAV for playback.
                eff_encoding = encoding or "linear16"
                if eff_encoding not in ("linear16", "mulaw", "alaw"):
                    return BaseResult(
                        status="error",
                        message=(
                            f"Encoding '{eff_encoding}' is not supported for Flux "
                            "(Speak v2) streaming, which emits raw audio. Use "
                            "linear16 (default), mulaw, or alaw."
                        ),
                    )
                eff_sample_rate = sample_rate or 24000.0
                console.print(
                    f"[blue]Generating speech with {model} "
                    f"via WebSocket streaming...[/blue]"
                )

                pcm = bytearray()
                for chunk in client.speak_text_stream(
                    text=text,
                    model=model,
                    encoding=eff_encoding,
                    sample_rate=eff_sample_rate,
                ):
                    pcm.extend(chunk)

                if eff_encoding == "linear16":
                    audio_bytes = _pcm_to_wav(
                        bytes(pcm), sample_rate=int(eff_sample_rate)
                    )
                else:
                    audio_bytes = bytes(pcm)

                total_bytes = len(audio_bytes)
                if output_path:
                    Path(output_path).write_bytes(audio_bytes)
                    console.print(
                        f"[green]Audio saved to {output_path}[/green] "
                        f"({total_bytes:,} bytes)"
                    )
                    return SpeakResult(
                        status="success",
                        message=f"Audio saved to {output_path}",
                        output_path=output_path,
                        model=model,
                        bytes_written=total_bytes,
                    )
                sys.stdout.buffer.write(audio_bytes)
                sys.stdout.buffer.flush()
                return SpeakResult(
                    status="success",
                    message=f"Wrote {total_bytes:,} bytes to stdout",
                    model=model,
                    bytes_written=total_bytes,
                )

            # REST path (Aura v1) — unchanged.
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
