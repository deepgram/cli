"""serve_after_handshake: the fast path's hand-off to the proxy."""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

import deepctl_cmd_mcp.handoff as handoff

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


async def _echo_serve(stdin: Any, stdout: Any, **kwargs: Any) -> None:
    """Stands in for proxy.serve: a stdio server answering every request."""
    import mcp.server.stdio
    import mcp.types as types
    from mcp.shared.message import SessionMessage

    assert kwargs["api_key"]() == "fake-key"
    async with mcp.server.stdio.stdio_server(stdin=stdin, stdout=stdout) as (
        read,
        write,
    ):
        async for item in read:
            root = item.message.root
            if isinstance(root, types.JSONRPCRequest):
                reply = types.JSONRPCResponse(
                    jsonrpc="2.0", id=root.id, result={"echo": root.method}
                )
                await write.send(SessionMessage(types.JSONRPCMessage(reply)))
        await write.aclose()


def test_replays_the_handshake_and_drops_its_reply(
    key: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import deepctl_cmd_mcp.proxy as proxy

    monkeypatch.setattr(proxy, "serve", _echo_serve)
    stdin = io.BytesIO(b'{"jsonrpc":"2.0","id":4,"method":"ping"}\n')
    stdout = io.BytesIO()
    code = handoff.serve_after_handshake(INIT_PARAMS, CALL, stdin, stdout)
    assert code == 0
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    # The replayed initialize's reply is dropped; the client sees only its own.
    assert replies == [
        {"jsonrpc": "2.0", "id": 3, "result": {"echo": "tools/call"}},
        {"jsonrpc": "2.0", "id": 4, "result": {"echo": "ping"}},
    ]


def test_connection_failure_raises_before_touching_streams(
    key: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import deepctl_cmd_mcp.proxy as proxy

    async def offline(stdin: Any, stdout: Any, **kwargs: Any) -> None:
        raise ConnectionError("[Errno -3] Temporary failure in name resolution")

    monkeypatch.setattr(proxy, "serve", offline)
    stdin = io.BytesIO(b'{"jsonrpc":"2.0","id":4,"method":"ping"}\n')
    stdout = io.BytesIO()
    with pytest.raises(
        RuntimeError,
        match="^dg mcp couldn't reach Deepgram's documentation search: .*name resolution",
    ):
        handoff.serve_after_handshake(INIT_PARAMS, CALL, stdin, stdout)
    assert stdin.tell() == 0
    assert stdout.getvalue() == b""


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


class _Group(Exception):
    """Shaped like an ExceptionGroup (3.11+; a backport on 3.10)."""

    def __init__(self, *exceptions: BaseException) -> None:
        super().__init__("unhandled errors in a TaskGroup")
        self.exceptions = exceptions


def test_reason_unwraps_exception_groups() -> None:
    group = _Group(_Group(OSError("connection refused\nmore detail")))
    assert handoff._reason(group) == "connection refused"
