"""Base class for group commands that can contain subcommands."""

from typing import Any

import click
from rich.console import Console

from .auth import AuthManager
from .base_command import BaseCommand
from .client import DeepgramClient
from .config import Config

console = Console()


class BaseGroupCommand(BaseCommand):
    """Base class for commands that act as groups containing subcommands.

    This class allows commands to act as groups that can contain other commands
    as subcommands. It provides the necessary interface for the plugin manager
    to create Click groups and register subcommands under them.

    Example:
        class DebugCommand(BaseGroupCommand):
            name = "debug"
            help = "Debug utilities for troubleshooting"

            def handle_group(self, config, auth_manager, client, **kwargs):
                # Optional: logic to run when group is invoked without
                # subcommand
                console.print("Debug command requires a subcommand")
    """

    def __init__(self) -> None:
        """Initialize the group command."""
        super().__init__()
        self.subcommands: dict[str, type[BaseCommand]] = {}
        self.is_group = True
        # By default, groups show help when invoked without subcommand
        # Only set if not already defined at class level
        if not hasattr(self, "invoke_without_command"):
            self.invoke_without_command = False

    def add_subcommand(
        self, name: str, command_class: type[BaseCommand]
    ) -> None:
        """Add a subcommand to this group.

        Args:
            name: The name of the subcommand
            command_class: The command class to register
        """
        self.subcommands[name] = command_class

    def get_subcommands(self) -> dict[str, type[BaseCommand]]:
        """Get all registered subcommands.

        Returns:
            Dictionary mapping subcommand names to their classes
        """
        return self.subcommands

    def handle(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> Any:
        """Handle the group command execution.

        This method is called when the group is invoked. By default, it will
        show help if no subcommand is provided. Override handle_group() to
        provide custom behavior.

        Args:
            config: Configuration instance
            auth_manager: Authentication manager instance
            client: Deepgram client instance
            **kwargs: Additional keyword arguments from CLI

        Returns:
            Result of the group execution
        """
        ctx = click.get_current_context()

        # Check if a subcommand was invoked
        if ctx.invoked_subcommand is None:
            # No subcommand was invoked
            if self.invoke_without_command:
                # Call the group handler if defined
                return self.handle_group(
                    config, auth_manager, client, **kwargs
                )
            else:
                # Show help by default
                click.echo(ctx.get_help())
                return None
        else:
            # A subcommand was invoked, it will be handled automatically
            # We can optionally run group-level logic here
            return self.handle_group(config, auth_manager, client, **kwargs)

    def handle_group(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> Any:
        """Handle group-specific logic.

        Override this method to provide custom behavior when the group is
        invoked.

        Args:
            config: Configuration instance
            auth_manager: Authentication manager instance
            client: Deepgram client instance
            **kwargs: Additional keyword arguments from CLI

        Returns:
            Result of the group handler
        """
        # Default implementation does nothing
        # Subclasses can override to add group-level logic
        pass

    def get_click_group(self) -> click.Group:
        """Create and return a Click Group for this command.

        This method is used by the plugin manager to create the actual Click
        group that will be registered with the CLI.

        Returns:
            Click Group instance
        """
        # Create the group
        group = click.Group(
            name=self.name,
            help=self.help,
            short_help=self.short_help or self.help,
            invoke_without_command=self.invoke_without_command,
            callback=self._create_group_callback(),
        )

        # Add any group-level options/arguments
        if hasattr(self, "get_arguments"):
            arguments = self.get_arguments()
            for arg in reversed(arguments):
                if arg.get("is_option", False):
                    group = click.option(
                        *arg.get("names", []),
                        default=arg.get("default"),
                        help=arg.get("help", ""),
                        type=arg.get("type", str),
                        required=arg.get("required", False),
                        is_flag=arg.get("is_flag", False),
                        multiple=arg.get("multiple", False),
                    )(group)
                else:
                    group = click.argument(
                        arg.get("name", ""),
                        type=arg.get("type", str),
                        required=arg.get("required", True),
                        nargs=arg.get("nargs", 1),
                    )(group)

        return group

    def _create_group_callback(self) -> Any:
        """Create the callback function for the Click group.

        Returns:
            Callback function for the group
        """

        def group_callback(**kwargs: Any) -> Any:
            # Pass CLI context and arguments to the command
            ctx = click.get_current_context()
            return self.execute(ctx, **kwargs)

        # Set function name and docstring
        group_callback.__name__ = self.name.replace("-", "_")
        group_callback.__doc__ = self.help

        return group_callback
