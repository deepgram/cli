"""WebSocket proxy with periodic ffprobe analysis."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import tempfile
import time
import uuid
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from deepctl_core.websocket import RedirectRefused
from deepctl_shared_utils.ffprobe import (
    _parse_probe_data,  # lockstep sibling; same parsing as probe_buffer
    get_ffprobe_path,
)
from rich.console import Console
from rich.text import Text
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed, WebSocketException

from .access import readable, refusal_reason, untrusted
from .models import ProbeConnectionStats, ProbeSnapshot

if TYPE_CHECKING:
    from deepctl_core import Config
    from deepctl_shared_utils.ffprobe_models import AudioProbeResult
    from websockets.asyncio.client import ClientConnection
    from websockets.http11 import Request, Response

console = Console()

# Frames up to 4 MiB each way, the limit earlier releases allowed.
MAX_FRAME_BYTES = 4 * 2**20


def _quiet_logger(name: str) -> logging.Logger:
    """A logger that never writes headers (the upstream one carries the API
    key) anywhere, whatever the root logger's level or handlers."""
    logger = logging.getLogger(f"deepctl.debug.probe.{name}")
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    logger.propagate = False
    logger.setLevel(logging.CRITICAL + 1)
    return logger


def _refuse(connection: ServerConnection, status: int, text: str) -> Response:
    """A fixed plain-text response; nothing from the request is echoed.

    connection.respond() also marks the handshake as refused, so websockets
    never runs the handler, and never connects upstream, for it (13.x would
    for a hand-built Response).
    """
    return connection.respond(status, text)


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
        # Where connections go: wss:// plus the fixed host, then the client's
        # path and query. Tests point it at a loopback ws:// server.
        self._upstream_base = f"wss://{upstream_host}"
        self._stop = asyncio.Event()
        # Open client and upstream connections, and pending upstream
        # connects, so a second Ctrl+C can end them at once.
        self._live: set[Any] = set()
        self._connecting: set[asyncio.Future[Any]] = set()
        self._aborting = False
        self._ffprobes: set[asyncio.subprocess.Process] = set()
        self._reaping: set[asyncio.Future[None]] = set()

    def _detect_stream_type(self, path: str) -> str:
        """Detect stream type from the request path."""
        if "/v1/listen" in path:
            return "stt"
        elif "/v1/speak" in path:
            return "tts"
        elif "/agent" in path:
            return "agent"
        return "unknown"

    def process_request(
        self, connection: ServerConnection, request: Request
    ) -> Response | None:
        """Accept only a WebSocket upgrade from a local page or a native
        client, checked before the handshake and before any upstream
        connection: the Host must name this listener and an Origin, if sent,
        must be a page on this machine (see `access`).
        """
        # websockets < 17 closes the connection on other methods itself,
        # without a response.
        if getattr(request, "method", "GET") != "GET":
            return _refuse(connection, 405, "Method not allowed.\n")
        if not request.path.startswith("/"):
            return _refuse(connection, 400, "Bad request.\n")
        origins = request.headers.get_all("Origin")
        reason = refusal_reason(
            request.headers.get_all("Host"), origins, connection.local_address[1]
        )
        if reason == "origin":
            console.print(
                Text("Refused a connection from ", style="yellow"),
                untrusted(", ".join(origins)),
                Text(": only pages on this machine can connect.", style="yellow"),
                sep="",
            )
        if reason is not None:
            return _refuse(connection, 403, "Forbidden.\n")
        if "websocket" not in request.headers.get("Upgrade", "").lower():
            return _refuse(connection, 400, "Bad request.\n")
        return None

    async def _connect_upstream(
        self, url: str, headers: dict[str, str]
    ) -> ClientConnection:
        """Open the upstream WebSocket. The API key goes only here, and a
        redirect on the upgrade is refused (``RedirectRefused``), never
        followed."""
        from deepctl_core.websocket import open_async

        conn: ClientConnection = await open_async(
            url,
            headers,
            command="dg debug probe",
            payload="audio",
            open_timeout=10,
            close_timeout=30,
            max_size=MAX_FRAME_BYTES,
            compression=None,
            ping_interval=None,
            logger=_quiet_logger("upstream"),
        )
        return conn

    async def start(self, port: int) -> Server:
        """Listen on 127.0.0.1:`port` (0 for any free port)."""
        server: Server = await serve(
            self.handle_connection,
            "127.0.0.1",
            port,
            process_request=self.process_request,
            max_size=MAX_FRAME_BYTES,
            ping_interval=None,
            server_header=None,
            logger=_quiet_logger("server"),
        )
        return server

    def abort(self) -> None:
        """End every open connection now, without close handshakes.

        For a second Ctrl+C: running ffprobe snapshots are stopped and the
        final probe is skipped; each handler still prints its summary, and
        `run` returns once they have.
        """
        self._aborting = True
        self._stop.set()
        for task in list(self._connecting):
            task.cancel()
        for conn in list(self._live):
            conn.transport.abort()
        for proc in list(self._ffprobes):
            self._reaping.add(asyncio.ensure_future(self._stop_ffprobe(proc)))

    async def run(self, port: int) -> None:
        """Serve until `_stop` is set or this task is cancelled; then close
        the listener and wait for every connection's summary."""
        server = await self.start(port)
        try:
            await self._stop.wait()
        finally:
            server.close()
            await server.wait_closed()
            await asyncio.gather(*self._reaping)

    async def handle_connection(self, ws_client: ServerConnection) -> None:
        """Handle an incoming WebSocket connection."""
        conn_id = str(uuid.uuid4())[:8]
        target = ws_client.request.path  # type: ignore[union-attr]
        path = urlsplit(target).path
        stream_type = self._detect_stream_type(path)

        stats = ProbeConnectionStats(
            connection_id=conn_id,
            stream_type=stream_type,
            path=path,
        )
        self.connections.append(stats)

        upstream_url = self._upstream_base + target

        upstream_headers = {
            "Authorization": f"Token {self.api_key}",
        }

        console.print(
            Text.from_markup(
                f"\n[green]New connection[/green] [{conn_id}] type={stream_type} path="
            ),
            untrusted(path),
            sep="",
        )

        start_time = time.time()
        last_probe_time = start_time
        last_probe_bytes = 0

        ws_upstream: ClientConnection | None = None
        dropped = [0, 0]  # frames and bytes read but not relayed while stopping
        self._live.add(ws_client)
        try:
            connecting = asyncio.ensure_future(
                self._connect_upstream(upstream_url, upstream_headers)
            )
            self._connecting.add(connecting)
            try:
                ws_upstream = await connecting
            except RedirectRefused as e:
                console.print(
                    Text.from_markup(
                        f"[red]Upstream connection refused[/red] [{conn_id}]: "
                    ),
                    untrusted(e.message),
                    sep="",
                )
                return
            except (WebSocketException, OSError, asyncio.TimeoutError) as e:
                console.print(
                    Text.from_markup(
                        f"[red]Upstream connection failed[/red] [{conn_id}]: "
                    ),
                    untrusted(str(e)),
                    sep="",
                )
                return
            except asyncio.CancelledError:
                if not self._aborting:
                    raise
                return  # abort() during the upstream handshake
            finally:
                self._connecting.discard(connecting)

            self._live.add(ws_upstream)
            async with ws_upstream:

                async def forward_client_to_upstream() -> None:
                    nonlocal last_probe_time, last_probe_bytes

                    async for msg in ws_client:
                        if self._stop.is_set():
                            # Shutting down: read past what the client queued
                            # (so its close frame arrives) without relaying it
                            # or probing it snapshot by snapshot.
                            dropped[0] += 1
                            dropped[1] += len(msg)
                            continue
                        if isinstance(msg, bytes):
                            stats.bytes_sent += len(msg)
                            stats.frames_sent += 1

                            # Keep only the tail of the buffer to bound memory
                            stats.audio_buffer += msg
                            if len(stats.audio_buffer) > self.MAX_BUFFER_BYTES:
                                stats.audio_buffer = stats.audio_buffer[
                                    -self.MAX_BUFFER_BYTES :
                                ]

                            await ws_upstream.send(msg)

                            if self.verbose:
                                console.print(f"  [dim]→ binary {len(msg)} bytes[/dim]")

                            # Check probe thresholds
                            now = time.time()
                            bytes_since = stats.bytes_sent - last_probe_bytes
                            time_since = now - last_probe_time

                            if (
                                bytes_since >= self.probe_interval_bytes
                                or time_since >= self.probe_interval_seconds
                            ):
                                await self._run_probe_snapshot(stats, now)
                                last_probe_time = now
                                last_probe_bytes = stats.bytes_sent
                        else:
                            stats.text_frames_sent += 1
                            await ws_upstream.send(msg)

                async def forward_upstream_to_client() -> None:
                    async for msg in ws_upstream:
                        if isinstance(msg, bytes):
                            stats.bytes_received += len(msg)
                            stats.frames_received += 1
                            await ws_client.send(msg)
                        else:
                            stats.text_frames_received += 1
                            await ws_client.send(msg)

                            # Parse transcript from Deepgram response
                            self._parse_transcript(msg, stats)

                await self._relay(
                    forward_client_to_upstream(), forward_upstream_to_client()
                )

        except Exception as e:
            console.print(
                Text.from_markup(f"[red]Connection error[/red] [{conn_id}]: "),
                untrusted(str(e)),
                sep="",
            )
        finally:
            self._live.difference_update({ws_client, ws_upstream})
            stats.duration_seconds = time.time() - start_time

            # Final probe (not after a second Ctrl+C)
            if stats.audio_buffer and not self._aborting:
                console.print(f"\n[bold]Final probe[/bold] [{conn_id}]:")
                await self._run_probe_snapshot(stats, time.time())

            self._print_connection_summary(stats, *dropped)

            await ws_client.close(1000)

    async def _probe(self, audio: bytes) -> AudioProbeResult | None:
        """ffprobe the audio, as probe_buffer does, as an asyncio subprocess:
        other connections and Ctrl+C aren't held up, and abort() can stop it.
        """
        ffprobe_bin = get_ffprobe_path(self.config) if audio else None
        if not ffprobe_bin:
            return None
        tmp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".raw", delete=False, prefix="deepctl_probe_"
            ) as tmp_file:
                tmp_name = tmp_file.name
                tmp_file.write(audio)
            proc = await asyncio.create_subprocess_exec(
                ffprobe_bin,
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                tmp_name,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                # its own process group, so stopping it also stops anything
                # it started (a wrapper script's children hold stdout open)
                start_new_session=True,
            )
            self._ffprobes.add(proc)
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), 10)
            except asyncio.TimeoutError:
                await self._stop_ffprobe(proc)
                return None
            finally:
                self._ffprobes.discard(proc)
            if proc.returncode != 0:
                return None
            return _parse_probe_data(json.loads(out))
        except (json.JSONDecodeError, OSError):
            return None
        finally:
            if tmp_name:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)

    @staticmethod
    async def _stop_ffprobe(proc: asyncio.subprocess.Process) -> None:
        """SIGTERM to ffprobe's process group, up to 1 s to exit, then
        SIGKILL."""

        def send(sig: int) -> None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                if hasattr(os, "killpg"):
                    os.killpg(proc.pid, sig)
                else:  # Windows
                    proc.send_signal(sig)

        if proc.returncode is not None:
            return
        send(signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), 1)
        except asyncio.TimeoutError:
            send(getattr(signal, "SIGKILL", signal.SIGTERM))
            await proc.wait()

    @staticmethod
    async def _relay(*directions: Any) -> None:
        """Run both directions; when either ends (a close, an error, or the
        other side going away) cancel the other."""

        async def quietly(direction: Any) -> None:
            try:
                await direction
            except ConnectionClosed:
                pass

        tasks = [asyncio.ensure_future(quietly(d)) for d in directions]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            # Suppress CancelledError from the cancelled task
            results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                raise result

    async def _run_probe_snapshot(
        self, stats: ProbeConnectionStats, timestamp: float
    ) -> None:
        """Run a probe snapshot on the accumulated audio buffer."""
        result = await self._probe(stats.audio_buffer)
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
                        Text.from_markup(
                            f"  [green]transcript[/green] [{stats.connection_id}]: "
                        ),
                        readable(transcript[:120]),
                        sep="",
                    )
        except (json.JSONDecodeError, AttributeError):
            pass

    def _print_connection_summary(
        self, stats: ProbeConnectionStats, dropped: int = 0, dropped_bytes: int = 0
    ) -> None:
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
        if dropped:
            console.print(
                "  Not forwarded (arrived while stopping): "
                f"{dropped_bytes:,} {'byte' if dropped_bytes == 1 else 'bytes'} "
                f"({dropped} {'frame' if dropped == 1 else 'frames'})"
            )
        console.print(f"  Probe snapshots: {len(stats.snapshots)}")
        if stats.transcripts:
            console.print(f"  Transcripts received: {len(stats.transcripts)}")
