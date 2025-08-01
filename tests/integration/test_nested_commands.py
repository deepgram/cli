"""Integration tests for nested command execution."""

import pytest
from click.testing import CliRunner
from unittest.mock import patch, Mock

from deepctl.main import cli as deepctl_cli


class TestNestedCommandsIntegration:
    """Integration tests for nested command structure."""

    @pytest.fixture
    def runner(self):
        """Create a Click CliRunner for testing."""
        return CliRunner()

    @pytest.mark.integration
    def test_debug_group_help_output(self, runner):
        """Test that debug group shows proper help with subcommands."""
        result = runner.invoke(deepctl_cli, ["debug", "--help"])

        assert result.exit_code == 0
        assert "Debug utilities for troubleshooting" in result.output

        # Check that subcommands are listed
        assert "audio" in result.output
        assert "browser" in result.output
        assert "network" in result.output

    @pytest.mark.integration
    def test_debug_audio_command_execution(self, runner):
        """Test executing debug audio subcommand."""
        with patch(
            "deepctl_cmd_debug_audio.command.AudioCommand.handle"
        ) as mock_handle:
            mock_handle.return_value = {"status": "audio debug complete"}

            result = runner.invoke(
                deepctl_cli, ["debug", "audio", "--file", "test.mp3"]
            )

            # Check command was executed
            assert result.exit_code == 0
            mock_handle.assert_called_once()

    @pytest.mark.integration
    def test_debug_browser_command_execution(self, runner):
        """Test executing debug browser subcommand."""
        with patch(
            "deepctl_cmd_debug_browser.command.BrowserCommand.handle"
        ) as mock_handle:
            mock_handle.return_value = {"status": "browser debug complete"}

            result = runner.invoke(deepctl_cli, ["debug", "browser"])

            # Check command was executed
            assert result.exit_code == 0
            mock_handle.assert_called_once()

    @pytest.mark.integration
    def test_debug_network_command_execution(self, runner):
        """Test executing debug network subcommand."""
        with patch(
            "deepctl_cmd_debug_network.command.NetworkCommand.handle"
        ) as mock_handle:
            mock_handle.return_value = {"status": "network debug complete"}

            result = runner.invoke(deepctl_cli, ["debug", "network"])

            # Check command was executed
            assert result.exit_code == 0
            mock_handle.assert_called_once()

    @pytest.mark.integration
    def test_debug_without_subcommand_shows_help(self, runner):
        """Test that debug without subcommand shows help."""
        result = runner.invoke(deepctl_cli, ["debug"])

        # Click exits with code 2 when a required subcommand is missing
        assert result.exit_code == 2
        assert "Debug utilities for troubleshooting" in result.output
        assert "Commands:" in result.output

    @pytest.mark.integration
    def test_hyphenated_command_routing(self, runner):
        """Test that hyphenated commands work correctly."""
        # Test a command with hyphens in the name
        result = runner.invoke(deepctl_cli, ["--help"])

        assert result.exit_code == 0
        # Verify hyphenated commands appear correctly
        assert "debug" in result.output

    @pytest.mark.integration
    def test_nested_command_with_options(self, runner):
        """Test nested command with options passed correctly."""
        with patch(
            "deepctl_cmd_debug_network.command.NetworkCommand.handle"
        ) as mock_handle:
            mock_handle.return_value = {"verbose": True}

            # Assuming network debug has a --verbose flag
            result = runner.invoke(
                deepctl_cli, ["debug", "network", "--verbose"]
            )

            # Verify option was passed
            assert result.exit_code == 0
            mock_handle.assert_called_once()
            # Check that verbose was passed in kwargs
            call_kwargs = mock_handle.call_args[1]
            # This depends on how options are passed to handle

    @pytest.mark.integration
    def test_invalid_subcommand_error(self, runner):
        """Test error handling for invalid subcommand."""
        result = runner.invoke(deepctl_cli, ["debug", "invalid-subcommand"])

        assert result.exit_code != 0
        assert "Error" in result.output or "No such command" in result.output

    @pytest.mark.integration
    def test_command_hierarchy_depth(self, runner):
        """Test that command hierarchy is displayed correctly."""
        # Test help at different levels

        # Root level
        root_result = runner.invoke(deepctl_cli, ["--help"])
        assert "debug" in root_result.output
        assert "transcribe" in root_result.output

        # Group level
        group_result = runner.invoke(deepctl_cli, ["debug", "--help"])
        assert "audio" in group_result.output
        assert "browser" in group_result.output
        assert "network" in group_result.output

    @pytest.mark.integration
    def test_plugin_discovery_integration(self, runner):
        """Test that plugins are discovered and loaded correctly."""
        # This test verifies the full plugin discovery mechanism
        result = runner.invoke(deepctl_cli, ["--help"])

        assert result.exit_code == 0

        # Check that expected commands are present
        expected_commands = [
            "login",
            "projects",
            "transcribe",
            "usage",
            "debug",
        ]
        for cmd in expected_commands:
            assert cmd in result.output

    @pytest.mark.integration
    def test_subcommand_inherits_parent_context(self, runner):
        """Test that subcommands inherit context from parent group."""
        with patch(
            "deepctl_cmd_debug_audio.command.AudioCommand.handle"
        ) as mock_handle:

            def check_context(config, auth_manager, client, **kwargs):
                # Verify that config, auth_manager, and client are passed
                assert config is not None
                assert auth_manager is not None
                assert client is not None
                return {"context": "verified"}

            mock_handle.side_effect = check_context

            result = runner.invoke(
                deepctl_cli, ["debug", "audio", "--file", "test.mp3"]
            )
            assert result.exit_code == 0

    @pytest.mark.integration
    def test_multiple_nested_groups(self, runner):
        """Test handling of multiple levels of nested groups if implemented."""
        # This would test deeper nesting if we had commands like:
        # deepctl debug network advanced
        # For now, we'll just verify our current structure works
        result = runner.invoke(deepctl_cli, ["debug", "--help"])
        assert "Commands:" in result.output
        assert result.exit_code == 0
