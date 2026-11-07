"""Main entry point for deepctl."""

from __future__ import annotations

import importlib.metadata
import sys

import click
from deepctl_core import (
    Config,
    TimingContext,
    enable_timing,
    print_timing_summary,
    setup_output,
)
from deepctl_core.output import stderr_console
from rich.traceback import install

# Install rich traceback for better error messages, without frame locals.
install(show_locals=False)

# Diagnostics go to stderr so stdout carries only the requested payload -- a
# crash or usage error must not corrupt `dg -o json ...` for a script piping
# stdout into jq. Same console the command layer uses via print_error(), so
# root-level and command-level diagnostics format identically (including the
# no-color handling for agentic/CI callers).
console = stderr_console


def _record_install_method_cb(
    ctx: click.Context, param: click.Parameter, value: str | None
) -> None:
    """Write the installation method to config. Called by install scripts."""
    if not value or ctx.resilient_parsing:
        return
    try:
        config = Config()
        config._set_config_value("update.installation_method", value)
        config.save(implicit=True)
    except OSError as exc:
        # A failed write must not look like success to the install script.
        console.print(f"[red]Error:[/red] could not save the config file: {exc}")
        # sys.exit, not ctx.exit: main() runs Click with standalone_mode=False,
        # where ctx.exit(1) would only become an ignored return value.
        sys.exit(1)
    except Exception:
        pass
    ctx.exit()


def _root_flag_opts() -> set[str]:
    """Root options that take no value: flags, plus `--help`."""
    opts = {"--help"}
    for param in cli.params:
        if isinstance(param, click.Option) and param.is_flag:
            opts.update(param.opts)
            opts.update(param.secondary_opts)
    return opts


def _takes_next_token(arg: str, flag_opts: set[str]) -> bool:
    """Whether root option `arg` takes the following token as its value.

    Mirrors click: flags take nothing, `--opt=value` and `-Xvalue` carry
    their value, and a short cluster such as `-qv` or `-qpprod` is read
    letter by letter until the first option that takes a value.
    """
    if arg in flag_opts or "=" in arg:
        return False
    if arg.startswith("--") or len(arg) <= 2:
        return True
    letters = arg[1:]
    for index, letter in enumerate(letters):
        if f"-{letter}" in flag_opts:
            continue
        # The first option that takes a value: anything after it is the
        # attached value; nothing after it means the next token is.
        return index == len(letters) - 1
    return False


def preprocess_hyphenated_commands(args: list[str]) -> list[str]:
    """Convert hyphenated commands to nested commands.

    This function looks for commands in the format 'group-subcommand' and
    converts them to the nested format Click expects (e.g., 'group
    subcommand').

    Args:
        args: Command line arguments

    Returns:
        Modified arguments with hyphenated commands converted to nested format
    """
    if not args:
        return args

    # Get all registered commands to know which are groups
    from importlib import metadata

    group_commands = set()

    # Discover group commands from entry points
    try:
        entry_points = metadata.entry_points()
        for entry_point in entry_points.select(group="deepctl.commands"):
            # We'll need to check if it's a group, but for now we'll use a
            # heuristic
            subcommand_group = f"deepctl.subcommands.{entry_point.name}"
            try:
                subcommand_eps = list(entry_points.select(group=subcommand_group))
                if subcommand_eps:
                    group_commands.add(entry_point.name)
            except Exception:
                pass
    except Exception:
        pass

    flag_opts = _root_flag_opts()

    # Walk the root options to find the command token. Only that token is
    # expanded; everything after it belongs to the command and is kept
    # verbatim (`dg listen debug-call.wav` must not be split).
    new_args: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            break
        if arg.startswith("-"):
            new_args.append(arg)
            i += 1
            # An option that takes a value consumes the next token too,
            # unless the value is attached (`--opt=value`, `-Xvalue`).
            if (
                _takes_next_token(arg, flag_opts)
                and i < len(args)
                and not args[i].startswith("-")
            ):
                new_args.append(args[i])
                i += 1
            continue

        group_name, sep, subcommand = arg.partition("-")
        if sep and group_name in group_commands:
            new_args.extend([group_name, subcommand])
            i += 1
        break

    return new_args + args[i:]


def _root_option_tokens(args: list[str]) -> list[str]:
    """The option tokens before the subcommand (values skipped)."""
    flag_opts = _root_flag_opts()
    tokens: list[str] = []
    i = 0
    while i < len(args) and args[i].startswith("-"):
        arg = args[i]
        tokens.append(arg)
        i += 1
        if _takes_next_token(arg, flag_opts):
            i += 1  # skip the option's value
    return tokens


def _no_telemetry_cb(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    """Turn telemetry off for this run. Eager, so root `--help` reflects it."""
    if not value or ctx.resilient_parsing:
        return
    try:
        from deepctl_telemetry import disable_for_run

        disable_for_run()
    except Exception:
        pass


def _init_telemetry(config: Config) -> None:
    """Decide telemetry from the resolved Config. Never loads the SDK."""
    try:
        from deepctl_telemetry import init_telemetry

        init_telemetry(config)
    except Exception:
        pass


def _report_error(exc: BaseException) -> None:
    """Send an error report if telemetry is on (the SDK loads only here).

    A Ctrl-C while the report is sent is still a user cancellation: exit 2.
    """
    try:
        from deepctl_telemetry import report_exception

        report_exception(exc)
    except KeyboardInterrupt:
        _safe_console_print("\n[yellow]Stopped sending the error report.[/yellow]")
        sys.exit(2)
    except Exception:
        pass


def _argv_config_path(args: list[str]) -> str | None:
    """`--config` from the root arguments, only for the notice on root `--help`.

    Root `--help` is eager and fires before the root callback resolves
    `--config`; every other path uses the parsed value. Accepts
    `--config X`, `--config=X`, `-c X`, `-cX` and clusters such as `-qcX`.
    When the option repeats, the last value wins, as in click.
    """
    flag_opts = _root_flag_opts()
    found: str | None = None
    i = 0
    while i < len(args) and args[i].startswith("-"):
        arg = args[i]
        i += 1
        if arg.startswith("--config="):
            found = arg.split("=", 1)[1]
        elif arg == "--config":
            found = args[i] if i < len(args) else None
        elif not arg.startswith("--"):
            for index, letter in enumerate(arg[1:]):
                if f"-{letter}" in flag_opts:
                    continue
                if letter == "c":
                    rest = arg[index + 2 :]
                    found = rest or (args[i] if i < len(args) else None)
                break
        if _takes_next_token(arg, flag_opts):
            i += 1
    return found


# Create CLI group
@click.group(name="deepctl")
@click.version_option(
    version=importlib.metadata.version("deepctl"), prog_name="deepctl"
)
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=True),
    help="Path to configuration file",
)
@click.option(
    "--profile",
    "-p",
    help="Configuration profile to use",
)
@click.option(
    "--base-url",
    help=(
        "Override the API base URL (e.g. https://api.eu.deepgram.com). "
        "Hosts other than Deepgram API hosts must be listed in "
        "api.allowed_hosts. "
        "REST and WebSocket endpoints are derived from this host. "
        "Also settable via the DEEPGRAM_BASE_URL environment variable."
    ),
)
@click.option(
    "--api-key",
    help=(
        "Deepgram API key to use, overriding login/profile/env credentials. "
        "Intended for testing and CI."
    ),
)
@click.option(
    "--output",
    "-o",
    type=click.Choice(["json", "yaml", "table", "csv"], case_sensitive=False),
    help="Output format",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Suppress non-essential output",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Enable verbose output",
)
@click.option(
    "--timing",
    is_flag=True,
    help="Show performance timing information",
)
@click.option(
    "--timing-detailed",
    is_flag=True,
    help="Show detailed performance timing information",
)
@click.option(
    "--no-telemetry",
    is_flag=True,
    is_eager=True,
    expose_value=False,
    callback=_no_telemetry_cb,
    help="No effect: this version of dg sends no telemetry",
)
@click.option(
    "--non-interactive",
    is_flag=True,
    is_eager=True,
    expose_value=False,
    help="Skip interactive prompts; use defaults for any optional features.",
)
@click.option(
    "--record-install-method",
    is_eager=True,
    expose_value=False,
    hidden=True,
    callback=_record_install_method_cb,
    metavar="METHOD",
    help="Internal: record the installation method used by the install script",
)
@click.pass_context
def cli(
    ctx: click.Context,
    config: str | None,
    profile: str | None,
    base_url: str | None,
    api_key: str | None,
    output: str | None,
    quiet: bool,
    verbose: bool,
    timing: bool,
    timing_detailed: bool,
) -> None:
    """\b
    ████████████████
    ██████████████████
    ████████████████████
    █████████████████████
    ███████      ████████
    ███████       ███████
                 ████████
          ███████████████
        ████████████████
      ████████████████
    ████████████████

    deepctl — Official Deepgram CLI
    STT · TTS · Audio Intelligence"""

    # Enable timing if requested
    if timing or timing_detailed:
        enable_timing()

    with TimingContext("cli_initialization"):
        # Initialize configuration. A --base-url flag overrides the
        # configured/env base URL for this run only; Config records that it
        # came from the command line, so it is never saved and gets
        # credentials only if the host is trusted.
        ctx.ensure_object(dict)
        ctx.obj["config"] = Config(
            config_path=config, profile=profile, base_url=base_url
        )
        # Global --api-key passthrough (highest-precedence explicit credential).
        ctx.obj["api_key"] = api_key
        ctx.obj["timing"] = timing or timing_detailed
        ctx.obj["timing_detailed"] = timing_detailed

        # Setup output formatting
        setup_output(
            format_type=output or "default",
            quiet=quiet,
            verbose=verbose,
        )

        # Telemetry is decided here, after option parsing, so --config and
        # --no-telemetry are honored.
        _init_telemetry(ctx.obj["config"])


# Load commands from entry points
def load_commands() -> None:
    """Load commands from package entry points."""
    # Use the plugin manager to load all commands
    from deepctl_core import PluginManager

    with TimingContext("plugin_loading"):
        plugin_manager = PluginManager()
        plugin_manager.load_plugins(cli)


# Load commands when module is imported
load_commands()


def _global_option_hint(message: str) -> str | None:
    """Build a placement hint when a 'No such option' names a global option.

    Global options belong before the subcommand (``dg -o json models``).
    When one is placed after the subcommand instead (``dg models --profile
    staging``), Click reports "No such option" with no explanation, which
    reads as if the option does not exist at all. This looks the failing
    option up on the root group and, when it is a global one, returns a
    hint showing the working placement.
    """
    import re

    match = re.search(r"[Nn]o such option:?\s*'?(--?[A-Za-z][\w-]*)'?", message)
    if not match:
        return None
    token = match.group(1)

    for param in cli.params:
        opts = list(getattr(param, "opts", [])) + list(
            getattr(param, "secondary_opts", [])
        )
        if token in opts:
            takes_value = not getattr(param, "is_flag", False)
            usage = f"{token} <value>" if takes_value else token
            return (
                f"[yellow]Hint: '{token}' is a global option and must come "
                f"before the subcommand, for example: "
                f"dg {usage} <command> ...[/yellow]"
            )
    return None


def _safe_console_print(message: str) -> None:
    """Print a diagnostic to stderr, tolerating a closed/broken stream.

    When `dg` runs as an MCP server, the host can close stdio before the
    process finishes. A write to the closed stream raises ``BrokenPipeError``
    (or rich's ``ValueError: I/O operation on closed file``); there is nowhere
    left to report the message, so swallow it rather than crash on exit.
    """
    try:
        console.print(message)
    except (BrokenPipeError, OSError, ValueError):
        pass


# Error handlers that never raise on a character the encoding lacks.
# "strict" and "surrogateescape" (a C locale) both raise.
_SAFE_ERRORS = frozenset(
    {"backslashreplace", "replace", "ignore", "namereplace", "xmlcharrefreplace"}
)


def safe_output_streams() -> None:
    """Have stdout and stderr escape what their encoding can't write.

    A redirected stream on Windows uses the ANSI code page (cp1252, say),
    which has no block art: writing the --help banner raised
    UnicodeEncodeError and ended the command. Such a stream now writes
    ``\\u2588`` instead. A UTF-8 stream is left as it is, and the encoding
    never changes, so a real console keeps what Python chose for it.
    """
    import codecs
    import io

    for stream in (sys.stdout, sys.stderr):
        if not isinstance(stream, io.TextIOWrapper):
            continue  # None, or a stream that can't be reconfigured
        try:
            encoding = codecs.lookup(stream.encoding or "").name
            if encoding == "utf-8" or stream.errors in _SAFE_ERRORS:
                continue
            stream.reconfigure(errors="backslashreplace")
        except (LookupError, OSError, ValueError):
            continue  # an unknown encoding, or a closed stream


def main() -> None:
    """Main entry point for the CLI."""
    safe_output_streams()
    try:
        # Check if timing was requested and enable it early
        args = sys.argv[1:]  # Skip the program name
        timing_requested = "--timing" in args or "--timing-detailed" in args
        detailed_timing = "--timing-detailed" in args
        quiet_requested = "--quiet" in args or "-q" in args

        if timing_requested:
            enable_timing()

        # Install the --help telemetry footer. Telemetry itself is decided in
        # the root callback, after option parsing. `--no-telemetry` is also
        # applied here so it holds even after an eager `--help` before it.
        try:
            from deepctl_telemetry import disable_for_run, install_help_notice

            if "--no-telemetry" in _root_option_tokens(args):
                disable_for_run()
            install_help_notice(lambda: Config(config_path=_argv_config_path(args)))
        except Exception:
            pass

        # Start background update checks (non-blocking)
        try:
            from deepctl_cmd_update.startup_check import (
                check_and_notify,
                print_pending_notification,
            )

            check_and_notify(quiet=quiet_requested)
        except ImportError:
            check_and_notify = None  # type: ignore[assignment]
            print_pending_notification = None  # type: ignore[assignment]

        try:
            from deepctl_cmd_update.plugin_update_check import (
                check_plugins_and_notify,
                print_pending_plugin_notifications,
            )

            check_plugins_and_notify(quiet=quiet_requested)
        except ImportError:
            print_pending_plugin_notifications = None  # type: ignore[assignment]

        # Start background AI CLI detection (non-blocking)
        try:
            from deepctl_cmd_skills.startup_check import (
                check_and_notify as skills_check_and_notify,
            )
            from deepctl_cmd_skills.startup_check import (
                print_pending_notification as print_pending_skills_notification,
            )

            skills_check_and_notify(quiet=quiet_requested)
        except ImportError:
            print_pending_skills_notification = None  # type: ignore[assignment]

        with TimingContext("total_execution"):
            with TimingContext("argument_preprocessing"):
                # Preprocess arguments to handle hyphenated commands
                processed_args = preprocess_hyphenated_commands(args)

            exit_code = 0
            with TimingContext("cli_execution"):
                try:
                    cli(args=processed_args, standalone_mode=False)
                except SystemExit as exc:
                    # Click calls sys.exit() even in non-standalone mode, so we
                    # cannot let this propagate — the notifications and timing
                    # summary below still need to run. But swallowing the code
                    # outright made every failure exit 0, which is what broke
                    # `if dg -o json keys; then ...` in scripts and CI. Carry it
                    # and re-raise at the end instead.
                    exit_code = exc.code if isinstance(exc.code, int) else 0

        # Print update notifications if available (before timing summary)
        if print_pending_notification is not None:
            print_pending_notification()
        if print_pending_plugin_notifications is not None:
            print_pending_plugin_notifications()
        if print_pending_skills_notification is not None:
            print_pending_skills_notification()

        # Print timing summary if timing was enabled
        if timing_requested:
            print_timing_summary(detailed_timing)

        if exit_code:
            sys.exit(exit_code)

    except (KeyboardInterrupt, click.exceptions.Abort):
        # Click wraps a Ctrl-C/Ctrl-D raised inside cli() in Abort when
        # standalone_mode=False, so a mid-command interrupt arrives here as
        # Abort, not KeyboardInterrupt. Both are user cancellation: exit 2.
        _safe_console_print("\n[yellow]Operation cancelled by user[/yellow]")
        sys.exit(2)
    except click.UsageError as e:
        # Keep the published exit-code contract (1 = error), but explain the
        # one failure mode that reads as a lie: a *global* option placed
        # after the subcommand, where Click says "No such option" about an
        # option that very much exists.
        message = e.format_message()
        _safe_console_print(f"[red]Error: {message}[/red]")
        hint = _global_option_hint(message)
        if hint:
            _safe_console_print(hint)
        sys.exit(1)
    except Exception as e:
        _safe_console_print(f"[red]Error: {e}[/red]")
        _report_error(e)
        sys.exit(1)  # 1 = error; 2 is reserved for user interrupt


if __name__ == "__main__":
    main()
