"""Tests for the authentication module."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, Mock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import keyring.errors
import pytest
from deepctl_core.auth import AuthenticationError, AuthManager
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
    with patch("deepctl_core.auth.httpx.Client") as mock_client_class:
        # Create a mock client instance
        mock_client = Mock()
        mock_client_class.return_value = mock_client

        # Patch keyring to return None by default
        with patch("deepctl_core.auth.keyring") as mock_keyring:
            mock_keyring.get_password.return_value = None

            # Create the auth manager
            auth_manager = AuthManager(mock_config)
            auth_manager.client = mock_client
            return auth_manager


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
            api_key="sk-test-key", project_id="test-project"
        )

        assert success is True
        assert message == "Credentials verified successfully"
        assert error_type is None

        # Verify API call
        auth_manager.client.get.assert_called_once_with(
            "https://api.deepgram.com/v1/projects/test-project",
            headers={
                "Authorization": "Token sk-test-key",
                "Content-Type": "application/json",
            },
        )

    def test_verify_credentials_invalid_api_key(self, auth_manager):
        """Test verification with invalid API key."""
        # Mock 401 response
        mock_response = Mock()
        mock_response.status_code = 401

        auth_manager.client.get.return_value = mock_response

        success, message, error_type = auth_manager.verify_credentials(
            api_key="sk-invalid-key", project_id="test-project"
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
            api_key="sk-test-key", project_id="test-project"
        )

        assert success is False
        assert (
            message == "API key is valid but lacks permission for this project"
        )
        assert error_type == "auth"

    def test_verify_credentials_project_not_found(self, auth_manager):
        """Test verification with non-existent project."""
        # Mock 404 response
        mock_response = Mock()
        mock_response.status_code = 404

        auth_manager.client.get.return_value = mock_response

        success, message, error_type = auth_manager.verify_credentials(
            api_key="sk-test-key", project_id="non-existent-project"
        )

        assert success is False
        assert message == "Project ID 'non-existent-project' not found"
        assert error_type == "project"

    @patch.dict("os.environ", {}, clear=True)  # Clear environment variables
    def test_verify_credentials_no_api_key(self, auth_manager):
        """Test verification without API key."""
        # Mock get_api_key to return None
        with patch.object(auth_manager, "get_api_key", return_value=None):
            success, message, error_type = auth_manager.verify_credentials(
                api_key=None, project_id="test-project"
            )

            assert success is False
            assert message == "No API key provided or stored"
            assert error_type == "auth"

    @patch.dict("os.environ", {}, clear=True)  # Clear environment variables
    def test_verify_credentials_no_project_id(self, auth_manager):
        """Test verification without project ID falls back to key-only check."""
        # Mock successful API response for /v1/projects
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"projects": []}
        auth_manager.client.get.return_value = mock_response

        with patch.object(auth_manager, "get_project_id", return_value=None):
            success, message, error_type = auth_manager.verify_credentials(
                api_key="sk-test-key", project_id=None
            )

            assert success is True
            assert message == "API key verified successfully"
            assert error_type is None

            # Should have called /v1/projects (key-only verification)
            auth_manager.client.get.assert_called_once_with(
                "https://api.deepgram.com/v1/projects",
                headers={
                    "Authorization": "Token sk-test-key",
                    "Content-Type": "application/json",
                },
            )

    def test_verify_credentials_network_error(self, auth_manager):
        """Test verification with network error."""
        # Mock network error
        auth_manager.client.get.side_effect = httpx.RequestError(
            "Connection failed"
        )

        success, message, error_type = auth_manager.verify_credentials(
            api_key="sk-test-key", project_id="test-project"
        )

        assert success is False
        assert "Network error during verification" in message
        assert error_type == "network"

    def test_verify_credentials_uses_stored_credentials(
        self, auth_manager, mock_config
    ):
        """Test verification uses stored credentials when not provided."""
        # Mock the get_api_key and get_project_id methods to return stored values
        with patch.object(
            auth_manager, "get_api_key", return_value="sk-stored-key"
        ), patch.object(
            auth_manager, "get_project_id", return_value="stored-project"
        ):
            # Mock successful response
            mock_response = Mock()
            mock_response.status_code = 200
            auth_manager.client.get.return_value = mock_response

            success, message, error_type = (
                auth_manager.verify_credentials()
            )

            assert success is True

            # Verify it used stored credentials
            auth_manager.client.get.assert_called_once_with(
                "https://api.deepgram.com/v1/projects/stored-project",
                headers={
                    "Authorization": "Token sk-stored-key",
                    "Content-Type": "application/json",
                },
            )

    @patch.dict(
        "os.environ",
        {
            "DEEPGRAM_API_KEY": "sk-env-key",
            "DEEPGRAM_PROJECT_ID": "env-project",
        },
    )
    def test_verify_credentials_uses_env_vars(self, auth_manager):
        """Test verification uses environment variables."""
        # Mock successful response
        mock_response = Mock()
        mock_response.status_code = 200
        auth_manager.client.get.return_value = mock_response

        # Keyring must return None during execution so get_api_key() falls
        # through to the env var.  The fixture only patches keyring during
        # AuthManager construction; we need it mocked here too.
        with patch("deepctl_core.auth.keyring") as mock_keyring:
            mock_keyring.get_password.return_value = None
            success, message, error_type = auth_manager.verify_credentials()

        assert success is True

        # Verify it used environment credentials
        auth_manager.client.get.assert_called_once_with(
            "https://api.deepgram.com/v1/projects/env-project",
            headers={
                "Authorization": "Token sk-env-key",
                "Content-Type": "application/json",
            },
        )

    def test_guard_with_valid_credentials(self, auth_manager):
        """Test guard method with valid credentials."""
        # Set up API key
        with patch.object(
            auth_manager, "get_api_key", return_value="sk-test-key"
        ):
            # Mock successful key verification
            with patch.object(
                auth_manager,
                "verify_api_key",
                return_value=(True, "Success", None),
            ):
                # Should not raise
                auth_manager.guard()

    def test_guard_with_no_api_key(self, auth_manager):
        """Test guard method with no API key."""
        # No API key
        with patch.object(auth_manager, "get_api_key", return_value=None):
            with pytest.raises(
                AuthenticationError, match="DEEPGRAM_API_KEY is not set"
            ):
                auth_manager.guard()

    def test_guard_with_invalid_credentials(self, auth_manager):
        """Test guard method with invalid credentials."""
        # Set up API key
        with patch.object(
            auth_manager, "get_api_key", return_value="sk-test-key"
        ):
            # Mock failed key verification
            with patch.object(
                auth_manager,
                "verify_api_key",
                return_value=(False, "Invalid API key", "auth"),
            ):
                with pytest.raises(
                    AuthenticationError, match="Invalid API key"
                ):
                    auth_manager.guard()

    def test_guard_does_not_require_project_id(self, auth_manager):
        """Test that guard() only checks API key, not project_id."""
        with patch.object(
            auth_manager, "get_api_key", return_value="sk-test-key"
        ), patch.object(
            auth_manager,
            "verify_api_key",
            return_value=(True, "API key verified", None),
        ), patch.object(
            auth_manager, "get_project_id", return_value=None
        ):
            # Should NOT raise even without project_id
            auth_manager.guard()

    def test_login_with_api_key_success(self, auth_manager, mock_config):
        """Test successful login with API key."""
        # Mock successful verification
        with patch.object(
            auth_manager,
            "verify_credentials",
            return_value=(True, "Success", None),
        ):
            # Mock keyring
            with patch("deepctl_core.auth.keyring") as mock_keyring:
                mock_keyring.get_password.return_value = None

                # Test login with both api_key and project_id
                auth_manager.login_with_api_key(
                    "test_api_key", "test_project_id"
                )

                # Verify keyring was called to store both API key and project ID
                assert mock_keyring.set_password.call_count >= 1

    def test_login_with_api_key_verification_fails(self, auth_manager):
        """Test login fails when verification fails."""
        # Mock failed verification
        with patch.object(
            auth_manager,
            "verify_credentials",
            return_value=(False, "Invalid API key", "auth"),
        ), pytest.raises(
            AuthenticationError, match="Invalid API key"
        ):
            auth_manager.login_with_api_key(
                "sk-invalid-key", "test-project"
            )

    @patch.dict(
        "os.environ",
        {
            "DEEPGRAM_API_KEY": "sk-env-key",
            "DEEPGRAM_PROJECT_ID": "env-project",
        },
    )
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


class TestAuthPrecedence:
    """Test authentication precedence rules."""

    def test_explicit_credentials_highest_priority(self, mock_config):
        """Test that explicit credentials have highest priority."""
        # Set up environment variables
        with patch.dict(
            "os.environ",
            {
                "DEEPGRAM_API_KEY": "sk-env-key",
                "DEEPGRAM_PROJECT_ID": "env-project",
            },
        ):
            # Set up profile credentials
            mock_profile = Mock()
            mock_profile.api_key = "sk-profile-key"
            mock_profile.project_id = "profile-project"
            mock_config.get_profile.return_value = mock_profile

            # Create auth manager with explicit credentials
            auth_manager = AuthManager(
                mock_config,
                explicit_api_key="sk-explicit-key",
                explicit_project_id="explicit-project",
            )

            # Explicit credentials should take precedence
            assert auth_manager.get_api_key() == "sk-explicit-key"
            assert auth_manager.get_project_id() == "explicit-project"
            assert auth_manager.get_credential_source() == "explicit flags"

    def test_profile_credentials_over_env(self, mock_config):
        """Test that profile credentials take precedence over environment."""
        # Set up environment variables
        with patch.dict(
            "os.environ",
            {
                "DEEPGRAM_API_KEY": "sk-env-key",
                "DEEPGRAM_PROJECT_ID": "env-project",
            },
        ):
            # Set up profile credentials in keyring
            with patch("deepctl_core.auth.keyring") as mock_keyring:

                def get_password_side_effect(service, key):
                    if key == "api-key.default":
                        return "sk-profile-key"
                    return None

                mock_keyring.get_password.side_effect = (
                    get_password_side_effect
                )

                mock_profile = Mock()
                mock_profile.api_key = None  # Not in config
                mock_profile.project_id = "profile-project"
                mock_config.get_profile.return_value = mock_profile

                auth_manager = AuthManager(mock_config)

                # Profile credentials should take precedence
                assert auth_manager.get_api_key() == "sk-profile-key"
                assert auth_manager.get_project_id() == "profile-project"
                assert (
                    auth_manager.get_credential_source() == "profile 'default'"
                )

    @patch.dict(
        "os.environ",
        {
            "DEEPGRAM_API_KEY": "sk-env-key",
            "DEEPGRAM_PROJECT_ID": "env-project",
        },
    )
    def test_env_credentials_fallback(self, mock_config):
        """Test that environment variables are used as fallback."""
        # No profile credentials
        mock_profile = Mock()
        mock_profile.api_key = None
        mock_profile.project_id = None
        mock_config.get_profile.return_value = mock_profile

        with patch("deepctl_core.auth.keyring") as mock_keyring:
            mock_keyring.get_password.return_value = None

            auth_manager = AuthManager(mock_config)

            # Should fall back to environment variables
            assert auth_manager.get_api_key() == "sk-env-key"
            assert auth_manager.get_project_id() == "env-project"
            assert (
                auth_manager.get_credential_source() == "environment variables"
            )

    def test_ignore_env_parameter(self, mock_config):
        """Test that ignore_env parameter works correctly."""
        with patch.dict(
            "os.environ",
            {
                "DEEPGRAM_API_KEY": "sk-env-key",
                "DEEPGRAM_PROJECT_ID": "env-project",
            },
        ):
            # No profile credentials
            mock_profile = Mock()
            mock_profile.api_key = None
            mock_profile.project_id = None
            mock_config.get_profile.return_value = mock_profile

            with patch("deepctl_core.auth.keyring") as mock_keyring:
                mock_keyring.get_password.return_value = None

                auth_manager = AuthManager(mock_config)

                # With ignore_env=True, should return None
                assert auth_manager.get_api_key(ignore_env=True) is None
                assert auth_manager.get_project_id(ignore_env=True) is None

                # Without ignore_env, should return env values
                assert (
                    auth_manager.get_api_key(ignore_env=False) == "sk-env-key"
                )
                assert (
                    auth_manager.get_project_id(ignore_env=False)
                    == "env-project"
                )


class TestCredentialDetection:
    """Test credential detection methods."""

    @patch.dict(
        "os.environ",
        {
            "DEEPGRAM_API_KEY": "sk-env-key",
            "DEEPGRAM_PROJECT_ID": "env-project",
        },
    )
    def test_has_env_credentials_both(self, auth_manager):
        """Test detection when both env vars are set."""
        has_key, has_project = auth_manager.has_env_credentials()
        assert has_key is True
        assert has_project is True

    @patch.dict("os.environ", {"DEEPGRAM_API_KEY": "sk-env-key"})
    def test_has_env_credentials_key_only(self, auth_manager):
        """Test detection when only API key is set."""
        has_key, has_project = auth_manager.has_env_credentials()
        assert has_key is True
        assert has_project is False

    @patch.dict("os.environ", {})
    def test_has_env_credentials_none(self, auth_manager):
        """Test detection when no env vars are set."""
        has_key, has_project = auth_manager.has_env_credentials()
        assert has_key is False
        assert has_project is False

    def test_has_profile_credentials_in_keyring(self, mock_config):
        """Test profile credential detection from keyring."""
        with patch("deepctl_core.auth.keyring") as mock_keyring:
            # Set up keyring to return API key
            mock_keyring.get_password.side_effect = lambda service, key: {
                "api-key.test-profile": "sk-profile-key"
            }.get(key)

            mock_profile = Mock()
            mock_profile.api_key = None
            mock_profile.project_id = "profile-project"
            mock_config.get_profile.return_value = mock_profile

            auth_manager = AuthManager(mock_config)
            has_key, has_project = auth_manager.has_profile_credentials(
                "test-profile"
            )

            assert has_key is True
            assert has_project is True

    def test_has_profile_credentials_in_config(self, mock_config):
        """Test profile credential detection from config."""
        with patch("deepctl_core.auth.keyring") as mock_keyring:
            mock_keyring.get_password.return_value = None

            mock_profile = Mock()
            mock_profile.api_key = "sk-config-key"
            mock_profile.project_id = "config-project"
            mock_config.get_profile.return_value = mock_profile

            auth_manager = AuthManager(mock_config)
            has_key, has_project = auth_manager.has_profile_credentials(
                "test-profile"
            )

            assert has_key is True
            assert has_project is True

    def test_has_profile_credentials_none(self, mock_config):
        """Test profile credential detection when none exist."""
        with patch("deepctl_core.auth.keyring") as mock_keyring:
            mock_keyring.get_password.return_value = None

            mock_profile = Mock()
            mock_profile.api_key = None
            mock_profile.project_id = None
            mock_config.get_profile.return_value = mock_profile

            auth_manager = AuthManager(mock_config)
            has_key, has_project = auth_manager.has_profile_credentials(
                "test-profile"
            )

            assert has_key is False
            assert has_project is False

    def test_is_authenticated_with_explicit_key(self, mock_config):
        """Test is_authenticated with explicit API key."""
        auth_manager = AuthManager(mock_config, explicit_api_key="sk-explicit")
        assert auth_manager.is_authenticated() is True

    def test_is_authenticated_check_profile_only(self, mock_config):
        """Test is_authenticated with check_profile_only flag."""
        with patch.dict("os.environ", {"DEEPGRAM_API_KEY": "sk-env-key"}):
            # No profile credentials
            mock_profile = Mock()
            mock_profile.api_key = None
            mock_config.get_profile.return_value = mock_profile

            with patch("deepctl_core.auth.keyring") as mock_keyring:
                mock_keyring.get_password.return_value = None

                auth_manager = AuthManager(mock_config)

                # Should be False when checking profile only
                assert (
                    auth_manager.is_authenticated(check_profile_only=True)
                    is False
                )

                # Should be True when checking all sources
                assert (
                    auth_manager.is_authenticated(check_profile_only=False)
                    is True
                )


class TestFiniteCredentialFlow:
    """Tests for finite direct credentials returned by dx-id."""

    def _make_auth(self, mock_config):
        with patch("deepctl_core.auth.httpx.Client"):
            with patch("deepctl_core.auth.keyring"):
                return AuthManager(mock_config)

    # ------------------------------------------------------------------
    # _store_token — finite direct credential path
    # ------------------------------------------------------------------

    def test_store_token_direct_credential_saves_to_keyring(self, mock_config):
        from deepctl_core.auth import TokenResponse

        token = TokenResponse(
            access_token="jwt-abc",
            dg_token="dg-finite-key",
            dg_expires_in=2592000,
            project_id="proj-1",
        )
        auth = self._make_auth(mock_config)

        with patch("deepctl_core.auth.keyring") as mock_kr:
            auth._store_token(token)

        calls = {call[0][1]: call[0][2] for call in mock_kr.set_password.call_args_list}
        assert calls["api-key.default"] == "dg-finite-key"
        assert "jwt.default" not in calls
        assert "refresh-token.default" not in calls

    def test_store_token_direct_credential_clears_legacy_session(self, mock_config):
        from deepctl_core.auth import TokenResponse

        token = TokenResponse(
            access_token="jwt-abc",
            dg_token="dg-finite-key",
            dg_expires_in=2592000,
            project_id="proj-1",
        )
        auth = self._make_auth(mock_config)

        with patch("deepctl_core.auth.keyring") as mock_kr:
            auth._store_token(token)

        deleted = [c[0][1] for c in mock_kr.delete_password.call_args_list]
        assert "jwt.default" in deleted
        assert "refresh-token.default" in deleted

    def test_store_token_ignores_missing_legacy_keyring_entries(self, mock_config):
        from deepctl_core.auth import TokenResponse

        token = TokenResponse(
            access_token="jwt-abc",
            dg_token="dg-finite-key",
            dg_expires_in=3600,
            project_id="proj-1",
        )
        auth = self._make_auth(mock_config)

        with patch("deepctl_core.auth.keyring") as mock_kr:
            mock_kr.delete_password.side_effect = keyring.errors.PasswordDeleteError(
                "not found"
            )
            auth._store_token(token)

        assert mock_config.create_profile.call_args.kwargs["api_key"] is None

    def test_store_token_preserves_profile_base_url(self, mock_config):
        from deepctl_core.auth import TokenResponse

        mock_config.get_profile.return_value.base_url = "https://staging.example.com"
        token = TokenResponse(
            access_token="jwt-abc",
            dg_token="dg-finite-key",
            dg_expires_in=3600,
            project_id="proj-1",
        )
        auth = self._make_auth(mock_config)

        with patch("deepctl_core.auth.keyring"):
            auth._store_token(token)

        assert (
            mock_config.create_profile.call_args.kwargs["base_url"]
            == "https://staging.example.com"
        )

    def test_store_token_legacy_response_requires_reauthentication(self, mock_config):
        from deepctl_core.auth import TokenResponse

        token = TokenResponse(
            access_token="sk-legacy-key",
            project_id="proj-1",
        )
        auth = self._make_auth(mock_config)

        with pytest.raises(AuthenticationError, match="Run `dg update` and `dg login` again"):
            auth._store_token(token)

    def test_get_api_key_rejects_expired_finite_credential(self, mock_config):
        mock_profile = Mock()
        mock_profile.dg_token_expires_at = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        mock_config.get_profile.return_value = mock_profile
        auth = self._make_auth(mock_config)

        with pytest.raises(AuthenticationError, match="Run `dg login` again"):
            auth.get_api_key()

    def test_get_api_key_uses_environment_key_when_profile_credential_expired(
        self, mock_config
    ):
        mock_profile = Mock()
        mock_profile.api_key = None
        mock_profile.dg_token_expires_at = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        mock_config.get_profile.return_value = mock_profile
        auth = self._make_auth(mock_config)

        with patch("deepctl_core.auth.keyring") as mock_kr, patch.dict(
            "os.environ", {"DEEPGRAM_API_KEY": "sk-env"}, clear=True
        ):
            assert auth.get_api_key() == "sk-env"

        mock_kr.delete_password.assert_called_once_with(
            "com.deepgram.dx.deepctl", "api-key.default"
        )

    def test_get_api_key_rejects_timezone_naive_expiry(self, mock_config):
        mock_profile = Mock()
        mock_profile.api_key = None
        mock_profile.dg_token_expires_at = "2026-10-13T12:00:00"
        mock_config.get_profile.return_value = mock_profile
        auth = self._make_auth(mock_config)

        with pytest.raises(AuthenticationError, match="expiry is invalid"):
            auth.get_api_key()

    def test_device_login_prints_code_and_opens_plain_verification_uri(
        self, mock_config
    ):
        from deepctl_core.auth import DeviceCodeResponse, TokenResponse

        auth = self._make_auth(mock_config)
        device = DeviceCodeResponse(
            device_code="device-code",
            user_code="ABCD-EFGH",
            verification_uri="https://id.dx.deepgram.com/auth/cli",
            expires_in=600,
            interval=1,
        )
        token = TokenResponse(
            access_token="jwt",
            dg_token="dg-finite-key",
            dg_expires_in=3600,
            project_id="project",
        )

        with patch.object(auth, "_request_device_code", return_value=device), patch.object(
            auth, "_poll_for_token", return_value=token
        ), patch.object(auth, "_store_token"), patch("deepctl_core.auth.webbrowser.open") as open_browser, patch(
            "deepctl_core.auth.console.print"
        ) as print_console:
            auth.login_with_device_flow()

        open_browser.assert_called_once_with(device.verification_uri)
        assert any("ABCD-EFGH" in str(call) for call in print_console.call_args_list)

    def test_device_login_uses_complete_verification_uri_without_user_code(
        self, mock_config
    ):
        from deepctl_core.auth import DeviceCodeResponse, TokenResponse

        auth = self._make_auth(mock_config)
        device = DeviceCodeResponse(
            device_code="device-code",
            verification_uri="https://id.dx.deepgram.com/auth/cli",
            verification_uri_complete="https://id.dx.deepgram.com/auth/cli?code=complete",
            expires_in=600,
            interval=1,
        )
        token = TokenResponse(
            access_token="jwt",
            dg_token="dg-finite-key",
            dg_expires_in=3600,
            project_id="project",
        )

        with patch.object(auth, "_request_device_code", return_value=device), patch.object(
            auth, "_poll_for_token", return_value=token
        ), patch.object(auth, "_store_token"), patch(
            "deepctl_core.auth.webbrowser.open"
        ) as open_browser, patch("deepctl_core.auth.console.print") as print_console:
            auth.login_with_device_flow()

        open_browser.assert_called_once_with(device.verification_uri_complete)
        assert not any("Enter code:" in str(call) for call in print_console.call_args_list)

    def test_get_api_key_uses_environment_key_without_profile_credential(
        self, mock_config
    ):
        mock_profile = Mock()
        mock_profile.api_key = None
        mock_config.get_profile.return_value = mock_profile

        auth = self._make_auth(mock_config)

        with patch("deepctl_core.auth.keyring") as mock_kr:
            mock_kr.get_password.return_value = None
            with patch.dict("os.environ", {"DEEPGRAM_API_KEY": "sk-env"}):
                result = auth.get_api_key()

        assert result == "sk-env"

    def test_get_api_key_uses_explicit_key(self, mock_config):
        auth = AuthManager(mock_config, explicit_api_key="sk-explicit")

        assert auth.get_api_key() == "sk-explicit"

    def test_poll_for_token_encodes_device_flow_parameters(self, mock_config):
        from deepctl_core.auth import DeviceCodeResponse

        auth = self._make_auth(mock_config)
        response = Mock()
        response.status_code = 201
        response.json.return_value = {
            "access_token": "jwt",
            "dg_token": "dg-finite-key",
            "dg_expires_in": 3600,
            "project_id": "project",
        }
        auth.client.get.return_value = response

        token = auth._poll_for_token(
            DeviceCodeResponse(
                device_code="device-code",
                verification_uri="https://id.dx.deepgram.com/auth/cli",
                expires_in=600,
                interval=1,
            ),
            "test-host",
        )

        poll_url = auth.client.get.call_args.args[0]
        assert parse_qs(urlparse(poll_url).query) == {
            "device_code": ["device-code"],
            "client_id": ["deepgram-cli"],
            "hostname": ["test-host"],
        }
        assert token.dg_token == "dg-finite-key"

    # ------------------------------------------------------------------
    # logout — clears JWT entries
    # ------------------------------------------------------------------

    def test_logout_clears_jwt_and_refresh_token(self, mock_config):
        mock_config.list_profiles.return_value = []
        auth = self._make_auth(mock_config)

        with patch("deepctl_core.auth.keyring") as mock_kr:
            auth.logout(keep_config=False)

        deleted = {c[0][1] for c in mock_kr.delete_password.call_args_list}
        assert "jwt.default" in deleted
        assert "refresh-token.default" in deleted
        assert "api-key.default" in deleted

    # ------------------------------------------------------------------
    # has_profile_credentials — expired finite credentials do not count
    # ------------------------------------------------------------------

    def test_has_profile_credentials_ignores_expired_finite_credential(
        self, mock_config
    ):
        mock_profile = Mock()
        mock_profile.api_key = None
        mock_profile.project_id = "proj-1"
        mock_profile.dg_token_expires_at = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        mock_config.get_profile.return_value = mock_profile

        auth = self._make_auth(mock_config)

        with patch("deepctl_core.auth.keyring") as mock_kr:
            mock_kr.get_password.return_value = "some-key"
            has_key, has_project = auth.has_profile_credentials("test-profile")

        assert has_key is False
        assert has_project is True
