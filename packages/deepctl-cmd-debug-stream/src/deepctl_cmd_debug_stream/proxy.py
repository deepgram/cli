"""WebSocket proxy for stream debug."""

import asyncio
import time
import uuid

import aiohttp
from aiohttp import web
from rich.console import Console

from .analyzer import AudioAnalyzer
from .models import AudioFormatReport, ConnectionStats

console = Console()


class WebSocketProxy:
    """WebSocket proxy that intercepts and analyzes audio streams."""

    def __init__(
        self,
        api_key: str,
        upstream_host: str = "api.deepgram.com",
        sample_size: int = 65536,
        no_analysis: bool = False,
        verbose: bool = False,
    ) -> None:
        self.api_key = api_key
        self.upstream_host = upstream_host
        self.sample_size = sample_size
        self.no_analysis = no_analysis
        self.verbose = verbose
        self.connections: list[ConnectionStats] = []

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

        stats = ConnectionStats(
            connection_id=conn_id,
            stream_type=stream_type,
            path=path,
        )
        self.connections.append(stats)

        # Build upstream URL
        upstream_url = f"wss://{self.upstream_host}{path}"
        if query_string:
            upstream_url = f"{upstream_url}?{query_string}"

        # Build upstream headers with auth
        upstream_headers = {
            "Authorization": f"Token {self.api_key}",
        }

        console.print(
            f"\n[green]New connection[/green] [{conn_id}] "
            f"type={stream_type} path={path}"
        )

        start_time = time.time()

        try:
            async with (
                aiohttp.ClientSession() as session,
                session.ws_connect(
                    upstream_url,
                    headers=upstream_headers,
                    timeout=aiohttp.ClientWSTimeout(ws_close=30.0),
                ) as ws_upstream,
            ):
                    # Run bidirectional forwarding
                    await asyncio.gather(
                        self._forward_client_to_upstream(
                            ws_client, ws_upstream, stats
                        ),
                        self._forward_upstream_to_client(
                            ws_upstream, ws_client, stats
                        ),
                        return_exceptions=True,
                    )

        except aiohttp.ClientError as e:
            console.print(
                f"[red]Upstream connection failed[/red] [{conn_id}]: {e}"
            )
        except Exception as e:
            console.print(
                f"[red]Connection error[/red] [{conn_id}]: {e}"
            )
        finally:
            stats.duration_seconds = time.time() - start_time
            self._print_connection_summary(stats)

            if not self.no_analysis:
                self._run_analysis(stats)

            if not ws_client.closed:
                await ws_client.close()

        return ws_client

    async def _forward_client_to_upstream(
        self,
        ws_client: web.WebSocketResponse,
        ws_upstream: aiohttp.ClientWebSocketResponse,
        stats: ConnectionStats,
    ) -> None:
        """Forward frames from client to upstream."""
        async for msg in ws_client:
            if msg.type == aiohttp.WSMsgType.BINARY:
                stats.bytes_sent += len(msg.data)
                stats.frames_sent += 1

                # Sample audio
                if len(stats.sent_audio_buffer) < self.sample_size:
                    remaining = self.sample_size - len(stats.sent_audio_buffer)
                    stats.sent_audio_buffer += msg.data[:remaining]

                await ws_upstream.send_bytes(msg.data)

                if self.verbose:
                    console.print(
                        f"  [dim]→ binary {len(msg.data)} bytes[/dim]"
                    )

            elif msg.type == aiohttp.WSMsgType.TEXT:
                stats.text_frames_sent += 1
                await ws_upstream.send_str(msg.data)

                if self.verbose:
                    console.print(f"  [dim]→ text: {msg.data[:100]}[/dim]")

            elif msg.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.ERROR,
            ):
                break

    async def _forward_upstream_to_client(
        self,
        ws_upstream: aiohttp.ClientWebSocketResponse,
        ws_client: web.WebSocketResponse,
        stats: ConnectionStats,
    ) -> None:
        """Forward frames from upstream to client."""
        async for msg in ws_upstream:
            if ws_client.closed:
                break

            if msg.type == aiohttp.WSMsgType.BINARY:
                stats.bytes_received += len(msg.data)
                stats.frames_received += 1

                # Sample audio
                if len(stats.received_audio_buffer) < self.sample_size:
                    remaining = self.sample_size - len(
                        stats.received_audio_buffer
                    )
                    stats.received_audio_buffer += msg.data[:remaining]

                await ws_client.send_bytes(msg.data)

                if self.verbose:
                    console.print(
                        f"  [dim]← binary {len(msg.data)} bytes[/dim]"
                    )

            elif msg.type == aiohttp.WSMsgType.TEXT:
                stats.text_frames_received += 1
                await ws_client.send_str(msg.data)

                if self.verbose:
                    console.print(f"  [dim]← text: {msg.data[:100]}[/dim]")

            elif msg.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.ERROR,
            ):
                break

    def _print_connection_summary(self, stats: ConnectionStats) -> None:
        """Print summary for a completed connection."""
        console.print(
            f"\n[blue]Connection closed[/blue] [{stats.connection_id}]"
        )
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

    def _run_analysis(self, stats: ConnectionStats) -> None:
        """Run ffprobe analysis on sampled audio."""
        if not AudioAnalyzer.is_available():
            console.print(
                "\n[yellow]ffprobe not found — skipping audio analysis[/yellow]"
            )
            console.print(
                "[dim]Install ffmpeg for audio format detection: "
                "brew install ffmpeg[/dim]"
            )
            return

        if stats.sent_audio_buffer:
            console.print("\n[bold]Sent audio analysis:[/bold]")
            report = AudioAnalyzer.analyze_buffer(
                stats.sent_audio_buffer, "sent"
            )
            if report:
                stats.sent_audio_format = report
                self._print_audio_report(report)
            else:
                console.print(
                    "  [dim]Could not determine format "
                    "(may be raw PCM or unknown encoding)[/dim]"
                )

        if stats.received_audio_buffer:
            console.print("\n[bold]Received audio analysis:[/bold]")
            report = AudioAnalyzer.analyze_buffer(
                stats.received_audio_buffer, "received"
            )
            if report:
                stats.received_audio_format = report
                self._print_audio_report(report)
            else:
                console.print(
                    "  [dim]Could not determine format "
                    "(may be raw PCM or unknown encoding)[/dim]"
                )

    def _print_audio_report(self, report: AudioFormatReport) -> None:
        """Print an audio format report."""
        if report.codec:
            console.print(f"  Codec: {report.codec}")
        if report.format_name:
            console.print(f"  Format: {report.format_name}")
        if report.sample_rate:
            console.print(f"  Sample rate: {report.sample_rate} Hz")
        if report.channels:
            console.print(f"  Channels: {report.channels}")
        if report.bit_rate:
            console.print(f"  Bit rate: {report.bit_rate}")
        if report.duration:
            console.print(f"  Duration: {report.duration:.2f}s")
