"""MCP proxy command for Deepgram — bridges remote MCP tools to local editors."""

from __future__ import annotations

import asyncio
import os
import signal
from typing import TYPE_CHECKING, Any

from deepctl_core import AuthManager, BaseCommand, Config, DeepgramClient, output
from deepctl_core.hosts import (
    DX_API_HOSTS,
    SOURCE_FLAG,
    CredentialHostError,
    canonical_url,
    evaluate,
    report,
)
from deepctl_core.messages import display
from deepctl_core.output import stderr_console
from deepgram_mcp import TransportType, run_proxy
from deepgram_mcp.proxy import DEFAULT_BASE_URL
from rich.markup import escape

from . import messages as mcp_msg
from .models import MCPServerResult

if TYPE_CHECKING:
    pass

# Use stderr for all output: when `dg mcp --transport stdio` is invoked by an
# MCP host (Claude Desktop, Codex, etc.), stdout is the JSON-RPC protocol
# channel and must not receive any human-readable messages.
console = stderr_console


def _report_error(exc: Exception) -> None:
    """Send a telemetry error report if telemetry is on for this run.

    The proxy turns failures into an error result instead of raising, so
    they never reach main()'s error path. Reporting writes nothing to
    stdout, which the MCP host owns in stdio mode.
    """
    try:
        from deepctl_telemetry import report_exception

        report_exception(exc)
    except Exception:
        pass


def _refuse_transport(transport: str) -> None:
    """Report an unsupported --transport on stderr and exit 1.

    stdout stays empty: an MCP host may be reading it as the protocol channel.
    """
    text = mcp_msg.E_MCP_TRANSPORT.format(transport=display(transport))
    output.print_error(escape(text))
    output.print_hint(escape(mcp_msg.E_MCP_TRANSPORT_HINT))
    raise SystemExit(1)


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
    stdout_is_protocol = True

    examples = [
        "dg mcp",
        "dg mcp --base-url https://api.dx.deepgram.com",
    ]
    agent_help = (
        "Run an MCP (Model Context Protocol) proxy that connects to Deepgram's "
        "developer API and exposes remote AI assistant tools locally. Configure "
        "in your AI editor's MCP settings. It runs over stdio."
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
                "help": "Transport: stdio (the only one supported)",
                "type": str,
                "default": "stdio",
                "required": False,
                "is_option": True,
            },
            # --port and --host stay accepted so existing MCP configs that
            # pass them still parse; only stdio runs, so they are unused.
            {
                "names": ["--port", "-p"],
                "help": "Ignored: dg mcp runs only over stdio.",
                "type": int,
                "default": 8000,
                "required": False,
                "is_option": True,
            },
            {
                "names": ["--host"],
                "help": "Ignored: dg mcp runs only over stdio.",
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

    def pre_auth_check(self, config: Config, **kwargs: Any) -> Any | None:
        """Refuse a transport other than stdio before the credential check,
        so a refused run sends no request and starts no listener."""
        transport = (kwargs.get("transport") or "stdio").lower()
        if transport != "stdio":
            _refuse_transport(transport)
        return None

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
        transport = (kwargs.get("transport") or "stdio").lower()
        base_url = kwargs.get("base_url") or DEFAULT_BASE_URL

        # Only stdio runs. pre_auth_check() refuses other transports before
        # the credential check; this repeats it for direct callers, before
        # the key is read and before anything listens.
        if transport != "stdio":
            _refuse_transport(transport)

        # The proxy sends the key to --base-url, so that host must be the
        # Deepgram developer API or one the user approved in their config.
        # Refuse at startup, on stderr only: stdout is the protocol channel.
        if base_url != DEFAULT_BASE_URL:
            try:
                evaluate(
                    base_url,
                    SOURCE_FLAG,
                    config.allowed_hosts(),
                    trusted_hosts=DX_API_HOSTS,
                )
            except CredentialHostError as err:
                report(err)
                raise SystemExit(1) from err

        api_key = auth_manager.get_api_key()
        assert api_key  # guaranteed by requires_auth = True
        debug = kwargs.get("debug", False)

        # Set up signal handling for graceful shutdown
        self._original_sigint_handler = signal.signal(
            signal.SIGINT, self._handle_shutdown
        )
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        try:
            asyncio.run(
                run_proxy(
                    transport="stdio",
                    host="127.0.0.1",
                    port=0,
                    api_key=api_key,
                    base_url=canonical_url(base_url),
                    debug=debug,
                )
            )

            # The MCP host owns stdout (JSON-RPC channel) and typically
            # closes it on disconnect, so we return None to skip
            # output_result rather than risk a write to a closed stream.
            return None

        except KeyboardInterrupt:
            return MCPServerResult(
                status="cancelled",
                message="MCP proxy stopped by user",
                transport=TransportType.STDIO,
            )
        except Exception as e:
            _report_error(e)
            msg = str(e)
            if hasattr(e, "exceptions"):
                for sub in e.exceptions:
                    msg += f"\n  Caused by: {sub!r}"
            console.print(f"[red]Error running MCP proxy:[/red] {msg}")
            return MCPServerResult(
                status="error",
                message=str(e),
                transport=TransportType.STDIO,
            )
        finally:
            if self._original_sigint_handler:
                signal.signal(signal.SIGINT, self._original_sigint_handler)
