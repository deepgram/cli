"""Debug probe command for deepctl — WebSocket proxy with live ffprobe analysis."""

from __future__ import annotations

import asyncio
import socket
import time
from typing import Any

from aiohttp import web
from deepctl_core import AuthManager, BaseCommand, Config, DeepgramClient
from deepctl_shared_utils import require_ffprobe
from rich.console import Console
from rich.panel import Panel

from .models import ProbeDebugResult
from .proxy import ProbeProxy

console = Console()


def _find_available_port(start: int = 3100, end: int = 3199) -> int | None:
    """Find an available port in the given range."""
    for port in range(start, end + 1):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    return None


class ProbeCommand(BaseCommand):
    """WebSocket proxy with periodic ffprobe analysis of streaming audio."""

    name = "probe"
    help = (
        "Launch a local WebSocket proxy that periodically probes "
        "streaming audio with ffprobe while forwarding to Deepgram."
    )
    short_help = "Stream probe proxy"

    requires_auth = True
    requires_project = False
    ci_friendly = False

    examples = [
        "dg debug probe",
        "dg debug probe --port 3100",
        "dg debug probe --probe-interval-bytes 131072",
        "dg debug probe --probe-interval-seconds 10",
    ]
    agent_help = (
        "Launch a local WebSocket proxy that sits between your application "
        "and Deepgram's streaming API. Periodically runs ffprobe on the "
        "accumulated audio buffer to show codec, sample rate, and channel "
        "information alongside real-time transcripts."
    )

    def get_arguments(self) -> list[dict[str, Any]]:
        """Get command arguments and options."""
        return [
            {
                "names": ["--port", "-p"],
                "help": "Local proxy port (default: auto-select 3100-3199)",
                "type": int,
                "is_option": True,
            },
            {
                "names": ["--probe-interval-bytes"],
                "help": "Probe every N bytes of audio (default: 131072)",
                "type": int,
                "default": 131072,
                "is_option": True,
            },
            {
                "names": ["--probe-interval-seconds"],
                "help": "Probe every N seconds (default: 10)",
                "type": float,
                "default": 10.0,
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
        """Handle debug probe command execution."""
        port = kwargs.get("port")
        probe_interval_bytes = kwargs.get("probe_interval_bytes") or 131072
        probe_interval_seconds = kwargs.get("probe_interval_seconds") or 10.0
        verbose = kwargs.get("verbose", False)

        # Check ffprobe availability
        if not require_ffprobe(config):
            return ProbeDebugResult(
                status="error",
                message="ffprobe is required but not found",
            )

        # Get API key
        api_key = auth_manager.get_api_key()
        if not api_key:
            console.print("[red]No API key found. Run 'dg login' first.[/red]")
            return ProbeDebugResult(
                status="error",
                message="No API key found",
            )

        # Find available port
        if port is None:
            port = _find_available_port()
            if port is None:
                console.print("[red]No available ports in range 3100-3199[/red]")
                return ProbeDebugResult(
                    status="error",
                    message="No available ports",
                )
        else:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("127.0.0.1", port))
            except OSError:
                console.print(f"[red]Port {port} is already in use[/red]")
                return ProbeDebugResult(
                    status="error",
                    message=f"Port {port} is in use",
                    port=port,
                )

        upstream_host = "api.deepgram.com"

        # Create proxy
        proxy = ProbeProxy(
            api_key=api_key,
            config=config,
            upstream_host=upstream_host,
            probe_interval_bytes=probe_interval_bytes,
            probe_interval_seconds=probe_interval_seconds,
            verbose=verbose,
        )

        # Print instructions
        console.print(
            Panel(
                f"[bold cyan]WebSocket Probe Proxy[/bold cyan]\n\n"
                f"Listening on: [green]ws://localhost:{port}[/green]\n"
                f"Upstream:     [yellow]wss://{upstream_host}[/yellow]\n"
                f"API key:      [dim]****{api_key[-4:]}[/dim]\n\n"
                f"Probe every:  {probe_interval_bytes:,} bytes "
                f"or {probe_interval_seconds}s\n\n"
                f"[bold]Point your app at the proxy URL:[/bold]\n"
                f"  ws://localhost:{port}/v1/listen?encoding=...&sample_rate=...\n\n"
                f"[dim]Press Ctrl+C to stop[/dim]",
                title="Debug Probe Proxy",
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

            # Run until Ctrl+C
            loop.run_forever()

        except KeyboardInterrupt:
            console.print("\n[yellow]Shutting down probe proxy...[/yellow]")
        finally:
            loop.run_until_complete(runner.cleanup())
            loop.close()

        duration = time.time() - start_time

        console.print(f"\n[green]Probe proxy stopped after {duration:.1f}s[/green]")
        console.print(f"Total connections: {len(proxy.connections)}")

        return ProbeDebugResult(
            status="success",
            message="Probe proxy session completed",
            port=port,
            upstream_host=upstream_host,
            connections=proxy.connections,
            duration_seconds=duration,
        )
