"""Speak (text-to-speech) command for deepctl."""

from __future__ import annotations

import io
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import wave
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

import click
from deepctl_core import (
    AuthManager,
    BaseCommand,
    BaseResult,
    Config,
    DeepgramClient,
    get_output_format,
)
from rich.console import Console
from rich.table import Table

from .models import SpeakResult, SpeakVoicesResult, VoiceInfo

if TYPE_CHECKING:
    from collections.abc import Iterator

console = Console(stderr=True)
# Tables and other stdout-bound rendering (only used when no audio goes to
# stdout, i.e. --list-voices).
stdout_console = Console()

# Keep in sync with the --model option default below; also reported by
# --list-voices so the table says which voice you get for free.
_DEFAULT_MODEL = "flux-alexis-en"

# Flux (Speak v2) streaming controls, per the /v2/speak API. `speed` is a
# 0.05-increment multiplier and `expressivity` is a small integer range; both
# are validated up front so we fail with a clear message instead of surfacing
# a raw SPEED_OUT_OF_RANGE / server error mid-stream.
_FLUX_SPEEDS = (0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15)
_FLUX_EXPRESSIVITY = (-2, -1, 0, 1, 2)

# Audio players probed for --play, in preference order. ffplay ships with
# ffmpeg and plays every format we can emit; the rest are OS-native fallbacks.
_AUDIO_PLAYERS = ("ffplay", "afplay", "paplay", "aplay")

# Players that read audio from stdin, and the argv that makes them do it.
# afplay (macOS) has no stdin mode, so it is handed a temp file instead.
_STDIN_PLAYER_ARGV = {
    "ffplay": ["ffplay", "-loglevel", "error", "-nodisp", "-autoexit", "-"],
    "paplay": ["paplay"],
    "aplay": ["aplay", "-q", "-"],
}

# Players with no stdin mode, which are handed a temp file by path instead.
_FILE_PLAYER_ARGV = {"afplay": ["afplay"]}

# paplay and aplay decode PCM/WAV only -- they cannot play a compressed
# container such as Aura's default mp3.
_PCM_ONLY_PLAYERS = ("paplay", "aplay")

# Compressed, self-describing encodings: a player can sniff these from the
# byte stream. The PCM encodings cannot be sniffed — Speak v1 wraps them in a
# WAV container unless `--container none` is passed, and Flux wraps its
# linear16 stream itself.
_COMPRESSED_ENCODINGS = ("mp3", "aac", "opus", "flac")
_PCM_ENCODINGS = ("linear16", "mulaw", "alaw")

# Encoding/container -> temp-file suffix, so the temp file handed to afplay is
# sniffed correctly by CoreAudio.
_SUFFIX_BY_ENCODING = {
    "mp3": ".mp3",
    "linear16": ".wav",
    "flac": ".flac",
    "opus": ".ogg",
    "aac": ".aac",
}


def _find_audio_player() -> str | None:
    """Return the first available audio player command, or ``None``."""
    for player in _AUDIO_PLAYERS:
        if shutil.which(player):
            return player
    return None


def _voice_type_badge(model_name: str) -> str:
    """Derive an aura/flux badge from a TTS model name prefix."""
    name = model_name.lower()
    if name.startswith("aura"):
        return "aura"
    if name.startswith("flux"):
        return "flux"
    return "tts"


def _play_suffix(*, is_flux: bool, encoding: str | None, container: str | None) -> str:
    """Best-effort file suffix for the audio handed to afplay's temp file."""
    if is_flux:
        # Flux streams raw audio; only linear16 gets a WAV wrapper (below).
        return ".wav" if (encoding or "linear16") == "linear16" else ".raw"
    if container == "wav":
        return ".wav"
    if container == "ogg":
        return ".ogg"
    eff_encoding = (encoding or "mp3").lower()
    if eff_encoding in _PCM_ENCODINGS:
        # Speak v1 defaults these to a WAV container; `--container none` is
        # the only way to get them bare.
        return ".raw" if container == "none" else ".wav"
    return _SUFFIX_BY_ENCODING.get(eff_encoding, ".mp3")


def _check_playable(
    player: str, *, is_flux: bool, encoding: str | None, container: str | None
) -> str | None:
    """Return why ``player`` cannot play this audio format, or ``None``.

    Checked before the API call so a format the chosen player cannot decode
    fails with an explanation instead of silence or a decoder error.
    """
    eff_encoding = (encoding or ("linear16" if is_flux else "mp3")).lower()
    compressed = eff_encoding in _COMPRESSED_ENCODINGS

    # Raw PCM with no container is undetectable: the player has no way to know
    # the sample rate, width, or encoding. Flux linear16 gets a WAV wrapper
    # (so it is fine); Flux mulaw/alaw and an explicit `--container none` do
    # not.
    raw = (is_flux and eff_encoding != "linear16") or (
        not is_flux and not compressed and container == "none"
    )
    if raw:
        return (
            f"--play cannot play raw {eff_encoding} audio: it has no container "
            "for the player to detect. Use the default linear16 audio, add "
            "--container wav (Aura), or save it with -o."
        )

    if compressed and player in _PCM_ONLY_PLAYERS:
        return (
            f"'{player}' can only play PCM/WAV audio, not {eff_encoding}. "
            "Install ffmpeg (ffplay) to play it, or save it with -o."
        )
    return None


@contextmanager
def _player_stdin(player: str) -> Iterator[IO[bytes]]:
    """Spawn a stdin-reading player and yield its stdin for streaming writes.

    On a clean exit the pipe is closed (which is how the player learns the
    audio ended) and the process is waited for, so the command does not return
    before playback finishes. If the body raises, the player is killed rather
    than left holding a half-written stream.
    """
    proc = subprocess.Popen(_STDIN_PLAYER_ARGV[player], stdin=subprocess.PIPE)
    stdin = proc.stdin
    assert stdin is not None  # stdin=PIPE above
    try:
        yield stdin
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    finally:
        # A player that quit early (ffplay's "q") leaves a broken pipe; that
        # is the user stopping playback, not a failure.
        with suppress(BrokenPipeError, OSError):
            stdin.close()
    returncode = proc.wait()
    if returncode:
        raise click.ClickException(
            f"Audio player '{player}' exited with status {returncode}."
        )


def _play_audio(player: str, audio: bytes, *, suffix: str) -> None:
    """Play a fully-synthesized blob through the detected system player.

    ffplay/paplay/aplay read it from stdin; afplay (macOS) has no stdin mode,
    so the bytes go to a temp file that is played by path. Playback failures
    raise so the command exits non-zero rather than reporting a success it
    did not deliver.
    """
    if player in _FILE_PLAYER_ARGV:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio)
            tmp_path = tmp.name
        try:
            returncode = subprocess.run(
                [*_FILE_PLAYER_ARGV[player], tmp_path], check=False
            ).returncode
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        if returncode:
            raise click.ClickException(
                f"Audio player '{player}' exited with status {returncode}."
            )
        return

    with _player_stdin(player) as sink, suppress(BrokenPipeError):
        sink.write(audio)


def _fail(message: str) -> BaseResult:
    """An error result whose message the user actually sees.

    In default output mode the framework prints nothing for a returned
    result — it only maps the status to a non-zero exit code — so the human
    message has to go to the stderr console here, the way every other status
    line in this command does.
    """
    console.print(f"[red]Error:[/red] {message}")
    return BaseResult(status="error", message=message)


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
        'dg speak "Hello from Deepgram" --play',
        "dg speak --list-voices",
        'dg speak "Hello world"',
        'dg speak "Hello world" -o hello.wav',
        'dg speak "Hello world" -o hello.wav --play',
        "dg speak --file message.txt -o output.wav",
        'dg speak "Hello" | ffplay -loglevel error -nodisp -autoexit -',
        # Flux TTS streaming controls: --speed (0.85-1.15) and beta
        # --expressivity (-2..2, default 0).
        'dg speak "A little slower, please" --speed 0.9 -o slow.wav',
        'dg speak "So exciting!" --expressivity 2 -o lively.wav  # beta; default: 0',
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
        "Flux TTS models also accept --speed (0.85–1.15) and beta "
        "--expressivity (-2..2; default 0 = nominal) streaming controls; these "
        "are rejected for other models. --play sends the audio to a local "
        "system player instead of (or as well as) a file, and --list-voices "
        "prints the available TTS voices without generating speech."
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
                    "(REST batch, e.g. aura-2-asteria-en). "
                    "See voices: dg speak --list-voices"
                ),
                "type": str,
                "is_option": True,
                "default": _DEFAULT_MODEL,
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
                    "Flux TTS (v2) only, beta. Expressive range: -2, -1, 0, 1, "
                    "or 2 (default 0 = nominal; negative flatter, positive more "
                    "animated). Fixed for the connection."
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
            {
                "names": ["--play"],
                "help": (
                    "Play the audio through your system player (ffplay, afplay, "
                    "paplay, or aplay). Can be combined with -o to save and play."
                ),
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--list-voices"],
                "help": "List available TTS voices and exit (no speech generated)",
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
    ) -> BaseResult | None:
        text = kwargs.get("text")
        output_path = kwargs.get("output")
        model = kwargs.get("model") or _DEFAULT_MODEL
        encoding = kwargs.get("encoding")
        container = kwargs.get("container")
        sample_rate = kwargs.get("sample_rate")
        speed = kwargs.get("speed")
        expressivity = kwargs.get("expressivity")
        file_path = kwargs.get("file")
        play = kwargs.get("play", False)
        list_voices = kwargs.get("list_voices", False)

        # --list-voices: discover voices and exit before any TTS generation.
        if list_voices:
            return self._list_voices(client)

        # Resolve text input: arg > --file > stdin
        if not text and file_path:
            path = Path(file_path)
            if not path.exists():
                return _fail(f"File not found: {file_path}")
            text = path.read_text().strip()
        elif not text and not sys.stdin.isatty():
            text = sys.stdin.read().strip()

        if not text:
            return _fail(
                "No text provided. Pass text as argument, use --file, "
                "or pipe via stdin."
            )

        # If --play was requested, resolve the player up front so we fail fast
        # with a clear message before spending an API call.
        player: str | None = None
        if play:
            player = _find_audio_player()
            if player is None:
                return _fail(
                    "No audio player found — install ffmpeg, or use -o to save a file."
                )

        # If stdout is a TTY and there's nowhere for the audio to go, require an
        # explicit destination. --play satisfies that requirement.
        stdout_is_tty = sys.stdout.isatty()
        if not output_path and not play and stdout_is_tty:
            return _fail(
                "No output specified. Use -o/--output to save to a file, "
                "--play to hear it, or pipe stdout."
            )

        # Only the documented flux-* namespace uses speak.v2. Aura and unknown
        # model names pass through to the REST API so the service can resolve them.
        is_flux = model.lower().startswith("flux-")

        # A player that cannot decode what we are about to request should say
        # so now, before the API call, rather than emit silence or a decoder
        # error after synthesis.
        if player is not None:
            unplayable = _check_playable(
                player, is_flux=is_flux, encoding=encoding, container=container
            )
            if unplayable is not None:
                return _fail(unplayable)

        # speed / expressivity are Flux (Speak v2) connect controls; reject them
        # for other models rather than silently dropping them. Raise (not return)
        # so the failure exits non-zero in every output mode.
        if not is_flux and (speed is not None or expressivity is not None):
            raise click.ClickException(
                "--speed and --expressivity are only supported for Flux TTS "
                "(Speak v2) models (flux-*). They are not available for "
                f"Speak v1 model '{model}'."
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

        if play and not output_path and not stdout_is_tty:
            # Playing sends the audio to the player, not to stdout, so a
            # redirect or pipe would otherwise collect nothing and give no
            # hint why.
            console.print(
                "[yellow]Note:[/yellow] --play sends the audio to your player, "
                "so stdout stays empty. Add -o to save a file too."
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

            # Flux linear16 into a stdin-reading player: stream it. The
            # player gets the streaming WAV header before the first frame and
            # then every frame as Flux emits it, so sound starts at
            # first-audio latency instead of after the whole utterance — the
            # same framing `dg speak | ffplay -` relies on.
            if (
                player is not None
                and player in _STDIN_PLAYER_ARGV
                and eff_encoding == "linear16"
            ):
                console.print(f"[blue]Playing audio ({player})...[/blue]")
                pcm = bytearray()
                saved_bytes = 0
                player_gone = False
                try:
                    with _player_stdin(player) as sink:
                        wrote_header = False
                        for chunk in stream:
                            if not chunk:
                                continue
                            if output_path:
                                pcm.extend(chunk)
                            if player_gone:
                                continue
                            try:
                                if not wrote_header:
                                    sink.write(
                                        _streaming_wav_header(
                                            sample_rate=int(eff_sample_rate)
                                        )
                                    )
                                    wrote_header = True
                                sink.write(chunk)
                                sink.flush()
                            except BrokenPipeError:
                                # The player exited first (ffplay's "q", for
                                # instance). That is the user stopping
                                # playback, not a failure — but keep draining
                                # the stream so a -o file is still complete.
                                player_gone = True

                        if output_path and pcm:
                            # Save before waiting on the player (that wait
                            # happens on leaving this block), so stopping
                            # playback still leaves the file behind. The file
                            # gets a real, exact-length header rather than the
                            # streaming placeholder the player was handed.
                            audio_bytes = _pcm_to_wav(
                                bytes(pcm), sample_rate=int(eff_sample_rate)
                            )
                            Path(output_path).write_bytes(audio_bytes)
                            saved_bytes = len(audio_bytes)
                except click.ClickException:
                    raise
                except Exception as e:
                    raise click.ClickException(f"Flux streaming failed: {e}")

                if prog.total == 0:
                    raise click.ClickException(
                        "Flux (Speak v2) streaming returned no audio."
                    )

                if output_path:
                    console.print(
                        f"[green]Audio saved to {output_path}[/green] "
                        f"({saved_bytes:,} bytes — {prog.timing()})"
                    )
                else:
                    console.print(
                        f"[green]✓ Played {prog.total:,} bytes[/green] "
                        f"({prog.timing()})"
                    )
                return SpeakResult(
                    status="success",
                    message=(
                        f"Audio saved to {output_path}"
                        if output_path
                        else f"Played {prog.total:,} bytes"
                    ),
                    output_path=output_path or "",
                    model=model,
                    bytes_written=saved_bytes or prog.total,
                    played=True,
                )

            if output_path or player:
                # Saving, or playing through afplay (which has no stdin mode),
                # needs the full utterance: a WAV file must declare its data
                # length in the header (known only once the stream ends), and
                # afplay is handed a complete file. So buffer, then wrap once
                # and reuse for both sinks.
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
                if output_path:
                    Path(output_path).write_bytes(audio_bytes)
                    console.print(
                        f"[green]Audio saved to {output_path}[/green] "
                        f"({total_bytes:,} bytes — {prog.timing()})"
                    )

                if player:
                    suffix = _play_suffix(
                        is_flux=True, encoding=eff_encoding, container=None
                    )
                    console.print(f"[blue]Playing audio ({player})...[/blue]")
                    _play_audio(player, audio_bytes, suffix=suffix)

                return SpeakResult(
                    status="success",
                    message=(
                        f"Audio saved to {output_path}"
                        if output_path
                        else f"Played {total_bytes:,} bytes"
                    ),
                    output_path=output_path or "",
                    model=model,
                    bytes_written=total_bytes,
                    played=bool(player),
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

        # REST path (Speak v1, including Aura and unknown-model pass-through).
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

            if player:
                # Playback needs the full utterance, so buffer it. When -o is
                # also given, save the same bytes to the file too.
                audio = bytearray()
                for chunk in audio_iter:
                    audio.extend(chunk)
                audio_bytes = bytes(audio)
                total_bytes = len(audio_bytes)

                if not audio_bytes:
                    return _fail("TTS returned no audio.")

                if output_path:
                    Path(output_path).write_bytes(audio_bytes)
                    console.print(
                        f"[green]Audio saved to {output_path}[/green] "
                        f"({total_bytes:,} bytes)"
                    )

                suffix = _play_suffix(
                    is_flux=False, encoding=encoding, container=container
                )
                console.print(f"[blue]Playing audio ({player})...[/blue]")
                _play_audio(player, audio_bytes, suffix=suffix)

                message = (
                    f"Audio saved to {output_path}"
                    if output_path
                    else f"Played {total_bytes:,} bytes"
                )
                return SpeakResult(
                    status="success",
                    message=message,
                    output_path=output_path or "",
                    model=model,
                    bytes_written=total_bytes,
                    played=True,
                )
            elif output_path:
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

        except click.ClickException:
            raise
        except Exception as e:
            # Raise (not return an error result) so the failure exits non-zero
            # in every output mode — a returned BaseResult is only printed and
            # still exits 0. Reachable now that unknown models (e.g. a bare
            # "flux" typo) route here instead of the raising v2 path.
            raise click.ClickException(f"Error generating speech: {e}")

    def _list_voices(self, client: DeepgramClient) -> BaseResult:
        """List available TTS voices as a table (mirrors `dg models`)."""
        try:
            result = client.list_models()
        except Exception as e:
            console.print(f"[red]Error listing voices:[/red] {e}")
            return BaseResult(status="error", message=str(e))

        voices: list[VoiceInfo] = []
        for m in result.get("tts", []):
            name = m.get("name", "")
            voices.append(
                VoiceInfo(
                    name=name,
                    voice_type=_voice_type_badge(name),
                    language=m.get("language", ""),
                )
            )

        if not voices:
            console.print("[yellow]No TTS voices found[/yellow]")
            return SpeakVoicesResult(status="info", message="No voices found")

        # Render the human table only in default mode: for json/yaml/csv the
        # framework serializes the returned result to stdout, so a table here
        # would corrupt what callers pipe into jq. (No audio goes to stdout on
        # this path, so the table is safe to print there, same as `dg models`.)
        if get_output_format() == "default":
            table = Table(
                title="Deepgram TTS Voices",
                show_header=True,
                header_style="bold blue",
            )
            table.add_column("Voice", style="green")
            table.add_column("Type", style="cyan")
            table.add_column("Language")
            for v in voices:
                table.add_row(v.name, v.voice_type, v.language)

            stdout_console.print(table)
            stdout_console.print(
                f"\n[dim]{len(voices)} voice(s) — generate with "
                f'dg speak "..." -m <voice>; '
                f"default: {_DEFAULT_MODEL}[/dim]"
            )

        return SpeakVoicesResult(
            status="success",
            voices=voices,
            count=len(voices),
        )
