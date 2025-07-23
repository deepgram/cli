"""Plugin manager for deepctl command discovery and loading."""

from importlib import metadata
from pathlib import Path
from typing import Any, cast

import click
from rich.console import Console

from .base_command import BaseCommand
from .base_group_command import BaseGroupCommand
from .models import ErrorResult, PluginInfo

console = Console()


class PluginManager:
    """Manager for loading and organizing CLI plugins/commands."""

    def __init__(self) -> None:
        """Initialize plugin manager."""
        self.loaded_plugins: dict[str, Any] = {}
        self.command_classes: dict[str, type[Any]] = {}

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
        """Load built-in commands from the commands entry point group."""
        try:
            # Load built-in commands from entry points
            entry_points = metadata.entry_points()
            for entry_point in entry_points.select(group="deepctl.commands"):
                try:
                    # Load the command class
                    command_class = entry_point.load()

                    # Create instance
                    command_instance = command_class()

                    # Create Click command
                    click_command = self._create_click_command(
                        command_instance
                    )

                    # Add to CLI group
                    cli_group.add_command(click_command)

                    # Store reference
                    self.command_classes[entry_point.name] = command_class

                    console.print(
                        f"[dim]Loaded built-in command:[/dim] "
                        f"{entry_point.name}"
                    )

                except Exception as e:
                    console.print(
                        f"[red]Error loading command "
                        f"{entry_point.name}:[/red] {e}"
                    )

        except Exception as e:
            console.print(f"[red]Error loading built-in commands:[/red] {e}")

    def _load_external_plugins(self, cli_group: click.Group) -> None:
        """Load external plugins from the plugins entry point group."""
        try:
            # Load plugins from entry points
            entry_points = metadata.entry_points()
            for entry_point in entry_points.select(group="deepctl.plugins"):
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
                        f"[dim]Loaded external plugin:[/dim] "
                        f"{entry_point.name}"
                    )

                except Exception as e:
                    console.print(
                        f"[red]Error loading plugin {entry_point.name}:[/red] "
                        f"{e}"
                    )

        except Exception as e:
            console.print(f"[red]Error loading external plugins:[/red] {e}")

    def _create_click_command(self, command_instance: Any) -> click.Command:
        """Create a Click command from a BaseCommand instance.

        Args:
            command_instance: Instance of BaseCommand

        Returns:
            Click command object
        """
        # Check if this is a group command
        if isinstance(command_instance, BaseGroupCommand) or getattr(
            command_instance, "is_group", False
        ):
            # Create a Click Group for group commands
            return self._create_click_group(command_instance)

        # Create the command function
        def command_func(**kwargs: Any) -> Any:
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
            short_help=command_instance.short_help or command_instance.help,
        )

        # Add arguments and options
        cmd = self._add_command_arguments(cmd, command_instance)

        return cmd

    def _create_click_group(
        self, group_instance: BaseGroupCommand
    ) -> click.Group:
        """Create a Click group from a BaseGroupCommand instance.

        Args:
            group_instance: Instance of BaseGroupCommand

        Returns:
            Click group object
        """
        # Use the group's own method to create the Click group
        if hasattr(group_instance, "get_click_group"):
            group = group_instance.get_click_group()
        else:
            # Fallback for basic group creation
            def group_func(**kwargs: Any) -> Any:
                ctx = click.get_current_context()
                return group_instance.execute(ctx, **kwargs)

            group_func.__name__ = group_instance.name.replace("-", "_")
            group_func.__doc__ = group_instance.help

            group = click.Group(
                name=group_instance.name,
                callback=group_func,
                help=group_instance.help,
                short_help=group_instance.short_help or group_instance.help,
                invoke_without_command=getattr(
                    group_instance, "invoke_without_command", False
                ),
            )

            # Add arguments and options to the group
            group = cast(
                "click.Group",
                self._add_command_arguments(group, group_instance),
            )

        # Load subcommands for this group
        self._load_subcommands_for_group(group, group_instance)

        return group

    def _load_subcommands_for_group(
        self, group: click.Group, group_instance: BaseGroupCommand
    ) -> None:
        """Load subcommands for a group command.

        Args:
            group: Click Group to add subcommands to
            group_instance: Instance of BaseGroupCommand
        """
        # Load both built-in subcommands and plugin subcommands
        subcommand_groups = [
            # Built-in subcommands
            f"deepctl.subcommands.{group_instance.name}",
            f"deepctl.subplugins.{group_instance.name}",  # Plugin subcommands
        ]

        for subcommand_group in subcommand_groups:
            try:
                entry_points = metadata.entry_points()
                for entry_point in entry_points.select(group=subcommand_group):
                    try:
                        # Load the subcommand class
                        subcommand_class = entry_point.load()

                        # Create instance
                        subcommand_instance = subcommand_class()

                        # Create Click command for the subcommand
                        click_subcommand = self._create_click_command(
                            subcommand_instance
                        )

                        # Add to the group
                        group.add_command(click_subcommand)

                        # Store reference in the group instance
                        if hasattr(group_instance, "add_subcommand"):
                            group_instance.add_subcommand(
                                entry_point.name, subcommand_class
                            )

                        # Determine type for logging
                        cmd_type = (
                            "plugin subcommand"
                            if "subplugins" in subcommand_group
                            else "subcommand"
                        )
                        console.print(
                            f"[dim]Loaded {cmd_type}:[/dim] "
                            f"{group_instance.name} {entry_point.name}"
                        )

                    except Exception as e:
                        console.print(
                            f"[red]Error loading subcommand "
                            f"{entry_point.name} for "
                            f"{group_instance.name}:[/red] {e}"
                        )

            except Exception as e:
                console.print(
                    f"[red]Error loading subcommands from "
                    f"{subcommand_group}:[/red] {e}"
                )

    def _add_command_arguments(
        self, cmd: click.Command, command_instance: Any
    ) -> click.Command:
        """Add arguments and options to a Click command.

        Args:
            cmd: Click command
            command_instance: BaseCommand instance

        Returns:
            Updated Click command
        """
        # Get arguments from command instance
        if hasattr(command_instance, "get_arguments"):
            arguments = command_instance.get_arguments()

            # Add arguments in reverse order (Click applies decorators
            # in reverse)
            for arg in reversed(arguments):
                if arg.get("is_option", False):
                    # Add as option
                    cmd = click.option(
                        *arg.get("names", []),
                        default=arg.get("default"),
                        help=arg.get("help", ""),
                        type=arg.get("type", str),
                        required=arg.get("required", False),
                        is_flag=arg.get("is_flag", False),
                        multiple=arg.get("multiple", False),
                    )(cmd)
                else:
                    # Add as argument
                    cmd = click.argument(
                        arg.get("name", ""),
                        type=arg.get("type", str),
                        required=arg.get("required", True),
                        nargs=arg.get("nargs", 1),
                    )(cmd)

        return cmd

    def get_command_list(self) -> list[str]:
        """Get list of loaded command names.

        Returns:
            List of command names
        """
        return list(self.command_classes.keys()) + list(
            self.loaded_plugins.keys()
        )

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

    def validate_plugin(self, plugin_class: type[Any]) -> bool:
        """Validate that a plugin class is properly implemented.

        Args:
            plugin_class: Plugin class to validate

        Returns:
            True if valid, False otherwise
        """
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
            return hasattr(instance, "execute")

        except Exception:
            return False

    def discover_plugin_directories(self) -> list[Path]:
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
