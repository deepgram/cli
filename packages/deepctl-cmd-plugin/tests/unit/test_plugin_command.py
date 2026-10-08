"""Unit tests for plugin command."""

import ast
import hashlib
import inspect
import json
import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner
from deepctl_cmd_plugin import command as plugin_module
from deepctl_cmd_plugin.command import PluginCommand
from deepctl_cmd_plugin.models import (
    PluginInstallOptions,
    PluginOperationResult,
)
from deepctl_cmd_update.installation import InstallMethod
from deepctl_core import output, skill_bundle
from deepctl_core import skill_generator as sg
from deepctl_core.auth import AuthManager
from deepctl_core.client import DeepgramClient
from deepctl_core.config import Config
from deepctl_core.skill_bundle import RepoSkill, SkillFetchError

REF = skill_bundle.DEFAULT_SKILLS_COMMIT
RETRY = "run 'dg skills update' to try again"
AGAIN = ", then run the command again."


def retried(msg):
    """The refresh's warning for ``msg``: it names the retry, not a plugin rerun."""
    if AGAIN in msg:
        return msg.replace(AGAIN, f", then {RETRY}.")
    assert msg.endswith(".")
    return msg[:-1] + f"; {RETRY}."


def invoke_install(cmd=None):
    """Run ``dg plugin install p`` through click with the pip step stubbed out."""
    cmd = cmd or PluginCommand()
    ok = PluginOperationResult(
        success=True, action="install", package="p", message="Installed p"
    )
    group = click.Group("plugin", commands=cmd.setup_commands())
    obj = {"config": MagicMock(), "auth_manager": MagicMock(), "client": MagicMock()}
    with patch.object(cmd, "install_plugin", return_value=ok):
        return CliRunner().invoke(group, ["install", "p"], obj=obj)


@pytest.fixture(autouse=True)
def _no_real_skills_state(tmp_path, monkeypatch):
    """No test here reads or writes the developer's real skills.json (T19).

    Handler tests that succeed run the real skills refresh; with no records
    under this throwaway path it returns before any fetch.
    """
    skills_dir = tmp_path / "deepctl-skills"
    monkeypatch.setattr(sg, "_SKILLS_DIR", skills_dir)
    monkeypatch.setattr(sg, "_STATE_FILE", skills_dir / "skills.json")


def use_home(monkeypatch, home):
    """Point every home lookup the skills refresh makes at ``home``."""
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
    for con in (output.console, output.stderr_console, plugin_module.console):
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


def gen(cli):
    return next(g for g in sg.get_all_generators() if g.cli_name == cli)


def write_state(state):
    sg._STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    sg._STATE_FILE.write_text(json.dumps(state), encoding="utf-8")


def state_bytes():
    try:
        return sg._STATE_FILE.read_bytes()
    except FileNotFoundError:
        return None


def normalized(home):
    """skills.json with paths rebased on ``home`` and timestamps dropped."""
    text = sg._STATE_FILE.read_text(encoding="utf-8")
    state = json.loads(text.replace(json.dumps(str(home))[1:-1], "~"))
    for section in ("skill_folders", "installed_skills"):
        for tool in state.get(section, {}).values():
            tool.pop("installed_at", None)
    return state


def sha_tree(path):
    path = Path(path)
    if path.is_symlink():
        return {"": "link:" + os.readlink(path)}
    return {
        str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(path.rglob("*"))
        if p.is_file()
    }


def staging_dirs(where):
    if not where.is_dir():
        return []
    return [n for n in os.listdir(where) if n.startswith(sg._STAGING_PREFIX)]


def bundle_for(where, ref):
    """A separate bundle whose SKILL.md files name ``ref``."""
    skills = []
    for name in ("api", "docs"):
        folder = where.parent / f"bundle-{ref}" / "skills" / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_bytes(f"---\nname: {name}\n---\n{ref}\n".encode())
        skills.append(RepoSkill(name, folder))
    return skills


def fail_for(monkeypatch, cli, exc):
    real = sg.install_tool

    def install_tool(g, *a, **k):
        if g.cli_name == cli:
            raise exc
        return real(g, *a, **k)

    monkeypatch.setattr(sg, "install_tool", install_tool)


def refresh(capsys):
    capsys.readouterr()
    PluginCommand()._maybe_update_skills()
    return capsys.readouterr()


def warnings(err):
    return [line for line in err.splitlines() if line.startswith(("WARN:", "⚠"))]


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
            self.command._plugin_venv
            == Path.home() / ".deepctl" / "plugins" / "venv"
        )
        assert (
            self.command._plugin_state_file
            == Path.home() / ".deepctl" / "plugins" / "plugins.json"
        )

    @patch("deepctl_cmd_plugin.command.subprocess.run")
    def test_ensure_plugin_environment_creates_venv(
        self, mock_run: MagicMock
    ) -> None:
        """Test that plugin environment is created when it doesn't exist."""
        # Mock that venv doesn't exist
        with patch.object(Path, "exists", return_value=False):
            with patch.object(Path, "mkdir"):
                mock_run.return_value.returncode = 0

                success, python_path = (
                    self.command._ensure_plugin_environment()
                )

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

        with patch.object(Path, "exists", return_value=True), patch.object(
            Path, "read_text", return_value=json.dumps(test_state)
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
    def test_install_plugin_pip_environment(
        self, mock_strategy_run: MagicMock
    ) -> None:
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
                with patch.object(
                    self.command, "_get_plugin_state"
                ) as mock_get_state, patch.object(
                    self.command, "_save_plugin_state"
                ) as mock_save_state, patch.object(
                    self.command,
                    "_get_package_version",
                    return_value="1.0.0",
                ):
                    mock_get_state.return_value = {"plugins": {}}

                    options = PluginInstallOptions(
                        package="test-plugin"
                    )
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
                mock_strategy_run.return_value = MagicMock(
                    returncode=0, stdout="OK"
                )

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
                PluginPackage(
                    name="test-plugin", version="1.0.0", is_builtin=False
                )
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
        # Create a mock context
        mock_ctx = MagicMock()

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

        with patch.object(
            self.command, "_discover_plugins", return_value=test_plugins
        ), patch(
            "deepctl_cmd_plugin.command.console.print"
        ) as mock_print, patch(
            "deepctl_cmd_plugin.command.print_info"
        ) as mock_print_info, patch.object(
            self.command.detector, "detect"
        ) as mock_detect:
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
                "system" in str(call).lower()
                for call in mock_print_info.call_args_list
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
        with patch.object(
            self.command, "_get_plugin_registry"
        ) as mock_registry:
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
            with patch.object(
                self.command, "_discover_plugins"
            ) as mock_discover:
                from deepctl_cmd_plugin.models import PluginPackage

                mock_discover.return_value = [
                    PluginPackage(
                        name="test-plugin", version="1.0.0", is_builtin=False
                    )
                ]

                # Test search all
                with patch(
                    "deepctl_cmd_plugin.command.console.print"
                ) as mock_print, patch(
                    "deepctl_cmd_plugin.command.print_info"
                ) as mock_info:
                    self.command._handle_search(
                        self.config, self.auth_manager, self.client
                    )

                    # Should print a table
                    mock_print.assert_called_once()
                    # Should show install hint
                    assert any(
                        "install" in str(call)
                        for call in mock_info.call_args_list
                    )

                # Test search with query
                with patch(
                    "deepctl_cmd_plugin.command.console.print"
                ) as mock_print:
                    self.command._handle_search(
                        self.config,
                        self.auth_manager,
                        self.client,
                        query="test",
                    )

                    # Should print a table with filtered results
                    mock_print.assert_called_once()

                # Test search installed only
                with patch(
                    "deepctl_cmd_plugin.command.console.print"
                ) as mock_print:
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

        with patch.object(Path, "exists", return_value=False):
            with patch.object(Path, "mkdir"):
                with patch(
                    "deepctl_cmd_plugin.command.get_venv_python",
                    return_value="/fake/venv/bin/python",
                ):
                    success, python_path = (
                        self.command._ensure_plugin_environment()
                    )

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
        with patch.object(Path, "exists", return_value=False):
            with patch.object(Path, "mkdir"):
                success, python_path = (
                    self.command._ensure_plugin_environment()
                )

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
            mock_run.return_value = MagicMock(
                returncode=0, stdout="Installed"
            )

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
                PluginPackage(
                    name="test-plugin", version="1.0.0", is_builtin=False
                )
            ]

            with patch.object(self.command.detector, "detect") as mock_detect:
                mock_detect.return_value.method = InstallMethod.PIP
                mock_run.return_value = MagicMock(
                    returncode=0, stdout="Removed"
                )

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


class TestSkillsRefresh:
    """B3: the plugin refresh installs through the path 'dg skills update' uses."""

    def test_refresh_writes_the_records_dg_skills_update_would(
        self, home, bundle, monkeypatch, capsys, tmp_path
    ):
        from deepctl_cmd_skills.command import SkillsCommand

        homes = [home, use_home(monkeypatch, tmp_path / "other")]
        for h in homes:
            use_home(monkeypatch, h)
            for cli in ("claude", "cursor"):
                h.joinpath(*gen(cli).homes[0]).mkdir(parents=True)
            SkillsCommand()._handle_install(install_all=True)
        use_home(monkeypatch, homes[0])
        out, err = refresh(capsys)
        assert (out, err) == ("", "")
        use_home(monkeypatch, homes[1])
        SkillsCommand()._handle_update()
        after_update = normalized(homes[1])
        use_home(monkeypatch, homes[0])
        assert normalized(homes[0]) == after_update
        assert set(after_update["skill_folders"]) == {"claude", "cursor"}

    @pytest.mark.parametrize("quiet", [False, True])
    def test_successful_refresh_keeps_skills_off_stdout_and_quiet_silent(
        self, home, bundle, quiet
    ):
        output._output_config.update(agentic=False, quiet=quiet)
        write_state({"installed_skills": {"claude": {"paths": []}}})
        result = invoke_install()
        assert result.exit_code == 0, result.output
        assert bundle == [REF]
        assert "skills" not in result.stdout.lower()
        assert "skills" not in result.stderr.lower()
        if quiet:
            assert (result.stdout, result.stderr) == ("", "")

    @pytest.mark.parametrize("agentic", [False, True])
    def test_refresh_second_tool_failure_keeps_first_recorded_warns_once_exit_zero(
        self, home, bundle, monkeypatch, agentic
    ):
        output._output_config["agentic"] = agentic
        write_state(
            {"installed_skills": {"claude": {"paths": []}, "cursor": {"paths": []}}}
        )
        exc = sg._err("E5", gen("cursor"), reason="No space left on device")
        fail_for(monkeypatch, "cursor", exc)
        result = invoke_install()
        assert result.exit_code == 0, result.output
        assert "not updated" not in result.stdout
        assert len(warnings(result.stderr)) == 1
        assert warnings(result.stderr)[0].endswith(
            "AI assistant skills were not updated: " + retried(str(exc))
        )
        claude = sg.get_skills_state()["skill_folders"]["claude"]["folders"]
        assert {n: r["state"] for n, r in claude.items()} == {
            "api": "installed",
            "docs": "installed",
        }
        assert "cursor" not in sg.get_skills_state()["skill_folders"]

    def test_refresh_keeps_03x_and_hint_only_records_byte_identical(
        self, home, bundle, capsys
    ):
        old = Path.home() / ".claude" / "commands" / "deepgram" / "api.md"
        rule = Path.home() / ".amazonq" / "rules" / "deepctl.md"
        conf = Path.home() / ".aider.conf.yml"
        legacy = {
            "claude": {"paths": [str(old)], "version": "0.3.0", "commands_hash": "h"},
            "amazonq": {"paths": [str(rule)], "version": "0.3.0"},
            "aider": {"paths": [str(conf)], "commands_hash": "h"},
        }
        write_state({"installed_skills": legacy})
        _, err = refresh(capsys)
        assert warnings(err) == []
        state = sg.get_skills_state()
        assert state["installed_skills"] == legacy
        assert set(state["skill_folders"]) == {"claude"}
        assert state["skill_folders"]["claude"]["v03"] is True
        assert (gen("claude").skills_root() / "api" / "SKILL.md").is_file()
        assert bundle == [REF]

    def _install_then_overlap(self, monkeypatch, action):
        list(sg.install_for([(gen("claude"), REF), (gen("cursor"), REF)]))
        real, pending = skill_bundle.fetch_skill_bundle, [action]

        def fetch(ref=None):
            if pending:
                pending.pop()()
            return real(ref)

        monkeypatch.setattr(skill_bundle, "fetch_skill_bundle", fetch)

    def test_refresh_skips_a_tool_removed_during_its_download(
        self, home, bundle, monkeypatch, capsys
    ):
        self._install_then_overlap(monkeypatch, lambda: sg.remove_tool(gen("claude")))
        out, err = refresh(capsys)
        assert out == ""
        assert warnings(err) == ["WARN: " + sg._msg("E31", gen("claude"))]
        state = sg.get_skills_state()
        assert "claude" not in state["skill_folders"]
        assert "claude" not in state["installed_skills"]
        assert not (gen("claude").skills_root() / "api").exists()
        assert set(state["skill_folders"]["cursor"]["folders"]) == {"api", "docs"}

    def test_refresh_keeps_an_explicit_ref_set_during_its_download(
        self, home, bundle, monkeypatch
    ):
        skills = bundle_for(home, "newer")
        self._install_then_overlap(
            monkeypatch,
            lambda: sg.install_tool(gen("claude"), skills, ref="newer", version="9"),
        )
        result = invoke_install()
        assert result.exit_code == 0, result.output
        assert warnings(result.stderr) == ["WARN: " + sg._msg("E30", gen("claude"))]
        assert sg.get_skills_state()["skill_folders"]["claude"]["skills_ref"] == "newer"
        assert sg.get_skills_state()["skill_folders"]["cursor"]["skills_ref"] == REF

    def test_refresh_keeps_a_newer_copy_of_the_same_moving_ref(
        self, home, bundle, monkeypatch, capsys
    ):
        """B1 (review): REF moved; the newer download landed first and stays."""
        newer = bundle_for(home, "newer")
        self._install_then_overlap(
            monkeypatch,
            lambda: sg.install_tool(gen("claude"), newer, ref=REF, version="9"),
        )
        out, err = refresh(capsys)
        assert out == ""
        assert warnings(err) == ["WARN: " + sg._msg("E32", gen("claude"))]
        api = gen("claude").skills_root() / "api" / "SKILL.md"
        assert api.read_bytes() == (newer[0].path / "SKILL.md").read_bytes()
        state = sg.get_skills_state()["skill_folders"]
        assert (state["claude"]["skills_ref"], state["claude"]["version"]) == (REF, "9")
        assert state["cursor"]["version"] != "9"

    @pytest.mark.parametrize(
        "state",
        [
            None,
            {"installed_skills": {}},
            {"installed_skills": {"claude": {"paths": []}}, "auto_update": False},
            {"installed_skills": {"amazonq": {"paths": []}, "aider": {"paths": []}}},
        ],
        ids=["no-file", "empty", "auto-update-off", "hint-only"],
    )
    def test_refresh_noops_without_fetch(self, home, bundle, capsys, state):
        if state is not None:
            write_state(state)
        saved = state_bytes()
        out, err = refresh(capsys)
        assert bundle == []
        assert state_bytes() == saved
        assert (out, err) == ("", "")
        assert not gen("claude").skills_root().exists()

    @pytest.mark.parametrize("agentic", [False, True])
    def test_refresh_fetch_failure_warns_once_exit_zero_keeps_records(
        self, home, monkeypatch, agentic
    ):
        output._output_config["agentic"] = agentic
        write_state({"installed_skills": {"claude": {"paths": []}}})
        saved = state_bytes()

        def fetch(ref=None):
            raise SkillFetchError("Could not download the skills.")

        monkeypatch.setattr(skill_bundle, "fetch_skill_bundle", fetch)
        result = invoke_install()
        assert result.exit_code == 0, result.output
        assert "not updated" not in result.stdout
        assert "skills updated" not in result.stdout
        assert warnings(result.stderr) == [
            ("WARN: " if agentic else "⚠ ")
            + "AI assistant skills were not updated: Could not download the skills; "
            "run 'dg skills update' to try again."
        ]
        assert state_bytes() == saved
        assert not gen("claude").skills_root().exists()

    def test_refresh_invalid_recorded_ref_warns_once_exit_zero_keeps_records(
        self, home, bundle
    ):
        list(sg.install_for([(gen("claude"), REF)]))
        state = json.loads(state_bytes())
        state["skill_folders"]["claude"]["skills_ref"] = "../evil"
        write_state(state)
        saved, tree = state_bytes(), sha_tree(gen("claude").skills_root())
        result = invoke_install()
        assert result.exit_code == 0, result.output
        assert "updated" not in result.stdout
        [line] = warnings(result.stderr)
        assert line.startswith("WARN: AI assistant skills were not updated: ")
        assert "'../evil'" in line
        assert line.endswith(f"; {RETRY}.")
        assert bundle == [REF]
        assert state_bytes() == saved
        assert sha_tree(gen("claude").skills_root()) == tree

    def test_refresh_follows_recorded_ref_and_env_wins(
        self, home, bundle, capsys, monkeypatch
    ):
        list(sg.install_for([(gen("claude"), "my-branch")]))
        refresh(capsys)
        assert bundle == ["my-branch", "my-branch"]
        assert sg.get_skills_state()["skill_folders"]["claude"]["skills_ref"] == (
            "my-branch"
        )
        monkeypatch.setenv(skill_bundle.REF_ENV_VAR, "env-ref")
        refresh(capsys)
        monkeypatch.delenv(skill_bundle.REF_ENV_VAR)
        assert bundle[-1] == "env-ref"
        write_state({"installed_skills": {"cursor": {"paths": []}}})
        refresh(capsys)
        assert bundle[-1] == REF

    @pytest.mark.parametrize("kind", ["dir", "symlink", "dangling-symlink"])
    def test_refresh_conflict_refuses_every_tool(
        self, home, bundle, capsys, tmp_path, kind
    ):
        list(sg.install_for([(gen("claude"), REF)]))
        write_state(
            {**json.loads(state_bytes()), "installed_skills": {"cursor": {"paths": []}}}
        )
        saved, claude = state_bytes(), sha_tree(gen("claude").skills_root())
        dest = gen("cursor").skills_root() / "api"
        dest.parent.mkdir(parents=True)
        mine = tmp_path / "mine"
        mine.mkdir()
        (mine / "notes.md").write_bytes(b"mine")
        if kind == "dir":
            shutil.copytree(mine, dest)
        else:
            target = mine if kind == "symlink" else tmp_path / "gone"
            try:
                dest.symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                pytest.skip(f"cannot create a symlink here: {exc}")
        before = sha_tree(dest)
        out, err = refresh(capsys)
        e1 = sg._msg("E1", paths=str(dest))
        assert warnings(err) == [
            f"WARN: AI assistant skills were not updated: {e1.removesuffix(AGAIN)}, "
            "then run 'dg skills update' to try again."
        ]
        assert "updated" not in out
        assert sha_tree(dest) == before
        assert sha_tree(mine) == {"notes.md": hashlib.sha256(b"mine").hexdigest()}
        assert state_bytes() == saved
        assert sha_tree(gen("claude").skills_root()) == claude
        assert bundle == [REF, REF]

    def test_refresh_edited_folder_warns_e22_and_leaves_it(self, home, bundle, capsys):
        list(sg.install_for([(gen("claude"), REF)]))
        api = gen("claude").skills_root() / "api"
        with open(api / "SKILL.md", "ab") as f:
            f.write(b"mine\n")
        before, saved = sha_tree(api), state_bytes()
        _, err = refresh(capsys)
        e22 = sg._msg("E22", dest=api)
        assert warnings(err) == [
            f"WARN: AI assistant skills were not updated: {retried(e22)}"
        ]
        assert sha_tree(api) == before
        assert state_bytes() == saved

    def test_refresh_multi_problem_warning_names_the_retry_in_every_sentence(
        self, home, bundle, monkeypatch, capsys
    ):
        monkeypatch.setattr(output.stderr_console, "_width", 2000)
        write_state({"installed_skills": {"claude": {"paths": []}}})
        p1, p2, p3 = (home / n for n in ("a", "b", "c"))
        fail_for(monkeypatch, "claude", sg.SkillOwnershipError([p1], [p2, p3]))
        _, err = refresh(capsys)
        [line] = warnings(err)
        assert "run the command again" not in line
        assert line.count(f", then {RETRY}.") == 3
        assert line.endswith(f", then {RETRY}.")

    def test_refresh_corrupt_skills_json_warns_and_keeps_bytes(
        self, home, bundle, capsys
    ):
        sg._STATE_FILE.parent.mkdir(parents=True)
        sg._STATE_FILE.write_bytes(b'{"installed_skills": []}')
        out, err = refresh(capsys)
        e7 = sg._msg("E7")
        assert warnings(err) == [
            f"WARN: AI assistant skills were not updated: {retried(e7)}"
        ]
        assert out == ""
        assert state_bytes() == b'{"installed_skills": []}'
        assert bundle == []

    @pytest.mark.parametrize("handler", ["install", "update", "remove"])
    @pytest.mark.parametrize("success", [True, False])
    def test_refresh_runs_after_install_update_remove_only_on_success(
        self, handler, success
    ):
        cmd = PluginCommand()
        result = PluginOperationResult(
            success=success, action=handler, package="p", message="m"
        )
        method = "remove_plugin" if handler == "remove" else "install_plugin"
        with (
            patch.object(cmd, method, return_value=result),
            patch.object(cmd, "_maybe_update_skills") as refresh_mock,
        ):
            call = getattr(cmd, f"_handle_{handler}")
            args = (MagicMock(), MagicMock(), MagicMock())
            if success:
                call(*args, package="p", yes=True)
            else:
                with pytest.raises(click.ClickException):
                    call(*args, package="p", yes=True)
        assert refresh_mock.call_count == (1 if success else 0)

    def test_only_install_update_remove_refresh(self):
        tree = ast.parse(inspect.getsource(PluginCommand).lstrip())
        callers = {
            fn.name
            for fn in ast.walk(tree)
            if isinstance(fn, ast.FunctionDef)
            for node in ast.walk(fn)
            if isinstance(node, ast.Attribute) and node.attr == "_maybe_update_skills"
        }
        assert callers == {"_handle_install", "_handle_update", "_handle_remove"}

    def test_refresh_ctrl_c_in_second_tool_keeps_first_and_no_staging(
        self, home, bundle, monkeypatch
    ):
        write_state(
            {"installed_skills": {"claude": {"paths": []}, "cursor": {"paths": []}}}
        )
        real = sg._swap

        def swap(g, *a, **k):
            if g.cli_name == "cursor":
                raise KeyboardInterrupt
            return real(g, *a, **k)

        monkeypatch.setattr(sg, "_swap", swap)
        with pytest.raises(KeyboardInterrupt):
            PluginCommand()._maybe_update_skills()
        state = sg.get_skills_state()
        claude = state["skill_folders"]["claude"]["folders"]
        assert {r["state"] for r in claude.values()} == {"installed"}
        assert "cursor" not in state.get("skill_folders", {})
        for cli in ("claude", "cursor"):
            assert staging_dirs(gen(cli).skills_root()) == []

    def test_refresh_leftover_staging_warns_on_stderr(
        self, home, bundle, monkeypatch, capsys
    ):
        output._output_config["agentic"] = False
        write_state({"installed_skills": {"claude": {"paths": []}}})
        staging = gen("claude").skills_root() / ".deepctl-staging-x"
        real = sg.install_tool
        monkeypatch.setattr(
            sg, "install_tool", lambda *a, **k: (real(*a, **k)[0], staging)
        )
        out, err = refresh(capsys)
        assert err.splitlines() == ["⚠ " + sg._msg("E12", staging=staging)]
        assert out == ""
