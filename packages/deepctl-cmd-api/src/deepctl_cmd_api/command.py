"""API command for deepctl."""

import json
import shutil
import subprocess
import sys
from typing import Any

import httpx
from deepctl_core import AuthManager, BaseCommand, Config, DeepgramClient
from rich.console import Console
from rich.panel import Panel

from .models import ApiResult

console = Console()

DEFAULT_BASE_URL = "https://api.deepgram.com"


class ApiCommand(BaseCommand):
    """Make authenticated requests to Deepgram APIs."""

    name = "api"
    help = "Make authenticated requests to Deepgram APIs"
    short_help = "Make API requests"

    requires_auth = True
    requires_project = False
    ci_friendly = True

    examples = [
        "dg api /v1/projects",
        "dg api -X POST /v1/projects -f name=MyProject",
        "dg api /v1/projects --jq '.projects[0].name'",
    ]
    agent_help = (
        "Make authenticated HTTP requests to any Deepgram REST API endpoint. "
        "Works like curl with auto-injected credentials. Supports all HTTP methods, "
        "body construction via -f key=value, and jq filtering of responses."
    )

    def get_arguments(self) -> list[dict[str, Any]]:
        """Get command arguments and options."""
        return [
            {
                "name": "endpoint",
                "help": "API endpoint path (e.g. /v1/projects)",
                "type": str,
                "required": True,
                "is_option": False,
            },
            {
                "names": ["-X", "--method"],
                "help": "HTTP method (default: GET)",
                "type": str,
                "default": "GET",
                "is_option": True,
            },
            {
                "names": ["-f", "--field"],
                "help": (
                    "Request body field as key=value (string) "
                    "or key:=value (raw JSON)"
                ),
                "type": str,
                "multiple": True,
                "is_option": True,
            },
            {
                "names": ["--input"],
                "help": "Request body from file path or - for stdin",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["-H", "--header"],
                "help": "Custom header as 'Key: Value'",
                "type": str,
                "multiple": True,
                "is_option": True,
            },
            {
                "names": ["--jq"],
                "help": "jq filter expression to apply to response",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--raw"],
                "help": "Output raw response without formatting",
                "is_flag": True,
                "is_option": True,
            },
        ]

    def _resolve_url(self, endpoint: str) -> str:
        """Resolve endpoint to full URL."""
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            return endpoint
        base = DEFAULT_BASE_URL.rstrip("/")
        path = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        return f"{base}{path}"

    def _build_headers(
        self,
        api_key: str,
        custom_headers: tuple[str, ...] | list[str] | None,
    ) -> dict[str, str]:
        """Build request headers with auth and custom headers."""
        headers: dict[str, str] = {
            "Authorization": f"Token {api_key}",
        }
        if custom_headers:
            for header in custom_headers:
                if ":" in header:
                    key, value = header.split(":", 1)
                    headers[key.strip()] = value.strip()
        return headers

    def _build_body(
        self,
        fields: tuple[str, ...] | list[str] | None,
        input_source: str | None,
    ) -> Any | None:
        """Build request body from fields or input source."""
        if fields and input_source:
            raise ValueError(
                "Cannot use both --field and --input. Choose one."
            )

        if fields:
            body: dict[str, Any] = {}
            for field in fields:
                if ":=" in field:
                    key, raw_value = field.split(":=", 1)
                    body[key] = json.loads(raw_value)
                elif "=" in field:
                    key, value = field.split("=", 1)
                    body[key] = value
                else:
                    raise ValueError(
                        f"Invalid field format: {field}. "
                        f"Use key=value or key:=json_value"
                    )
            return body

        if input_source:
            if input_source == "-":
                raw = sys.stdin.read()
            else:
                with open(input_source) as f:
                    raw = f.read()
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw

        return None

    def _apply_jq_filter(self, response_text: str, jq_filter: str) -> str:
        """Apply jq filter to response text."""
        if not shutil.which("jq"):
            console.print(
                Panel(
                    "[red]jq is not installed[/red]\n\n"
                    "[bold]To install jq:[/bold]\n"
                    "  macOS: [dim]brew install jq[/dim]\n"
                    "  Ubuntu/Debian: [dim]sudo apt install jq[/dim]\n"
                    "  Windows: [dim]choco install jq[/dim]\n"
                    "  Or visit: [link]https://jqlang.github.io/jq/download/[/link]",
                    title="jq Required",
                    border_style="red",
                )
            )
            raise RuntimeError("jq is not installed")

        result = subprocess.run(
            ["jq", jq_filter],
            input=response_text,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"jq error: {result.stderr.strip()}")
        return result.stdout.strip()

    def handle(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> Any:
        """Handle API command execution."""
        endpoint = kwargs.get("endpoint", "")
        method = (kwargs.get("method") or "GET").upper()
        fields = kwargs.get("field") or ()
        input_source = kwargs.get("input")
        custom_headers = kwargs.get("header") or ()
        jq_filter = kwargs.get("jq")
        raw_output = kwargs.get("raw", False)

        # Resolve URL
        url = self._resolve_url(endpoint)

        # Get API key
        api_key = auth_manager.get_api_key()
        if not api_key:
            console.print("[red]No API key found. Run 'deepctl login' first.[/red]")
            return ApiResult(
                status="error",
                message="No API key found",
                method=method,
                url=url,
            )

        # Build headers
        headers = self._build_headers(api_key, custom_headers)

        # Build body
        try:
            body = self._build_body(fields, input_source)
        except (ValueError, json.JSONDecodeError) as e:
            console.print(f"[red]Error building request body: {e}[/red]")
            return ApiResult(
                status="error",
                message=str(e),
                method=method,
                url=url,
            )

        # Prepare request kwargs
        request_kwargs: dict[str, Any] = {
            "method": method,
            "url": url,
            "headers": headers,
        }

        if body is not None:
            if isinstance(body, (dict, list)):
                request_kwargs["json"] = body
            else:
                request_kwargs["content"] = body

        # Make request
        try:
            with httpx.Client(timeout=30.0) as http_client:
                response = http_client.request(**request_kwargs)
        except httpx.RequestError as e:
            console.print(f"[red]Request failed: {e}[/red]")
            return ApiResult(
                status="error",
                message=f"Request failed: {e}",
                method=method,
                url=url,
            )

        elapsed_ms = response.elapsed.total_seconds() * 1000

        # Handle response
        response_text = response.text
        response_body: Any = None

        try:
            response_body = response.json()
        except (json.JSONDecodeError, ValueError):
            response_body = response_text

        # Check for errors
        if response.status_code >= 400:
            console.print(
                f"[red]{method} {url} → {response.status_code}[/red]"
            )
            if isinstance(response_body, dict):
                console.print_json(data=response_body)
            elif response_text:
                console.print(f"[dim]{response_text[:1000]}[/dim]")

            return ApiResult(
                status="error",
                message=f"HTTP {response.status_code}",
                method=method,
                url=url,
                status_code=response.status_code,
                response_body=response_body,
                elapsed_ms=elapsed_ms,
            )

        # Apply jq filter if specified
        if jq_filter:
            try:
                json_text = (
                    json.dumps(response_body)
                    if isinstance(response_body, (dict, list))
                    else response_text
                )
                filtered = self._apply_jq_filter(json_text, jq_filter)
                console.print(filtered)
                return ApiResult(
                    status="success",
                    method=method,
                    url=url,
                    status_code=response.status_code,
                    response_body=filtered,
                    elapsed_ms=elapsed_ms,
                )
            except RuntimeError as e:
                console.print(f"[red]{e}[/red]")
                return ApiResult(
                    status="error",
                    message=str(e),
                    method=method,
                    url=url,
                    status_code=response.status_code,
                    response_body=response_body,
                    elapsed_ms=elapsed_ms,
                )

        # Output response
        if raw_output:
            console.print(response_text)
        elif isinstance(response_body, (dict, list)):
            console.print_json(data=response_body)
        else:
            console.print(response_text)

        return ApiResult(
            status="success",
            method=method,
            url=url,
            status_code=response.status_code,
            response_body=response_body,
            elapsed_ms=elapsed_ms,
        )
