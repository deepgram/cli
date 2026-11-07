"""Tests for debug probe command and proxy."""

import asyncio
import json
import sys
from unittest.mock import Mock, patch

import pytest
from deepctl_cmd_debug_probe.command import ProbeCommand, _find_available_port
from deepctl_cmd_debug_probe.models import (
    ProbeConnectionStats,
    ProbeDebugResult,
    ProbeSnapshot,
)
from deepctl_cmd_debug_probe.proxy import ProbeProxy


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


IMPORT_CHECK = (
    "import sys, deepctl_core; before = set(sys.modules); "
    "import deepctl_cmd_debug_probe.command; "
    "bad = sorted(m for m in set(sys.modules) - before for f in "
    "('asyncio', 'ssl', 'websockets', 'aiohttp') if m == f or m.startswith(f + '.')); "
    "print(' '.join(bad))"
)


def _probe_through_redirect(location: str):
    """Proxy one client stream to an upstream that answers the upgrade with
    a redirect. Returns (origin requests, target requests, console text)."""
    import asyncio
    import io

    from rich.console import Console
    from websockets.asyncio.client import connect as ws_connect

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

        stubs = []
        for name, handler in (("origin", origin), ("target", target)):
            srv = await asyncio.start_server(handler, "127.0.0.1", 0)
            stubs.append(srv)
            ports[name] = srv.sockets[0].getsockname()[1]

        proxy = ProbeProxy(
            api_key="fa1ce0000000000000000000000000000000000d",
            upstream_base=f"ws://127.0.0.1:{ports['origin']}",
            upstream_headers={
                "Authorization": "Token fa1ce0000000000000000000000000000000000d"
            },
        )
        server = await proxy.start(0)
        port = next(iter(server.sockets)).getsockname()[1]
        try:
            async with ws_connect(f"ws://127.0.0.1:{port}/v1/listen?x=1") as ws:
                await ws.send(b"\0\0" * 800)
                try:
                    await asyncio.wait_for(ws.recv(), 5)
                except Exception:
                    pass
        finally:
            server.close()
            await server.wait_closed()
            for srv in stubs:
                srv.close()

    out = io.StringIO()
    with patch(
        "deepctl_cmd_debug_probe.proxy.console",
        Console(file=out, force_terminal=False, width=300, stderr=True),
    ):
        asyncio.run(run())
    return origin_reqs, target_reqs, out.getvalue()


class TestProbeUpstreamRedirect:
    """The upstream connect never follows a redirect."""

    @pytest.mark.parametrize(
        "location", ["http://{target}/second", "ws://{target}/second", "/second"]
    )
    def test_redirect_refused_and_nothing_reaches_the_target(self, location):
        origin, target, text = _probe_through_redirect(location)
        assert target == []
        assert len(origin) == 1
        assert b"Token fa1ce0" in origin[0]
        assert "Deepgram connection failed" in text
        assert "(network_unavailable)" in text
        assert "dg debug probe doesn't follow redirects, so no audio was sent." in (
            " ".join(text.split())
        )
        assert "fa1ce0" not in text


def test_command_module_adds_no_network_stack_on_import():
    """`dg debug probe --help` must not pay for websockets or asyncio."""
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-c", IMPORT_CHECK],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
    assert proc.stdout.strip() == ""


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _run_probe(**proxy_hooks):
    """Run ProbeCommand.handle with a fake key and ffprobe check. Each hook
    is a callable(proxy, loop, port) run when the proxy starts serving."""
    from deepctl_cmd_debug_probe import proxy as proxymod

    original = proxymod.ProbeProxy.run

    async def run(self, port):
        import asyncio

        loop = asyncio.get_running_loop()
        for hook in proxy_hooks.values():
            hook(self, loop, port)
        await original(self, port)

    auth = Mock()
    auth.get_api_key.return_value = "fa1ce0000000000000000000000000000000000d"
    # The Transport's host check: the default host passes.
    auth.check_base_url.return_value = "https://api.deepgram.com"
    with (
        patch("deepctl_cmd_debug_probe.command.require_ffprobe", return_value=True),
        patch.object(proxymod.ProbeProxy, "run", run),
    ):
        return ProbeCommand().handle(Mock(), auth, Mock(), port=_free_port())


class TestInterrupt:
    """Ctrl+C handling in handle(): POSIX signal handler, Windows fallback."""

    @pytest.mark.skipif(sys.platform == "win32", reason="os.kill of SIGINT ends the whole process on Windows")
    def test_sigint_on_the_loop_stops_and_restores_the_previous_handler(
        self, capsys
    ):
        import os
        import signal

        seen = []

        def custom(signum, frame):  # pragma: no cover - never called
            seen.append(signum)

        old = signal.signal(signal.SIGINT, custom)
        try:
            result = _run_probe(
                kill=lambda proxy, loop, port: loop.call_later(
                    0.3, os.kill, os.getpid(), signal.SIGINT
                )
            )
            assert signal.getsignal(signal.SIGINT) is custom
        finally:
            signal.signal(signal.SIGINT, old)
        assert result.status == "success"
        assert seen == []
        assert "Stopping: open connections can finish" in capsys.readouterr().err

    @pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="reads the SigIgn mask from /proc/self/status (Linux only)",
    )
    def test_ignored_sigint_stays_ignored(self):
        """A background job in a script starts with SIGINT ignored: kill -INT
        must not stop the proxy, and SIGINT stays ignored afterwards."""
        import json
        import subprocess
        import sys

        code = """
import json, os, signal, sys
sys.path[:0] = {path!r}
signal.signal(signal.SIGINT, signal.SIG_IGN)
from test_probe import _run_probe
def hooks(proxy, loop, port):
    loop.call_later(0.3, os.kill, os.getpid(), signal.SIGINT)
    loop.call_later(1.5, proxy._stop.set)
result = _run_probe(hooks=hooks)
ign = [l for l in open("/proc/self/status") if l.startswith("SigIgn")]
print(json.dumps({{
    "status": result.status,
    "ignored": signal.getsignal(signal.SIGINT) is signal.SIG_IGN,
    "sigign": ign[0].split()[1] if ign else None,
}}))
""".format(path=[str(__import__("pathlib").Path(__file__).parent)])
        proc = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        out = json.loads(proc.stdout.strip().splitlines()[-1])
        assert out["status"] == "success"
        assert out["ignored"] is True
        assert out["sigign"] is not None
        assert int(out["sigign"], 16) & (1 << (2 - 1))  # the SIGINT bit is still set
        assert "Stopping: open connections can finish" not in proc.stderr

    def test_keyboard_interrupt_path_first_and_second_press(self, capsys):
        """Where the loop can't take SIGINT (Windows), Ctrl+C raises
        KeyboardInterrupt from the loop: the first stops listening and the
        second aborts open connections."""
        import time

        from deepctl_cmd_debug_probe import command as cmdmod

        def press(*_args):
            raise KeyboardInterrupt

        calls = []

        def hooks(proxy, loop, port):
            original = proxy.abort

            def abort():
                calls.append("abort")
                original()

            proxy.abort = abort
            # keep a connection open so the first press can't finish at once
            loop.create_task(_hold_connection(proxy, port))
            loop.call_later(0.5, press)
            loop.call_later(0.8, press)

        with patch.object(
            cmdmod, "_handle_sigint", lambda loop, cb: (False, None)
        ):
            t0 = time.monotonic()
            result = _run_probe(hooks=hooks)
        assert time.monotonic() - t0 < 10
        assert result.status == "success"
        assert calls == ["abort"]
        assert "Stopping: open connections can finish" in capsys.readouterr().err


async def _hold_connection(proxy, port):
    """Open a connection through `proxy` whose upstream never answers the
    handshake, so shutdown waits for it until abort()."""
    import asyncio

    from websockets.asyncio.client import connect

    async def never(reader, writer):
        await reader.read()  # never answers; ends when the proxy hangs up
        writer.close()

    hang = await asyncio.start_server(never, "127.0.0.1", 0)
    proxy.upstream_base = f"ws://127.0.0.1:{hang.sockets[0].getsockname()[1]}"
    for _ in range(100):
        try:
            ws = await connect(f"ws://127.0.0.1:{port}/v1/listen")
            break
        except OSError:
            await asyncio.sleep(0.02)
    await ws.wait_closed()
    hang.close()


class TestProbeConnectionChecks:
    """0.4.1's Host and Origin cases, on the websockets proxy: checked in
    process_request, before the handshake and any upstream connection."""

    KEY = "fa1ce0000000000000000000000000000000000d"

    def _status(self, headers, port=3100):
        from websockets.datastructures import Headers
        from websockets.http11 import Request

        class Conn:
            local_address = ("127.0.0.1", port)

            def respond(self, status, text):
                assert text == "Forbidden.\n"
                return status

        if not any(k == "Host" for k, _ in headers):
            headers = [("Host", f"127.0.0.1:{port}"), *headers]
        request = Request(
            "/v1/listen?encoding=linear16",
            Headers([*headers, ("Upgrade", "websocket")]),
        )
        return ProbeProxy(api_key=self.KEY).process_request(Conn(), request)

    def test_foreign_origin_refused(self):
        assert self._status([("Origin", "https://evil.example")]) == 403

    def test_null_origin_refused(self):
        assert self._status([("Origin", "null")]) == 403

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
        assert self._status([("Origin", origin)]) is None

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
        assert self._status([("Origin", origin)]) == 403

    def test_two_origins_refused(self):
        headers = [("Origin", "http://localhost:5173"), ("Origin", "https://evil.example")]
        assert self._status(headers) == 403

    def test_no_origin_allowed(self):
        assert self._status([]) is None

    def test_localhost_host_allowed_any_case(self):
        assert self._status([("Host", "LocalHost:3100")]) is None

    @pytest.mark.parametrize(
        "host", ["evil.example:3100", "evil.example", "localhost", "127.0.0.1:1"]
    )
    def test_foreign_host_refused(self, host):
        assert self._status([("Host", host)]) == 403


# ── The upstream comes from the Transport ──

KEY = "fa1ce0000000000000000000000000000000000d"
_QUIET = __import__("logging").getLogger("test.probe.fake-upstream")
_QUIET.propagate = False
_QUIET.setLevel(__import__("logging").CRITICAL + 1)


async def _upstream(process_request, handler=None):
    """A websockets server on a free loopback port. A refusal must come from
    ``connection.respond()`` in ``process_request`` (websockets 13 runs the
    handler for a hand-built Response)."""
    from websockets.asyncio.server import serve

    async def echo(ws):
        async for msg in ws:
            await ws.send(msg)

    server = await serve(
        handler or echo,
        "127.0.0.1",
        0,
        process_request=process_request,
        logger=_QUIET,
    )
    return server, next(iter(server.sockets)).getsockname()[1]


def _proxy_to(port):
    proxy = ProbeProxy(
        api_key=KEY,
        upstream_base=f"http://127.0.0.1:{port}",
        upstream_headers={"Authorization": f"Token {KEY}"},
    )
    assert proxy.upstream_base == f"ws://127.0.0.1:{port}"
    return proxy


async def _client_until_closed(port, target="/v1/listen?encoding=linear16"):
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed, InvalidHandshake

    try:
        async with connect(f"ws://127.0.0.1:{port}{target}", open_timeout=5) as ws:
            await asyncio.wait_for(ws.recv(), 10)
    except (ConnectionClosed, InvalidHandshake, OSError, asyncio.TimeoutError):
        pass


def _run_proxy_against(process_request, *, connections=1):
    """Serve the fake upstream and the proxy on loopback, connect
    ``connections`` clients one after another, and run until the proxy stops
    itself or the clients are done. Returns (proxy, seen upstream auth)."""
    seen = []

    def record(connection, request):
        seen.append(request.headers.get("Authorization"))
        return process_request(connection, request)

    async def main():
        upstream, up_port = await _upstream(record)
        proxy = _proxy_to(up_port)
        port = _free_port()
        run = asyncio.ensure_future(proxy.run(port))
        await asyncio.sleep(0.1)
        for _ in range(connections):
            await _client_until_closed(port)
        await asyncio.sleep(0.3)
        proxy._stop.set()
        await asyncio.wait_for(run, 20)
        upstream.close()
        await upstream.wait_closed()
        return proxy

    return asyncio.run(main()), seen


def _refusing(status):
    def process_request(connection, request):
        return connection.respond(status, "{}")

    return process_request


@pytest.mark.parametrize(("status", "code"), [(401, "auth_invalid")])
def test_refused_key_stops_the_proxy_with_its_code(status, code):
    proxy, seen = _run_proxy_against(_refusing(status), connections=1)
    assert proxy.fatal is not None
    assert proxy.fatal.code == code
    assert seen == [f"Token {KEY}"]
    assert KEY not in proxy.fatal.error


@pytest.mark.parametrize("status", [400, 403, 404, 429, 500, 503])
def test_other_refusals_fail_only_that_connection(status, capsys):
    proxy, seen = _run_proxy_against(_refusing(status), connections=2)
    assert proxy.fatal is None  # the proxy kept serving
    assert seen == [f"Token {KEY}"] * 2
    assert len(proxy.connections) == 2
    err = capsys.readouterr().err
    assert err.count("Deepgram connection failed") == 2
    assert KEY not in err
    if status == 404:
        assert "/v1/listen" in err


def test_a_later_401_doesnt_stop_the_loop_again():
    """A second client refused after the first fatal 401 (here: while the
    server shuts down) must not stop the proxy a second time."""
    proxy, seen = _run_proxy_against(_refusing(401), connections=2)
    assert proxy.fatal is not None and proxy.fatal.code == "auth_invalid"
    assert 1 <= len(seen) <= 2


def test_fatal_stop_with_an_open_stream_is_fast(capsys):
    """A 401 while another stream is open stops within seconds, with
    no leftover tasks or loop exception-handler errors."""
    import time

    def process_request(connection, request):
        if "mode=401" in request.path:
            return connection.respond(401, "{}")
        return None

    errors: list[dict] = []
    marks: dict[str, float] = {}

    async def main():
        asyncio.get_running_loop().set_exception_handler(
            lambda _loop, ctx: errors.append(ctx)
        )
        upstream, up_port = await _upstream(process_request)
        proxy = _proxy_to(up_port)
        port = _free_port()
        run = asyncio.ensure_future(proxy.run(port))
        await asyncio.sleep(0.1)

        async def held():
            from websockets.asyncio.client import connect
            from websockets.exceptions import ConnectionClosed

            async with connect(f"ws://127.0.0.1:{port}/v1/listen") as ws:
                await ws.send("ping")
                await asyncio.wait_for(ws.recv(), 5)
                try:
                    await asyncio.wait_for(ws.recv(), 30)  # until the proxy closes it
                except ConnectionClosed:
                    pass

        holder = asyncio.ensure_future(held())
        await asyncio.sleep(0.5)
        marks["refused"] = time.monotonic()
        await _client_until_closed(port, "/v1/listen?mode=401")
        await asyncio.wait_for(run, 30)
        marks["stopped"] = time.monotonic()
        await asyncio.wait_for(holder, 5)
        upstream.close()
        await upstream.wait_closed()
        return proxy

    proxy = asyncio.run(main())
    assert proxy.fatal is not None and proxy.fatal.code == "auth_invalid"
    assert marks["stopped"] - marks["refused"] < 10
    assert errors == []


def test_open_timeout_fails_only_that_connection(monkeypatch, capsys):
    from deepctl_core import transport

    monkeypatch.setattr(transport, "WS_OPEN_TIMEOUT_S", 0.3)

    async def hang(connection, request):
        await asyncio.sleep(2)

    proxy, seen = _run_proxy_against(hang, connections=1)
    assert proxy.fatal is None
    assert "(timeout)" in capsys.readouterr().err


def test_default_upstream_is_the_hosts_default():
    from deepctl_core.hosts import DEFAULT_BASE_URL

    proxy = ProbeProxy(api_key=KEY)
    assert proxy.upstream_base == DEFAULT_BASE_URL.replace("https://", "wss://")
    assert proxy.upstream_headers == {}  # no key without the command's check


def test_refused_host_gets_no_key(monkeypatch, tmp_path):
    from deepctl_core import hosts, output
    from deepctl_core.auth import AuthManager
    from deepctl_core.config import Config

    monkeypatch.setattr(output, "is_ai_agent", lambda: True)
    monkeypatch.setattr(hosts, "_reported", set())
    monkeypatch.setenv("DEEPGRAM_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("DEEPGRAM_API_KEY", KEY)
    config = Config(config_path=str(tmp_path / "none.yaml"))
    built = []
    # handle() imports ProbeProxy from .proxy at call time, so the
    # seam is the proxy module's name.
    monkeypatch.setattr(
        "deepctl_cmd_debug_probe.proxy.ProbeProxy",
        lambda **kw: built.append(kw),
    )
    with patch("deepctl_cmd_debug_probe.command.require_ffprobe", return_value=True):
        with pytest.raises(hosts.CredentialHostError):
            ProbeCommand().handle(config, AuthManager(config), Mock(), port=None)
    assert built == []
