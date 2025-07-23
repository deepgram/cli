"""Debug command group for deepctl."""

from typing import Any

import click
from deepctl_core import AuthManager, BaseGroupCommand, Config, DeepgramClient
from rich.console import Console

from .models import DebugGroupResult

console = Console()


class DebugCommand(BaseGroupCommand):
    """Debug command group for diagnostic utilities."""

    name = "debug"
    help = "Debug utilities for troubleshooting Deepgram integration issues"
    short_help = "Debug utilities"

    # Debug commands don't require auth by default
    requires_auth = False
    requires_project = False
    ci_friendly = True

    # Show help when invoked without subcommand
    invoke_without_command = False

    def get_arguments(self) -> list[dict[str, Any]]:
        """Get command arguments and options."""
        return [
            {
                "names": ["--verbose", "-v"],
                "help": "Enable verbose debug output",
                "is_flag": True,
                "is_option": True,
            }
        ]

    def handle_group(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> Any:
        """Handle group-specific logic.

        This is called when the group is invoked, whether or not a
        subcommand is specified. We can use this for group-level
        initialization or logging.
        """
        verbose = kwargs.get("verbose", False)

        if verbose:
            console.print("[dim]Debug mode enabled with verbose output[/dim]")

        # If no subcommand was invoked, we could provide custom help or info
        ctx = click.get_current_context()
        if ctx.invoked_subcommand is None:
            # The base class will show help automatically, but we can add
            # additional information here if needed
            console.print("\n[blue]Available debug utilities:[/blue]")
            console.print("  • browser - Debug browser-related issues")
            console.print("  • network - Debug network connectivity")
            console.print("  • audio   - Debug audio file issues")
            console.print(
                "\n[dim]Use 'deepctl debug <subcommand> --help' for "
                "more information[/dim]"
            )

            return DebugGroupResult(
                status="info",
                message="Debug command requires a subcommand",
                subcommands={
                    "browser": "Debug browser-related issues",
                    "network": "Debug network connectivity",
                    "audio": "Debug audio file issues",
                },
            )

        # A subcommand was invoked, we can optionally do something here
        # but usually we just let the subcommand handle itself
        return None
