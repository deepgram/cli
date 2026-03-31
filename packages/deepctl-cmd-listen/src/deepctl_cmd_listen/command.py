"""Unified listen/transcribe command for deepctl.

Handles all Deepgram STT modes from a single entry point:
  - Pre-recorded file      dg listen audio.mp3
  - Pre-recorded URL       dg listen https://example.com/audio.mp3
  - Live microphone        dg listen --mic
  - Live stdin stream      dg listen --encoding linear16 < audio.raw
                           ffmpeg ... | dg listen --encoding linear16
  - Explicit stdin         dg listen -

`dg transcribe` is registered as an alias to this command.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from deepctl_core import (
    AuthManager,
    BaseCommand,
    BaseResult,
    Config,
    DeepgramClient,
)
from deepctl_core.output import _agentic
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from .captions import (
    StreamingCaptionWriter,
    captions_from_prerecorded,
    captions_from_words,
)
from .formatters import (
    extract_plain_transcript,
    extract_sentiment,
    extract_summary,
    extract_topics,
    format_diarized_transcript,
    format_diarized_words,
)
from .models import ListenResult

# Status messages → stderr so stdout stays clean for transcript piping
status = Console(stderr=True)
# Structured output (table, json) → stdout
out = Console()


def _is_url(s: str) -> bool:
    return s.startswith(("http://", "https://"))


def _ws_base(client: DeepgramClient) -> str:
    """Build the WebSocket base URL from the configured profile."""
    profile = client.config.get_profile()
    base = (profile.base_url or "https://api.deepgram.com").rstrip("/")
    return base.replace("https://", "wss://").replace("http://", "ws://")


class ListenCommand(BaseCommand):
    """Unified speech-to-text command supporting files, URLs, mic, and streams."""

    name = "listen"
    help = (
        "Speech-to-text: transcribe files, URLs, microphone, and live streams.\n\n"
        "Source is auto-detected: pass a file path or URL as the first argument,\n"
        "use --mic for live microphone, pipe audio via stdin, or run with no\n"
        "arguments for interactive source selection."
    )
    short_help = "Speech-to-text"

    requires_auth = True
    requires_project = False
    ci_friendly = True

    examples = [
        "dg listen audio.mp3",
        "dg listen https://example.com/call.mp3 --diarize",
        "dg listen --mic --model nova-3 --interim",
        "dg listen audio.mp3 --diarize --summarize --save-to transcript.txt",
        "dg listen audio.mp3 -o json | jq '.full_result.results.channels[0].alternatives[0].transcript'",
        "ffmpeg -i video.mp4 -f s16le -ar 16000 -ac 1 - | dg listen --encoding linear16",
        "dg listen -  # read raw audio from stdin interactively",
    ]
    agent_help = (
        "Unified speech-to-text command. Auto-detects input mode from the SOURCE "
        "argument (file path or URL), --mic flag, or stdin pipe. Supports diarization "
        "(--diarize), summarization (--summarize), topic detection (--topics), "
        "smart formatting, and all Deepgram models. Use -o json for machine-readable "
        "output of the full Deepgram API response. `dg transcribe` is an alias."
    )

    def get_arguments(self) -> list[dict[str, Any]]:
        return [
            # ── Source ────────────────────────────────────────────────
            {
                "name": "source",
                "help": (
                    "Audio file path, URL, or '-' for stdin. "
                    "Omit to use --mic or for interactive selection."
                ),
                "required": False,
                "default": None,
            },
            {
                "names": ["--mic"],
                "help": (
                    "Use microphone for live transcription "
                    "(requires: pip install 'deepctl-cmd-listen[mic]')"
                ),
                "is_flag": True,
                "is_option": True,
            },
            # ── Model ─────────────────────────────────────────────────
            {
                "names": ["--model", "-m"],
                "help": "Deepgram model to use (default: nova-3)",
                "type": str,
                "is_option": True,
                "default": "nova-3",
            },
            {
                "names": ["--language", "-l"],
                "help": "Language code, e.g. en-US, es-ES (default: en-US)",
                "type": str,
                "is_option": True,
                "default": "en-US",
            },
            # ── Features (all modes) ──────────────────────────────────
            {
                "names": ["--diarize"],
                "help": "Label speakers in output as [Speaker 0], [Speaker 1], …",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--smart-format"],
                "help": "Enable smart formatting — numbers, dates, punctuation (default: on)",
                "is_flag": True,
                "is_option": True,
                "default": True,
            },
            {
                "names": ["--punctuate"],
                "help": "Add punctuation (default: on)",
                "is_flag": True,
                "is_option": True,
                "default": True,
            },
            # ── Features (pre-recorded only) ──────────────────────────
            {
                "names": ["--summarize"],
                "help": "Generate a summary (pre-recorded only)",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--topics"],
                "help": "Detect topics (pre-recorded only)",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--sentiment"],
                "help": "Analyse sentiment (pre-recorded only)",
                "is_flag": True,
                "is_option": True,
            },
            # ── Live streaming options ────────────────────────────────
            {
                "names": ["--interim"],
                "help": "Show partial results as they arrive (live streaming only)",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--encoding"],
                "help": (
                    "Raw audio encoding for stdin streams "
                    "(e.g. linear16, mulaw, opus). Auto-detect skips this."
                ),
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--sample-rate"],
                "help": "Sample rate in Hz for raw stdin streams (default: 16000)",
                "type": int,
                "is_option": True,
                "default": 16000,
            },
            {
                "names": ["--channels"],
                "help": "Number of audio channels (default: 1)",
                "type": int,
                "is_option": True,
                "default": 1,
            },
            # ── Output options ────────────────────────────────────────
            {
                "names": ["--save-to", "-s"],
                "help": "Save transcript (or caption file) to a path",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--probe"],
                "help": "Run ffprobe analysis before transcribing (local files only)",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--no-validate"],
                "help": "Skip source validation (faster, but less helpful errors)",
                "is_flag": True,
                "is_option": True,
            },
            # ── Caption output ────────────────────────────────────────
            {
                "names": ["--webvtt"],
                "help": (
                    "Output WebVTT captions instead of plain transcript. "
                    "In live mode, entries are printed as they arrive. "
                    "Mutually exclusive with --srt."
                ),
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--srt"],
                "help": (
                    "Output SRT captions instead of plain transcript. "
                    "In live mode, entries are printed as they arrive. "
                    "Mutually exclusive with --webvtt."
                ),
                "is_flag": True,
                "is_option": True,
            },
        ]

    # ── Entry point ────────────────────────────────────────────────────

    def handle(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> BaseResult:
        source: str | None = kwargs.get("source")
        use_mic: bool = kwargs.get("mic", False)

        # ── Resolve mode ───────────────────────────────────────────────
        if source == "-":
            mode = "stream_stdin"
            source = None
        elif source and _is_url(source):
            mode = "prerecorded_url"
        elif source:
            mode = "prerecorded_file"
        elif use_mic:
            mode = "stream_mic"
        elif not sys.stdin.isatty():
            mode = "stream_stdin"
        elif _agentic:
            return BaseResult(
                status="error",
                message=(
                    "No audio source. Pass a file/URL as SOURCE, use --mic, "
                    "pipe audio via stdin, or use '-' to read stdin explicitly."
                ),
            )
        else:
            # Interactive: ask the user
            selected_mode, selected_source = self._interactive_select_source()
            if not selected_mode:
                return BaseResult(status="cancelled", message="Cancelled.")
            mode = selected_mode
            source = selected_source

        # ── Gather options ─────────────────────────────────────────────
        model = kwargs.get("model") or "nova-3"
        language = kwargs.get("language") or "en-US"

        # flux-* models require listen.v2; everything else uses v1.
        api_version = 2 if model.startswith("flux") else 1
        diarize = kwargs.get("diarize", False)
        smart_format = kwargs.get("smart_format", True)
        punctuate = kwargs.get("punctuate", True)
        summarize = kwargs.get("summarize", False)
        topics = kwargs.get("topics", False)
        sentiment = kwargs.get("sentiment", False)
        interim = kwargs.get("interim", False)
        encoding = kwargs.get("encoding")
        sample_rate = kwargs.get("sample_rate") or 16000
        channels = kwargs.get("channels") or 1
        save_to = kwargs.get("save_to")
        probe = kwargs.get("probe", False)
        no_validate = kwargs.get("no_validate", False)

        # ── Caption format ─────────────────────────────────────────────
        want_webvtt = kwargs.get("webvtt", False)
        want_srt = kwargs.get("srt", False)
        if want_webvtt and want_srt:
            return BaseResult(
                status="error",
                message="--webvtt and --srt are mutually exclusive. Use one or the other.",
            )
        caption_format: str | None = (
            "webvtt" if want_webvtt else ("srt" if want_srt else None)
        )
        # Diarization makes captions more useful — auto-enable when captioning
        if caption_format and not diarize:
            diarize = False  # keep explicit — user can add --diarize themselves

        # Interactive feature selection when user chose source interactively
        # (signals they want a guided experience)
        if (
            not _agentic
            and mode in ("prerecorded_file", "prerecorded_url")
            and not any([diarize, summarize, topics, sentiment])
        ):
            diarize, summarize, topics, sentiment = self._interactive_features()

        # ── Dispatch ───────────────────────────────────────────────────
        if mode == "prerecorded_file":
            assert source is not None
            return self._prerecorded(
                client,
                source,
                is_url=False,
                model=model,
                language=language,
                api_version=api_version,
                diarize=diarize,
                smart_format=smart_format,
                punctuate=punctuate,
                summarize=summarize,
                topics=topics,
                sentiment=sentiment,
                save_to=save_to,
                probe=probe,
                no_validate=no_validate,
                caption_format=caption_format,
                config=config,
            )
        elif mode == "prerecorded_url":
            assert source is not None
            return self._prerecorded(
                client,
                source,
                is_url=True,
                model=model,
                language=language,
                api_version=api_version,
                diarize=diarize,
                smart_format=smart_format,
                punctuate=punctuate,
                summarize=summarize,
                topics=topics,
                sentiment=sentiment,
                save_to=save_to,
                probe=False,
                no_validate=no_validate,
                caption_format=caption_format,
                config=config,
            )
        elif mode == "stream_mic":
            return self._stream_mic(
                client,
                model=model,
                language=language,
                api_version=api_version,
                diarize=diarize,
                smart_format=smart_format,
                punctuate=punctuate,
                interim=interim,
                sample_rate=sample_rate,
                channels=channels,
                save_to=save_to,
                caption_format=caption_format,
            )
        else:  # stream_stdin
            return self._stream_stdin(
                client,
                model=model,
                language=language,
                api_version=api_version,
                diarize=diarize,
                smart_format=smart_format,
                punctuate=punctuate,
                interim=interim,
                encoding=encoding,
                sample_rate=sample_rate,
                channels=channels,
                save_to=save_to,
                caption_format=caption_format,
            )

    # ── Interactive helpers ────────────────────────────────────────────

    def _interactive_select_source(
        self,
    ) -> tuple[str | None, str | None]:
        """Prompt the user to pick an audio source. Returns (mode, source_path)."""
        status.print()
        status.print("[bold]Select audio source:[/bold]")
        status.print("  [cyan]1[/cyan]  Microphone [dim](live)[/dim]")
        status.print("  [cyan]2[/cyan]  File")
        status.print("  [cyan]3[/cyan]  URL")
        status.print("  [cyan]4[/cyan]  Cancel")
        choice = Prompt.ask(
            "\nSource",
            choices=["1", "2", "3", "4"],
            default="1",
            console=status,
        )
        status.print()

        if choice == "4":
            return None, None
        elif choice == "1":
            return "stream_mic", None
        elif choice == "2":
            path = Prompt.ask("  Audio file path", console=status)
            return "prerecorded_file", path.strip()
        else:
            url = Prompt.ask("  Audio URL", console=status)
            return "prerecorded_url", url.strip()

    def _interactive_features(
        self,
    ) -> tuple[bool, bool, bool, bool]:
        """Ask the user which optional features to enable. Returns (diarize, summarize, topics, sentiment)."""
        status.print("[dim]Optional features (press Enter to skip all):[/dim]")
        diarize = Confirm.ask(
            "  Speaker diarization [Speaker 0] / [Speaker 1] …",
            default=False,
            console=status,
        )
        summarize = Confirm.ask("  Generate summary", default=False, console=status)
        topics = Confirm.ask("  Detect topics", default=False, console=status)
        sentiment = Confirm.ask("  Sentiment analysis", default=False, console=status)
        status.print()
        return diarize, summarize, topics, sentiment

    # ── Pre-recorded handler ───────────────────────────────────────────

    def _prerecorded(
        self,
        client: DeepgramClient,
        source: str,
        *,
        is_url: bool,
        model: str,
        language: str,
        api_version: int,
        diarize: bool,
        smart_format: bool,
        punctuate: bool,
        summarize: bool,
        topics: bool,
        sentiment: bool,
        save_to: str | None,
        probe: bool,
        no_validate: bool,
        caption_format: str | None,
        config: Config,
    ) -> BaseResult:
        from deepctl_shared_utils import (
            probe_file,
            require_ffprobe,
            validate_audio_file,
            validate_url,
        )

        source_kind = "url" if is_url else "file"

        # ── Validation ─────────────────────────────────────────────────
        if not no_validate:
            if is_url:
                if not validate_url(source, check_accessibility=True):
                    return BaseResult(
                        status="error", message=f"URL is not accessible: {source}"
                    )
            else:
                if not Path(source).exists():
                    return BaseResult(
                        status="error", message=f"File not found: {source}"
                    )
                if not validate_audio_file(source):
                    return BaseResult(
                        status="error",
                        message=f"File does not appear to be audio: {source}",
                    )

        # ── Optional ffprobe analysis ──────────────────────────────────
        probe_info: dict[str, Any] | None = None
        if probe and not is_url:
            if not require_ffprobe(config):
                status.print(
                    "[yellow]Warning:[/yellow] ffprobe not found — skipping probe"
                )
            else:
                probe_result = probe_file(source, config)
                if probe_result:
                    probe_info = probe_result.model_dump(exclude_none=True)
                    self._print_probe_table(probe_result)
                else:
                    status.print(
                        "[yellow]Warning:[/yellow] ffprobe analysis failed — continuing"
                    )

        # ── Build API options ──────────────────────────────────────────
        # Deepgram REST API requires lowercase string booleans ("true"/"false").
        # Python booleans (True/False) serialize as "True"/"False" and are
        # rejected with HTTP 400 INVALID_QUERY_PARAMETER.
        options: dict[str, Any] = {
            "model": model,
            "language": language,
            "smart_format": "true" if smart_format else "false",
            "punctuate": "true" if punctuate else "false",
        }
        if diarize:
            options["diarize"] = "true"
        if summarize:
            options["summarize"] = "true"
        if topics:
            options["topics"] = "true"
        if sentiment:
            options["sentiment"] = "true"

        # ── Call API ───────────────────────────────────────────────────
        status.print(f"[dim]Transcribing[/dim] {source}")
        status.print(f"[dim]Model:[/dim] {model}  [dim]Language:[/dim] {language}")

        try:
            if is_url:
                result_dict = client.transcribe_url(source, options)
            else:
                result_dict = client.transcribe_file(source, options)
        except Exception as e:
            return BaseResult(status="error", message=f"Transcription failed: {e}")

        # ── Format transcript ──────────────────────────────────────────
        if diarize:
            transcript = format_diarized_transcript(result_dict)
            if not transcript:
                # Fall back to plain if no word data
                transcript = extract_plain_transcript(result_dict)
        else:
            transcript = extract_plain_transcript(result_dict)

        # ── Caption output (WebVTT / SRT) ──────────────────────────────
        captions: str | None = None
        if caption_format:
            try:
                captions = captions_from_prerecorded(result_dict, caption_format)
            except Exception as e:
                status.print(
                    f"[yellow]Warning:[/yellow] Caption generation failed: {e}"
                )

        # ── Save to file ───────────────────────────────────────────────
        if save_to:
            content = captions if captions is not None else transcript
            if content:
                self._save_transcript(content, save_to)
                status.print(f"[green]✓[/green] Saved to {save_to}")

        # ── Metadata ───────────────────────────────────────────────────
        metadata = result_dict.get("metadata", {})
        raw_duration = metadata.get("duration", 0.0)
        duration_seconds = float(raw_duration) if raw_duration else 0.0

        return ListenResult(
            status="success",
            source=source_kind,
            source_path=source,
            mode="prerecorded",
            model=model,
            language=language,
            diarized=diarize,
            transcript=transcript,
            captions=captions,
            caption_format=caption_format,
            saved_to=save_to,
            full_result=result_dict,
            probe_info=probe_info,
            duration_seconds=duration_seconds,
        )

    # ── Live mic handler ───────────────────────────────────────────────

    def _stream_mic(
        self,
        client: DeepgramClient,
        *,
        model: str,
        language: str,
        api_version: int,
        diarize: bool,
        smart_format: bool,
        punctuate: bool,
        interim: bool,
        sample_rate: int,
        channels: int,
        save_to: str | None,
        caption_format: str | None,
    ) -> BaseResult:
        caption_writer: StreamingCaptionWriter | None = None
        if caption_format:
            caption_writer = StreamingCaptionWriter(caption_format)
            caption_writer.print_header()

        status.print(
            f"[green]✓[/green] Listening on microphone · {model} · Ctrl+C to stop"
        )
        if diarize:
            status.print("[dim]Speaker labels enabled[/dim]")
        if caption_format:
            status.print(f"[dim]Caption format: {caption_format.upper()}[/dim]")

        try:
            result = asyncio.run(
                self._ws_mic(
                    client,
                    model=model,
                    language=language,
                    api_version=api_version,
                    diarize=diarize,
                    smart_format=smart_format,
                    punctuate=punctuate,
                    interim=interim,
                    sample_rate=sample_rate,
                    channels=channels,
                    caption_writer=caption_writer,
                )
            )
        except KeyboardInterrupt:
            status.print("\n[yellow]Stopped.[/yellow]")
            result = ListenResult(status="success", source="mic", mode="live")
        except Exception as e:
            err = str(e)
            msg = f"Microphone error: {err}"
            if (
                "Invalid input device" in err
                or "PortAudio" in err
                or "No Default Input" in err
            ):
                msg += (
                    "\n\nOn macOS, make sure your terminal has microphone access:"
                    "\n  System Settings → Privacy & Security → Microphone"
                )
            return BaseResult(status="error", message=msg)

        # Save captions or transcript
        if save_to:
            if caption_writer and caption_writer.accumulated_words:
                content = captions_from_words(
                    caption_writer.accumulated_words, caption_format or "webvtt"
                )
            elif result.transcript:
                content = result.transcript
            else:
                content = ""
            if content:
                self._save_transcript(content, save_to)
                status.print(f"[green]✓[/green] Saved to {save_to}")

        return result

    async def _ws_mic(
        self,
        client: DeepgramClient,
        *,
        model: str,
        language: str,
        api_version: int,
        diarize: bool,
        smart_format: bool,
        punctuate: bool,
        interim: bool,
        sample_rate: int,
        channels: int,
        caption_writer: StreamingCaptionWriter | None = None,
    ) -> ListenResult:
        import numpy as np
        import sounddevice as sd
        import websockets

        url = self._ws_url(
            client,
            api_version=api_version,
            model=model,
            language=language,
            diarize=diarize,
            smart_format=smart_format,
            punctuate=punctuate,
            interim=interim,
            encoding="linear16",
            sample_rate=sample_rate,
            channels=channels,
        )
        api_key = client.auth_manager.get_api_key()
        full_transcript: list[str] = []
        stop_event = threading.Event()

        async with websockets.connect(
            url, additional_headers={"Authorization": f"Token {api_key}"}
        ) as ws:

            async def send_audio() -> None:
                loop = asyncio.get_event_loop()
                q: asyncio.Queue[bytes] = asyncio.Queue()

                def callback(indata: Any, frames: int, t: Any, s: Any) -> None:
                    if s:
                        status.print(f"[yellow]Audio warning: {s}[/yellow]")
                    loop.call_soon_threadsafe(q.put_nowait, bytes(indata))

                stream = sd.RawInputStream(
                    samplerate=sample_rate,
                    channels=channels,
                    dtype=np.int16,
                    callback=callback,
                    blocksize=int(sample_rate * 0.1),
                )
                stream.start()
                try:
                    while not stop_event.is_set():
                        try:
                            data = await asyncio.wait_for(q.get(), timeout=0.5)
                            await ws.send(data)
                        except asyncio.TimeoutError:
                            continue
                finally:
                    stream.stop()
                    stream.close()
                    await ws.send(json.dumps({"type": "CloseStream"}))

            async def recv_transcripts() -> None:
                async for msg in ws:
                    self._handle_ws_message(
                        msg,
                        full_transcript,
                        diarize=diarize,
                        interim=interim,
                        caption_writer=caption_writer,
                    )

            send_task = asyncio.create_task(send_audio())
            recv_task = asyncio.create_task(recv_transcripts())
            try:
                await asyncio.gather(send_task, recv_task)
            except (KeyboardInterrupt, asyncio.CancelledError):
                stop_event.set()
                send_task.cancel()
                recv_task.cancel()

        return ListenResult(
            status="success",
            source="mic",
            mode="live",
            model=model,
            language=language,
            diarized=diarize,
            transcript=" ".join(full_transcript),
        )

    # ── Live stdin handler ─────────────────────────────────────────────

    def _stream_stdin(
        self,
        client: DeepgramClient,
        *,
        model: str,
        language: str,
        api_version: int,
        diarize: bool,
        smart_format: bool,
        punctuate: bool,
        interim: bool,
        encoding: str | None,
        sample_rate: int,
        channels: int,
        save_to: str | None,
        caption_format: str | None,
    ) -> BaseResult:
        resolved_encoding = encoding or "linear16"
        if not encoding:
            status.print(
                "[dim]No --encoding specified — assuming linear16. "
                "Pass --encoding to suppress this warning.[/dim]"
            )

        caption_writer: StreamingCaptionWriter | None = None
        if caption_format:
            caption_writer = StreamingCaptionWriter(caption_format)
            caption_writer.print_header()

        status.print(
            f"[green]✓[/green] Streaming stdin · {model} · send EOF (Ctrl+D) to finish"
        )
        if caption_format:
            status.print(f"[dim]Caption format: {caption_format.upper()}[/dim]")

        try:
            result = asyncio.run(
                self._ws_stdin(
                    client,
                    model=model,
                    language=language,
                    api_version=api_version,
                    diarize=diarize,
                    smart_format=smart_format,
                    punctuate=punctuate,
                    interim=interim,
                    encoding=resolved_encoding,
                    sample_rate=sample_rate,
                    channels=channels,
                    caption_writer=caption_writer,
                )
            )
        except KeyboardInterrupt:
            status.print("\n[yellow]Stopped.[/yellow]")
            result = ListenResult(status="success", source="stdin", mode="live")

        if save_to:
            if caption_writer and caption_writer.accumulated_words:
                content = captions_from_words(
                    caption_writer.accumulated_words, caption_format or "webvtt"
                )
            elif result.transcript:
                content = result.transcript
            else:
                content = ""
            if content:
                self._save_transcript(content, save_to)
                status.print(f"[green]✓[/green] Saved to {save_to}")

        return result

    async def _ws_stdin(
        self,
        client: DeepgramClient,
        *,
        model: str,
        language: str,
        api_version: int,
        diarize: bool,
        smart_format: bool,
        punctuate: bool,
        interim: bool,
        encoding: str,
        sample_rate: int,
        channels: int,
        caption_writer: StreamingCaptionWriter | None = None,
    ) -> ListenResult:
        import websockets

        url = self._ws_url(
            client,
            api_version=api_version,
            model=model,
            language=language,
            diarize=diarize,
            smart_format=smart_format,
            punctuate=punctuate,
            interim=interim,
            encoding=encoding,
            sample_rate=sample_rate,
            channels=channels,
        )
        api_key = client.auth_manager.get_api_key()
        full_transcript: list[str] = []

        async with websockets.connect(
            url, additional_headers={"Authorization": f"Token {api_key}"}
        ) as ws:

            async def send_audio() -> None:
                loop = asyncio.get_event_loop()
                while True:
                    data = await loop.run_in_executor(None, sys.stdin.buffer.read, 4096)
                    if not data:
                        break
                    await ws.send(data)
                await ws.send(json.dumps({"type": "CloseStream"}))

            async def recv_transcripts() -> None:
                async for msg in ws:
                    self._handle_ws_message(
                        msg,
                        full_transcript,
                        diarize=diarize,
                        interim=interim,
                        caption_writer=caption_writer,
                    )

            await asyncio.gather(send_audio(), recv_transcripts())

        return ListenResult(
            status="success",
            source="stdin",
            mode="live",
            model=model,
            language=language,
            diarized=diarize,
            transcript=" ".join(full_transcript),
        )

    # ── Shared WebSocket helpers ───────────────────────────────────────

    def _ws_url(
        self,
        client: DeepgramClient,
        *,
        api_version: int,
        model: str,
        language: str,
        diarize: bool,
        smart_format: bool,
        punctuate: bool,
        interim: bool,
        encoding: str,
        sample_rate: int,
        channels: int,
    ) -> str:
        params: dict[str, Any] = {
            "model": model,
            "language": language,
            "smart_format": "true" if smart_format else "false",
            "punctuate": "true" if punctuate else "false",
            "encoding": encoding,
            "sample_rate": sample_rate,
            "channels": channels,
        }
        if diarize:
            params["diarize"] = "true"
        if interim:
            params["interim_results"] = "true"
        base = _ws_base(client)
        return f"{base}/v{api_version}/listen?{urlencode(params)}"

    def _handle_ws_message(
        self,
        raw_msg: str | bytes,
        transcript_acc: list[str],
        *,
        diarize: bool,
        interim: bool,
        caption_writer: StreamingCaptionWriter | None = None,
    ) -> None:
        """Parse one WebSocket message and print/accumulate the transcript."""
        try:
            data = json.loads(raw_msg)
        except Exception:
            return

        if data.get("type") != "Results":
            return

        channel = data.get("channel", {})
        alternatives = channel.get("alternatives", [])
        if not alternatives:
            return

        alt = alternatives[0]
        is_final = data.get("is_final", False)

        if is_final:
            words = alt.get("words", [])

            # Caption mode: route words to the writer, suppress plain text output
            if caption_writer and words:
                start = words[0].get("start", data.get("start", 0.0))
                end = words[-1].get("end", start + data.get("duration", 0.0))
                caption_writer.write_entry(words, start, end)
                transcript_acc.append(alt.get("transcript", ""))
                return

            # Diarize mode: format with speaker labels
            if diarize and words:
                line = format_diarized_words(words)
                if line:
                    transcript_acc.append(line)
                    print(line, flush=True)
                    return

            transcript = alt.get("transcript", "")
            if transcript:
                transcript_acc.append(transcript)
                print(transcript, flush=True)

        elif interim and not caption_writer:
            transcript = alt.get("transcript", "")
            if transcript:
                print(f"\r{transcript}          ", end="", flush=True)

    # ── Output rendering ───────────────────────────────────────────────

    def output_result(self, result: Any, config: Config) -> None:
        """Render the result. Streaming results are already printed line-by-line."""
        import json as _json
        from datetime import date, datetime

        def _default(obj: Any) -> str:
            if isinstance(obj, (datetime, date)):
                return obj.isoformat()
            return str(obj)

        from deepctl_core.output import get_output_format

        fmt = get_output_format()

        if not isinstance(result, ListenResult) or result.status != "success":
            super().output_result(result, config)
            return

        # Live results are printed in real-time; only show a brief summary
        if result.mode == "live":
            if result.status == "success" and not result.transcript:
                return  # nothing to add
            if fmt == "json":
                out.print_json(
                    _json.dumps(result.model_dump(exclude_none=True), default=_default)
                )
            return

        # Pre-recorded: full structured output
        if fmt == "json":
            payload = (
                result.full_result
                if result.full_result
                else result.model_dump(exclude_none=True)
            )
            out.print_json(_json.dumps(payload, indent=2, default=_default))
            return

        # Caption mode: print captions to stdout and skip the rich table
        if result.captions is not None:
            print(result.captions)
            return

        # Table / default: metadata + transcript
        self._print_prerecorded_result(result)

    def _print_prerecorded_result(self, result: ListenResult) -> None:
        """Print metadata table followed by transcript (and optionally summary/topics)."""
        metadata = (result.full_result or {}).get("metadata", {})
        model_info = metadata.get("model_info", {})
        model_name: str | None = None
        model_version: str | None = None
        for info in model_info.values():
            model_name = info.get("name")
            model_version = info.get("version")
            break

        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Key", style="dim")
        table.add_column("Value")

        table.add_row("Source", result.source_path)
        table.add_row("Model", model_name or result.model)
        if model_version:
            table.add_row("Version", model_version)
        if result.duration_seconds:
            m, s = divmod(result.duration_seconds, 60)
            table.add_row("Duration", f"{int(m)}:{s:05.2f}")
        channels_count = metadata.get("channels")
        if channels_count is not None:
            table.add_row("Channels", str(int(channels_count)))
        if result.diarized:
            table.add_row("Diarization", "enabled")
        if result.saved_to:
            table.add_row("Saved to", result.saved_to)

        out.print(table)
        out.print()

        # Transcript — use plain print() so [Speaker N] labels are never
        # interpreted as Rich markup and output always reaches the terminal.
        if result.transcript:
            print(result.transcript.strip())
        else:
            print("(no transcript in response)")

        # Summary (if present in full_result)
        if result.full_result:
            summary = extract_summary(result.full_result)
            if summary:
                out.print()
                out.print("[green]Summary:[/green]")
                print(f"  {summary}")

            topics_list = extract_topics(result.full_result)
            if topics_list:
                out.print()
                out.print("[green]Topics:[/green]")
                for t in topics_list:
                    out.print(f"  • {t}")

            sentiment = extract_sentiment(result.full_result)
            if sentiment:
                out.print()
                out.print("[green]Sentiment:[/green]")
                print(f"  {sentiment}")

    # ── Utilities ──────────────────────────────────────────────────────

    def _print_probe_table(self, probe_result: Any) -> None:
        from deepctl_shared_utils.ffprobe_models import AudioProbeResult

        if not isinstance(probe_result, AudioProbeResult):
            return

        table = Table(title="Audio Analysis", show_header=False, padding=(0, 2))
        table.add_column("Property", style="cyan")
        table.add_column("Value")

        if probe_result.format:
            fmt = probe_result.format
            if fmt.format_name:
                table.add_row("Format", fmt.format_name)
            if fmt.duration is not None:
                m, s = divmod(fmt.duration, 60)
                table.add_row("Duration", f"{int(m)}:{s:05.2f}")
            if fmt.size is not None:
                table.add_row("Size", f"{fmt.size / 1_048_576:.2f} MB")
            if fmt.bit_rate:
                table.add_row("Bit Rate", f"{int(fmt.bit_rate) // 1000} kbps")

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

        status.print(table)
        status.print()

    def _save_transcript(self, transcript: str, file_path: str) -> None:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(transcript, encoding="utf-8")
