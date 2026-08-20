"""Unit tests for PluginManager class."""

from importlib.metadata import EntryPoint
from typing import Any, Dict, List
from unittest.mock import MagicMock, Mock, create_autospec, patch

import click
import pytest
from deepctl_core import (
    AuthManager,
    BaseCommand,
    BaseGroupCommand,
    Config,
    DeepgramClient,
    PluginManager,
)


class TestPluginManager:
    """Test suite for PluginManager class."""

    @pytest.fixture
    def plugin_manager(self):
        """Create a plugin manager instance for testing."""
        return PluginManager()

    @pytest.fixture
    def mock_command_class(self):
        """Create a mock command class for testing."""

        class MockCommand(BaseCommand):
            name = "test-command"
            help = "Test command"

            def handle(
                self,
                config: Config,
                auth_manager: AuthManager,
                client: DeepgramClient,
                **kwargs,
            ) -> Any:
                return {"result": "success"}

        return MockCommand

    @pytest.fixture
    def mock_group_command_class(self):
        """Create a mock group command class for testing."""

        class MockGroupCommand(BaseGroupCommand):
            name = "test-group"
            help = "Test group command"

            def handle_group(
                self,
                config: Config,
                auth_manager: AuthManager,
                client: DeepgramClient,
                **kwargs,
            ) -> Any:
                return {"group": "success"}

        return MockGroupCommand

    @pytest.fixture
    def mock_cli_group(self):
        """Create a mock Click CLI group."""
        return create_autospec(click.Group, spec_set=True)

    @pytest.mark.unit
    def test_constructor_initializes_attributes(self, plugin_manager):
        """Test that constructor initializes required attributes."""
        assert plugin_manager.loaded_plugins == {}
        assert plugin_manager.command_classes == {}

    @pytest.mark.unit
    def test_hyphenated_command_name_from_underscore_entry_point(
        self, plugin_manager, mock_command_class, mock_cli_group
    ):
        """Test that entry points with underscores are converted to hyphenated commands."""
        # Create mock entry point with underscore name
        mock_entry_point = Mock(spec=EntryPoint)
        mock_entry_point.name = "test_command"  # Underscore in entry point
        mock_entry_point.load.return_value = mock_command_class

        # Mock metadata.entry_points
        with patch(
            "deepctl_core.plugin_manager.metadata.entry_points"
        ) as mock_entry_points:
            mock_ep_group = Mock()
            mock_ep_group.select.return_value = [mock_entry_point]
            mock_entry_points.return_value = mock_ep_group

            # Load plugins
            plugin_manager._load_external_plugins(mock_cli_group)

            # Verify the command was added with the class's hyphenated name
            mock_cli_group.add_command.assert_called_once()
            click_command = mock_cli_group.add_command.call_args[0][0]
            # Should use class name, not entry point
            assert click_command.name == "test-command"

    @pytest.mark.unit
    def test_hyphenated_subcommand_loading(
        self, plugin_manager, mock_group_command_class, mock_command_class
    ):
        """Test loading subcommands with hyphenated names."""
        # Create group instance
        group_instance = mock_group_command_class()

        # Create mock Click group
        mock_click_group = create_autospec(click.Group, spec_set=True)

        # Create mock entry point for subcommand
        mock_entry_point = Mock(spec=EntryPoint)
        mock_entry_point.name = "sub_command"  # Underscore in entry point
        mock_entry_point.load.return_value = mock_command_class

        # Mock metadata.entry_points
        with patch(
            "deepctl_core.plugin_manager.metadata.entry_points"
        ) as mock_entry_points:
            mock_ep_group = Mock()
            mock_ep_group.select.return_value = [mock_entry_point]
            mock_entry_points.return_value = mock_ep_group

            # Load subcommands
            plugin_manager._load_subcommands_for_group(
                mock_click_group, group_instance
            )

            # Verify subcommand was added twice (once for each entry point group checked)
            assert mock_click_group.add_command.call_count == 2
            # Both calls should add the same command
            for call in mock_click_group.add_command.call_args_list:
                click_command = call[0][0]
                assert click_command.name == "test-command"

    @pytest.mark.unit
    def test_function_name_conversion_for_hyphens(
        self, plugin_manager, mock_command_class
    ):
        """Test that hyphenated command names are converted to underscores for function names."""
        # Create command instance with hyphenated name
        command_instance = mock_command_class()
        command_instance.name = "test-hyphenated-command"

        # Create Click command
        click_command = plugin_manager._create_click_command(command_instance)

        # Check that function name has underscores
        assert click_command.callback.__name__ == "test_hyphenated_command"

    @pytest.mark.unit
    def test_group_function_name_conversion_for_hyphens(
        self, plugin_manager, mock_group_command_class
    ):
        """Test that hyphenated group names are converted to underscores for function names."""
        # Create group instance with hyphenated name
        group_instance = mock_group_command_class()
        group_instance.name = "test-hyphenated-group"

        # Create Click group
        click_group = plugin_manager._create_click_group(group_instance)

        # Check that function name has underscores
        assert click_group.callback.__name__ == "test_hyphenated_group"

    @pytest.mark.unit
    def test_subcommand_entry_point_pattern(
        self, plugin_manager, mock_group_command_class, mock_command_class
    ):
        """Test that subcommands are loaded from the correct entry point pattern."""
        # Create group with specific name
        group_instance = mock_group_command_class()
        group_instance.name = "debug"

        mock_click_group = create_autospec(click.Group, spec_set=True)

        # Create mock entry points
        mock_entry_point = Mock(spec=EntryPoint)
        mock_entry_point.name = "audio"
        mock_entry_point.load.return_value = mock_command_class

        with patch(
            "deepctl_core.plugin_manager.metadata.entry_points"
        ) as mock_entry_points:
            mock_ep_group = Mock()

            # Should query for "deepctl.subcommands.debug"
            def mock_select(group):
                if group == "deepctl.subcommands.debug":
                    return [mock_entry_point]
                return []

            mock_ep_group.select = mock_select
            mock_entry_points.return_value = mock_ep_group

            # Load subcommands
            plugin_manager._load_subcommands_for_group(
                mock_click_group, group_instance
            )

            # Verify it loaded the subcommand
            mock_click_group.add_command.assert_called_once()

    @pytest.mark.unit
    def test_nested_hyphenated_commands(self, plugin_manager):
        """Test that nested commands with hyphens work correctly."""

        # Create a group with hyphenated name
        class DebugNetworkGroup(BaseGroupCommand):
            name = "debug-network"
            help = "Debug network commands"

        # Create a subcommand with hyphenated name
        class TestConnectionCommand(BaseCommand):
            name = "test-connection"
            help = "Test network connection"

            def handle(self, config, auth_manager, client, **kwargs):
                return {"tested": True}

        group_instance = DebugNetworkGroup()
        mock_click_group = create_autospec(click.Group, spec_set=True)

        # Create mock entry point
        mock_entry_point = Mock(spec=EntryPoint)
        mock_entry_point.name = (
            "test_connection"  # Entry point with underscore
        )
        mock_entry_point.load.return_value = TestConnectionCommand

        with patch(
            "deepctl_core.plugin_manager.metadata.entry_points"
        ) as mock_entry_points:
            mock_ep_group = Mock()

            def mock_select(group):
                if group == "deepctl.subcommands.debug-network":
                    return [mock_entry_point]
                return []

            mock_ep_group.select = mock_select
            mock_entry_points.return_value = mock_ep_group

            # Load subcommands
            plugin_manager._load_subcommands_for_group(
                mock_click_group, group_instance
            )

            # Verify the command was added with correct hyphenated name
            mock_click_group.add_command.assert_called_once()
            click_command = mock_click_group.add_command.call_args[0][0]
            assert click_command.name == "test-connection"

    @pytest.mark.unit
    def test_command_list_returns_all_loaded_commands(
        self, plugin_manager, mock_command_class
    ):
        """Test that get_command_list returns all loaded commands."""
        # Add some commands
        plugin_manager.command_classes["cmd1"] = mock_command_class
        plugin_manager.loaded_plugins["cmd2"] = Mock()

        command_list = plugin_manager.get_command_list()

        assert "cmd1" in command_list
        assert "cmd2" in command_list
        assert len(command_list) == 2

    @pytest.mark.unit
    def test_error_handling_for_invalid_plugin(
        self, plugin_manager, mock_cli_group
    ):
        """Test that invalid plugins are handled gracefully."""
        # Create mock entry point that raises exception
        mock_entry_point = Mock(spec=EntryPoint)
        mock_entry_point.name = "bad_plugin"
        mock_entry_point.load.side_effect = ImportError("Cannot import plugin")

        with patch(
            "deepctl_core.plugin_manager.metadata.entry_points"
        ) as mock_entry_points:
            mock_ep_group = Mock()
            mock_ep_group.select.return_value = [mock_entry_point]
            mock_entry_points.return_value = mock_ep_group

            # Should not raise exception
            plugin_manager._load_external_plugins(mock_cli_group)

            # Command should not be added
            mock_cli_group.add_command.assert_not_called()

    @pytest.mark.unit
    def test_validate_plugin(self, plugin_manager, mock_command_class):
        """Test plugin validation."""
        # Valid plugin
        assert plugin_manager.validate_plugin(mock_command_class) is True

        # Invalid plugin - no name
        class NoNameCommand(BaseCommand):
            help = "Command without name"

        assert plugin_manager.validate_plugin(NoNameCommand) is False

        # Invalid plugin - not a BaseCommand
        class NotACommand:
            name = "test"
            help = "Not a command"

        assert plugin_manager.validate_plugin(NotACommand) is False

    @pytest.mark.unit
    def test_constructor_initializes_dedup_set(self, plugin_manager):
        """Test that constructor initializes the dedup tracking set."""
        assert plugin_manager._loaded_entry_point_values == set()

    @pytest.mark.unit
    def test_builtin_commands_track_entry_point_values(
        self, plugin_manager, mock_command_class, mock_cli_group
    ):
        """Test that loading builtin commands populates the dedup set."""
        mock_entry_point = Mock(spec=EntryPoint)
        mock_entry_point.name = "test_cmd"
        mock_entry_point.value = "my_module:TestCommand"
        mock_entry_point.load.return_value = mock_command_class

        with patch(
            "deepctl_core.plugin_manager.metadata.entry_points"
        ) as mock_entry_points:
            mock_ep_group = Mock()
            mock_ep_group.select.return_value = [mock_entry_point]
            mock_entry_points.return_value = mock_ep_group

            plugin_manager._load_builtin_commands(mock_cli_group)

            assert "my_module:TestCommand" in plugin_manager._loaded_entry_point_values

    @pytest.mark.unit
    def test_external_plugins_track_entry_point_values(
        self, plugin_manager, mock_command_class, mock_cli_group
    ):
        """Test that loading external plugins populates the dedup set."""
        mock_entry_point = Mock(spec=EntryPoint)
        mock_entry_point.name = "ext_plugin"
        mock_entry_point.value = "ext_module:ExtPlugin"
        mock_entry_point.load.return_value = mock_command_class

        with patch(
            "deepctl_core.plugin_manager.metadata.entry_points"
        ) as mock_entry_points:
            mock_ep_group = Mock()
            mock_ep_group.select.return_value = [mock_entry_point]
            mock_entry_points.return_value = mock_ep_group

            plugin_manager._load_external_plugins(mock_cli_group)

            assert "ext_module:ExtPlugin" in plugin_manager._loaded_entry_point_values

    @pytest.mark.unit
    def test_plugin_venv_skips_when_no_venv(
        self, plugin_manager, mock_cli_group
    ):
        """Test that _load_plugin_venv_entries bails when venv doesn't exist."""
        with patch(
            "deepctl_core.plugin_manager.PLUGIN_VENV"
        ) as mock_venv:
            mock_venv.exists.return_value = False

            # Should not raise
            plugin_manager._load_plugin_venv_entries(mock_cli_group)

            # No commands should be added
            mock_cli_group.add_command.assert_not_called()

    @pytest.mark.unit
    def test_plugin_venv_skips_when_state_empty(
        self, plugin_manager, mock_cli_group
    ):
        """Test that _load_plugin_venv_entries bails when plugins.json is empty."""
        with patch(
            "deepctl_core.plugin_manager.PLUGIN_VENV"
        ) as mock_venv:
            mock_venv.exists.return_value = True

            with patch(
                "deepctl_core.plugin_manager.get_plugin_state",
                return_value={"plugins": {}},
            ):
                plugin_manager._load_plugin_venv_entries(mock_cli_group)
                mock_cli_group.add_command.assert_not_called()

    @pytest.mark.unit
    def test_plugin_venv_skips_when_no_site_packages(
        self, plugin_manager, mock_cli_group
    ):
        """Test that _load_plugin_venv_entries bails when site-packages can't be found."""
        with patch(
            "deepctl_core.plugin_manager.PLUGIN_VENV"
        ) as mock_venv:
            mock_venv.exists.return_value = True

            with patch(
                "deepctl_core.plugin_manager.get_plugin_state",
                return_value={"plugins": {"some-plugin": {}}},
            ), patch(
                "deepctl_core.plugin_manager.get_venv_site_packages",
                return_value=None,
            ):
                plugin_manager._load_plugin_venv_entries(mock_cli_group)
                mock_cli_group.add_command.assert_not_called()

    @pytest.mark.unit
    def test_plugin_venv_deduplicates_already_loaded(
        self, plugin_manager, mock_command_class, mock_cli_group
    ):
        """Test that plugins already loaded from main env are skipped."""
        # Pre-populate the dedup set
        plugin_manager._loaded_entry_point_values.add(
            "my_plugin.command:MyPlugin"
        )

        # Create a mock distribution with the same entry point value
        mock_ep = Mock()
        mock_ep.name = "my-plugin"
        mock_ep.group = "deepctl.plugins"
        mock_ep.value = "my_plugin.command:MyPlugin"

        mock_dist = Mock()
        mock_dist.entry_points = [mock_ep]

        from pathlib import Path as RealPath
        fake_sp = RealPath("/fake/site-packages")

        with patch(
            "deepctl_core.plugin_manager.PLUGIN_VENV"
        ) as mock_venv:
            mock_venv.exists.return_value = True

            with patch(
                "deepctl_core.plugin_manager.get_plugin_state",
                return_value={"plugins": {"my-plugin": {}}},
            ), patch(
                "deepctl_core.plugin_manager.get_venv_site_packages",
                return_value=fake_sp,
            ), patch(
                "deepctl_core.plugin_manager.metadata.distributions",
                return_value=[mock_dist],
            ), patch(
                "deepctl_core.plugin_manager.sys"
            ) as mock_sys:
                mock_sys.path = []

                plugin_manager._load_plugin_venv_entries(
                    mock_cli_group
                )

                # Should NOT add the command (it's a duplicate)
                mock_cli_group.add_command.assert_not_called()

    @pytest.mark.unit
    def test_plugin_venv_loads_new_plugins(
        self, plugin_manager, mock_command_class, mock_cli_group
    ):
        """Test that new plugins from the venv are loaded."""
        mock_ep = Mock()
        mock_ep.name = "venv-plugin"
        mock_ep.group = "deepctl.plugins"
        mock_ep.value = "venv_plugin.command:VenvPlugin"
        mock_ep.load.return_value = mock_command_class

        mock_dist = Mock()
        mock_dist.entry_points = [mock_ep]

        from pathlib import Path as RealPath
        fake_sp = RealPath("/fake/site-packages")

        with patch(
            "deepctl_core.plugin_manager.PLUGIN_VENV"
        ) as mock_venv:
            mock_venv.exists.return_value = True

            with patch(
                "deepctl_core.plugin_manager.get_plugin_state",
                return_value={"plugins": {"venv-plugin": {}}},
            ), patch(
                "deepctl_core.plugin_manager.get_venv_site_packages",
                return_value=fake_sp,
            ), patch(
                "deepctl_core.plugin_manager.metadata.distributions",
                return_value=[mock_dist],
            ), patch(
                "deepctl_core.plugin_manager.sys"
            ) as mock_sys:
                mock_sys.path = []

                plugin_manager._load_plugin_venv_entries(
                    mock_cli_group
                )

                # Should add the command
                mock_cli_group.add_command.assert_called_once()
                # Should track in dedup set
                assert (
                    "venv_plugin.command:VenvPlugin"
                    in plugin_manager._loaded_entry_point_values
                )
                # Should be in loaded_plugins
                assert "venv-plugin" in plugin_manager.loaded_plugins

    @pytest.mark.unit
    def test_warn_if_plugin_venv_python_mismatch_silent_when_unknown(
        self, plugin_manager
    ):
        """No warning when get_venv_python_version returns None."""
        with patch(
            "deepctl_core.plugin_manager.get_venv_python_version",
            return_value=None,
        ), patch("deepctl_core.plugin_manager.print_warning") as mock_warn:
            plugin_manager._warn_if_plugin_venv_python_mismatch()
            mock_warn.assert_not_called()

    @pytest.mark.unit
    def test_warn_if_plugin_venv_python_mismatch_silent_when_match(
        self, plugin_manager
    ):
        """No warning when venv version matches running interpreter."""
        with patch(
            "deepctl_core.plugin_manager.get_venv_python_version",
            return_value=(3, 13),
        ), patch(
            "deepctl_core.plugin_manager.sys.version_info",
            Mock(major=3, minor=13),
        ), patch(
            "deepctl_core.plugin_manager.print_warning"
        ) as mock_warn:
            plugin_manager._warn_if_plugin_venv_python_mismatch()
            mock_warn.assert_not_called()

    @pytest.mark.unit
    def test_warn_if_plugin_venv_python_mismatch_warns_on_minor_diff(
        self, plugin_manager
    ):
        """Warning fires when venv minor differs from running interpreter."""
        with patch(
            "deepctl_core.plugin_manager.get_venv_python_version",
            return_value=(3, 12),
        ), patch(
            "deepctl_core.plugin_manager.sys.version_info",
            Mock(major=3, minor=13),
        ), patch(
            "deepctl_core.plugin_manager.print_warning"
        ) as mock_warn:
            plugin_manager._warn_if_plugin_venv_python_mismatch()
            mock_warn.assert_called_once()
            msg = mock_warn.call_args[0][0]
            assert "3.12" in msg
            assert "3.13" in msg
            assert "rm -rf" in msg

    @pytest.mark.unit
    def test_warn_if_plugin_venv_python_mismatch_warns_on_major_diff(
        self, plugin_manager
    ):
        """Warning fires when venv major differs (e.g. Python 4 someday)."""
        with patch(
            "deepctl_core.plugin_manager.get_venv_python_version",
            return_value=(3, 13),
        ), patch(
            "deepctl_core.plugin_manager.sys.version_info",
            Mock(major=4, minor=0),
        ), patch(
            "deepctl_core.plugin_manager.print_warning"
        ) as mock_warn:
            plugin_manager._warn_if_plugin_venv_python_mismatch()
            mock_warn.assert_called_once()


class TestLoadErrorStream:
    """Plugin-load diagnostics must go to stderr, never stdout.

    A broken plugin's ImportError used to print through a stdout Console,
    corrupting `dg ... -o json` payloads for every remaining command (the
    amplifier in the 0.2.x core-floor incident). The module console is the
    shared stderr console so that cannot recur.
    """

    def test_module_console_is_the_shared_stderr_console(self):
        from deepctl_core import plugin_manager
        from deepctl_core.output import stderr_console

        assert plugin_manager.console is stderr_console
        assert plugin_manager.console.stderr is True

    def test_load_error_writes_to_stderr_not_stdout(self, capsys):
        from deepctl_core import plugin_manager

        mock_entry_point = Mock()
        mock_entry_point.name = "broken-command"
        mock_entry_point.load.side_effect = ImportError("Module not found")

        with patch(
            "deepctl_core.plugin_manager.metadata.entry_points"
        ) as mock_eps:
            mock_eps.return_value.select.return_value = [mock_entry_point]
            plugin_manager.PluginManager()._load_builtin_commands(
                click.Group("dg")
            )

        captured = capsys.readouterr()
        assert "broken-command" in captured.err
        assert captured.out == ""
