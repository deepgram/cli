"""``dg mcp`` over stdio: the handshake answered locally, stdlib only.

An MCP host spawns ``dg mcp`` and waits for the ``initialize`` result before
it does anything else, so that answer must not wait for Click, the
``deepctl_core`` imports or a connection to Deepgram's MCP service. This
module answers ``initialize``, ``ping`` and ``tools/list`` from fixed local
data, using only ``sys``, ``os`` and ``json`` (the import allowlist in
``bench/gates.json``).

The first ``tools/call`` after ``initialize`` goes to
``deepctl_cmd_mcp.serve_after_handshake``, which serves the rest of the
session and connects to the service once per tool call. When it can't
start (no API key, unreadable config), the call gets a tool error and this
loop keeps serving, so the host never sees the server die. Every other
method, ``server/discover`` included, gets "Method not found" here and
never causes a connection; a ``tools/call`` before ``initialize`` gets
"not initialized".

Contract with ``deepctl_cmd_mcp.serve_after_handshake(initialize_params,
pending_message, stdin, stdout)``: ``stdin``/``stdout`` are the binary
streams this loop reads and writes (with whatever is still buffered); it
returns the process exit code when the session ends, and raises an
``Exception`` only if it could not start, before it has read stdin or
written stdout. Its message is the complete text shown to the client.

Nothing but JSON-RPC is ever written to stdout. Diagnostics go to stderr.
``isolate_stdout()`` (called by ``deepctl.main`` for every ``dg mcp`` run)
keeps it that way for code that doesn't know about the protocol: fd 1 then
points at stderr, and the protocol stream is a private copy of the
original stdout.
"""

import json
import os
import sys

TYPE_CHECKING = False
if TYPE_CHECKING:
    from typing import Any, BinaryIO

# Protocol versions this server (and the proxy it hands off to) accepts; a
# client asking for one gets it back, any other gets the newest.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]

SERVER_NAME = "deepgram-mcp"

# The original stdout, once isolate_stdout() moved fd 1 to stderr.
_PROTOCOL_OUT: "BinaryIO | None" = None

# Fixed local text, never passed through from the remote service: its
# instructions and tool descriptions are text an attacker could influence.
INSTRUCTIONS = (
    "Deepgram developer documentation search. Call "
    "search_deepgram_knowledge_sources with a question about Deepgram's "
    "speech-to-text, text-to-speech, Voice Agent or audio intelligence APIs, "
    "SDKs or CLI. Results are passages from Deepgram's documentation, each "
    "with its source URL; cite the URL when you use a passage."
)

# The tool names are the remote service's (kapa's); the descriptions and
# schemas are ours. deepctl_cmd_mcp.proxy serves this same table and
# forwards calls only for the names in it.
TOOLS = [
    {
        "name": "search_deepgram_knowledge_sources",
        "title": "Search Deepgram documentation",
        "description": (
            "Search Deepgram's documentation, API reference and guides. "
            "Returns the most relevant passages, each with its source URL."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A question or search terms, in natural language.",
                }
            },
            "required": ["query"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
    }
]

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603

# Shown when the proxy package itself can't be imported.
CANT_START = "dg mcp can't start the documentation search: {reason}"


def isolate_stdout() -> None:
    """Give the protocol its own copy of stdout and point fd 1 at stderr.

    After this, a stray ``print()``, a library warning or a child process
    writing to fd 1 lands on stderr instead of corrupting the JSON-RPC
    stream; ``protocol_stdout()`` is the only way to the client. Safe to
    call twice. Does nothing when stdout or stderr is closed.
    """
    global _PROTOCOL_OUT
    if _PROTOCOL_OUT is not None:
        return
    try:
        sys.stdout.flush()
    except (AttributeError, OSError, ValueError):
        pass
    try:
        proto = os.dup(1)
    except OSError:
        return
    try:
        os.dup2(2, 1)
    except OSError:
        os.close(proto)
        return
    _PROTOCOL_OUT = os.fdopen(proto, "wb")


def protocol_stdout() -> "BinaryIO":
    """The stream the client reads: the isolated copy, else stdout."""
    if _PROTOCOL_OUT is not None:
        return _PROTOCOL_OUT
    return sys.stdout.buffer


def _write(out: "BinaryIO", message: "dict[str, Any]") -> None:
    out.write(json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n")
    out.flush()


def _result(out: "BinaryIO", msg_id: object, result: "dict[str, Any]") -> None:
    _write(out, {"jsonrpc": "2.0", "id": msg_id, "result": result})


def _error(out: "BinaryIO", msg_id: object, code: int, message: str) -> None:
    _write(
        out,
        {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}},
    )


def _initialize_result(params: "dict[str, Any]") -> "dict[str, Any]":
    from deepctl import __version__

    requested = params.get("protocolVersion")
    version = (
        requested
        if requested in SUPPORTED_PROTOCOL_VERSIONS
        else LATEST_PROTOCOL_VERSION
    )
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": __version__},
        "instructions": INSTRUCTIONS,
    }


def _hand_off_failed(out: "BinaryIO", message: "dict[str, Any]", text: str) -> None:
    """Answer the request that couldn't be handed off; the loop goes on."""
    sys.stderr.write(f"ERROR: {text}\n")
    sys.stderr.flush()
    if "id" not in message:
        return
    if message.get("method") == "tools/call":
        # A tool execution error, which the model gets to see (MCP spec).
        _result(
            out,
            message["id"],
            {"content": [{"type": "text", "text": text}], "isError": True},
        )
    else:
        _error(out, message["id"], INTERNAL_ERROR, text)


def _hand_off(
    initialize_params: "dict[str, Any]",
    message: "dict[str, Any]",
    inp: "BinaryIO",
    out: "BinaryIO",
) -> "int | None":
    """Run the rest of the session through the proxy; None if it can't start."""
    try:
        from deepctl_cmd_mcp import serve_after_handshake
    except ImportError as exc:
        _hand_off_failed(out, message, CANT_START.format(reason=exc))
        return None
    try:
        code: int = serve_after_handshake(initialize_params, message, inp, out)
    except Exception as exc:
        _hand_off_failed(out, message, str(exc) or type(exc).__name__)
        return None
    return code


def _silence(out: "BinaryIO") -> None:
    """The client closed stdout: drop further writes, including at exit."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, out.fileno())
        os.close(devnull)
    except (AttributeError, OSError, ValueError):
        pass


def serve(
    inp: "BinaryIO | None" = None,
    out: "BinaryIO | None" = None,
) -> int:
    """Serve one MCP stdio session; return the process exit code."""
    if inp is None:
        if sys.stdin is None:
            return 0
        inp = sys.stdin.buffer
    if out is None:
        out = protocol_stdout()

    initialize_params: dict[str, Any] | None = None
    msg_id: object = None
    try:
        while True:
            msg_id = None
            line = inp.readline()
            if not line:
                return 0  # the client closed stdin
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except ValueError:
                _error(out, None, PARSE_ERROR, "Parse error")
                continue
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                _error(out, None, INVALID_REQUEST, "Invalid Request")
                continue
            method = message.get("method")
            if not isinstance(method, str):
                continue  # a response to a request we never sent
            if "id" not in message:
                continue  # notifications/initialized, cancelled, ...: nothing to do
            msg_id = message["id"]

            if method == "initialize":
                params = message.get("params")
                initialize_params = params if isinstance(params, dict) else {}
                _result(out, msg_id, _initialize_result(initialize_params))
            elif method == "ping":
                _result(out, msg_id, {})
            elif method == "tools/list":
                _result(out, msg_id, {"tools": TOOLS})
            elif method != "tools/call":
                # server/discover, resources/list, ...: nothing to hand off.
                _error(out, msg_id, METHOD_NOT_FOUND, f"Method not found: {method}")
            elif initialize_params is None:
                _error(
                    out,
                    msg_id,
                    INVALID_REQUEST,
                    "Not initialized: send initialize first",
                )
            else:
                code = _hand_off(initialize_params, message, inp, out)
                if code is not None:
                    return code
    except KeyboardInterrupt:
        return 0
    except BrokenPipeError:
        _silence(out)
        return 0
    except Exception as exc:
        sys.stderr.write(f"ERROR: dg mcp stopped: {exc}\n")
        sys.stderr.flush()
        try:
            _error(out, msg_id, INTERNAL_ERROR, "Internal error")
        except Exception:
            pass
        return 1
