"""Tests for the authentication module."""

import pytest
from unittest.mock import Mock, patch, MagicMock
import httpx

from deepctl_core.auth import AuthManager, AuthenticationError
from deepctl_core.config import Config


@pytest.fixture
def mock_config():
    """Create a mock config instance."""
    config = Mock(spec=Config)
    config.config_path = "/mock/path/config.yaml"
    config.profile = "default"

    # Mock profile configuration
    mock_profile = Mock()
    mock_profile.api_key = None
    mock_profile.project_id = None
    mock_profile.base_url = "https://api.deepgram.com"

    config.get_profile.return_value = mock_profile
    return config


@pytest.fixture
def auth_manager(mock_config):
    """Create an AuthManager instance with mocked dependencies."""
    with patch("deepctl_core.auth.httpx.Client"):
        return AuthManager(mock_config)


class TestAuthManager:
    """Test AuthManager class."""

    def test_verify_credentials_success(self, auth_manager):
        """Test successful credential verification."""
        # Mock successful API response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"project_id": "test-project"}

        auth_manager.client.get.return_value = mock_response

        success, message, error_type = auth_manager.verify_credentials(
            api_key="sk-test-key",
            project_id="test-project"
        )

        assert success is True
        assert message == "Credentials verified successfully"
        assert error_type is None

        # Verify API call
        auth_manager.client.get.assert_called_once_with(
            "https://api.deepgram.com/v1/projects/test-project",
            headers={
                "Authorization": "Token sk-test-key",
                "Content-Type": "application/json"
            }
        )

    def test_verify_credentials_invalid_api_key(self, auth_manager):
        """Test verification with invalid API key."""
        # Mock 401 response
        mock_response = Mock()
        mock_response.status_code = 401

        auth_manager.client.get.return_value = mock_response

        success, message, error_type = auth_manager.verify_credentials(
            api_key="sk-invalid-key",
            project_id="test-project"
        )

        assert success is False
        assert message == "Invalid API key - authentication failed"
        assert error_type == "auth"

    def test_verify_credentials_no_permission(self, auth_manager):
        """Test verification with valid key but no permission."""
        # Mock 403 response
        mock_response = Mock()
        mock_response.status_code = 403

        auth_manager.client.get.return_value = mock_response

        success, message, error_type = auth_manager.verify_credentials(
            api_key="sk-test-key",
            project_id="test-project"
        )

        assert success is False
        assert message == "API key is valid but lacks permission for this project"
        assert error_type == "auth"

    def test_verify_credentials_project_not_found(self, auth_manager):
        """Test verification with non-existent project."""
        # Mock 404 response
        mock_response = Mock()
        mock_response.status_code = 404

        auth_manager.client.get.return_value = mock_response

        success, message, error_type = auth_manager.verify_credentials(
            api_key="sk-test-key",
            project_id="non-existent-project"
        )

        assert success is False
        assert message == "Project ID 'non-existent-project' not found"
        assert error_type == "project"

    def test_verify_credentials_no_api_key(self, auth_manager):
        """Test verification without API key."""
        success, message, error_type = auth_manager.verify_credentials(
            api_key=None,
            project_id="test-project"
        )

        assert success is False
        assert message == "No API key provided or stored"
        assert error_type == "auth"

    def test_verify_credentials_no_project_id(self, auth_manager):
        """Test verification without project ID."""
        success, message, error_type = auth_manager.verify_credentials(
            api_key="sk-test-key",
            project_id=None
        )

        assert success is False
        assert message == "No project ID provided or stored"
        assert error_type == "project"

    def test_verify_credentials_network_error(self, auth_manager):
        """Test verification with network error."""
        # Mock network error
        auth_manager.client.get.side_effect = httpx.RequestError(
            "Connection failed")

        success, message, error_type = auth_manager.verify_credentials(
            api_key="sk-test-key",
            project_id="test-project"
        )

        assert success is False
        assert "Network error during verification" in message
        assert error_type == "network"

    def test_verify_credentials_uses_stored_credentials(self, auth_manager, mock_config):
        """Test verification uses stored credentials when not provided."""
        # Set up stored credentials
        mock_profile = mock_config.get_profile.return_value
        mock_profile.api_key = "sk-stored-key"
        mock_profile.project_id = "stored-project"

        # Mock successful response
        mock_response = Mock()
        mock_response.status_code = 200
        auth_manager.client.get.return_value = mock_response

        success, message, error_type = auth_manager.verify_credentials()

        assert success is True

        # Verify it used stored credentials
        auth_manager.client.get.assert_called_once_with(
            "https://api.deepgram.com/v1/projects/stored-project",
            headers={
                "Authorization": "Token sk-stored-key",
                "Content-Type": "application/json"
            }
        )

    @patch.dict("os.environ", {"DEEPGRAM_API_KEY": "sk-env-key", "DEEPGRAM_PROJECT_ID": "env-project"})
    def test_verify_credentials_uses_env_vars(self, auth_manager):
        """Test verification uses environment variables."""
        # Mock successful response
        mock_response = Mock()
        mock_response.status_code = 200
        auth_manager.client.get.return_value = mock_response

        success, message, error_type = auth_manager.verify_credentials()

        assert success is True

        # Verify it used environment credentials
        auth_manager.client.get.assert_called_once_with(
            "https://api.deepgram.com/v1/projects/env-project",
            headers={
                "Authorization": "Token sk-env-key",
                "Content-Type": "application/json"
            }
        )

    def test_guard_with_valid_credentials(self, auth_manager):
        """Test guard method with valid credentials."""
        # Set up API key
        with patch.object(auth_manager, 'get_api_key', return_value='sk-test-key'):
            # Mock successful verification
            with patch.object(auth_manager, 'verify_credentials', return_value=(True, "Success", None)):
                # Should not raise
                auth_manager.guard()

    def test_guard_with_no_api_key(self, auth_manager):
        """Test guard method with no API key."""
        # No API key
        with patch.object(auth_manager, 'get_api_key', return_value=None):
            with pytest.raises(AuthenticationError, match="DEEPGRAM_API_KEY is not set"):
                auth_manager.guard()

    def test_guard_with_invalid_credentials(self, auth_manager):
        """Test guard method with invalid credentials."""
        # Set up API key
        with patch.object(auth_manager, 'get_api_key', return_value='sk-test-key'):
            # Mock failed verification
            with patch.object(auth_manager, 'verify_credentials',
                              return_value=(False, "Invalid API key", "auth")):
                with pytest.raises(AuthenticationError, match="Invalid API key"):
                    auth_manager.guard()

    def test_login_with_api_key_success(self, auth_manager, mock_config):
        """Test successful login with API key."""
        # Mock successful verification
        with patch.object(auth_manager, 'verify_credentials', return_value=(True, "Success", None)):
            # Mock keyring
            with patch('deepctl_core.auth.keyring') as mock_keyring:
                mock_keyring.get_password.return_value = None

                # Test login
                result = auth_manager.login_with_api_key("test_api_key")

                # Verify keyring was called
                mock_keyring.set_password.assert_any_call(
                    "deepgram", "api_key", "sk-test-key")
                mock_keyring.set_password.assert_any_call(
                    "deepgram", "project_id", "test-project")

                # Verify profile was created
                mock_config.create_profile.assert_called_once_with(
                    "default",
                    api_key="sk-test-key",
                    project_id="test-project"
                )

    def test_login_with_api_key_verification_fails(self, auth_manager):
        """Test login fails when verification fails."""
        # Mock failed verification
        with patch.object(auth_manager, 'verify_credentials',
                          return_value=(False, "Invalid API key", "auth")):
            with pytest.raises(AuthenticationError, match="API key verification failed"):
                auth_manager.login_with_api_key(
                    "sk-invalid-key", "test-project")

    @patch.dict("os.environ", {"DEEPGRAM_API_KEY": "sk-env-key", "DEEPGRAM_PROJECT_ID": "env-project"})
    def test_is_ci_mode_true(self, auth_manager):
        """Test CI mode detection when both env vars are set."""
        assert auth_manager.is_ci_mode() is True

    @patch.dict("os.environ", {"DEEPGRAM_API_KEY": "sk-env-key"})
    def test_is_ci_mode_false_missing_project(self, auth_manager):
        """Test CI mode detection when project ID is missing."""
        assert auth_manager.is_ci_mode() is False

    @patch.dict("os.environ", {})
    def test_is_ci_mode_false_no_env_vars(self, auth_manager):
        """Test CI mode detection when no env vars are set."""
        assert auth_manager.is_ci_mode() is False
