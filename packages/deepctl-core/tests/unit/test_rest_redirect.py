"""SDK REST calls don't follow a redirect.

A loopback stub stands in for the API host and answers every request with
a redirect to a second stub, which records anything that reaches it. The
real SDK and httpx client are used.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from unittest.mock import Mock

import pytest
from deepctl_core import Config, DeepgramClient
from deepctl_core.auth import AuthManager
from deepctl_core.websocket import RedirectRefused

FAKE_KEY = "fa1ce0000000000000000000000000000000000d"


@dataclass
class _Server:
    port: int = 0
    requests: list[bytes] = field(default_factory=list)


class _Servers:
    def __init__(self, status: str, location: str) -> None:
        self.origin = _Server()
        self.target = _Server()
        self._status = status
        self._location = location
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    async def _read(self, reader: asyncio.StreamReader) -> bytes:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError):
            return b""
        length = 0
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":", 1)[1])
        body = await reader.readexactly(length) if length else b""
        return head + body

    async def _origin(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.origin.requests.append(await self._read(reader))
        location = self._location.format(target=f"127.0.0.1:{self.target.port}")
        writer.write(
            f"HTTP/1.1 {self._status}\r\nLocation: {location}\r\n"
            "Content-Length: 0\r\nConnection: close\r\n\r\n".encode()
        )
        await writer.drain()
        writer.close()

    async def _target(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.target.requests.append(await self._read(reader) or b"<conn>")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
        await writer.drain()
        writer.close()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)

        async def start() -> None:
            for srv, handler in (
                (self.origin, self._origin),
                (self.target, self._target),
            ):
                server = await asyncio.start_server(handler, "127.0.0.1", 0)
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


def _client(port: int) -> DeepgramClient:
    auth = Mock(spec=AuthManager)
    auth.get_api_key.return_value = FAKE_KEY
    auth.get_project_id.return_value = "p"
    auth.check_base_url.return_value = f"http://127.0.0.1:{port}"
    return DeepgramClient(Mock(spec=Config), auth)


def _transcribe(client: DeepgramClient, tmp_path) -> object:
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF" + b"\0" * 4000)
    return client.transcribe_file(audio)


def _speak(client: DeepgramClient, tmp_path) -> object:
    return b"".join(client.speak_text("secret words", model="aura-2-asteria-en"))


def _read(client: DeepgramClient, tmp_path) -> object:
    return client.analyze_text("secret words", summarize=True)


CALLS = {"transcribe": _transcribe, "speak": _speak, "read": _read}


@pytest.mark.parametrize("status", ["307 Temporary Redirect", "302 Found"])
@pytest.mark.parametrize("call", sorted(CALLS))
def test_cross_origin_redirect_never_reaches_the_target(call, status, tmp_path):
    with _Servers(status, "http://{target}/second") as servers:
        client = _client(servers.origin.port)
        with pytest.raises(RedirectRefused) as info:
            CALLS[call](client, tmp_path)

    assert servers.target.requests == []
    assert len(servers.origin.requests) == 1
    assert f"Token {FAKE_KEY}".encode() in servers.origin.requests[0]
    assert info.value.exit_code != 0
    assert info.value.message == (
        f"http://127.0.0.1:{servers.origin.port} answered with a redirect to "
        f"http://127.0.0.1:{servers.target.port}. dg doesn't follow redirects, "
        "so nothing was sent there."
    )


@pytest.mark.parametrize("call", sorted(CALLS))
def test_same_origin_redirect_is_not_followed_either(call, tmp_path):
    with _Servers("307 Temporary Redirect", "/second") as servers:
        client = _client(servers.origin.port)
        with pytest.raises(RedirectRefused):
            CALLS[call](client, tmp_path)

    assert len(servers.origin.requests) == 1
    assert servers.target.requests == []


def test_sdk_client_is_built_without_redirects():
    client = _client(1)
    http = client.client._client_wrapper.httpx_client.httpx_client
    assert http.follow_redirects is False
