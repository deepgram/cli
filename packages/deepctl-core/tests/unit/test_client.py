"""Tests for the DeepgramClient class."""

import pytest
from unittest.mock import Mock, patch, MagicMock, mock_open
from pathlib import Path

from deepgram.core.api_error import ApiError
from deepctl_core.client import DeepgramClient
from deepctl_core.auth import AuthManager
from deepctl_core.config import Config


class TestDeepgramClient:
    """Test DeepgramClient class."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock config instance."""
        config = Mock(spec=Config)

        # Mock profile with standard settings
        mock_profile = Mock()
        mock_profile.api_key = "sk-test"
        mock_profile.project_id = "test-project"
        mock_profile.base_url = "https://api.deepgram.com"

        config.get_profile.return_value = mock_profile
        return config

    @pytest.fixture
    def mock_auth_manager(self):
        """Create a mock auth manager."""
        auth_manager = Mock(spec=AuthManager)
        auth_manager.get_api_key.return_value = "sk-test"
        auth_manager.get_project_id.return_value = "test-project"
        return auth_manager

    @pytest.fixture
    def client(self, mock_config, mock_auth_manager):
        """Create a DeepgramClient instance."""
        return DeepgramClient(mock_config, mock_auth_manager)

    def test_init(self, mock_config, mock_auth_manager):
        """Test client initialization."""
        client = DeepgramClient(mock_config, mock_auth_manager)

        assert client.config == mock_config
        assert client.auth_manager == mock_auth_manager
        assert client._client is None
        assert client._project_id is None

    @patch("deepctl_core.client.DGClient")
    def test_client_property_creates_client(
        self, mock_dg_client, client, mock_auth_manager
    ):
        """Test that accessing client property creates DG client."""
        mock_instance = Mock()
        mock_dg_client.return_value = mock_instance

        # Access the client property
        result = client.client

        # Verify auth guard was called
        mock_auth_manager.guard.assert_called_once()

        # Verify DGClient was created with keyword args
        assert mock_dg_client.called
        assert mock_dg_client.call_args.kwargs["api_key"] == "sk-test"

        # Verify client is cached
        assert client._client == mock_instance
        assert result == mock_instance

    @patch("deepctl_core.client.DGClient")
    def test_client_property_reuses_existing(
        self, mock_dg_client, client, mock_auth_manager
    ):
        """Test that client property reuses existing client."""
        mock_instance = Mock()
        client._client = mock_instance

        # Access the client property
        result = client.client

        # Verify no new client was created
        mock_dg_client.assert_not_called()
        mock_auth_manager.guard.assert_not_called()

        assert result == mock_instance

    @patch("deepctl_core.client.DGClient")
    @patch("deepctl_core.client.DeepgramClientEnvironment")
    def test_create_client_with_custom_base_url(
        self, mock_env, mock_dg_client, mock_config, mock_auth_manager
    ):
        """Test creating client with custom base URL."""
        # Set custom base URL
        mock_profile = Mock()
        mock_profile.base_url = "https://custom.deepgram.com"
        mock_config.get_profile.return_value = mock_profile

        client = DeepgramClient(mock_config, mock_auth_manager)
        mock_instance = Mock()
        mock_dg_client.return_value = mock_instance

        # Create client
        result = client._create_client()

        # Verify environment was created with custom URL
        mock_env.assert_called_once_with(
            base="https://custom.deepgram.com",
            production="https://custom.deepgram.com",
            agent="https://custom.deepgram.com",
        )

        # Verify DGClient was created with environment kwarg
        mock_dg_client.assert_called_once_with(
            api_key="sk-test",
            environment=mock_env.return_value,
        )

    def test_create_client_no_api_key(self, client, mock_auth_manager):
        """Test creating client without API key raises error."""
        mock_auth_manager.get_api_key.return_value = None

        with pytest.raises(ApiError):
            client._create_client()

    @patch("deepctl_core.client.DGClient")
    def test_create_client_error_handling(
        self, mock_dg_client, client, mock_auth_manager
    ):
        """Test error handling when creating client fails."""
        mock_dg_client.side_effect = Exception("Connection error")

        with pytest.raises(ApiError):
            client._create_client()

    @patch("deepctl_core.client.DGClient")
    @patch("deepctl_core.client.Path.exists")
    @patch("builtins.open", new_callable=mock_open, read_data=b"audio data")
    def test_transcribe_file(
        self, mock_file, mock_exists, mock_dg_client, client
    ):
        """Test transcribing a file."""
        # Setup mocks
        mock_exists.return_value = True
        mock_instance = Mock()
        mock_media = Mock()
        mock_response = {"transcript": "Hello world"}
        mock_media.transcribe_file.return_value = mock_response
        mock_instance.listen.v1.media = mock_media
        mock_dg_client.return_value = mock_instance

        # Test transcription
        result = client.transcribe_file(
            "/audio/test.mp3", options={"model": "nova-2", "language": "en"}
        )

        assert result == {"transcript": "Hello world"}

        # Verify transcribe_file was called
        mock_media.transcribe_file.assert_called_once()

    @patch("deepctl_core.client.DGClient")
    def test_transcribe_url(self, mock_dg_client, client):
        """Test transcribing a URL."""
        # Setup mock client
        mock_instance = Mock()
        mock_media = Mock()
        mock_response = {"transcript": "Hello world"}
        mock_media.transcribe_url.return_value = mock_response
        mock_instance.listen.v1.media = mock_media
        mock_dg_client.return_value = mock_instance

        # Test transcription
        result = client.transcribe_url(
            "https://example.com/audio.mp3",
            options={"model": "nova-2", "detect_language": True},
        )

        assert result == {"transcript": "Hello world"}

        # Verify transcribe_url was called
        mock_media.transcribe_url.assert_called_once()

    @patch("deepctl_core.client.DGClient")
    @patch("deepctl_core.client.Path.exists")
    def test_transcribe_file_not_found(
        self, mock_exists, mock_dg_client, client
    ):
        """Test error when file not found."""
        mock_exists.return_value = False

        with pytest.raises(FileNotFoundError, match="Audio file not found"):
            client.transcribe_file("/audio/test.mp3")

    @patch("deepctl_core.client.DGClient")
    def test_get_projects(self, mock_dg_client, client):
        """Test getting projects list."""
        # Setup mock
        mock_instance = Mock()
        mock_manage = Mock()
        mock_response = {
            "projects": [
                {"project_id": "proj1", "name": "Project 1"},
                {"project_id": "proj2", "name": "Project 2"},
            ]
        }
        mock_manage.get_projects.return_value = mock_response
        mock_instance.manage.v.return_value = mock_manage
        mock_dg_client.return_value = mock_instance

        # Get projects
        result = client.get_projects()

        assert result == mock_response
        mock_manage.get_projects.assert_called_once()

    @patch("deepctl_core.client.DGClient")
    def test_get_project(self, mock_dg_client, client):
        """Test getting a specific project."""
        # Setup mock
        mock_instance = Mock()
        mock_manage = Mock()
        mock_response = {"project_id": "test-project", "name": "Test Project"}
        mock_manage.get_project.return_value = mock_response
        mock_instance.manage.v.return_value = mock_manage
        mock_dg_client.return_value = mock_instance

        # Get project
        result = client.get_project("test-project")

        assert result == mock_response
        mock_manage.get_project.assert_called_once_with("test-project")

    @patch("deepctl_core.client.DGClient")
    def test_get_usage(self, mock_dg_client, client):
        """Test getting usage statistics."""
        # Setup mock
        mock_instance = Mock()
        mock_manage = Mock()
        mock_response = {"minutes": 1000, "cost": 25.00}
        mock_manage.get_usage_summary.return_value = mock_response
        mock_instance.manage.v.return_value = mock_manage
        mock_dg_client.return_value = mock_instance

        # Get usage with individual date parameters
        result = client.get_usage(
            "test-project", start_date="2024-01-01", end_date="2024-01-31"
        )

        assert result == {
            "minutes": 1000,
            "cost": 25.00,
            "project_id": "test-project",
        }
        mock_manage.get_usage_summary.assert_called_once()

        # Check call arguments
        call_args = mock_manage.get_usage_summary.call_args[0]
        assert call_args[0] == "test-project"
        assert call_args[1] == {"start": "2024-01-01", "end": "2024-01-31"}

    @patch("deepctl_core.client.DGClient")
    def test_create_project(self, mock_dg_client, client):
        """Test creating a project."""
        # Setup mock
        mock_instance = Mock()
        mock_manage = Mock()
        mock_response = {"project_id": "new-proj", "name": "New Project"}
        mock_manage.create_project.return_value = mock_response
        mock_instance.manage.v.return_value = mock_manage
        mock_dg_client.return_value = mock_instance

        # Create project
        result = client.create_project("New Project")

        assert result == mock_response
        mock_manage.create_project.assert_called_once()
