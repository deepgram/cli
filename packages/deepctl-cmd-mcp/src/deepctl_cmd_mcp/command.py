"""MCP proxy command for Deepgram — bridges remote MCP tools to local editors."""

from __future__ import annotations

import asyncio
import os
import signal
from typing import TYPE_CHECKING, Any

from deepctl_core import AuthManager, BaseCommand, Config, DeepgramClient
from deepctl_core.output import stderr_console
from deepgram_mcp import TransportType, run_proxy
from deepgram_mcp.proxy import DEFAULT_BASE_URL

from .models import MCPServerResult

if TYPE_CHECKING:
    pass

# Use stderr for all output: when `dg mcp --transport stdio` is invoked by an
# MCP host (Claude Desktop, Codex, etc.), stdout is the JSON-RPC protocol
# channel and must not receive any human-readable messages.
console = stderr_console


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
        "dg mcp --base-url https://api.dx.deepgram.com",
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

            if transport == "stdio":
                # The MCP host owns stdout (JSON-RPC channel) and typically
                # closes it on disconnect, so we return None to skip
                # output_result rather than risk a write to a closed stream.
                return None

            return MCPServerResult(
                status="success",
                message="MCP proxy stopped",
                transport=TransportType(transport),
                port=port,
                host=host,
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
