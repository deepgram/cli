"""Main entry point for the Deepgram CLI."""

import sys
from typing import Optional

import click
from rich.console import Console
from rich.traceback import install

from .core.config import Config
from .utils.output import setup_output

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
    from importlib import metadata

    entry_points = metadata.entry_points()
    for entry_point in entry_points.select(group="deepgram_cli.commands"):
        try:
            command_class = entry_point.load()
            command_instance = command_class()

            # Create Click command from our command class
            @cli.command(name=command_instance.name, help=command_instance.help)
            @click.pass_context
            def cmd(ctx, **kwargs):
                from .core.auth import AuthManager
                from .core.client import DeepgramClient

                config = ctx.obj["config"]
                auth_manager = AuthManager(config)
                client = DeepgramClient(config, auth_manager)

                return command_instance.handle(
                    config=config,
                    auth_manager=auth_manager,
                    client=client,
                    **kwargs
                )

            # Add arguments to the command
            if hasattr(command_instance, 'get_arguments'):
                for arg in reversed(command_instance.get_arguments()):
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
