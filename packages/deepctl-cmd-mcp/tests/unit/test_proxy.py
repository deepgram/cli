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


def test_isolate_stdout_moves_fd1_to_stderr(tmp_path: Any) -> None:
    """In a child process: stray stdout writes land on stderr."""
    import subprocess
    import sys

    code = (
        "import sys\n"
        "from deepctl_cmd_mcp.proxy import isolate_stdout\n"
        "out = isolate_stdout()\n"
        "print('stray')\n"
        "sys.stdout.flush()\n"
        "out.write(b'protocol\\n'); out.flush()\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b"protocol\n"
    assert b"stray" in proc.stderr
