"""Tests for the login command."""

from unittest.mock import MagicMock, Mock, call, patch

import pytest
from deepctl_cmd_login.command import (
    LoginCommand,
    LogoutCommand,
    ProfilesCommand,
)
from deepctl_cmd_login.models import LoginResult, LogoutResult
from deepctl_core import AuthManager, Config, DeepgramClient
from deepctl_core.models import ProfileInfo, ProfilesResult


@pytest.fixture
def mock_config():
    """Create a mock config instance."""
    config = Mock(spec=Config)
    config.config_path = "/mock/path/config.yaml"
    config.profile = None
    config._config = Mock()
    config._config.active_profile = None
    config._config.default_profile = "default"
    config.list_profiles.return_value = []
    config.save = Mock()

    # Mock profile configuration
    mock_profile = Mock()
    mock_profile.api_key = None
    mock_profile.project_id = None
    mock_profile.base_url = "https://api.deepgram.com"

    config.get_profile.return_value = mock_profile
    return config


@pytest.fixture
def mock_auth_manager():
    """Create a mock auth manager instance."""
    auth_manager = Mock(spec=AuthManager)
    auth_manager.has_env_credentials.return_value = (False, False)
    auth_manager.has_profile_credentials.return_value = (False, False)
    auth_manager.is_authenticated.return_value = False
    auth_manager.login_with_api_key = Mock()
    auth_manager.login_with_device_flow = Mock()
    auth_manager.logout = Mock()
    auth_manager.get_api_key.return_value = None
    auth_manager.get_project_id.return_value = None
    return auth_manager


@pytest.fixture
def mock_client():
    """Create a mock Deepgram client."""
    return Mock(spec=DeepgramClient)


@pytest.fixture
def login_command():
    """Create a login command instance."""
    return LoginCommand()


class TestLoginCommand:
    """Test LoginCommand class."""

    def test_login_with_env_vars_warning(
        self, login_command, mock_config, mock_auth_manager, mock_client
    ):
        """Test login shows warning when environment variables are set."""
        # Set up environment variable detection
        mock_auth_manager.has_env_credentials.return_value = (True, True)

        # Mock user declining to login
        with patch.object(login_command, "confirm", return_value=False):
            result = login_command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
            )

        assert result.status == "cancelled"
        assert (
            result.message == "Login cancelled - using environment variables"
        )

    def test_login_with_env_vars_no_project(
        self, login_command, mock_config, mock_auth_manager, mock_client
    ):
        """Test login warning when only API key env var is set."""
        # Only API key env var is set
        mock_auth_manager.has_env_credentials.return_value = (True, False)

        # Mock user confirming login
        with patch.object(login_command, "confirm", return_value=True):
            # Mock web auth
            mock_auth_manager.login_with_device_flow.return_value = None
            mock_auth_manager.get_api_key.return_value = "sk-test"
            mock_auth_manager.get_project_id.return_value = "test-project"

            result = login_command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
            )

        # Should proceed with login
        assert result.status == "success"
        mock_auth_manager.login_with_device_flow.assert_called_once()

    def test_re_login_to_existing_profile(
        self, login_command, mock_config, mock_auth_manager, mock_client
    ):
        """Test re-login prompt for existing profile."""
        # Profile already has credentials
        mock_auth_manager.has_profile_credentials.return_value = (True, True)

        # Mock user confirming re-login
        with patch.object(login_command, "confirm", side_effect=[True]):
            # Mock web auth
            mock_auth_manager.login_with_device_flow.return_value = None
            mock_auth_manager.get_api_key.return_value = "sk-test"
            mock_auth_manager.get_project_id.return_value = "test-project"

            result = login_command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
            )

        assert result.status == "success"
        mock_auth_manager.login_with_device_flow.assert_called_once()

    def test_login_with_different_profile(
        self, login_command, mock_config, mock_auth_manager, mock_client
    ):
        """Test login with a different profile when one exists."""
        # Profile already has credentials
        mock_auth_manager.has_profile_credentials.return_value = (True, True)

        # Mock user declining re-login but wanting another profile
        with patch.object(login_command, "confirm", side_effect=[False, True]):
            with patch(
                "deepctl_cmd_login.command.Prompt.ask", return_value="work"
            ):
                # Mock web auth
                mock_auth_manager.login_with_device_flow.return_value = None
                mock_auth_manager.get_api_key.return_value = "sk-test"
                mock_auth_manager.get_project_id.return_value = "test-project"

                result = login_command.handle(
                    config=mock_config,
                    auth_manager=mock_auth_manager,
                    client=mock_client,
                )

        # Should have switched to new profile
        assert mock_config.profile == "work"
        assert result.status == "success"
        assert result.profile == "work"

    def test_login_with_api_key_updates_active_profile(
        self, login_command, mock_config, mock_auth_manager, mock_client
    ):
        """Test that successful login updates the active profile."""
        # Mock successful API key login
        mock_auth_manager.login_with_api_key.return_value = None

        result = login_command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            api_key="sk-test-key",
            project_id="test-project",
            force_write=True,
        )

        # Should update active profile
        assert mock_config._config.active_profile == "default"
        mock_config.save.assert_called_once()
        assert result.status == "success"

    def test_login_with_explicit_profile(
        self, login_command, mock_config, mock_auth_manager, mock_client
    ):
        """Test login with explicit profile parameter."""
        # Mock web auth
        mock_auth_manager.login_with_device_flow.return_value = None
        mock_auth_manager.get_api_key.return_value = "sk-test"
        mock_auth_manager.get_project_id.return_value = "test-project"

        result = login_command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            profile="production",
        )

        # Should use the specified profile
        assert mock_config.profile == "production"
        assert mock_config._config.active_profile == "production"
        assert result.profile == "production"


class TestLogoutCommand:
    """Test LogoutCommand class."""

    def test_logout_clears_active_profile(
        self, mock_config, mock_auth_manager, mock_client
    ):
        """Test that logout clears active profile when logging out from it."""
        command = LogoutCommand()

        # Set active profile
        mock_config._config.active_profile = "default"
        mock_config.profile = "default"
        mock_config.list_profiles.return_value = ["default"]

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
        )

        # Should clear active profile
        assert mock_config._config.active_profile is None
        mock_config.save.assert_called_once()
        mock_auth_manager.logout.assert_called_once()

    def test_logout_keeps_active_profile_for_other(
        self, mock_config, mock_auth_manager, mock_client
    ):
        """Test that logout doesn't clear active profile when logging out from different profile."""
        command = LogoutCommand()

        # Active profile is different
        mock_config._config.active_profile = "work"
        mock_config.profile = "default"
        mock_config.list_profiles.return_value = ["default", "work"]

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
        )

        # Should NOT clear active profile
        assert mock_config._config.active_profile == "work"
        mock_config.save.assert_not_called()
        mock_auth_manager.logout.assert_called_once()

    def test_logout_all_clears_active_profile(
        self, mock_config, mock_auth_manager, mock_client
    ):
        """Test that logout --all clears active profile."""
        command = LogoutCommand()

        # Set active profile and multiple profiles
        mock_config._config.active_profile = "work"
        mock_config.list_profiles.return_value = ["default", "work", "test"]

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            all=True,
        )

        # Should clear active profile
        assert mock_config._config.active_profile is None
        mock_config.save.assert_called_once()
        assert result.profiles_count == 3


class TestProfilesCommand:
    """Test ProfilesCommand class."""

    def test_switch_profile_requires_credentials(
        self, mock_config, mock_auth_manager, mock_client
    ):
        """Test that switching profiles checks for credentials."""
        command = ProfilesCommand()

        mock_config.list_profiles.return_value = ["default", "work"]

        # Mock keyring to return no credentials
        with patch("keyring.get_password") as mock_get_password:
            mock_get_password.return_value = None

            # Mock profile with no credentials
            mock_profile = Mock()
            mock_profile.api_key = None
            mock_config.get_profile.return_value = mock_profile

            result = command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                switch="work",
            )

        assert result.status == "error"
        assert "No credentials found" in result.message

    def test_switch_profile_updates_active(
        self, mock_config, mock_auth_manager, mock_client
    ):
        """Test that switching profiles updates active profile."""
        command = ProfilesCommand()

        mock_config.list_profiles.return_value = ["default", "work"]

        # Mock keyring to return credentials
        with patch("keyring.get_password") as mock_get_password:
            mock_get_password.return_value = "sk-test-key"

            # Mock profile with project ID
            mock_profile = Mock()
            mock_profile.project_id = "test-project"
            mock_config.get_profile.return_value = mock_profile

            result = command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                switch="work",
            )

        assert result.status == "success"
        assert mock_config._config.active_profile == "work"
        mock_config.save.assert_called_once()

    def test_list_profiles_shows_current(
        self, mock_config, mock_auth_manager, mock_client
    ):
        """Test that list profiles indicates current profile."""
        command = ProfilesCommand()

        # Set up mock profiles result
        mock_auth_manager.list_profiles.return_value = ProfilesResult(
            profiles={
                "default": ProfileInfo(
                    api_key="****abcd",
                    project_id="proj-1",
                    base_url="https://api.deepgram.com",
                ),
                "work": ProfileInfo(
                    api_key="****efgh",
                    project_id="proj-2",
                    base_url="https://api.deepgram.com",
                ),
            },
            current_profile="default",
        )

        mock_config.profile = "default"

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            list=True,
        )

        assert isinstance(result, ProfilesResult)
        assert len(result.profiles) == 2


class TestMaybePromptSkillsSetup:
    """Verify the post-login skills-setup prompt respects _guided + non-tty."""

    def test_returns_early_when_not_guided(self):
        cmd = LoginCommand()
        cmd._guided = False
        with patch("sys.stdout") as mock_stdout, patch(
            "deepctl_core.skill_generator.detect_ai_clis"
        ) as mock_detect:
            mock_stdout.isatty.return_value = True
            cmd._maybe_prompt_skills_setup()
        mock_detect.assert_not_called()

    def test_returns_early_when_not_tty(self):
        cmd = LoginCommand()
        cmd._guided = True
        with patch("sys.stdout") as mock_stdout, patch(
            "deepctl_core.skill_generator.detect_ai_clis"
        ) as mock_detect:
            mock_stdout.isatty.return_value = False
            cmd._maybe_prompt_skills_setup()
        mock_detect.assert_not_called()

    def test_proceeds_when_guided_and_tty(self):
        cmd = LoginCommand()
        cmd._guided = True
        with patch("sys.stdout") as mock_stdout, patch(
            "deepctl_core.skill_generator.detect_ai_clis", return_value=[]
        ) as mock_detect, patch(
            "deepctl_core.skill_generator.get_skills_state",
            return_value={"installed_skills": {}},
        ):
            mock_stdout.isatty.return_value = True
            cmd._maybe_prompt_skills_setup()
        mock_detect.assert_called_once()
