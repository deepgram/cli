"""Debug probe command for deepctl — WebSocket proxy with live ffprobe analysis."""

from __future__ import annotations

import socket
import time
from typing import Any

from deepctl_core import (
    AuthManager,
    BaseCommand,
    Config,
    DeepgramClient,
    get_status_console,
)
from deepctl_shared_utils import require_ffprobe
from rich.panel import Panel

from .models import ProbeDebugResult

# Status chrome is diagnostics: stderr, so stdout carries only the result.
console = get_status_console()


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


def _handle_sigint(loop: Any, callback: Any) -> tuple[bool, Any]:
    """Run `callback` on the event loop for SIGINT (POSIX), so Ctrl+C never
    lands inside a connection handler. Returns whether it was installed and
    the handler it replaced, to put back afterwards.

    SIGINT is left exactly as it is when it isn't ours to replace: ignored
    (a background job in a script starts with SIGINT ignored, or `trap ''
    INT`), so the proxy keeps running as before; or a handler not installed
    from Python (`signal.getsignal` returns None), which `signal.signal`
    couldn't restore. Where the loop can't take signals (Windows), Ctrl+C
    raises KeyboardInterrupt instead.
    """
    import signal

    previous = signal.getsignal(signal.SIGINT)
    if previous is signal.SIG_IGN or previous is None:
        return False, previous
    try:
        loop.add_signal_handler(signal.SIGINT, callback)
    except (NotImplementedError, RuntimeError, ValueError):
        return False, previous
    return True, previous


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
        import asyncio
        import signal

        from .proxy import ProbeProxy

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

        from deepctl_core import transport
        from rich.markup import escape

        # The upstream is the Transport's checked WebSocket base, and the key
        # goes only where that check allows (refused hosts raise, reported).
        upstream = transport.Transport(config, check_host=auth_manager.check_base_url)
        upstream_base = upstream.ws_base()
        upstream_headers = upstream.auth_headers(api_key)

        # Create proxy
        proxy = ProbeProxy(
            api_key=api_key,
            config=config,
            upstream_base=upstream_base,
            probe_interval_bytes=probe_interval_bytes,
            probe_interval_seconds=probe_interval_seconds,
            verbose=verbose,
            upstream_headers=upstream_headers,
        )
        upstream_host = proxy.upstream_host

        # Print instructions
        console.print(
            Panel(
                f"[bold cyan]WebSocket Probe Proxy[/bold cyan]\n\n"
                f"Listening on: [green]ws://localhost:{port}[/green]\n"
                f"Forwards to:  [yellow]{escape(proxy.upstream_base)}[/yellow]\n"
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
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        def interrupt() -> None:
            """First Ctrl+C: stop listening, let open connections finish and
            print their summaries. Second: end them now."""
            if proxy._stop.is_set():
                proxy.abort()
            else:
                console.print(
                    "\n[yellow]Stopping: open connections can finish. Press "
                    "Ctrl+C again to end them now.[/yellow]"
                )
                proxy._stop.set()

        installed, previous = _handle_sigint(loop, interrupt)
        serve_task = loop.create_task(proxy.run(port))
        try:
            while True:
                try:
                    # Run until Ctrl+C
                    loop.run_until_complete(serve_task)
                    break
                except KeyboardInterrupt:
                    # Windows, or wherever the loop doesn't take SIGINT.
                    if serve_task.done():  # it landed inside the task
                        serve_task.exception()
                        break
                    interrupt()
        finally:
            try:
                loop.run_until_complete(loop.shutdown_default_executor())
            finally:
                if installed:
                    loop.remove_signal_handler(signal.SIGINT)
                    signal.signal(signal.SIGINT, previous)
                loop.close()

        if proxy.fatal is not None:
            # The key was rejected at the upgrade: exit 1 with its code.
            raise proxy.fatal

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
