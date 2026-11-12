"""The vendored stdio MCP server: local handshake, per-call remote sessions."""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

import deepctl_cmd_mcp.proxy as proxy
from deepctl.mcp import _fast

INIT = {
    "jsonrpc": "2.0",
    "id": 0,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "1"},
    },
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
CALL = {
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
    "params": {
        "name": "search_deepgram_knowledge_sources",
        "arguments": {"query": "x"},
    },
}


class _Lines:
    """Async lines, as the MCP SDK's stdio server reads stdin.

    EOF comes only once every request has its reply in ``stdout``, as a
    client would wait for its answers before closing the pipe.
    """

    def __init__(self, messages: tuple[dict[str, Any], ...], stdout: Any) -> None:
        self._lines = [json.dumps(m) + "\n" for m in messages]
        self._expected = sum(1 for m in messages if "id" in m)
        self._stdout = stdout

    def __aiter__(self) -> Any:
        return self._gen()

    async def _gen(self) -> Any:
        import anyio

        for line in self._lines:
            yield line
        with anyio.move_on_after(10):
            while len(self._stdout.getvalue().splitlines()) < self._expected:
                await anyio.sleep(0.01)


def _remote(monkeypatch: pytest.MonkeyPatch, *outcomes: Any) -> list[Any]:
    """Stand in for the remote: each call takes the next outcome."""
    calls: list[Any] = []
    pending = list(outcomes)

    async def fake_call_remote(
        base_url: str,
        api_key: str,
        name: str,
        arguments: dict[str, Any],
        *,
        debug: bool = False,
    ) -> Any:
        calls.append((base_url, api_key, name, arguments))
        outcome = pending.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(proxy, "call_remote", fake_call_remote)
    return calls


def _serve(*messages: dict[str, Any], api_key: Any = lambda: "fake-key") -> list[Any]:
    import anyio

    stdout = io.BytesIO()

    async def run() -> None:
        await proxy.serve(
            _Lines((INIT, INITIALIZED, *messages), stdout),
            proxy.LineWriter(stdout),
            base_url="https://api.dx.deepgram.com",
            api_key=api_key,
        )

    anyio.run(run)
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert replies[0]["id"] == 0
    return replies[1:]


def test_handshake_and_tool_list_are_local(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _remote(monkeypatch)
    stdout = io.BytesIO()
    import anyio

    async def run() -> None:
        await proxy.serve(
            _Lines((INIT, INITIALIZED, LIST), stdout),
            proxy.LineWriter(stdout),
            base_url="https://api.dx.deepgram.com",
            api_key=lambda: pytest.fail("no key is read before a call"),
        )

    anyio.run(run)
    init, listed = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert init["result"]["instructions"] == _fast.INSTRUCTIONS
    assert init["result"]["serverInfo"]["name"] == "deepgram-mcp"
    assert [t["name"] for t in listed["result"]["tools"]] == [
        "search_deepgram_knowledge_sources"
    ]
    assert (
        listed["result"]["tools"][0]["description"] == (_fast.TOOLS[0]["description"])
    )
    assert calls == []


def test_remote_result_comes_back_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    import mcp.types as types

    calls = _remote(
        monkeypatch,
        types.CallToolResult(
            content=[types.TextContent(type="text", text="rate limited")],
            structuredContent={"result": "rate limited"},
            isError=True,
            _meta={"remote": "kept"},
        ),
    )
    replies = _serve(CALL)
    assert replies[0]["result"] == {
        "_meta": {"remote": "kept"},
        "content": [{"type": "text", "text": "rate limited"}],
        "structuredContent": {"result": "rate limited"},
        "isError": True,
    }
    assert calls == [
        (
            "https://api.dx.deepgram.com",
            "fake-key",
            "search_deepgram_knowledge_sources",
            {"query": "x"},
        )
    ]


def _status_error(status: int) -> Exception:
    import httpx

    request = httpx.Request("POST", "https://api.dx.deepgram.com/kapa/mcp")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(str(status), request=request, response=response)


class _GroupError(Exception):
    """Shaped like an ExceptionGroup (3.11+; a backport on 3.10)."""

    def __init__(self, *exceptions: BaseException) -> None:
        super().__init__("unhandled errors in a TaskGroup")
        self.exceptions = exceptions


REACH = "dg mcp couldn't reach Deepgram's documentation search: "


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (
            _GroupError(ConnectionError("[Errno -3] Temporary failure in name resolution")),
            "[Errno -3] Temporary failure in name resolution",
        ),
        (
            _GroupError(_GroupError(_status_error(401))),
            "Deepgram refused the API key (HTTP 401).",
        ),
        (
            _status_error(429),
            "too many requests (HTTP 429). Try again in a minute.",
        ),
        (_status_error(502), "the service answered HTTP 502."),
        (
            _status_error(307),
            "https://api.dx.deepgram.com answered with a redirect to ?. dg mcp "
            "doesn't follow redirects, so nothing was sent there.",
        ),
        (proxy.RemoteTimeoutError(15.0), "no answer within 15 seconds."),
        (TimeoutError(), "no answer within 135 seconds."),
    ],
)
def test_remote_failure_is_a_tool_error_and_the_session_goes_on(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    reason: str,
) -> None:
    import mcp.types as types

    ok = types.CallToolResult(content=[types.TextContent(type="text", text="ok")])
    _remote(monkeypatch, error, ok)
    replies = _serve(CALL, dict(CALL, id=5))
    assert replies[0]["id"] == 3
    assert replies[0]["result"]["isError"] is True
    assert replies[0]["result"]["content"] == [{"type": "text", "text": REACH + reason}]
    # The next call connects again and succeeds.
    assert replies[1]["id"] == 5
    assert replies[1]["result"]["content"][0]["text"] == "ok"
    assert f"ERROR: dg mcp: {REACH}{reason}" in capsys.readouterr().err


def test_expected_failures_are_not_reported_as_bugs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reported: list[BaseException] = []
    monkeypatch.setattr(
        "deepctl_telemetry.report_exception", reported.append, raising=False
    )
    bug = ValueError("unexpected")
    _remote(monkeypatch, _status_error(401), bug)
    _serve(CALL, dict(CALL, id=5))
    assert reported == [bug]


def test_unknown_tool_is_refused_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _remote(monkeypatch)
    replies = _serve(dict(CALL, params={"name": "failing_tool", "arguments": {}}))
    assert replies[0]["result"]["isError"] is True
    assert replies[0]["result"]["content"][0]["text"] == (
        "Unknown tool: failing_tool. dg mcp has: search_deepgram_knowledge_sources."
    )
    assert calls == []


def test_bad_arguments_are_refused_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _remote(monkeypatch)
    replies = _serve(
        dict(
            CALL,
            params={"name": "search_deepgram_knowledge_sources", "arguments": {}},
        )
    )
    assert replies[0]["result"]["isError"] is True
    assert "query" in replies[0]["result"]["content"][0]["text"]
    assert calls == []


def test_missing_key_is_a_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _remote(monkeypatch)

    def no_key() -> str:
        raise RuntimeError(proxy.CANT_START.format(reason=proxy.NO_KEY))

    replies = _serve(CALL, api_key=no_key)
    assert replies[0]["result"]["isError"] is True
    assert replies[0]["result"]["content"][0]["text"] == (
        "dg mcp can't start the documentation search: No API key is set. Run dg "
        "login or set DEEPGRAM_API_KEY, then restart the MCP server."
    )
    assert calls == []


def test_reason_unwraps_exception_groups() -> None:
    group = _GroupError(_GroupError(OSError("connection refused\nmore detail")))
    assert proxy.reason(group) == "connection refused"


def test_http_client_follows_no_redirects_and_keeps_no_cookies() -> None:
    import asyncio

    client = proxy.http_client(headers={"Authorization": "Token k"})
    try:
        assert client.follow_redirects is False
        assert client.max_redirects == 0
        assert client.headers["Authorization"] == "Token k"
        assert not client.cookies.jar._policy.allowed_domains()
    finally:
        asyncio.run(client.aclose())


def _capture_server() -> tuple[Any, list[dict[str, str]]]:
    """A loopback HTTP server that answers 200 and records each request's
    headers (and the request line, under "_line")."""
    import http.server
    import threading

    seen: list[dict[str, str]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def _answer(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            seen.append({"_line": self.requestline, **dict(self.headers)})
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = do_POST = _answer

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, seen


@pytest.mark.timeout(30)
@pytest.mark.parametrize("variable", ["HTTP_PROXY", "http_proxy", "ALL_PROXY"])
def test_http_client_never_sends_the_key_to_an_environment_proxy(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    """An approved loopback MCP base is reached directly: HTTP_PROXY and
    friends never see the request or its Authorization header."""
    import asyncio

    target, to_target = _capture_server()
    trap, to_proxy = _capture_server()
    try:
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
            monkeypatch.delenv(name, raising=False)
            monkeypatch.delenv(name.lower(), raising=False)
        monkeypatch.setenv(variable, f"http://127.0.0.1:{trap.server_port}")
        monkeypatch.setenv("NO_PROXY", "")

        async def call() -> int:
            client = proxy.http_client(headers={"Authorization": "Token k"})
            try:
                url = f"http://127.0.0.1:{target.server_port}/v1/mcp"
                return (await client.post(url, json={})).status_code
            finally:
                await client.aclose()

        assert asyncio.run(call()) == 200
        assert to_proxy == []
        assert [r["Authorization"] for r in to_target] == ["Token k"]
    finally:
        target.shutdown()
        trap.shutdown()
