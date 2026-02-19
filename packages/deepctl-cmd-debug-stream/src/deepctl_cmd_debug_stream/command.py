"""Stream debug command for deepctl."""

import asyncio
import socket
import time
from typing import Any

from aiohttp import web
from deepctl_core import AuthManager, BaseCommand, Config, DeepgramClient
from rich.console import Console
from rich.panel import Panel

from .models import StreamDebugResult
from .proxy import WebSocketProxy

console = Console()


def _find_available_port(start: int = 3000, end: int = 3099) -> int | None:
    """Find an available port in the given range."""
    for port in range(start, end + 1):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    return None


class StreamCommand(BaseCommand):
    """WebSocket proxy for diagnosing audio streaming issues."""

    name = "stream"
    help = "WebSocket proxy for diagnosing audio streaming issues"
    short_help = "Stream debug proxy"

    requires_auth = True
    requires_project = False
    ci_friendly = False

    def get_arguments(self) -> list[dict[str, Any]]:
        """Get command arguments and options."""
        return [
            {
                "names": ["--port", "-p"],
                "help": "Local proxy port (default: auto-select 3000-3099)",
                "type": int,
                "is_option": True,
            },
            {
                "names": ["--upstream"],
                "help": "Upstream Deepgram host (default: api.deepgram.com)",
                "type": str,
                "default": "api.deepgram.com",
                "is_option": True,
            },
            {
                "names": ["--timeout"],
                "help": "Max session duration in seconds (default: 300)",
                "type": int,
                "default": 300,
                "is_option": True,
            },
            {
                "names": ["--sample-size"],
                "help": (
                    "Bytes of audio to sample for analysis "
                    "(default: 65536)"
                ),
                "type": int,
                "default": 65536,
                "is_option": True,
            },
            {
                "names": ["--no-analysis"],
                "help": "Skip ffprobe audio analysis on disconnect",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--verbose", "-v"],
                "help": "Show per-frame details",
                "is_flag": True,
                "is_option": True,
            },
        ]

    def handle(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> Any:
        """Handle stream debug command execution."""
        port = kwargs.get("port")
        upstream = kwargs.get("upstream") or "api.deepgram.com"
        timeout = kwargs.get("timeout") or 300
        sample_size = kwargs.get("sample_size") or 65536
        no_analysis = kwargs.get("no_analysis", False)
        verbose = kwargs.get("verbose", False)

        # Get API key
        api_key = auth_manager.get_api_key()
        if not api_key:
            console.print(
                "[red]No API key found. Run 'deepctl login' first.[/red]"
            )
            return StreamDebugResult(
                status="error",
                message="No API key found",
                upstream_host=upstream,
            )

        # Find available port
        if port is None:
            port = _find_available_port()
            if port is None:
                console.print(
                    "[red]No available ports in range 3000-3099[/red]"
                )
                return StreamDebugResult(
                    status="error",
                    message="No available ports",
                    upstream_host=upstream,
                )
        else:
            # Verify requested port is available
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("127.0.0.1", port))
            except OSError:
                console.print(f"[red]Port {port} is already in use[/red]")
                return StreamDebugResult(
                    status="error",
                    message=f"Port {port} is in use",
                    port=port,
                    upstream_host=upstream,
                )

        # Create proxy
        proxy = WebSocketProxy(
            api_key=api_key,
            upstream_host=upstream,
            sample_size=sample_size,
            no_analysis=no_analysis,
            verbose=verbose,
        )

        # Print instructions
        console.print(
            Panel(
                f"[bold cyan]WebSocket Debug Proxy[/bold cyan]\n\n"
                f"Listening on: [green]ws://localhost:{port}[/green]\n"
                f"Upstream:     [yellow]wss://{upstream}[/yellow]\n"
                f"API key:      [dim]****{api_key[-4:]}[/dim]\n\n"
                f"[bold]Point your app at the proxy URL:[/bold]\n"
                f"  STT:   ws://localhost:{port}/v1/listen?...\n"
                f"  TTS:   ws://localhost:{port}/v1/speak?...\n"
                f"  Agent: ws://localhost:{port}/agent?...\n\n"
                f"[dim]Press Ctrl+C to stop[/dim]",
                title="Debug Stream Proxy",
                border_style="cyan",
            )
        )

        # Run the proxy server
        start_time = time.time()
        app = web.Application()
        app.router.add_route("GET", "/{path:.*}", proxy.handle_connection)

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            runner = web.AppRunner(app)
            loop.run_until_complete(runner.setup())

            site = web.TCPSite(runner, "127.0.0.1", port)
            loop.run_until_complete(site.start())

            # Run until timeout or Ctrl+C
            loop.run_until_complete(asyncio.sleep(timeout))

        except KeyboardInterrupt:
            console.print("\n[yellow]Shutting down proxy...[/yellow]")
        finally:
            loop.run_until_complete(runner.cleanup())
            loop.close()

        duration = time.time() - start_time

        console.print(
            f"\n[green]Proxy stopped after {duration:.1f}s[/green]"
        )
        console.print(
            f"Total connections: {len(proxy.connections)}"
        )

        return StreamDebugResult(
            status="success",
            message="Proxy session completed",
            port=port,
            upstream_host=upstream,
            connections=proxy.connections,
            duration_seconds=duration,
        )
