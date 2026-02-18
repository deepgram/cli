"""Unit tests for plugin command."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from deepctl_cmd_plugin.command import PluginCommand
from deepctl_cmd_plugin.models import (
    PluginInstallOptions,
    PluginOperationResult,
)
from deepctl_cmd_update.installation import InstallMethod
from deepctl_core.auth import AuthManager
from deepctl_core.client import DeepgramClient
from deepctl_core.config import Config


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

        with patch.object(Path, "exists", return_value=True):
            with patch.object(
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
                ) as mock_get_state:
                    with patch.object(
                        self.command, "_save_plugin_state"
                    ) as mock_save_state:
                        with patch.object(
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
        ):
            with patch(
                "deepctl_cmd_plugin.command.console.print"
            ) as mock_print:
                with patch(
                    "deepctl_cmd_plugin.command.print_info"
                ) as mock_print_info:
                    with patch.object(
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
                ) as mock_print:
                    with patch(
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
