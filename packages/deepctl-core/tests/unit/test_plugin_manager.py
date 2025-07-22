"""Unit tests for PluginManager class."""

from typing import Any, Dict, List
from unittest.mock import Mock, patch, MagicMock, create_autospec
from importlib.metadata import EntryPoint

import click
import pytest

from deepctl_core import BaseCommand, BaseGroupCommand, PluginManager, Config, AuthManager, DeepgramClient


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

            def handle(self, config: Config, auth_manager: AuthManager,
                       client: DeepgramClient, **kwargs) -> Any:
                return {"result": "success"}

        return MockCommand

    @pytest.fixture
    def mock_group_command_class(self):
        """Create a mock group command class for testing."""
        class MockGroupCommand(BaseGroupCommand):
            name = "test-group"
            help = "Test group command"

            def handle_group(self, config: Config, auth_manager: AuthManager,
                             client: DeepgramClient, **kwargs) -> Any:
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
        with patch('deepctl_core.plugin_manager.metadata.entry_points') as mock_entry_points:
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
        with patch('deepctl_core.plugin_manager.metadata.entry_points') as mock_entry_points:
            mock_ep_group = Mock()
            mock_ep_group.select.return_value = [mock_entry_point]
            mock_entry_points.return_value = mock_ep_group

            # Load subcommands
            plugin_manager._load_subcommands_for_group(
                mock_click_group, group_instance)

            # Verify subcommand was added twice (once for each entry point group checked)
            assert mock_click_group.add_command.call_count == 2
            # Both calls should add the same command
            for call in mock_click_group.add_command.call_args_list:
                click_command = call[0][0]
                assert click_command.name == "test-command"

    @pytest.mark.unit
    def test_function_name_conversion_for_hyphens(self, plugin_manager, mock_command_class):
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

        with patch('deepctl_core.plugin_manager.metadata.entry_points') as mock_entry_points:
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
                mock_click_group, group_instance)

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
        mock_entry_point.name = "test_connection"  # Entry point with underscore
        mock_entry_point.load.return_value = TestConnectionCommand

        with patch('deepctl_core.plugin_manager.metadata.entry_points') as mock_entry_points:
            mock_ep_group = Mock()

            def mock_select(group):
                if group == "deepctl.subcommands.debug-network":
                    return [mock_entry_point]
                return []

            mock_ep_group.select = mock_select
            mock_entry_points.return_value = mock_ep_group

            # Load subcommands
            plugin_manager._load_subcommands_for_group(
                mock_click_group, group_instance)

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
    def test_error_handling_for_invalid_plugin(self, plugin_manager, mock_cli_group):
        """Test that invalid plugins are handled gracefully."""
        # Create mock entry point that raises exception
        mock_entry_point = Mock(spec=EntryPoint)
        mock_entry_point.name = "bad_plugin"
        mock_entry_point.load.side_effect = ImportError("Cannot import plugin")

        with patch('deepctl_core.plugin_manager.metadata.entry_points') as mock_entry_points:
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
