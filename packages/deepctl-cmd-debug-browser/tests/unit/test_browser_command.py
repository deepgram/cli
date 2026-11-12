"""Unit tests for browser debug command."""

import asyncio
import json
import logging
import socket
import threading
from typing import Any
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from deepctl_cmd_debug_browser import BrowserCommand, BrowserDebugResult
from deepctl_cmd_debug_browser.command import (
    CAPABILITY_KEYS,
    FORBIDDEN,
    MAX_MESSAGE_BYTES,
    MAX_MESSAGE_TEXT,
    MAX_MESSAGES,
    PAGE_HEADERS,
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def _raw(port: int, request: str) -> tuple[int, dict[str, str], bytes]:
    """Send one raw HTTP request; return status, headers and body.

    Status 0 means the server closed the connection without a response.
    """
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request.encode("latin-1"))
    await writer.drain()
    data = await asyncio.wait_for(reader.read(), 10)
    writer.close()
    if not data:
        return 0, {}, b""
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    return int(lines[0].split()[1]), headers, body


def _get(path: str, port: int, host: str | None = None, **extra: str) -> str:
    lines = [f"GET {path} HTTP/1.1", f"Host: {host or f'127.0.0.1:{port}'}"]
    lines += [f"{k.replace('_', '-')}: {v}" for k, v in extra.items()]
    return "\r\n".join(lines) + "\r\n\r\n"


def _upgrade(path: str, port: int, host: str | None = None, **extra: str) -> str:
    return _get(
        path,
        port,
        host,
        Upgrade="websocket",
        Connection="Upgrade",
        Sec_WebSocket_Key="dGhlIHNhbXBsZSBub25jZQ==",
        Sec_WebSocket_Version="13",
        **extra,
    )


class _CountingCommand(BrowserCommand):
    """Counts WebSocket handler runs (a refused request must not start one)."""

    handler_runs = 0

    async def websocket_handler(self, ws):  # type: ignore[no-untyped-def]
        self.handler_runs += 1
        await super().websocket_handler(ws)


class _Server:
    """A real BrowserCommand server on a free loopback port."""

    def __init__(self) -> None:
        self.cmd = _CountingCommand()
        self.cmd._token = "tok-123"

    async def __aenter__(self) -> "_Server":
        self.port = await self.cmd._start_server(0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.cmd._stop_server()


class TestBrowserCommand:
    """Test cases for BrowserCommand."""

    def test_command_properties(self):
        """Test basic command properties."""
        cmd = BrowserCommand()
        assert cmd.name == "browser"
        assert cmd.requires_auth is False
        assert cmd.requires_project is False
        assert cmd.ci_friendly is False  # Opens browser

    def test_get_arguments(self):
        """Test command arguments configuration."""
        cmd = BrowserCommand()
        args = cmd.get_arguments()

        # Check we have the expected arguments
        arg_names = []
        for arg in args:
            arg_names.extend(arg["names"])

        assert "--port" in arg_names
        assert "-p" in arg_names
        assert "--no-browser" in arg_names
        assert "--timeout" in arg_names

    def test_find_available_port(self):
        """Test port finding functionality."""
        cmd = BrowserCommand()

        # Should find a port starting from 3000
        port = cmd.find_available_port(3000)
        assert isinstance(port, int)
        assert port >= 3000
        assert port < 3100  # Should find within 100 ports

    @patch("webbrowser.open")
    @patch("builtins.input", return_value="")
    def test_handle_with_browser(self, mock_input, mock_browser_open):
        """Test command execution with browser opening."""
        cmd = BrowserCommand()

        # Mock the async run_servers method
        with patch.object(
            cmd, "run_servers", new_callable=AsyncMock
        ) as mock_run_servers:
            mock_run_servers.return_value = {
                "completed": True,
                "timed_out": False,
                "duration": 5.0,
            }

            # Set up capabilities data with proper BrowserCapability instances
            from deepctl_cmd_debug_browser.models import BrowserCapability

            cmd.capabilities_data = {
                "web_audio_api": BrowserCapability(
                    name="Web Audio API", supported=True, details="Supported"
                ),
                "audio_context": BrowserCapability(
                    name="AudioContext", supported=True, details="Supported"
                ),
                "audio_worklet": BrowserCapability(
                    name="AudioWorklet", supported=True, details="Supported"
                ),
                "websocket_api": BrowserCapability(
                    name="WebSocket API", supported=True, details="Supported"
                ),
                "fetch_api": BrowserCapability(
                    name="Fetch API", supported=True, details="Supported"
                ),
                "es6_features": BrowserCapability(
                    name="ES6+ Features", supported=True, details="Supported"
                ),
                "dom_apis": BrowserCapability(
                    name="DOM APIs", supported=True, details="Supported"
                ),
                "console_api": BrowserCapability(
                    name="Console API", supported=True, details="Supported"
                ),
                "timer_apis": BrowserCapability(
                    name="Timer APIs", supported=True, details="Supported"
                ),
                "secure_context": BrowserCapability(
                    name="Secure Context", supported=True, details="Supported"
                ),
                "user_agent": "Test Browser",
                "overall_compatible": True,
            }

            result = cmd.handle(
                config=Mock(),
                auth_manager=Mock(),
                client=Mock(),
                port=None,
                no_browser=False,
                timeout=60,
            )

        # Verify browser was opened, on the loopback URL with the token
        mock_browser_open.assert_called_once()
        url = urlsplit(mock_browser_open.call_args[0][0])
        assert url.hostname == "127.0.0.1"
        assert 3000 <= url.port < 3100
        assert parse_qs(url.query)["t"] == [cmd._token]

        # Verify result
        assert isinstance(result, BrowserDebugResult)
        assert result.status == "success"
        assert result.browser_opened is True
        assert result.port == url.port

    def test_handle_no_browser(self):
        """Test command execution without opening browser."""
        cmd = BrowserCommand()
        port = _free_port()

        # Mock the async run_servers method
        with patch.object(
            cmd, "run_servers", new_callable=AsyncMock
        ) as mock_run_servers:
            mock_run_servers.return_value = {
                "completed": False,
                "timed_out": True,
                "duration": 60.0,
            }

            result = cmd.handle(
                config=Mock(),
                auth_manager=Mock(),
                client=Mock(),
                port=port,
                no_browser=True,
                timeout=60,
            )

        # Verify result
        assert isinstance(result, BrowserDebugResult)
        assert result.status == "timeout"
        assert result.browser_opened is False
        assert result.port == port

    def test_given_port_in_use_raises(self):
        """A --port that is taken fails, as it did before (no fallback)."""
        cmd = BrowserCommand()
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            port = busy.getsockname()[1]
            try:
                cmd.handle(
                    config=Mock(),
                    auth_manager=Mock(),
                    client=Mock(),
                    port=port,
                    no_browser=True,
                    timeout=0,
                )
            except OSError:
                pass
            else:  # pragma: no cover
                raise AssertionError("expected OSError")

    def test_websocket_handler(self):
        """Messages from the page are recorded; bad frames are dropped and
        the connection stays open."""
        from websockets.asyncio.client import connect

        async def main() -> BrowserCommand:
            async with _Server() as srv:
                url = f"ws://127.0.0.1:{srv.port}/ws?t=tok-123"
                origin = f"http://127.0.0.1:{srv.port}"
                async with connect(url, origin=origin) as ws:
                    await ws.send(
                        json.dumps(
                            {"type": "info", "message": "Test message", "data": {}}
                        )
                    )
                    await ws.send("not json")
                    await ws.send(json.dumps({"type": "nope", "data": {}}))
                    await ws.send(json.dumps([1, 2]))
                    await ws.send(b"\x00\x01")
                    await ws.send(
                        json.dumps(
                            {
                                "type": "capability_check",
                                "data": {"capability": "web_audio_api", "result": "x"},
                            }
                        )
                    )
                    await ws.send(
                        '{"type": "capability_check", "data": {"capability": '
                        '"web_audio_api", "result": {"name": "Web Audio API", '
                        '"supported": true, "details": "Supported", "required": true}}}'
                    )
                    await ws.send(json.dumps({"type": "complete", "data": {}}))
                    for _ in range(100):
                        if srv.cmd.debug_complete:
                            break
                        await asyncio.sleep(0.02)
                    await ws.ping()  # still open after the bad frames
                return srv.cmd

        cmd = asyncio.run(main())
        assert [m.type.value for m in cmd.messages] == [
            "info",
            "capability_check",
            "complete",
        ]
        assert cmd.messages[0].message == "Test message"
        assert cmd.capabilities_data["web_audio_api"].supported is True
        assert cmd.debug_complete is True
        assert cmd.ws_clients == set()

    def test_messages_are_capped(self):
        cmd = BrowserCommand()
        for _ in range(MAX_MESSAGES + 5):
            cmd._handle_text('{"type": "info", "message": "m", "data": {}}')
        cmd._handle_text('{"type": "complete", "data": {}}')
        assert len(cmd.messages) == MAX_MESSAGES
        assert cmd.debug_complete is True

    def test_stored_messages_are_capped_by_size(self):
        cmd = BrowserCommand()
        too_long = json.dumps(
            {"type": "info", "message": "m" * MAX_MESSAGE_TEXT, "data": {}}
        )
        cmd._handle_text(too_long)
        assert cmd.messages == []
        assert cmd.dropped_messages == 1
        # object-heavy frames are counted by what is stored, not by input size
        heavy = json.dumps(
            {"type": "info", "message": "m", "data": {"a": [{}] * 10000}}
        )
        for _ in range(400):
            cmd._handle_text(heavy)
        assert cmd._stored_bytes <= MAX_MESSAGE_BYTES
        assert 0 < len(cmd.messages) < 400
        assert cmd.dropped_messages == 1 + 400 - len(cmd.messages)

    def test_only_known_capability_names_are_kept(self):
        cmd = BrowserCommand()
        result = {"name": "x", "supported": True}
        for name in [f"cap{i}" for i in range(300)] + list(CAPABILITY_KEYS):
            cmd._handle_text(
                json.dumps(
                    {
                        "type": "capability_check",
                        "data": {"capability": name, "result": result},
                    }
                )
            )
        assert set(cmd.capabilities_data) == set(CAPABILITY_KEYS)
        assert cmd.dropped_capabilities == 300

    def test_find_available_port_closes_sockets(self):
        import gc
        import warnings

        cmd = BrowserCommand()
        # Only this call's sockets count: collect what earlier tests left.
        gc.collect()
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            taken = busy.getsockname()[1]
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                assert cmd.find_available_port(taken) != taken
                gc.collect()
        assert not [w for w in caught if issubclass(w.category, ResourceWarning)]

    def test_page_text_is_printed_as_plain_ascii(self):
        import io

        from deepctl_cmd_debug_browser import command as cmdmod
        from deepctl_cmd_debug_browser.models import (
            BrowserCapabilities,
            BrowserCapability,
            MessageType,
            WebSocketMessage,
        )
        from rich.console import Console

        out = io.StringIO()
        with patch.object(
            cmdmod, "console", Console(file=out, width=300, force_terminal=False)
        ):
            BrowserCommand().display_results(
                None,
                [
                    WebSocketMessage(
                        type=MessageType.INFO,
                        data={},
                        message="[bold]x\x1b[2J\u00e9 :smile: y\\\\\\",
                    )
                ],
            )
            BrowserCommand().display_results(
                BrowserCapabilities(
                    **{
                        key: BrowserCapability(
                            name=key, supported=True, details="d\\\\\\ :smile:"
                        )
                        for key in CAPABILITY_KEYS
                    },
                    user_agent="[bold]UA :smile:",
                    overall_compatible=True,
                ),
                [],
            )
        text = out.getvalue()
        assert "[bold]x\\x1b[2J\\xe9 :smile: y\\\\\\" in text
        assert "d\\\\\\ :smile:" in text
        assert "User Agent: [bold]UA :smile:" in text
        assert "[/" not in text


class TestBrowserSecurity:
    """The local server only talks to the page this command opened."""

    @pytest.mark.parametrize(
        ("port", "host", "origin", "allowed"),
        [
            (80, "127.0.0.1", "http://127.0.0.1", True),
            (80, "localhost", "http://localhost", True),
            (80, "127.0.0.1:80", "http://127.0.0.1:80", True),
            (80, "evil.example", "http://127.0.0.1", False),
            (80, "127.0.0.1", "http://evil.example", False),
            (3000, "127.0.0.1", "http://127.0.0.1:3000", False),
            (3000, "127.0.0.1:3000", "http://127.0.0.1", False),
        ],
    )
    def test_bare_host_and_origin_only_on_port_80(self, port, host, origin, allowed):
        """Browsers leave the default port out of Host and Origin."""
        from websockets.datastructures import Headers
        from websockets.http11 import Request

        class Conn:
            local_address = ("127.0.0.1", port)

            def respond(self, status: int, text: str) -> Any:
                return Mock(status_code=status, headers=Headers())

        cmd = BrowserCommand()
        cmd._token = "tok-123"
        request = Request(
            "/ws?t=tok-123",
            Headers([("Host", host), ("Upgrade", "websocket"), ("Origin", origin)]),
        )
        result = cmd._process_request(Conn(), request)  # type: ignore[arg-type]
        assert (result is None) is allowed

    def test_listens_on_ipv4_loopback_only(self):
        async def main() -> list[tuple[int, str]]:
            async with _Server() as srv:
                return [
                    (s.family, s.getsockname()[0])
                    for s in srv.cmd._server.sockets  # type: ignore[union-attr]
                ]

        assert asyncio.run(main()) == [(socket.AF_INET, "127.0.0.1")]

    def test_page_with_token(self):
        async def main() -> tuple[int, dict[str, str], bytes]:
            async with _Server() as srv:
                page = await _raw(srv.port, _get("/?t=tok-123", srv.port))
                await asyncio.sleep(0.1)
                assert srv.cmd.handler_runs == 0
                return page

        status, headers, body = asyncio.run(main())
        assert status == 200
        for name, value in PAGE_HEADERS:
            assert headers[name.lower()] == value
        assert "server" not in headers
        assert b"WS_URL" in body
        assert "location.host" in body.decode()

    def test_localhost_host_is_accepted(self):
        async def main() -> int:
            async with _Server() as srv:
                req = _get("/?t=tok-123", srv.port, host=f"LOCALHOST:{srv.port}")
                return (await _raw(srv.port, req))[0]

        assert asyncio.run(main()) == 200

    def test_refusals_are_fixed_text(self):
        async def main() -> dict[str, tuple[int, bytes]]:
            out = {}
            async with _Server() as srv:
                p = srv.port
                origin = f"http://127.0.0.1:{p}"
                cases = {
                    "page-no-token": _get("/", p),
                    "page-bad-token": _get("/?t=evil-token", p),
                    "page-two-tokens": _get("/?t=tok-123&t=tok-123", p),
                    "page-evil-host": _get("/?t=tok-123", p, host=f"evil.example:{p}"),
                    "page-wrong-port": _get("/?t=tok-123", p, host="127.0.0.1:1"),
                    "ws-no-token": _upgrade("/ws", p, Origin=origin),
                    "ws-bad-token": _upgrade("/ws?t=evil-token", p, Origin=origin),
                    "ws-evil-host": _upgrade(
                        "/ws?t=tok-123", p, host=f"evil.example:{p}", Origin=origin
                    ),
                    "ws-no-origin": _upgrade("/ws?t=tok-123", p),
                    "ws-evil-origin": _upgrade(
                        "/ws?t=tok-123", p, Origin="http://evil.example"
                    ),
                    "ws-null-origin": _upgrade("/ws?t=tok-123", p, Origin="null"),
                    "ws-other-port": _upgrade(
                        "/ws?t=tok-123", p, Origin="http://127.0.0.1:1"
                    ),
                }
                for name, req in cases.items():
                    status, _, body = await _raw(p, req)
                    out[name] = (status, body)
                await asyncio.sleep(0.1)
                assert srv.cmd.handler_runs == 0
            return out

        for name, (status, body) in asyncio.run(main()).items():
            assert (status, body) == (403, FORBIDDEN.encode()), name
        assert FORBIDDEN == (
            "Forbidden. Open the exact link that dg debug browser printed.\n"
        )

    def test_valid_upgrade_gets_101(self):
        async def main() -> list[int]:
            async with _Server() as srv:
                p = srv.port
                return [
                    (await _raw_status_only(p, _upgrade("/ws?t=tok-123", p, Origin=o)))
                    for o in (f"http://127.0.0.1:{p}", f"http://localhost:{p}")
                ]

        assert asyncio.run(main()) == [101, 101]

    def test_unknown_path_and_methods(self, caplog):
        caplog.set_level(logging.DEBUG)

        async def main() -> list[int]:
            async with _Server() as srv:
                p = srv.port
                post = _get("/?t=tok-123", p).replace("GET", "POST", 1)
                return [
                    (await _raw(p, _get("/x?t=tok-123", p)))[0],
                    (await _raw(p, _get("/static/debug.html?t=tok-123", p)))[0],
                    (await _raw(p, post))[0],
                ]

        statuses = asyncio.run(main())
        assert statuses[:2] == [404, 404]
        # 405 from us on websockets 17; older versions close without a reply
        assert statuses[2] in (0, 405)
        assert "Traceback" not in caplog.text
        assert "tok-123" not in caplog.text

    def test_token_not_in_result_or_report(self, tmp_path):
        cmd = BrowserCommand()
        with patch.object(
            cmd,
            "run_servers",
            new=AsyncMock(
                return_value={"completed": False, "timed_out": True, "duration": 0.0}
            ),
        ):
            result = cmd.handle(
                config=Mock(),
                auth_manager=Mock(),
                client=Mock(),
                port=_free_port(),
                no_browser=True,
                timeout=0,
                save_report=str(tmp_path / "r.json"),
            )
        assert cmd._token
        assert cmd._token not in result.model_dump_json()
        assert cmd._token not in (tmp_path / "r.json").read_text()

    def test_end_to_end_page_session(self):
        """handle() serves the page and completes when the page says so."""
        from websockets.sync.client import connect

        cmd = BrowserCommand()
        cmd._guided = False
        seen = {}

        def page(url: str) -> None:
            parts = urlsplit(url)
            seen["url"] = url
            ws_url = f"ws://{parts.netloc}/ws?{parts.query}"
            with connect(ws_url, origin=f"http://{parts.netloc}") as ws:
                ws.send(json.dumps({"type": "info", "message": "hi", "data": {}}))
                ws.send(json.dumps({"type": "complete", "data": {}}))

        def open_browser(url: str) -> bool:
            threading.Thread(target=page, args=(url,), daemon=True).start()
            return True

        with patch(
            "deepctl_cmd_debug_browser.command.webbrowser.open",
            side_effect=open_browser,
        ):
            result = cmd.handle(
                config=Mock(),
                auth_manager=Mock(),
                client=Mock(),
                port=None,
                no_browser=False,
                timeout=20,
            )
        assert result.status == "success"
        assert [m.message for m in result.messages] == ["hi", None]
        assert seen["url"] == f"http://127.0.0.1:{result.port}/?t={cmd._token}"


async def _raw_status_only(port: int, request: str) -> int:
    """Status of an upgrade request (the connection stays open on 101)."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request.encode("latin-1"))
    await writer.drain()
    line = await asyncio.wait_for(reader.readline(), 10)
    writer.close()
    return int(line.split()[1])


class TestBrowserGuidedGate:
    """Verify the press-Enter prompt only fires in guided (bare) invocations."""

    def _run_handle(self, guided: bool):
        """Run BrowserCommand.handle and capture whether input() was called."""
        cmd = BrowserCommand()
        cmd._guided = guided
        called = {"input": 0, "open": 0}

        with (
            patch(
                "builtins.input",
                side_effect=lambda: called.__setitem__("input", called["input"] + 1),
            ),
            patch(
                "deepctl_cmd_debug_browser.command.webbrowser.open",
                side_effect=lambda _url: called.__setitem__("open", called["open"] + 1),
            ),
        ):
            cmd.handle(
                config=Mock(),
                auth_manager=Mock(),
                client=Mock(),
                port=None,
                no_browser=False,
                timeout=0,
                save_report=None,
            )
        return called

    def test_guided_invocation_waits_for_press_enter(self):
        called = self._run_handle(guided=True)
        assert called["input"] == 1
        assert called["open"] == 1

    def test_non_guided_invocation_opens_browser_immediately(self):
        called = self._run_handle(guided=False)
        assert called["input"] == 0
        assert called["open"] == 1


IMPORT_CHECK = (
    "import sys, deepctl_core; before = set(sys.modules); "
    "import deepctl_cmd_debug_browser.command; "
    "bad = sorted(m for m in set(sys.modules) - before for f in "
    "('asyncio', 'ssl', 'websockets', 'aiohttp') if m == f or m.startswith(f + '.')); "
    "print(' '.join(bad))"
)


def test_command_module_adds_no_network_stack_on_import():
    """`dg debug browser --help` must not pay for websockets or asyncio."""
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


def _flat(text: str) -> str:
    return " ".join(text.split())


def _timed_out(cmd: BrowserCommand) -> Any:
    return patch.object(
        cmd,
        "run_servers",
        new=AsyncMock(
            return_value={"completed": False, "timed_out": True, "duration": 0.0}
        ),
    )


class TestBrowserLinkLines:
    """Every mode shows the page link, on stderr only."""

    @pytest.mark.parametrize(
        ("no_browser", "guided", "lead", "note"),
        [
            (
                True,
                False,
                "Open this link in your browser to start debugging:",
                "It works only while this command runs.",
            ),
            (
                False,
                False,
                "Opening the debugger in your browser:",
                "If it doesn't open, paste the link into your browser. "
                "It works only while this command runs.",
            ),
            (
                False,
                True,
                "Opening the debugger in your browser:",
                "If it doesn't open, paste the link into your browser. "
                "It works only while this command runs.",
            ),
        ],
        ids=["no-browser", "auto-open", "guided"],
    )
    def test_link_is_shown_on_stderr(self, no_browser, guided, lead, note, capsys):
        cmd = BrowserCommand()
        cmd._guided = guided
        order: list[str] = []
        before: list[str] = []

        def enter() -> str:
            order.append("enter")
            out, err = capsys.readouterr()
            before.append(out)
            assert "?t=" not in err  # the link comes after Enter
            return ""

        with (
            _timed_out(cmd),
            patch("builtins.input", side_effect=enter),
            patch(
                "deepctl_cmd_debug_browser.command.webbrowser.open",
                side_effect=lambda url: order.append("open") or True,
            ),
        ):
            result = cmd.handle(
                config=Mock(),
                auth_manager=Mock(),
                client=Mock(),
                port=None,
                no_browser=no_browser,
                timeout=0,
            )
        out, err = capsys.readouterr()
        url = f"http://127.0.0.1:{result.port}/?t={cmd._token}"
        assert "".join(before) + out == ""
        assert f"{lead} {url}\n" in err  # the whole link on one line
        assert note in _flat(err)
        assert order == ([] if no_browser else (["enter"] if guided else []) + ["open"])

    def test_left_out_line_names_only_non_zero_parts(self, capsys):
        cmd = BrowserCommand()
        result = {"completed": False, "timed_out": False, "duration": 0.0}
        for messages, names, line in [
            (0, 0, None),
            (1, 0, "Left out of the results: 1 message over the size limit"),
            (0, 1, "Left out of the results: 1 unknown capability name"),
            (
                3,
                2,
                "Left out of the results: 3 messages over the size limit, "
                "2 unknown capability names",
            ),
        ]:
            cmd.dropped_messages, cmd.dropped_capabilities = messages, names
            cmd._finish(0, 0, None, result)
            err = _flat(capsys.readouterr().err)
            if line is None:
                assert "Left out" not in err
            else:
                assert line in err


def test_link_token_reaches_stderr_only(tmp_path):
    """`dg -o json debug browser`: the token-bearing link is printed to stderr
    and nowhere else: not stdout (the JSON result), the saved report, or any
    log record at DEBUG."""
    import os
    import re
    import subprocess
    import sys

    script = (
        "import logging, sys\n"
        "from deepctl.main import main\n"
        "log_path, report_path = sys.argv[1], sys.argv[2]\n"
        "class Keep(logging.Handler):\n"
        "    def emit(self, record):\n"
        "        with open(log_path, 'a') as f:\n"
        "            f.write(self.format(record) + '\\n')\n"
        "logging.basicConfig(level=logging.DEBUG, handlers=[Keep()], force=True)\n"
        "sys.argv = ['dg', '-o', 'json', 'debug', 'browser', '--no-browser',\n"
        "            '--timeout', '1', '--save-report', report_path]\n"
        "main()\n"
    )
    log, report = tmp_path / "log.txt", tmp_path / "report.json"
    log.write_text("")
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "DEEPCTL_TELEMETRY_DISABLED": "1",
        "DEEPCTL_TELEMETRY_DSN": "http://k@127.0.0.1:9/1",
    }
    proc = subprocess.run(
        [sys.executable, "-c", script, str(log), str(report)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        stdin=subprocess.DEVNULL,
        cwd=tmp_path,
    )
    found = re.findall(r"http://127\.0\.0\.1:\d+/\?t=([A-Za-z0-9_-]+)", proc.stderr)
    assert len(found) == 1, proc.stderr[-2000:]
    token = found[0]
    assert json.loads(proc.stdout)["status"] == "timeout"
    for where, text in [
        ("stdout", proc.stdout),
        ("report", report.read_text()),
        ("log", log.read_text()),
    ]:
        assert token not in text, where
        assert "?t=" not in text, where
    assert log.read_text(), "the DEBUG handler saw no records"
