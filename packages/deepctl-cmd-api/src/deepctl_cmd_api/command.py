"""API command for deepctl."""

import json
import os
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from deepctl_core import (
    AuthManager,
    BaseCommand,
    Config,
    DeepgramClient,
    is_agentic,
    is_ai_agent,
)
from deepctl_core import messages as core_msg
from deepctl_core.hosts import (
    DEFAULT_BASE_URL,
    UrlOrigin,
    is_allowlisted,
    is_loopback,
    is_regional,
    parse_origin,
    same_origin,
)
from deepctl_core.output import (
    print_error,
    print_hint,
    print_security_warning,
)
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from . import messages as msg
from .models import ApiErrorResult, ApiResult

if TYPE_CHECKING:
    import httpx


# httpx (and with it ssl) loads only when a request is sent, so `dg api
# --help` doesn't import it.
def __getattr__(name: str) -> Any:
    if name == "httpx":
        import httpx

        return httpx
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


console = Console()


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
                    "Request body field as key=value (string) or key:=value (raw JSON)"
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
            {
                "names": ["--allow-foreign-host"],
                "help": (
                    "Send your API key to this non-Deepgram host (must equal "
                    "the request URL's host)"
                ),
                "type": str,
                "is_option": True,
            },
        ]

    def _resolve_url(self, endpoint: str, base_url: str = DEFAULT_BASE_URL) -> str:
        """Resolve endpoint to full URL, relative to the profile's base URL."""
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            return endpoint
        base = base_url.rstrip("/")
        path = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        return f"{base}{path}"

    @staticmethod
    def _display_url(url: str) -> str:
        """The URL without userinfo, query or fragment."""
        try:
            parts = urlsplit(url)
            netloc = parts.hostname or ""
            if ":" in netloc:
                netloc = f"[{netloc}]"
            if parts.port is not None:
                netloc = f"{netloc}:{parts.port}"
            return urlunsplit((parts.scheme, netloc, parts.path, "", ""))
        except ValueError:
            return ""

    def pre_auth_check(self, config: Config, **kwargs: Any) -> Any | None:
        """Refuse a disallowed --allow-foreign-host before any request."""
        endpoint = kwargs.get("endpoint") or ""
        allow_host = kwargs.get("allow_foreign_host")
        if not allow_host or not endpoint.startswith(("http://", "https://")):
            return None
        method = (kwargs.get("method") or "GET").upper()
        base_url = config.get_profile().base_url or DEFAULT_BASE_URL
        _, refusal = self._authorize(
            endpoint, base_url, config, allow_host, method, warn=False
        )
        return refusal

    def _authorize(
        self,
        url: str,
        base_url: str,
        config: Config,
        allow_host: str | None,
        method: str,
        warn: bool = True,
    ) -> tuple[bool, ApiErrorResult | None]:
        """Decide whether the request to ``url`` may carry the API key.

        Returns ``(attach, error)``; with an error, nothing is sent.
        """
        target = parse_origin(url)
        configured = parse_origin(base_url)
        allowed = config.allowed_hosts()
        if target is None:
            return False, None

        trusted = (
            (configured is not None and same_origin(target, configured))
            or is_regional(target)
            or is_allowlisted(target, allowed)
        )
        host = core_msg.display(target.host)

        if not trusted:
            if not allow_host:
                if not warn:
                    return False, None
                print_security_warning(escape(msg.W_API_NOAUTH.format(host=host)))
                # The hint names the override flag: never in agent mode.
                if not is_agentic() and not is_ai_agent():
                    print_hint(escape(msg.W_API_NOAUTH_HINT.format(host=host)))
                return False, None
            # Refuse the command-line override under a hard AI-agent signal
            # (never CI or TTY state); api.allowed_hosts is the real control.
            if is_ai_agent():
                return False, self._refuse(
                    msg.E_API_AGENT.format(host=host),
                    msg.E_API_AGENT_HINT.format(host=host),
                    core_msg.CODE_FOREIGN_HOST,
                    method,
                    url,
                )
            if not self._flag_matches(allow_host, target):
                return False, self._refuse(
                    msg.E_API_MISMATCH.format(
                        given=core_msg.display(allow_host), host=host
                    ),
                    msg.E_API_MISMATCH_HINT.format(host=host),
                    core_msg.CODE_FOREIGN_HOST,
                    method,
                    url,
                )

        # Plain http only to a loopback host the user configured or
        # allowlisted; the command-line override is https only.
        if not target.secure and (not trusted or not is_loopback(target.host)):
            hint = msg.E_API_INSECURE_HINT
            return False, self._refuse(
                msg.E_API_INSECURE.format(origin=core_msg.display(target.origin)),
                hint,
                core_msg.CODE_INSECURE_BASE_URL,
                method,
                url,
            )
        return True, None

    @staticmethod
    def _flag_matches(given: str, target: UrlOrigin) -> bool:
        """``--allow-foreign-host`` names exactly the request host."""
        value = given.strip().lower()
        parsed = parse_origin(f"https://{value}") if value else None
        if parsed is None or parsed.host != target.host:
            return False
        return parsed.port is None or parsed.port == target.effective_port

    def _refuse(
        self, error: str, hint: str, code: str, method: str, url: str
    ) -> ApiErrorResult:
        print_error(escape(error))
        print_hint(escape(hint))
        return ApiErrorResult(
            status="error",
            message=error,
            method=method,
            url=self._display_url(url),
            code=code,
            hint=hint,
        )

    def _warn_redirect(
        self, response: "httpx.Response", target: UrlOrigin | None
    ) -> None:
        """Note a redirect that wasn't followed."""
        location = response.headers.get("location")
        if not location or target is None:
            return
        dest = parse_origin(str(response.url.join(location)))
        print_security_warning(
            escape(
                msg.W_API_REDIRECT.format(
                    origin=core_msg.display(target.origin),
                    location_origin=core_msg.display(dest.origin if dest else "?"),
                )
            )
        )

    def _build_headers(
        self,
        api_key: str | None,
        custom_headers: tuple[str, ...] | list[str] | None,
    ) -> dict[str, str]:
        """Build request headers with auth (when given) and custom headers."""
        from deepctl_core.transport import request_headers

        headers: dict[str, str] = {}
        if api_key:
            # The key's host passed the check; the session ID goes only
            # where the key goes.
            headers = request_headers(api_key)
        if custom_headers:
            for header in custom_headers:
                if ":" in header:
                    key, value = header.split(":", 1)
                    key = key.strip()
                    # A -H header replaces one of the same name, in any case.
                    for name in [k for k in headers if k.lower() == key.lower()]:
                        del headers[name]
                    headers[key] = value.strip()
        return headers

    def _build_body(
        self,
        fields: tuple[str, ...] | list[str] | None,
        input_source: str | None,
    ) -> Any | None:
        """Build request body from fields or input source."""
        if fields and input_source:
            raise ValueError("Cannot use both --field and --input. Choose one.")

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

    @staticmethod
    def _insecure_ignored(url: str, error: BaseException) -> bool:
        """A TLS failure on a loopback host while DEEPGRAM_CLI_INSECURE would
        have skipped verification for the SDK: say it doesn't reach dg api."""
        from deepctl_core import auth, output, transport

        if output.is_ai_agent():
            return False
        value = os.environ.get("DEEPGRAM_CLI_INSECURE", "").strip().lower()
        origin = parse_origin(url)
        if value not in ("1", "true") or origin is None:
            return False
        if origin.host not in auth._INSECURE_HOSTS:
            return False
        mapped = transport.map_exception(error)
        return (
            mapped is not None and mapped.details.get("reason") == transport.REASON_TLS
        )

    def handle(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> Any:
        """Handle API command execution."""
        import httpx

        endpoint = kwargs.get("endpoint", "")
        method = (kwargs.get("method") or "GET").upper()
        fields = kwargs.get("field") or ()
        input_source = kwargs.get("input")
        custom_headers = kwargs.get("header") or ()
        jq_filter = kwargs.get("jq")
        raw_output = kwargs.get("raw", False)
        allow_host = kwargs.get("allow_foreign_host")

        from deepctl_core import transport

        # Resolve URL against the profile's base URL. The Transport's host
        # check raises (already reported) when that host isn't trusted with
        # the key.
        base_url = transport.Transport(
            config, check_host=auth_manager.check_base_url
        ).rest_base()
        url = self._resolve_url(endpoint, base_url)

        attach, refusal = self._authorize(url, base_url, config, allow_host, method)
        if refusal is not None:
            return refusal

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

        # Build headers (the key only goes to a host that may receive it)
        headers = self._build_headers(api_key if attach else None, custom_headers)

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
            # The process's one client (one connection), with TLS verified
            # for every host as in 0.4.1: DEEPGRAM_CLI_INSECURE doesn't reach
            # dg api. Never follow redirects: a 3xx could point the key
            # elsewhere.
            response = transport.verified_client().request(
                **request_kwargs, follow_redirects=False, timeout=30.0
            )
        except httpx.RequestError as e:
            console.print(f"[red]Request failed: {e}[/red]")
            if self._insecure_ignored(url, e):
                print_hint(msg.H_API_INSECURE_IGNORED)
            return ApiResult(
                status="error",
                message=f"Request failed: {e}",
                method=method,
                url=url,
            )

        elapsed_ms = response.elapsed.total_seconds() * 1000
        if response.is_redirect:
            self._warn_redirect(response, parse_origin(url))

        # Handle response
        response_text = response.text
        response_body: Any = None

        try:
            response_body = response.json()
        except (json.JSONDecodeError, ValueError):
            response_body = response_text

        # Check for errors
        if response.status_code >= 400:
            console.print(f"[red]{method} {url} → {response.status_code}[/red]")
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
