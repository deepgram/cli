"""Tests for the authentication module."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import httpx
import pytest

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
        ):
            with patch.object(
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
        ):
            with patch.object(
                auth_manager,
                "verify_api_key",
                return_value=(True, "API key verified", None),
            ):
                with patch.object(
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
        ):
            with pytest.raises(
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


class TestJWTFlow:
    """Tests for the new JWT-based device flow."""

    def _make_auth(self, mock_config):
        with patch("deepctl_core.auth.httpx.Client"):
            with patch("deepctl_core.auth.keyring"):
                return AuthManager(mock_config)

    # ------------------------------------------------------------------
    # _store_token — JWT path
    # ------------------------------------------------------------------

    def test_store_token_jwt_path_saves_to_keyring(self, mock_config):
        from deepctl_core.auth import TokenResponse

        token = TokenResponse(
            access_token="jwt-abc",
            refresh_token="rt-xyz",
            project_id="proj-1",
            expires_in=900,
        )
        auth = self._make_auth(mock_config)

        with patch("deepctl_core.auth.keyring") as mock_kr:
            auth._store_token(token)

        calls = {call[0][1]: call[0][2] for call in mock_kr.set_password.call_args_list}
        assert calls["jwt.default"] == "jwt-abc"
        assert calls["refresh-token.default"] == "rt-xyz"

    def test_store_token_jwt_path_clears_stale_dg_token(self, mock_config):
        from deepctl_core.auth import TokenResponse

        token = TokenResponse(
            access_token="jwt-abc",
            refresh_token="rt-xyz",
            project_id="proj-1",
            expires_in=900,
        )
        auth = self._make_auth(mock_config)

        with patch("deepctl_core.auth.keyring") as mock_kr:
            auth._store_token(token)

        deleted = [c[0][1] for c in mock_kr.delete_password.call_args_list]
        assert "api-key.default" in deleted

    def test_store_token_legacy_path_saves_api_key(self, mock_config):
        from deepctl_core.auth import TokenResponse

        token = TokenResponse(
            access_token="sk-legacy-key",
            project_id="proj-1",
        )
        auth = self._make_auth(mock_config)

        with patch("deepctl_core.auth.keyring") as mock_kr:
            auth._store_token(token)

        calls = {call[0][1]: call[0][2] for call in mock_kr.set_password.call_args_list}
        assert calls.get("api-key.default") == "sk-legacy-key"
        assert "jwt.default" not in calls

    # ------------------------------------------------------------------
    # get_api_key — JWT fast-path
    # ------------------------------------------------------------------

    def test_get_api_key_uses_jwt_path_when_jwt_present(self, mock_config):
        mock_profile = Mock()
        mock_profile.api_key = None
        mock_profile.dg_token_expires_at = None
        mock_profile.jwt_expires_at = None
        mock_config.get_profile.return_value = mock_profile

        auth = self._make_auth(mock_config)

        def keyring_side_effect(service, key):
            if key == "jwt.default":
                return "jwt-token"
            return None

        with patch("deepctl_core.auth.keyring") as mock_kr:
            mock_kr.get_password.side_effect = keyring_side_effect
            with patch.object(auth, "_get_dg_token_via_jwt", return_value="dg-tok") as mock_exchange:
                result = auth.get_api_key()

        assert result == "dg-tok"
        mock_exchange.assert_called_once_with("jwt-token", "default")

    def test_get_api_key_skips_jwt_path_when_no_jwt(self, mock_config):
        mock_profile = Mock()
        mock_profile.api_key = None
        mock_config.get_profile.return_value = mock_profile

        auth = self._make_auth(mock_config)

        with patch("deepctl_core.auth.keyring") as mock_kr:
            mock_kr.get_password.return_value = None
            with patch.dict("os.environ", {"DEEPGRAM_API_KEY": "sk-env"}):
                result = auth.get_api_key()

        assert result == "sk-env"

    def test_get_api_key_explicit_bypasses_jwt_path(self, mock_config):
        auth = AuthManager(mock_config, explicit_api_key="sk-explicit")

        with patch.object(auth, "_get_dg_token_via_jwt") as mock_jwt:
            result = auth.get_api_key()

        assert result == "sk-explicit"
        mock_jwt.assert_not_called()

    # ------------------------------------------------------------------
    # _get_dg_token_via_jwt — cache and refresh logic
    # ------------------------------------------------------------------

    def test_get_dg_token_returns_cached_token_when_valid(self, mock_config):
        from datetime import timezone

        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        mock_profile = Mock()
        mock_profile.dg_token_expires_at = future
        mock_profile.jwt_expires_at = future
        mock_config.get_profile.return_value = mock_profile

        auth = self._make_auth(mock_config)

        with patch("deepctl_core.auth.keyring") as mock_kr:
            mock_kr.get_password.return_value = "cached-dg-token"
            result = auth._get_dg_token_via_jwt("any-jwt", "default")

        assert result == "cached-dg-token"

    def test_get_dg_token_refreshes_jwt_when_expired(self, mock_config):
        from datetime import timezone

        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        mock_profile = Mock()
        mock_profile.dg_token_expires_at = None
        mock_profile.jwt_expires_at = past
        mock_config.get_profile.return_value = mock_profile

        auth = self._make_auth(mock_config)

        with patch("deepctl_core.auth.keyring") as mock_kr:
            mock_kr.get_password.return_value = None
            with patch.object(auth, "_refresh_jwt", return_value="new-jwt") as mock_refresh:
                with patch.object(auth, "_exchange_for_dg_token", return_value="dg-tok"):
                    auth._get_dg_token_via_jwt("old-jwt", "default")

        mock_refresh.assert_called_once_with("default")

    def test_get_dg_token_exchanges_when_no_cache(self, mock_config):
        from datetime import timezone

        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        mock_profile = Mock()
        mock_profile.dg_token_expires_at = None
        mock_profile.jwt_expires_at = future
        mock_config.get_profile.return_value = mock_profile

        auth = self._make_auth(mock_config)

        with patch("deepctl_core.auth.keyring") as mock_kr:
            mock_kr.get_password.return_value = None
            with patch.object(auth, "_exchange_for_dg_token", return_value="dg-tok") as mock_ex:
                result = auth._get_dg_token_via_jwt("jwt-tok", "default")

        assert result == "dg-tok"
        mock_ex.assert_called_once_with("jwt-tok", "default")

    # ------------------------------------------------------------------
    # _refresh_jwt
    # ------------------------------------------------------------------

    def test_refresh_jwt_raises_when_no_refresh_token(self, mock_config):
        from deepctl_core.auth import AuthenticationError

        auth = self._make_auth(mock_config)

        with patch("deepctl_core.auth.keyring") as mock_kr:
            mock_kr.get_password.return_value = None
            with pytest.raises(AuthenticationError, match="refresh token"):
                auth._refresh_jwt("default")

    def test_refresh_jwt_updates_keyring_and_config(self, mock_config):
        mock_profile = Mock()
        mock_config.get_profile.return_value = mock_profile

        auth = self._make_auth(mock_config)
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new-jwt",
            "expires_in": 900,
        }
        auth.client = Mock()
        auth.client.post.return_value = mock_response

        with patch("deepctl_core.auth.keyring") as mock_kr:
            mock_kr.get_password.return_value = "old-refresh"
            result = auth._refresh_jwt("default")

        assert result == "new-jwt"
        set_calls = {c[0][1]: c[0][2] for c in mock_kr.set_password.call_args_list}
        assert set_calls["jwt.default"] == "new-jwt"

    def test_refresh_jwt_raises_on_http_error(self, mock_config):
        from deepctl_core.auth import AuthenticationError

        auth = self._make_auth(mock_config)
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.json.return_value = {}
        auth.client = Mock()
        auth.client.post.return_value = mock_response

        with patch("deepctl_core.auth.keyring") as mock_kr:
            mock_kr.get_password.return_value = "old-refresh"
            with pytest.raises(AuthenticationError, match="Token refresh failed"):
                auth._refresh_jwt("default")

    # ------------------------------------------------------------------
    # _exchange_for_dg_token
    # ------------------------------------------------------------------

    def test_exchange_for_dg_token_returns_token_and_caches(self, mock_config):
        mock_profile = Mock()
        mock_config.get_profile.return_value = mock_profile

        auth = self._make_auth(mock_config)
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"dg_token": "dg-abc", "expires_in": 86400}
        auth.client = Mock()
        auth.client.post.return_value = mock_response

        with patch("deepctl_core.auth.keyring") as mock_kr:
            result = auth._exchange_for_dg_token("jwt-tok", "default")

        assert result == "dg-abc"
        set_calls = {c[0][1]: c[0][2] for c in mock_kr.set_password.call_args_list}
        assert set_calls["api-key.default"] == "dg-abc"

    def test_exchange_for_dg_token_raises_on_http_error(self, mock_config):
        from deepctl_core.auth import AuthenticationError

        auth = self._make_auth(mock_config)
        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.json.return_value = {}
        auth.client = Mock()
        auth.client.post.return_value = mock_response

        with patch("deepctl_core.auth.keyring"):
            with pytest.raises(AuthenticationError, match="Credentials exchange failed"):
                auth._exchange_for_dg_token("jwt-tok", "default")

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
    # has_profile_credentials — JWT counts as having credentials
    # ------------------------------------------------------------------

    def test_has_profile_credentials_true_when_jwt_in_keyring(self, mock_config):
        mock_profile = Mock()
        mock_profile.api_key = None
        mock_profile.project_id = "proj-1"
        mock_config.get_profile.return_value = mock_profile

        auth = self._make_auth(mock_config)

        def keyring_side_effect(service, key):
            if key == "jwt.test-profile":
                return "some-jwt"
            return None

        with patch("deepctl_core.auth.keyring") as mock_kr:
            mock_kr.get_password.side_effect = keyring_side_effect
            has_key, has_project = auth.has_profile_credentials("test-profile")

        assert has_key is True
        assert has_project is True
