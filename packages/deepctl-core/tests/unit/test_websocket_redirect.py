"""WebSocket connects refuse a redirect on the upgrade.

Two raw TCP servers on loopback stand in for the API host and for the
host a redirect points at, so the real websockets client is exercised on
whatever version is installed.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from unittest.mock import Mock

import pytest
from deepctl_core import Config, DeepgramClient
from deepctl_core.auth import AuthManager
from deepctl_core.websocket import (
    RedirectRefused,
    connect_async,
    connect_sync,
    redirect_refused,
)

FAKE_KEY = "fa1ce0000000000000000000000000000000000d"
HEADERS = {"Authorization": f"Token {FAKE_KEY}"}


@dataclass
class _Server:
    host: str = "127.0.0.1"
    port: int = 0
    requests: list[bytes] = field(default_factory=list)


class _Servers:
    """The API host (``origin``) and the redirect target, on their own loop."""

    def __init__(self, location: str, status: str = "302 Found") -> None:
        self.origin = _Server()
        self.target = _Server()
        self._location = location
        self._status = status
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.origin.port}/v1/listen"

    async def _read(self, reader: asyncio.StreamReader) -> bytes:
        try:
            return await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError):
            return b""

    async def _origin(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.origin.requests.append(await self._read(reader))
        location = self._location.format(
            origin=f"ws://127.0.0.1:{self.origin.port}",
            target=f"ws://127.0.0.1:{self.target.port}",
        )
        writer.write(
            f"HTTP/1.1 {self._status}\r\n".encode()
            + f"Location: {location}\r\n".encode("latin-1")
            + b"Content-Length: 0\r\nConnection: close\r\n\r\n"
        )
        await writer.drain()
        writer.close()

    async def _target(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.target.requests.append(await self._read(reader) or b"<conn>")
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


CROSS = "{target}/second"
SAME = "{origin}/second"


def _open_async(url: str) -> BaseException | None:
    async def go() -> None:
        async with connect_async(url, HEADERS, command="dg listen", payload="audio"):
            pass

    try:
        asyncio.run(go())
    except BaseException as exc:  # inspected by the caller
        return exc
    return None


def _open_sync(url: str) -> BaseException | None:
    try:
        with connect_sync(url, HEADERS, command="dg speak", payload="text"):
            pass
    except BaseException as exc:  # inspected by the caller
        return exc
    return None


OPENERS = {"async": _open_async, "sync": _open_sync}


@pytest.mark.parametrize("mode", ["async", "sync"])
@pytest.mark.parametrize("location", [CROSS, SAME])
def test_redirect_is_refused(mode, location):
    with _Servers(location) as servers:
        err = OPENERS[mode](servers.url)

    # Nothing reached the target, and the API host saw one upgrade only.
    assert servers.target.requests == []
    assert len(servers.origin.requests) == 1
    assert isinstance(err, RedirectRefused)
    assert err.exit_code != 0
    assert "doesn't follow redirects" in err.message


def test_message_names_both_origins():
    with _Servers(CROSS) as servers:
        err = _open_sync(servers.url)

    assert isinstance(err, RedirectRefused)
    assert err.message == (
        f"ws://127.0.0.1:{servers.origin.port} answered with a redirect to "
        f"ws://127.0.0.1:{servers.target.port}. dg speak doesn't follow "
        "redirects, so no text was sent."
    )


@pytest.mark.parametrize("mode", ["async", "sync"])
def test_other_statuses_are_not_rewritten(mode):
    from websockets.exceptions import InvalidStatus

    with _Servers(CROSS, status="401 Unauthorized") as servers:
        err = OPENERS[mode](servers.url)

    assert isinstance(err, InvalidStatus)
    assert servers.target.requests == []


@pytest.mark.parametrize(
    "location",
    ["ws://[evil.example/x", "http://[::1/x", "ws://evil\x9b31m.example/x"],
)
def test_unparseable_location_still_gives_the_message(location):
    err = redirect_refused(
        "ws://127.0.0.1:8001/v1/listen", location, command="dg listen", payload="audio"
    )
    assert err.message.startswith("ws://127.0.0.1:8001 answered with a redirect to ")
    assert err.message.isascii()
    assert all(c.isprintable() for c in err.message)


def test_unbalanced_bracket_location_over_the_wire():
    with _Servers("ws://[evil.example/x") as servers:
        err = _open_async(servers.url)

    assert isinstance(err, RedirectRefused)
    assert "answered with a redirect to ?." in err.message
    assert servers.target.requests == []


def test_flux_speak_refuses_a_cross_origin_redirect():
    """dg speak's Flux stream goes through the same refusal."""
    with _Servers(CROSS) as servers:
        auth = Mock(spec=AuthManager)
        auth.get_api_key.return_value = FAKE_KEY
        auth.get_project_id.return_value = "p"
        auth.check_base_url.return_value = f"http://127.0.0.1:{servers.origin.port}"
        client = DeepgramClient(Mock(spec=Config), auth)
        with pytest.raises(RedirectRefused) as info:
            list(client.speak_text_stream("secret words", model="flux-alexis-en"))

    assert servers.target.requests == []
    assert len(servers.origin.requests) == 1
    assert b"/v2/speak?model=flux-alexis-en" in servers.origin.requests[0]
    assert info.value.message.endswith(
        "dg speak doesn't follow redirects, so no text was sent."
    )
