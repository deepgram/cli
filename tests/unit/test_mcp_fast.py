"""``dg mcp``'s fast path: the handshake answered locally."""

from __future__ import annotations

import io
import json
import sys
import types
from typing import Any

import pytest

from deepctl import __version__
from deepctl.mcp import _fast


def _session(*messages: Any) -> io.BytesIO:
    return io.BytesIO(
        b"".join(
            (m if isinstance(m, bytes) else json.dumps(m).encode()) + b"\n"
            for m in messages
        )
    )


def _replies(out: io.BytesIO) -> list[dict[str, Any]]:
    lines = out.getvalue().splitlines()
    replies = [json.loads(line) for line in lines]
    assert all(r["jsonrpc"] == "2.0" for r in replies)
    return replies


def _init(version: str = "2025-06-18", msg_id: Any = 1) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": "initialize",
        "params": {
            "protocolVersion": version,
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "1"},
        },
    }


INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
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
def handoff(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """Replace deepctl_cmd_mcp with a stub recording the hand-off."""
    calls: list[tuple[Any, ...]] = []
    stub = types.ModuleType("deepctl_cmd_mcp")

    def serve_after_handshake(params: Any, message: Any, inp: Any, out: Any) -> int:
        calls.append((params, message, inp.read()))
        return 7

    stub.serve_after_handshake = serve_after_handshake  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "deepctl_cmd_mcp", stub)
    return calls


def test_handshake_and_tools_list_then_eof() -> None:
    out = io.BytesIO()
    inp = _session(
        _init(),
        INITIALIZED,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": "p", "method": "ping"},
    )
    assert _fast.serve(inp, out) == 0
    init, tools, ping = _replies(out)
    assert init["id"] == 1
    result = init["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["serverInfo"] == {"name": "deepgram-mcp", "version": __version__}
    assert result["instructions"] == _fast.INSTRUCTIONS
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert tools == {"jsonrpc": "2.0", "id": 2, "result": {"tools": _fast.TOOLS}}
    assert ping == {"jsonrpc": "2.0", "id": "p", "result": {}}


@pytest.mark.parametrize(
    ("asked", "answered"),
    [
        ("2024-11-05", "2024-11-05"),
        ("2025-11-25", "2025-11-25"),
        ("2099-01-01", _fast.LATEST_PROTOCOL_VERSION),
    ],
)
def test_protocol_version_negotiation(asked: str, answered: str) -> None:
    out = io.BytesIO()
    _fast.serve(_session(_init(asked)), out)
    assert _replies(out)[0]["result"]["protocolVersion"] == answered


def test_supported_versions_match_the_proxy() -> None:
    """The replayed initialize must negotiate the same version upstream."""
    shared = pytest.importorskip("mcp.shared.version")
    assert set(_fast.SUPPORTED_PROTOCOL_VERSIONS) == set(
        shared.SUPPORTED_PROTOCOL_VERSIONS
    )
    import mcp.types

    assert _fast.LATEST_PROTOCOL_VERSION == mcp.types.LATEST_PROTOCOL_VERSION


def test_bad_lines_get_json_rpc_errors() -> None:
    out = io.BytesIO()
    inp = _session(b"not json", b"[1, 2]", b"", _init())
    assert _fast.serve(inp, out) == 0
    parse, invalid, init = _replies(out)
    assert parse["error"]["code"] == -32700 and parse["id"] is None
    assert invalid["error"]["code"] == -32600
    assert init["id"] == 1


def test_first_call_hands_off_with_the_rest_of_stdin(
    handoff: list[tuple[Any, ...]],
) -> None:
    out = io.BytesIO()
    later = {"jsonrpc": "2.0", "id": 9, "method": "ping"}
    code = _fast.serve(_session(_init(), INITIALIZED, CALL, later), out)
    assert code == 7
    [(params, message, rest)] = handoff
    assert params == _init()["params"]
    assert message == CALL
    assert json.loads(rest) == later
    assert [r["id"] for r in _replies(out)] == [1]


def test_failed_hand_off_answers_and_keeps_serving(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stub = types.ModuleType("deepctl_cmd_mcp")
    text = "dg mcp couldn't reach Deepgram's documentation search: no network"

    def serve_after_handshake(*args: Any) -> int:
        raise RuntimeError(text)

    stub.serve_after_handshake = serve_after_handshake  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "deepctl_cmd_mcp", stub)
    out = io.BytesIO()
    ping = {"jsonrpc": "2.0", "id": 5, "method": "ping"}
    assert _fast.serve(_session(_init(), CALL, ping), out) == 0
    _, call, pong = _replies(out)
    # The adapter's text reaches the model unchanged, as a tool error.
    assert call["result"] == {
        "content": [{"type": "text", "text": text}],
        "isError": True,
    }
    assert pong["result"] == {}
    assert capsys.readouterr().err.splitlines() == [f"ERROR: {text}"]


@pytest.mark.parametrize(
    "method", ["server/discover", "resources/list", "prompts/list", "no/such"]
)
def test_other_methods_are_answered_locally_without_hand_off(
    handoff: list[tuple[Any, ...]], method: str
) -> None:
    """Only tools/call is handed off: nothing else may cause a connection."""
    out = io.BytesIO()
    request = {"jsonrpc": "2.0", "id": 7, "method": method, "params": {}}
    assert _fast.serve(_session(request, _init(), request), out) == 0
    before, _init_reply, after = _replies(out)
    for reply in (before, after):
        assert reply["id"] == 7
        assert reply["error"]["code"] == -32601
    assert handoff == []


def test_discover_first_then_legacy_handshake(handoff: list[tuple[Any, ...]]) -> None:
    """mcp 2.x clients probe server/discover first, then fall back to initialize."""
    out = io.BytesIO()
    discover = {"jsonrpc": "2.0", "id": 0, "method": "server/discover", "params": {}}
    inp = _session(
        discover,
        _init("2025-11-25", msg_id=1),
        INITIALIZED,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    )
    assert _fast.serve(inp, out) == 0
    disc, init, tools = _replies(out)
    assert disc["error"]["code"] == -32601
    assert init["result"]["instructions"] == _fast.INSTRUCTIONS
    assert init["result"]["serverInfo"]["name"] == "deepgram-mcp"
    assert tools["result"]["tools"] == _fast.TOOLS
    assert handoff == []


def test_tools_call_before_initialize_is_refused_locally(
    handoff: list[tuple[Any, ...]],
) -> None:
    out = io.BytesIO()
    assert _fast.serve(_session(CALL, _init(), CALL), out) == 7
    early, _init_reply = _replies(out)
    assert early["id"] == 3 and early["error"]["code"] == -32600
    [(params, message, _rest)] = handoff
    assert params == _init()["params"] and message == CALL


def test_missing_proxy_package_is_a_local_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "deepctl_cmd_mcp", None)
    out = io.BytesIO()
    assert _fast.serve(_session(_init(), CALL), out) == 0
    text = _replies(out)[1]["result"]["content"][0]["text"]
    assert text.startswith("dg mcp can't start the documentation search: ")


def test_unexpected_exception_answers_the_request_and_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(params: Any) -> Any:
        raise ValueError("bug")

    monkeypatch.setattr(_fast, "_initialize_result", broken)
    out = io.BytesIO()
    assert _fast.serve(_session(_init(msg_id=42)), out) == 1
    [reply] = _replies(out)
    assert reply["id"] == 42 and reply["error"]["code"] == -32603
    err = capsys.readouterr().err.splitlines()
    assert err == ["ERROR: dg mcp stopped: bug"]


def test_client_responses_and_notifications_get_no_reply() -> None:
    out = io.BytesIO()
    inp = _session(
        {"jsonrpc": "2.0", "id": 1, "result": {}},
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {}},
    )
    assert _fast.serve(inp, out) == 0
    assert out.getvalue() == b""


def test_tool_table_shape() -> None:
    for tool in _fast.TOOLS:
        assert set(tool) >= {"name", "description", "inputSchema"}
        assert tool["inputSchema"]["type"] == "object"


def test_isolate_stdout_leaves_only_the_protocol_on_stdout() -> None:
    """Stray prints and child processes land on stderr, not the client's stream."""
    import subprocess

    code = (
        "import os, subprocess, sys\n"
        "from deepctl.mcp import _fast\n"
        "_fast.isolate_stdout()\n"
        "_fast.isolate_stdout()\n"
        "print('stray print')\n"
        "sys.stdout.flush()\n"
        "subprocess.run([sys.executable, '-c', 'print(\"child\")'])\n"
        "out = _fast.protocol_stdout()\n"
        "out.write(b'{}\\n')\n"
        "out.flush()\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, timeout=60, check=True
    )
    assert proc.stdout == b"{}\n"
    assert b"stray print" in proc.stderr
    assert b"child" in proc.stderr


def test_serve_writes_to_the_isolated_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    out = io.BytesIO()
    monkeypatch.setattr(_fast, "_PROTOCOL_OUT", out)
    assert _fast.protocol_stdout() is out
    _fast.serve(_session(_init()))
    assert _replies(out)[0]["id"] == 1
