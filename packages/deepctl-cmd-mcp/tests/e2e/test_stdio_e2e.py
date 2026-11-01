"""`dg mcp` end to end over stdio, against a fake remote on loopback.

Each test starts the real CLI (`dg mcp --base-url ...`) and
drives it with the official MCP Python SDK client, or with raw JSON-RPC
lines when the test counts stdout bytes. The fake remote
(``fake_remote.py``) stands in for Deepgram's documentation-search service;
nothing leaves 127.0.0.1 and the keys are fakes.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX config file modes; fake remote on loopback"
)

FAKE = Path(__file__).with_name("fake_remote.py")
GOOD_KEY = "fa1ce0000000000000000000000000000000000d"
BAD_KEY = "fa1ce00000000000000000000000000000000bad"
SEARCH = "search_deepgram_knowledge_sources"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for(port: int, proc: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("the fake remote exited")
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("the fake remote did not start")


@pytest.fixture
def remote(request: pytest.FixtureRequest, tmp_path: Path) -> Any:
    """Start the fake remote; returns its base URL.

    Its request log is ``tmp_path / "remote.log"``.
    """
    port = _free_port()
    env = dict(
        os.environ, FAKE_REMOTE_KEY=GOOD_KEY, FAKE_REMOTE_LOG=str(_log(tmp_path))
    )
    param = getattr(request, "param", False)
    if param is True:
        env["FAKE_REMOTE_STRUCT"] = "1"
    elif param == "same-origin":
        env["FAKE_REMOTE_REDIRECT"] = f"http://127.0.0.1:{port}/followed"
    elif isinstance(param, str):
        env["FAKE_REMOTE_REDIRECT"] = param
    proc = subprocess.Popen(
        [sys.executable, str(FAKE), str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for(port, proc)
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        proc.wait(10)


def _log(tmp_path: Path) -> Path:
    return tmp_path / "remote.log"


def _requests(tmp_path: Path) -> list[str]:
    path = _log(tmp_path)
    return path.read_text().splitlines() if path.exists() else []


def _env(home: Path, base_url: str, key: str | None) -> dict[str, str]:
    """A clean environment whose user config approves the fake host."""
    from deepctl_core.output import AI_AGENT_ENV_MARKERS

    config = home / ".config" / "deepctl"
    config.mkdir(parents=True, mode=0o700, exist_ok=True)
    (home / ".config").chmod(0o700)
    host = base_url.split("://", 1)[1]
    path = config / "config.yaml"
    path.write_text(f'api:\n  allowed_hosts:\n    - "{host}"\n')
    path.chmod(0o600)
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("DEEPGRAM_") and k not in AI_AGENT_ENV_MARKERS
    }
    env.update(
        HOME=str(home),
        XDG_CONFIG_HOME=str(home / ".config"),
        DEEPCTL_TELEMETRY_DISABLED="1",
        DO_NOT_TRACK="1",
    )
    if key is not None:
        env["DEEPGRAM_API_KEY"] = key
    return env


DG = [sys.executable, "-c", "from deepctl.main import main; main()"]


def _command(base_url: str, *extra: str) -> list[str]:
    return [*DG, "mcp", "--base-url", base_url, *extra]


def _with_session(base_url: str, env: dict[str, str], body: Any) -> Any:
    """Run ``body(session, init)`` against `dg mcp` via the SDK client."""
    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    command = _command(base_url)
    params = StdioServerParameters(command=command[0], args=command[1:], env=env)

    async def run() -> Any:
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            with anyio.fail_after(60):
                init = await session.initialize()
                return await body(session, init)

    return anyio.run(run)


def _raw(base_url: str, env: dict[str, str], *extra: str) -> tuple[list[Any], int, str]:
    """Handshake, list and one call as raw lines; every stdout line parsed.

    Returns (replies, exit code after stdin closes, stderr). A stdout line
    that isn't a JSON-RPC message fails the test.
    """
    proc = subprocess.Popen(
        _command(base_url, *extra),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin and proc.stdout and proc.stderr
    lines: list[bytes] = []
    reader = threading.Thread(target=lambda: lines.extend(proc.stdout), daemon=True)  # type: ignore[union-attr,arg-type]
    reader.start()
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "raw", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": SEARCH, "arguments": {"query": "flux"}},
        },
    ]
    for message in messages:
        proc.stdin.write(json.dumps(message).encode() + b"\n")
        proc.stdin.flush()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and len(lines) < 3 and proc.poll() is None:
        time.sleep(0.05)
    assert proc.poll() is None, "dg mcp exited during the session"
    proc.stdin.close()
    code = proc.wait(30)
    reader.join(5)
    stderr = proc.stderr.read().decode()
    replies = []
    for line in lines:
        reply = json.loads(line)  # raises on a non-JSON stdout line
        assert reply["jsonrpc"] == "2.0"
        replies.append(reply)
    return replies, code, stderr


def test_handshake_and_tool_list_are_fixed_local_text(
    remote: str, tmp_path: Path
) -> None:
    from importlib.metadata import version

    from deepctl.mcp._fast import INSTRUCTIONS, TOOLS

    async def body(session: Any, init: Any) -> Any:
        return init, await session.list_tools()

    init, tools = _with_session(remote, _env(tmp_path, remote, GOOD_KEY), body)
    assert init.instructions == INSTRUCTIONS
    assert init.serverInfo.name == "deepgram-mcp"
    assert init.serverInfo.version == version("deepctl")
    listed = [t.model_dump(mode="json", exclude_none=True) for t in tools.tools]
    assert [(t["name"], t["description"]) for t in listed] == [
        (t["name"], t["description"]) for t in TOOLS
    ]
    assert "IGNORE" not in json.dumps(listed)
    assert "IGNORE" not in (init.instructions or "")
    # Answered locally: the remote saw nothing.
    assert _requests(tmp_path) == []


@pytest.mark.parametrize("remote", [False, True], indirect=True, ids=["text", "struct"])
def test_call_returns_the_remote_result_whole(
    remote: str, tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    async def body(session: Any, init: Any) -> Any:
        ok = await session.call_tool(SEARCH, {"query": "flux"})
        failed = await session.call_tool(SEARCH, {"query": "FAIL"})
        unknown = await session.call_tool("failing_tool", {})
        return ok, failed, unknown

    ok, failed, unknown = _with_session(remote, _env(tmp_path, remote, GOOD_KEY), body)
    assert ok.isError is False
    assert ok.content[0].text.startswith("Result for flux")
    if request.node.callspec.params["remote"]:
        # The remote declares an outputSchema; its structuredContent arrives.
        assert ok.structuredContent == {"result": ok.content[0].text}
    else:
        assert ok.structuredContent is None
    assert failed.isError is True
    assert "Rate limit exceeded" in failed.content[0].text
    assert unknown.isError is True
    assert unknown.content[0].text.startswith("Unknown tool: failing_tool.")


@pytest.mark.parametrize("extra", [(), ("--debug",)], ids=["plain", "debug"])
def test_refused_key_is_a_tool_error_and_stdout_stays_clean(
    remote: str, tmp_path: Path, extra: tuple[str, ...]
) -> None:
    replies, code, stderr = _raw(remote, _env(tmp_path, remote, BAD_KEY), *extra)
    assert [r["id"] for r in replies] == [0, 1, 2]
    assert replies[2]["result"]["isError"] is True
    assert replies[2]["result"]["content"][0]["text"] == (
        "dg mcp couldn't reach Deepgram's documentation search: Deepgram "
        "refused the API key (HTTP 401)."
    )
    assert code == 0
    assert BAD_KEY not in stderr
    assert GOOD_KEY not in stderr


def test_remote_down_is_a_tool_error(tmp_path: Path) -> None:
    url = f"http://127.0.0.1:{_free_port()}"  # nothing listens here
    replies, code, stderr = _raw(url, _env(tmp_path, url, GOOD_KEY))
    assert [r["id"] for r in replies] == [0, 1, 2]
    assert replies[2]["result"]["isError"] is True
    assert replies[2]["result"]["content"][0]["text"].startswith(
        "dg mcp couldn't reach Deepgram's documentation search: "
    )
    assert code == 0
    assert GOOD_KEY not in stderr


def test_no_key_is_a_tool_error(remote: str, tmp_path: Path) -> None:
    replies, code, _ = _raw(remote, _env(tmp_path, remote, None))
    assert replies[0]["result"]["serverInfo"]["name"] == "deepgram-mcp"
    assert replies[2]["result"]["isError"] is True
    assert replies[2]["result"]["content"][0]["text"] == (
        "dg mcp can't start the documentation search: No API key is set. Run dg "
        "login or set DEEPGRAM_API_KEY, then restart the MCP server."
    )
    assert code == 0


@pytest.mark.parametrize(
    "argv",
    [["mcp"], ["mcp", "--debug"], ["--profile", "default", "mcp"]],
    ids=["plain", "debug", "root-option-then-mcp"],
)
def test_exits_on_eof_without_connecting(tmp_path: Path, argv: list[str]) -> None:
    """Handshake, list, EOF: exits at once and never opens a connection.

    The listener stands in for the remote: nothing may connect to it before
    a tool is called.
    """
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        listener.settimeout(0)
        url = "http://127.0.0.1:%d" % listener.getsockname()[1]
        env = _env(tmp_path, url, GOOD_KEY)
        session = (
            b'{"jsonrpc":"2.0","id":0,"method":"initialize","params":'
            b'{"protocolVersion":"2025-06-18","capabilities":{},'
            b'"clientInfo":{"name":"t","version":"0"}}}\n'
            b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
            b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n'
        )
        args = [*argv, "--base-url", url]
        started = time.monotonic()
        proc = subprocess.run(
            [*DG, *args],
            input=session,
            env=env,
            capture_output=True,
            timeout=30,
        )
        elapsed = time.monotonic() - started
        try:
            listener.accept()
            connected = True
        except (BlockingIOError, OSError):
            connected = False
    assert proc.returncode == 0, proc.stderr
    assert elapsed < 5
    assert not connected
    replies = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [r["id"] for r in replies] == [0, 1]


class _Sink:
    """A listener on another origin that counts every connection to it."""

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = int(self.sock.getsockname()[1])
        self.connections = 0
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.connections += 1
            conn.close()

    def close(self) -> None:
        self.sock.close()


_SINK = _Sink()


@pytest.mark.parametrize(
    "remote",
    [f"http://127.0.0.1:{_SINK.port}/kapa/mcp"],
    indirect=True,
    ids=["307-to-other-origin"],
)
def test_redirect_is_a_tool_error_and_never_followed(
    remote: str, tmp_path: Path
) -> None:
    """A 3xx from the approved host is refused: the key stays on that host."""
    from importlib.metadata import version

    before = _SINK.connections

    async def body(session: Any, init: Any) -> Any:
        first = await session.call_tool(SEARCH, {"query": "flux"})
        second = await session.call_tool(SEARCH, {"query": "again"})
        return first, second

    first, second = _with_session(remote, _env(tmp_path, remote, GOOD_KEY), body)
    for result in (first, second):
        assert result.isError is True
        assert result.content[0].text == (
            "dg mcp couldn't reach Deepgram's documentation search: "
            f"{remote} answered with a redirect to http://127.0.0.1:{_SINK.port}. "
            "dg mcp doesn't follow redirects, so nothing was sent there."
        ), version("mcp")
    time.sleep(0.2)
    assert _SINK.connections == before


@pytest.mark.parametrize("remote", ["same-origin"], indirect=True)
def test_same_origin_redirect_is_not_followed(remote: str, tmp_path: Path) -> None:
    """Even a redirect within the approved origin is a tool error."""

    async def body(session: Any, init: Any) -> Any:
        return await session.call_tool(SEARCH, {"query": "flux"})

    result = _with_session(remote, _env(tmp_path, remote, GOOD_KEY), body)
    assert result.isError is True
    assert "answered with a redirect" in result.content[0].text
    seen = _requests(tmp_path)
    assert seen and all(line.endswith(" /kapa/mcp") for line in seen), seen
