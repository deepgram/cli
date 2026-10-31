"""``dg mcp`` over stdio: the handshake answered locally, stdlib only.

An MCP host spawns ``dg mcp`` and waits for the ``initialize`` result before
it does anything else, so that answer must not wait for Click, the
``deepctl_core`` imports or a connection to Deepgram's MCP service. This
module answers ``initialize``, ``ping`` and ``tools/list`` from fixed local
data, using only ``sys``, ``os`` and ``json`` (the import allowlist in
``bench/gates.json``).

The first ``tools/call`` after ``initialize`` goes to
``deepctl_cmd_mcp.serve_after_handshake``, which connects to the service
and serves the rest of the session. When that can't start (no API key, no
network), the call gets a tool error and this loop keeps serving, so the
host never sees the server die. Every other method, ``server/discover``
included, gets "Method not found" here and never causes a connection; a
``tools/call`` before ``initialize`` gets "not initialized".

Contract with ``deepctl_cmd_mcp.serve_after_handshake(initialize_params,
pending_message, stdin, stdout)``: ``stdin``/``stdout`` are the binary
streams this loop reads and writes (with whatever is still buffered); it
returns the process exit code when the session ends, and raises an
``Exception`` only if it could not start, before it has read stdin or
written stdout. Its message is the complete text shown to the client.

Nothing but JSON-RPC is ever written to stdout. Diagnostics go to stderr.
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

# Fixed local text, never passed through from the remote service
# (SECURITY-S6). W4.1 owns this text and the tool table below.
INSTRUCTIONS = (
    "Deepgram developer documentation search. Call "
    "search_deepgram_knowledge_sources with a question about Deepgram's "
    "speech-to-text, text-to-speech, Voice Agent or audio intelligence APIs, "
    "SDKs or CLI. Results are passages from Deepgram's documentation, each "
    "with its source URL; cite the URL when you use a passage."
)

# The tool names are the remote service's (kapa's); the descriptions and
# schemas are ours. W4.1 keeps this table in sync with the service.
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


def _silence_stdout() -> None:
    """The client closed stdout: drop further writes, including at exit."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        os.close(devnull)
    except (OSError, ValueError):
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
        out = sys.stdout.buffer

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
        _silence_stdout()
        return 0
    except Exception as exc:
        sys.stderr.write(f"ERROR: dg mcp stopped: {exc}\n")
        sys.stderr.flush()
        try:
            _error(out, msg_id, INTERNAL_ERROR, "Internal error")
        except Exception:
            pass
        return 1
