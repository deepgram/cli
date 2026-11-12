"""Browser debug command for deepctl."""

import importlib.resources
import json
import logging
import re
import socket
import time
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

from deepctl_core import (
    AuthManager,
    BaseCommand,
    Config,
    DeepgramClient,
    get_status_console,
)
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text

from .models import (
    BrowserCapabilities,
    BrowserCapability,
    BrowserDebugResult,
    MessageType,
    WebSocketMessage,
)

if TYPE_CHECKING:
    from websockets.asyncio.server import Server, ServerConnection
    from websockets.http11 import Request, Response

# Everything this command prints is chrome, and the page link carries the
# session token, so all of it goes to stderr: under -o json or yaml, stdout
# holds only the result.
console = get_status_console()

PORT_RANGE = range(3000, 3100)
MAX_FRAME_BYTES = 2**20
# What the page can make this process keep: messages by count and by total
# size, and capability names.
MAX_MESSAGES = 1000
MAX_MESSAGE_BYTES = 8 * 2**20  # counted as stored JSON
MAX_MESSAGE_TEXT = 64 * 2**10  # a longer frame is dropped
CAPABILITY_KEYS = (
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
    "microphone_access",
    "websocket_deepgram",
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# The same fixed body for every 403: it echoes nothing from the request and
# doesn't say which check failed.
FORBIDDEN = "Forbidden. Open the exact link that dg debug browser printed.\n"
LINK_NOTE = "It works only while this command runs."

# The page and the WebSocket are only for the local browser this command
# opens: loopback Host, the page's own Origin, and the session token.
PAGE_HEADERS = (
    ("Content-Type", "text/html; charset=utf-8"),
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Content-Security-Policy", "frame-ancestors 'none'"),
    ("Referrer-Policy", "no-referrer"),
)


def _quiet_logger() -> logging.Logger:
    """A logger for the server that never writes request headers anywhere,
    whatever the root logger's level or handlers."""
    logger = logging.getLogger("deepctl.debug.browser.server")
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    logger.propagate = False
    logger.setLevel(logging.CRITICAL + 1)
    return logger


def _untrusted(text: str, style: str = "") -> Text:
    """Text from the page for rich to print as it is: plain ASCII, control
    characters and anything non-ASCII as backslash escapes, in a Text object
    so neither markup nor :emoji: codes in it are interpreted."""
    text = text.encode("ascii", "backslashreplace").decode("ascii")
    return Text(_CONTROL.sub(lambda m: f"\\x{ord(m.group()):02x}", text), style=style)


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _print_link(lead: str, url: str, note: str) -> None:
    """The page link, whole on one line so it can be copied, then a dim note.
    These lines are the only place the session token is shown (stderr)."""
    console.print(f"\n{lead} [cyan]{url}[/cyan]", soft_wrap=True)
    console.print(f"[dim]{note}[/dim]", soft_wrap=True)


def _respond(
    connection: "ServerConnection",
    status: int,
    text: str,
    headers: tuple[tuple[str, str], ...] = (),
) -> "Response":
    """A complete response that ends the connection.

    connection.respond() also marks the handshake as refused, so websockets
    never runs the WebSocket handler for it (13.x would for a hand-built
    Response).
    """
    response = connection.respond(status, text)
    if headers:
        del response.headers["Content-Type"]
        for name, value in headers:
            response.headers[name] = value
    return response


# asyncio (with ssl) loads only when the server starts, so
# `dg debug browser --help` doesn't import it.
def __getattr__(name: str) -> Any:
    if name == "asyncio":
        import asyncio

        return asyncio
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class BrowserCommand(BaseCommand):
    """Debug browser-related connectivity and access issues."""

    name = "browser"
    help = "Debug browser compatibility and capabilities for Deepgram services"
    short_help = "Debug browser capabilities"

    # Browser debug doesn't require auth
    requires_auth = False

    examples = [
        "dg debug browser",
        "dg debug browser --port 8080",
    ]
    agent_help = (
        "Launch a local web page that tests browser capabilities for "
        "Deepgram services — WebSocket support, microphone access, "
        "audio encoding, and WebRTC. Opens a browser automatically."
    )
    requires_project = False
    ci_friendly = False  # This command opens a browser

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[WebSocketMessage] = []
        self.capabilities_data: dict[str, Any] = {}
        self.ws_clients: set[Any] = set()
        self.debug_complete = False
        self.start_time: float | None = None
        self._token = ""
        self._server: Server | None = None
        self.browser_opened = False
        self._stored_bytes = 0
        self.dropped_messages = 0
        self.dropped_capabilities = 0

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
                    "Timeout in seconds to wait for browser connection (default: 60)"
                ),
                "type": int,
                "default": 60,
                "is_option": True,
            },
            {
                "names": ["--save-report"],
                "help": "Save debug report to a JSON file",
                "type": str,
                "is_option": True,
            },
        ]

    def find_available_port(self, start_port: int = 3000) -> int:
        """Find an available port starting from the given port."""
        for port in range(start_port, start_port + 100):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                try:
                    sock.bind(("127.0.0.1", port))
                except OSError:
                    continue
                return port
        raise RuntimeError(f"Could not find available port starting from {start_port}")

    @staticmethod
    def _allowed(values: list[str], form: str, port: int) -> bool:
        """True if there is one value, and it is `form` for 127.0.0.1 or
        localhost on `port` (case-insensitive; on port 80 without the port,
        as browsers send it)."""
        forms = [form]
        if port == 80:
            forms.append(form.replace(":{port}", ""))
        return len(values) == 1 and values[0].lower() in {
            f.format(host=host, port=port)
            for f in forms
            for host in ("127.0.0.1", "localhost")
        }

    def _token_ok(self, target: str) -> bool:
        import hmac

        tokens = parse_qs(urlsplit(target).query).get("t", [])
        return len(tokens) == 1 and hmac.compare_digest(
            tokens[0].encode("utf-8", "surrogateescape"),
            self._token.encode(),
        )

    def _process_request(
        self, connection: "ServerConnection", request: "Request"
    ) -> "Response | None":
        """Serve the page, refuse, or let a /ws upgrade through.

        Runs before any WebSocket handshake. Rejections are fixed text and
        never echo the Host, Origin or token back.
        """
        # websockets < 17 closes the connection on other methods itself,
        # without a response.
        if getattr(request, "method", "GET") != "GET":
            return _respond(connection, 405, "Method not allowed.\n")
        port = connection.local_address[1]
        if not self._allowed(request.headers.get_all("Host"), "{host}:{port}", port):
            return _respond(connection, 403, FORBIDDEN)
        path = urlsplit(request.path).path
        if path not in ("/", "/ws"):
            return _respond(connection, 404, "Not found.\n")
        if not self._token_ok(request.path):
            return _respond(connection, 403, FORBIDDEN)
        if path == "/":
            return _respond(connection, 200, self._page(), PAGE_HEADERS)
        origins = request.headers.get_all("Origin")
        if not self._allowed(origins, "http://{host}:{port}", port):
            return _respond(connection, 403, FORBIDDEN)
        return None

    @staticmethod
    def _page() -> str:
        """The debug page, read from the package."""
        try:
            files = importlib.resources.files("deepctl_cmd_debug_browser")
            html_content = (files / "static" / "debug.html").read_text()
        except Exception:
            # Fallback to Path-based approach
            html_path = Path(__file__).parent / "static" / "debug.html"
            html_content = html_path.read_text()
        return html_content

    def _handle_text(self, text: str) -> None:
        """Record one message from the page.

        Raises ValueError, TypeError or AttributeError for a frame that isn't
        a valid message; nothing is recorded then.
        """
        if len(text) > MAX_MESSAGE_TEXT:
            self.dropped_messages += 1
            return
        data = json.loads(text)
        ws_msg = WebSocketMessage(
            type=MessageType(data.get("type", "info")),
            data=data.get("data", {}),
            message=data.get("message"),
        )
        capabilities = self.capabilities_data

        # Handle different message types
        if ws_msg.type == MessageType.CAPABILITY_CHECK:
            cap_data = ws_msg.data.get("result", {})
            cap_key = ws_msg.data.get("capability")
            if cap_key and cap_key not in CAPABILITY_KEYS:
                self.dropped_capabilities += 1
            elif cap_key and cap_data:
                capabilities = {
                    **capabilities,
                    cap_key: BrowserCapability(**cap_data),
                }

        elif ws_msg.type == MessageType.COMPLETE:
            # Store complete capabilities
            if "capabilities" in ws_msg.data:
                caps = ws_msg.data["capabilities"]
                # Convert all capability data
                cap_dict = {}
                for key in CAPABILITY_KEYS:
                    if key in caps:
                        cap_dict[key] = BrowserCapability(**caps[key])
                    elif key in self.capabilities_data:
                        cap_dict[key] = self.capabilities_data[key]

                # Store complete capabilities
                capabilities = {
                    **cap_dict,
                    "user_agent": caps.get("user_agent", "Unknown"),
                    "overall_compatible": caps.get("overall_compatible", False),
                }

        size = len(ws_msg.model_dump_json())
        if (
            len(self.messages) < MAX_MESSAGES
            and self._stored_bytes + size <= MAX_MESSAGE_BYTES
        ):
            self.messages.append(ws_msg)
            self._stored_bytes += size
        else:
            self.dropped_messages += 1
        self.capabilities_data = capabilities
        if ws_msg.type == MessageType.COMPLETE:
            self.debug_complete = True

    async def websocket_handler(self, ws: "ServerConnection") -> None:
        """Handle WebSocket connections from the browser.

        Text frames carry JSON messages; a frame that isn't a valid message
        is dropped and the connection stays open. Binary frames are ignored.
        """
        from websockets.exceptions import ConnectionClosed

        self.ws_clients.add(ws)
        try:
            async for frame in ws:
                if isinstance(frame, str):
                    try:
                        self._handle_text(frame)
                    except (ValueError, TypeError, AttributeError):
                        continue
        except ConnectionClosed:
            pass
        finally:
            self.ws_clients.discard(ws)

    async def _start_server(self, port: int | None) -> int:
        """Listen on 127.0.0.1 and return the port.

        A given port is tried once and an OSError propagates (0 picks any
        free port). Otherwise the first port in 3000-3099 that binds is used.
        """
        from websockets.asyncio.server import serve

        for candidate in PORT_RANGE if port is None else [port]:
            try:
                self._server = await serve(
                    self.websocket_handler,
                    "127.0.0.1",
                    candidate,
                    process_request=self._process_request,
                    max_size=MAX_FRAME_BYTES,
                    ping_interval=None,
                    server_header=None,
                    logger=_quiet_logger(),
                )
            except OSError:
                if port is not None:
                    raise
                continue
            bound: int = next(iter(self._server.sockets)).getsockname()[1]
            return bound
        raise RuntimeError(
            f"Could not find available port starting from {PORT_RANGE.start}"
        )

    async def _stop_server(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def run_servers(self, port: int, timeout: int) -> dict[str, Any]:
        """Wait for the page to finish its checks, or for the timeout."""
        import asyncio

        console.print(
            f"\n[green]✓[/green] Debug server started on port [cyan]{port}[/cyan]"
        )

        # Wait for debug to complete or timeout
        start_time = time.time()
        while not self.debug_complete and (time.time() - start_time) < timeout:
            await asyncio.sleep(0.1)

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
                        _untrusted(msg.message or ""),
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
                ("microphone_access", "Microphone Access"),
                ("websocket_deepgram", "WebSocket to Deepgram"),
            ]

            for field, display_name in capability_fields:
                cap = getattr(capabilities, field, None)
                if cap:
                    status_icon = "✓" if cap.supported else "✗"
                    status_color = "green" if cap.supported else "red"
                    cap_table.add_row(
                        display_name,
                        Text.assemble(
                            f"{status_icon} ",
                            _untrusted(str(cap.details)),
                            style=status_color,
                        ),
                        _untrusted(cap.version or ""),
                    )

            console.print(cap_table)

            # Overall compatibility
            console.print(
                Text("\nUser Agent:", style="bold"),
                _untrusted(capabilities.user_agent),
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

    def _save_report(self, result: BrowserDebugResult, filename: str) -> None:
        """Save debug report to a JSON file."""
        import json
        from datetime import datetime

        try:
            report = {
                "timestamp": datetime.now().isoformat(),
                "result": result.model_dump(),
            }
            with open(filename, "w") as f:
                json.dump(report, f, indent=2, default=str)
            console.print(f"\n[green]Report saved to: {filename}[/green]")
        except Exception as e:
            console.print(f"\n[red]Failed to save report: {e!s}[/red]")

    def handle(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> Any:
        """Handle browser debug command execution."""
        import asyncio
        import secrets

        port = kwargs.get("port")
        no_browser = kwargs.get("no_browser", False)
        timeout = kwargs.get("timeout", 60)
        save_report = kwargs.get("save_report")

        self.start_time = time.time()
        self._token = secrets.token_urlsafe(16)

        # Show initial message
        console.print(
            Panel.fit(
                "[cyan]🌐 Browser Debug[/cyan]\n\n"
                "Starting debug server to check browser capabilities...",
                title="Deepctl Browser Debug",
                border_style="cyan",
            )
        )

        # Listen first, so the URL shown is the one that is being served.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            port = loop.run_until_complete(self._start_server(port or None))
            result = self._serve(loop, port, timeout, no_browser)
        finally:
            loop.run_until_complete(self._stop_server())
            loop.close()

        return self._finish(port, timeout, save_report, result)

    def _serve(
        self, loop: Any, port: int, timeout: int, no_browser: bool
    ) -> dict[str, Any]:
        """Point the browser at the page, then wait for its checks."""
        url = f"http://127.0.0.1:{port}/?t={self._token}"

        # Prompt user to open browser
        if not no_browser:
            if self._guided:
                console.print(
                    "\n[yellow]Press Enter to open the debugger in your browser...[/yellow]"
                )
                input()
            _print_link(
                "Opening the debugger in your browser:",
                url,
                f"If it doesn't open, paste the link into your browser. {LINK_NOTE}",
            )
            webbrowser.open(url)
            self.browser_opened = True
        else:
            _print_link(
                "Open this link in your browser to start debugging:", url, LINK_NOTE
            )
            self.browser_opened = False

        # Run the async event loop
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("Waiting for browser connection...", total=None)
            result: dict[str, Any] = loop.run_until_complete(
                self.run_servers(port, timeout)
            )
            progress.update(task, completed=True)
        return result

    def _finish(
        self,
        port: int,
        timeout: int,
        save_report: str | None,
        result: dict[str, Any],
    ) -> BrowserDebugResult:
        """Show the results and build the command result."""
        # Process results
        capabilities = None
        if self.capabilities_data and "overall_compatible" in self.capabilities_data:
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
                    user_agent=self.capabilities_data.get("user_agent", "Unknown"),
                    overall_compatible=self.capabilities_data.get(
                        "overall_compatible", False
                    ),
                )

        # Display results
        if result["completed"]:
            console.print("\n[green]✓ Debug session completed successfully![/green]")
            self.display_results(capabilities, self.messages)
        elif result["timed_out"]:
            console.print(
                f"\n[red]✗ Debug session timed out after {timeout} seconds[/red]"
            )
            console.print("[dim]No browser connection was established.[/dim]")
        else:
            console.print("\n[yellow]Debug session ended[/yellow]")
        left_out = []
        if self.dropped_messages:
            left_out.append(
                _count(self.dropped_messages, "message", "messages")
                + " over the size limit"
            )
        if self.dropped_capabilities:
            left_out.append(
                _count(
                    self.dropped_capabilities,
                    "unknown capability name",
                    "unknown capability names",
                )
            )
        if left_out:
            console.print(f"[dim]Left out of the results: {', '.join(left_out)}[/dim]")

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

        debug_result = BrowserDebugResult(
            status="success" if result["completed"] else "timeout",
            port=port,
            capabilities=capabilities,
            messages=self.messages,
            errors=errors,
            warnings=warnings,
            duration_seconds=result["duration"],
            browser_opened=self.browser_opened,
        )

        # Save report if requested
        if save_report:
            self._save_report(debug_result, save_report)

        return debug_result
