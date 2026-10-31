"""Live listen refuses a redirect on the WebSocket upgrade.

Two raw TCP servers on loopback stand in for the API host and for the
host a redirect points at. Raw sockets keep the tests independent of the
installed websockets version: the client under test is the real one.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import types
from dataclasses import dataclass, field
from unittest.mock import MagicMock

import pytest
from deepctl_cmd_listen.command import ListenCommand
from deepctl_core.websocket import RedirectRefused

FAKE_KEY = "fa1ce0000000000000000000000000000000000d"


@dataclass
class _Server:
    host: str
    port: int = 0
    requests: list[bytes] = field(default_factory=list)

    @property
    def origin(self) -> str:
        return f"http://{self.host}:{self.port}"


class _Servers:
    """The API host (``origin``) and the redirect target, on their own loop."""

    def __init__(self, location: str) -> None:
        self.origin = _Server("127.0.0.1")
        self.target = _Server("127.0.0.1")
        self._location = location
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    async def _read_request(self, reader: asyncio.StreamReader) -> bytes:
        try:
            return await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError):
            return b""

    async def _origin(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.origin.requests.append(await self._read_request(reader))
        location = self._location.format(
            origin=f"ws://{self.origin.host}:{self.origin.port}",
            target=f"ws://{self.target.host}:{self.target.port}",
        )
        writer.write(
            b"HTTP/1.1 302 Found\r\n"
            + f"Location: {location}\r\n".encode("latin-1")
            + b"Content-Length: 0\r\nConnection: close\r\n\r\n"
        )
        await writer.drain()
        writer.close()

    async def _target(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        # Any connection at all is a failure; record what arrived.
        self.target.requests.append(await self._read_request(reader) or b"<conn>")
        writer.close()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)

        async def start() -> None:
            for srv, handler in (
                (self.origin, self._origin),
                (self.target, self._target),
            ):
                server = await asyncio.start_server(handler, srv.host, 0)
                srv.port = server.sockets[0].getsockname()[1]
            self._ready.set()

        self._loop.run_until_complete(start())
        self._loop.run_forever()

    def __enter__(self) -> _Servers:
        self._thread.start()
        assert self._ready.wait(5)
        return self

    def __exit__(self, *_exc: object) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5)


CROSS = "{target}/second/v1/listen"
SAME = "{origin}/second/v1/listen"


@pytest.fixture
def no_audio_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    # The mic path imports these at call time; no hardware is touched
    # because the connect fails first.
    for name in ("sounddevice", "numpy"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))


def _client(base_url: str) -> MagicMock:
    client = MagicMock()
    client.auth_manager.check_base_url.return_value = base_url
    client.auth_manager.get_api_key.return_value = FAKE_KEY
    return client


_COMMON = {
    "model": "nova-3",
    "language": "en-US",
    "api_version": 1,
    "diarize": False,
    "smart_format": True,
    "punctuate": True,
    "interim": False,
    "redact": (),
    "numerals": False,
    "sample_rate": 16000,
    "channels": 1,
    "save_to": None,
    "caption_format": None,
}


def _run(mode: str, base_url: str) -> BaseException | None:
    """Run the live path; return what it raised (None if it returned)."""
    command = ListenCommand()
    client = _client(base_url)
    try:
        if mode == "mic":
            command._stream_mic(client, **_COMMON)
        else:
            command._stream_stdin(client, encoding="linear16", **_COMMON)
    except BaseException as exc:  # inspected by the caller
        return exc
    return None


@pytest.mark.parametrize("mode", ["mic", "stdin"])
def test_cross_origin_redirect_is_refused(mode, no_audio_devices):
    with _Servers(CROSS) as servers:
        err = _run(mode, servers.origin.origin)

    # Exactly one upgrade, to the API host, which did get the key.
    assert len(servers.origin.requests) == 1
    assert f"Token {FAKE_KEY}".encode() in servers.origin.requests[0]
    # Nothing at all reached the redirect target.
    assert servers.target.requests == []
    assert isinstance(err, RedirectRefused)
    assert err.exit_code != 0
    target = f"ws://127.0.0.1:{servers.target.port}"
    assert err.message == (
        f"ws://127.0.0.1:{servers.origin.port} answered with a redirect to "
        f"{target}. dg listen doesn't follow redirects, so no audio was sent."
    )


@pytest.mark.parametrize("mode", ["mic", "stdin"])
def test_same_origin_redirect_is_refused(mode, no_audio_devices):
    with _Servers(SAME) as servers:
        err = _run(mode, servers.origin.origin)

    # The redirect back to the same host is not followed either.
    assert len(servers.origin.requests) == 1
    assert servers.target.requests == []
    assert isinstance(err, RedirectRefused)
    assert err.exit_code != 0
    assert "doesn't follow redirects" in err.message


def test_relative_location_names_the_same_origin(no_audio_devices):
    with _Servers("/second/v1/listen") as servers:
        err = _run("stdin", servers.origin.origin)

    origin = f"ws://127.0.0.1:{servers.origin.port}"
    assert len(servers.origin.requests) == 1
    assert isinstance(err, RedirectRefused)
    assert err.message.startswith(f"{origin} answered with a redirect to {origin}.")


def test_control_bytes_in_location_are_not_echoed(no_audio_devices):
    # A C1 control (CSI) passes websockets' header check as a raw byte.
    with _Servers("ws://evil\x9b31m.example/x") as servers:
        err = _run("stdin", servers.origin.origin)

    assert isinstance(err, RedirectRefused)
    assert err.message.isascii()
    assert all(c.isprintable() for c in err.message)
    assert "redirect to ws://evil. " in err.message
    assert servers.target.requests == []


@pytest.mark.parametrize("mode", ["mic", "stdin"])
def test_unbalanced_bracket_in_location_gives_the_redirect_message(
    mode, no_audio_devices
):
    # urljoin raises ValueError on this; the message falls back to "?".
    with _Servers("ws://[evil.example/x") as servers:
        err = _run(mode, servers.origin.origin)

    assert servers.target.requests == []
    assert isinstance(err, RedirectRefused)
    assert err.message == (
        f"ws://127.0.0.1:{servers.origin.port} answered with a redirect to ?. "
        "dg listen doesn't follow redirects, so no audio was sent."
    )


@pytest.mark.parametrize("mode", ["mic", "stdin"])
def test_upgrade_carries_the_request_headers(mode, no_audio_devices):
    """The live upgrade identifies the run as every other API request does:
    the SDK's User-Agent, the session ID and the key."""
    from deepctl_core import transport

    with _Servers(CROSS) as servers:
        _run(mode, servers.origin.origin)

    request = servers.origin.requests[0].decode("latin-1").lower()
    for name, value in transport.request_headers(FAKE_KEY).items():
        assert f"\r\n{name.lower()}: {value.lower()}\r\n" in request, name
