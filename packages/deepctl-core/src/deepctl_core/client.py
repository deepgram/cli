"""Deepgram SDK wrapper for deepctl with authentication integration."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlencode

from .hosts import DEFAULT_BASE_URL
from .output import stderr_console

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import datetime

    # Bound at run time by _sdk() below, not by these imports.
    from deepgram import DeepgramClient as DGClient  # noqa: TC004
    from deepgram import DeepgramClientEnvironment  # noqa: TC004
    from deepgram.core.api_error import ApiError  # noqa: TC004

    from .auth import AuthManager
    from .config import Config

# The Deepgram SDK loads on first use, not when this module is imported (it
# pulls in httpx and pydantic models for every API). _sdk() binds these
# module globals; __getattr__ lets `deepctl_core.client.DGClient` (and
# mock.patch of it) work before anything here has used the SDK.
_SDK_NAMES: dict[str, tuple[str, str]] = {
    "ApiError": ("deepgram.core.api_error", "ApiError"),
    "DGClient": ("deepgram", "DeepgramClient"),
    "DeepgramClientEnvironment": ("deepgram", "DeepgramClientEnvironment"),
}


def _bind_sdk_name(name: str) -> Any:
    module, attr = _SDK_NAMES[name]
    import importlib

    value = getattr(importlib.import_module(module), attr)
    globals()[name] = value
    return value


# Only at run time: type checkers see the TYPE_CHECKING imports above.
if not TYPE_CHECKING:

    def __getattr__(name: str) -> Any:
        if name not in _SDK_NAMES:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        return _bind_sdk_name(name)


def _sdk(*names: str) -> None:
    """Bind SDK names that aren't bound yet (all of them by default)."""
    for name in names or tuple(_SDK_NAMES):
        if name not in globals():
            _bind_sdk_name(name)


# Diagnostics only: everything printed here is status or error chrome, so
# it goes to stderr and can never corrupt the machine-readable payload a
# command writes to stdout. See get_status_console() in output.py.
console = stderr_console


def _split_base_url(base_url: str) -> tuple[str, str, str]:
    """Split a base URL into (rest_scheme, ws_scheme, host).

    Preserves the transport implied by the input scheme so a plaintext
    endpoint is not forced onto TLS: an ``http://`` base maps to ``http`` for
    REST and ``ws`` for WebSocket; anything else (including a bare host) maps
    to ``https``/``wss``. The host is returned without scheme or trailing slash.
    """
    match = re.match(r"^([a-z]+)://", base_url, re.IGNORECASE)
    scheme = match.group(1).lower() if match else "https"
    host = re.sub(r"^[a-z]+://", "", base_url, flags=re.IGNORECASE).rstrip("/")
    if scheme in ("http", "ws"):
        return "http", "ws", host
    return "https", "wss", host


class DeepgramClient:
    """Wrapper around Deepgram SDK with authentication integration."""

    def __init__(self, config: Config, auth_manager: AuthManager):
        _sdk("ApiError")  # raised before the client exists, too
        self.config = config
        self.auth_manager = auth_manager
        self._client: DGClient | None = None
        self._project_id: str | None = None
        # The REST base the SDK client was given (for redirect errors).
        self._rest_base: str = DEFAULT_BASE_URL

    @property
    def client(self) -> DGClient:
        """Get authenticated Deepgram client."""
        if self._client is None:
            self._client = self._create_client()
        return self._client

    def _resolve_project_id(self, project_id: str | None = None) -> str:
        """Resolve project ID from argument, config, or auth manager."""
        if project_id:
            return project_id
        _ = self.client  # ensure client is initialized
        resolved = self._project_id or self.auth_manager.get_project_id()
        if not resolved:
            raise ApiError(body="No project ID available")
        return resolved

    def _create_client(self) -> DGClient:
        """Create authenticated Deepgram client."""
        _sdk()
        self.auth_manager.guard()
        api_key = self.auth_manager.get_api_key()
        project_id = self.auth_manager.get_project_id()

        if not api_key:
            raise ApiError(body="No API key available")

        # The SDK sends the key to whatever environment it is given, so the
        # base URL goes through the same host check as every other path.
        base_url = self.auth_manager.check_base_url()

        try:
            kwargs: dict[str, Any] = {"api_key": api_key}

            if base_url and base_url != DEFAULT_BASE_URL:
                # Derive both the REST and WebSocket endpoints from the
                # configured base URL, so a custom base works for batch REST
                # *and* streaming. Preserve the transport implied by the input
                # scheme (http→ws, https→wss) so a plaintext local/staging
                # endpoint isn't forced onto TLS. agent_rest is required by the
                # SDK environment.
                rest_scheme, ws_scheme, host = _split_base_url(base_url)
                self._rest_base = f"{rest_scheme}://{host}"
                kwargs["environment"] = DeepgramClientEnvironment(
                    base=f"{rest_scheme}://{host}",
                    production=f"{ws_scheme}://{host}",
                    agent=f"{ws_scheme}://{host}",
                    agent_rest=f"{rest_scheme}://{host}",
                )

            # A redirect would take the request body (audio, text) to
            # another host, and with the SDK's default the key too on a
            # same-origin hop. Refuse it; _failed() words the error.
            kwargs["follow_redirects"] = False

            client = DGClient(**kwargs)
            self._project_id = project_id
            return client

        except Exception as e:
            console.print(f"[red]Error creating Deepgram client:[/red] {e}")
            raise ApiError(body=f"Failed to create client: {e}")

    def _failed(self, what: str, e: Exception) -> Exception:
        """The error to raise for a failed SDK call.

        A refused redirect passes through unchanged, and a 3xx answer
        (the SDK client doesn't follow redirects) becomes the same refusal.
        Anything else is wrapped as before.
        """
        from .websocket import RedirectRefused, redirect_refused

        if isinstance(e, RedirectRefused):
            return e
        status = getattr(e, "status_code", None)
        if isinstance(e, ApiError) and status is not None and 300 <= status < 400:
            headers = {k.lower(): v for k, v in (e.headers or {}).items()}
            return redirect_refused(
                self._rest_base, headers.get("location"), command="dg", payload=None
            )
        return ApiError(body=f"{what}: {e}")

    def _refuse_redirects_in(self, chunks: Iterator[bytes]) -> Iterator[bytes]:
        """Iterate ``chunks``, turning a 3xx answer into the refusal."""
        from .websocket import RedirectRefused

        try:
            yield from chunks
        except Exception as e:
            refused = self._failed("", e)
            if isinstance(refused, RedirectRefused):
                raise refused from e
            raise

    # ── Speech-to-Text (pre-recorded) ──────────────────────────────

    def transcribe_file(
        self,
        file_path: str | Path,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Transcribe an audio file."""
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Audio file not found: {file_path}")

        default_options: dict[str, Any] = {
            "model": "nova-3",
            "smart_format": True,
            "language": "en-US",
        }
        if options:
            default_options.update(options)

        try:
            with open(file_path, "rb") as audio_file:
                audio_data = audio_file.read()

            response = self.client.listen.v1.media.transcribe_file(
                request=audio_data, **default_options
            )
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise self._failed("Transcription failed", e)

    def transcribe_url(
        self, url: str, options: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Transcribe audio from URL."""
        default_options: dict[str, Any] = {
            "model": "nova-3",
            "smart_format": True,
            "language": "en-US",
        }
        if options:
            default_options.update(options)

        try:
            response = self.client.listen.v1.media.transcribe_url(
                url=url, **default_options
            )
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise self._failed("Transcription failed", e)

    # ── Text-to-Speech ─────────────────────────────────────────────

    def speak_text(
        self,
        text: str,
        model: str = "aura-2-asteria-en",
        encoding: str | None = None,
        container: str | None = None,
        sample_rate: float | None = None,
    ) -> Iterator[bytes]:
        """Generate speech audio from text. Returns an iterator of bytes."""
        try:
            kwargs: dict[str, Any] = {"text": text, "model": model}
            if encoding:
                kwargs["encoding"] = encoding
            if container:
                kwargs["container"] = container
            if sample_rate:
                kwargs["sample_rate"] = sample_rate

            audio = cast(
                "Iterator[bytes]", self.client.speak.v1.audio.generate(**kwargs)
            )
            # The request runs when the caller starts iterating.
            return self._refuse_redirects_in(audio)

        except Exception as e:
            raise self._failed("Text-to-speech failed", e)

    def speak_text_stream(
        self,
        text: str,
        model: str,
        encoding: str | None = None,
        sample_rate: float | None = None,
        speed: float | None = None,
        expressivity: int | None = None,
    ) -> Iterator[bytes]:
        """Stream TTS audio over the Flux v2 WebSocket (/v2/speak).

        Yields raw audio chunks as they arrive. The streaming transport emits
        raw (non-containerized) audio, so only linear16/mulaw/alaw encodings
        apply and sample_rate is sent as the string the streaming API expects.

        ``speed`` (0.85-1.15) and beta ``expressivity`` (-2..2, default 0) are
        Flux connect query parameters; they are only sent when set.
        """
        from deepgram.speak.v2.socket_client import V2SocketClient
        from deepgram.speak.v2.types.speak_v2speak import SpeakV2Speak

        from .websocket import connect_sync

        query: dict[str, str] = {"model": model}
        if encoding:
            query["encoding"] = encoding
        if sample_rate:
            query["sample_rate"] = str(int(sample_rate))
        if speed is not None:
            query["speed"] = str(speed)
        if expressivity is not None:
            query["expressivity"] = str(expressivity)

        # Opened here rather than through the SDK's speak.v2.connect, so a
        # redirect on the upgrade is refused instead of followed: the text
        # goes only to the host that passed the host check.
        # The SDK's own headers (User-Agent, X-Fern-*, Authorization), as
        # its connect sent them; creating the client runs the key and host
        # checks.
        headers = dict(self.client._client_wrapper.get_headers())
        url = f"{self.ws_base_url()}/v2/speak?{urlencode(query)}"
        try:
            with connect_sync(
                url,
                headers,
                command="dg speak",
                payload="text",
            ) as ws:
                conn = V2SocketClient(websocket=ws)
                conn.send_speak(SpeakV2Speak(type="Speak", text=text))
                conn.send_flush()
                conn.send_close()
                for message in conn:
                    # Audio arrives as raw bytes; control messages are parsed
                    # objects, which we skip here.
                    if isinstance(message, bytes):
                        yield message
        except Exception as e:
            raise self._failed("Text-to-speech streaming failed", e)

    def ws_base_url(self) -> str:
        """The WebSocket base for the checked base URL, without a slash."""
        base_url = self.auth_manager.check_base_url()
        if not base_url or base_url == DEFAULT_BASE_URL:
            base_url = DEFAULT_BASE_URL
        _rest, ws_scheme, host = _split_base_url(str(base_url))
        return f"{ws_scheme}://{host}"

    # ── Text Intelligence (Read) ───────────────────────────────────

    def analyze_text(
        self,
        text: str,
        sentiment: bool = False,
        summarize: bool = False,
        topics: bool = False,
        intents: bool = False,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Analyze text for sentiment, topics, intents, summary."""
        try:
            from deepgram.requests.read_v1request_text import ReadV1RequestTextParams

            kwargs: dict[str, Any] = {
                "request": ReadV1RequestTextParams(text=text),
            }
            if sentiment:
                kwargs["sentiment"] = True
            if summarize:
                kwargs["summarize"] = "v2"
            if topics:
                kwargs["topics"] = True
            if intents:
                kwargs["intents"] = True
            kwargs["language"] = language or "en"

            response = self.client.read.v1.text.analyze(**kwargs)
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise self._failed("Text analysis failed", e)

    # ── Models ─────────────────────────────────────────────────────

    def list_models(self, include_outdated: bool = False) -> dict[str, Any]:
        """List all public models."""
        try:
            kwargs: dict[str, Any] = {}
            if include_outdated:
                kwargs["include_outdated"] = True

            response = self.client.manage.v1.models.list(**kwargs)
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise self._failed("Failed to list models", e)

    def get_model(self, model_id: str) -> dict[str, Any]:
        """Get a specific model by ID."""
        try:
            response = self.client.manage.v1.models.get(model_id)
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise self._failed("Failed to get model", e)

    # ── Projects ───────────────────────────────────────────────────

    def get_projects(self) -> dict[str, Any]:
        """Get user's projects."""
        try:
            response = self.client.manage.v1.projects.list()
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise self._failed("Failed to get projects", e)

    def get_project(self, project_id: str | None = None) -> dict[str, Any]:
        """Get specific project."""
        pid = self._resolve_project_id(project_id)
        try:
            response = self.client.manage.v1.projects.get(pid)
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise self._failed("Failed to get project", e)

    def create_project(self, name: str, company: str | None = None) -> dict[str, Any]:
        """Create a new project."""
        try:
            project_data: dict[str, str] = {"name": name}
            if company:
                project_data["company"] = company

            response = self.client.manage.v1.projects.update(request=project_data)
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise self._failed("Failed to create project", e)

    # ── Usage ──────────────────────────────────────────────────────

    def get_usage(
        self,
        project_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """Get usage statistics."""
        pid = self._resolve_project_id(project_id)
        try:
            kwargs: dict[str, Any] = {}
            if start_date:
                kwargs["start"] = start_date
            if end_date:
                kwargs["end"] = end_date

            response = self.client.manage.v1.projects.usage.get(pid, **kwargs)
            result = cast("dict[str, Any]", response.model_dump())
            result["project_id"] = pid
            return result

        except Exception as e:
            raise self._failed("Failed to get usage", e)

    # ── API Keys ───────────────────────────────────────────────────

    def list_keys(
        self,
        project_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """List API keys for a project."""
        pid = self._resolve_project_id(project_id)
        try:
            kwargs: dict[str, Any] = {}
            if status:
                kwargs["status"] = status

            response = self.client.manage.v1.projects.keys.list(pid, **kwargs)
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise self._failed("Failed to list keys", e)

    def create_key(
        self,
        project_id: str | None = None,
        comment: str = "",
        scopes: list[str] | None = None,
        expiration_date: str | None = None,
        time_to_live: int | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new API key."""
        pid = self._resolve_project_id(project_id)
        try:
            request_data: dict[str, Any] = {
                "comment": comment,
                "scopes": scopes or ["member"],
            }
            if expiration_date:
                request_data["expiration_date"] = expiration_date
            if time_to_live:
                request_data["time_to_live_in_seconds"] = time_to_live
            if tags:
                request_data["tags"] = tags

            response = self.client.manage.v1.projects.keys.create(
                pid, request=request_data
            )
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise self._failed("Failed to create key", e)

    def get_key(self, key_id: str, project_id: str | None = None) -> dict[str, Any]:
        """Get a specific API key."""
        pid = self._resolve_project_id(project_id)
        try:
            response = self.client.manage.v1.projects.keys.get(pid, key_id)
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise self._failed("Failed to get key", e)

    def delete_key(self, key_id: str, project_id: str | None = None) -> dict[str, Any]:
        """Delete an API key."""
        pid = self._resolve_project_id(project_id)
        try:
            response = self.client.manage.v1.projects.keys.delete(pid, key_id)
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise self._failed("Failed to delete key", e)

    # ── Requests ───────────────────────────────────────────────────

    def list_requests(
        self,
        project_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
        page: int | None = None,
        status: str | None = None,
        endpoint: str | None = None,
        method: str | None = None,
    ) -> dict[str, Any]:
        """List API requests for a project."""
        pid = self._resolve_project_id(project_id)
        try:
            kwargs: dict[str, Any] = {}
            if start:
                kwargs["start"] = start
            if end:
                kwargs["end"] = end
            if limit:
                kwargs["limit"] = limit
            if page:
                kwargs["page"] = page
            if status:
                kwargs["status"] = status
            if endpoint:
                kwargs["endpoint"] = endpoint
            if method:
                kwargs["method"] = method

            response = self.client.manage.v1.projects.requests.list(pid, **kwargs)
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise self._failed("Failed to list requests", e)

    def get_request(
        self, request_id: str, project_id: str | None = None
    ) -> dict[str, Any]:
        """Get a specific API request."""
        pid = self._resolve_project_id(project_id)
        try:
            response = self.client.manage.v1.projects.requests.get(pid, request_id)
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise self._failed("Failed to get request", e)

    # ── Billing ────────────────────────────────────────────────────

    def get_billing_breakdown(
        self,
        project_id: str | None = None,
        start: str | None = None,
        end: str | None = None,
        grouping: str | None = None,
    ) -> dict[str, Any]:
        """Get billing breakdown for a project."""
        pid = self._resolve_project_id(project_id)
        try:
            kwargs: dict[str, Any] = {}
            if start:
                kwargs["start"] = start
            if end:
                kwargs["end"] = end
            if grouping:
                kwargs["grouping"] = grouping

            response = self.client.manage.v1.projects.billing.breakdown.list(
                pid, **kwargs
            )
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise self._failed("Failed to get billing breakdown", e)

    def get_balances(self, project_id: str | None = None) -> dict[str, Any]:
        """Get project balances."""
        pid = self._resolve_project_id(project_id)
        try:
            response = self.client.manage.v1.projects.billing.balances.list(pid)
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise self._failed("Failed to get balances", e)

    # ── Members ────────────────────────────────────────────────────

    def list_members(self, project_id: str | None = None) -> dict[str, Any]:
        """List project members."""
        pid = self._resolve_project_id(project_id)
        try:
            response = self.client.manage.v1.projects.members.list(pid)
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise self._failed("Failed to list members", e)

    def remove_member(
        self, member_id: str, project_id: str | None = None
    ) -> dict[str, Any]:
        """Remove a member from the project."""
        pid = self._resolve_project_id(project_id)
        try:
            response = self.client.manage.v1.projects.members.delete(pid, member_id)
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise self._failed("Failed to remove member", e)

    def list_member_scopes(
        self, member_id: str, project_id: str | None = None
    ) -> dict[str, Any]:
        """List scopes for a project member."""
        pid = self._resolve_project_id(project_id)
        try:
            response = self.client.manage.v1.projects.members.scopes.list(
                pid, member_id
            )
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise self._failed("Failed to list member scopes", e)

    def update_member_scopes(
        self, member_id: str, scope: str, project_id: str | None = None
    ) -> dict[str, Any]:
        """Update scopes for a project member."""
        pid = self._resolve_project_id(project_id)
        try:
            response = self.client.manage.v1.projects.members.scopes.update(
                pid, member_id, scope=scope
            )
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise self._failed("Failed to update member scopes", e)

    # ── Invites ────────────────────────────────────────────────────

    def list_invites(self, project_id: str | None = None) -> dict[str, Any]:
        """List project invites."""
        pid = self._resolve_project_id(project_id)
        try:
            response = self.client.manage.v1.projects.members.invites.list(pid)
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise self._failed("Failed to list invites", e)

    def create_invite(
        self, email: str, scope: str, project_id: str | None = None
    ) -> dict[str, Any]:
        """Invite a member to the project."""
        pid = self._resolve_project_id(project_id)
        try:
            response = self.client.manage.v1.projects.members.invites.create(
                pid, email=email, scope=scope
            )
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise self._failed("Failed to create invite", e)

    def delete_invite(
        self, email: str, project_id: str | None = None
    ) -> dict[str, Any]:
        """Delete a project invite."""
        pid = self._resolve_project_id(project_id)
        try:
            response = self.client.manage.v1.projects.members.invites.delete(pid, email)
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise self._failed("Failed to delete invite", e)

    # ── Utilities ──────────────────────────────────────────────────

    def validate_api_key(self, api_key: str | None = None) -> bool:
        """Validate API key by making a simple API call."""
        project_id = self.auth_manager.get_project_id()
        success, _, _ = self.auth_manager.verify_credentials(
            api_key=api_key, project_id=project_id
        )
        return success

    def test_connection(self) -> bool:
        """Test connection to Deepgram API."""
        success, message, _ = self.auth_manager.verify_credentials()
        if not success:
            console.print(f"[red]Connection test failed:[/red] {message}")
        return success
