"""Main entry point for deepctl."""

import importlib.metadata
import sys
from contextlib import contextmanager
from typing import Iterator

import click
from deepctl_core import (
    Config,
    TimingContext,
    enable_timing,
    print_timing_summary,
    setup_output,
)
from rich.console import Console
from rich.traceback import install

# Install rich traceback for better error messages
install(show_locals=True)
console = Console()


def _record_install_method_cb(
    ctx: click.Context, param: click.Parameter, value: str | None
) -> None:
    """Write the installation method to config. Called by install scripts."""
    if not value or ctx.resilient_parsing:
        return
    try:
        config = Config()
        config._set_config_value("update.installation_method", value)
        config.save()
    except Exception:
        pass
    ctx.exit()


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

    # Process arguments
    new_args = []
    i = 0

    while i < len(args):
        arg = args[i]

        # Skip if it's an option (starts with -)
        if arg.startswith("-"):
            new_args.append(arg)
            i += 1
            # If it's an option with a value, include the next arg too
            if i < len(args) and not args[i].startswith("-"):
                new_args.append(args[i])
                i += 1
            continue

        # Check if this could be a hyphenated command
        if "-" in arg and not arg.startswith("-"):
            parts = arg.split("-", 1)
            if len(parts) == 2:
                group_name, subcommand = parts

                # If the first part is a known group command, convert it
                if group_name in group_commands:
                    new_args.extend([group_name, subcommand])
                    i += 1
                    continue

        # Otherwise, keep the argument as-is
        new_args.append(arg)
        i += 1

    return new_args


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
        # Initialize configuration
        ctx.ensure_object(dict)
        ctx.obj["config"] = Config(config_path=config, profile=profile)
        ctx.obj["timing"] = timing or timing_detailed
        ctx.obj["timing_detailed"] = timing_detailed

        # Setup output formatting
        setup_output(
            format_type=output or "default",
            quiet=quiet,
            verbose=verbose,
        )


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


@contextmanager
def _telemetry_transaction() -> "Iterator[None]":
    """Wrap CLI dispatch in a Sentry transaction (no-op when telemetry is off).

    The transaction is named generically ('cli') here. BaseCommand.execute
    renames it to the full Click command path (e.g. 'deepctl debug audio')
    once Click has dispatched — which is the single source of truth for
    the name. Trying to extract a command name from raw sys.argv is unsafe
    because flag values can look like command names ('--output json' makes
    'json' look like a command).
    """
    try:
        import sentry_sdk
    except ImportError:
        yield
        return

    with sentry_sdk.start_transaction(op="cli.command", name="cli"):
        yield


def main() -> None:
    """Main entry point for the CLI."""
    try:
        # Check if timing was requested and enable it early
        args = sys.argv[1:]  # Skip the program name
        timing_requested = "--timing" in args or "--timing-detailed" in args
        detailed_timing = "--timing-detailed" in args
        quiet_requested = "--quiet" in args or "-q" in args

        if timing_requested:
            enable_timing()

        # Initialize phone-home telemetry and install the --help footer.
        # Both are no-ops when the user has opted out via config or env.
        try:
            from deepctl_telemetry import (
                init_telemetry,
                install_help_notice,
            )

            telemetry_config = Config()
            init_telemetry(telemetry_config)
            install_help_notice(telemetry_config)
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

            with TimingContext("cli_execution"):
                with _telemetry_transaction():
                    try:
                        cli(args=processed_args, standalone_mode=False)
                    except SystemExit:
                        # Click calls sys.exit() even in non-standalone mode
                        pass

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

    except KeyboardInterrupt:
        console.print("\n[yellow]Operation cancelled by user[/yellow]")
        sys.exit(2)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(2)


if __name__ == "__main__":
    main()
