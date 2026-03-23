"""Listen (live speech-to-text) command for deepctl."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from typing import Any

from deepctl_core import (
    AuthManager,
    BaseCommand,
    BaseResult,
    Config,
    DeepgramClient,
)
from rich.console import Console

from .models import ListenResult

console = Console(stderr=True)


class ListenCommand(BaseCommand):
    """Command for live speech-to-text transcription."""

    name = "listen"
    help = "Live speech-to-text transcription"
    short_help = "Live transcription"

    requires_auth = True
    requires_project = False
    ci_friendly = True

    examples = [
        "dg listen --mic",
        "dg listen --mic --model nova-3 --language en-US",
        "dg listen --mic --interim",
        "cat audio.raw | dg listen --encoding linear16 --sample-rate 16000",
        "ffmpeg -i audio.mp3 -f s16le -ar 16000 -ac 1 - | dg listen --encoding linear16 --sample-rate 16000",
    ]
    agent_help = (
        "Live speech-to-text transcription using Deepgram's streaming API. "
        "Use --mic for microphone input (requires sounddevice package) "
        "or pipe raw audio via stdin. Transcripts are printed to stdout."
    )

    def get_arguments(self) -> list[dict[str, Any]]:
        return [
            {
                "names": ["--mic"],
                "help": "Use microphone input (requires 'pip install deepctl-cmd-listen[mic]')",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--model", "-m"],
                "help": "STT model (default: nova-3)",
                "type": str,
                "is_option": True,
                "default": "nova-3",
            },
            {
                "names": ["--language", "-l"],
                "help": "Language code (default: en-US)",
                "type": str,
                "is_option": True,
                "default": "en-US",
            },
            {
                "names": ["--encoding"],
                "help": "Audio encoding for stdin input (e.g., linear16, mulaw)",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--sample-rate"],
                "help": "Audio sample rate in Hz for stdin input (default: 16000)",
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
            {
                "names": ["--interim"],
                "help": "Show interim (partial) results",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--punctuate"],
                "help": "Enable punctuation (default: true)",
                "is_flag": True,
                "is_option": True,
                "default": True,
            },
            {
                "names": ["--smart-format"],
                "help": "Enable smart formatting (default: true)",
                "is_flag": True,
                "is_option": True,
                "default": True,
            },
        ]

    def handle(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> BaseResult:
        use_mic = kwargs.get("mic", False)
        model = kwargs.get("model") or "nova-3"
        language = kwargs.get("language") or "en-US"
        encoding = kwargs.get("encoding")
        sample_rate = kwargs.get("sample_rate") or 16000
        channels = kwargs.get("channels") or 1
        interim = kwargs.get("interim", False)
        punctuate = kwargs.get("punctuate", True)
        smart_format = kwargs.get("smart_format", True)

        if use_mic:
            return self._listen_mic(
                client,
                model,
                language,
                sample_rate,
                channels,
                interim,
                punctuate,
                smart_format,
            )
        elif not sys.stdin.isatty():
            return self._listen_stdin(
                client,
                model,
                language,
                encoding,
                sample_rate,
                channels,
                interim,
                punctuate,
                smart_format,
            )
        else:
            return BaseResult(
                status="error",
                message=(
                    "No audio source. Use --mic for microphone or pipe audio via stdin.\n"
                    "  Example: dg listen --mic\n"
                    "  Example: cat audio.raw | dg listen --encoding linear16 --sample-rate 16000"
                ),
            )

    def _listen_mic(
        self,
        client: DeepgramClient,
        model: str,
        language: str,
        sample_rate: int,
        channels: int,
        interim: bool,
        punctuate: bool,
        smart_format: bool,
    ) -> BaseResult:
        try:
            import sounddevice  # noqa: F401
        except ImportError:
            return BaseResult(
                status="error",
                message=(
                    "Microphone support requires sounddevice. "
                    "Install with: pip install 'deepctl-cmd-listen[mic]'"
                ),
            )

        console.print("[blue]Listening from microphone... Press Ctrl+C to stop.[/blue]")

        try:
            result = asyncio.run(
                self._stream_mic(
                    client,
                    model,
                    language,
                    sample_rate,
                    channels,
                    interim,
                    punctuate,
                    smart_format,
                )
            )
            return result
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopped listening.[/yellow]")
            return ListenResult(status="success", source="mic")

    async def _stream_mic(
        self,
        client: DeepgramClient,
        model: str,
        language: str,
        sample_rate: int,
        channels: int,
        interim: bool,
        punctuate: bool,
        smart_format: bool,
    ) -> ListenResult:
        import numpy as np
        import sounddevice as sd

        api_key = client.auth_manager.get_api_key()
        import websockets

        url = f"wss://api.deepgram.com/v1/listen?model={model}&language={language}&punctuate={str(punctuate).lower()}&smart_format={str(smart_format).lower()}&encoding=linear16&sample_rate={sample_rate}&channels={channels}"
        if interim:
            url += "&interim_results=true"

        full_transcript: list[str] = []

        async with websockets.connect(
            url, additional_headers={"Authorization": f"Token {api_key}"}
        ) as ws:
            stop_event = threading.Event()

            async def send_audio() -> None:
                loop = asyncio.get_event_loop()
                q: asyncio.Queue[bytes] = asyncio.Queue()

                def audio_callback(
                    indata: Any, frames: int, time_info: Any, status: Any
                ) -> None:
                    if status:
                        console.print(f"[yellow]Audio: {status}[/yellow]")
                    loop.call_soon_threadsafe(q.put_nowait, bytes(indata))

                stream = sd.RawInputStream(
                    samplerate=sample_rate,
                    channels=channels,
                    dtype=np.int16,
                    callback=audio_callback,
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

            async def receive_transcripts() -> None:
                try:
                    async for msg in ws:
                        data = json.loads(msg)
                        if data.get("type") == "Results":
                            channel = data.get("channel", {})
                            alternatives = channel.get("alternatives", [])
                            if alternatives:
                                transcript = alternatives[0].get("transcript", "")
                                is_final = data.get("is_final", False)
                                if transcript:
                                    if is_final:
                                        full_transcript.append(transcript)
                                        print(transcript, flush=True)
                                    elif interim:
                                        print(f"\r{transcript}", end="", flush=True)
                except Exception:
                    pass

            send_task = asyncio.create_task(send_audio())
            recv_task = asyncio.create_task(receive_transcripts())

            try:
                await asyncio.gather(send_task, recv_task)
            except (KeyboardInterrupt, asyncio.CancelledError):
                stop_event.set()
                send_task.cancel()
                recv_task.cancel()

        return ListenResult(
            status="success",
            transcript=" ".join(full_transcript),
            source="mic",
        )

    def _listen_stdin(
        self,
        client: DeepgramClient,
        model: str,
        language: str,
        encoding: str | None,
        sample_rate: int,
        channels: int,
        interim: bool,
        punctuate: bool,
        smart_format: bool,
    ) -> BaseResult:
        if not encoding:
            console.print(
                "[yellow]Warning: No --encoding specified for stdin. "
                "Assuming linear16. Specify with --encoding.[/yellow]"
            )
            encoding = "linear16"

        console.print(
            "[blue]Reading audio from stdin... Send EOF (Ctrl+D) to finish.[/blue]"
        )

        try:
            result = asyncio.run(
                self._stream_stdin(
                    client,
                    model,
                    language,
                    encoding,
                    sample_rate,
                    channels,
                    interim,
                    punctuate,
                    smart_format,
                )
            )
            return result
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopped listening.[/yellow]")
            return ListenResult(status="success", source="stdin")

    async def _stream_stdin(
        self,
        client: DeepgramClient,
        model: str,
        language: str,
        encoding: str,
        sample_rate: int,
        channels: int,
        interim: bool,
        punctuate: bool,
        smart_format: bool,
    ) -> ListenResult:
        import websockets

        api_key = client.auth_manager.get_api_key()
        url = f"wss://api.deepgram.com/v1/listen?model={model}&language={language}&punctuate={str(punctuate).lower()}&smart_format={str(smart_format).lower()}&encoding={encoding}&sample_rate={sample_rate}&channels={channels}"
        if interim:
            url += "&interim_results=true"

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

            async def receive_transcripts() -> None:
                try:
                    async for msg in ws:
                        data = json.loads(msg)
                        if data.get("type") == "Results":
                            channel = data.get("channel", {})
                            alternatives = channel.get("alternatives", [])
                            if alternatives:
                                transcript = alternatives[0].get("transcript", "")
                                is_final = data.get("is_final", False)
                                if transcript:
                                    if is_final:
                                        full_transcript.append(transcript)
                                        print(transcript, flush=True)
                                    elif interim:
                                        print(f"\r{transcript}", end="", flush=True)
                except Exception:
                    pass

            await asyncio.gather(send_audio(), receive_transcripts())

        return ListenResult(
            status="success",
            transcript=" ".join(full_transcript),
            source="stdin",
        )
