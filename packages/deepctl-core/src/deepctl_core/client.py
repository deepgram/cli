"""Deepgram SDK wrapper for deepctl with authentication integration."""

from pathlib import Path
from typing import Any

from deepgram import (  # type: ignore[import-untyped]
    DeepgramClient as DGClient,
)
from deepgram import (
    DeepgramClientOptions,
    DeepgramError,
)
from deepgram.clients.prerecorded import (  # type: ignore[import-untyped]
    PrerecordedOptions,
)
from rich.console import Console

from .auth import AuthManager
from .config import Config

console = Console()


class DeepgramClient:
    """Wrapper around Deepgram SDK with authentication integration."""

    def __init__(self, config: Config, auth_manager: AuthManager):
        """Initialize Deepgram client.

        Args:
            config: Configuration manager
            auth_manager: Authentication manager
        """
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

    def _create_client(self) -> DGClient:
        """Create authenticated Deepgram client."""
        # Ensure user is authenticated and credentials are valid
        self.auth_manager.guard()

        # Get API key and project ID
        api_key = self.auth_manager.get_api_key()
        project_id = self.auth_manager.get_project_id()

        if not api_key:
            raise DeepgramError("No API key available")

        # Create client with configuration
        current_profile = self.config.get_profile()

        try:
            # Create options if we have a custom base URL
            options = None
            if current_profile.base_url:
                options = DeepgramClientOptions(url=current_profile.base_url)

            # Create client with API key and options
            client = (
                DGClient(api_key, options) if options else DGClient(api_key)
            )

            # Store project ID for later use
            self._project_id = project_id

            return client

        except Exception as e:
            console.print(f"[red]Error creating Deepgram client:[/red] {e}")
            raise DeepgramError(f"Failed to create client: {e}")

    def transcribe_file(
        self,
        file_path: str | Path,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Transcribe an audio file.

        Args:
            file_path: Path to audio file
            options: Transcription options

        Returns:
            Transcription results
        """
        file_path = Path(file_path)

        if not file_path.exists():
            raise FileNotFoundError(f"Audio file not found: {file_path}")

        # Default options
        default_options = {
            "model": "nova-2",
            "smart_format": True,
            "language": "en-US",
        }

        if options:
            default_options.update(options)

        try:
            # Read file
            with open(file_path, "rb") as audio_file:
                payload = {"buffer": audio_file}

                # Create options object
                prerecorded_options = PrerecordedOptions(**default_options)

                # Make request using the new SDK API
                response = self.client.listen.rest.v("1").transcribe_file(
                    payload, prerecorded_options
                )

                return dict(response)

        except Exception as e:
            console.print(f"[red]Error transcribing file:[/red] {e}")
            raise DeepgramError(f"Transcription failed: {e}")

    def transcribe_url(
        self, url: str, options: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Transcribe audio from URL.

        Args:
            url: URL to audio file
            options: Transcription options

        Returns:
            Transcription results
        """
        # Default options
        default_options = {
            "model": "nova-2",
            "smart_format": True,
            "language": "en-US",
        }

        if options:
            default_options.update(options)

        try:
            # Create payload
            payload = {"url": url}

            # Create options object
            prerecorded_options = PrerecordedOptions(**default_options)

            # Make request using the new SDK API
            response = self.client.listen.rest.v("1").transcribe_url(
                payload, prerecorded_options
            )

            return dict(response)

        except Exception as e:
            console.print(f"[red]Error transcribing URL:[/red] {e}")
            raise DeepgramError(f"Transcription failed: {e}")

    def get_projects(self) -> dict[str, Any]:
        """Get user's projects.

        Returns:
            Projects data
        """
        try:
            response = self.client.manage.v("1").get_projects()
            return dict(response)

        except Exception as e:
            console.print(f"[red]Error getting projects:[/red] {e}")
            raise DeepgramError(f"Failed to get projects: {e}")

    def get_project(self, project_id: str | None = None) -> dict[str, Any]:
        """Get specific project.

        Args:
            project_id: Project ID (uses configured project if not provided)

        Returns:
            Project data
        """
        if not project_id:
            # Ensure client is initialized which sets _project_id
            _ = self.client
            project_id = self._project_id or self.auth_manager.get_project_id()

        if not project_id:
            raise DeepgramError("No project ID available")

        try:
            response = self.client.manage.v("1").get_project(project_id)
            return dict(response)

        except Exception as e:
            console.print(f"[red]Error getting project:[/red] {e}")
            raise DeepgramError(f"Failed to get project: {e}")

    def create_project(
        self, name: str, company: str | None = None
    ) -> dict[str, Any]:
        """Create a new project.

        Args:
            name: Project name
            company: Company name

        Returns:
            Created project data
        """
        try:
            project_data = {"name": name}
            if company:
                project_data["company"] = company

            response = self.client.manage.v("1").create_project(project_data)
            return dict(response)

        except Exception as e:
            console.print(f"[red]Error creating project:[/red] {e}")
            raise DeepgramError(f"Failed to create project: {e}")

    def get_usage(
        self,
        project_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """Get usage statistics.

        Args:
            project_id: Project ID (uses configured project if not provided)
            start_date: Start date (ISO format)
            end_date: End date (ISO format)

        Returns:
            Usage data
        """
        if not project_id:
            # Ensure client is initialized which sets _project_id
            _ = self.client
            project_id = self._project_id or self.auth_manager.get_project_id()

        if not project_id:
            raise DeepgramError("No project ID available")

        try:
            # Build query parameters
            params = {}
            if start_date:
                params["start"] = start_date
            if end_date:
                params["end"] = end_date

            response = self.client.manage.v("1").get_usage_summary(
                project_id, params
            )
            # Add project_id to response for consistency
            if isinstance(response, dict):
                response["project_id"] = project_id
            return dict(response)

        except Exception as e:
            console.print(f"[red]Error getting usage:[/red] {e}")
            raise DeepgramError(f"Failed to get usage: {e}")

    def get_models(self, project_id: str | None = None) -> dict[str, Any]:
        """Get available models.

        Args:
            project_id: Project ID (uses configured project if not provided)

        Returns:
            Models data
        """
        if not project_id:
            # Ensure client is initialized which sets _project_id
            _ = self.client
            project_id = self._project_id or self.auth_manager.get_project_id()

        try:
            # The new SDK doesn't have get_models, this is likely part of
            # get_project
            response = self.client.manage.v("1").get_project(project_id)
            return dict(response)

        except Exception as e:
            console.print(f"[red]Error getting models:[/red] {e}")
            raise DeepgramError(f"Failed to get models: {e}")

    def validate_api_key(self, api_key: str | None = None) -> bool:
        """Validate API key by making a simple API call.

        Args:
            api_key: API key to validate (uses configured key if not provided)

        Returns:
            True if valid, False otherwise
        """
        # Use the auth manager's verification method
        project_id = self.auth_manager.get_project_id()
        success, _, _ = self.auth_manager.verify_credentials(
            api_key=api_key, project_id=project_id
        )
        return success

    def test_connection(self) -> bool:
        """Test connection to Deepgram API.

        Returns:
            True if connection successful, False otherwise
        """
        success, message, _ = self.auth_manager.verify_credentials()
        if not success:
            console.print(f"[red]Connection test failed:[/red] {message}")
        return success
