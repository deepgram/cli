"""Plugin manager for deepctl command discovery and loading."""

import importlib
import inspect
from pathlib import Path
from typing import Dict, List, Type, Any
from importlib import metadata
from .models import PluginInfo, ErrorResult

import click
from rich.console import Console

console = Console()


class PluginManager:
    """Manager for loading and organizing CLI plugins/commands."""

    def __init__(self):
        """Initialize plugin manager."""
        self.loaded_plugins: Dict[str, Any] = {}
        self.command_classes: Dict[str, Type] = {}

    def load_plugins(self, cli_group: click.Group) -> None:
        """Load all plugins into the CLI group.

        Args:
            cli_group: Main CLI group to add commands to
        """
        # Load built-in commands
        self._load_builtin_commands(cli_group)

        # Load external plugins
        self._load_external_plugins(cli_group)

    def _load_builtin_commands(self, cli_group: click.Group) -> None:
        """Load built-in commands from the commands package."""
        from ..commands.base import BaseCommand

        commands_dir = Path(__file__).parent.parent / "commands"

        # Skip __init__.py and base.py
        command_files = [
            f for f in commands_dir.glob("*.py")
            if f.name not in ("__init__.py", "base.py")
        ]

        for command_file in command_files:
            try:
                # Import the module
                module_name = f"deepctl.commands.{command_file.stem}"
                module = importlib.import_module(module_name)

                # Find command classes
                for name, obj in inspect.getmembers(module):
                    if (inspect.isclass(obj) and
                        issubclass(obj, BaseCommand) and
                            obj is not BaseCommand):

                        # Create command instance
                        command_instance = obj()

                        # Create Click command
                        click_command = self._create_click_command(
                            command_instance)

                        # Add to CLI group
                        cli_group.add_command(click_command)

                        # Store reference
                        self.command_classes[command_instance.name] = obj

            except Exception as e:
                console.print(
                    f"[red]Error loading command from {command_file}:[/red] {e}")

    def _load_external_plugins(self, cli_group: click.Group) -> None:
        """Load external plugins from entry points."""
        try:
            # Load plugins from entry points using importlib.metadata (modern API)
            entry_points = metadata.entry_points()
            for entry_point in entry_points.select(group="deepctl.commands"):
                try:
                    # Load the plugin class
                    plugin_class = entry_point.load()

                    # Create instance
                    plugin_instance = plugin_class()

                    # Create Click command
                    click_command = self._create_click_command(plugin_instance)

                    # Add to CLI group
                    cli_group.add_command(click_command)

                    # Store reference
                    self.loaded_plugins[entry_point.name] = plugin_instance

                    console.print(
                        f"[dim]Loaded external plugin:[/dim] {entry_point.name}")

                except Exception as e:
                    console.print(
                        f"[red]Error loading plugin {entry_point.name}:[/red] {e}")

        except Exception as e:
            console.print(f"[red]Error loading external plugins:[/red] {e}")

    def _create_click_command(self, command_instance) -> click.Command:
        """Create a Click command from a BaseCommand instance.

        Args:
            command_instance: Instance of BaseCommand

        Returns:
            Click command object
        """
        # Create the command function
        def command_func(**kwargs):
            # Pass CLI context and arguments to the command
            ctx = click.get_current_context()
            return command_instance.execute(ctx, **kwargs)

        # Set function name and docstring
        command_func.__name__ = command_instance.name.replace("-", "_")
        command_func.__doc__ = command_instance.help

        # Create base command
        cmd = click.Command(
            name=command_instance.name,
            callback=command_func,
            help=command_instance.help,
            short_help=command_instance.short_help or command_instance.help
        )

        # Add arguments and options
        cmd = self._add_command_arguments(cmd, command_instance)

        return cmd

    def _add_command_arguments(self, cmd: click.Command, command_instance) -> click.Command:
        """Add arguments and options to a Click command.

        Args:
            cmd: Click command
            command_instance: BaseCommand instance

        Returns:
            Updated Click command
        """
        # Get arguments from command instance
        if hasattr(command_instance, 'get_arguments'):
            arguments = command_instance.get_arguments()

            # Add arguments in reverse order (Click applies decorators in reverse)
            for arg in reversed(arguments):
                if arg.get('is_option', False):
                    # Add as option
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
                    # Add as argument
                    cmd = click.argument(
                        arg.get('name', ''),
                        type=arg.get('type', str),
                        required=arg.get('required', True),
                        nargs=arg.get('nargs', 1)
                    )(cmd)

        return cmd

    def get_command_list(self) -> List[str]:
        """Get list of loaded command names.

        Returns:
            List of command names
        """
        return list(self.command_classes.keys()) + list(self.loaded_plugins.keys())

    def get_command_info(self, command_name: str) -> PluginInfo | ErrorResult:
        """Get information about a specific command.

        Args:
            command_name: Name of the command

        Returns:
            Command information
        """
        if command_name in self.command_classes:
            cmd_class = self.command_classes[command_name]
            instance = cmd_class()
            return PluginInfo(
                name=instance.name,
                help=instance.help,
                short_help=instance.short_help,
                type="builtin",
                module=cmd_class.__module__,
            )

        elif command_name in self.loaded_plugins:
            instance = self.loaded_plugins[command_name]
            return PluginInfo(
                name=instance.name,
                help=instance.help,
                short_help=instance.short_help,
                type="external",
                module=instance.__class__.__module__,
            )

        else:
            return ErrorResult(error=f"Command '{command_name}' not found")

    def reload_plugins(self, cli_group: click.Group) -> None:
        """Reload all plugins.

        Args:
            cli_group: Main CLI group
        """
        # Clear existing plugins
        self.loaded_plugins.clear()
        self.command_classes.clear()

        # Remove commands from CLI group
        cli_group.commands.clear()

        # Reload plugins
        self.load_plugins(cli_group)

    def validate_plugin(self, plugin_class: Type) -> bool:
        """Validate that a plugin class is properly implemented.

        Args:
            plugin_class: Plugin class to validate

        Returns:
            True if valid, False otherwise
        """
        from ..commands.base import BaseCommand

        try:
            # Check if it's a subclass of BaseCommand
            if not issubclass(plugin_class, BaseCommand):
                return False

            # Check required attributes
            instance = plugin_class()

            required_attrs = ["name", "help"]
            for attr in required_attrs:
                if not hasattr(instance, attr) or not getattr(instance, attr):
                    return False

            # Check if execute method exists
            if not hasattr(instance, "execute"):
                return False

            return True

        except Exception:
            return False

    def discover_plugin_directories(self) -> List[Path]:
        """Discover directories that might contain plugins.

        Returns:
            List of plugin directories
        """
        plugin_dirs = []

        # Built-in plugins directory
        builtin_plugins = Path(__file__).parent.parent / "plugins"
        if builtin_plugins.exists():
            plugin_dirs.append(builtin_plugins)

        # User plugins directory
        from .config import Config
        config = Config()
        user_plugins = config.config_dir / "plugins"
        if user_plugins.exists():
            plugin_dirs.append(user_plugins)

        return plugin_dirs
