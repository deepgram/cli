"""Tests for debug probe command and proxy."""

import asyncio
import io
import json
from unittest.mock import Mock, patch

import aiohttp
import pytest
from aiohttp import web
from deepctl_cmd_debug_probe.command import ProbeCommand, _find_available_port
from deepctl_cmd_debug_probe.models import (
    ProbeConnectionStats,
    ProbeDebugResult,
    ProbeSnapshot,
)
from deepctl_cmd_debug_probe.proxy import ProbeProxy
from rich.console import Console


class TestModels:
    """Test probe data models."""

    def test_probe_snapshot_defaults(self):
        snap = ProbeSnapshot()
        assert snap.timestamp == 0.0
        assert snap.bytes_at_probe == 0
        assert snap.result is None

    def test_probe_connection_stats_defaults(self):
        stats = ProbeConnectionStats(connection_id="abc123")
        assert stats.connection_id == "abc123"
        assert stats.stream_type == "unknown"
        assert stats.bytes_sent == 0
        assert stats.snapshots == []
        assert stats.transcripts == []
        assert stats.audio_buffer == b""

    def test_probe_connection_stats_buffer_excluded(self):
        stats = ProbeConnectionStats(
            connection_id="test",
            audio_buffer=b"\x00" * 100,
        )
        data = stats.model_dump()
        assert "audio_buffer" not in data

    def test_probe_debug_result(self):
        result = ProbeDebugResult(
            status="success",
            message="done",
            port=3100,
            upstream_host="api.deepgram.com",
        )
        assert result.port == 3100
        assert result.connections == []


class TestProbeProxy:
    """Test ProbeProxy."""

    def test_detect_stream_type_stt(self):
        proxy = ProbeProxy(api_key="test-key")
        assert proxy._detect_stream_type("/v1/listen") == "stt"

    def test_detect_stream_type_tts(self):
        proxy = ProbeProxy(api_key="test-key")
        assert proxy._detect_stream_type("/v1/speak") == "tts"

    def test_detect_stream_type_agent(self):
        proxy = ProbeProxy(api_key="test-key")
        assert proxy._detect_stream_type("/agent") == "agent"

    def test_detect_stream_type_unknown(self):
        proxy = ProbeProxy(api_key="test-key")
        assert proxy._detect_stream_type("/other") == "unknown"

    def test_parse_transcript_valid(self):
        proxy = ProbeProxy(api_key="test-key")
        stats = ProbeConnectionStats(connection_id="test")

        data = {
            "channel": {
                "alternatives": [{"transcript": "hello world", "confidence": 0.99}]
            },
            "is_final": True,
        }

        proxy._parse_transcript(json.dumps(data), stats)
        assert len(stats.transcripts) == 1
        assert stats.transcripts[0] == "hello world"

    def test_parse_transcript_not_final(self):
        proxy = ProbeProxy(api_key="test-key")
        stats = ProbeConnectionStats(connection_id="test")

        data = {
            "channel": {"alternatives": [{"transcript": "hello"}]},
            "is_final": False,
        }

        proxy._parse_transcript(json.dumps(data), stats)
        assert len(stats.transcripts) == 0

    def test_parse_transcript_invalid_json(self):
        proxy = ProbeProxy(api_key="test-key")
        stats = ProbeConnectionStats(connection_id="test")

        proxy._parse_transcript("not json", stats)
        assert len(stats.transcripts) == 0

    def test_parse_transcript_empty(self):
        proxy = ProbeProxy(api_key="test-key")
        stats = ProbeConnectionStats(connection_id="test")

        data = {
            "channel": {"alternatives": [{"transcript": ""}]},
            "is_final": True,
        }

        proxy._parse_transcript(json.dumps(data), stats)
        assert len(stats.transcripts) == 0


class TestProbeCommand:
    """Test ProbeCommand."""

    def setup_method(self):
        self.cmd = ProbeCommand()
        self.config = Mock()
        self.auth = Mock()
        self.client = Mock()

    def test_command_metadata(self):
        assert self.cmd.name == "probe"
        assert self.cmd.requires_auth is True
        assert self.cmd.ci_friendly is False

    def test_get_arguments(self):
        args = self.cmd.get_arguments()
        names = []
        for a in args:
            n = a.get("names", [a.get("name")])
            names.append(n[0])
        assert "--port" in names
        assert "--probe-interval-bytes" in names
        assert "--probe-interval-seconds" in names
        assert "--verbose" in names

    @patch("deepctl_cmd_debug_probe.command.require_ffprobe")
    def test_handle_no_ffprobe(self, mock_require):
        mock_require.return_value = False

        result = self.cmd.handle(self.config, self.auth, self.client)

        assert result.status == "error"
        assert "ffprobe" in result.message

    @patch("deepctl_cmd_debug_probe.command.require_ffprobe")
    def test_handle_no_api_key(self, mock_require):
        mock_require.return_value = True
        self.auth.get_api_key.return_value = None

        result = self.cmd.handle(self.config, self.auth, self.client)

        assert result.status == "error"
        assert "API key" in result.message


class TestFindAvailablePort:
    """Test port finding utility."""

    @patch("socket.socket")
    def test_finds_first_available(self, mock_socket_cls):
        mock_socket = Mock()
        mock_socket.__enter__ = Mock(return_value=mock_socket)
        mock_socket.__exit__ = Mock(return_value=False)
        mock_socket_cls.return_value = mock_socket

        port = _find_available_port(3100, 3100)
        assert port == 3100

    @patch("socket.socket")
    def test_all_ports_taken(self, mock_socket_cls):
        mock_socket = Mock()
        mock_socket.__enter__ = Mock(return_value=mock_socket)
        mock_socket.__exit__ = Mock(return_value=False)
        mock_socket.bind.side_effect = OSError("Address already in use")
        mock_socket_cls.return_value = mock_socket

        port = _find_available_port(3100, 3100)
        assert port is None


_REAL_SESSION = aiohttp.ClientSession


class _PlainUpstream:
    """A real aiohttp session whose ws_connect goes over plain http to a
    loopback stub instead of wss. It takes the same arguments as
    ClientSession, so the proxy's trace hook is installed as usual."""

    def __init__(self, *args, **kwargs):
        self._session = _REAL_SESSION(*args, **kwargs)

    async def __aenter__(self):
        await self._session.__aenter__()
        return self

    async def __aexit__(self, *exc):
        return await self._session.__aexit__(*exc)

    def ws_connect(self, url, **kwargs):
        return self._session.ws_connect(url.replace("wss://", "http://", 1), **kwargs)


def _probe_through_redirect(location: str):
    """Proxy one client stream to an upstream that answers the upgrade with
    a redirect. Returns (origin requests, target requests, console text)."""
    origin_reqs: list[bytes] = []
    target_reqs: list[bytes] = []

    async def read_head(reader):
        try:
            return await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError):
            return b""

    async def run():
        ports = {}

        async def origin(reader, writer):
            origin_reqs.append(await read_head(reader))
            loc = location.format(target=f"127.0.0.1:{ports['target']}")
            writer.write(
                f"HTTP/1.1 302 Found\r\nLocation: {loc}\r\n"
                "Content-Length: 0\r\nConnection: close\r\n\r\n".encode()
            )
            await writer.drain()
            writer.close()

        async def target(reader, writer):
            target_reqs.append(await read_head(reader) or b"<conn>")
            writer.close()

        for name, handler in (("origin", origin), ("target", target)):
            srv = await asyncio.start_server(handler, "127.0.0.1", 0)
            ports[name] = srv.sockets[0].getsockname()[1]

        proxy = ProbeProxy(
            api_key="fa1ce0000000000000000000000000000000000d",
            upstream_host=f"127.0.0.1:{ports['origin']}",
        )
        app = web.Application()
        app.router.add_route("GET", "/{path:.*}", proxy.handle_connection)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        try:
            async with _REAL_SESSION() as client:
                ws = await client.ws_connect(f"http://127.0.0.1:{port}/v1/listen?x=1")
                await ws.send_bytes(b"\0\0" * 800)
                await asyncio.wait_for(ws.receive(), 5)
                await ws.close()
        finally:
            await runner.cleanup()

    out = io.StringIO()
    with (
        patch("deepctl_cmd_debug_probe.proxy.aiohttp.ClientSession", _PlainUpstream),
        patch(
            "deepctl_cmd_debug_probe.proxy.console",
            Console(file=out, force_terminal=False, width=300),
        ),
    ):
        asyncio.run(run())
    return origin_reqs, target_reqs, out.getvalue()


class TestProbeUpstreamRedirect:
    """The upstream connect never follows a redirect."""

    # aiohttp follows http(s) Locations itself; ws:// ones it rejects.
    @pytest.mark.parametrize(
        "location", ["http://{target}/second", "ws://{target}/second", "/second"]
    )
    def test_redirect_refused_and_nothing_reaches_the_target(self, location):
        origin, target, text = _probe_through_redirect(location)
        assert target == []
        assert len(origin) == 1
        assert "Upstream connection refused" in text
        assert "dg debug probe doesn't follow redirects, so no audio was sent." in (
            " ".join(text.split())
        )
        assert "fa1ce0" not in text


class _CountingSession:
    """Stands in for aiohttp.ClientSession; counts upstream connect attempts."""

    connects = 0

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def ws_connect(self, *args, **kwargs):
        type(self).connects += 1
        raise aiohttp.ClientError("no upstream in tests")


def _handshake(headers: list[tuple[str, str]] | None, host: str | None = None):
    """Start a real proxy on 127.0.0.1, send one upgrade request, return
    (status line, response bytes, upstream connects, console text).

    host is a format string; {port} becomes the listener's port.
    """

    async def run():
        proxy = ProbeProxy(api_key="fa1ce0000000000000000000000000000000000d")
        app = web.Application()
        app.router.add_route("GET", "/{path:.*}", proxy.handle_connection)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            lines = [
                "GET /v1/listen?encoding=linear16 HTTP/1.1",
                "Host: " + (host or "127.0.0.1:{port}").format(port=port),
                "Upgrade: websocket",
                "Connection: Upgrade",
                "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==",
                "Sec-WebSocket-Version: 13",
            ]
            lines += [f"{k}: {v}" for k, v in (headers or [])]
            writer.write(("\r\n".join(lines) + "\r\n\r\n").encode())
            await writer.drain()
            data = await asyncio.wait_for(reader.read(4096), 5)
            # Let an accepted handler reach the upstream connect.
            for _ in range(50):
                if _CountingSession.connects or not data.startswith(b"HTTP/1.1 101"):
                    break
                await asyncio.sleep(0.01)
            writer.close()
            return data
        finally:
            await runner.cleanup()

    _CountingSession.connects = 0
    out = io.StringIO()
    with (
        patch("deepctl_cmd_debug_probe.proxy.aiohttp.ClientSession", _CountingSession),
        patch(
            "deepctl_cmd_debug_probe.proxy.console",
            Console(file=out, force_terminal=False, width=200),
        ),
    ):
        data = asyncio.run(run())
    status = data.split(b"\r\n", 1)[0].decode()
    return status, data, _CountingSession.connects, out.getvalue()


class TestProbeConnectionChecks:
    """Only local pages and native clients may use the proxy."""

    def test_foreign_origin_refused_before_upstream(self):
        status, data, connects, text = _handshake([("Origin", "https://evil.example")])
        assert status == "HTTP/1.1 403 Forbidden"
        assert connects == 0
        assert b"evil" not in data
        assert "[conn] refused: origin not allowed" in text
        assert "evil" not in text

    def test_null_origin_refused(self):
        status, _, connects, _ = _handshake([("Origin", "null")])
        assert status == "HTTP/1.1 403 Forbidden"
        assert connects == 0

    @pytest.mark.parametrize(
        "origin",
        [
            "http://localhost:5173",
            "https://127.0.0.1",
            "http://[::1]:8080",
            "HTTP://LOCALHOST:3000",
        ],
    )
    def test_loopback_origin_allowed(self, origin):
        status, _, connects, text = _handshake([("Origin", origin)])
        assert status == "HTTP/1.1 101 Switching Protocols"
        assert connects == 1
        assert "refused" not in text

    @pytest.mark.parametrize(
        "origin",
        [
            "http://localhost.evil.example",
            "http://127.0.0.1.evil.example:80",
            "http://evil.example#http://localhost",
            "file://",
            "",
        ],
    )
    def test_lookalike_origin_refused(self, origin):
        status, _, connects, _ = _handshake([("Origin", origin)])
        assert status == "HTTP/1.1 403 Forbidden"
        assert connects == 0

    def test_two_origins_refused(self):
        status, _, connects, _ = _handshake(
            [("Origin", "http://localhost:5173"), ("Origin", "https://evil.example")]
        )
        assert status == "HTTP/1.1 403 Forbidden"
        assert connects == 0

    def test_no_origin_allowed(self):
        status, _, connects, _ = _handshake([])
        assert status == "HTTP/1.1 101 Switching Protocols"
        assert connects == 1

    def test_localhost_host_allowed_any_case(self):
        status, _, connects, _ = _handshake([], host="LocalHost:{port}")
        assert status == "HTTP/1.1 101 Switching Protocols"
        assert connects == 1

    @pytest.mark.parametrize(
        "host", ["evil.example:3100", "evil.example", "localhost", "127.0.0.1:1"]
    )
    def test_foreign_host_refused(self, host):
        status, data, connects, text = _handshake([], host=host)
        assert status == "HTTP/1.1 403 Forbidden"
        assert connects == 0
        assert b"evil" not in data
        assert "[conn] refused: host not allowed" in text
