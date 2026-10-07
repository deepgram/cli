"""Tests for the login command."""

import errno
import hashlib
import json
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

import click
import pytest
from deepctl_cmd_login import command as login_module
from deepctl_cmd_login.command import (
    LoginCommand,
    LogoutCommand,
    ProfilesCommand,
)
from deepctl_cmd_login.models import LoginResult, LogoutResult
from deepctl_core import AuthManager, Config, DeepgramClient, output, skill_bundle
from deepctl_core import skill_generator as sg
from deepctl_core.models import ProfileInfo, ProfilesResult
from deepctl_core.skill_bundle import RepoSkill, SkillFetchError


@pytest.fixture(autouse=True)
def _no_real_skills_state(tmp_path, monkeypatch):
    """No test here reads or writes the developer's real skills.json (T19)."""
    skills_dir = tmp_path / "deepctl-skills"
    monkeypatch.setattr(sg, "_SKILLS_DIR", skills_dir)
    monkeypatch.setattr(sg, "_STATE_FILE", skills_dir / "skills.json")


def use_home(monkeypatch, home):
    """Point every home lookup the skills step makes at ``home``."""
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(sg, "_SKILLS_DIR", home / ".deepctl" / "skills")
    monkeypatch.setattr(sg, "_STATE_FILE", home / ".deepctl" / "skills" / "skills.json")
    return home


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A throwaway HOME with the output mode pinned (S4) and no PATH detection."""
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.delenv(skill_bundle.REF_ENV_VAR, raising=False)
    for con in (output.console, output.stderr_console, login_module.console):
        monkeypatch.setattr(con, "_width", 400)
    saved = dict(output._output_config)
    output._output_config.update(agentic=True, format="default", quiet=False)
    yield use_home(monkeypatch, tmp_path / "home")
    output._output_config.clear()
    output._output_config.update(saved)


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    """Patch the one fetch point; return the list of refs fetched."""
    fetched = []

    def fetch(ref=None):
        fetched.append(ref)
        skills = []
        for name in ("api", "docs"):
            folder = tmp_path / "bundle" / "skills" / name
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "SKILL.md").write_bytes(f"---\nname: {name}\n---\n{ref}\n".encode())
            skills.append(RepoSkill(name, folder))
        return skills

    monkeypatch.setattr(skill_bundle, "fetch_skill_bundle", fetch)
    return fetched


def detect(*clis):
    for cli in clis:
        Path.home().joinpath(*gen(cli).homes[0]).mkdir(parents=True, exist_ok=True)


def gen(cli):
    return next(g for g in sg.get_all_generators() if g.cli_name == cli)


def run_skills_step(monkeypatch, capsys, answer="all"):
    """Run login's skills step as a TTY user typing ``answer``; return (out, err)."""
    capsys.readouterr()
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    cmd = LoginCommand()
    cmd._guided = True
    with patch.object(login_module.Prompt, "ask", return_value=answer) as ask:
        cmd._maybe_prompt_skills_setup()
    out, err = capsys.readouterr()
    return click.unstyle(out), click.unstyle(err), ask


def normalized(home):
    """skills.json with paths rebased on ``home`` and timestamps dropped."""
    text = sg._STATE_FILE.read_text(encoding="utf-8")
    state = json.loads(text.replace(json.dumps(str(home))[1:-1], "~"))
    for section in ("skill_folders", "installed_skills"):
        for tool in state.get(section, {}).values():
            tool.pop("installed_at", None)
    return state


def sha_tree(path):
    return {
        str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path(path).rglob("*"))
        if p.is_file()
    }


RETRY = "run 'dg skills install' to try again"
AGAIN = ", then run the command again."


def retried(msg):
    """Login's warning text for ``msg``: it names the retry, not a rerun of login."""
    if AGAIN in msg:
        return msg.replace(AGAIN, f", then {RETRY}.")
    assert msg.endswith(".")
    return msg[:-1] + f"; {RETRY}."


def fail_for(monkeypatch, cli, exc):
    real = sg.install_tool

    def install_tool(g, *a, **k):
        if g.cli_name == cli:
            raise exc
        return real(g, *a, **k)

    monkeypatch.setattr(sg, "install_tool", install_tool)


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


class TestLoginSkillsThroughSharedInstaller:
    """B3: login installs through the path 'dg skills install' uses."""

    def test_login_writes_the_records_dg_skills_install_would(
        self, home, bundle, monkeypatch, capsys, tmp_path
    ):
        from deepctl_cmd_skills.command import SkillsCommand

        detect("claude", "cursor")
        out, err, _ = run_skills_step(monkeypatch, capsys)
        assert err == ""
        root = gen("claude").skills_root()
        assert f"✓ Claude Code → {root} (2 skills)" in out
        assert "Skills installed!" in out
        from_login = normalized(home)

        other = use_home(monkeypatch, tmp_path / "other")
        detect("claude", "cursor")
        SkillsCommand()._handle_install(install_all=True)
        assert normalized(other) == from_login
        assert set(from_login["skill_folders"]) == {"claude", "cursor"}

    @pytest.mark.parametrize("leftover", [False, True])
    def test_login_second_tool_failure_keeps_first_recorded_warns_once_exit_zero(
        self,
        home,
        bundle,
        monkeypatch,
        capsys,
        leftover,
        mock_config,
        mock_auth_manager,
        mock_client,
    ):
        detect("claude", "cursor")
        exc = sg._err("E5", gen("cursor"), reason="No space left on device")
        staging = gen("cursor").skills_root() / ".deepctl-staging-x"
        exc.leftover = staging if leftover else None
        fail_for(monkeypatch, "cursor", exc)
        ok = LoginResult(status="success", message="ok", profile="default")
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
        cmd = LoginCommand()
        cmd._guided = True
        capsys.readouterr()
        with (
            patch.object(cmd, "_web_auth", return_value=ok),
            patch.object(login_module.Prompt, "ask", return_value="all"),
        ):
            result = cmd.handle(
                config=mock_config, auth_manager=mock_auth_manager, client=mock_client
            )
        out, err = (click.unstyle(t) for t in capsys.readouterr())
        assert result.status == "success"
        warning = "WARN: Skills setup did not finish: " + retried(str(exc))
        e12 = "WARN: " + sg._msg("E12", staging=staging)
        assert err.splitlines() == ([e12] if leftover else []) + [warning]
        assert "did not finish" not in out
        assert f"✓ Claude Code → {gen('claude').skills_root()} (2 skills)" in out
        assert "Skills installed!" not in out  # As 'dg skills install': no summary.
        claude = sg.get_skills_state()["skill_folders"]["claude"]["folders"]
        assert {n: r["state"] for n, r in claude.items()} == {
            "api": "installed",
            "docs": "installed",
        }
        assert (gen("claude").skills_root() / "api" / "SKILL.md").is_file()
        assert "cursor" not in sg.get_skills_state()["skill_folders"]

    def test_login_uses_the_skills_ref_env_var_and_records_it(
        self, home, bundle, monkeypatch, capsys
    ):
        detect("claude")
        monkeypatch.setenv(skill_bundle.REF_ENV_VAR, " v9.9.9 ")
        _, err, _ = run_skills_step(monkeypatch, capsys)
        assert err == ""
        assert bundle == ["v9.9.9"]
        record = sg.get_skills_state()["skill_folders"]["claude"]
        assert record["skills_ref"] == "v9.9.9"

    def test_login_markup_in_home_path_prints_literally(
        self, tmp_path, bundle, monkeypatch, capsys
    ):
        monkeypatch.setattr(shutil, "which", lambda name: None)
        monkeypatch.delenv(skill_bundle.REF_ENV_VAR, raising=False)
        monkeypatch.setitem(output._output_config, "agentic", True)
        for con in (output.console, output.stderr_console, login_module.console):
            monkeypatch.setattr(con, "_width", 400)
        use_home(monkeypatch, tmp_path / "[dim]home")
        detect("claude")
        out, err, _ = run_skills_step(monkeypatch, capsys)
        root = gen("claude").skills_root()
        assert "[dim]home" in str(root)
        assert err == ""
        assert f"  ✓ Claude Code → {root} (2 skills)" in out.splitlines()

    def test_login_warning_is_on_stderr_outside_agentic_mode(
        self, home, bundle, monkeypatch, capsys
    ):
        output._output_config["agentic"] = False
        detect("claude")
        fail_for(monkeypatch, "claude", sg._err("E5", gen("claude"), reason="nope"))
        out, err, _ = run_skills_step(monkeypatch, capsys)
        assert "did not finish" not in out
        assert err.strip().startswith("⚠ Skills setup did not finish: ")
        assert err.strip().endswith(f"; {RETRY}.")

    def test_login_conflict_in_any_tool_writes_nothing(
        self, home, bundle, monkeypatch, capsys
    ):
        detect("claude", "cursor")
        mine = gen("cursor").skills_root() / "api"
        mine.mkdir(parents=True)
        (mine / "notes.md").write_bytes(b"mine")
        before = sha_tree(mine)
        out, err, _ = run_skills_step(monkeypatch, capsys)
        e1 = sg._msg("E1", paths=str(mine))
        assert err.splitlines() == [
            "WARN: Skills setup did not finish: " + e1.removesuffix(AGAIN) + ", then "
            "run 'dg skills install' to try again."
        ]
        assert not gen("claude").skills_root().exists()
        assert sha_tree(mine) == before
        assert not sg._STATE_FILE.exists()
        assert "Skills installed!" not in out

    def test_login_fetch_failure_warns_and_writes_nothing(
        self, home, monkeypatch, capsys
    ):
        detect("claude")

        def fetch(ref=None):
            raise SkillFetchError("Could not download the skills.")

        monkeypatch.setattr(skill_bundle, "fetch_skill_bundle", fetch)
        _, err, _ = run_skills_step(monkeypatch, capsys)
        assert err.splitlines() == [
            "WARN: Skills setup did not finish: Could not download the skills; "
            "run 'dg skills install' to try again."
        ]
        assert not gen("claude").skills_root().exists()
        assert not sg._STATE_FILE.exists()

    @pytest.mark.parametrize(("agentic", "prefix"), [(True, "WARN: "), (False, "⚠ ")])
    def test_login_hint_only_selection_warns_e15_on_stderr_installs_nothing(
        self, home, bundle, monkeypatch, capsys, agentic, prefix
    ):
        output._output_config["agentic"] = agentic
        detect("amazonq")
        out, err, _ = run_skills_step(monkeypatch, capsys)
        assert err.splitlines() == [prefix + sg._msg("E15", gen("amazonq"))]
        assert bundle == []
        assert not sg._STATE_FILE.exists()
        assert "Skills installed!" not in out

    def test_login_prompt_text_is_unchanged(self, home, bundle, monkeypatch, capsys):
        detect("claude", "cursor")
        out, _, ask = run_skills_step(monkeypatch, capsys, answer="none")
        ask.assert_called_once_with(
            "Install skills for (comma-separated numbers, [bold]all[/bold], or [bold]none[/bold])",
            default="all",
        )
        assert "AI coding tools detected:" in out
        assert "  1. Claude Code" in out
        assert "  2. Cursor" in out
        assert "You can run 'dg skills setup' later." in out
        assert bundle == []
        assert not sg._STATE_FILE.exists()

    def test_login_numbered_selection_installs_only_that_tool(
        self, home, bundle, monkeypatch, capsys
    ):
        detect("claude", "cursor")
        run_skills_step(monkeypatch, capsys, answer="2")
        assert set(sg.get_skills_state()["skill_folders"]) == {"cursor"}

    def test_login_does_not_prompt_when_skills_recorded(
        self, home, bundle, monkeypatch, capsys
    ):
        detect("claude")
        rule = Path.home() / ".amazonq" / "rules" / "deepctl.md"
        sg._STATE_FILE.parent.mkdir(parents=True)
        sg._STATE_FILE.write_text(
            json.dumps({"installed_skills": {"amazonq": {"paths": [str(rule)]}}})
        )
        saved = sg._STATE_FILE.read_bytes()
        out, err, ask = run_skills_step(monkeypatch, capsys)
        ask.assert_not_called()
        assert (out, err) == ("", "")
        assert sg._STATE_FILE.read_bytes() == saved

    def test_login_corrupt_skills_json_warns_once_and_does_not_prompt(
        self, home, bundle, monkeypatch, capsys
    ):
        detect("claude")
        sg._STATE_FILE.parent.mkdir(parents=True)
        sg._STATE_FILE.write_bytes(b"{not json")
        out, err, ask = run_skills_step(monkeypatch, capsys)
        ask.assert_not_called()
        assert err.splitlines() == [
            "WARN: Skills setup did not finish: " + retried(sg._msg("E7"))
        ]
        assert out == ""
        assert sg._STATE_FILE.read_bytes() == b"{not json"

    def test_login_ctrl_c_at_prompt_propagates_and_writes_nothing(
        self, home, bundle, monkeypatch
    ):
        detect("claude")
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
        cmd = LoginCommand()
        cmd._guided = True
        with (
            patch.object(login_module.Prompt, "ask", side_effect=KeyboardInterrupt),
            pytest.raises(KeyboardInterrupt),
        ):
            cmd._maybe_prompt_skills_setup()
        assert bundle == []
        assert not sg._STATE_FILE.exists()
        assert not gen("claude").skills_root().exists()

    def test_login_disk_error_mid_install_warns_and_exits_zero(
        self, home, bundle, monkeypatch, capsys
    ):
        detect("claude")

        def full(*a, **k):
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(sg, "_swap", full)
        _, err, _ = run_skills_step(monkeypatch, capsys)
        e5 = sg._msg("E5", gen("claude"), reason="No space left on device")
        assert err.splitlines()[-1] == f"WARN: Skills setup did not finish: {retried(e5)}"

    def test_login_leftover_staging_after_success_warns_on_stderr(
        self, home, bundle, monkeypatch, capsys
    ):
        output._output_config["agentic"] = False
        detect("claude")
        staging = gen("claude").skills_root() / ".deepctl-staging-x"
        real = sg.install_tool
        monkeypatch.setattr(
            sg, "install_tool", lambda *a, **k: (real(*a, **k)[0], staging)
        )
        out, err, _ = run_skills_step(monkeypatch, capsys)
        assert err.splitlines() == ["⚠ " + sg._msg("E12", staging=staging)]
        assert "Skills installed!" in out
