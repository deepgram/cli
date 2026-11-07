"""serve_after_handshake: the fast path's hand-off to the local MCP server."""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

import deepctl_cmd_mcp.handoff as handoff
import deepctl_cmd_mcp.proxy as proxy

INIT_PARAMS = {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "t", "version": "1"},
}
CALL = {
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
    "params": {
        "name": "search_deepgram_knowledge_sources",
        "arguments": {"query": "x"},
    },
}


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handoff, "_api_key", lambda: "fake-key")


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


def _serve(*lines: dict[str, Any], first: dict[str, Any] = CALL) -> list[Any]:
    stdin = io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in lines))
    stdout = io.BytesIO()
    assert handoff.serve_after_handshake(INIT_PARAMS, first, stdin, stdout) == 0
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def test_replays_the_handshake_and_drops_its_reply(
    key: None, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    replies = _serve({"jsonrpc": "2.0", "id": 4, "method": "ping"})
    # The replayed initialize's reply is dropped; the client sees only its own.
    assert [r["id"] for r in replies] == [3, 4]
    # The remote's result comes back whole.
    assert replies[0]["result"] == {
        "_meta": {"remote": "kept"},
        "content": [{"type": "text", "text": "rate limited"}],
        "structuredContent": {"result": "rate limited"},
        "isError": True,
    }
    assert replies[1]["result"] == {}
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


class _Group(Exception):
    """Shaped like an ExceptionGroup (3.11+; a backport on 3.10)."""

    def __init__(self, *exceptions: BaseException) -> None:
        super().__init__("unhandled errors in a TaskGroup")
        self.exceptions = exceptions


REACH = "dg mcp couldn't reach Deepgram's documentation search: "


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (
            _Group(ConnectionError("[Errno -3] Temporary failure in name resolution")),
            "[Errno -3] Temporary failure in name resolution",
        ),
        (
            _Group(_Group(_status_error(401))),
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
    key: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    reason: str,
) -> None:
    import mcp.types as types

    ok = types.CallToolResult(content=[types.TextContent(type="text", text="ok")])
    _remote(monkeypatch, error, ok)
    replies = _serve(dict(CALL, id=5))
    assert replies[0]["id"] == 3
    assert replies[0]["result"]["isError"] is True
    assert replies[0]["result"]["content"] == [{"type": "text", "text": REACH + reason}]
    # The next call connects again and succeeds.
    assert replies[1]["id"] == 5
    assert replies[1]["result"]["content"][0]["text"] == "ok"
    assert f"ERROR: dg mcp: {REACH}{reason}" in capsys.readouterr().err


def test_expected_failures_are_not_reported_as_bugs(
    key: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    reported: list[BaseException] = []
    monkeypatch.setattr(
        "deepctl_telemetry.report_exception", reported.append, raising=False
    )
    bug = ValueError("unexpected")
    _remote(monkeypatch, _status_error(401), bug)
    _serve(dict(CALL, id=5))
    assert reported == [bug]


def test_unknown_tool_is_refused_locally(
    key: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _remote(monkeypatch)
    call = dict(CALL, params={"name": "failing_tool", "arguments": {}})
    replies = _serve(first=call)
    assert replies[0]["result"]["isError"] is True
    assert replies[0]["result"]["content"][0]["text"] == (
        "Unknown tool: failing_tool. dg mcp has: search_deepgram_knowledge_sources."
    )
    assert calls == []


def test_bad_arguments_are_refused_locally(
    key: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _remote(monkeypatch)
    call = dict(
        CALL, params={"name": "search_deepgram_knowledge_sources", "arguments": {}}
    )
    replies = _serve(first=call)
    assert replies[0]["result"]["isError"] is True
    assert "query" in replies[0]["result"]["content"][0]["text"]
    assert calls == []


def test_no_api_key_is_a_start_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    import keyring

    monkeypatch.setattr(keyring, "get_password", lambda *a: None)
    with pytest.raises(RuntimeError) as exc:
        handoff.serve_after_handshake(INIT_PARAMS, CALL, io.BytesIO(), io.BytesIO())
    assert str(exc.value) == (
        "dg mcp can't start the documentation search: No API key is set. Run dg "
        "login or set DEEPGRAM_API_KEY, then restart the MCP server."
    )


def test_untrusted_config_key_sends_an_agent_to_a_person(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """0.4.1's W-PERM-AGENT rule: never tell an agent to run dg login."""
    import os
    import sys

    import yaml

    if sys.platform == "win32" or os.geteuid() == 0:
        pytest.skip("needs POSIX modes and a non-root user")
    from deepctl_core import messages as msg

    config_dir = tmp_path / ".config" / "deepctl"
    config_dir.mkdir(parents=True)
    path = config_dir / "config.yaml"
    path.write_text(
        yaml.safe_dump({"profiles": {"default": {"api_key": "sk-FILESENTINEL"}}})
    )
    path.chmod(0o666)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    import keyring

    monkeypatch.setattr(keyring, "get_password", lambda *a: None)
    with pytest.raises(RuntimeError) as exc:
        handoff.serve_after_handshake(INIT_PARAMS, CALL, io.BytesIO(), io.BytesIO())
    text = str(exc.value)
    assert text.endswith(msg.W_CFG_UNTRUSTED_FILE_HINT)
    assert "ask the user" in text and "dg login" not in text
    assert "sk-FILESENTINEL" not in text
    captured = capsys.readouterr()
    # The config warning was flushed to stderr, before any reply.
    assert captured.err.strip() and captured.out == ""


def test_reason_unwraps_exception_groups() -> None:
    group = _Group(_Group(OSError("connection refused\nmore detail")))
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
