"""Speak (text-to-speech) command for deepctl."""

from __future__ import annotations

import io
import struct
import sys
import time
import wave
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
from deepctl_core import (
    AuthManager,
    BaseCommand,
    BaseResult,
    Config,
    DeepgramClient,
)
from rich.console import Console

from .models import SpeakResult

if TYPE_CHECKING:
    from collections.abc import Iterator

console = Console(stderr=True)

# Flux (Speak v2) streaming controls, per the /v2/speak API. `speed` is a
# 0.05-increment multiplier and `expressivity` is a small integer range; both
# are validated up front so we fail with a clear message instead of surfacing
# a raw SPEED_OUT_OF_RANGE / server error mid-stream.
_FLUX_SPEEDS = (0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15)
_FLUX_EXPRESSIVITY = (-2, -1, 0, 1, 2)


def _fmt_bytes(n: int) -> str:
    """Human-readable byte count for progress display."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


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


def _streaming_wav_header(
    *, sample_rate: int, channels: int = 1, sample_width: int = 2
) -> bytes:
    """A 44-byte PCM WAV header with placeholder (streaming) length fields.

    stdout is not seekable, so we can't back-patch the RIFF/data sizes after
    the audio has streamed. A player reading a piped WAV reads until EOF, so we
    emit an oversized length up front and let the closing pipe stop playback.
    This lets ``dg speak ... | ffplay -`` start playing on the first chunk
    instead of waiting for the whole utterance.

    The length is 0x7FFFFFFF rather than 0xFFFFFFFF (which ffmpeg treats as a
    sentinel and warns "Ignoring maximum wav data size, file may be invalid")
    and rather than 0 (which macOS CoreAudio trusts literally, so a redirected
    ``dg speak ... > out.wav`` would look empty). An oversized-but-not-sentinel
    length is read-to-EOF by ffmpeg and estimated-from-bytes by CoreAudio, so
    both the pipe and the redirect-to-file cases work.
    """
    byte_rate = sample_rate * channels * sample_width
    block_align = channels * sample_width
    bits_per_sample = sample_width * 8
    placeholder = 0x7FFFFFFF  # oversized "unknown / streaming" length
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        placeholder,
        b"WAVE",
        b"fmt ",
        16,  # fmt chunk size
        1,  # PCM
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        placeholder,
    )


class _StreamProgress:
    """Scrolling stderr progress for a Flux audio stream.

    Prints a line as the first audio arrives (with its latency) and then a
    throttled ``received N`` line as bytes accumulate, so the stream is visibly
    arriving over time. Also tracks the byte total and time-to-first-audio for
    the caller's final summary. Wrap the SDK stream with ``track()``.

    Scrolling lines are shown only on an interactive stderr (a TTY); agent/CI
    runs get just the caller's one-line summary. Nothing is written to stdout,
    so a piped audio stream is never corrupted.
    """

    _INTERVAL = 0.2  # min seconds between scrolling "received N" lines

    def __init__(self, model: str) -> None:
        self.model = model
        self.total = 0
        self.first_audio: float | None = None
        self._start = time.monotonic()
        self._last_print = 0.0
        self._live = console.is_terminal

    def track(self, stream: Iterator[bytes]) -> Iterator[bytes]:
        """Yield each chunk unchanged while emitting scrolling progress."""
        for chunk in stream:
            self.total += len(chunk)
            if chunk and self.first_audio is None:
                self.first_audio = time.monotonic() - self._start
                self._emit(first=True)
            elif chunk:
                self._emit()
            yield chunk

    def _emit(self, first: bool = False) -> None:
        if not self._live:
            return
        now = time.monotonic()
        if not first and now - self._last_print < self._INTERVAL:
            return
        self._last_print = now
        if first:
            assert self.first_audio is not None  # set by the caller before first=True
            console.print(
                f"[dim]Streaming {self.model} — "
                f"first audio {self.first_audio * 1000:.0f} ms[/dim]"
            )
        else:
            console.print(f"[dim]  received {_fmt_bytes(self.total)}[/dim]")

    def timing(self) -> str:
        """A 'first audio X ms, Ys total' suffix for the final summary."""
        fa = (
            f"{self.first_audio * 1000:.0f} ms"
            if self.first_audio is not None
            else "n/a"
        )
        return f"first audio {fa}, {time.monotonic() - self._start:.1f}s total"


class SpeakCommand(BaseCommand):
    """Command for generating speech from text using Deepgram TTS."""

    name = "speak"
    help = "Convert text to speech using Deepgram TTS"
    short_help = "Text-to-speech"

    requires_auth = True
    requires_project = False
    ci_friendly = True

    examples = [
        # Flux TTS is the default (flux-alexis-en) — WebSocket streaming,
        # raw audio wrapped in WAV. Piped audio is a streaming WAV (unknown
        # length up front), so pass `-loglevel error` to silence ffmpeg's
        # cosmetic end-of-stream notice.
        'dg speak "Hello world"',
        'dg speak "Hello world" -o hello.wav',
        "dg speak --file message.txt -o output.wav",
        'dg speak "Hello" | ffplay -loglevel error -nodisp -autoexit -',
        # Flux TTS streaming controls: --speed (0.85-1.15) and --expressivity (-2..2).
        'dg speak "A little slower, please" --speed 0.9 -o slow.wav',
        'dg speak "So exciting!" --expressivity 2 -o lively.wav',
        # Aura (Speak v1, batch REST) — opt in with -m aura-*; needed for
        # containerized formats like mp3.
        'dg speak "Hello" -m aura-2-asteria-en -o hello.mp3',
        'dg speak "Hello" -m aura-2-luna-en -o hello.wav --encoding linear16 --container wav',
        'echo "Hello" | dg speak -o hello.mp3 -m aura-2-asteria-en',
        # Aura-2 Spanish voice (run `dg models` for the full list).
        'dg speak "Hola, mundo" -m aura-2-selena-es -o hola.mp3',
    ]
    agent_help = (
        "Convert text to speech using Deepgram's TTS API. "
        "Text can be provided as an argument, from a file, or piped via stdin. "
        "Audio is written to a file (--output) or stdout for piping. "
        "By default (flux-* models, e.g. flux-alexis-en) uses Flux TTS / Speak "
        "v2, streaming over WebSocket and emitting raw audio; linear16 output is "
        "wrapped in a WAV container so it is directly playable. Pass an aura-* "
        "model to use Speak v1 (batch REST), which supports containerized "
        "formats like mp3. Supports model selection and audio format options. "
        "Flux TTS models also accept --speed (0.85–1.15) and --expressivity "
        "(-2..2) streaming controls; these are rejected for Aura models."
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
                    "TTS model. flux-* = Flux TTS / Speak v2 (WebSocket "
                    "streaming; default flux-alexis-en); aura-* = Speak v1 "
                    "(REST batch, e.g. aura-2-asteria-en)."
                ),
                "type": str,
                "is_option": True,
                "default": "flux-alexis-en",
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
                "names": ["--speed"],
                "help": (
                    "Flux TTS (v2) only. Speech-rate multiplier: 0.85, 0.90, 0.95, "
                    "1.00, 1.05, 1.10, or 1.15 (1.00 = nominal)."
                ),
                "type": float,
                "is_option": True,
            },
            {
                "names": ["--expressivity"],
                "help": (
                    "Flux TTS (v2) only. Expressive range: -2, -1, 0, 1, or 2 "
                    "(0 = nominal; negative flatter, positive more animated)."
                ),
                "type": int,
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
    ) -> BaseResult | None:
        text = kwargs.get("text")
        output_path = kwargs.get("output")
        model = kwargs.get("model") or "flux-alexis-en"
        encoding = kwargs.get("encoding")
        container = kwargs.get("container")
        sample_rate = kwargs.get("sample_rate")
        speed = kwargs.get("speed")
        expressivity = kwargs.get("expressivity")
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

        # speed / expressivity are Flux (Speak v2) connect controls; reject them
        # for Aura rather than silently dropping them. Raise (not return) so the
        # failure exits non-zero in every output mode.
        if not is_flux and (speed is not None or expressivity is not None):
            raise click.ClickException(
                "--speed and --expressivity are only supported for Flux TTS "
                "(Speak v2) models (flux-*). They are not available for Aura "
                f"(Speak v1) model '{model}'."
            )
        if speed is not None and speed not in _FLUX_SPEEDS:
            allowed = ", ".join(f"{s:.2f}" for s in _FLUX_SPEEDS)
            raise click.ClickException(
                f"--speed must be one of: {allowed} (got {speed})."
            )
        if expressivity is not None and expressivity not in _FLUX_EXPRESSIVITY:
            allowed = ", ".join(str(e) for e in _FLUX_EXPRESSIVITY)
            raise click.ClickException(
                f"--expressivity must be one of: {allowed} (got {expressivity})."
            )

        if is_flux:
            # WebSocket streaming path. Streaming output is raw audio, so
            # default to linear16 @ 24kHz and wrap it in WAV for playback.
            # These paths raise (rather than returning an error result) so a
            # failure exits non-zero and is visible in every output format —
            # a returned error is only printed in structured output and still
            # exits 0.
            eff_encoding = encoding or "linear16"
            if eff_encoding not in ("linear16", "mulaw", "alaw"):
                raise click.ClickException(
                    f"Encoding '{eff_encoding}' is not supported for Flux "
                    "(Speak v2) streaming, which emits raw audio. Use "
                    "linear16 (default), mulaw, or alaw."
                )
            eff_sample_rate = sample_rate or 24000.0

            # A live spinner + byte counter + time-to-first-audio on stderr, so
            # the stream is visibly arriving rather than a single static line.
            prog = _StreamProgress(model)
            stream = prog.track(
                client.speak_text_stream(
                    text=text,
                    model=model,
                    encoding=eff_encoding,
                    sample_rate=eff_sample_rate,
                    speed=speed,
                    expressivity=expressivity,
                )
            )

            if output_path:
                # A WAV file must declare its data length in the header, which
                # we only know once the stream ends — so buffer, then wrap and
                # write. (Streaming to disk has no user-visible benefit here.)
                pcm = bytearray()
                try:
                    for chunk in stream:
                        pcm.extend(chunk)
                except Exception as e:
                    raise click.ClickException(f"Flux streaming failed: {e}")

                if not pcm:
                    # No audio means the stream errored upstream or returned
                    # nothing. Wrapping empty PCM yields a valid header-only
                    # WAV, so guard rather than report success on a silent file.
                    raise click.ClickException(
                        "Flux (Speak v2) streaming returned no audio."
                    )

                if eff_encoding == "linear16":
                    audio_bytes = _pcm_to_wav(
                        bytes(pcm), sample_rate=int(eff_sample_rate)
                    )
                else:
                    audio_bytes = bytes(pcm)

                total_bytes = len(audio_bytes)
                Path(output_path).write_bytes(audio_bytes)
                console.print(
                    f"[green]Audio saved to {output_path}[/green] "
                    f"({total_bytes:,} bytes — {prog.timing()})"
                )
                return SpeakResult(
                    status="success",
                    message=f"Audio saved to {output_path}",
                    output_path=output_path,
                    model=model,
                    bytes_written=total_bytes,
                )

            # Pipe path — write each chunk to stdout as it arrives so a
            # downstream player starts on the first frame (the point of
            # "streaming by default"). For linear16 we emit a streaming WAV
            # header before the first frame so `| ffplay -` plays with no
            # extra flags; raw mulaw/alaw stream as-is.
            out = sys.stdout.buffer
            wrote_header = False
            try:
                for chunk in stream:
                    if not chunk:
                        continue
                    if eff_encoding == "linear16" and not wrote_header:
                        out.write(
                            _streaming_wav_header(sample_rate=int(eff_sample_rate))
                        )
                        wrote_header = True
                    out.write(chunk)
                    out.flush()
            except Exception as e:
                raise click.ClickException(f"Flux streaming failed: {e}")

            if prog.total == 0:
                # Guard before anything is written: the header is only emitted
                # on the first frame, so a no-audio stream writes nothing.
                raise click.ClickException(
                    "Flux (Speak v2) streaming returned no audio."
                )

            console.print(
                f"[green]✓ Streamed {prog.total:,} bytes to stdout[/green] "
                f"({prog.timing()})"
            )
            # Return None so the framework skips output_result: in agentic/CI
            # (or --output json) mode it would serialize the result to the
            # stdout console — i.e. append JSON to the audio we just streamed,
            # corrupting a `> out.wav` redirect. The summary above already went
            # to the stderr console.
            return None

        # REST path (Aura v1) — unchanged.
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

                console.print(f"[green]✓ Wrote {total_bytes:,} bytes to stdout[/green]")
                # Return None so the framework skips output_result — otherwise
                # agentic/CI (or --output json) mode serializes JSON onto the
                # same stdout as the audio, corrupting a `> out` redirect. The
                # summary above went to the stderr console.
                return None

        except Exception as e:
            console.print(f"[red]Error generating speech:[/red] {e}")
            return BaseResult(status="error", message=str(e))
