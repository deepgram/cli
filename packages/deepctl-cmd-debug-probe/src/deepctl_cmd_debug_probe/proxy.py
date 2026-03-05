"""WebSocket proxy with periodic ffprobe analysis."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import TYPE_CHECKING

import aiohttp
from aiohttp import web
from deepctl_shared_utils import probe_buffer
from rich.console import Console

from .models import ProbeConnectionStats, ProbeSnapshot

if TYPE_CHECKING:
    from deepctl_core import Config

console = Console()


class ProbeProxy:
    """WebSocket proxy that probes audio at configurable intervals."""

    # Maximum audio buffer size (1 MB). Only the most recent bytes are kept
    # to prevent unbounded memory growth on long-running streams.
    MAX_BUFFER_BYTES = 1_048_576

    def __init__(
        self,
        api_key: str,
        config: Config | None = None,
        upstream_host: str = "api.deepgram.com",
        probe_interval_bytes: int = 131072,
        probe_interval_seconds: float = 10.0,
        verbose: bool = False,
    ) -> None:
        self.api_key = api_key
        self.config = config
        self.upstream_host = upstream_host
        self.probe_interval_bytes = probe_interval_bytes
        self.probe_interval_seconds = probe_interval_seconds
        self.verbose = verbose
        self.connections: list[ProbeConnectionStats] = []

    def _detect_stream_type(self, path: str) -> str:
        """Detect stream type from the request path."""
        if "/v1/listen" in path:
            return "stt"
        elif "/v1/speak" in path:
            return "tts"
        elif "/agent" in path:
            return "agent"
        return "unknown"

    async def handle_connection(self, request: web.Request) -> web.WebSocketResponse:
        """Handle an incoming WebSocket connection."""
        ws_client = web.WebSocketResponse()
        await ws_client.prepare(request)

        conn_id = str(uuid.uuid4())[:8]
        path = request.path
        query_string = request.query_string
        stream_type = self._detect_stream_type(path)

        stats = ProbeConnectionStats(
            connection_id=conn_id,
            stream_type=stream_type,
            path=path,
        )
        self.connections.append(stats)

        upstream_url = f"wss://{self.upstream_host}{path}"
        if query_string:
            upstream_url = f"{upstream_url}?{query_string}"

        upstream_headers = {
            "Authorization": f"Token {self.api_key}",
        }

        console.print(
            f"\n[green]New connection[/green] [{conn_id}] "
            f"type={stream_type} path={path}"
        )

        start_time = time.time()
        last_probe_time = start_time
        last_probe_bytes = 0

        try:
            async with (
                aiohttp.ClientSession() as session,
                session.ws_connect(
                    upstream_url,
                    headers=upstream_headers,
                    timeout=aiohttp.ClientWSTimeout(ws_close=30.0),
                ) as ws_upstream,
            ):

                async def forward_client_to_upstream() -> None:
                    nonlocal last_probe_time, last_probe_bytes

                    async for msg in ws_client:
                        if msg.type == aiohttp.WSMsgType.BINARY:
                            stats.bytes_sent += len(msg.data)
                            stats.frames_sent += 1

                            # Keep only the tail of the buffer to bound memory
                            stats.audio_buffer += msg.data
                            if len(stats.audio_buffer) > self.MAX_BUFFER_BYTES:
                                stats.audio_buffer = stats.audio_buffer[
                                    -self.MAX_BUFFER_BYTES :
                                ]

                            await ws_upstream.send_bytes(msg.data)

                            if self.verbose:
                                console.print(
                                    f"  [dim]→ binary {len(msg.data)} bytes[/dim]"
                                )

                            # Check probe thresholds
                            now = time.time()
                            bytes_since = stats.bytes_sent - last_probe_bytes
                            time_since = now - last_probe_time

                            if (
                                bytes_since >= self.probe_interval_bytes
                                or time_since >= self.probe_interval_seconds
                            ):
                                self._run_probe_snapshot(stats, now)
                                last_probe_time = now
                                last_probe_bytes = stats.bytes_sent

                        elif msg.type == aiohttp.WSMsgType.TEXT:
                            stats.text_frames_sent += 1
                            await ws_upstream.send_str(msg.data)
                        elif msg.type in (
                            aiohttp.WSMsgType.CLOSE,
                            aiohttp.WSMsgType.ERROR,
                        ):
                            break

                async def forward_upstream_to_client() -> None:
                    async for msg in ws_upstream:
                        if ws_client.closed:
                            break

                        if msg.type == aiohttp.WSMsgType.BINARY:
                            stats.bytes_received += len(msg.data)
                            stats.frames_received += 1
                            await ws_client.send_bytes(msg.data)
                        elif msg.type == aiohttp.WSMsgType.TEXT:
                            stats.text_frames_received += 1
                            await ws_client.send_str(msg.data)

                            # Parse transcript from Deepgram response
                            self._parse_transcript(msg.data, stats)
                        elif msg.type in (
                            aiohttp.WSMsgType.CLOSE,
                            aiohttp.WSMsgType.ERROR,
                        ):
                            break

                # Run both forwarders concurrently. If one fails,
                # cancel the other instead of letting it dangle.
                task_c2u = asyncio.ensure_future(forward_client_to_upstream())
                task_u2c = asyncio.ensure_future(forward_upstream_to_client())
                try:
                    await asyncio.gather(task_c2u, task_u2c)
                except Exception:
                    pass
                finally:
                    for task in (task_c2u, task_u2c):
                        if not task.done():
                            task.cancel()
                    # Suppress CancelledError from the cancelled task
                    await asyncio.gather(task_c2u, task_u2c, return_exceptions=True)

        except aiohttp.ClientError as e:
            console.print(f"[red]Upstream connection failed[/red] [{conn_id}]: {e}")
        except Exception as e:
            console.print(f"[red]Connection error[/red] [{conn_id}]: {e}")
        finally:
            stats.duration_seconds = time.time() - start_time

            # Final probe
            if stats.audio_buffer:
                console.print(f"\n[bold]Final probe[/bold] [{conn_id}]:")
                self._run_probe_snapshot(stats, time.time())

            self._print_connection_summary(stats)

            if not ws_client.closed:
                await ws_client.close()

        return ws_client

    def _run_probe_snapshot(
        self, stats: ProbeConnectionStats, timestamp: float
    ) -> None:
        """Run a probe snapshot on the accumulated audio buffer."""
        result = probe_buffer(stats.audio_buffer, "probe", self.config)
        snapshot = ProbeSnapshot(
            timestamp=timestamp,
            bytes_at_probe=stats.bytes_sent,
            result=result,
        )
        stats.snapshots.append(snapshot)

        if result and result.streams:
            stream = result.streams[0]
            parts = []
            if stream.codec_name:
                parts.append(f"codec={stream.codec_name}")
            if stream.sample_rate:
                parts.append(f"rate={stream.sample_rate}Hz")
            if stream.channels is not None:
                parts.append(f"ch={stream.channels}")
            info = " ".join(parts)
            console.print(
                f"  [cyan]probe[/cyan] [{stats.connection_id}] "
                f"@{stats.bytes_sent:,}B: {info}"
            )
        elif result and result.format:
            console.print(
                f"  [cyan]probe[/cyan] [{stats.connection_id}] "
                f"@{stats.bytes_sent:,}B: format={result.format.format_name}"
            )
        else:
            console.print(
                f"  [dim]probe[/dim] [{stats.connection_id}] "
                f"@{stats.bytes_sent:,}B: could not determine format"
            )

    def _parse_transcript(self, text: str, stats: ProbeConnectionStats) -> None:
        """Parse Deepgram transcript from upstream TEXT frame."""
        try:
            data = json.loads(text)
            # Deepgram STT responses have channel.alternatives[].transcript
            channel = data.get("channel", {})
            alternatives = channel.get("alternatives", [])
            if alternatives:
                transcript = alternatives[0].get("transcript", "")
                is_final = data.get("is_final", False)
                if transcript and is_final:
                    stats.transcripts.append(transcript)
                    console.print(
                        f"  [green]transcript[/green] [{stats.connection_id}]: "
                        f"{transcript[:120]}"
                    )
        except (json.JSONDecodeError, AttributeError):
            pass

    def _print_connection_summary(self, stats: ProbeConnectionStats) -> None:
        """Print summary for a completed connection."""
        console.print(f"\n[blue]Connection closed[/blue] [{stats.connection_id}]")
        console.print(f"  Type: {stats.stream_type}")
        console.print(
            f"  Duration: {stats.duration_seconds:.1f}s"
            if stats.duration_seconds
            else "  Duration: <1s"
        )
        console.print(
            f"  Sent: {stats.bytes_sent:,} bytes "
            f"({stats.frames_sent} binary, "
            f"{stats.text_frames_sent} text frames)"
        )
        console.print(
            f"  Received: {stats.bytes_received:,} bytes "
            f"({stats.frames_received} binary, "
            f"{stats.text_frames_received} text frames)"
        )
        console.print(f"  Probe snapshots: {len(stats.snapshots)}")
        if stats.transcripts:
            console.print(f"  Transcripts received: {len(stats.transcripts)}")
