"""Browser debug command for deepctl."""

import asyncio
import importlib.resources
import json
import socket
import time
import webbrowser
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web
from deepctl_core import AuthManager, BaseCommand, Config, DeepgramClient
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from .models import (
    BrowserCapabilities,
    BrowserCapability,
    BrowserDebugResult,
    MessageType,
    WebSocketMessage,
)

console = Console()


class BrowserCommand(BaseCommand):
    """Debug browser-related connectivity and access issues."""

    name = "browser"
    help = "Debug browser compatibility and capabilities for Deepgram services"
    short_help = "Debug browser capabilities"

    # Browser debug doesn't require auth
    requires_auth = False
    requires_project = False
    ci_friendly = False  # This command opens a browser

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[WebSocketMessage] = []
        self.capabilities_data: dict[str, Any] = {}
        self.ws_clients: set[Any] = set()
        self.debug_complete = False
        self.start_time: float | None = None

    def get_arguments(self) -> list[dict[str, Any]]:
        """Get command arguments and options."""
        return [
            {
                "names": ["--port", "-p"],
                "help": (
                    "Port to run the debug server on "
                    "(default: auto-select starting from 3000)"
                ),
                "type": int,
                "is_option": True,
            },
            {
                "names": ["--no-browser"],
                "help": "Don't automatically open the browser",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--timeout"],
                "help": (
                    "Timeout in seconds to wait for browser connection "
                    "(default: 60)"
                ),
                "type": int,
                "default": 60,
                "is_option": True,
            },
        ]

    def find_available_port(self, start_port: int = 3000) -> int:
        """Find an available port starting from the given port."""
        for port in range(start_port, start_port + 100):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind(("localhost", port))
                sock.close()
                return port
            except OSError:
                continue
        raise RuntimeError(
            f"Could not find available port starting from {start_port}"
        )

    async def websocket_handler(self, request: Any) -> Any:
        """Handle WebSocket connections from the browser."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.ws_clients.add(ws)

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    ws_msg = WebSocketMessage(
                        type=MessageType(data.get("type", "info")),
                        data=data.get("data", {}),
                        message=data.get("message"),
                    )
                    self.messages.append(ws_msg)

                    # Handle different message types
                    if ws_msg.type == MessageType.CAPABILITY_CHECK:
                        cap_data = ws_msg.data.get("result", {})
                        cap_key = ws_msg.data.get("capability")
                        if cap_key and cap_data:
                            self.capabilities_data[cap_key] = (
                                BrowserCapability(**cap_data)
                            )

                    elif ws_msg.type == MessageType.COMPLETE:
                        # Store complete capabilities
                        if "capabilities" in ws_msg.data:
                            caps = ws_msg.data["capabilities"]
                            # Convert all capability data
                            cap_dict = {}
                            for key in [
                                "web_audio_api",
                                "audio_context",
                                "audio_worklet",
                                "websocket_api",
                                "fetch_api",
                                "es6_features",
                                "dom_apis",
                                "console_api",
                                "timer_apis",
                                "secure_context",
                            ]:
                                if key in caps:
                                    cap_dict[key] = BrowserCapability(
                                        **caps[key]
                                    )
                                elif key in self.capabilities_data:
                                    cap_dict[key] = self.capabilities_data[key]

                            # Store complete capabilities
                            self.capabilities_data = {
                                **cap_dict,
                                "user_agent": caps.get(
                                    "user_agent", "Unknown"
                                ),
                                "overall_compatible": caps.get(
                                    "overall_compatible", False
                                ),
                            }
                        self.debug_complete = True
                elif msg.type == WSMsgType.ERROR:
                    console.print(
                        f"[red]WebSocket error: {ws.exception()}[/red]"
                    )
        finally:
            self.ws_clients.discard(ws)
            await ws.close()

        return ws

    async def http_handler(self, request: Any) -> Any:
        """Serve the debug HTML page."""
        if request.path == "/":
            # Read the HTML template from the static directory
            try:
                files = importlib.resources.files("deepctl_cmd_debug_browser")
                html_content = (files / "static" / "debug.html").read_text()
            except Exception:
                # Fallback to Path-based approach
                html_path = Path(__file__).parent / "static" / "debug.html"
                html_content = html_path.read_text()

            return web.Response(text=html_content, content_type="text/html")
        return web.Response(status=404)

    async def run_servers(self, port: int, timeout: int) -> dict[str, Any]:
        """Run both HTTP and WebSocket servers."""
        # Create aiohttp app
        app = web.Application()
        app.router.add_get("/", self.http_handler)
        app.router.add_get("/ws", self.websocket_handler)

        # Start HTTP server (which also handles WebSocket)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", port)
        await site.start()

        console.print(
            f"\n[green]✓[/green] Debug server started on port "
            f"[cyan]{port}[/cyan]"
        )

        # Wait for debug to complete or timeout
        start_time = time.time()
        while not self.debug_complete and (time.time() - start_time) < timeout:
            await asyncio.sleep(0.1)

        # Cleanup
        await runner.cleanup()

        return {
            "completed": self.debug_complete,
            "timed_out": not self.debug_complete
            and (time.time() - start_time) >= timeout,
            "duration": time.time() - start_time,
        }

    def display_results(
        self,
        capabilities: BrowserCapabilities | None,
        messages: list[WebSocketMessage],
    ) -> None:
        """Display the debug results in a formatted way."""
        console.print("\n[bold cyan]Browser Debug Results[/bold cyan]\n")

        # Display messages/logs
        if messages:
            log_table = Table(
                title="Debug Log", show_header=True, header_style="bold cyan"
            )
            log_table.add_column("Time", style="dim", width=12)
            log_table.add_column("Type", width=10)
            log_table.add_column("Message")

            for msg in messages:
                if msg.message:
                    time_str = msg.timestamp.strftime("%H:%M:%S.%f")[:-3]
                    type_style = {
                        MessageType.ERROR: "red",
                        MessageType.WARNING: "yellow",
                        MessageType.INFO: "cyan",
                        MessageType.CAPABILITY_CHECK: "green",
                        MessageType.COMPLETE: "magenta",
                    }.get(msg.type, "white")

                    log_table.add_row(
                        time_str,
                        f"[{type_style}]{msg.type.value}[/{type_style}]",
                        msg.message or "",
                    )

            console.print(log_table)
            console.print()

        # Display capabilities
        if capabilities:
            cap_table = Table(
                title="Browser Capabilities",
                show_header=True,
                header_style="bold cyan",
            )
            cap_table.add_column("Feature", style="cyan", width=30)
            cap_table.add_column("Status", width=15)
            cap_table.add_column("Details", width=40)

            # Add capability rows
            capability_fields = [
                ("web_audio_api", "Web Audio API"),
                ("audio_context", "AudioContext"),
                ("audio_worklet", "AudioWorklet API"),
                ("websocket_api", "WebSocket API"),
                ("fetch_api", "Fetch API"),
                ("es6_features", "ES6+ Features"),
                ("dom_apis", "DOM APIs"),
                ("console_api", "Console API"),
                ("timer_apis", "Timer APIs"),
                ("secure_context", "Secure Context"),
            ]

            for field, display_name in capability_fields:
                cap = getattr(capabilities, field, None)
                if cap:
                    status_icon = "✓" if cap.supported else "✗"
                    status_color = "green" if cap.supported else "red"
                    cap_table.add_row(
                        display_name,
                        f"[{status_color}]{status_icon} "
                        f"{cap.details}[/{status_color}]",
                        cap.version or "",
                    )

            console.print(cap_table)

            # Overall compatibility
            console.print(
                f"\n[bold]User Agent:[/bold] {capabilities.user_agent}"
            )
            if capabilities.overall_compatible:
                console.print(
                    "[bold green]✓ Browser is fully compatible with "
                    "Deepgram services[/bold green]"
                )
            else:
                console.print(
                    "[bold red]✗ Browser may have compatibility issues "
                    "with some Deepgram features[/bold red]"
                )

    def handle(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> Any:
        """Handle browser debug command execution."""
        port = kwargs.get("port")
        no_browser = kwargs.get("no_browser", False)
        timeout = kwargs.get("timeout", 60)

        self.start_time = time.time()

        # Find available port
        if not port:
            port = self.find_available_port()

        # Show initial message
        console.print(
            Panel.fit(
                "[cyan]🌐 Browser Debug[/cyan]\n\n"
                "Starting debug server to check browser capabilities...",
                title="Deepctl Browser Debug",
                border_style="cyan",
            )
        )

        url = f"http://localhost:{port}"

        # Prompt user to open browser
        if not no_browser:
            console.print(
                "\n[yellow]Press Enter to open the debugger in your "
                "browser...[/yellow]"
            )
            input()
            webbrowser.open(url)
            browser_opened = True
        else:
            console.print(
                f"\n[dim]Open [cyan]{url}[/cyan] in your browser to start "
                f"debugging[/dim]"
            )
            browser_opened = False

        # Run the async event loop
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task(
                "Waiting for browser connection...", total=None
            )

            # Run servers
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                result = loop.run_until_complete(
                    self.run_servers(port, timeout)
                )
            finally:
                loop.close()

            progress.update(task, completed=True)

        # Process results
        capabilities = None
        if (
            self.capabilities_data
            and "overall_compatible" in self.capabilities_data
        ):
            # Build BrowserCapabilities object
            cap_dict = {}
            for key in [
                "web_audio_api",
                "audio_context",
                "audio_worklet",
                "websocket_api",
                "fetch_api",
                "es6_features",
                "dom_apis",
                "console_api",
                "timer_apis",
                "secure_context",
            ]:
                if key in self.capabilities_data:
                    cap_dict[key] = self.capabilities_data[key]

            if all(key in cap_dict for key in cap_dict):
                capabilities = BrowserCapabilities(
                    **cap_dict,
                    user_agent=self.capabilities_data.get(
                        "user_agent", "Unknown"
                    ),
                    overall_compatible=self.capabilities_data.get(
                        "overall_compatible", False
                    ),
                )

        # Display results
        if result["completed"]:
            console.print(
                "\n[green]✓ Debug session completed successfully![/green]"
            )
            self.display_results(capabilities, self.messages)
        elif result["timed_out"]:
            console.print(
                f"\n[red]✗ Debug session timed out after {timeout} "
                f"seconds[/red]"
            )
            console.print("[dim]No browser connection was established.[/dim]")
        else:
            console.print("\n[yellow]Debug session ended[/yellow]")

        # Build result
        errors = [
            msg.message
            for msg in self.messages
            if msg.type == MessageType.ERROR and msg.message
        ]
        warnings = [
            msg.message
            for msg in self.messages
            if msg.type == MessageType.WARNING and msg.message
        ]

        return BrowserDebugResult(
            status="success" if result["completed"] else "timeout",
            port=port,
            capabilities=capabilities,
            messages=self.messages,
            errors=errors,
            warnings=warnings,
            duration_seconds=result["duration"],
            browser_opened=browser_opened,
        )
