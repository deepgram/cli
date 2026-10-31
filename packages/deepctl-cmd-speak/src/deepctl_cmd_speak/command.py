"""Speak (text-to-speech) command for deepctl.

Signal handling (SIGINT/SIGTERM/SIGHUP during a request) is written for
the dg process; running this command in-process inside another program
isn't supported for signal semantics.
"""

from __future__ import annotations

import io
import os
import secrets
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any
from urllib.parse import urlsplit

import click
from deepctl_core import (
    AuthManager,
    BaseCommand,
    BaseResult,
    Config,
    DeepgramClient,
    get_output_format,
    get_status_console,
)
from deepctl_core.output import (
    _agentic,
    format_source,
    is_ai_agent,
    print_error,
    print_hint,
    print_warning,
    stderr_console,
)
from deepctl_core.signals import coalescing as _coalescing
from deepctl_core.signals import defer_in_importlib as _defer_in_importlib
from deepctl_core.signals import die_by_signal as _die_by_signal
from deepctl_core.signals import send_to_main as _send_to_main
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from . import messages as msg
from . import sinks
from .models import SpeakCodedError, SpeakResult, SpeakVoicesResult, VoiceInfo

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

console = get_status_console()
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

# Compressed, self-describing encodings: a player can sniff these from the
# byte stream. The PCM encodings cannot be sniffed — Speak v1 wraps them in a
# WAV container unless `--container none` is passed, and Flux wraps its
# linear16 stream itself.
_COMPRESSED_ENCODINGS = ("mp3", "aac", "opus", "flac")
_PCM_ENCODINGS = ("linear16", "mulaw", "alaw")

# Compressed encodings each fallback player cannot decode, checked before the
# API call. ffplay (ffmpeg) and afplay (CoreAudio) play everything deepctl
# emits, so they have no entry. paplay decodes through libsndfile, which reads
# WAV, FLAC, and Ogg (Vorbis and Opus) but not mp3 or aac on the distro builds
# in common use. aplay plays PCM/WAV only.
_UNPLAYABLE_BY_PLAYER: dict[str, tuple[str, ...]] = {
    "paplay": ("mp3", "aac"),
    "aplay": _COMPRESSED_ENCODINGS,
}

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
            f"--play can't play raw {eff_encoding} audio because the player "
            "can't tell its format. Use linear16 (the default), add "
            "--container wav (Aura only), or save it with --out-file FILE."
        )

    unplayable = _UNPLAYABLE_BY_PLAYER.get(player, ())
    if compressed and eff_encoding in unplayable:
        playable = ", ".join(
            "linear16 (WAV)" if e == "linear16" else e
            for e in ("linear16", *_COMPRESSED_ENCODINGS)
            if e not in unplayable
        )
        return (
            f"'{player}' can't decode {eff_encoding}; it plays {playable}. "
            "Pick one of those with --encoding, install ffmpeg (for ffplay) to "
            f"play {eff_encoding}, or save it with --out-file FILE."
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


def _prog() -> str:
    """The program name to show in messages and ``next_step``.

    The bare name when it resolves on PATH to the running executable;
    otherwise ``dg``.
    """
    try:
        argv0 = sys.argv[0]
        name = os.path.basename(argv0)
        if name in ("dg", "deepgram", "deepctl"):
            found = shutil.which(name)
            if found and os.path.realpath(found) == os.path.realpath(argv0):
                return name
    except Exception:
        pass
    return "dg"


def _silence_stdout() -> None:
    """Point fd 1 at the null device, so buffered output and later writes,
    including the flush at exit, succeed quietly. Used once the reader of
    stdout went away (EPIPE) or stdout can't be written (ENOSPC, EIO)."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)
        os.close(devnull)
    except OSError:
        pass


def _os_error_text(exc: OSError) -> str:
    """The OS error text for a message: printable, one line, bounded."""
    text = exc.strerror or str(exc) or exc.__class__.__name__
    return "".join(ch if ch.isprintable() else " " for ch in text).strip()[:200]


#: Longest wait for an audio stream to close: a WebSocket closing handshake
#: the server doesn't answer (Ctrl-C mid-stream) must not hold up the exit.
_CLOSE_TIMEOUT_S = 2.0


def _close(*streams: object, timeout: float = _CLOSE_TIMEOUT_S) -> None:
    """Close audio iterators (and the responses behind them), quietly and in
    order, and wait at most ``timeout`` seconds for all of them together."""
    closes = [c for c in (getattr(s, "close", None) for s in streams) if c]
    if not closes:
        return

    def run() -> None:
        for close in closes:
            with suppress(BaseException):
                close()

    closer = threading.Thread(target=run, name="deepctl-speak-close", daemon=True)
    closer.start()
    closer.join(timeout)


def _coded(
    text: str,
    *,
    code: str,
    hint_human: str,
    hint_agent: str,
    next_step: str | None = None,
    details: dict[str, Any] | None = None,
) -> SpeakCodedError:
    """Report a coded failure on stderr (two lines) and return its envelope."""
    hint = hint_agent if (_agentic or is_ai_agent()) else hint_human
    print_error(escape(text))
    print_hint(escape(hint))
    return SpeakCodedError(
        error=text, code=code, hint=hint, next_step=next_step, details=details
    )


def _output_unwritable(exc: OSError, *, path: str) -> SpeakCodedError:
    """``output_unwritable`` for a failed write to ``path`` (``-`` = stdout)."""
    reason = _os_error_text(exc)
    return _coded(
        msg.E_OUTPUT_UNWRITABLE.format(
            output="stdout" if path == "-" else path, reason=reason
        ),
        code=msg.CODE_OUTPUT_UNWRITABLE,
        hint_human=msg.H_OUTPUT_UNWRITABLE,
        hint_agent=msg.A_OUTPUT_UNWRITABLE.format(prog=_prog(), reason=reason),
        details={"path": path, "reason": reason},
    )


def _stdout_unwritable(exc: OSError) -> SpeakCodedError:
    """stdout failed (not EPIPE): silence fd 1 first, so the envelope goes
    to stderr only and the flush at exit can't fail again."""
    _silence_stdout()
    return _output_unwritable(exc, path="-")


class _NoAudio(Exception):
    """The stream ended without a single audio byte."""


# Raised by an AudioFile; they pass through the "request failed" handlers.
_SINK_ERRORS = (sinks.Exists, sinks.WriteFailed, sinks.ReaderGone)

# Values of `speak -o` that are output formats, not file names.
_FORMAT_WORDS = ("json", "yaml", "text", "table", "csv")

# Root -o values: the formats a corrected command line can actually use.
_ROOT_FORMATS = ("json", "yaml", "table", "csv")

# Root options that take a value, used when no Click context is available.
_ROOT_VALUE_OPTS = frozenset(
    {"--config", "-c", "--profile", "-p", "--base-url", "--api-key", "--output", "-o"}
)

_deprecation_shown = False


def _argv() -> list[str]:
    """The user's arguments after the program name (a seam for tests)."""
    return list(sys.argv[1:])


def _root_value_opts() -> frozenset[str]:
    """Root options that take a value, from the running root command."""
    ctx = click.get_current_context(silent=True)
    if ctx is None:
        return _ROOT_VALUE_OPTS
    opts: set[str] = set()
    for param in ctx.find_root().command.params:
        if isinstance(param, click.Option) and (param.is_flag or param.count):
            continue
        opts.update(param.opts)
        opts.update(param.secondary_opts)
    return frozenset(opts)


def _split_argv(argv: list[str], command: str) -> tuple[list[str], list[str]] | None:
    """Split argv into (root options, command arguments), or ``None``."""
    value_opts = _root_value_opts()
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == command:
            return argv[:i], argv[i + 1 :]
        if token == "--" or not token.startswith("-"):
            return None
        if token in value_opts:
            i += 2
            continue
        if not token.startswith("--"):
            # A cluster such as -qo: a value letter takes the rest of the
            # token, or the next one when it is last.
            for j, letter in enumerate(token[1:]):
                if f"-{letter}" in value_opts:
                    if j == len(token) - 2:
                        i += 1
                    break
        i += 1
    return None


def _drop_output(args: list[str]) -> list[str]:
    """``args`` without -o/--output and its value."""
    kept: list[str] = []
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--":
            kept.extend(args[i:])
            break
        if token in ("-o", "--output"):
            i += 2
            continue
        if token.startswith("--output=") or (
            token.startswith("-o") and not token.startswith("--")
        ):
            i += 1
            continue
        kept.append(token)
        i += 1
    return kept


def _secret_bearing(tokens: list[str]) -> bool:
    """True when argv holds a key or a URL with userinfo or a query."""
    for token in tokens:
        if token in ("--api-key", "-k") or token.startswith("--api-key="):
            return True
        if token.startswith("-k") and not token.startswith("--"):
            return True
        if "://" in token:
            try:
                parts = urlsplit(token)
            except ValueError:
                return True
            if parts.username or parts.password or parts.query:
                return True
    return False


def _next_step(tokens: list[str]) -> str | None:
    """A command line runnable verbatim, or ``None``."""
    if _secret_bearing(tokens):
        return None
    for token in tokens:
        if token.startswith("=") or (
            token.startswith("<") and token.endswith(">") and token[1:-1].isupper()
        ):
            return None
    line = shlex.join(tokens)
    if os.name == "nt" and line != " ".join(tokens):
        return None
    if shlex.join(shlex.split(line)) != line or tokens[:1] == ["sudo"]:
        return None
    return line


def _proposed_out_file(
    *, is_flux: bool, encoding: str | None, container: str | None
) -> str:
    """A new file name in the cwd for next_step: never an existing file."""
    ext = _play_suffix(is_flux=is_flux, encoding=encoding, container=container)
    while True:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        name = f"speech-{stamp}-{secrets.token_hex(2)}{ext}"
        if not os.path.lexists(name):
            return name


def _deprecate_output() -> None:
    """The one DEPRECATED line for speak -o/--output FILE, even under -q."""
    global _deprecation_shown
    if _deprecation_shown:
        return
    _deprecation_shown = True
    text = msg.D_SPEAK_OUTPUT.format(prog=_prog())
    if _agentic:
        stderr_console.print(f"DEPRECATED: {escape(text)}", soft_wrap=True)
    else:
        stderr_console.print(
            f"[yellow]⚠[/yellow] Deprecated: {escape(text)}", soft_wrap=True
        )


class _Terminated(BaseException):
    """SIGTERM or SIGHUP arrived mid-stream: unwind so a partial file
    or temp file is removed, then end by that signal."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


#: How long a signalled run may take to unwind before the signal is
#: delivered again and ends it. This timer is how a Flux run ends after a
#: signal when the server doesn't answer the closing handshake: the SDK
#: waits up to 10 s for it in the main thread. A close still waiting in
#: ``finally`` is cut short the same way. SIGTERM and SIGHUP then end at
#: once; Ctrl-C runs the usual cancel path, so it ends a little later.
#: Both stay well inside the 2.6 s a signalled run may take to exit.
_SIGNAL_GRACE_S = 1.5


def _end_by_signal(signum: int) -> None:
    """End the process by ``signum`` (POSIX); on Windows, exit 128+N."""
    if os.name == "nt":
        raise SystemExit(128 + signum)
    _die_by_signal(signum)


@contextmanager
def _terminate_cleanly(cleanup: Callable[[], None]) -> Iterator[None]:
    """For the duration of a request and stream:

    - SIGTERM and SIGHUP raise :class:`_Terminated` in the main thread
      instead of killing the process before ``cleanup`` can run;
    - SIGINT raises ``KeyboardInterrupt`` as before.

    Unwinding can stall in a WebSocket closing handshake the server doesn't
    answer. So the first signal also arms a timer: if the run is still here
    after ``_SIGNAL_GRACE_S``, the signal is delivered again, and a second
    signal runs ``cleanup`` and ends the run: SIGINT by ``KeyboardInterrupt``
    again (the usual cancel message, exit 2, exit handlers run; a hard exit
    follows only if that unwind stalls too), SIGTERM and SIGHUP by the signal
    itself. A double Ctrl-C after the guard has exited is the default
    handler's: exit by SIGINT (130), as before.

    Signals that arrive during a file critical section are deferred (see
    ``sinks.critical_section``); the most severe is delivered after it.

    A signal is left exactly as it was when its handler isn't ours to
    replace:

    - ``SIG_IGN`` (nohup ignores SIGHUP, a background job in a script starts
      with SIGINT ignored, ``trap '' TERM``): the run keeps going, as 0.4.0
      did, and the file is completed;
    - a handler not installed from Python (``signal.getsignal`` returns
      ``None``): ``signal.signal`` can't put it back afterwards, so it is
      not replaced at all.

    On exit the guard is closed first (its handler then only records a
    signal, and the timer sends nothing more), the signals are blocked where
    ``pthread_sigmask`` exists, the previous handlers are restored and the
    timers cancelled, the mask is restored, and a signal recorded meanwhile
    is raised again through the restored handler. The guard is closed before
    the mask call because that call itself runs pending handlers.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    first: list[int] = []
    timers: list[threading.Timer] = []
    closed: list[bool] = []
    late: list[int] = []
    sent: list[int] = []  # re-deliveries the timer made
    lock = threading.Lock()

    def redeliver(signum: int) -> None:
        # Under the lock, so nothing is sent once the guard is closing.
        with lock:
            if closed:
                return
            sent.append(signum)
            _send_to_main(signum)

    def arm(delay: float, action: Callable[..., None], *args: Any) -> None:
        timer = threading.Timer(delay, action, args)
        timer.daemon = True
        timer.start()
        timers.append(timer)

    @_coalescing  # a signal while this runs is coalesced (core, as for main)
    def handler(signum: int, _frame: Any) -> None:
        if signum in sent:
            sent.remove(signum)
            if closed:
                return  # our own re-delivery, arriving late: drop it
        elif closed:
            late.append(signum)
            return
        if _defer_in_importlib(signum, _frame):
            return  # raising inside importlib could leave its lock held
        if sinks.defer_signal(signum):
            return  # creating, committing or removing the file
        if first:
            # Second signal, or the timer: stop waiting for the stream.
            cleanup()
            decisive = sinks.most_severe([first[0], signum]) or signum
            if decisive == signal.SIGINT:
                # Unwind again from wherever the stall is; if that stalls
                # too, end without waiting any longer.
                arm(_SIGNAL_GRACE_S, os._exit, 2)
                raise KeyboardInterrupt
            _end_by_signal(decisive)
        first.append(signum)
        arm(_SIGNAL_GRACE_S, redeliver, signum)
        if signum == signal.SIGINT:
            raise KeyboardInterrupt
        raise _Terminated(signum)

    handler._deepctl_defers = True  # type: ignore[attr-defined]

    previous: dict[int, Any] = {}
    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        current = signal.getsignal(sig)
        if current is signal.SIG_IGN or current is None:
            continue
        try:
            signal.signal(sig, handler)
        except (OSError, ValueError):
            continue
        previous[sig] = current
    try:
        yield
    finally:
        with lock:
            closed.append(True)
        blocked = None
        if hasattr(signal, "pthread_sigmask"):
            with suppress(OSError, ValueError):
                blocked = signal.pthread_sigmask(signal.SIG_BLOCK, set(previous))
        try:
            sinks.restore_handlers(previous)
        finally:
            for timer in timers:
                timer.cancel()
            if blocked is not None:
                signal.pthread_sigmask(signal.SIG_SETMASK, blocked)
            if late:
                again = sinks.most_severe(late)
                late.clear()
                if again is not None:
                    signal.raise_signal(again)


@dataclass
class _Plan:
    """What the pre-request checks decided."""

    target: sinks.Target | None = None


def _fail(message: str) -> BaseResult:
    """An error result whose message the user actually sees.

    In default output mode the framework prints nothing for a returned
    result — it only maps the status to a non-zero exit code — so the human
    message has to go to the stderr console here, the way every other status
    line in this command does. The message is escaped because it can carry a
    user-supplied path, and rich would otherwise eat any [bracketed] segment
    of it as markup.
    """
    console.print(f"[red]Error:[/red] {escape(message)}")
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


def _speak_result(
    audio_file: sinks.AudioFile | None,
    *,
    model: str,
    bytes_written: int,
    played: bool = False,
) -> SpeakResult:
    """The 0.4.0 ``SpeakResult`` (unchanged keys, including ``played``)."""
    path = audio_file.path if audio_file is not None else ""
    return SpeakResult(
        status="success",
        message=f"Audio saved to {path}" if path else f"Played {bytes_written:,} bytes",
        output_path=path,
        model=model,
        bytes_written=bytes_written,
        played=played,
    )


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
        'dg speak "Hello world" --out-file hello.wav',
        'dg speak "Hello world" --out-file hello.wav --play',
        "dg speak --file message.txt --out-file output.wav",
        'dg speak "Hello" | ffplay -loglevel error -nodisp -autoexit -',
        # Flux TTS streaming controls: --speed (0.85-1.15) and beta
        # --expressivity (-2..2, default 0).
        'dg speak "A little slower, please" --speed 0.9 --out-file slow.wav',
        'dg speak "So exciting!" --expressivity 2 --out-file lively.wav  # beta; default: 0',
        # Aura (Speak v1, batch REST) — opt in with -m aura-*; needed for
        # containerized formats like mp3.
        'dg speak "Hello" -m aura-2-asteria-en --out-file hello.mp3',
        'dg speak "Hello" -m aura-2-luna-en --out-file hello.wav --encoding linear16 --container wav',
        'echo "Hello" | dg speak --out-file hello.mp3 -m aura-2-asteria-en',
        # Aura-2 Spanish voice (run `dg models` for the full list).
        'dg speak "Hola, mundo" -m aura-2-selena-es --out-file hola.mp3',
    ]
    agent_help = (
        "Convert text to speech using Deepgram's TTS API. "
        "Text can be provided as an argument, from a file, or piped via stdin. "
        "Audio is written to a file (--out-file; add --force to replace an "
        "existing file) or stdout for piping. -o/--output FILE is deprecated. "
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
                "names": ["--out-file"],
                "help": (
                    "Save the audio to FILE. An existing file isn't "
                    "overwritten unless you add --force."
                ),
                "type": str,
                "metavar": "FILE",
                "is_option": True,
            },
            {
                "names": ["--force"],
                "help": "With --out-file, overwrite FILE if it already exists.",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--output", "-o"],
                "help": (
                    "Deprecated: use --out-file. Saves the audio to FILE and "
                    "overwrites it. In 1.0, -o will set the output format."
                ),
                "type": str,
                "metavar": "FILE",
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
                    "paplay, or aplay). Can be combined with --out-file to save "
                    "and play."
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

    # ── Checks before the request ─────────────────────────────────────

    _plan: _Plan | None = None

    def pre_auth_check(self, config: Config, **kwargs: Any) -> Any | None:
        """Refusals that need no request: they run
        before authentication, so nothing is sent or billed."""
        self._plan = None
        planned = self._preflight(kwargs)
        if isinstance(planned, SpeakCodedError):
            return planned
        self._plan = planned
        return None

    def _preflight(self, kwargs: dict[str, Any]) -> _Plan | SpeakCodedError:
        if kwargs.get("list_voices"):
            return _Plan()
        out_file = kwargs.get("out_file") or None
        legacy = kwargs.get("output") or None
        prog = _prog()

        if out_file and legacy:
            return _coded(
                msg.E_SPEAK_OUT_CONFLICT,
                code=msg.CODE_INVALID_ARGUMENT,
                hint_human=msg.H_SPEAK_OUT_CONFLICT,
                hint_agent=msg.H_SPEAK_OUT_CONFLICT,
                next_step=_next_step([prog, self.name, "--help"]),
            )
        if legacy and legacy.lower() == "text":
            # Not a root format either: point at --out-file instead.
            return _coded(
                msg.E_SPEAK_O_TEXT.format(prog=prog),
                code=msg.CODE_INVALID_ARGUMENT,
                hint_human=msg.H_SPEAK_O_TEXT,
                hint_agent=msg.H_SPEAK_O_TEXT,
                next_step=self._o_format_next_step(prog, legacy, kwargs),
            )
        if legacy and legacy.lower() in _FORMAT_WORDS:
            return _coded(
                msg.E_SPEAK_O_FORMAT.format(prog=prog, value=legacy),
                code=msg.CODE_INVALID_ARGUMENT,
                hint_human=msg.H_SPEAK_O_FORMAT.format(prog=prog, value=legacy),
                hint_agent=msg.H_SPEAK_O_FORMAT.format(prog=prog, value=legacy),
                next_step=self._o_format_next_step(prog, legacy, kwargs),
            )
        if legacy:
            _deprecate_output()

        path = out_file or legacy
        if not path:
            return _Plan()
        planned = sinks.plan_target(
            path, overwrite=bool(legacy) or bool(kwargs.get("force"))
        )
        if isinstance(planned, sinks.Refusal):
            if planned.reason == "exists":
                return _coded(
                    msg.E_OUT_FILE_EXISTS.format(out_file=path, prog=prog),
                    code=msg.CODE_INVALID_ARGUMENT,
                    hint_human=msg.H_OUT_FILE_EXISTS,
                    hint_agent=msg.H_OUT_FILE_EXISTS,
                )
            assert planned.error is not None
            return _output_unwritable(planned.error, path=path)
        if (
            planned.kind == "stdout"
            and get_output_format() in ("json", "yaml")
            and format_source() == "explicit"
        ):
            # The audio is stdout's payload; there's no room for JSON.
            fmt = get_output_format().upper()
            return _coded(
                msg.E_STDOUT_BYTES.format(
                    prog=prog, command_path=self.name, format=fmt
                ),
                code=msg.CODE_INVALID_ARGUMENT,
                hint_human=msg.H_STDOUT_BYTES.format(format=fmt),
                hint_agent=msg.H_STDOUT_BYTES.format(format=fmt),
                next_step=_next_step([prog, self.name, "--help"]),
            )
        return _Plan(target=planned)

    def _o_format_next_step(
        self, prog: str, value: str, kwargs: dict[str, Any]
    ) -> str | None:
        """``{prog} {root_options} -o {value} speak {speak_args} --out-file
        {out_file}``, or ``None`` when it can't be built safely. For
        ``-o text``, which no command takes, the ``-o`` is dropped."""
        text = value.lower() == "text"
        if not text and value.lower() not in _ROOT_FORMATS:
            return None
        ctx = click.get_current_context(silent=True)
        name = (ctx.info_name if ctx is not None else None) or self.name
        split = _split_argv(_argv(), name)
        if split is None:
            return None
        root, args = split
        model = kwargs.get("model") or _DEFAULT_MODEL
        out_file = _proposed_out_file(
            is_flux=model.lower().startswith("flux-"),
            encoding=kwargs.get("encoding"),
            container=kwargs.get("container"),
        )
        return _next_step(
            [
                prog,
                *_drop_output(root),
                *(() if text else ("-o", value)),
                name,
                *_drop_output(args),
                "--out-file",
                out_file,
            ]
        )

    # ── Run ───────────────────────────────────────────────────────────

    def handle(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> BaseResult | SpeakCodedError | None:
        planned = self._plan if self._plan is not None else self._preflight(kwargs)
        self._plan = None
        if isinstance(planned, SpeakCodedError):
            return planned

        text = kwargs.get("text")
        model = kwargs.get("model") or _DEFAULT_MODEL
        encoding = kwargs.get("encoding")
        container = kwargs.get("container")
        sample_rate = kwargs.get("sample_rate")
        speed = kwargs.get("speed")
        expressivity = kwargs.get("expressivity")
        file_path = kwargs.get("file")
        play = kwargs.get("play", False)
        list_voices = kwargs.get("list_voices", False)
        output_path = planned.target.path if planned.target else None

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
                    "No audio player found. Install ffmpeg, or save the audio "
                    "with --out-file FILE."
                )

        # If stdout is a TTY and there's nowhere for the audio to go, require an
        # explicit destination. --play satisfies that requirement.
        stdout_is_tty = sys.stdout.isatty()
        if not output_path and not play and stdout_is_tty:
            return _fail(msg.NO_OUTPUT_TTY)

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
            print_warning(msg.PLAY_STDOUT_EMPTY, stderr=True, ignore_quiet=True)

        audio_file = sinks.AudioFile(planned.target) if planned.target else None
        if audio_file is not None and audio_file.is_stdout:
            # stdout carries audio, so a failure is reported on stderr
            # only, never as JSON after the audio.
            self.stdout_is_protocol = True
        try:
            with _terminate_cleanly(
                audio_file.abort if audio_file is not None else lambda: None
            ):
                try:
                    result = self._speak(
                        client,
                        is_flux=is_flux,
                        text=text,
                        model=model,
                        encoding=encoding,
                        container=container,
                        sample_rate=sample_rate,
                        speed=speed,
                        expressivity=expressivity,
                        player=player,
                        audio_file=audio_file,
                    )
                except BaseException:
                    # Clean up while the guard is still installed, so a
                    # second signal can't cut the removal short.
                    if audio_file is not None:
                        audio_file.abort()
                    raise
        except _Terminated as exc:
            # The partial file or temp file is gone (an old file is kept);
            # end by the signal itself.
            _end_by_signal(exc.signum)
            raise
        except sinks.Exists:
            # O_EXCL lost a race with another writer: nothing of ours exists.
            return _coded(
                msg.E_OUT_FILE_EXISTS.format(out_file=output_path, prog=_prog()),
                code=msg.CODE_INVALID_ARGUMENT,
                hint_human=msg.H_OUT_FILE_EXISTS,
                hint_agent=msg.H_OUT_FILE_EXISTS,
            )
        except sinks.WriteFailed as exc:
            if audio_file is not None:
                audio_file.abort()
            if exc.stdout:
                return _stdout_unwritable(exc.error)
            assert audio_file is not None
            return _output_unwritable(exc.error, path=audio_file.path)
        except sinks.ReaderGone:
            # Whoever read the pipe went away: stop quietly, like stdout.
            if audio_file is not None and audio_file.is_stdout:
                _silence_stdout()
            return None
        finally:
            if audio_file is not None:
                audio_file.abort()  # a no-op once the file was committed

        if (
            audio_file is not None
            and audio_file.is_stdout
            and isinstance(result, SpeakResult)
            and result.status == "success"
        ):
            # The audio was stdout's payload, so the summary goes to
            # stderr and no document follows it.
            n = audio_file.bytes_written
            if _agentic:
                console.print(f"OK: Wrote {n:,} bytes to stdout", markup=False)
            else:
                console.print(f"[green]✓ Wrote {n:,} bytes to stdout[/green]")
            return None
        return result

    def _speak(
        self,
        client: DeepgramClient,
        *,
        is_flux: bool,
        text: str,
        model: str,
        encoding: str | None,
        container: str | None,
        sample_rate: float | None,
        speed: float | None,
        expressivity: int | None,
        player: str | None,
        audio_file: sinks.AudioFile | None,
    ) -> BaseResult | SpeakCodedError | None:
        if is_flux:
            return self._speak_flux(
                client,
                text=text,
                model=model,
                encoding=encoding,
                sample_rate=sample_rate,
                speed=speed,
                expressivity=expressivity,
                player=player,
                audio_file=audio_file,
            )
        return self._speak_v1(
            client,
            text=text,
            model=model,
            encoding=encoding,
            container=container,
            sample_rate=sample_rate,
            player=player,
            audio_file=audio_file,
        )

    # ── Flux (Speak v2, WebSocket streaming) ──────────────────────────

    def _speak_flux(
        self,
        client: DeepgramClient,
        *,
        text: str,
        model: str,
        encoding: str | None,
        sample_rate: float | None,
        speed: float | None,
        expressivity: int | None,
        player: str | None,
        audio_file: sinks.AudioFile | None,
    ) -> BaseResult | SpeakCodedError | None:
        # Streaming output is raw audio, so default to linear16 @ 24kHz and
        # wrap it in WAV for playback. These paths raise (rather than
        # returning an error result) so a failure exits non-zero and is
        # visible in every output format.
        eff_encoding = encoding or "linear16"
        if eff_encoding not in ("linear16", "mulaw", "alaw"):
            raise click.ClickException(
                f"Flux voices stream raw audio, so --encoding {eff_encoding} "
                "isn't available. Use linear16 (the default), mulaw or alaw."
            )
        rate = int(sample_rate or 24000.0)

        # A live byte counter + time-to-first-audio on stderr, so the stream
        # is visibly arriving rather than a single static line.
        prog = _StreamProgress(model)
        raw = client.speak_text_stream(
            text=text,
            model=model,
            encoding=eff_encoding,
            sample_rate=float(rate),
            speed=speed,
            expressivity=expressivity,
        )
        stream = prog.track(raw)
        try:
            if player in _STDIN_PLAYER_ARGV and eff_encoding == "linear16":
                assert player is not None
                return self._flux_play_streaming(
                    stream,
                    prog,
                    player=player,
                    rate=rate,
                    audio_file=audio_file,
                    model=model,
                )
            if audio_file is not None or player:
                return self._flux_buffered(
                    stream,
                    prog,
                    player=player,
                    rate=rate,
                    encoding=eff_encoding,
                    audio_file=audio_file,
                    model=model,
                )
            return self._flux_to_stdout(
                stream,
                prog,
                header=(
                    _streaming_wav_header(sample_rate=rate)
                    if eff_encoding == "linear16"
                    else b""
                ),
            )
        except click.ClickException:
            raise
        except _SINK_ERRORS:
            raise
        except Exception as e:
            raise click.ClickException(f"Flux streaming failed: {e}")
        finally:
            _close(stream, raw)

    def _flux_play_streaming(
        self,
        stream: Iterator[bytes],
        prog: _StreamProgress,
        *,
        player: str,
        rate: int,
        audio_file: sinks.AudioFile | None,
        model: str,
    ) -> BaseResult:
        """Flux linear16 into a stdin-reading player, frame by frame.

        The player gets the streaming WAV header before the first frame and
        then every frame as Flux emits it, so sound starts at first-audio
        latency — the same framing `dg speak | ffplay -` relies on. A file
        gets the exact-length WAV once the stream ends; it is created on
        the first frame.
        """
        console.print(f"[blue]Playing audio ({player})...[/blue]")
        pcm = bytearray()
        header = _streaming_wav_header(sample_rate=rate)
        player_gone = False
        with _player_stdin(player) as sink:
            for chunk in stream:
                if not chunk:
                    continue
                if audio_file is not None:
                    audio_file.open()
                    pcm.extend(chunk)
                if player_gone:
                    continue
                try:
                    if header:
                        sink.write(header)
                        header = b""
                    sink.write(chunk)
                    sink.flush()
                except BrokenPipeError:
                    # The player exited first (ffplay's "q"): the user
                    # stopped playback, not a failure. With a file, keep
                    # draining so it is complete; otherwise stop.
                    player_gone = True
                    if audio_file is None:
                        break

            if audio_file is not None and pcm:
                # Save before waiting on the player (on leaving this block),
                # so stopping playback still leaves the file behind.
                audio_file.write(_pcm_to_wav(bytes(pcm), sample_rate=rate))
                audio_file.commit()

        if prog.total == 0:
            raise click.ClickException(
                "Deepgram returned no audio. Try again, or try another --model."
            )

        if audio_file is not None:
            self._saved(audio_file, f"{prog.timing()}")
        else:
            console.print(
                f"[green]✓ Played {prog.total:,} bytes[/green] ({prog.timing()})"
            )
        return _speak_result(
            audio_file,
            model=model,
            bytes_written=(audio_file.bytes_written if audio_file else 0) or prog.total,
            played=True,
        )

    def _flux_buffered(
        self,
        stream: Iterator[bytes],
        prog: _StreamProgress,
        *,
        player: str | None,
        rate: int,
        encoding: str,
        audio_file: sinks.AudioFile | None,
        model: str,
    ) -> BaseResult:
        """Flux into a file and/or afplay: both need the whole utterance.

        A WAV file declares its data length in the header, known only once
        the stream ends, and afplay is handed a complete file. The file is
        still created when the first frame arrives.
        """
        pcm = bytearray()
        for chunk in stream:
            if chunk and audio_file is not None:
                audio_file.open()
            pcm.extend(chunk)

        if not pcm:
            # Wrapping empty PCM yields a valid header-only WAV, so guard
            # rather than report success on a silent file.
            raise click.ClickException(
                "Deepgram returned no audio. Try again, or try another --model."
            )

        if encoding == "linear16":
            audio_bytes = _pcm_to_wav(bytes(pcm), sample_rate=rate)
        else:
            audio_bytes = bytes(pcm)

        if audio_file is not None:
            audio_file.write(audio_bytes)
            audio_file.commit()
            self._saved(audio_file, prog.timing())

        if player:
            suffix = _play_suffix(is_flux=True, encoding=encoding, container=None)
            console.print(f"[blue]Playing audio ({player})...[/blue]")
            _play_audio(player, audio_bytes, suffix=suffix)

        return _speak_result(
            audio_file, model=model, bytes_written=len(audio_bytes), played=bool(player)
        )

    def _flux_to_stdout(
        self, stream: Iterator[bytes], prog: _StreamProgress, *, header: bytes
    ) -> SpeakCodedError | None:
        """Pipe path: each frame goes to stdout as it arrives, after the
        streaming WAV header for linear16 (so `| ffplay -` plays with no
        extra flags); raw mulaw/alaw stream as-is."""
        _, outcome = self._stream_to_stdout(stream, header=header)
        if outcome is not True:
            # The reader went away (quiet, exit 0) or stdout failed.
            return outcome or None

        if prog.total == 0:
            # The header is only emitted on the first frame, so a no-audio
            # stream wrote nothing.
            raise click.ClickException(
                "Deepgram returned no audio. Try again, or try another --model."
            )

        console.print(
            f"[green]✓ Streamed {prog.total:,} bytes to stdout[/green] "
            f"({prog.timing()})"
        )
        # Return None so the framework skips output_result: in agentic/CI
        # (or --output json) mode it would serialize the result to the
        # stdout console — i.e. append JSON to the audio we just streamed,
        # corrupting a `> out.wav` redirect.
        return None

    # ── Speak v1 (REST) ───────────────────────────────────────────────

    def _speak_v1(
        self,
        client: DeepgramClient,
        *,
        text: str,
        model: str,
        encoding: str | None,
        container: str | None,
        sample_rate: float | None,
        player: str | None,
        audio_file: sinks.AudioFile | None,
    ) -> BaseResult | SpeakCodedError | None:
        """Speak v1, including Aura and unknown-model pass-through. Each
        chunk goes on as the SDK yields it: to stdout (flushed), to a
        stdin-reading player, and to the file, so none of them waits for
        the whole utterance."""
        console.print(f"[blue]Generating speech with {model}...[/blue]")
        try:
            audio_iter = client.speak_text(
                text=text,
                model=model,
                encoding=encoding,
                container=container,
                sample_rate=sample_rate,
            )
        except Exception as e:
            raise click.ClickException(f"Error generating speech: {e}")

        try:
            if player in _STDIN_PLAYER_ARGV:
                assert player is not None
                return self._v1_play_streaming(
                    audio_iter, player=player, audio_file=audio_file, model=model
                )
            if player:
                return self._v1_play_buffered(
                    audio_iter,
                    player=player,
                    audio_file=audio_file,
                    model=model,
                    encoding=encoding,
                    container=container,
                )
            if audio_file is not None:
                for chunk in audio_iter:
                    audio_file.write(chunk)
                if not audio_file.bytes_written:
                    return _fail(
                        "Deepgram returned no audio. Try again, or try another --model."
                    )
                audio_file.commit()
                self._saved(audio_file)
                return _speak_result(
                    audio_file, model=model, bytes_written=audio_file.bytes_written
                )

            total_bytes, outcome = self._stream_to_stdout(audio_iter)
            if outcome is not True:
                # The reader went away (quiet, exit 0) or stdout failed.
                return outcome or None
            console.print(f"[green]✓ Wrote {total_bytes:,} bytes to stdout[/green]")
            # Return None so the framework skips output_result — otherwise
            # agentic/CI (or --output json) mode serializes JSON onto the
            # same stdout as the audio, corrupting a `> out` redirect.
            return None

        except click.ClickException:
            raise
        except _SINK_ERRORS:
            raise
        except Exception as e:
            # Raise (not return an error result) so the failure exits non-zero
            # in every output mode — a returned BaseResult is only printed and
            # still exits 0. Reachable now that unknown models (e.g. a bare
            # "flux" typo) route here instead of the raising v2 path.
            raise click.ClickException(f"Error generating speech: {e}")
        finally:
            _close(audio_iter)

    def _stream_to_stdout(
        self, stream: Iterator[bytes], *, header: bytes = b""
    ) -> tuple[int, SpeakCodedError | bool]:
        """Write each chunk to stdout and flush it as it arrives.

        ``header`` goes out just before the first chunk, so a stream with no
        audio writes nothing. Returns the audio bytes written and ``True``
        when the stream ran to its end; ``False`` when the reader of stdout
        went away (EPIPE: fd 1 now points at the null device and the caller
        ends quietly); or the ``output_unwritable`` envelope when the write
        failed (ENOSPC, EIO, ...). Errors from the stream itself propagate.
        """
        out = sys.stdout.buffer
        total = 0
        for chunk in stream:
            if not chunk:
                continue
            # stdout now carries audio: a later failure is reported on
            # stderr only, never as JSON after the audio.
            self.stdout_is_protocol = True
            try:
                if header:
                    out.write(header)
                    header = b""
                out.write(chunk)
                out.flush()
            except BrokenPipeError:
                _silence_stdout()
                return total, False
            except OSError as exc:
                return total, _stdout_unwritable(exc)
            total += len(chunk)
        return total, True

    def _v1_play_streaming(
        self,
        audio_iter: Iterator[bytes],
        *,
        player: str,
        audio_file: sinks.AudioFile | None,
        model: str,
    ) -> BaseResult:
        """Speak v1 into a stdin-reading player, chunk by chunk.

        The player is started before the request, so sound starts as the
        first chunk arrives. With a file, every chunk also goes there.
        """
        console.print(f"[blue]Playing audio ({player})...[/blue]")
        total = 0
        player_gone = False
        try:
            with _player_stdin(player) as sink:
                for chunk in audio_iter:
                    if not chunk:
                        continue
                    total += len(chunk)
                    if audio_file is not None:
                        audio_file.write(chunk)
                    if player_gone:
                        continue
                    try:
                        sink.write(chunk)
                        sink.flush()
                    except BrokenPipeError:
                        # The user stopped playback (ffplay's "q"). With a
                        # file, keep draining so it is complete; otherwise
                        # stop and close the response.
                        player_gone = True
                        if audio_file is None:
                            break
                if not total:
                    # Kill the player rather than hand it an empty stream.
                    raise _NoAudio
                if audio_file is not None:
                    # Before waiting on the player, so stopping playback
                    # still leaves the file behind.
                    audio_file.commit()
        except _NoAudio:
            return _fail(
                "Deepgram returned no audio. Try again, or try another --model."
            )

        if audio_file is not None:
            self._saved(audio_file)
        return _speak_result(audio_file, model=model, bytes_written=total, played=True)

    def _v1_play_buffered(
        self,
        audio_iter: Iterator[bytes],
        *,
        player: str,
        audio_file: sinks.AudioFile | None,
        model: str,
        encoding: str | None,
        container: str | None,
    ) -> BaseResult:
        """Speak v1 into a player that has no stdin mode (afplay).

        It is handed a complete temp file, so the utterance is buffered; a
        file still gets each chunk as it arrives.
        """
        audio = bytearray()
        for chunk in audio_iter:
            if audio_file is not None:
                audio_file.write(chunk)
            audio.extend(chunk)
        audio_bytes = bytes(audio)

        if not audio_bytes:
            return _fail(
                "Deepgram returned no audio. Try again, or try another --model."
            )

        if audio_file is not None:
            audio_file.commit()
            self._saved(audio_file)

        suffix = _play_suffix(is_flux=False, encoding=encoding, container=container)
        console.print(f"[blue]Playing audio ({player})...[/blue]")
        _play_audio(player, audio_bytes, suffix=suffix)

        return _speak_result(
            audio_file, model=model, bytes_written=len(audio_bytes), played=True
        )

    @staticmethod
    def _saved(audio_file: sinks.AudioFile, timing: str = "") -> None:
        """The stderr summary for a saved file (none for stdout)."""
        if audio_file.is_stdout:
            return
        extra = f" — {timing}" if timing else ""
        console.print(
            f"[green]Audio saved to {escape(audio_file.path)}[/green] "
            f"({audio_file.bytes_written:,} bytes{extra})"
        )

    # ── Result on stdout ──────────────────────────────────────────────

    #: Writing the result to stdout failed; the run exits 1.
    _output_failed: bool = False

    def output_result(self, result: Any, config: Config) -> None:
        """Write the result; a stdout that can't take it is
        ``output_unwritable`` (exit 1), never an exit-time "Exception
        ignored"."""
        try:
            super().output_result(result, config)
            if result is not None:
                sys.stdout.flush()
        except BrokenPipeError:
            _silence_stdout()
        except SystemExit as exc:
            # rich handles EPIPE itself: it points stdout at the null device
            # and raises SystemExit(1). The reader went away, which ends the
            # run quietly with 0, as the streaming paths do.
            if not isinstance(exc.__context__, BrokenPipeError):
                raise
            _silence_stdout()
        except OSError as exc:
            _silence_stdout()
            if isinstance(result, SpeakCodedError):
                return  # already reported on stderr; it exits 1 anyway
            _output_unwritable(exc, path="-")
            self._output_failed = True

    def exit_code_for(self, result: Any) -> int:
        if self._output_failed:
            return 1
        return super().exit_code_for(result)

    def _list_voices(self, client: DeepgramClient) -> BaseResult:
        """List available TTS voices as a table (mirrors `dg models`)."""
        try:
            result = client.list_models()
        except Exception as e:
            console.print(f"[red]Error listing voices:[/red] {e}")
            return BaseResult(status="error", message=str(e))

        voices: list[VoiceInfo] = []
        for m in result.get("tts", []):
            # canonical_name is the value -m takes ("aura-2-agathe-fr"); the
            # bare "name" is just the voice ("agathe") and is not a usable
            # model id. Languages moved to a list in the same catalog reshape,
            # so keep the singular key as the fallback for both.
            name = m.get("canonical_name") or m.get("name") or ""
            languages = [str(lang) for lang in (m.get("languages") or [])]
            legacy_language = m.get("language") or ""
            if not languages and legacy_language:
                languages = [legacy_language]
            voices.append(
                VoiceInfo(
                    name=name,
                    voice_type=_voice_type_badge(name),
                    # The table wants one cell; -o json consumers want the
                    # tags as data, so carry both rather than make them split
                    # a display string.
                    language=", ".join(languages),
                    languages=languages,
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
            # The catalog endpoint returns no Flux voices today, so the
            # default is missing from the list --model's help points at.
            # Say so rather than let it read as a typo; computed, so the
            # note disappears once the catalog carries it.
            default_note = (
                ""
                if any(v.name == _DEFAULT_MODEL for v in voices)
                else " (not listed above)"
            )
            stdout_console.print(
                f"\n[dim]{len(voices)} voice(s) — generate with "
                f'dg speak "..." -m <voice>; '
                f"default: {_DEFAULT_MODEL}{default_note}[/dim]"
            )

        return SpeakVoicesResult(
            status="success",
            voices=voices,
            count=len(voices),
        )
