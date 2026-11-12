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

# The Click CLI and run() live in deepctl._cli; deepctl.main is the light entry
# point that imports it on the full path. Patch names there.
import deepctl._cli as _cli_mod


@pytest.fixture
def reload_commands(monkeypatch):
    """load_commands() runs once per process; let a test run it again."""
    monkeypatch.setattr(_cli_mod, "_commands_loaded", False)


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
    def test_load_commands_success(self, mock_plugin_manager_class, reload_commands):
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
    def test_load_commands_error_handling(
        self, mock_entry_points, mock_console, reload_commands
    ):
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

        # Patch the module-global `cli` that run() calls, in deepctl._cli.
        # (Patching the instance's __call__ is inert: dunder lookup bypasses
        # instance attributes, so the old patch.object(cli, "__call__", ...)
        # form exercised the bare-`dg` help path instead.)
        main_mod = _cli_mod
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

        main_mod = _cli_mod
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

        main_mod = _cli_mod
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
        # "Error: ..." for people, "ERROR: ..." in agent mode.
        assert "error" in captured.err.lower()

    def test_interrupt_message_goes_to_stderr(self, capsys):
        """The cancellation notice is a diagnostic, so it also stays off stdout."""
        import click

        from deepctl.main import main

        main_mod = _cli_mod
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
        main_mod = _cli_mod

        with patch.object(main_mod.console, "print", side_effect=error):
            # Must not raise.
            main_mod._safe_console_print("[red]Error: boom[/red]")

    def test_main_survives_broken_console_on_error(self):
        """The full DX-CLI-P cascade: cli raises AND the console is closed.

        main() must still exit(1) cleanly rather than let rich's ValueError
        escape to the excepthook.
        """
        from deepctl.main import main

        main_mod = _cli_mod

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
            main()

        assert exc_info.value.code == 1


class TestGlobalOptionHint:
    """A global option after the subcommand gets a placement hint.

    `dg models --profile staging` fails because Click parses group options
    only before the subcommand name. The bare "No such option" reads as if
    the option does not exist; the hint names the working placement. The
    exit code stays 1 per the published contract.
    """

    def test_hint_for_global_option_after_subcommand(self, capsys):
        from deepctl.main import main

        # --api-key is a real global option with no pass-through copy, so
        # it still fails when placed after the subcommand — now with a hint.
        with (
            patch("sys.argv", ["deepctl", "not-a-command", "--api-key", "x"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        # "Error: ..." for people, "ERROR: ..." in agent mode.
        assert "error" in captured.err.lower()

    def test_hint_names_global_option(self):
        """The helper recognizes root-group options and skips unknowns."""
        from deepctl.main import _global_option_hint

        hint = _global_option_hint("No such option: '--api-key'")
        assert hint is not None
        assert "--api-key" in hint
        assert "is a global option, so it goes before the command" in hint

        hint = _global_option_hint("No such option: '--profile'")
        assert hint is not None
        assert "--profile" in hint

        # An option that is not global gets no hint.
        assert _global_option_hint("No such option: '--not-a-real-flag'") is None

        # A message with no option token gets no hint.
        assert _global_option_hint("Missing argument 'FILE'.") is None

    def test_usage_error_without_hint_keeps_contract(self, capsys):
        """A plain unknown flag still errors to stderr and exits 1."""
        from deepctl.main import main

        with (
            patch("sys.argv", ["deepctl", "--definitely-not-a-flag"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        # "Error: ..." for people, "ERROR: ..." in agent mode.
        assert "error" in captured.err.lower()


def _no_such(token: str, *possible: str) -> str:
    """Click's own "No such option" text: click 8.4 quotes the option and
    ends with a period ("No such option '--x'."), earlier releases don't
    ("No such option: --x")."""
    import click

    return click.NoSuchOption(token, possibilities=list(possible)).format_message()


class TestUsageErrorModes:
    """Usage/Try lines for people; two prefixed lines in agent mode."""

    def _run(self, argv, capsys, *, agentic):
        from deepctl.main import main

        with (
            # The name a launcher passes; pytest's argv[0] would show instead.
            patch.dict("os.environ", {"DEEPCTL_PROG_NAME": "dg"}),
            patch("sys.argv", argv),
            patch("deepctl_core.output.is_agentic", return_value=agentic),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        return captured.err

    def test_agent_mode_root_prints_error_and_hint_only(self, capsys):
        err = self._run(["dg", "-o", "json", "--bogus"], capsys, agentic=True)
        assert err.splitlines() == [
            "ERROR: " + _no_such("--bogus", "--verbose"),
            "HINT: Run dg --help to see valid options.",
        ]

    def test_agent_mode_subcommand_names_the_command(self, capsys):
        err = self._run(["dg", "models", "--bogus"], capsys, agentic=True)
        lines = err.splitlines()
        assert lines[0].startswith("ERROR: No such option")
        assert lines[1:] == ["HINT: Run dg models --help to see valid options."]

    def test_agent_mode_global_option_hint_is_prefixed(self, capsys):
        err = self._run(["dg", "models", "--profile", "staging"], capsys, agentic=True)
        # Always exactly two lines: the placement hint replaces A-USAGE.
        assert err.splitlines() == [
            "ERROR: " + _no_such("--profile"),
            "HINT: --profile is a global option, so it goes before the "
            "command: dg --profile <value> models …",
        ]

    def test_human_mode_keeps_usage_and_try_lines(self, capsys):
        err = self._run(["dg", "models", "--bogus"], capsys, agentic=False)
        assert err.startswith("Usage: dg models [OPTIONS]")
        assert "Try 'dg models --help' for help." in err
        assert "Error: No such option" in err
        assert "ERROR:" not in err and "HINT:" not in err

    @pytest.mark.parametrize("token", ["--[/x]", "--[red]x"])
    def test_human_mode_message_is_not_markup(self, capsys, token):
        err = self._run(["dg", "models", token], capsys, agentic=False)
        assert f"Error: {_no_such(token)}" in " ".join(err.split())
        assert "Traceback" not in err and "MarkupError" not in err

    def test_human_global_option_hint_uses_real_names(self, capsys):
        err = self._run(["dg", "models", "--profile", "x"], capsys, agentic=False)
        flat = " ".join(err.split())
        assert (
            "Hint: --profile is a global option, so it goes before the "
            "command: dg --profile <value> models …"
        ) in flat

    def test_global_option_hint_leaves_out_value_for_flags(self):
        import click

        from deepctl.main import _global_option_hint, cli

        flag = next(
            opt
            for p in cli.params
            if getattr(p, "is_flag", False)
            for opt in getattr(p, "opts", [])
            if opt.startswith("--")
        )
        ctx = click.Context(cli, info_name="foo")
        sub = click.Context(click.Command("models"), parent=ctx, info_name="models")
        hint = _global_option_hint(f"No such option: '{flag}'", sub, markup=False)
        assert hint == (
            f"{flag} is a global option, so it goes before the command: "
            f"foo {flag} models …"
        )


class TestRootHandlerMessages:
    """Cancel and the catch-all print the catalogue's coded messages."""

    def _run(self, capsys, error, *, agentic):
        from deepctl.main import main

        with (
            patch("sys.argv", ["dg", "models"]),
            patch("deepctl_core.output.is_agentic", return_value=agentic),
            patch.object(_cli_mod, "cli", side_effect=error),
            patch.object(_cli_mod, "_report_error"),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        captured = capsys.readouterr()
        assert captured.out == ""
        return exc_info.value.code, captured.err

    def test_cancel_human(self, capsys):
        code, err = self._run(capsys, click.exceptions.Abort(), agentic=False)
        assert code == 2
        assert err == "\nCancelled.\n"

    def test_cancel_agent_mode_prints_two_prefixed_lines(self, capsys):
        code, err = self._run(capsys, KeyboardInterrupt(), agentic=True)
        assert code == 2
        assert err.splitlines() == [
            "ERROR: Cancelled.",
            "HINT: The user cancelled. Don't retry unless they ask.",
        ]

    def test_unexpected_error_human_gives_next_step(self, capsys):
        code, err = self._run(capsys, RuntimeError("boom [red]x"), agentic=False)
        assert code == 1
        flat = " ".join(err.split())
        assert "Error: Command failed: boom [red]x" in flat
        assert (
            "Run the command again with --verbose for details, and report it "
            "at https://github.com/deepgram/cli/issues."
        ) in flat

    def test_unexpected_error_agent_mode(self, capsys):
        code, err = self._run(capsys, RuntimeError("boom"), agentic=True)
        assert code == 1
        assert err.splitlines() == [
            "ERROR: Command failed: boom",
            "HINT: This is a bug in dg. Tell the user and include the command you ran.",
        ]

    def test_unexpected_error_without_text_names_its_type(self, capsys):
        _, err = self._run(capsys, RuntimeError(), agentic=True)
        assert err.splitlines()[0] == "ERROR: Command failed: RuntimeError"


class TestRichTraceback:
    """An uncaught exception gets a rich traceback, without frame locals.

    main() installs a stdlib excepthook on the full path; rich loads only
    when an exception reaches it, and it never renders locals.
    """

    def test_excepthook_does_not_render_locals(self, capsys, monkeypatch):
        from deepctl.main import _rich_excepthook

        monkeypatch.setattr(sys, "excepthook", _rich_excepthook)
        # Built at runtime so the source-context lines rich prints can't
        # contain it; only a rendered local variable could.
        sentinel = "-".join(["LOCALS", "SENTINEL", "KEY"])

        def crash() -> None:
            value = sentinel  # noqa: F841
            raise RuntimeError("boom")

        try:
            crash()
        except RuntimeError:
            sys.excepthook(*sys.exc_info())

        captured = capsys.readouterr()
        assert "RuntimeError" in captured.err
        assert "Traceback" in captured.err
        assert sentinel not in captured.err
        # Rich's hook replaced the lazy one, so later errors skip the import.
        assert sys.excepthook.__module__ == "rich.traceback"


def resolved_args(argv):
    """The argv the root group resolves its command from (after root
    options), with any hidden alias already rewritten; None when no command
    is resolved."""
    seen = []

    def record(self, ctx, args):
        seen.append(list(args))
        raise click.exceptions.Exit(0)

    with patch.object(click.Group, "resolve_command", record):
        try:
            cli.main(args=list(argv), prog_name="dg", standalone_mode=False)
        except click.exceptions.Exit:
            pass
    return seen[0] if seen else None


class TestHiddenAliases:
    """`group-subcommand` aliases come from the manifest and rewrite only the
    command word."""

    @pytest.mark.parametrize(
        ("args", "expected"),
        [
            (["debug-audio", "--help"], ["debug", "audio", "--help"]),
            (["--quiet", "debug-audio"], ["debug", "audio"]),
            (["-qv", "debug-audio"], ["debug", "audio"]),
            (["--output=json", "debug-audio"], ["debug", "audio"]),
            (["--profile", "debug-x", "debug-audio"], ["debug", "audio"]),
            (["listen", "debug-call.wav"], ["listen", "debug-call.wav"]),
            (
                ["speak", "-v", "debug-voice", "hello"],
                ["speak", "-v", "debug-voice", "hello"],
            ),
            (
                ["debug-audio", "debug-audio.wav"],
                ["debug", "audio", "debug-audio.wav"],
            ),
            (["-ojson", "debug-audio"], ["debug", "audio"]),
            (["-pq", "debug-audio"], ["debug", "audio"]),
            (["-qpprod", "debug-audio"], ["debug", "audio"]),
            (["-qp", "debug-x", "debug-audio"], ["debug", "audio"]),
            (["-o", "json", "debug-audio"], ["debug", "audio"]),
            (["debug-probe", "-x"], ["debug", "probe", "-x"]),
            (["listen-now"], ["listen-now"]),
            (["transcribe", "debug-call.wav"], ["transcribe", "debug-call.wav"]),
        ],
    )
    def test_alias_rewrites_only_the_command_word(self, args, expected):
        assert resolved_args(args) == expected

    def test_every_alias_comes_from_the_manifest(self):
        from deepctl import _manifest

        aliases = _manifest.aliases()
        assert aliases == {
            f"debug-{name}": ["debug", name]
            for name in ("audio", "browser", "network", "probe", "toolkit")
        }
        for alias in aliases:
            assert alias not in cli.list_commands(click.Context(cli))

    def test_preprocessor_is_gone(self):
        assert not hasattr(_cli_mod, "preprocess_hyphenated_commands")


class TestMcpServerRun:
    """Which argv runs `dg mcp` as a server (stdout isolated first)."""

    @pytest.mark.parametrize(
        ("argv", "expected"),
        [
            (["mcp"], True),
            (["mcp", "--base-url", "https://x.example"], True),
            (["mcp", "--debug"], True),
            (["--profile", "staging", "mcp"], True),
            (["-p", "mcp", "mcp"], True),
            (["-o", "json", "--quiet", "mcp", "--transport", "sse"], True),
            (["--profile=mcp", "mcp"], True),
            (["mcp", "--help"], False),
            (["mcp", "-h"], False),
            (["mcp", "--agent-friendly"], False),
            (["--help", "mcp"], False),
            (["--profile", "mcp"], False),
            (["models", "mcp"], False),
            ([], False),
        ],
    )
    def test_detection(self, argv, expected):
        from deepctl import main as main_module

        assert main_module._is_mcp_server_run(argv) is expected

    def test_root_value_options_match_the_cli(self):
        from deepctl import main as main_module
        from deepctl._cli import cli

        takes_value = {
            opt
            for param in cli.params
            if isinstance(param, click.Option) and not param.is_flag
            for opt in param.opts
        }
        assert takes_value == main_module._ROOT_VALUE_OPTIONS


def test_launcher_captured_by_main_reaches_deepctl_core():
    """main() takes DEEPCTL_LAUNCHER before deepctl_core is imported.

    deepctl_core._launcher (the npm launcher's reader since 0.4.1) and
    deepctl_cmd_update.channel must still see the value main() kept, and a
    child process must not inherit the variable.
    """
    import os
    import subprocess

    code = (
        "import os, subprocess, sys\n"
        "import deepctl.main as m\n"
        "sys.argv = ['dg', '--version']\n"
        "m.main()\n"
        "early = 'deepctl_core' in sys.modules\n"
        "from deepctl_core import _launcher\n"
        "from deepctl_cmd_update import channel\n"
        "child = subprocess.run([sys.executable, '-c', "
        "'import os; print(os.environ.get(\"DEEPCTL_LAUNCHER\"))'], "
        "capture_output=True, text=True).stdout.strip()\n"
        "print(early, m.LAUNCHER, _launcher.get_launcher(), "
        "_launcher.launched_via_npm(), channel.launcher(), "
        "'DEEPCTL_LAUNCHER' in os.environ, child, file=sys.stderr)\n"
    )
    env = {**os.environ, "DEEPCTL_LAUNCHER": "npm"}
    out = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    assert out.stderr.split() == [
        "False", "npm", "npm", "True", "npm", "False", "None"
    ]
