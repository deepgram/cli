"""Tests for the login command."""

import contextlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

import pytest
from deepctl_cmd_login.command import (
    LoginCommand,
    LogoutCommand,
    ProfilesCommand,
)
from deepctl_cmd_login.models import LoginResult, LogoutResult
from deepctl_core import AuthManager, Config, DeepgramClient, output, skill_generator
from deepctl_core.models import ProfileInfo, ProfilesResult
from deepctl_core.skill_bundle import RepoSkill
from deepctl_core.skill_generator import AiderGenerator

#: Set by the autouse fixture below, so one test can prove it is in force.
_ACTIVE_THROWAWAY_HOME: Path | None = None


@pytest.fixture(autouse=True)
def _throwaway_home(tmp_path, monkeypatch):
    """Point every home-derived path in this module at a throwaway directory.

    Nothing here may read or write the home of whoever is running pytest.
    The post-login skills step reads ``skills.json`` and the generators
    resolve ``Path.home()`` when called, while ``_SKILLS_DIR``,
    ``_STATE_FILE``, ``_REPO_CACHE_DIR`` and ``AiderGenerator._LEGACY_FILE``
    are evaluated at import time, so each is pointed at the throwaway home
    by hand. Same pattern as deepctl-core's test_skill_generator.py.
    """
    home = tmp_path / "throwaway-home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    # Rich sizes its output to the terminal; a narrow one folds a long
    # tmp_path in the middle, and assertions on that path then fail for
    # reasons that have nothing to do with what the command did.
    monkeypatch.setenv("COLUMNS", "400")

    skills_dir = home / ".deepctl" / "skills"
    monkeypatch.setattr(skill_generator, "_SKILLS_DIR", skills_dir)
    monkeypatch.setattr(skill_generator, "_STATE_FILE", skills_dir / "skills.json")
    monkeypatch.setattr(skill_generator, "_REPO_CACHE_DIR", skills_dir / "repo_cache")
    monkeypatch.setattr(
        AiderGenerator, "_LEGACY_FILE", skills_dir / "deepctl-conventions.md"
    )

    global _ACTIVE_THROWAWAY_HOME
    _ACTIVE_THROWAWAY_HOME = home
    yield home
    _ACTIVE_THROWAWAY_HOME = None


@pytest.fixture(autouse=True)
def _default_output_mode():
    """Start every test in default, non-agentic, non-quiet output mode.

    ``deepctl_core.output`` keeps the output format in a module-global
    ``_output_config`` that another package's tests can leave set to json
    or agentic. There is no public setter for the agentic flag, so the
    dict is snapshotted and restored directly.
    """
    saved = dict(output._output_config)
    saved_quiet = output.console.quiet
    output._output_config.update(format="default", agentic=False, quiet=False)
    output.console.quiet = False
    yield
    output._output_config.clear()
    output._output_config.update(saved)
    output.console.quiet = saved_quiet


def test_no_test_in_this_module_can_reach_the_real_home():
    """The guard above is the finding, so it gets its own assertion."""
    home = _ACTIVE_THROWAWAY_HOME
    assert home is not None, "the throwaway-home fixture is no longer autouse"
    assert Path.home() == home
    assert skill_generator._STATE_FILE.is_relative_to(home)
    assert skill_generator._REPO_CACHE_DIR.is_relative_to(home)


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
        assert result.message == "Login cancelled - using environment variables"

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
        with (
            patch.object(login_command, "confirm", side_effect=[False, True]),
            patch("deepctl_cmd_login.command.Prompt.ask", return_value="work"),
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

        command.handle(
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

        command.handle(
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
        with (
            patch("sys.stdout") as mock_stdout,
            patch("deepctl_core.skill_generator.detect_ai_clis") as mock_detect,
        ):
            mock_stdout.isatty.return_value = True
            cmd._maybe_prompt_skills_setup()
        mock_detect.assert_not_called()

    def test_returns_early_when_not_tty(self):
        cmd = LoginCommand()
        cmd._guided = True
        with (
            patch("sys.stdout") as mock_stdout,
            patch("deepctl_core.skill_generator.detect_ai_clis") as mock_detect,
        ):
            mock_stdout.isatty.return_value = False
            cmd._maybe_prompt_skills_setup()
        mock_detect.assert_not_called()

    def test_proceeds_when_guided_and_tty(self):
        cmd = LoginCommand()
        cmd._guided = True
        with (
            patch("sys.stdout") as mock_stdout,
            patch(
                "deepctl_core.skill_generator.detect_ai_clis", return_value=[]
            ) as mock_detect,
            patch(
                "deepctl_core.skill_generator.get_skills_state",
                return_value={"installed_skills": {}},
            ),
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
        gen.prune_retired_result.return_value = skill_generator.PruneResult()
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
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
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

    def test_a_tool_with_no_skills_directory_is_not_recorded(self, capsys):
        """Nothing was written for it, so nothing may claim it was."""
        gen = self._generator("amazonq", "Amazon Q Developer", None, [])
        state = self._run([gen], {"installed_skills": {}})

        assert state["installed_skills"] == {}
        # The empty map is also what this started as, so on its own it
        # would pass if the whole block had thrown into login's bare
        # `except`. The hint only prints from the far side of the
        # install, which is what pins down that it ran and declined.
        # On stderr, as the README says, and not on stdout.
        captured = capsys.readouterr()
        assert "Amazon Q Developer has no skills directory." in " ".join(
            captured.err.split()
        )
        assert "no skills directory" not in captured.out
        gen.install_skills.assert_not_called()

    def test_a_second_tool_failing_leaves_the_first_recorded(self, tmp_path, capsys):
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

        state = self._run([first, second], {"installed_skills": {}}, skills=("api",))

        entry = state["installed_skills"]["claude"]
        assert [Path(p).name for p in entry["paths"]] == ["api"]
        assert "cursor" not in state["installed_skills"]
        # And the user is told, rather than the login going quiet on it.
        # Not just "Cursor" -- every detected tool is named in the menu
        # printed before the install, so that would match either way.
        captured = capsys.readouterr()
        said = " ".join(captured.err.split())
        # The skills directory, not the errno or a staging folder inside it.
        cursor_root = tmp_path / ".cursor" / "skills"
        assert (
            f"Cursor: could not write to {cursor_root}: Read-only file system. "
            "Fix its permissions and run 'dg skills install' again." in said
        )
        assert "Errno" not in said
        assert "Read-only file system" not in captured.out

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


class TestASkillsProblemNeverFailsTheLogin:
    """The skills step is best-effort, but best-effort is not silent.

    It ended in `except Exception: pass`, so a crash anywhere in it left
    the user logged in, with nothing installed, and no hint that the step
    had run at all.
    """

    def _login(self, login_command, mock_config, mock_auth_manager, mock_client):
        """A successful `dg login --api-key` with the skills step reachable."""
        login_command._guided = True
        mock_auth_manager.login_with_api_key.return_value = None
        # Only isatty is faked. Replacing sys.stdout wholesale would also
        # swallow the warning under test, which the output helpers write
        # to stdout outside agentic mode.
        with patch.object(sys.stdout, "isatty", return_value=True):
            return login_command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                api_key="sk-test-key",
                project_id="proj",
                force_write=True,
            )

    def test_a_crash_in_the_skills_step_warns_and_the_login_still_succeeds(
        self, login_command, mock_config, mock_auth_manager, mock_client, capsys
    ):
        with patch(
            "deepctl_core.skill_generator.get_skills_state",
            side_effect=RuntimeError("records exploded"),
        ):
            result = self._login(
                login_command, mock_config, mock_auth_manager, mock_client
            )

        assert result.status == "success"
        captured = capsys.readouterr()
        said = " ".join((captured.out + captured.err).split())
        assert "Could not install the Deepgram skills" in said
        assert "RuntimeError: records exploded" in said
        assert "Run 'dg skills install' to retry" in said

    @pytest.mark.parametrize("unanswered", [EOFError, KeyboardInterrupt])
    def test_no_answer_at_the_skills_prompt_is_a_none(
        self,
        login_command,
        mock_config,
        mock_auth_manager,
        mock_client,
        capsys,
        unanswered,
    ):
        """stdin at EOF, or Ctrl-C, at the prompt: skip the skills, keep the login.

        The EOF fell into the broad except and printed "Could not install
        the Deepgram skills: EOFError", a failure for a question nobody
        answered.
        """
        gen = MagicMock()
        gen.cli_name = "claude"
        gen.display_name = "Claude Code"
        with (
            patch(
                "deepctl_core.skill_generator.get_skills_state",
                return_value={"installed_skills": {}},
            ),
            patch("deepctl_core.skill_generator.detect_ai_clis", return_value=[gen]),
            patch("deepctl_core.skill_generator.fetch_repo_skills") as fetch,
            patch("deepctl_cmd_login.command.Prompt.ask", side_effect=unanswered),
        ):
            result = self._login(
                login_command, mock_config, mock_auth_manager, mock_client
            )

        assert result.status == "success"
        said = " ".join((lambda c: c.out + c.err)(capsys.readouterr()).split())
        assert "You can run 'dg skills setup' later." in said
        assert "Could not install" not in said
        fetch.assert_not_called()
        gen.install_skills.assert_not_called()

    def test_a_skills_file_core_cannot_read_names_the_file(
        self,
        login_command,
        mock_config,
        mock_auth_manager,
        mock_client,
        capsys,
        monkeypatch,
    ):
        """Core raises SkillsStateError for a skills.json it cannot read."""
        with patch(
            "deepctl_core.skill_generator.get_skills_state",
            side_effect=skill_generator.SkillsStateError(
                "Expecting value: line 1 column 1"
            ),
        ):
            result = self._login(
                login_command, mock_config, mock_auth_manager, mock_client
            )

        assert result.status == "success"
        captured = capsys.readouterr()
        said = " ".join((captured.out + captured.err).split())
        assert str(skill_generator._STATE_FILE) in said
        assert "Expecting value" in said
        assert "dg skills install" in said

    def test_a_clean_skills_step_prints_no_warning(
        self, login_command, mock_config, mock_auth_manager, mock_client, capsys
    ):
        """Positive control: the warning is for failures, not for every login."""
        with patch(
            "deepctl_core.skill_generator.get_skills_state",
            return_value={"installed_skills": {}},
        ):
            result = self._login(
                login_command, mock_config, mock_auth_manager, mock_client
            )

        assert result.status == "success"
        captured = capsys.readouterr()
        assert (
            "Could not install the Deepgram skills" not in captured.out + captured.err
        )


@contextlib.contextmanager
def _records_lock_held_elsewhere(monkeypatch):
    """Hold core's skills.json lock through a second handle, as another process would.

    ``flock`` and ``msvcrt.locking`` belong to the open file, so a second
    ``open()`` in this process contends exactly like another deepctl.
    The wait is cut to a fraction of a second so the test stays fast.
    """
    lock_file = getattr(skill_generator, "_lock_file", None)
    try_lock = getattr(skill_generator, "_try_lock", None)
    unlock = getattr(skill_generator, "_unlock", None)
    if not (callable(lock_file) and callable(try_lock) and callable(unlock)):
        pytest.skip("core has no cross-process skills.json lock yet")
    monkeypatch.setattr(skill_generator, "_STATE_LOCK_TIMEOUT", 0.2, raising=False)
    path = lock_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as other:
        assert try_lock(other)
        try:
            yield
        finally:
            unlock(other)


class TestTheSkillsStepSavesUnderTheLock:
    """Login prompts, then reads, installs and saves `skills.json` under the lock.

    The prompt can sit open for minutes. A copy of the records read before
    it and saved after would overwrite whatever another deepctl recorded
    in the meantime, so the read that the save is built on happens after
    the prompt, with the lock held.
    """

    def _generator(self, root):
        gen = MagicMock()
        gen.cli_name = "claude"
        gen.display_name = "Claude Code"
        gen.skills_root.return_value = root
        gen.install_conflicts.return_value = []
        gen.install_skills.return_value = [root / "api"]
        gen.prune_retired_result.return_value = skill_generator.PruneResult()
        return gen

    def _run(self, gen, ask):
        cmd = LoginCommand()
        cmd._guided = True
        with (
            patch.object(sys.stdout, "isatty", return_value=True),
            patch("deepctl_core.skill_generator.detect_ai_clis", return_value=[gen]),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata",
                return_value=[],
            ),
            patch(
                "deepctl_core.skill_generator.fetch_repo_skills",
                return_value=[RepoSkill(name="api", path=Path("/upstream/api"))],
            ),
            patch("deepctl_cmd_login.command.Prompt.ask", side_effect=ask),
        ):
            cmd._maybe_prompt_skills_setup()

    def test_a_record_another_deepctl_wrote_during_the_prompt_survives(self, tmp_path):
        state_file = Path(skill_generator._STATE_FILE)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"installed_skills": {}}))
        other = {"paths": [], "skills": [], "version": "9.9.9"}

        def ask(*_args, **_kwargs):
            # Another deepctl installs for Cursor while the prompt is open.
            state_file.write_text(json.dumps({"installed_skills": {"cursor": other}}))
            return "all"

        self._run(self._generator(tmp_path / ".claude" / "skills"), ask)

        saved = json.loads(state_file.read_text())["installed_skills"]
        assert saved["cursor"] == other
        assert [Path(p).name for p in saved["claude"]["paths"]] == ["api"]

    def test_records_held_by_another_process_warn_and_the_login_goes_on(
        self, tmp_path, monkeypatch, capsys
    ):
        gen = self._generator(tmp_path / ".claude" / "skills")
        with _records_lock_held_elsewhere(monkeypatch):
            # Returns rather than raising: the login is not failed.
            self._run(gen, lambda *_a, **_k: "all")

        captured = capsys.readouterr()
        said = " ".join(captured.err.split())
        assert "Skipped the Deepgram skills install" not in captured.out
        assert "Skipped the Deepgram skills install" in said
        assert "holds the skill records lock" in said
        # Core's message says to wait; login adds only the command.
        assert said.count("ait for it to finish") == 1
        assert said.count("dg skills install") == 1
        assert "exits. Then run 'dg skills install'." in said
        # The file is fine, so the advice must not be to delete it.
        assert "Fix or delete" not in said
        gen.install_skills.assert_not_called()

    def test_a_legacy_file_left_in_place_is_named(self, tmp_path, capsys):
        from deepctl_core.skill_generator import SkillInstallReport

        report = SkillInstallReport(
            ref="v1",
            skills=[],
            written={},
            unsupported=[],
            conflicts=[],
            failures=[],
        )
        legacy = tmp_path / "CONVENTIONS.md"
        report.legacy_skipped = [
            ("Aider", legacy, "it holds text deepctl did not write")
        ]
        with (
            patch(
                "deepctl_core.skill_generator.install_skills_for", return_value=report
            ),
            patch("deepctl_core.skill_generator.save_skills_state"),
        ):
            self._run(self._generator(tmp_path / "skills"), lambda *_a, **_k: "all")

        # Captured output, not the repr of mock calls: that doubles every
        # backslash in a Windows path, so the substring never matched there.
        captured = capsys.readouterr()
        assert (
            f"Aider: left {legacy} in place: it holds text deepctl did not write"
            in " ".join(captured.err.split())
        )
        assert "in place" not in captured.out

    def test_a_retired_skill_it_deleted_is_named(self, tmp_path, capsys):
        from deepctl_core.skill_generator import SkillInstallReport

        root = tmp_path / "skills"
        report = SkillInstallReport(
            ref="v1.8.0",
            skills=[],
            written={},
            unsupported=[],
            conflicts=[],
            failures=[],
        )
        report.pruned = {"claude": [root / "self-hosted"]}
        report.pruned_tools = {"claude": "Claude Code"}
        with (
            patch(
                "deepctl_core.skill_generator.install_skills_for", return_value=report
            ),
            patch("deepctl_core.skill_generator.save_skills_state"),
        ):
            self._run(self._generator(root), lambda *_a, **_k: "all")

        captured = capsys.readouterr()
        assert (
            "Removed retired skill self-hosted from Claude Code "
            "(no longer in deepgram/skills@v1.8.0)" in " ".join(captured.err.split())
        )
        assert "retired" not in captured.out

    def test_the_lock_is_not_held_while_the_bundle_downloads(self, tmp_path):
        state_file = Path(skill_generator._STATE_FILE)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"installed_skills": {}}))
        held_during_fetch = []

        def fetch(*_args, **_kwargs):
            held_during_fetch.append(skill_generator._state_lock_depth)
            return [RepoSkill(name="api", path=Path("/upstream/api"))]

        gen = self._generator(tmp_path / ".claude" / "skills")
        with patch(
            "deepctl_core.skill_generator.fetch_repo_skills", side_effect=fetch
        ) as patched:
            cmd = LoginCommand()
            cmd._guided = True
            with (
                patch.object(sys.stdout, "isatty", return_value=True),
                patch(
                    "deepctl_core.skill_generator.detect_ai_clis", return_value=[gen]
                ),
                patch(
                    "deepctl_core.skill_generator.collect_command_metadata",
                    return_value=[],
                ),
                patch("deepctl_cmd_login.command.Prompt.ask", return_value="all"),
            ):
                cmd._maybe_prompt_skills_setup()

        assert patched.call_count == 1
        assert held_during_fetch == [0]
        saved = json.loads(state_file.read_text())["installed_skills"]
        assert [Path(p).name for p in saved["claude"]["paths"]] == ["api"]

    def test_records_damaged_during_the_prompt_are_not_replaced(self, tmp_path, capsys):
        state_file = Path(skill_generator._STATE_FILE)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"installed_skills": {}}))

        def ask(*_args, **_kwargs):
            state_file.write_text(json.dumps({"installed_skills": "oops"}))
            return "all"

        gen = self._generator(tmp_path / ".claude" / "skills")
        self._run(gen, ask)

        gen.install_skills.assert_not_called()
        assert json.loads(state_file.read_text()) == {"installed_skills": "oops"}
        said = " ".join(capsys.readouterr().err.split())
        assert "Skipped the Deepgram skills install" in said
        assert "'installed_skills'" in said

    def test_an_invalid_environment_ref_names_the_variable_not_a_retry(
        self, tmp_path, monkeypatch, capsys
    ):
        """A retry reads the same DEEPCTL_SKILLS_REF and fails the same way.

        The warning said "Run 'dg skills install' to retry." without naming
        the variable. It also said "Could not download" for a ref refused
        before any download began, and doubled the reason's full stop.
        """
        from deepctl_core.skill_bundle import REF_ENV_VAR

        monkeypatch.setenv(REF_ENV_VAR, "a..b")
        gen = self._generator(tmp_path / ".claude" / "skills")
        self._run(gen, lambda *_a, **_k: "all")

        said = " ".join(capsys.readouterr().err.split())
        assert "Could not install the Deepgram skills: Invalid skills ref" in said
        assert "Could not download" not in said
        assert (
            f"segment. That ref came from {REF_ENV_VAR}. Set it to another ref, "
            "or unset it to use the pinned release. Then run 'dg skills install'."
            in said
        )
        assert "to retry" not in said
        assert ".. " not in said.replace("'..'", "")
        gen.install_skills.assert_not_called()

    @pytest.mark.parametrize(
        ("env", "names", "advice"),
        [
            (
                None,
                "which came from deepctl's pinned default.",
                "Run 'dg skills install --ref <tag>' to choose another ref.",
            ),
            (
                "gone-from-env",
                "which came from DEEPCTL_SKILLS_REF.",
                "Set DEEPCTL_SKILLS_REF to another ref, or unset it, then run "
                "'dg skills install'.",
            ),
        ],
    )
    def test_a_missing_ref_advises_choosing_another_not_a_retry(
        self, tmp_path, monkeypatch, capsys, env, names, advice
    ):
        """A retry reuses DEEPCTL_SKILLS_REF or the pinned tag: the same 404."""
        import urllib.error

        from deepctl_core import skill_bundle

        if env is None:
            monkeypatch.delenv(skill_bundle.REF_ENV_VAR, raising=False)
        else:
            monkeypatch.setenv(skill_bundle.REF_ENV_VAR, env)
        not_found = urllib.error.HTTPError(
            "https://codeload.github.com/x", 404, "Not Found", {}, None
        )

        def fetch(ref=None, *, force=False, ref_source=None):
            return skill_bundle.fetch_skill_bundle(
                ref, cache_dir=tmp_path / "cache", force=force, ref_source=ref_source
            )

        gen = self._generator(tmp_path / ".claude" / "skills")
        cmd = LoginCommand()
        cmd._guided = True
        with (
            patch.object(sys.stdout, "isatty", return_value=True),
            patch("deepctl_core.skill_generator.detect_ai_clis", return_value=[gen]),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata",
                return_value=[],
            ),
            patch("deepctl_core.skill_generator.fetch_repo_skills", side_effect=fetch),
            patch("urllib.request.urlopen", side_effect=not_found),
            patch("deepctl_cmd_login.command.Prompt.ask", return_value="all"),
        ):
            cmd._maybe_prompt_skills_setup()

        said = " ".join(capsys.readouterr().err.split())
        assert f"{names} {advice}" in said
        assert "to retry" not in said
        gen.install_skills.assert_not_called()

    def test_an_unreadable_skills_file_gives_its_fix_once(self, tmp_path, capsys):
        """Core's message carries the advice; login used to add a second one."""
        state_file = Path(skill_generator._STATE_FILE)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text("{not json")
        gen = self._generator(tmp_path / ".claude" / "skills")
        self._run(gen, lambda *_a, **_k: "all")

        said = " ".join(capsys.readouterr().err.split())
        assert "Skipped the Deepgram skills install" in said
        assert str(state_file) in said
        assert said.count("dg skills install") == 1
        assert "Fix or delete" not in said
        gen.install_skills.assert_not_called()

    def test_a_collision_says_how_to_retry(self, tmp_path, capsys):
        """The README says every warning names the command that retries it."""
        root = tmp_path / ".claude" / "skills"
        gen = self._generator(root)
        gen.install_conflicts.return_value = [root / "api"]
        self._run(gen, lambda *_a, **_k: "all")

        said = " ".join(capsys.readouterr().err.split())
        assert (
            f"Skipped Claude Code: {root / 'api'} is not deepctl's to replace. "
            "Move it aside, then run 'dg skills install'." in said
        )
        gen.install_skills.assert_not_called()

    def test_a_lock_path_that_is_a_directory_ends_its_sentence(self, tmp_path, capsys):
        """An OSError ends in a quoted path; the fix used to run straight on."""
        lock = skill_generator._lock_file()
        lock.mkdir(parents=True, exist_ok=True)
        gen = self._generator(tmp_path / ".claude" / "skills")
        self._run(gen, lambda *_a, **_k: "all")

        said = " ".join(capsys.readouterr().err.split())
        assert "Skipped the Deepgram skills install: Cannot open" in said
        # The OSError text differs by platform; the break before the fix
        # does not.
        assert "'. Fix or delete that file, then run 'dg skills install'." in said
        assert "' Fix or delete" not in said
        assert said.count("dg skills install") == 1
        gen.install_skills.assert_not_called()
