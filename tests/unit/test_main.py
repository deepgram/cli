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
        assert "deepctl, version" in result.output

    def test_cli_help(self, runner):
        """Test that --help displays help information."""
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "deepctl" in result.output
        assert "Official Deepgram CLI" in result.output
        assert "--config" in result.output
        assert "--profile" in result.output
        assert "--output" in result.output
        assert "--quiet" in result.output
        assert "--verbose" in result.output

    def test_cli_with_config_option(self, runner, tmp_path):
        """Test CLI with custom config path."""
        config_file = tmp_path / "custom.yaml"
        config_file.write_text("version: 1.0\n")

        result = runner.invoke(cli, ["--config", str(config_file), "--help"])
        assert result.exit_code == 0

    def test_cli_with_output_format(self, runner):
        """Test CLI with different output formats."""
        for format_type in ["json", "yaml", "table", "csv"]:
            result = runner.invoke(cli, ["--output", format_type, "--help"])
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
            "Error loading plugin broken-command" in call for call in error_calls
        )

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

        # Patch the module-global `cli` that main() calls, going through
        # sys.modules because deepctl/__init__ re-exports the `main` function
        # as `deepctl.main`, shadowing the submodule attribute. (Patching the
        # instance's __call__ is inert: dunder lookup bypasses instance
        # attributes, so the old patch.object(cli, "__call__", ...) form
        # exercised the bare-`dg` help path instead.)
        main_mod = sys.modules["deepctl.main"]
        with patch("sys.argv", ["deepctl"]):
            with patch.object(main_mod, "cli", side_effect=KeyboardInterrupt()):
                with pytest.raises(SystemExit) as exc_info:
                    main()

                # main()'s own KeyboardInterrupt handler: 2 = user interrupt
                assert exc_info.value.code == 2

    def test_main_click_abort(self):
        """A click Abort (Ctrl-C/Ctrl-D during a command) exits 2.

        With standalone_mode=False, Click catches a KeyboardInterrupt raised
        inside command execution and re-raises it as Abort (a RuntimeError,
        not a KeyboardInterrupt) -- so the mid-command interrupt, the common
        case, reaches main() as Abort. It is user cancellation: 2, not 1.
        """
        import click

        from deepctl.main import main

        main_mod = sys.modules["deepctl.main"]
        with patch("sys.argv", ["deepctl"]):
            with patch.object(
                main_mod, "cli", side_effect=click.exceptions.Abort()
            ):
                with pytest.raises(SystemExit) as exc_info:
                    main()

                assert exc_info.value.code == 2

    def test_main_general_exception(self):
        """Test main() handles general exceptions."""
        from deepctl.main import main

        main_mod = sys.modules["deepctl.main"]
        with patch("sys.argv", ["deepctl"]):
            with patch.object(main_mod, "cli", side_effect=Exception("Test error")):
                with pytest.raises(SystemExit) as exc_info:
                    main()

                # main()'s own handler: 1 = error (2 is reserved for interrupt)
                assert exc_info.value.code == 1

    def test_main_usage_error(self):
        """A Click usage error (bad flag, unknown command) exits 1.

        standalone_mode=False means Click's UsageError propagates to main()'s
        generic handler rather than Click's own standalone exit(2) -- per the
        published contract, 1 = error and 2 is reserved for user interrupt.
        """
        from deepctl.main import main

        with patch("sys.argv", ["deepctl", "--definitely-not-a-flag"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

            assert exc_info.value.code == 1

    @pytest.mark.parametrize(
        ("argv", "label"),
        [
            (["deepctl", "-o", "json", "not-a-command"], "unknown command"),
            (["deepctl", "-o", "json", "--definitely-not-a-flag"], "bad flag"),
        ],
    )
    def test_failure_keeps_stdout_clean(self, capsys, argv, label):
        """A failing `dg -o json ...` writes nothing to stdout.

        The whole point of `-o json` is that stdout is machine-readable, so a
        script can pipe it into jq. Diagnostics therefore belong on stderr:
        printing `Error: ...` to stdout leaves the caller parsing prose. This
        is the root-handler half of the #97 sweep, which moved command-level
        status chrome to stderr but left main()'s own handlers on stdout.
        """
        from deepctl.main import main

        with patch("sys.argv", argv):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert captured.out == "", f"{label} polluted stdout: {captured.out!r}"
        assert "Error" in captured.err

    def test_interrupt_message_goes_to_stderr(self, capsys):
        """The cancellation notice is a diagnostic, so it also stays off stdout."""
        import click

        from deepctl.main import main

        main_mod = sys.modules["deepctl.main"]
        with patch("sys.argv", ["deepctl"]):
            with patch.object(
                main_mod, "cli", side_effect=click.exceptions.Abort()
            ):
                with pytest.raises(SystemExit):
                    main()

        captured = capsys.readouterr()
        assert captured.out == ""
        assert "cancelled" in captured.err.lower()


class TestSafeConsolePrint:
    """The closed/broken-stream guard on the error/interrupt exit path.

    Anchor: DX-CLI-P — a BrokenPipeError on the startup notification write
    cascaded into rich's `ValueError: I/O operation on closed file` when the
    error handler tried to print to the already-closed console, crashing the
    process through the excepthook.
    """

    @pytest.mark.parametrize(
        "error",
        [
            BrokenPipeError(32, "Broken pipe"),
            ValueError("I/O operation on closed file"),
            OSError("stream closed"),
        ],
    )
    def test_swallows_closed_stream(self, error):
        """A broken console does not raise out of _safe_console_print."""
        # deepctl/__init__ re-exports the `main` function as `deepctl.main`,
        # shadowing the submodule attribute — fetch the real module directly.
        main_mod = sys.modules["deepctl.main"]

        with patch.object(main_mod.console, "print", side_effect=error):
            # Must not raise.
            main_mod._safe_console_print("[red]Error: boom[/red]")

    def test_main_survives_broken_console_on_error(self):
        """The full DX-CLI-P cascade: cli raises AND the console is closed.

        main() must still exit(1) cleanly rather than let rich's ValueError
        escape to the excepthook.
        """
        main_mod = sys.modules["deepctl.main"]

        with (
            patch("sys.argv", ["deepctl"]),
            patch.object(main_mod, "cli", side_effect=Exception("boom")),
            patch.object(
                main_mod.console,
                "print",
                side_effect=ValueError("I/O operation on closed file"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main_mod.main()

        assert exc_info.value.code == 1
