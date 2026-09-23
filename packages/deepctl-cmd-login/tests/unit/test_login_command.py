"""Tests for the login command."""

from pathlib import Path
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
from deepctl_core.skill_bundle import RepoSkill


@pytest.fixture(autouse=True)
def _no_real_skill_installs():
    """Keep the post-login skills prompt off this machine.

    A successful login calls ``_maybe_prompt_skills_setup()``, whose only
    guard is ``sys.stdout.isatty()``. Under ``pytest -s`` that is True, and
    the prompt then downloads the deepgram/skills bundle and installs it
    into the real ``~/.claude/skills`` and friends. Reporting no detected
    tools stops it at the first branch; the tests that exercise the prompt
    itself patch this same function and win over this fixture.
    """
    with patch("deepctl_core.skill_generator.detect_ai_clis", return_value=[]):
        yield


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


class TestWhoamiKeySource:
    """The key-source label must name where the key actually came from.

    Config merges DEEPGRAM_API_KEY into the profile at load time, so the
    profile's api_key being set does not prove the key came from the config
    file. whoami used to label an environment-sourced key "config file".
    """

    @pytest.fixture
    def whoami_command(self):
        from deepctl_cmd_login.command import WhoamiCommand

        return WhoamiCommand()

    def _run(self, whoami_command, mock_config, mock_client, env, profile_key):
        auth_manager = Mock(spec=AuthManager)
        auth_manager.get_project_id.return_value = "proj-1"
        mock_config.get_profile.return_value.api_key = profile_key
        with (
            patch.dict("os.environ", env, clear=False),
            patch("keyring.get_password", return_value=None),
        ):
            if "DEEPGRAM_API_KEY" not in env:
                import os

                os.environ.pop("DEEPGRAM_API_KEY", None)
            return whoami_command.handle(
                config=mock_config,
                auth_manager=auth_manager,
                client=mock_client,
            )

    def test_env_key_merged_into_profile_labeled_env(
        self, whoami_command, mock_config, mock_client
    ):
        """A profile key equal to DEEPGRAM_API_KEY is labeled as env."""
        result = self._run(
            whoami_command,
            mock_config,
            mock_client,
            env={"DEEPGRAM_API_KEY": "dg_env_key_12345"},
            profile_key="dg_env_key_12345",
        )
        assert result.key_source == "DEEPGRAM_API_KEY (env)"
        assert result.authenticated is True

    def test_real_config_file_key_still_labeled_config_file(
        self, whoami_command, mock_config, mock_client
    ):
        """A profile key with no matching env var keeps the config label."""
        result = self._run(
            whoami_command,
            mock_config,
            mock_client,
            env={},
            profile_key="dg_file_key_67890",
        )
        assert result.key_source == "config file"

    def test_env_key_without_profile_labeled_env(
        self, whoami_command, mock_config, mock_client
    ):
        """No profile key, env set: the env fallback branch labels env."""
        result = self._run(
            whoami_command,
            mock_config,
            mock_client,
            env={"DEEPGRAM_API_KEY": "dg_env_only_11111"},
            profile_key=None,
        )
        assert result.key_source == "DEEPGRAM_API_KEY (env)"


class TestLoginRecordsTheSameStateAsSkillsInstall:
    """`dg login` writes the record `dg skills list/update/remove` then read."""

    def _generator(self, cli_name, display_name, root, paths):
        gen = MagicMock()
        gen.cli_name = cli_name
        gen.display_name = display_name
        gen.skills_root.return_value = root
        gen.install_conflicts.return_value = []
        gen.install_skills.return_value = paths
        gen.prune_retired.return_value = []
        gen.manual_hint.return_value = f"{display_name} has no skills directory."
        return gen

    def _run(self, generators, state, skills=("api", "docs")):
        cmd = LoginCommand()
        cmd._guided = True
        bundle = [
            RepoSkill(name=name, path=Path("/upstream") / name) for name in skills
        ]
        with (
            patch("sys.stdout") as mock_stdout,
            patch(
                "deepctl_core.skill_generator.detect_ai_clis", return_value=generators
            ),
            patch(
                "deepctl_core.skill_generator.get_skills_state", return_value=state
            ),
            patch("deepctl_core.skill_generator.save_skills_state"),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata",
                return_value=[],
            ),
            patch(
                "deepctl_core.skill_generator.fetch_repo_skills", return_value=bundle
            ) as fetch,
            patch("deepctl_cmd_login.command.Prompt.ask", return_value="all"),
        ):
            mock_stdout.isatty.return_value = True
            cmd._maybe_prompt_skills_setup()
        self.fetch = fetch
        return state

    def test_it_records_the_upstream_ref_and_skill_names(self, tmp_path):
        """Without these, `dg skills list` prints '?' for the ref it pinned."""
        from deepctl_core.skill_bundle import DEFAULT_SKILLS_REF

        root = tmp_path / ".claude" / "skills"
        gen = self._generator(
            "claude", "Claude Code", root, [root / "api", root / "docs"]
        )
        state = self._run([gen], {"installed_skills": {}})

        entry = state["installed_skills"]["claude"]
        assert entry["skills_ref"] == DEFAULT_SKILLS_REF
        assert entry["skills"] == ["api", "docs"]
        assert [Path(p).name for p in entry["paths"]] == ["api", "docs"]

    def test_a_tool_with_no_skills_directory_is_not_recorded(self):
        """Nothing was written for it, so nothing may claim it was."""
        gen = self._generator("amazonq", "Amazon Q Developer", None, [])
        with patch("deepctl_cmd_login.command.console") as printer:
            state = self._run([gen], {"installed_skills": {}})

        assert state["installed_skills"] == {}
        # The empty map is also what this started as, so on its own it
        # would pass if the whole block had thrown into login's bare
        # `except`. The hint only prints from the far side of the
        # install, which is what pins down that it ran and declined.
        printed = " ".join(str(c) for c in printer.print.call_args_list)
        assert "Amazon Q Developer has no skills directory." in printed
        gen.install_skills.assert_not_called()

    def test_a_second_tool_failing_leaves_the_first_recorded(self, tmp_path):
        """Login used to save state only after the whole loop.

        It installed tool by tool, refetching the bundle each time, and a
        later failure hit the bare `except` before `save_skills_state`.
        Whatever the earlier tools had written was then folders deepctl
        would neither update nor remove.
        """
        root = tmp_path / ".claude" / "skills"
        first = self._generator("claude", "Claude Code", root, [root / "api"])
        second = self._generator(
            "cursor", "Cursor", tmp_path / ".cursor" / "skills", []
        )
        second.install_skills.side_effect = OSError(30, "Read-only file system")

        with patch("deepctl_cmd_login.command.console") as printer:
            state = self._run(
                [first, second], {"installed_skills": {}}, skills=("api",)
            )

        entry = state["installed_skills"]["claude"]
        assert [Path(p).name for p in entry["paths"]] == ["api"]
        assert "cursor" not in state["installed_skills"]
        # And the user is told, rather than the login going quiet on it.
        # Not just "Cursor" -- every detected tool is named in the menu
        # printed before the install, so that would match either way.
        printed = " ".join(str(c) for c in printer.print.call_args_list)
        assert "Cursor: [Errno 30] Read-only file system" in printed
        assert "Run 'dg skills install' to retry" in printed

    def test_the_bundle_is_fetched_once_for_every_tool(self, tmp_path):
        """Two fetches could install two different revisions side by side."""
        claude_root = tmp_path / ".claude" / "skills"
        cursor_root = tmp_path / ".cursor" / "skills"
        generators = [
            self._generator(
                "claude", "Claude Code", claude_root, [claude_root / "api"]
            ),
            self._generator("cursor", "Cursor", cursor_root, [cursor_root / "api"]),
        ]

        self._run(generators, {"installed_skills": {}}, skills=("api",))

        assert self.fetch.call_count == 1
