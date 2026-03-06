"""MCP proxy command for Deepgram — bridges remote MCP tools to local editors."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from typing import TYPE_CHECKING, Any

from deepctl_core import AuthManager, BaseCommand, Config, DeepgramClient
from rich.console import Console

from .models import MCPServerResult, TransportType

if TYPE_CHECKING:
    pass

console = Console()

DEFAULT_BASE_URL = "http://localhost:8080"


class McpCommand(BaseCommand):
    """MCP proxy that connects to Deepgram's developer tools."""

    name = "mcp"
    help = (
        "Run an MCP proxy that connects to Deepgram's developer API and "
        "exposes remote tools locally for AI editors"
    )
    short_help = "Run MCP proxy for Deepgram AI"

    requires_auth = True
    requires_project = False
    ci_friendly = True

    examples = [
        "dg mcp",
        "dg mcp --transport sse --port 8000",
        "dg mcp --base-url http://localhost:8080",
    ]
    agent_help = (
        "Run an MCP (Model Context Protocol) proxy that connects to Deepgram's "
        "developer API and exposes remote AI assistant tools locally. Configure "
        "in your AI editor's MCP settings. Supports stdio, SSE, and "
        "streamable-http transports."
    )

    def __init__(self) -> None:
        """Initialize the MCP command."""
        super().__init__()
        self._shutdown_requested = False
        self._original_sigint_handler: Any = None

    def get_arguments(self) -> list[dict[str, Any]]:
        """Get command arguments and options."""
        return [
            {
                "names": ["--transport", "-t"],
                "help": ("Transport mode: stdio (default) or sse"),
                "type": str,
                "default": "stdio",
                "required": False,
                "is_option": True,
            },
            {
                "names": ["--port", "-p"],
                "help": "Port number for HTTP server (default: 8000)",
                "type": int,
                "default": 8000,
                "required": False,
                "is_option": True,
            },
            {
                "names": ["--host"],
                "help": "Host address for HTTP server (default: 127.0.0.1)",
                "type": str,
                "default": "127.0.0.1",
                "required": False,
                "is_option": True,
            },
            {
                "names": ["--base-url"],
                "help": "Base URL for Deepgram developer API",
                "type": str,
                "default": DEFAULT_BASE_URL,
                "required": False,
                "is_option": True,
            },
            {
                "names": ["--debug"],
                "help": "Enable debug logging",
                "is_flag": True,
                "is_option": True,
            },
        ]

    def _handle_shutdown(self, signum: int, frame: Any) -> None:
        """Handle shutdown signals gracefully."""
        if not self._shutdown_requested:
            self._shutdown_requested = True
            console.print("\n[yellow]MCP proxy stopped by user[/yellow]")
            os._exit(0)

    def handle(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> Any:
        """Handle MCP proxy command."""
        transport = kwargs.get("transport", "stdio").lower()
        port = kwargs.get("port", 8000)
        host = kwargs.get("host", "127.0.0.1")
        api_key = auth_manager.get_api_key()
        assert api_key  # guaranteed by requires_auth = True
        base_url = kwargs.get("base_url", DEFAULT_BASE_URL)
        debug = kwargs.get("debug", False)

        valid_transports = ["stdio", "sse"]
        if transport not in valid_transports:
            console.print(
                f"[red]Invalid transport type:[/red] {transport}. "
                f"Must be one of: {', '.join(valid_transports)}"
            )
            return MCPServerResult(
                status="error", message=f"Invalid transport type: {transport}"
            )

        # Set up signal handling for graceful shutdown
        self._original_sigint_handler = signal.signal(
            signal.SIGINT, self._handle_shutdown
        )
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        try:
            if transport != "stdio":
                console.print(
                    f"[blue]Starting MCP proxy ({transport}) on {host}:{port}...[/blue]"
                )

            asyncio.run(
                run_proxy(
                    transport=transport,
                    host=host,
                    port=port,
                    api_key=api_key,
                    base_url=base_url,
                    debug=debug,
                )
            )

            return MCPServerResult(
                status="success",
                message="MCP proxy stopped",
                transport=TransportType(transport),
                port=port if transport != "stdio" else None,
                host=host if transport != "stdio" else None,
            )

        except KeyboardInterrupt:
            return MCPServerResult(
                status="cancelled",
                message="MCP proxy stopped by user",
                transport=TransportType(transport),
                port=port if transport != "stdio" else None,
                host=host if transport != "stdio" else None,
            )
        except Exception as e:
            msg = str(e)
            if hasattr(e, "exceptions"):
                for sub in e.exceptions:
                    msg += f"\n  Caused by: {sub!r}"
            console.print(f"[red]Error running MCP proxy:[/red] {msg}")
            return MCPServerResult(
                status="error",
                message=str(e),
                transport=TransportType(transport),
                port=port if transport != "stdio" else None,
                host=host if transport != "stdio" else None,
            )
        finally:
            if self._original_sigint_handler:
                signal.signal(signal.SIGINT, self._original_sigint_handler)


async def run_proxy(
    *,
    transport: str,
    host: str,
    port: int,
    api_key: str,
    base_url: str,
    debug: bool,
) -> None:
    """Connect to remote Deepgram MCP server and expose tools locally."""
    import mcp.server.stdio
    import mcp.types as types
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    from mcp.server.lowlevel import NotificationOptions, Server
    from mcp.server.models import InitializationOptions

    mcp_url = f"{base_url.rstrip('/')}/kapa/mcp"
    headers = {"Authorization": f"Token {api_key}"}

    if debug:
        print(f"[DEBUG] Connecting to {mcp_url}", file=sys.stderr)

    async with streamablehttp_client(url=mcp_url, headers=headers) as (
        read_stream,
        write_stream,
        _get_session_id,
    ):
        async with ClientSession(read_stream, write_stream) as remote:
            await remote.initialize()

            # Discover remote tools
            tools_result = await remote.list_tools()
            remote_tools: list[types.Tool] = tools_result.tools

            if debug:
                names = [t.name for t in remote_tools]
                print(f"[DEBUG] Remote tools: {names}", file=sys.stderr)

            # Create local server that proxies everything to the remote
            local = Server("Deepgram MCP")

            @local.list_tools()
            async def handle_list_tools() -> list[types.Tool]:
                return remote_tools

            @local.call_tool()
            async def handle_call_tool(
                name: str, arguments: dict[str, Any] | None
            ) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
                result = await remote.call_tool(name, arguments or {})
                return result.content  # type: ignore[return-value]

            init_options = InitializationOptions(
                server_name="deepgram-mcp",
                server_version="0.1.10",
                capabilities=local.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            )

            if transport == "stdio":
                async with mcp.server.stdio.stdio_server() as (srv_r, srv_w):
                    await local.run(srv_r, srv_w, init_options)
            elif transport == "sse":
                await _run_sse_server(local, init_options, host, port)


async def _run_sse_server(
    server: Any,
    init_options: Any,
    host: str,
    port: int,
) -> None:
    """Run local MCP server with SSE transport."""
    import uvicorn
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.routing import Route

    sse = SseServerTransport("/messages")

    async def handle_sse(request: Any) -> None:
        async with sse.connect_sse(request.scope, request.receive, request.send) as (
            read,
            write,
        ):
            await server.run(read, write, init_options)

    app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Route(
                "/messages",
                endpoint=sse.handle_post_message,
                methods=["POST"],
            ),
        ]
    )

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    srv = uvicorn.Server(config)
    await srv.serve()
