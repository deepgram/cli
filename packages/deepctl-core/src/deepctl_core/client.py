"""Deepgram SDK wrapper for deepctl with authentication integration."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from deepgram import DeepgramClient as DGClient
from deepgram import DeepgramClientEnvironment
from deepgram.core.api_error import ApiError
from rich.console import Console

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import datetime

    from .auth import AuthManager
    from .config import Config

console = Console()


class DeepgramClient:
    """Wrapper around Deepgram SDK with authentication integration."""

    def __init__(self, config: Config, auth_manager: AuthManager):
        self.config = config
        self.auth_manager = auth_manager
        self._client: DGClient | None = None
        self._project_id: str | None = None

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
        self.auth_manager.guard()
        api_key = self.auth_manager.get_api_key()
        project_id = self.auth_manager.get_project_id()

        if not api_key:
            raise ApiError(body="No API key available")

        current_profile = self.config.get_profile()

        try:
            kwargs: dict[str, Any] = {"api_key": api_key}

            if (
                current_profile.base_url
                and current_profile.base_url != "https://api.deepgram.com"
            ):
                kwargs["environment"] = DeepgramClientEnvironment(
                    base=current_profile.base_url,
                    production=current_profile.base_url,
                    agent=current_profile.base_url,
                )

            client = DGClient(**kwargs)
            self._project_id = project_id
            return client

        except Exception as e:
            console.print(f"[red]Error creating Deepgram client:[/red] {e}")
            raise ApiError(body=f"Failed to create client: {e}")

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
            raise ApiError(body=f"Transcription failed: {e}")

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
            raise ApiError(body=f"Transcription failed: {e}")

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

            return cast(
                "Iterator[bytes]", self.client.speak.v1.audio.generate(**kwargs)
            )

        except Exception as e:
            raise ApiError(body=f"Text-to-speech failed: {e}")

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
            raise ApiError(body=f"Text analysis failed: {e}")

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
            raise ApiError(body=f"Failed to list models: {e}")

    def get_model(self, model_id: str) -> dict[str, Any]:
        """Get a specific model by ID."""
        try:
            response = self.client.manage.v1.models.get(model_id)
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise ApiError(body=f"Failed to get model: {e}")

    # ── Projects ───────────────────────────────────────────────────

    def get_projects(self) -> dict[str, Any]:
        """Get user's projects."""
        try:
            response = self.client.manage.v1.projects.list()
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise ApiError(body=f"Failed to get projects: {e}")

    def get_project(self, project_id: str | None = None) -> dict[str, Any]:
        """Get specific project."""
        pid = self._resolve_project_id(project_id)
        try:
            response = self.client.manage.v1.projects.get(pid)
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise ApiError(body=f"Failed to get project: {e}")

    def create_project(self, name: str, company: str | None = None) -> dict[str, Any]:
        """Create a new project."""
        try:
            project_data: dict[str, str] = {"name": name}
            if company:
                project_data["company"] = company

            response = self.client.manage.v1.projects.update(request=project_data)
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise ApiError(body=f"Failed to create project: {e}")

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
            raise ApiError(body=f"Failed to get usage: {e}")

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
            raise ApiError(body=f"Failed to list keys: {e}")

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
            raise ApiError(body=f"Failed to create key: {e}")

    def get_key(self, key_id: str, project_id: str | None = None) -> dict[str, Any]:
        """Get a specific API key."""
        pid = self._resolve_project_id(project_id)
        try:
            response = self.client.manage.v1.projects.keys.get(pid, key_id)
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise ApiError(body=f"Failed to get key: {e}")

    def delete_key(self, key_id: str, project_id: str | None = None) -> dict[str, Any]:
        """Delete an API key."""
        pid = self._resolve_project_id(project_id)
        try:
            response = self.client.manage.v1.projects.keys.delete(pid, key_id)
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise ApiError(body=f"Failed to delete key: {e}")

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
            raise ApiError(body=f"Failed to list requests: {e}")

    def get_request(
        self, request_id: str, project_id: str | None = None
    ) -> dict[str, Any]:
        """Get a specific API request."""
        pid = self._resolve_project_id(project_id)
        try:
            response = self.client.manage.v1.projects.requests.get(pid, request_id)
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise ApiError(body=f"Failed to get request: {e}")

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
            raise ApiError(body=f"Failed to get billing breakdown: {e}")

    def get_balances(self, project_id: str | None = None) -> dict[str, Any]:
        """Get project balances."""
        pid = self._resolve_project_id(project_id)
        try:
            response = self.client.manage.v1.projects.billing.balances.list(pid)
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise ApiError(body=f"Failed to get balances: {e}")

    # ── Members ────────────────────────────────────────────────────

    def list_members(self, project_id: str | None = None) -> dict[str, Any]:
        """List project members."""
        pid = self._resolve_project_id(project_id)
        try:
            response = self.client.manage.v1.projects.members.list(pid)
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise ApiError(body=f"Failed to list members: {e}")

    def remove_member(
        self, member_id: str, project_id: str | None = None
    ) -> dict[str, Any]:
        """Remove a member from the project."""
        pid = self._resolve_project_id(project_id)
        try:
            response = self.client.manage.v1.projects.members.delete(pid, member_id)
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise ApiError(body=f"Failed to remove member: {e}")

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
            raise ApiError(body=f"Failed to list member scopes: {e}")

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
            raise ApiError(body=f"Failed to update member scopes: {e}")

    # ── Invites ────────────────────────────────────────────────────

    def list_invites(self, project_id: str | None = None) -> dict[str, Any]:
        """List project invites."""
        pid = self._resolve_project_id(project_id)
        try:
            response = self.client.manage.v1.projects.members.invites.list(pid)
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise ApiError(body=f"Failed to list invites: {e}")

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
            raise ApiError(body=f"Failed to create invite: {e}")

    def delete_invite(
        self, email: str, project_id: str | None = None
    ) -> dict[str, Any]:
        """Delete a project invite."""
        pid = self._resolve_project_id(project_id)
        try:
            response = self.client.manage.v1.projects.members.invites.delete(pid, email)
            return cast("dict[str, Any]", response.model_dump())

        except Exception as e:
            raise ApiError(body=f"Failed to delete invite: {e}")

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
