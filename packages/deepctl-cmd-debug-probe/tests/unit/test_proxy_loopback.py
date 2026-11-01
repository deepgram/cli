"""The probe proxy against real loopback servers: relay, refusals, run loop.

The upstream is a websockets server on a free 127.0.0.1 port, reached
through the proxy's `_upstream_base` seam. Nothing is mocked.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

import pytest
from deepctl_cmd_debug_probe.proxy import ProbeProxy
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

if TYPE_CHECKING:
    from websockets.datastructures import Headers
    from websockets.http11 import Response

KEY = "fa1ce0000000000000000000000000000000000d"
RESULTS = json.dumps(
    {
        "type": "Results",
        "is_final": True,
        "channel": {"alternatives": [{"transcript": "fake words"}]},
    }
)


_SILENT = logging.getLogger("test.fake-upstream")
_SILENT.propagate = False
_SILENT.setLevel(logging.CRITICAL + 1)


class FakeUpstream:
    """Records each handshake's headers and echoes every frame back."""

    def __init__(self, status: int = 101) -> None:
        self.status = status
        self.handshakes: list[Headers] = []
        self.paths: list[str] = []
        self.received: list[str | bytes] = []

    def process_request(self, connection: Any, request: Any) -> Response | None:
        self.handshakes.append(request.headers)
        self.paths.append(request.path)
        if self.status != 101:
            return connection.respond(self.status, "Unauthorized.\n")
        return None

    async def handler(self, ws: Any) -> None:
        await ws.send(RESULTS)
        async for msg in ws:
            self.received.append(msg)
            await ws.send(msg)

    async def __aenter__(self) -> FakeUpstream:
        self.server = await serve(
            self.handler,
            "127.0.0.1",
            0,
            process_request=self.process_request,
            max_size=8 * 2**20,
            # The fake's own handshake log would show the key it receives.
            logger=_SILENT,
        )
        self.port = next(iter(self.server.sockets)).getsockname()[1]
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.server.close()
        await self.server.wait_closed()


class Proxy:
    """A ProbeProxy listening on a free port, pointed at `upstream`."""

    def __init__(self, upstream: FakeUpstream) -> None:
        self.proxy = ProbeProxy(api_key=KEY, probe_interval_bytes=10**9)
        self.proxy._upstream_base = f"ws://127.0.0.1:{upstream.port}"

    async def __aenter__(self) -> Proxy:
        self.server = await self.proxy.start(0)
        self.port = next(iter(self.server.sockets)).getsockname()[1]
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.server.close()
        await self.server.wait_closed()


async def _status(port: int, request: str) -> tuple[int, bytes]:
    """Status and body; status 0 if the server closed without a response."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request.encode("latin-1"))
    await writer.drain()
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
    except asyncio.IncompleteReadError:
        writer.close()
        return 0, b""
    status = int(head.split()[1])
    body = b""
    if status != 101:
        body = await asyncio.wait_for(reader.read(), 10)
    writer.close()
    return status, body


def _upgrade(port: int, target: str = "/v1/listen", **headers: str) -> str:
    lines = [
        f"GET {target} HTTP/1.1",
        f"Host: {headers.pop('Host', f'127.0.0.1:{port}')}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==",
        "Sec-WebSocket-Version: 13",
        *(f"{k}: {v}" for k, v in headers.items()),
    ]
    return "\r\n".join(lines) + "\r\n\r\n"


def test_relay_preserves_frames_and_uses_only_the_proxy_key(capsys):
    frames: list[str | bytes] = [bytes(range(256)) * 32 for _ in range(5)]
    frames.insert(2, '{"type": "KeepAlive"}')
    frames.append(b"\x01\x02" * 2**20)  # one 2 MiB frame

    async def main() -> tuple[FakeUpstream, list[str | bytes], ProbeProxy]:
        async with FakeUpstream() as up, Proxy(up) as px:
            echoed: list[str | bytes] = []
            url = f"ws://127.0.0.1:{px.port}/v1/listen?encoding=linear16&x=%20y"
            async with connect(
                url,
                additional_headers={"Authorization": "Token client-own-key"},
                subprotocols=["token", "client-subprotocol-key"],  # type: ignore[list-item]
                max_size=8 * 2**20,
            ) as ws:
                assert await ws.recv() == RESULTS
                for frame in frames:
                    await ws.send(frame)
                    echoed.append(await ws.recv())
            for _ in range(100):
                if px.proxy.connections[0].duration_seconds is not None:
                    break
                await asyncio.sleep(0.02)
            return up, echoed, px.proxy

    up, echoed, proxy = asyncio.run(main())

    assert up.received == frames
    assert echoed == frames
    assert [type(f) for f in echoed] == [type(f) for f in frames]
    digest = hashlib.sha256(b"".join(f for f in frames if isinstance(f, bytes)))
    assert (
        digest.hexdigest()
        == hashlib.sha256(
            b"".join(f for f in up.received if isinstance(f, bytes))
        ).hexdigest()
    )

    (headers,) = up.handshakes
    assert headers.get_all("Authorization") == [f"Token {KEY}"]
    assert "Sec-WebSocket-Protocol" not in headers
    assert up.paths == ["/v1/listen?encoding=linear16&x=%20y"]

    (stats,) = proxy.connections
    assert stats.path == "/v1/listen"
    assert stats.stream_type == "stt"
    assert stats.frames_sent == 6
    assert stats.text_frames_sent == 1
    assert stats.text_frames_received == 2
    assert stats.frames_received == 6
    assert stats.transcripts == ["fake words"]
    assert len(stats.audio_buffer) == ProbeProxy.MAX_BUFFER_BYTES

    out = capsys.readouterr()
    assert KEY not in out.out + out.err


@pytest.mark.parametrize(
    "origin",
    [None, "http://localhost:5173", "https://127.0.0.1", "http://[::1]:8080"],
)
def test_loopback_or_no_origin_is_accepted(origin):
    async def main() -> int:
        async with FakeUpstream() as up, Proxy(up) as px:
            extra = {"Origin": origin} if origin else {}
            status, _ = await _status(px.port, _upgrade(px.port, **extra))
            return status

    assert asyncio.run(main()) == 101


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Origin": "http://localhost.evil.example"},
        {"Origin": "file://"},
        {"Origin": "http://localhost:"},
        {"Origin": "http://local\thost:5173"},
        {"Host": "evil.example:{port}"},
        {"Host": "127.0.0.1:1"},
    ],
    ids=lambda h: "-".join(f"{k}={v}" for k, v in h.items()),
)
def test_refused_before_any_upstream_connection(headers, capsys):
    async def main() -> tuple[int, bytes, FakeUpstream]:
        async with FakeUpstream() as up, Proxy(up) as px:
            extra = {k: v.format(port=px.port) for k, v in headers.items()}
            status, body = await _status(px.port, _upgrade(px.port, **extra))
            return status, body, up

    status, body, up = asyncio.run(main())
    assert (status, body) == (403, b"Forbidden.\n")
    assert up.handshakes == []
    out = " ".join(capsys.readouterr().out.split())  # rich wraps at 80 columns
    if "Origin" in headers:
        shown = headers["Origin"].replace("\t", "\\x09")
        assert (
            f"Refused a connection from {shown}: "
            "only pages on this machine can connect." in out
        )


def test_absolute_form_target_gets_400():
    async def main() -> tuple[int, FakeUpstream]:
        async with FakeUpstream() as up, Proxy(up) as px:
            status, _ = await _status(
                px.port, _upgrade(px.port, target="http://evil.example/v1/listen")
            )
            return status, up

    status, up = asyncio.run(main())
    assert status == 400
    assert up.handshakes == []


def test_plain_get_and_other_methods_are_refused(caplog):
    caplog.set_level(logging.DEBUG)

    async def main() -> list[int]:
        async with FakeUpstream() as up, Proxy(up) as px:
            plain = f"GET /v1/listen HTTP/1.1\r\nHost: 127.0.0.1:{px.port}\r\n\r\n"
            post = plain.replace("GET", "POST", 1)
            statuses = [(await _status(px.port, r))[0] for r in (plain, post)]
            assert up.handshakes == []
            return statuses

    plain, post = asyncio.run(main())
    assert plain == 400
    # 405 from the proxy on websockets 17; older versions close without a reply
    assert post in (0, 405)
    assert "Traceback" not in caplog.text


def test_markup_in_the_path_prints_literally(capsys):
    async def main() -> None:
        async with FakeUpstream() as up, Proxy(up) as px:
            url = f"ws://127.0.0.1:{px.port}/v1/listen[bold]x"
            ws = await connect(url)
            await ws.recv()
            await ws.close()

    asyncio.run(main())
    assert "path=/v1/listen[bold]x" in capsys.readouterr().out


def test_upstream_401_reports_and_keeps_serving(capsys, caplog):
    caplog.set_level(logging.DEBUG)

    async def main() -> list[tuple[int | None, str | None]]:
        closes = []
        async with FakeUpstream(status=401) as up, Proxy(up) as px:
            for _ in range(2):
                async with connect(f"ws://127.0.0.1:{px.port}/v1/listen") as ws:
                    with pytest.raises(ConnectionClosed):
                        await ws.recv()
                    closes.append((ws.protocol.close_code, ws.protocol.close_reason))
            assert len(up.handshakes) == 2
        return closes

    assert asyncio.run(main()) == [(1000, ""), (1000, "")]
    out = capsys.readouterr()
    assert out.out.count("Upstream connection failed") == 2
    assert "HTTP 401" in out.out
    for text in (out.out, out.err, caplog.text):
        assert KEY not in text


def test_key_never_logged_at_debug(caplog, capsys):
    caplog.set_level(logging.DEBUG)

    async def main() -> None:
        async with (
            FakeUpstream() as up,
            Proxy(up) as px,
            connect(f"ws://127.0.0.1:{px.port}/v1/listen") as ws,
        ):
            await ws.recv()
            await ws.send(b"\x00" * 64)
            await ws.recv()

    asyncio.run(main())
    out = capsys.readouterr()
    for text in (out.out, out.err, caplog.text):
        assert KEY not in text
        assert KEY[-4:] not in text


def test_listens_on_ipv4_loopback_only():
    async def main() -> list[tuple[int, str]]:
        async with FakeUpstream() as up, Proxy(up) as px:
            return [(s.family, s.getsockname()[0]) for s in px.server.sockets]

    import socket

    assert asyncio.run(main()) == [(socket.AF_INET, "127.0.0.1")]


def test_run_stops_on_event_and_on_cancel_after_summaries(capsys):
    """`run` returns when `_stop` is set; cancelling it (Ctrl+C) closes the
    listener and waits for open connections to finish their summaries."""

    async def main() -> ProbeProxy:
        async with FakeUpstream() as up:
            proxy = ProbeProxy(api_key=KEY)
            proxy._upstream_base = f"ws://127.0.0.1:{up.port}"
            with __import__("socket").socket() as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]

            task = asyncio.ensure_future(proxy.run(port))
            for _ in range(100):
                try:
                    ws = await connect(f"ws://127.0.0.1:{port}/v1/listen")
                    break
                except OSError:
                    await asyncio.sleep(0.02)
            await ws.recv()
            await ws.send(b"\x00" * 1000)
            await ws.recv()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert ws.protocol.close_code is not None

            proxy._stop = asyncio.Event()
            task = asyncio.ensure_future(proxy.run(port))
            await asyncio.sleep(0.05)
            proxy._stop.set()
            await asyncio.wait_for(task, 10)
            return proxy

    proxy = asyncio.run(main())
    (stats,) = proxy.connections
    assert stats.duration_seconds is not None
    out = capsys.readouterr().out
    assert "Connection closed" in out
    assert "Sent: 1,000 bytes (1 binary, 0 text frames)" in out


async def _raw_status(port: int, data: bytes) -> int:
    """Status of a raw request; 0 if the server closed without a response."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(data)
    await writer.drain()
    line = await asyncio.wait_for(reader.readline(), 10)
    writer.close()
    return int(line.split()[1]) if line else 0


def test_non_ascii_origin_does_not_wedge_the_console(monkeypatch):
    """A strict UTF-8 stdout (a UTF-8 terminal) and a raw non-ASCII Origin
    byte: the refusal still prints, and later connections still work."""
    import io

    from deepctl_cmd_debug_probe import proxy as proxymod
    from rich.console import Console

    out = io.BytesIO()
    strict = io.TextIOWrapper(out, encoding="utf-8", errors="strict")
    monkeypatch.setattr(
        proxymod, "console", Console(file=strict, width=200, force_terminal=False)
    )

    async def main() -> tuple[list[int], str | bytes]:
        async with FakeUpstream() as up, Proxy(up) as px:
            hostile = (
                _upgrade(px.port)
                .encode("latin-1")
                .replace(
                    b"\r\n\r\n",
                    b"\r\nOrigin: https://evil.example/\xff\x9b[bold]\r\n\r\n",
                )
            )
            bad_path = _upgrade(px.port, target="/v1/listen\xff\x1b[2J").encode(
                "latin-1"
            )
            statuses = [
                await _raw_status(px.port, hostile),
                await _raw_status(px.port, bad_path),
            ]
            async with connect(f"ws://127.0.0.1:{px.port}/v1/listen") as ws:
                first = await asyncio.wait_for(ws.recv(), 10)
            evil = _upgrade(px.port, Origin="https://evil.example").encode("latin-1")
            statuses.append(await _raw_status(px.port, evil))
            assert up.handshakes
            return statuses, first

    statuses, first = asyncio.run(main())
    # websockets 17 refuses a non-ASCII target itself; older ones accept it
    assert statuses[0] == statuses[2] == 403
    assert statuses[1] in (0, 101)
    assert first == RESULTS
    strict.flush()
    text = out.getvalue().decode("ascii")  # nothing but ASCII reached it
    assert "Refused a connection from https://evil.example/\\" in text
    if statuses[1] == 101:
        assert "path=/v1/listen\\" in text
        assert "\\x1b[2J" in text


@pytest.mark.parametrize(
    ("port", "host", "allowed"),
    [
        (80, "localhost", True),
        (80, "127.0.0.1", True),
        (80, "LOCALHOST:80", True),
        (80, "evil.example", False),
        (3100, "localhost", False),
        (3100, "127.0.0.1", False),
        (3100, "localhost:3100", True),
    ],
)
def test_bare_host_only_on_port_80(port, host, allowed):
    """On port 80 the browser sends the Host without a port."""
    from websockets.datastructures import Headers
    from websockets.http11 import Request

    class Conn:
        local_address = ("127.0.0.1", port)

        def respond(self, status: int, text: str) -> int:
            return status

    request = Request(
        "/v1/listen",
        Headers(
            [("Host", host), ("Upgrade", "websocket"), ("Origin", "http://localhost")]
        ),
    )
    result = ProbeProxy(api_key=KEY).process_request(Conn(), request)  # type: ignore[arg-type]
    assert result == (None if allowed else 403)


def test_access_policy():
    from deepctl_cmd_debug_probe.access import printable, refusal_reason, untrusted

    def reason(origin: str | None, host: str = "127.0.0.1:3100") -> str | None:
        return refusal_reason([host], [origin] if origin else [], 3100)

    for ok in (
        None,
        "http://localhost",
        "HTTPS://[::1]:8443",
        "http://127.0.0.1:99999",
    ):
        assert reason(ok) is None, ok
    for bad in (
        "null",
        "http://localhost:",
        "http://localhost/",
        "http://localhost?",
        "http://localhost#",
        "http://local\thost",
        "http://localhost.evil.example",
        "ws://localhost",
        "http://[::1%25eth0]",
    ):
        assert reason(bad) == "origin", bad
    assert reason(None, "127.0.0.1:3101") == "host"
    assert refusal_reason(["127.0.0.1:3100"] * 2, [], 3100) == "host"
    assert refusal_reason([], [], 3100) == "host"
    assert printable("a[b]\x1b\x9b\udcff\u00e9") == "a[b]\\x1b\\x9b\\udcff\\xe9"
    assert untrusted("[bold]:smile:\\\\\\").plain == "[bold]:smile:\\\\\\"


def _fake_ffprobe(tmp_path: Any, body: str) -> str:
    """An executable that runs `body` (Python) and prints an empty probe."""
    import os
    import sys

    path = tmp_path / "ffprobe"
    path.write_text(
        f"#!{sys.executable}\nimport signal, sys, time\n{body}\nprint('{{}}')\n"
    )
    os.chmod(path, 0o755)
    return str(path)


class _DeafUpstream:
    """Completes the handshake, then never reads again (ignores close)."""

    async def __aenter__(self) -> _DeafUpstream:
        import base64

        async def handle(reader: Any, writer: Any) -> None:
            head = await reader.readuntil(b"\r\n\r\n")
            key = next(
                line.split(b":", 1)[1].strip()
                for line in head.split(b"\r\n")
                if line.lower().startswith(b"sec-websocket-key:")
            )
            accept = base64.b64encode(
                hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest()
            )
            writer.write(
                b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                b"Connection: Upgrade\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n"
            )
            self.writers.append(writer)
            await asyncio.sleep(3600)

        self.writers: list[Any] = []
        self.server = await asyncio.start_server(handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc: object) -> None:
        for w in self.writers:
            w.transport.abort()
        self.server.close()


def test_snapshots_do_not_block_the_loop_and_stop_waits_for_summaries(
    monkeypatch, capsys, tmp_path
):
    """ffprobe runs off the loop: while one connection's snapshot runs,
    another is relayed, and a stop during the snapshot still ends with that
    connection's summary."""
    import time

    from deepctl_cmd_debug_probe import proxy as proxymod

    marker = tmp_path / "started"
    monkeypatch.setattr(
        proxymod,
        "get_ffprobe_path",
        lambda _config=None: _fake_ffprobe(
            tmp_path, f"open({str(marker)!r}, 'w').close(); time.sleep(1.5)"
        ),
    )

    async def main() -> tuple[float, ProbeProxy]:
        async with FakeUpstream() as up:
            proxy = ProbeProxy(api_key=KEY, probe_interval_bytes=1)
            proxy._upstream_base = f"ws://127.0.0.1:{up.port}"
            server = await proxy.start(0)
            port = next(iter(server.sockets)).getsockname()[1]
            server.close()
            await server.wait_closed()
            task = asyncio.ensure_future(proxy.run(port))
            await asyncio.sleep(0.2)
            a = await connect(f"ws://127.0.0.1:{port}/v1/listen")
            await a.recv()
            await a.send(b"\x00" * 100)  # starts a 1.5 s snapshot
            while not marker.exists():
                await asyncio.sleep(0.01)
            t0 = time.monotonic()
            b = await connect(f"ws://127.0.0.1:{port}/v1/listen")
            await b.recv()
            await b.send("ping")
            assert await b.recv() == "ping"
            relayed_in = time.monotonic() - t0
            for _ in range(30):  # a backlog: 30 more 1.5 s snapshots if drained
                await a.send(b"\x00" * 100)
            proxy._stop.set()  # what the first Ctrl+C does, mid-snapshot
            await asyncio.wait_for(task, 10)
            return relayed_in, proxy

    relayed_in, proxy = asyncio.run(main())
    assert relayed_in < 1.0
    assert all(c.duration_seconds is not None for c in proxy.connections)
    out = capsys.readouterr().out
    assert out.count("Connection closed") == 2
    assert "Not forwarded (arrived while stopping): 3,000 bytes (30 frames)" in out


def test_abort_ends_connections_without_waiting_for_close_timeout(capsys):
    """The second Ctrl+C: an upstream that never answers the close no longer
    holds shutdown for close_timeout (30 s)."""
    import time

    async def main() -> float:
        async with _DeafUpstream() as up:
            proxy = ProbeProxy(api_key=KEY)
            proxy._upstream_base = f"ws://127.0.0.1:{up.port}"
            server = await proxy.start(0)
            port = next(iter(server.sockets)).getsockname()[1]
            server.close()
            await server.wait_closed()
            task = asyncio.ensure_future(proxy.run(port))
            await asyncio.sleep(0.2)
            ws = await connect(f"ws://127.0.0.1:{port}/v1/listen")
            await ws.send(b"\x00" * 10)
            await asyncio.sleep(0.3)
            proxy._stop.set()
            await asyncio.sleep(1)
            assert not task.done()  # waiting on the deaf upstream's close
            t0 = time.monotonic()
            proxy.abort()
            await asyncio.wait_for(task, 10)
            return time.monotonic() - t0

    assert asyncio.run(main()) < 3
    assert "Connection closed" in capsys.readouterr().out


def test_abort_during_upstream_handshake():
    async def main() -> ProbeProxy:
        async def never(reader: Any, writer: Any) -> None:
            await reader.read()  # never answers; ends when the proxy hangs up
            writer.close()

        hang = await asyncio.start_server(never, "127.0.0.1", 0)
        proxy = ProbeProxy(api_key=KEY)
        proxy._upstream_base = f"ws://127.0.0.1:{hang.sockets[0].getsockname()[1]}"
        server = await proxy.start(0)
        port = next(iter(server.sockets)).getsockname()[1]
        server.close()
        await server.wait_closed()
        task = asyncio.ensure_future(proxy.run(port))
        await asyncio.sleep(0.2)
        ws = await connect(f"ws://127.0.0.1:{port}/v1/listen")
        await asyncio.sleep(0.3)
        proxy.abort()
        await asyncio.wait_for(task, 5)
        await ws.close()
        hang.close()
        return proxy

    proxy = asyncio.run(main())
    assert proxy.connections[0].duration_seconds is not None


def test_second_stop_kills_a_running_ffprobe_and_skips_the_final_probe(
    monkeypatch, capsys, tmp_path
):
    """abort() during a snapshot: ffprobe (here one that ignores SIGTERM) is
    killed within about a second, and no final probe starts."""
    import time

    from deepctl_cmd_debug_probe import proxy as proxymod

    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(
        proxymod,
        "get_ffprobe_path",
        lambda _config=None: _fake_ffprobe(
            tmp_path,
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            f"open({str(runs)!r} + '/%d' % time.time_ns(), 'w').close()\n"
            "time.sleep(30)",
        ),
    )

    async def main() -> float:
        async with FakeUpstream() as up:
            proxy = ProbeProxy(api_key=KEY, probe_interval_bytes=1)
            proxy._upstream_base = f"ws://127.0.0.1:{up.port}"
            server = await proxy.start(0)
            port = next(iter(server.sockets)).getsockname()[1]
            server.close()
            await server.wait_closed()
            task = asyncio.ensure_future(proxy.run(port))
            await asyncio.sleep(0.2)
            ws = await connect(f"ws://127.0.0.1:{port}/v1/listen")
            await ws.recv()
            await ws.send(b"\x00" * 100)
            while not any(runs.iterdir()):
                await asyncio.sleep(0.01)
            proxy._stop.set()
            await asyncio.sleep(0.2)
            t0 = time.monotonic()
            proxy.abort()
            await asyncio.wait_for(task, 5)
            return time.monotonic() - t0

    elapsed = asyncio.run(main())
    assert elapsed < 1.5
    assert len(list(runs.iterdir())) == 1  # no final probe
    assert "Connection closed" in capsys.readouterr().out


def test_transcripts_print_literally_and_keep_unicode(capsys):
    from deepctl_cmd_debug_probe.models import ProbeConnectionStats

    stats = ProbeConnectionStats(connection_id="1234abcd")
    transcript = "[bold]x[/bold] :smile: \u3053\u3093\u306b\u3061\u306f\x1b[2J\x9b"
    ProbeProxy(api_key=KEY)._parse_transcript(
        json.dumps(
            {
                "is_final": True,
                "channel": {"alternatives": [{"transcript": transcript}]},
            }
        ),
        stats,
    )
    out = capsys.readouterr().out
    assert "[bold]x[/bold] :smile: \u3053\u3093\u306b\u3061\u306f[2J" in out
    assert "\x1b" not in out and "\x9b" not in out
    assert stats.transcripts == [transcript]


@pytest.mark.parametrize(
    ("frames", "size", "line"),
    [
        (1, 1, "Not forwarded (arrived while stopping): 1 byte (1 frame)"),
        (2, 1234, "Not forwarded (arrived while stopping): 1,234 bytes (2 frames)"),
    ],
)
def test_not_forwarded_line_counts(frames, size, line, capsys):
    from deepctl_cmd_debug_probe.models import ProbeConnectionStats

    stats = ProbeConnectionStats(connection_id="1234abcd")
    ProbeProxy(api_key=KEY)._print_connection_summary(stats, frames, size)
    assert f"  {line}\n" in capsys.readouterr().out
    ProbeProxy(api_key=KEY)._print_connection_summary(stats)
    assert "Not forwarded" not in capsys.readouterr().out
