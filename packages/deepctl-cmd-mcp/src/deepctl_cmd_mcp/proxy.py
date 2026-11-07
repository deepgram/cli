"""The stdio MCP server behind ``dg mcp``: Deepgram documentation search.

Vendored from deepgram-mcp 0.1.1 (MIT, Deepgram) and reworked so a broken
remote can never break the local session:

- ``initialize`` and ``tools/list`` are answered from fixed local text
  (``INSTRUCTIONS`` and ``TOOLS`` below). The remote's instructions and
  tool descriptions are never passed through.
- Nothing connects until a ``tools/call``. Each call opens its own session
  to ``{base_url}/kapa/mcp``, so a remote that is down, refuses the key or
  restarted is an ``isError`` result for that call, and the next call tries
  again.
- The remote's ``CallToolResult`` is returned whole: ``content``,
  ``isError``, ``structuredContent`` and ``_meta``.
- The HTTP client follows no redirects, keeps no cookies and verifies
  TLS: a 3xx is a tool error, so the key never goes to another origin.

Callers decide which hosts may receive the key before they get here.
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta
from typing import TYPE_CHECKING, Any, BinaryIO

from deepctl_core.hosts import DEFAULT_DX_URL

if TYPE_CHECKING:
    from collections.abc import Callable

    import mcp.types as types

DEFAULT_BASE_URL = DEFAULT_DX_URL
REMOTE_PATH = "/kapa/mcp"

SERVER_NAME = "deepgram-mcp"

# Fixed local text, never passed through from the remote service: its
# instructions and tool descriptions are text an attacker could influence.
INSTRUCTIONS = (
    "Deepgram developer documentation search. Call "
    "search_deepgram_knowledge_sources with a question about Deepgram's "
    "speech-to-text, text-to-speech, Voice Agent or audio intelligence APIs, "
    "SDKs or CLI. Results are passages from Deepgram's documentation, each "
    "with its source URL; cite the URL when you use a passage."
)

# The tool names are the remote service's; the descriptions and schemas are
# ours. Calls are forwarded only for the names in this table.
TOOLS: list[dict[str, Any]] = [
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

# Opening the remote session (HTTP connect plus MCP initialize) and one call.
CONNECT_TIMEOUT_S = 15.0
CALL_TIMEOUT_S = 120.0

# The client sees these texts (as a tool error) and stderr gets them after
# "ERROR: dg mcp: ". A local cause (no key, unreadable config) never reads
# as Deepgram being down.
CANT_START = "dg mcp can't start the documentation search: {reason}"
CANT_REACH = "dg mcp couldn't reach Deepgram's documentation search: {reason}"
NO_KEY = (
    "No API key is set. Run dg login or set DEEPGRAM_API_KEY, then restart "
    "the MCP server."
)
KEY_REFUSED = "Deepgram refused the API key (HTTP {status})."
RATE_LIMITED = "too many requests (HTTP 429). Try again in a minute."
HTTP_STATUS = "the service answered HTTP {status}."
TIMED_OUT = "no answer within {seconds:g} seconds."
UNKNOWN_TOOL = "Unknown tool: {name}. dg mcp has: {names}."


class RemoteTimeoutError(TimeoutError):
    """The remote didn't answer within ``seconds``."""

    def __init__(self, seconds: float) -> None:
        super().__init__(f"no answer within {seconds:g} seconds")
        self.seconds = seconds


def remote_url(base_url: str) -> str:
    """The remote MCP endpoint for a (host-checked) base URL."""
    return base_url.rstrip("/") + REMOTE_PATH


def innermost(exc: BaseException) -> BaseException:
    """The first leaf of an exception group (anyio wraps transport errors)."""
    # ExceptionGroup is 3.11+; anyio raises its backport on 3.10.
    while getattr(exc, "exceptions", None):
        exc = exc.exceptions[0]  # type: ignore[attr-defined]
    return exc


def reason(exc: BaseException) -> str:
    """One line: the innermost cause of an (exception-group) failure."""
    leaf = innermost(exc)
    text = str(leaf).strip().splitlines()
    return text[0] if text else type(leaf).__name__


def failure_text(exc: BaseException) -> str:
    """What the client is told when a call to the remote fails."""
    import httpx

    leaf = innermost(exc)
    if isinstance(leaf, httpx.HTTPStatusError):
        status = leaf.response.status_code
        if 300 <= status < 400:
            # Core's wording for a refused REST redirect (origins only).
            from deepctl_core.websocket import redirect_refused

            refusal = redirect_refused(
                str(leaf.request.url),
                leaf.response.headers.get("location"),
                command="dg mcp",
                payload=None,
            )
            return CANT_REACH.format(reason=refusal.message)
        if status in (401, 403):
            return CANT_REACH.format(reason=KEY_REFUSED.format(status=status))
        if status == 429:
            return CANT_REACH.format(reason=RATE_LIMITED)
        return CANT_REACH.format(reason=HTTP_STATUS.format(status=status))
    if isinstance(leaf, TimeoutError):
        seconds = getattr(leaf, "seconds", CONNECT_TIMEOUT_S + CALL_TIMEOUT_S)
        return CANT_REACH.format(reason=TIMED_OUT.format(seconds=seconds))
    return CANT_REACH.format(reason=reason(exc))


def _expected(exc: BaseException) -> bool:
    """Network and service failures: the user's situation, not a bug."""
    import httpx

    leaf = innermost(exc)
    return isinstance(leaf, (httpx.HTTPError, OSError, TimeoutError)) or (
        type(leaf).__name__ == "McpError"
    )


def report_error(exc: BaseException) -> None:
    """Errors-only telemetry for failures that look like bugs."""
    if _expected(exc):
        return
    try:
        from deepctl_telemetry import report_exception

        report_exception(exc)
    except Exception:
        pass


def _error_result(text: str) -> types.CallToolResult:
    import mcp.types as types

    sys.stderr.write(f"ERROR: dg mcp: {text}\n")
    sys.stderr.flush()
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)], isError=True
    )


def http_client(
    headers: dict[str, str] | None = None,
    timeout: Any = None,
    auth: Any = None,
) -> Any:
    """The MCP SDK's ``httpx_client_factory``: no redirects, no cookies.

    A 3xx could take the key to another origin. The SDK's own factory
    follows every redirect before mcp 1.30, and 1.30 follows same-origin
    ones itself up to ``max_redirects``, so that is 0 too: any 3xx is an
    ``HTTPStatusError`` and a tool error. TLS is verified.
    """
    from http.cookiejar import DefaultCookiePolicy

    import httpx

    client = httpx.AsyncClient(
        headers=headers,
        timeout=timeout if timeout is not None else httpx.Timeout(CALL_TIMEOUT_S),
        auth=auth,
        follow_redirects=False,
        max_redirects=0,
        verify=True,
    )
    client.cookies.jar.set_policy(DefaultCookiePolicy(allowed_domains=[]))
    return client


def local_tools() -> list[types.Tool]:
    """The fixed local tool table, as the MCP SDK's models."""
    import mcp.types as types

    return [types.Tool.model_validate(tool) for tool in TOOLS]


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    for dist in ("deepctl", "deepctl-cmd-mcp"):
        try:
            return version(dist)
        except PackageNotFoundError:
            continue
    return "0"


async def call_remote(
    base_url: str,
    api_key: str,
    name: str,
    arguments: dict[str, Any],
    *,
    debug: bool = False,
) -> types.CallToolResult:
    """One tool call on a fresh remote session; raises when it fails."""
    import anyio
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    url = remote_url(base_url)
    if debug:
        sys.stderr.write(f"[DEBUG] Calling {name} at {url}\n")
    headers = {"Authorization": f"Token {api_key}"}
    try:
        with anyio.fail_after(CONNECT_TIMEOUT_S + CALL_TIMEOUT_S):
            async with (
                streamablehttp_client(
                    url=url,
                    headers=headers,
                    timeout=CONNECT_TIMEOUT_S,
                    httpx_client_factory=http_client,
                ) as (read_stream, write_stream, _session_id),
                ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=CALL_TIMEOUT_S),
                ) as remote,
            ):
                try:
                    with anyio.fail_after(CONNECT_TIMEOUT_S):
                        await remote.initialize()
                except TimeoutError as exc:
                    raise RemoteTimeoutError(CONNECT_TIMEOUT_S) from exc
                return await remote.call_tool(name, arguments)
    except TimeoutError as exc:
        if isinstance(exc, RemoteTimeoutError):
            raise
        raise RemoteTimeoutError(CONNECT_TIMEOUT_S + CALL_TIMEOUT_S) from exc


def build_server(
    *,
    base_url: str,
    api_key: Callable[[], str],
    debug: bool = False,
) -> tuple[Any, Any]:
    """The local MCP server and its initialization options.

    ``api_key`` returns the key, or raises ``RuntimeError`` whose message is
    the complete text for the client. It runs on each call, so a key set
    up after the server started (``dg login``) is picked up.
    """
    from mcp.server.lowlevel import NotificationOptions, Server
    from mcp.server.models import InitializationOptions

    tools = local_tools()
    names = [tool.name for tool in tools]
    server: Server[Any, Any] = Server(SERVER_NAME, instructions=INSTRUCTIONS)

    @server.list_tools()  # type: ignore[untyped-decorator,no-untyped-call,unused-ignore]
    async def handle_list_tools() -> list[types.Tool]:
        return tools

    @server.call_tool()  # type: ignore[untyped-decorator,no-untyped-call,unused-ignore]
    async def handle_call_tool(
        name: str, arguments: dict[str, Any] | None
    ) -> types.CallToolResult:
        if name not in names:
            return _error_result(UNKNOWN_TOOL.format(name=name, names=", ".join(names)))
        try:
            key = api_key()
        except RuntimeError as exc:
            return _error_result(str(exc))
        try:
            return await call_remote(base_url, key, name, arguments or {}, debug=debug)
        except Exception as exc:
            report_error(exc)
            return _error_result(failure_text(exc))

    options = InitializationOptions(
        server_name=SERVER_NAME,
        server_version=_version(),
        capabilities=server.get_capabilities(
            notification_options=NotificationOptions(),
            experimental_capabilities={},
        ),
        instructions=INSTRUCTIONS,
    )
    return server, options


class LineWriter:
    """The protocol stream for the MCP SDK's stdio server: UTF-8 lines."""

    def __init__(self, stdout: BinaryIO) -> None:
        self._stdout = stdout

    async def write(self, data: str) -> None:
        self._stdout.write(data.encode("utf-8"))

    async def flush(self) -> None:
        self._stdout.flush()


def isolate_stdout() -> BinaryIO:
    """Give the protocol its own copy of stdout and point fd 1 at stderr.

    After this, a stray ``print()``, a library warning or a child process
    writing to fd 1 lands on stderr instead of corrupting the JSON-RPC
    stream; the returned stream is the only way to the client. Falls back
    to ``sys.stdout.buffer`` when the descriptors can't be duplicated.
    """
    try:
        sys.stdout.flush()
    except (AttributeError, OSError, ValueError):
        pass
    try:
        proto = os.dup(1)
    except OSError:
        return sys.stdout.buffer
    try:
        os.dup2(2, 1)
    except OSError:
        os.close(proto)
        return sys.stdout.buffer
    return os.fdopen(proto, "wb")


async def serve(
    stdin: Any,
    stdout: Any,
    *,
    base_url: str,
    api_key: Callable[[], str],
    debug: bool = False,
) -> None:
    """Serve one MCP session over the given async line streams.

    ``stdin`` of ``None`` is the process's own stdin.
    """
    import mcp.server.stdio as stdio

    server, options = build_server(base_url=base_url, api_key=api_key, debug=debug)
    async with stdio.stdio_server(stdin=stdin, stdout=stdout) as (read, write):
        await server.run(read, write, options)


async def run_proxy(
    *,
    base_url: str,
    api_key: Callable[[], str],
    debug: bool = False,
    stdout: BinaryIO | None = None,
) -> None:
    """Serve the documentation search on stdin and ``stdout``.

    ``stdout`` is the protocol stream (``sys.stdout.buffer`` by default);
    stdin is the process's own.
    """
    out = stdout if stdout is not None else sys.stdout.buffer
    await serve(None, LineWriter(out), base_url=base_url, api_key=api_key, debug=debug)
