"""dg mcp: an MCP server that serves Deepgram documentation search over stdio."""

from __future__ import annotations

import os
import signal
import sys
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
from rich.markup import escape

from . import messages as mcp_msg
from .models import MCPServerResult, TransportType
from .proxy import (
    CANT_START,
    DEFAULT_BASE_URL,
    NO_KEY,
    isolate_stdout,
    reason,
    report_error,
    run_proxy,
)

if TYPE_CHECKING:
    pass


# asyncio (and with it ssl) loads only when the proxy runs, so `dg mcp
# --help` doesn't import it.
def __getattr__(name: str) -> Any:
    if name == "asyncio":
        import asyncio

        return asyncio
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Use stderr for all output: when `dg mcp --transport stdio` is invoked by an
# MCP host (Claude Desktop, Codex, etc.), stdout is the JSON-RPC protocol
# channel and must not receive any human-readable messages.
console = stderr_console


def key_or_error(config: Any, auth_manager: Any) -> str:
    """The API key, or RuntimeError with the complete text for the client."""
    from contextlib import redirect_stdout

    from deepctl_core import messages as msg

    try:
        with redirect_stdout(sys.stderr):
            api_key = auth_manager.get_api_key()
    except Exception as exc:
        raise RuntimeError(CANT_START.format(reason=reason(exc))) from exc
    if api_key:
        return str(api_key)
    if getattr(config, "api_key_untrusted", False) is True:
        # The config file's key was left unused (W-PERM-AGENT /
        # W-CFG-UNTRUSTED): send the agent to a person, never to dg login.
        raise RuntimeError(
            msg.E_KEY_UNTRUSTED.format(user_config=msg.display_path(config.config_path))
            + " "
            + msg.W_CFG_UNTRUSTED_FILE_HINT
        )
    raise RuntimeError(CANT_START.format(reason=NO_KEY))


def _refuse_transport(transport: str) -> None:
    """Report an unsupported --transport on stderr and exit 1.

    stdout stays empty: an MCP host may be reading it as the protocol channel.
    """
    text = mcp_msg.E_MCP_TRANSPORT.format(transport=display(transport))
    output.print_error(escape(text))
    output.print_hint(escape(mcp_msg.E_MCP_TRANSPORT_HINT))
    raise SystemExit(1)


class McpCommand(BaseCommand):
    """MCP server that serves Deepgram documentation search over stdio."""

    name = "mcp"
    help = (
        "Run an MCP server that exposes Deepgram documentation search to "
        "your AI editor over stdio"
    )
    short_help = "Serve Deepgram docs search over MCP"

    # No credential check at startup: the server answers the handshake with
    # or without a key, and a missing or refused key is a tool error on the
    # first call (stdout belongs to the MCP client from the first byte).
    requires_auth = False
    requires_project = False
    ci_friendly = True
    stdout_is_protocol = True

    examples = [
        "dg mcp",
        "dg mcp --base-url https://api.dx.deepgram.com",
    ]
    agent_help = (
        "Run an MCP (Model Context Protocol) server that exposes Deepgram "
        "documentation search to your AI editor. Configure "
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

    def output_result(self, result: Any, config: Config) -> None:
        """Write nothing: stdout belongs to the MCP client.

        A failure was already reported on stderr; the result only sets the
        exit code.
        """
        return None

    def _handle_shutdown(self, signum: int, frame: Any) -> None:
        """Handle shutdown signals gracefully."""
        if not self._shutdown_requested:
            self._shutdown_requested = True
            console.print("\n[yellow]MCP server stopped by user[/yellow]")
            os._exit(0)

    def handle(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> Any:
        """Handle the MCP server command."""
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

        debug = kwargs.get("debug", False)

        # Set up signal handling for graceful shutdown
        self._original_sigint_handler = signal.signal(
            signal.SIGINT, self._handle_shutdown
        )
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        import asyncio

        # From here on only JSON-RPC reaches the client: anything else
        # written to stdout (fd 1) goes to stderr.
        protocol = isolate_stdout()

        try:
            asyncio.run(
                run_proxy(
                    base_url=canonical_url(base_url),
                    api_key=lambda: key_or_error(config, auth_manager),
                    debug=debug,
                    stdout=protocol,
                )
            )

            # The MCP host owns stdout (JSON-RPC channel) and typically
            # closes it on disconnect, so we return None to skip
            # output_result rather than risk a write to a closed stream.
            return None

        except KeyboardInterrupt:
            return MCPServerResult(
                status="cancelled",
                message="MCP server stopped by user",
                transport=TransportType.STDIO,
            )
        except Exception as e:
            report_error(e)
            msg = str(e)
            if hasattr(e, "exceptions"):
                for sub in e.exceptions:
                    msg += f"\n  Caused by: {sub!r}"
            console.print(f"[red]Error running MCP server:[/red] {msg}")
            return MCPServerResult(
                status="error",
                message=str(e),
                transport=TransportType.STDIO,
            )
        finally:
            if self._original_sigint_handler:
                signal.signal(signal.SIGINT, self._original_sigint_handler)
