"""Example plugin command for deepctl."""

from typing import Any

from deepctl_core import AuthManager, BaseCommand, Config, DeepgramClient
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


class ExampleCommand(BaseCommand):
    """Example plugin command demonstrating the plugin system."""

    name = "example"
    help = "Example plugin command demonstrating the plugin system"
    short_help = "Example plugin command"

    # This example command doesn't require authentication
    requires_auth = False
    requires_project = False
    ci_friendly = True

    def get_arguments(self) -> list[dict[str, Any]]:
        """Get command arguments and options."""
        return [
            {
                "names": ["--greeting", "-g"],
                "help": "Custom greeting message",
                "type": str,
                "default": "Hello",
                "required": False,
                "is_option": True,
            },
            {
                "names": ["--name", "-n"],
                "help": "Name to greet",
                "type": str,
                "default": "World",
                "required": False,
                "is_option": True,
            },
            {
                "names": ["--show-info"],
                "help": "Show plugin system information",
                "is_flag": True,
                "is_option": True,
            },
        ]

    def handle(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> Any:
        """Handle the example command."""
        greeting = kwargs.get("greeting", "Hello")
        name = kwargs.get("name", "World")
        show_info = kwargs.get("show_info", False)

        # Show plugin information if requested
        if show_info:
            # Display greeting
            console.print(f"\n[bold blue]{greeting}, {name}![/bold blue]\n")
            self._show_plugin_info()
            # Return None to avoid additional output
            return None

        # For normal operation, return structured data for output formatting
        # Don't print directly unless in help mode
        if kwargs.get("help_mode", False):
            self._show_help()
            return None

        # Return result for output formatting
        from .models import ExampleResult

        return ExampleResult(
            message=f"{greeting}, {name}!",
            plugin="deepctl-plugin-example",
            version="0.1.0",
            greeting=greeting,
            name=name,
        )

    def _show_help(self) -> None:
        """Show help information about the plugin."""
        # Create help panel
        help_content = """
This is an example plugin for deepctl demonstrating how to create
custom commands that integrate seamlessly with the CLI.

[bold]Usage:[/bold]
  deepctl example [OPTIONS]

[bold]Options:[/bold]
  -g, --greeting TEXT    Custom greeting message [default: Hello]
  -n, --name TEXT        Name to greet [default: World]
  --show-info            Show plugin system information

[bold]Examples:[/bold]
  # Basic usage
  deepctl example

  # Custom greeting
  deepctl example --greeting "Howdy" --name "Partner"

  # Show plugin info
  deepctl example --show-info
"""

        panel = Panel(
            help_content.strip(),
            title="[bold]Example Plugin Help[/bold]",
            border_style="blue",
            padding=(1, 2),
        )
        console.print(panel)

    def _show_plugin_info(self) -> None:
        """Show information about the plugin system."""
        console.print("[bold]Plugin System Information[/bold]\n")

        # Create info table
        table = Table(show_header=True, header_style="bold blue")
        table.add_column("Property", style="cyan")
        table.add_column("Value")

        table.add_row("Plugin Name", "deepctl-plugin-example")
        table.add_row("Version", "0.1.0")
        table.add_row("Command Name", self.name)
        table.add_row("Entry Point", "deepctl.commands")
        table.add_row("Base Class", "deepctl_core.BaseCommand")
        table.add_row("Requires Auth", str(self.requires_auth))
        table.add_row("Requires Project", str(self.requires_project))

        console.print(table)
        console.print()

        # Show how plugin loading works
        info_panel = Panel(
            """[bold]How Plugin Loading Works:[/bold]

1. Plugins register via entry points in pyproject.toml
2. The CLI's PluginManager discovers them at runtime
3. Plugins must inherit from BaseCommand
4. When installed alongside the CLI, they become available

To create your own plugin:
- Copy this example package
- Modify the command class
- Update pyproject.toml metadata
- Install with: pip install -e ./your-plugin/""",
            title="[bold]Plugin Development Guide[/bold]",
            border_style="green",
            padding=(1, 2),
        )
        console.print(info_panel)
