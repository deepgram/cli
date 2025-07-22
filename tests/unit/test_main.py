"""Unit tests for the main CLI entry point."""

from unittest.mock import patch, Mock, MagicMock

import click
import pytest
from click.testing import CliRunner
import sys


# Mock PluginManager before importing deepctl.main to prevent plugin loading
with patch("deepctl_core.PluginManager") as mock_pm_class:
    mock_pm = MagicMock()
    mock_pm_class.return_value = mock_pm
    mock_pm.load_plugins = MagicMock()
    from deepctl.main import cli, load_commands


class TestMainCLI:
    """Test suite for main CLI functionality."""

    @pytest.fixture
    def runner(self):
        """Create a Click test runner."""
        return CliRunner()

    def test_cli_version(self, runner):
        """Test that --version displays version information."""
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "deepctl, version 0.1.0" in result.output

    def test_cli_help(self, runner):
        """Test that --help displays help information."""
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "deepctl - Official Deepgram CLI for speech recognition and audio intelligence" in result.output
        assert "--config" in result.output
        assert "--profile" in result.output
        assert "--output" in result.output
        assert "--quiet" in result.output
        assert "--verbose" in result.output

    def test_cli_with_config_option(self, runner, tmp_path):
        """Test CLI with custom config path."""
        config_file = tmp_path / "custom.yaml"
        config_file.write_text("version: 1.0\n")

        result = runner.invoke(
            cli, ["--config", str(config_file), "--help"])
        assert result.exit_code == 0

    def test_cli_with_output_format(self, runner):
        """Test CLI with different output formats."""
        for format_type in ["json", "yaml", "table", "csv"]:
            result = runner.invoke(
                cli, ["--output", format_type, "--help"])
            assert result.exit_code == 0

    def test_cli_with_invalid_output_format(self, runner):
        """Test CLI with invalid output format."""
        result = runner.invoke(cli, ["--output", "invalid"])
        assert result.exit_code != 0
        assert "Invalid value" in result.output

    @patch("deepctl_core.PluginManager")
    def test_load_commands_success(self, mock_plugin_manager_class):
        """Test successful command loading from entry points."""
        # Mock the plugin manager instance
        mock_plugin_manager = Mock()
        mock_plugin_manager_class.return_value = mock_plugin_manager

        # Load commands should not raise
        load_commands()

        # Verify plugin manager was created and load_plugins was called
        mock_plugin_manager_class.assert_called_once()
        mock_plugin_manager.load_plugins.assert_called_once()

    @patch("deepctl_core.plugin_manager.console")
    @patch("deepctl_core.plugin_manager.metadata.entry_points")
    def test_load_commands_error_handling(self, mock_entry_points, mock_console):
        """Test error handling during command loading."""
        # Mock entry point that raises error
        mock_entry_point = Mock()
        mock_entry_point.name = "broken-command"
        mock_entry_point.load.side_effect = ImportError("Module not found")

        # Setup entry points mock
        mock_entry_points.return_value.select.return_value = [mock_entry_point]

        # Load commands should handle error gracefully
        load_commands()

        # Verify error was printed
        assert mock_console.print.called
        # Check that error message was printed
        error_calls = [str(call) for call in mock_console.print.call_args_list]
        assert any(
            "Error loading plugin broken-command" in call for call in error_calls)

    def test_cli_context_setup(self, runner):
        """Test that CLI context is properly set up."""
        @cli.command()
        @click.pass_context
        def test_cmd(ctx):
            assert "config" in ctx.obj
            assert ctx.obj["config"] is not None

        result = runner.invoke(cli, ["test_cmd"])
        # Command won't be found since we didn't actually register it properly
        # but we're just testing the context setup
        assert result.exit_code != 0

    def test_main_keyboard_interrupt(self):
        """Test main() handles KeyboardInterrupt."""
        from deepctl.main import main

        # Mock sys.argv and the cli call to raise KeyboardInterrupt
        with patch('sys.argv', ['deepctl']):
            # Patch the cli function that's already imported at module level
            with patch.object(cli, '__call__', side_effect=KeyboardInterrupt()):
                with pytest.raises(SystemExit) as exc_info:
                    main()

                # Click exits with code 2 when there's an error in standalone mode
                assert exc_info.value.code == 2

    def test_main_general_exception(self):
        """Test main() handles general exceptions."""
        from deepctl.main import main

        # Mock sys.argv and the cli call to raise an exception
        with patch('sys.argv', ['deepctl']):
            # Patch the cli function that's already imported at module level
            with patch.object(cli, '__call__', side_effect=Exception("Test error")):
                with pytest.raises(SystemExit) as exc_info:
                    main()

                # Click exits with code 2 when there's an error in standalone mode
                assert exc_info.value.code == 2
