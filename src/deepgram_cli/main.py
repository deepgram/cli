"""Main entry point for the Deepgram CLI."""

import sys
from typing import Optional

import click
from rich.console import Console
from rich.traceback import install

from .core.config import Config
from .core.plugin_manager import PluginManager
from .utils.output import setup_output

# Install rich traceback for better error messages
install(show_locals=True)
console = Console()


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
    
    # Initialize plugin manager and load commands
    plugin_manager = PluginManager()
    plugin_manager.load_plugins(cli)


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