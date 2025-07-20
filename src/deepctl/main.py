"""Main entry point for deepctl."""

import sys
from typing import Optional

import click
from rich.console import Console
from rich.traceback import install

from deepctl_core import Config, setup_output

# Install rich traceback for better error messages
install(show_locals=True)
console = Console()


# Create CLI group
@click.group(name="deepctl")
@click.version_option(version="0.1.0", prog_name="deepctl")
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
@click.pass_context
def cli(
    ctx: click.Context,
    config: Optional[str],
    profile: Optional[str],
    output: Optional[str],
    quiet: bool,
    verbose: bool,
) -> None:
    """deepctl - Official Deepgram CLI for speech recognition and audio intelligence."""

    # Initialize configuration
    ctx.ensure_object(dict)
    ctx.obj["config"] = Config(config_path=config, profile=profile)

    # Setup output formatting
    setup_output(
        format_type=output or ctx.obj["config"].get("output.format", "json"),
        quiet=quiet,
        verbose=verbose,
    )


# Load commands from entry points
def load_commands():
    """Load commands from package entry points."""
    # Discover and register commands dynamically
    from importlib import metadata

    entry_points = metadata.entry_points()
    for entry_point in entry_points.select(group="deepctl.commands"):
        try:
            command_class = entry_point.load()
            command_instance = command_class()

            # Create a closure that properly captures the command instance
            def create_command(cmd_instance):
                @cli.command(name=cmd_instance.name, help=cmd_instance.help)
                @click.pass_context
                def cmd(ctx, **kwargs):
                    # Use execute() instead of handle() to get proper output formatting
                    return cmd_instance.execute(ctx, **kwargs)

                # Add arguments to the command
                if hasattr(cmd_instance, 'get_arguments'):
                    for arg in reversed(cmd_instance.get_arguments()):
                        if arg.get('is_option', False):
                            cmd = click.option(
                                *arg.get('names', []),
                                default=arg.get('default'),
                                help=arg.get('help', ''),
                                type=arg.get('type', str),
                                required=arg.get('required', False),
                                is_flag=arg.get('is_flag', False),
                                multiple=arg.get('multiple', False)
                            )(cmd)
                        else:
                            cmd = click.argument(
                                arg.get('name', ''),
                                type=arg.get('type', str),
                                required=arg.get('required', True),
                                nargs=arg.get('nargs', 1)
                            )(cmd)

                return cmd

            # Create the command with proper closure
            create_command(command_instance)

        except Exception as e:
            console.print(
                f"[red]Error loading command {entry_point.name}:[/red] {e}")


# Load commands when module is imported
load_commands()


def main() -> None:
    """Main entry point for the CLI."""
    try:
        cli()
    except KeyboardInterrupt:
        console.print("\n[yellow]Operation cancelled by user[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
