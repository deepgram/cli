"""Unit tests for plugin command."""

import contextlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner
from deepctl_cmd_plugin.command import PluginCommand
from deepctl_cmd_plugin.models import (
    PluginInstallOptions,
    PluginOperationResult,
)
from deepctl_cmd_update.installation import InstallMethod
from deepctl_core import output, skill_generator
from deepctl_core.auth import AuthManager
from deepctl_core.client import DeepgramClient
from deepctl_core.config import Config
from deepctl_core.skill_bundle import RepoSkill, SkillFetchError
from deepctl_core.skill_generator import AiderGenerator

# Captured before the autouse fixture below replaces the attribute, so the
# one class that does want to exercise it still can.
_REAL_MAYBE_UPDATE_SKILLS = PluginCommand._maybe_update_skills

#: Set by the autouse fixture below, so one test can prove it is in force.
_ACTIVE_THROWAWAY_HOME: Path | None = None


@pytest.fixture(autouse=True)
def _throwaway_home(tmp_path, monkeypatch):
    """Point every home-derived path in this module at a throwaway directory.

    Nothing here may read or write the home of whoever is running pytest.
    The skills refresh reads ``skills.json`` and the generators resolve
    ``Path.home()`` when called, while ``_SKILLS_DIR``, ``_STATE_FILE``,
    ``_REPO_CACHE_DIR`` and ``AiderGenerator._LEGACY_FILE`` are evaluated
    at import time, so each is pointed at the throwaway home by hand. The
    plugin paths the command itself holds are import-time constants too,
    so they follow. Same pattern as deepctl-core's test_skill_generator.py.
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

    import deepctl_cmd_plugin.command as plugin_command
    from deepctl_core import plugin_env

    plugin_dir = home / ".deepctl" / "plugins"
    for module in (plugin_env, plugin_command):
        monkeypatch.setattr(module, "PLUGIN_DIR", plugin_dir)
        monkeypatch.setattr(module, "PLUGIN_VENV", plugin_dir / "venv")
        monkeypatch.setattr(module, "PLUGIN_STATE_FILE", plugin_dir / "plugins.json")

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
    assert PluginCommand()._plugin_state_file.is_relative_to(home)


@pytest.fixture(autouse=True)
def _no_real_skill_installs():
    """Keep the skills refresh that follows a plugin operation off this machine.

    Every successful plugin install, update or uninstall calls
    ``_maybe_update_skills()``, which downloads the deepgram/skills bundle
    and reinstalls it into the real ``~/.claude/skills`` and friends,
    deleting ``~/.amazonq/rules/deepctl.md`` and rewriting
    ``~/.aider.conf.yml`` on the way. It swallows every exception, so a
    test suite doing that leaves no trace in its own output.
    """
    with patch.object(PluginCommand, "_maybe_update_skills", return_value=None):
        yield


class TestPluginCommand:
    """Test PluginCommand class."""

    def setup_method(self) -> None:
        """Set up test environment."""
        self.config = Config()
        self.auth_manager = MagicMock(spec=AuthManager)
        self.client = MagicMock(spec=DeepgramClient)
        self.command = PluginCommand()

    def test_init(self) -> None:
        """Test command initialization."""
        assert self.command.name == "plugin"
        assert self.command.help == "Manage deepctl plugins"
        assert self.command._plugin_dir == Path.home() / ".deepctl" / "plugins"
        assert (
            self.command._plugin_venv == Path.home() / ".deepctl" / "plugins" / "venv"
        )
        assert (
            self.command._plugin_state_file
            == Path.home() / ".deepctl" / "plugins" / "plugins.json"
        )

    @patch("deepctl_cmd_plugin.command.subprocess.run")
    def test_ensure_plugin_environment_creates_venv(self, mock_run: MagicMock) -> None:
        """Test that plugin environment is created when it doesn't exist."""
        # Mock that venv doesn't exist
        with (
            patch.object(Path, "exists", return_value=False),
            patch.object(Path, "mkdir"),
        ):
            mock_run.return_value.returncode = 0

            success, python_path = self.command._ensure_plugin_environment()

            assert success is True
            assert "python" in python_path
            # Should call: venv creation, pip upgrade, deepctl-core install
            assert mock_run.call_count == 3

    def test_ensure_plugin_environment_existing(self) -> None:
        """Test that existing plugin environment is used."""
        # Mock that venv exists
        with patch.object(Path, "exists", return_value=True):
            success, python_path = self.command._ensure_plugin_environment()

            assert success is True
            assert "python" in python_path

    def test_get_plugin_state_empty(self) -> None:
        """Test getting plugin state when file doesn't exist."""
        with patch.object(Path, "exists", return_value=False):
            state = self.command._get_plugin_state()
            assert state == {"plugins": {}}

    def test_get_plugin_state_existing(self) -> None:
        """Test getting plugin state from existing file."""
        test_state = {"plugins": {"test-plugin": {"version": "1.0.0"}}}

        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "read_text", return_value=json.dumps(test_state)),
        ):
            state = self.command._get_plugin_state()
            assert state == test_state

    def test_save_plugin_state(self) -> None:
        """Test saving plugin state."""
        test_state = {"plugins": {"test-plugin": {"version": "1.0.0"}}}

        with patch.object(Path, "write_text") as mock_write:
            self.command._save_plugin_state(test_state)
            mock_write.assert_called_once()
            written_data = json.loads(mock_write.call_args[0][0])
            assert written_data == test_state

    @patch("deepctl_cmd_plugin.strategies.subprocess.run")
    def test_install_plugin_pip_environment(self, mock_strategy_run: MagicMock) -> None:
        """Test installing plugin in pip environment."""
        # Mock pip installation detection
        with patch.object(self.command.detector, "detect") as mock_detect:
            mock_detect.return_value.method = InstallMethod.PIP
            mock_strategy_run.return_value = MagicMock(
                returncode=0, stdout="Successfully installed"
            )

            options = PluginInstallOptions(package="test-plugin")
            result = self.command.install_plugin(
                self.config, self.auth_manager, self.client, options
            )

            assert result.success is True
            assert "Successfully installed" in result.message
            # PipStrategy uses sys.executable
            cmd = mock_strategy_run.call_args[0][0]
            assert cmd[0] == self.command._python_executable

    @patch("deepctl_cmd_plugin.strategies.subprocess.run")
    def test_install_plugin_system_environment(
        self, mock_strategy_run: MagicMock
    ) -> None:
        """Test installing plugin in system environment (brew, apt, etc)."""
        # Mock system installation detection
        with patch.object(self.command.detector, "detect") as mock_detect:
            mock_detect.return_value.method = InstallMethod.SYSTEM

            # Mock plugin environment creation
            with patch.object(
                self.command, "_ensure_plugin_environment"
            ) as mock_ensure:
                mock_ensure.return_value = (True, "/path/to/plugin/python")
                mock_strategy_run.return_value = MagicMock(
                    returncode=0, stdout="Successfully installed"
                )

                # Mock state and version operations
                with (
                    patch.object(self.command, "_get_plugin_state") as mock_get_state,
                    patch.object(self.command, "_save_plugin_state") as mock_save_state,
                    patch.object(
                        self.command,
                        "_get_package_version",
                        return_value="1.0.0",
                    ),
                ):
                    mock_get_state.return_value = {"plugins": {}}

                    options = PluginInstallOptions(package="test-plugin")
                    result = self.command.install_plugin(
                        self.config,
                        self.auth_manager,
                        self.client,
                        options,
                    )

                    assert result.success is True
                    assert "Successfully installed" in result.message
                    # Strategy should use the plugin env python
                    cmd = mock_strategy_run.call_args[0][0]
                    assert cmd[0] == "/path/to/plugin/python"
                    # Should save plugin state
                    mock_save_state.assert_called_once()

    def test_install_plugin_git_url(self) -> None:
        """Test handling git URL in install options."""
        with patch.object(self.command.detector, "detect") as mock_detect:
            mock_detect.return_value.method = InstallMethod.PIP

            with patch(
                "deepctl_cmd_plugin.strategies.subprocess.run"
            ) as mock_strategy_run:
                mock_strategy_run.return_value = MagicMock(returncode=0, stdout="OK")

                # Mock _get_package_version to return a version string
                with patch.object(
                    self.command, "_get_package_version", return_value="1.0.0"
                ):
                    options = PluginInstallOptions(
                        package="test-plugin",
                        git_url="git+https://github.com/user/repo.git",
                    )
                    result = self.command.install_plugin(
                        self.config, self.auth_manager, self.client, options
                    )

                    # Should include git URL in pip command
                    pip_cmd = mock_strategy_run.call_args[0][0]
                    assert "git+https://github.com/user/repo.git" in pip_cmd
                    assert result.success is True

    @patch("deepctl_cmd_plugin.strategies.subprocess.run")
    def test_remove_plugin_success(self, mock_strategy_run: MagicMock) -> None:
        """Test successful plugin removal."""
        # Mock plugin discovery
        with patch.object(self.command, "_discover_plugins") as mock_discover:
            from deepctl_cmd_plugin.models import PluginPackage

            mock_discover.return_value = [
                PluginPackage(name="test-plugin", version="1.0.0", is_builtin=False)
            ]

            # Mock pip environment
            with patch.object(self.command.detector, "detect") as mock_detect:
                mock_detect.return_value.method = InstallMethod.PIP
                mock_strategy_run.return_value = MagicMock(
                    returncode=0, stdout="Removed"
                )

                result = self.command.remove_plugin(
                    self.config, self.auth_manager, self.client, "test-plugin"
                )

                assert result.success is True
                assert "Successfully removed" in result.message

    def test_remove_plugin_not_installed(self) -> None:
        """Test removing plugin that's not installed."""
        with patch.object(self.command, "_discover_plugins") as mock_discover:
            mock_discover.return_value = []

            result = self.command.remove_plugin(
                self.config, self.auth_manager, self.client, "test-plugin"
            )

            assert result.success is False
            assert "not installed" in result.message

    def test_remove_declined_aborts_instead_of_exiting_zero(self) -> None:
        """Declining the prompt must exit 2, not 0.

        `_handle_remove` is a group subcommand returning None, so there is no
        result for BaseCommand.EXIT_CODES to map to an exit code. A bare
        return made a declined removal indistinguishable from a successful
        one for any script branching on the exit code, contradicting the
        contract published in the README. Abort is what main.py turns into 2.
        """
        with (
            patch("click.confirm", return_value=False),
            patch.object(self.command, "remove_plugin") as mock_remove,
        ):
            with pytest.raises(click.Abort):
                self.command._handle_remove(
                    self.config,
                    self.auth_manager,
                    self.client,
                    package="test-plugin",
                )

            mock_remove.assert_not_called()

    def test_remove_with_yes_skips_the_prompt(self) -> None:
        """Positive control: --yes must not prompt and must not abort."""
        with (
            patch("click.confirm") as mock_confirm,
            patch.object(self.command, "remove_plugin") as mock_remove,
            patch.object(self.command, "_maybe_update_skills"),
        ):
            mock_remove.return_value = PluginOperationResult(
                success=True,
                action="remove",
                package="test-plugin",
                message="Successfully removed test-plugin",
            )

            self.command._handle_remove(
                self.config,
                self.auth_manager,
                self.client,
                package="test-plugin",
                yes=True,
            )

            mock_confirm.assert_not_called()
            mock_remove.assert_called_once()

    @patch("deepctl_cmd_plugin.command.subprocess.run")
    def test_discover_from_environment(self, mock_run: MagicMock) -> None:
        """Test discovering plugins from a specific environment."""
        mock_output = json.dumps(
            [
                {
                    "name": "test-plugin",
                    "version": "1.0.0",
                    "entry_point": "test=test_plugin:main",
                    "is_builtin": False,
                }
            ]
        )

        mock_run.return_value.stdout = mock_output
        mock_run.return_value.returncode = 0

        plugins = self.command._discover_from_environment("/path/to/python")

        assert len(plugins) == 1
        assert plugins[0].name == "test-plugin"
        assert plugins[0].version == "1.0.0"

    def test_handle_install_git_url_detection(self) -> None:
        """Test that _handle_install properly detects git URLs."""
        # Test various git URL formats
        test_cases = [
            ("git+https://github.com/user/repo.git", True),
            ("https://github.com/user/repo.git", True),
            ("http://github.com/user/repo.git", True),
            ("test-plugin", False),
            ("test-plugin==1.0.0", False),
        ]

        for package, should_be_git in test_cases:
            with patch.object(self.command, "install_plugin") as mock_install:
                mock_install.return_value = PluginOperationResult(
                    success=True,
                    action="install",
                    package="test",
                    message="Success",
                )

                kwargs = {"package": package}
                self.command._handle_install(
                    self.config, self.auth_manager, self.client, **kwargs
                )

                # Get PluginInstallOptions
                call_args = mock_install.call_args[0][3]
                if should_be_git:
                    assert call_args.git_url == package
                else:
                    assert call_args.git_url is None
                    assert call_args.package == package

    def test_list_plugins_verbose(self) -> None:
        """Test listing plugins in verbose mode."""
        from deepctl_cmd_plugin.models import PluginPackage

        test_plugins = [
            PluginPackage(
                name="test-plugin",
                version="1.0.0",
                entry_point="test=test_plugin:main",
                is_builtin=False,
            ),
            PluginPackage(
                name="deepctl-cmd-test",
                version="1.0.0",
                entry_point="test=deepctl_cmd_test:TestCommand",
                is_builtin=True,
            ),
        ]

        with (
            patch.object(self.command, "_discover_plugins", return_value=test_plugins),
            patch("deepctl_cmd_plugin.command.console.print") as mock_print,
            patch("deepctl_cmd_plugin.command.print_info") as mock_print_info,
            patch.object(self.command.detector, "detect") as mock_detect,
        ):
            mock_detect.return_value.method = InstallMethod.SYSTEM

            self.command.list_plugins(
                self.config,
                self.auth_manager,
                self.client,
                verbose=True,
            )

            # Should print a table
            mock_print.assert_called_once()
            # Should show system installation info via print_info
            assert any(
                "system" in str(call).lower() for call in mock_print_info.call_args_list
            )

    def test_setup_commands(self) -> None:
        """Test that all subcommands are properly set up."""
        commands = self.command.setup_commands()

        assert len(commands) == 5
        command_names = [cmd.name for cmd in commands]
        assert "install" in command_names
        assert "list" in command_names
        assert "update" in command_names
        assert "remove" in command_names
        assert "search" in command_names

    def test_handle_search(self) -> None:
        """Test the search command functionality."""
        # Mock the registry
        with patch.object(self.command, "_get_plugin_registry") as mock_registry:
            from deepctl_cmd_plugin.models import PluginRegistryEntry

            mock_registry.return_value = [
                PluginRegistryEntry(
                    name="test-plugin",
                    description="Test plugin",
                    version="1.0.0",
                    keywords=["test", "demo"],
                    install_name="test-plugin",
                ),
                PluginRegistryEntry(
                    name="another-plugin",
                    description="Another test plugin",
                    version="2.0.0",
                    keywords=["other"],
                    install_name="another-plugin",
                ),
            ]

            # Mock discover_plugins to simulate one installed
            with patch.object(self.command, "_discover_plugins") as mock_discover:
                from deepctl_cmd_plugin.models import PluginPackage

                mock_discover.return_value = [
                    PluginPackage(name="test-plugin", version="1.0.0", is_builtin=False)
                ]

                # Test search all
                with (
                    patch("deepctl_cmd_plugin.command.console.print") as mock_print,
                    patch("deepctl_cmd_plugin.command.print_info") as mock_info,
                ):
                    self.command._handle_search(
                        self.config, self.auth_manager, self.client
                    )

                    # Should print a table
                    mock_print.assert_called_once()
                    # Should show install hint
                    assert any(
                        "install" in str(call) for call in mock_info.call_args_list
                    )

                # Test search with query
                with patch("deepctl_cmd_plugin.command.console.print") as mock_print:
                    self.command._handle_search(
                        self.config,
                        self.auth_manager,
                        self.client,
                        query="test",
                    )

                    # Should print a table with filtered results
                    mock_print.assert_called_once()

                # Test search installed only
                with patch("deepctl_cmd_plugin.command.console.print") as mock_print:
                    self.command._handle_search(
                        self.config,
                        self.auth_manager,
                        self.client,
                        installed=True,
                    )

                    # Should print a table with only installed plugins
                    mock_print.assert_called_once()

    def test_get_plugin_registry(self) -> None:
        """Test that plugin registry returns hardcoded plugins."""
        registry = self.command._get_plugin_registry()

        assert len(registry) > 0
        assert any(p.name == "deepctl-plugin-example" for p in registry)
        assert all(hasattr(p, "description") for p in registry)
        assert all(hasattr(p, "version") for p in registry)

    def test_uses_shared_plugin_env_constants(self) -> None:
        """Test that PluginCommand uses shared constants from plugin_env."""
        from deepctl_core.plugin_env import PLUGIN_DIR, PLUGIN_STATE_FILE, PLUGIN_VENV

        assert self.command._plugin_dir == PLUGIN_DIR
        assert self.command._plugin_venv == PLUGIN_VENV
        assert self.command._plugin_state_file == PLUGIN_STATE_FILE

    @patch("deepctl_cmd_plugin.command.is_frozen", return_value=True)
    @patch("deepctl_cmd_plugin.command.find_system_python")
    @patch("deepctl_cmd_plugin.command.subprocess.run")
    def test_ensure_plugin_env_frozen_uses_system_python(
        self,
        mock_run: MagicMock,
        mock_find: MagicMock,
        mock_frozen: MagicMock,
    ) -> None:
        """Test that frozen binary uses find_system_python for venv creation."""
        mock_find.return_value = "/usr/bin/python3.11"
        mock_run.return_value.returncode = 0

        with (
            patch.object(Path, "exists", return_value=False),
            patch.object(Path, "mkdir"),
            patch(
                "deepctl_cmd_plugin.command.get_venv_python",
                return_value="/fake/venv/bin/python",
            ),
        ):
            success, _python_path = self.command._ensure_plugin_environment()

            assert success is True
            # Should use system python to create the venv
            venv_create_cmd = mock_run.call_args_list[0][0][0]
            assert venv_create_cmd[0] == "/usr/bin/python3.11"
            assert "-m" in venv_create_cmd
            assert "venv" in venv_create_cmd

    @patch("deepctl_cmd_plugin.command.is_frozen", return_value=True)
    @patch("deepctl_cmd_plugin.command.find_system_python", return_value=None)
    def test_ensure_plugin_env_frozen_no_python_fails(
        self,
        mock_find: MagicMock,
        mock_frozen: MagicMock,
    ) -> None:
        """Test that frozen binary fails gracefully when no system Python found."""
        with (
            patch.object(Path, "exists", return_value=False),
            patch.object(Path, "mkdir"),
        ):
            success, python_path = self.command._ensure_plugin_environment()

            assert success is False
            assert python_path == ""

    @patch("deepctl_cmd_plugin.command.subprocess.run")
    def test_install_core_into_venv(self, mock_run: MagicMock) -> None:
        """Test that _install_core_into_venv installs deepctl-core."""
        mock_run.return_value.returncode = 0

        self.command._install_core_into_venv("/path/to/venv/python")

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/path/to/venv/python"
        assert "pip" in " ".join(cmd)
        assert any("deepctl-core" in arg for arg in cmd)

    @patch("deepctl_cmd_plugin.command.subprocess.run")
    def test_install_plugin_uses_strategy(self, mock_run: MagicMock) -> None:
        """Test that install_plugin delegates to strategy."""
        with patch.object(self.command.detector, "detect") as mock_detect:
            mock_detect.return_value.method = InstallMethod.PIP
            mock_run.return_value = MagicMock(returncode=0, stdout="Installed")

            options = PluginInstallOptions(package="test-plugin")
            result = self.command.install_plugin(
                self.config, self.auth_manager, self.client, options
            )

            assert result.success is True

    @patch("deepctl_cmd_plugin.command.subprocess.run")
    def test_remove_plugin_uses_strategy(self, mock_run: MagicMock) -> None:
        """Test that remove_plugin delegates to strategy."""
        from deepctl_cmd_plugin.models import PluginPackage

        with patch.object(self.command, "_discover_plugins") as mock_discover:
            mock_discover.return_value = [
                PluginPackage(name="test-plugin", version="1.0.0", is_builtin=False)
            ]

            with patch.object(self.command.detector, "detect") as mock_detect:
                mock_detect.return_value.method = InstallMethod.PIP
                mock_run.return_value = MagicMock(returncode=0, stdout="Removed")

                result = self.command.remove_plugin(
                    self.config, self.auth_manager, self.client, "test-plugin"
                )

                assert result.success is True
                assert "Successfully removed" in result.message

    def test_needs_isolated_venv(self) -> None:
        """Test _needs_isolated_venv for various methods."""
        assert self.command._needs_isolated_venv(InstallMethod.HOMEBREW) is True
        assert self.command._needs_isolated_venv(InstallMethod.SYSTEM) is True
        assert self.command._needs_isolated_venv(InstallMethod.UNKNOWN) is True
        assert self.command._needs_isolated_venv(InstallMethod.PIP) is False
        assert self.command._needs_isolated_venv(InstallMethod.PIPX) is False
        assert self.command._needs_isolated_venv(InstallMethod.UV) is False


class TestSkillsRefreshAfterAPluginChange:
    """`_maybe_update_skills` writes the record `dg skills list` then reads."""

    def _generator(self, cli_name, root, paths):
        gen = MagicMock()
        gen.cli_name = cli_name
        gen.display_name = cli_name
        gen.skills_root.return_value = root
        gen.install_conflicts.return_value = []
        gen.install_skills.return_value = paths
        gen.prune_retired_result.return_value = skill_generator.PruneResult()
        return gen

    def _run(self, generators, state, skills=("api", "docs")):
        command = PluginCommand()
        bundle = [
            RepoSkill(name=name, path=Path("/upstream") / name) for name in skills
        ]
        with (
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch("deepctl_core.skill_generator.save_skills_state"),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata",
                return_value=[],
            ),
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=generators,
            ),
            patch(
                "deepctl_core.skill_generator.fetch_repo_skills", return_value=bundle
            ) as fetch,
        ):
            # The autouse fixture stubs this out for every other test here.
            _REAL_MAYBE_UPDATE_SKILLS(command)
        self.fetch = fetch
        return state

    def test_it_records_the_upstream_ref_and_skill_names(self, tmp_path):
        from deepctl_core.skill_bundle import DEFAULT_SKILLS_REF

        root = tmp_path / ".claude" / "skills"
        gen = self._generator("claude", root, [root / "api", root / "docs"])
        state = {
            "installed_skills": {"claude": {"paths": [], "skills": []}},
            "auto_update": True,
        }
        self._run([gen], state)

        entry = state["installed_skills"]["claude"]
        assert entry["skills_ref"] == DEFAULT_SKILLS_REF
        assert entry["skills"] == ["api", "docs"]

    def test_a_branch_install_stays_on_its_branch(self, tmp_path, monkeypatch):
        """README: an install from a branch stays there until you say otherwise.

        The refresh used to reinstall the pinned tag over a `main` install.
        """
        from deepctl_core.skill_bundle import REF_ENV_VAR

        monkeypatch.delenv(REF_ENV_VAR, raising=False)
        claude_root = tmp_path / ".claude" / "skills"
        cursor_root = tmp_path / ".cursor" / "skills"
        generators = [
            self._generator("claude", claude_root, [claude_root / "api"]),
            self._generator("cursor", cursor_root, [cursor_root / "api"]),
        ]
        state = {
            "installed_skills": {
                "claude": {"paths": [], "skills_ref": "main"},
                "cursor": {"paths": [], "skills_ref": "main"},
            },
            "auto_update": True,
        }
        self._run(generators, state, skills=("api",))

        assert self.fetch.call_args.args[0] == "main"
        for cli in ("claude", "cursor"):
            assert state["installed_skills"][cli]["skills_ref"] == "main"

    def test_recorded_refs_that_disagree_use_the_pin_and_warn(
        self, tmp_path, monkeypatch, capsys
    ):
        from deepctl_core.skill_bundle import DEFAULT_SKILLS_REF, REF_ENV_VAR

        monkeypatch.delenv(REF_ENV_VAR, raising=False)
        claude_root = tmp_path / ".claude" / "skills"
        cursor_root = tmp_path / ".cursor" / "skills"
        generators = [
            self._generator("claude", claude_root, [claude_root / "api"]),
            self._generator("cursor", cursor_root, [cursor_root / "api"]),
        ]
        state = {
            "installed_skills": {
                "claude": {"paths": [], "skills_ref": "main"},
                "cursor": {"paths": [], "skills_ref": "v1.0.0"},
            },
            "auto_update": True,
        }
        self._run(generators, state, skills=("api",))

        assert state["installed_skills"]["claude"]["skills_ref"] == DEFAULT_SKILLS_REF
        captured = capsys.readouterr()
        assert "more than one deepgram/skills ref" in captured.err
        assert "more than one" not in captured.out

    def test_the_environment_ref_still_wins_over_the_record(
        self, tmp_path, monkeypatch
    ):
        from deepctl_core.skill_bundle import REF_ENV_VAR

        monkeypatch.setenv(REF_ENV_VAR, "v9.9.9")
        root = tmp_path / ".claude" / "skills"
        gen = self._generator("claude", root, [root / "api"])
        state = {
            "installed_skills": {"claude": {"paths": [], "skills_ref": "main"}},
            "auto_update": True,
        }
        self._run([gen], state, skills=("api",))

        assert state["installed_skills"]["claude"]["skills_ref"] == "v9.9.9"

    def test_records_that_are_not_a_map_warn_on_stderr(self, capsys):
        """A list under installed_skills used to end the refresh in silence."""
        state = {"installed_skills": ["claude"], "auto_update": True}
        self._run([], state)

        captured = capsys.readouterr()
        said = " ".join(captured.err.split())
        assert "AI assistant skills not updated" in said
        assert "'installed_skills'" in said
        assert str(skill_generator._STATE_FILE) in said
        assert "dg skills install" in said
        assert "not updated" not in captured.out
        self.fetch.assert_not_called()

    def test_auto_update_off_skips_even_damaged_records(self, capsys):
        state = {"installed_skills": ["claude"], "auto_update": False}
        self._run([], state)

        captured = capsys.readouterr()
        assert captured.out + captured.err == ""

    def test_a_tool_with_no_skills_directory_is_retired_as_update_retires_it(
        self,
    ):
        """Nothing is installed for it, so nothing is fetched; as with
        `dg skills update`, its 0.3.x files are cleared and the record goes."""
        gen = self._generator("amazonq", None, [])
        state = {
            "installed_skills": {"amazonq": {"paths": []}},
            "auto_update": True,
        }
        self._run([gen], state)

        gen.install_skills.assert_not_called()
        gen.clean_legacy_report.assert_called_once()
        self.fetch.assert_not_called()
        assert "amazonq" not in state["installed_skills"]

    def test_only_the_tool_with_no_skills_directory_is_not_installed(self, tmp_path):
        """The three negatives above also hold if the refresh threw.

        `_maybe_update_skills` ends in a bare `except Exception: pass`,
        so "nothing happened" is what a crash on line one looks like
        too. A sibling that must be refreshed in the same run is the
        positive signal that the code reached the per-tool loop.
        """
        root = tmp_path / ".claude" / "skills"
        claude = self._generator("claude", root, [root / "api"])
        amazonq = self._generator("amazonq", None, [])
        state = {
            "installed_skills": {
                "claude": {"paths": [], "skills": []},
                "amazonq": {"paths": []},
            },
            "auto_update": True,
        }
        self._run([claude, amazonq], state, skills=("api",))

        claude.install_skills.assert_called_once()
        assert state["installed_skills"]["claude"]["skills"] == ["api"]
        amazonq.install_skills.assert_not_called()
        assert "amazonq" not in state["installed_skills"]

    def test_a_second_tool_failing_leaves_the_first_recorded(self, tmp_path, capsys):
        """The refresh used to save state only after the whole loop.

        A later failure reached the bare `except` with the earlier tool's
        new folders already written and nothing recording them.
        """
        claude_root = tmp_path / ".claude" / "skills"
        cursor_root = tmp_path / ".cursor" / "skills"
        first = self._generator("claude", claude_root, [claude_root / "api"])
        second = self._generator("cursor", cursor_root, [])
        second.install_skills.side_effect = OSError(30, "Read-only file system")
        state = {
            "installed_skills": {
                "claude": {"paths": [], "skills_ref": "old", "skills": []},
                "cursor": {"paths": [], "skills_ref": "old", "skills": []},
            },
            "auto_update": True,
        }

        self._run([first, second], state, skills=("api",))

        # And the user is told, on stderr, rather than the refresh going
        # quiet on it.
        captured = capsys.readouterr()
        said = " ".join(captured.err.split())
        assert (
            f"cursor skills not updated: could not write to {cursor_root}: "
            "Read-only file system. Fix its permissions and run "
            "'dg skills update' again." in said
        )
        assert "not updated" not in captured.out

        assert state["installed_skills"]["claude"]["skills"] == ["api"]
        # Nothing landed for the tool that failed, so its record still
        # describes the install that is actually on disk.
        assert state["installed_skills"]["cursor"]["skills_ref"] == "old"

    def test_the_bundle_is_fetched_once_for_every_tool(self, tmp_path):
        """Two fetches could install two different revisions side by side."""
        claude_root = tmp_path / ".claude" / "skills"
        cursor_root = tmp_path / ".cursor" / "skills"
        generators = [
            self._generator("claude", claude_root, [claude_root / "api"]),
            self._generator("cursor", cursor_root, [cursor_root / "api"]),
        ]
        state = {
            "installed_skills": {"claude": {"paths": []}, "cursor": {"paths": []}},
            "auto_update": True,
        }

        self._run(generators, state, skills=("api",))

        assert self.fetch.call_count == 1


class TestASkillsProblemNeverFailsThePluginOperation:
    """The refresh is best-effort, but best-effort is not silent.

    It ended in `except Exception: pass`, so a crash anywhere in it left
    the plugin installed, the skills stale, and nothing on the screen to
    say so.
    """

    def _install_with_real_refresh(self):
        """`dg plugin install` whose install succeeds and whose refresh runs."""
        command = PluginCommand()
        with (
            patch.object(
                PluginCommand, "_maybe_update_skills", _REAL_MAYBE_UPDATE_SKILLS
            ),
            patch.object(
                command,
                "install_plugin",
                return_value=PluginOperationResult(
                    success=True,
                    action="install",
                    package="deepctl-plugin-example",
                    message="Successfully installed deepctl-plugin-example",
                ),
            ),
        ):
            # No exception means the exit code is the install's: main.py
            # turns a raised ClickException into 1, a clean return into 0.
            command._handle_install(
                Config(),
                MagicMock(spec=AuthManager),
                MagicMock(spec=DeepgramClient),
                package="deepctl-plugin-example",
            )

    def test_a_crash_in_the_refresh_warns_and_the_install_still_succeeds(self, capsys):
        with patch(
            "deepctl_core.skill_generator.get_skills_state",
            side_effect=RuntimeError("records exploded"),
        ):
            self._install_with_real_refresh()

        captured = capsys.readouterr()
        said = " ".join((captured.out + captured.err).split())
        assert "Successfully installed deepctl-plugin-example" in said
        assert "AI assistant skills not updated: RuntimeError: records exploded" in said
        assert "Run 'dg skills install' to retry" in said

    def test_a_finished_remove_is_said_before_the_fetch_failure(self, capsys):
        """The fetch warning replaced the line, so the user never heard
        the remove they asked for had finished."""
        api = Path.home() / ".claude" / "skills" / "api"
        api.mkdir(parents=True)
        (api / "SKILL.md").write_text("---\nname: api\ndescription: x\n---\n")
        codex = Path.home() / ".codex" / "skills" / "api"
        state_file = Path(skill_generator._STATE_FILE)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps(
                {
                    "installed_skills": {
                        "claude": {
                            "paths": [str(api)],
                            "skills": ["api"],
                            "remove_pending": True,
                        },
                        "codex": {"paths": [str(codex)], "skills": ["api"]},
                    }
                }
            )
        )

        with (
            patch(
                "deepctl_core.skill_generator.collect_command_metadata",
                return_value=[],
            ),
            patch(
                "deepctl_core.skill_generator.fetch_repo_skills",
                side_effect=SkillFetchError("offline"),
            ),
        ):
            self._install_with_real_refresh()

        captured = capsys.readouterr()
        said = " ".join(captured.err.split())
        finished = (
            "Claude Code skills: finished the earlier 'dg skills remove', so "
            "none were reinstalled."
        )
        failed = "AI assistant skills not updated: offline"
        assert finished in said
        assert failed in said
        assert said.index(finished) < said.index(failed)
        assert "Successfully installed deepctl-plugin-example" in " ".join(
            (captured.out + captured.err).split()
        )
        assert not api.exists()
        records = json.loads(state_file.read_text())["installed_skills"]
        assert list(records) == ["codex"]

    def test_a_skills_file_core_cannot_read_names_the_file(self, capsys, monkeypatch):
        """Core raises SkillsStateError for a skills.json it cannot read."""
        with patch(
            "deepctl_core.skill_generator.get_skills_state",
            side_effect=skill_generator.SkillsStateError(
                "Expecting value: line 1 column 1"
            ),
        ):
            self._install_with_real_refresh()

        captured = capsys.readouterr()
        said = " ".join((captured.out + captured.err).split())
        assert "AI assistant skills not updated" in said
        assert str(skill_generator._STATE_FILE) in said
        assert "Expecting value" in said
        assert "dg skills install" in said

    def test_a_dev_checkout_without_package_metadata_still_refreshes(
        self, tmp_path, capsys
    ):
        """`importlib.metadata.version("deepctl")` raises in a dev install.

        That used to fall into the broad except and report a packaging
        detail as a skills failure, with nothing refreshed.
        """
        import importlib.metadata

        root = tmp_path / ".claude" / "skills"
        gen = MagicMock()
        gen.cli_name = "claude"
        gen.display_name = "claude"
        gen.skills_root.return_value = root
        gen.install_conflicts.return_value = []
        gen.install_skills.return_value = [root / "api"]
        gen.prune_retired_result.return_value = skill_generator.PruneResult()
        state = {
            "installed_skills": {"claude": {"paths": [], "skills": []}},
            "auto_update": True,
        }
        with (
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch("deepctl_core.skill_generator.save_skills_state"),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata",
                return_value=[],
            ),
            patch(
                "deepctl_core.skill_generator.get_all_generators", return_value=[gen]
            ),
            patch(
                "deepctl_core.skill_generator.fetch_repo_skills",
                return_value=[RepoSkill(name="api", path=Path("/upstream/api"))],
            ),
            patch(
                "deepctl_cmd_plugin.command.importlib.metadata.version",
                side_effect=importlib.metadata.PackageNotFoundError("deepctl"),
            ),
        ):
            _REAL_MAYBE_UPDATE_SKILLS(PluginCommand())

        gen.install_skills.assert_called_once()
        assert state["installed_skills"]["claude"]["version"] == "0.0.0"
        captured = capsys.readouterr()
        assert "skills not updated" not in captured.out + captured.err


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


class TestTheRefreshSavesUnderTheLock:
    """The refresh reads, installs and saves `skills.json` under the lock.

    Its first look at the records is unlocked, so a plugin operation with
    nothing installed holds nothing. The read the save is built on comes
    after the lock is taken; saving the first copy would overwrite
    whatever another deepctl recorded in between.
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

    def _run(self, gen):
        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators", return_value=[gen]
            ),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata",
                return_value=[],
            ),
            patch(
                "deepctl_core.skill_generator.fetch_repo_skills",
                return_value=[RepoSkill(name="api", path=Path("/upstream/api"))],
            ),
        ):
            _REAL_MAYBE_UPDATE_SKILLS(PluginCommand())

    @staticmethod
    def _write(state):
        state_file = Path(skill_generator._STATE_FILE)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state))
        return state_file

    def test_a_record_another_deepctl_wrote_meanwhile_survives(self, tmp_path):
        state_file = self._write(
            {"installed_skills": {"claude": {"paths": [], "skills": []}}}
        )
        other = {"paths": [], "skills": [], "version": "9.9.9"}
        real_read = skill_generator.get_skills_state
        reads = []

        def read_then_race():
            state = real_read()
            if not reads:
                # Between the unlocked first look and the lock, another
                # deepctl records an install of its own.
                on_disk = json.loads(state_file.read_text())
                on_disk["installed_skills"]["cursor"] = other
                state_file.write_text(json.dumps(on_disk))
            reads.append(state)
            return state

        with patch(
            "deepctl_core.skill_generator.get_skills_state", side_effect=read_then_race
        ):
            self._run(self._generator(tmp_path / ".claude" / "skills"))

        saved = json.loads(state_file.read_text())["installed_skills"]
        assert saved["cursor"] == other
        assert saved["claude"]["skills"] == ["api"]

    def test_a_tool_removed_during_the_download_is_not_reinstalled(
        self, tmp_path, capsys
    ):
        """`dg skills remove --cli cursor` while the refresh is downloading.

        The refresh picked its tools from the unlocked first look. Writing
        them all after the fetch brought cursor back: recorded again, with
        every folder reinstalled, moments after the user removed it.
        """
        state_file = self._write(
            {
                "installed_skills": {
                    "claude": {"paths": [], "skills": []},
                    "cursor": {"paths": [], "skills": []},
                }
            }
        )
        claude = self._generator(tmp_path / ".claude" / "skills")
        cursor = self._generator(tmp_path / ".cursor" / "skills")
        cursor.cli_name = "cursor"
        cursor.display_name = "Cursor"

        def fetch_while_cursor_is_removed(*_args, **_kwargs):
            # What `remove --cli cursor` does: take the lock, drop the
            # record, save.
            with skill_generator.skills_state_lock():
                state = skill_generator.get_skills_state()
                del state["installed_skills"]["cursor"]
                skill_generator.save_skills_state(state)
            return [RepoSkill(name="api", path=Path("/upstream/api"))]

        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[claude, cursor],
            ),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata",
                return_value=[],
            ),
            patch(
                "deepctl_core.skill_generator.fetch_repo_skills",
                side_effect=fetch_while_cursor_is_removed,
            ),
        ):
            _REAL_MAYBE_UPDATE_SKILLS(PluginCommand())

        saved = json.loads(state_file.read_text())["installed_skills"]
        assert set(saved) == {"claude"}
        cursor.install_skills.assert_not_called()
        claude.install_skills.assert_called_once()
        captured = capsys.readouterr()
        assert captured.out.count("removed while the update ran") == 0
        err = " ".join(captured.err.split())
        assert "cursor skills: removed while the update ran" in err
        assert "AI assistant skills not updated" not in err

    def test_records_held_by_another_process_warn_and_the_operation_goes_on(
        self, tmp_path, monkeypatch, capsys
    ):
        self._write({"installed_skills": {"claude": {"paths": [], "skills": []}}})
        gen = self._generator(tmp_path / ".claude" / "skills")
        with _records_lock_held_elsewhere(monkeypatch):
            # Returns rather than raising: the plugin command is not failed.
            self._run(gen)

        captured = capsys.readouterr()
        said = " ".join((captured.out + captured.err).split())
        assert "AI assistant skills not updated" in said
        assert "holds the skill records lock" in said
        # Core's message says to wait; the refresh adds only the command.
        assert said.count("ait for it to finish") == 1
        assert said.count("dg skills update") == 1
        assert "exits. Then run 'dg skills update'." in said
        # The file is fine, so the advice must not be to delete it.
        assert "Fix or delete" not in said
        gen.install_skills.assert_not_called()

    def test_a_legacy_file_left_in_place_is_named(self, tmp_path, capsys):
        from deepctl_core.skill_generator import SkillInstallReport

        self._write({"installed_skills": {"claude": {"paths": [], "skills": []}}})
        legacy = tmp_path / "CONVENTIONS.md"
        report = SkillInstallReport(
            ref="v1",
            skills=[],
            written={},
            unsupported=[],
            conflicts=[],
            failures=[],
        )
        report.legacy_skipped = [
            ("Aider", legacy, "it holds text deepctl did not write")
        ]
        with (
            patch(
                "deepctl_core.skill_generator.install_skills_for", return_value=report
            ),
            patch("deepctl_core.skill_generator.save_skills_state"),
        ):
            self._run(self._generator(tmp_path / ".claude" / "skills"))

        # Captured output, not the repr of mock calls: that doubles every
        # backslash in a Windows path, so the substring never matched there.
        captured = capsys.readouterr()
        said = " ".join(captured.err.split())
        assert (
            f"Aider: left {legacy} in place: it holds text deepctl did not write"
            in said
        )
        assert "left" not in captured.out

    def _report_run(self, tmp_path, **fields):
        from deepctl_core.skill_generator import SkillInstallReport

        self._write({"installed_skills": {"claude": {"paths": [], "skills": []}}})
        report = SkillInstallReport(
            ref="v1.8.0",
            skills=[],
            written={},
            unsupported=[],
            conflicts=[],
            failures=[],
        )
        for name, value in fields.items():
            setattr(report, name, value)
        with (
            patch(
                "deepctl_core.skill_generator.install_skills_for", return_value=report
            ),
            patch("deepctl_core.skill_generator.save_skills_state"),
        ):
            self._run(self._generator(tmp_path / ".claude" / "skills"))

    def test_a_retired_skill_the_refresh_deleted_is_named(self, tmp_path, capsys):
        root = tmp_path / ".claude" / "skills"
        self._report_run(
            tmp_path,
            pruned={"claude": [root / "self-hosted"]},
            pruned_tools={"claude": "Claude Code"},
        )

        captured = capsys.readouterr()
        assert (
            "Removed retired skill self-hosted from Claude Code "
            "(no longer in deepgram/skills@v1.8.0)" in " ".join(captured.err.split())
        )
        assert "retired" not in captured.out

    def test_a_pending_remove_is_retried_not_reinstalled(self, tmp_path, capsys):
        self._report_run(
            tmp_path,
            removals_finished=[("codex", "OpenAI Codex")],
            removals_pending=[("claude", "Claude Code")],
        )

        said = " ".join(capsys.readouterr().err.split())
        assert (
            "OpenAI Codex skills: finished the earlier 'dg skills remove', so "
            "none were reinstalled." in said
        )
        assert (
            "Claude Code skills: an earlier 'dg skills remove' has not finished, "
            "so none were reinstalled. Fix the permissions and run "
            "'dg skills remove --cli claude' again." in said
        )

    def test_the_lock_is_not_held_while_the_bundle_downloads(self, tmp_path):
        self._write({"installed_skills": {"claude": {"paths": [], "skills": []}}})
        held_during_fetch = []

        def fetch(*_args, **_kwargs):
            held_during_fetch.append(skill_generator._state_lock_depth)
            return [RepoSkill(name="api", path=Path("/upstream/api"))]

        gen = self._generator(tmp_path / ".claude" / "skills")
        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators", return_value=[gen]
            ),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata",
                return_value=[],
            ),
            patch("deepctl_core.skill_generator.fetch_repo_skills", side_effect=fetch),
        ):
            _REAL_MAYBE_UPDATE_SKILLS(PluginCommand())

        assert held_during_fetch == [0]
        gen.install_skills.assert_called_once()

    def test_records_damaged_during_the_download_are_not_replaced(
        self, tmp_path, capsys
    ):
        """The re-read under the lock refuses a non-map, and nothing is written."""
        state_file = self._write(
            {"installed_skills": {"claude": {"paths": [], "skills": []}}}
        )

        def fetch(*_args, **_kwargs):
            state_file.write_text(json.dumps({"installed_skills": ["claude"]}))
            return [RepoSkill(name="api", path=Path("/upstream/api"))]

        gen = self._generator(tmp_path / ".claude" / "skills")
        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators", return_value=[gen]
            ),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata",
                return_value=[],
            ),
            patch("deepctl_core.skill_generator.fetch_repo_skills", side_effect=fetch),
        ):
            _REAL_MAYBE_UPDATE_SKILLS(PluginCommand())

        gen.install_skills.assert_not_called()
        assert json.loads(state_file.read_text()) == {"installed_skills": ["claude"]}
        assert "AI assistant skills not updated" in capsys.readouterr().err

    def test_the_updated_line_goes_to_stderr(self, tmp_path, capsys):
        """`dg -o json plugin remove ... --yes` keeps stdout for the payload."""
        self._write({"installed_skills": {"claude": {"paths": [], "skills": []}}})
        self._run(self._generator(tmp_path / ".claude" / "skills"))

        captured = capsys.readouterr()
        assert captured.out == ""
        assert "AI assistant skills updated" in captured.err

    def test_an_invalid_environment_ref_names_the_variable_not_a_retry(
        self, tmp_path, monkeypatch, capsys
    ):
        """`dg skills update` reads the same DEEPCTL_SKILLS_REF, so "retry"
        failed the same way."""
        from deepctl_core.skill_bundle import REF_ENV_VAR

        monkeypatch.setenv(REF_ENV_VAR, "a..b")
        self._write({"installed_skills": {"claude": {"paths": [], "skills": []}}})
        gen = self._generator(tmp_path / ".claude" / "skills")
        self._run(gen)

        said = " ".join(capsys.readouterr().err.split())
        assert "AI assistant skills not updated: Invalid skills ref" in said
        assert (
            f"segment. That ref came from {REF_ENV_VAR}. Set it to another ref, "
            "or unset it to use the pinned release. Then run 'dg skills update'."
            in said
        )
        assert "to retry" not in said
        assert ".. " not in said.replace("'..'", "")
        gen.install_skills.assert_not_called()

    def test_a_recorded_ref_gone_upstream_advises_choosing_another(
        self, tmp_path, monkeypatch, capsys
    ):
        """`dg skills update` reuses the recorded ref, so "retry" failed again."""
        import urllib.error

        from deepctl_core import skill_bundle

        monkeypatch.delenv(skill_bundle.REF_ENV_VAR, raising=False)
        state_file = self._write(
            {
                "installed_skills": {
                    "claude": {"paths": [], "skills": [], "skills_ref": "gone-tag"}
                }
            }
        )
        not_found = urllib.error.HTTPError(
            "https://codeload.github.com/x", 404, "Not Found", {}, None
        )

        def fetch(ref=None, *, force=False, ref_source=None):
            return skill_bundle.fetch_skill_bundle(
                ref, cache_dir=tmp_path / "cache", force=force, ref_source=ref_source
            )

        gen = self._generator(tmp_path / ".claude" / "skills")
        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators", return_value=[gen]
            ),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata",
                return_value=[],
            ),
            patch("deepctl_core.skill_generator.fetch_repo_skills", side_effect=fetch),
            patch("urllib.request.urlopen", side_effect=not_found),
        ):
            _REAL_MAYBE_UPDATE_SKILLS(PluginCommand())

        said = " ".join(capsys.readouterr().err.split())
        assert "has no ref 'gone-tag'" in said
        assert (
            f"which came from the last install's record in {state_file}. "
            "Run 'dg skills update --ref <tag>' to choose another ref." in said
        )
        assert "to retry" not in said
        gen.install_skills.assert_not_called()

    def test_an_unreadable_skills_file_gives_its_fix_once(self, tmp_path, capsys):
        """Core's message carries the advice; the refresh used to add a second."""
        state_file = Path(skill_generator._STATE_FILE)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text("{not json")
        gen = self._generator(tmp_path / ".claude" / "skills")
        self._run(gen)

        said = " ".join(capsys.readouterr().err.split())
        assert "AI assistant skills not updated" in said
        assert str(state_file) in said
        assert said.count("dg skills install") == 1
        assert "Fix or delete" not in said
        gen.install_skills.assert_not_called()

    def test_a_collision_says_how_to_retry(self, tmp_path, capsys):
        """The README says every warning names the command that retries it."""
        self._write({"installed_skills": {"claude": {"paths": [], "skills": []}}})
        root = tmp_path / ".claude" / "skills"
        gen = self._generator(root)
        gen.install_conflicts.return_value = [root / "api"]
        self._run(gen)

        said = " ".join(capsys.readouterr().err.split())
        assert (
            f"Skipped Claude Code skills: {root / 'api'} is not deepctl's to "
            "replace. Move it aside, then run 'dg skills update'." in said
        )
        gen.install_skills.assert_not_called()

    def test_a_lock_path_that_is_a_directory_ends_its_sentence(self, tmp_path, capsys):
        """An OSError ends in a quoted path; the fix used to run straight on."""
        self._write({"installed_skills": {"claude": {"paths": [], "skills": []}}})
        skill_generator._lock_file().mkdir(parents=True, exist_ok=True)
        gen = self._generator(tmp_path / ".claude" / "skills")
        self._run(gen)

        said = " ".join(capsys.readouterr().err.split())
        assert "AI assistant skills not updated: Cannot open" in said
        # The OSError text differs by platform; the break before the fix
        # does not.
        assert "'. Fix or delete that file, then run 'dg skills install'." in said
        assert "' Fix or delete" not in said
        gen.install_skills.assert_not_called()


class TestTheRefreshUpgradesADeepctl03Install:
    """deepctl 0.3.0 and 0.3.1 recorded each install as its paths, a
    timestamp, the version and the commands hash, with no ``skills`` key.
    The refresh read that as a pending remove and deleted it."""

    def test_the_old_files_are_replaced_by_the_skill_folders(self, tmp_path, capsys):
        from deepctl_core.skill_generator import ClaudeCodeGenerator

        home = Path.home()
        legacy = home / ".claude" / "commands" / "deepgram"
        legacy.mkdir(parents=True)
        files = []
        for name in ("api", "docs", "setup-mcp", "starters"):
            path = legacy / f"{name}.md"
            path.write_text(f"---\nname: {name}\ndescription: x\n---\n")
            files.append(path)
        state_file = Path(skill_generator._STATE_FILE)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        # Exactly what `dg skills install` and `dg login` wrote in 0.3.x.
        state_file.write_text(
            json.dumps(
                {
                    "installed_skills": {
                        "claude": {
                            "paths": [str(p) for p in files],
                            "installed_at": "2026-05-01T12:00:00.000000+00:00",
                            "version": "0.3.1",
                            "commands_hash": "0123456789abcdef",
                        }
                    }
                }
            )
        )
        names = [f"skill-{i:02d}" for i in range(14)]
        bundle = []
        for name in names:
            folder = tmp_path / "bundle" / name
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
            bundle.append(RepoSkill(name=name, path=folder))

        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[ClaudeCodeGenerator()],
            ),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata",
                return_value=[],
            ),
            patch(
                "deepctl_core.skill_generator.fetch_repo_skills", return_value=bundle
            ),
        ):
            _REAL_MAYBE_UPDATE_SKILLS(PluginCommand())

        root = home / ".claude" / "skills"
        assert sorted(p.name for p in root.iterdir()) == names
        assert not legacy.exists()
        said = " ".join(capsys.readouterr().err.split())
        assert "AI assistant skills updated" in said
        assert "finished the earlier" not in said
        entry = json.loads(state_file.read_text())["installed_skills"]["claude"]
        assert entry["skills"] == names
        assert "remove_pending" not in entry

    def test_amazonq_and_aider_0_3_files_are_cleared_too(self, capsys):
        """README: the refresh clears 0.3.x files for every recorded tool.

        It only handed on tools with a skills directory, so Amazon Q's and
        Aider's files stayed until a `dg skills update`.
        """
        from deepctl_core.skill_generator import AiderGenerator, AmazonQGenerator

        home = Path.home()
        guide = "# Deepgram CLI Reference\n\n> Auto-generated by deepctl v0.3.1\n"
        amazonq = home / ".amazonq" / "rules" / "deepctl.md"
        amazonq.parent.mkdir(parents=True)
        amazonq.write_text(guide)
        conventions = AiderGenerator._LEGACY_FILE
        conventions.parent.mkdir(parents=True, exist_ok=True)
        conventions.write_text(guide)
        conf = home / ".aider.conf.yml"
        conf.write_text(f"model: gpt-4o\nread:\n  - {conventions}\n")
        state_file = Path(skill_generator._STATE_FILE)

        def v03(paths):
            return {
                "paths": [str(p) for p in paths],
                "installed_at": "2026-05-01T12:00:00.000000+00:00",
                "version": "0.3.1",
                "commands_hash": "0123456789abcdef",
            }

        state_file.write_text(
            json.dumps(
                {
                    "installed_skills": {
                        "amazonq": v03([amazonq]),
                        "aider": v03([conventions]),
                    }
                }
            )
        )

        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[AmazonQGenerator(), AiderGenerator()],
            ),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata",
                return_value=[],
            ),
            patch("deepctl_core.skill_generator.fetch_repo_skills") as fetch,
        ):
            _REAL_MAYBE_UPDATE_SKILLS(PluginCommand())

        fetch.assert_not_called()
        assert not amazonq.exists()
        assert not conventions.exists()
        assert conf.read_text() == "model: gpt-4o\n"
        assert json.loads(state_file.read_text())["installed_skills"] == {}
        assert "left" not in capsys.readouterr().err
