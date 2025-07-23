"""Base command class for deepctl commands."""

from abc import ABC, abstractmethod
from typing import Any

import click
from rich.console import Console

from .auth import AuthManager
from .client import DeepgramClient
from .config import Config

console = Console()


class BaseCommand(ABC):
    """Base class for all deepctl commands."""

    # Command metadata (to be overridden by subclasses)
    name: str = ""
    help: str = ""
    short_help: str | None = None

    # Command requirements
    requires_auth: bool = False
    requires_project: bool = False
    ci_friendly: bool = True

    def __init__(self) -> None:
        """Initialize base command."""
        if not self.name:
            raise ValueError("Command must have a name")
        if not self.help:
            raise ValueError("Command must have help text")

    def execute(self, ctx: click.Context, **kwargs: Any) -> None:
        """Execute the command with Click context.

        Args:
            ctx: Click context
            **kwargs: Command arguments and options
        """
        # Get configuration from context
        config = ctx.obj.get("config")
        if not config:
            config = Config()

        # Create auth manager
        auth_manager = AuthManager(config)

        # Create Deepgram client
        client = DeepgramClient(config, auth_manager)

        # Check authentication if required
        if self.requires_auth:
            try:
                auth_manager.guard()
            except Exception as e:
                console.print(f"[red]Authentication required:[/red] {e}")
                raise click.ClickException(str(e))

        # Check project ID if required
        if self.requires_project:
            project_id = auth_manager.get_project_id()
            if not project_id:
                console.print(
                    "[red]Error:[/red] Project ID is required for this command"
                )
                console.print(
                    "Set DEEPGRAM_PROJECT_ID environment variable or "
                    "configure via profile"
                )
                raise click.ClickException("Project ID required")

        # Execute the command
        try:
            result = self.handle(config, auth_manager, client, **kwargs)

            # Handle command result
            if result is not None:
                self.output_result(result, config)

        except KeyboardInterrupt:
            console.print("\n[yellow]Command cancelled by user[/yellow]")
            raise click.Abort()

        except Exception as e:
            console.print(f"[red]Command failed:[/red] {e}")
            if config.get("output.verbose", False):
                console.print_exception()
            raise click.ClickException(str(e))

    @abstractmethod
    def handle(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> Any:
        """Handle the command execution.

        Args:
            config: Configuration manager
            auth_manager: Authentication manager
            client: Deepgram client
            **kwargs: Command arguments and options

        Returns:
            Command result (optional)
        """
        pass

    def get_arguments(self) -> list[dict[str, Any]]:
        """Get command arguments and options.

        Returns:
            List of argument/option definitions
        """
        return []

    def output_result(self, result: Any, config: Config) -> None:
        """Output command result in the configured format.

        Args:
            result: Command result
            config: Configuration manager
        """
        if result is None:
            return

        output_format = config.get("output.format", "json")

        # Unwrap Pydantic models for serialisation
        # local import to avoid circulars
        from pydantic import BaseModel as _PydanticBaseModel

        if isinstance(result, _PydanticBaseModel):
            result = result.model_dump()
        elif (
            isinstance(result, list)
            and result
            and isinstance(result[0], _PydanticBaseModel)
        ):
            result = [item.model_dump() for item in result]

        if output_format == "json":
            self._output_json(result)
        elif output_format == "yaml":
            self._output_yaml(result)
        elif output_format == "table":
            self._output_table(result)
        elif output_format == "csv":
            self._output_csv(result)
        else:
            console.print(f"[red]Unknown output format:[/red] {output_format}")
            self._output_json(result)

    def _output_json(self, result: Any) -> None:
        """Output result as JSON."""
        import json

        if isinstance(result, dict | list):
            console.print_json(json.dumps(result, indent=2))
        else:
            console.print(json.dumps({"result": str(result)}, indent=2))

    def _output_yaml(self, result: Any) -> None:
        """Output result as YAML."""
        import yaml

        if isinstance(result, dict | list):
            console.print(yaml.dump(result, default_flow_style=False))
        else:
            console.print(
                yaml.dump({"result": str(result)}, default_flow_style=False)
            )

    def _output_table(self, result: Any) -> None:
        """Output result as table."""
        from rich.table import Table

        if (
            isinstance(result, list)
            and len(result) > 0
            and isinstance(result[0], dict)
        ):
            # List of dictionaries - create table
            table = Table(show_header=True, header_style="bold blue")

            # Add columns
            if result:
                for key in result[0]:
                    table.add_column(key.replace("_", " ").title())

                # Add rows
                for item in result:
                    table.add_row(
                        *[str(item.get(key, "")) for key in result[0]]
                    )

            console.print(table)

        elif isinstance(result, dict):
            # Dictionary - create key-value table
            table = Table(show_header=True, header_style="bold blue")
            table.add_column("Key")
            table.add_column("Value")

            for key, value in result.items():
                table.add_row(key.replace("_", " ").title(), str(value))

            console.print(table)

        else:
            # Fallback to JSON
            self._output_json(result)

    def _output_csv(self, result: Any) -> None:
        """Output result as CSV."""
        import csv
        import io

        if (
            isinstance(result, list)
            and len(result) > 0
            and isinstance(result[0], dict)
        ):
            # List of dictionaries
            output = io.StringIO()
            dict_writer = csv.DictWriter(output, fieldnames=result[0].keys())
            dict_writer.writeheader()
            dict_writer.writerows(result)
            console.print(output.getvalue())

        elif isinstance(result, dict):
            # Dictionary
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["Key", "Value"])
            for key, value in result.items():
                writer.writerow([key, value])
            console.print(output.getvalue())

        else:
            # Fallback to JSON
            self._output_json(result)

    def confirm(self, message: str, default: bool = False) -> bool:
        """Ask for user confirmation.

        Args:
            message: Confirmation message
            default: Default value if user just presses Enter

        Returns:
            True if confirmed, False otherwise
        """
        if not self.ci_friendly:
            # In CI environments, always return the default
            return default

        try:
            return click.confirm(message, default=default)
        except click.Abort:
            return False

    def prompt(
        self,
        message: str,
        default: str | None = None,
        hide_input: bool = False,
    ) -> str:
        """Prompt user for input.

        Args:
            message: Prompt message
            default: Default value
            hide_input: Whether to hide input (for passwords)

        Returns:
            User input
        """
        if not self.ci_friendly and default is not None:
            # In CI environments, return the default
            return default

        try:
            return str(
                click.prompt(message, default=default, hide_input=hide_input)
            )
        except click.Abort:
            raise click.ClickException("User cancelled input")

    def validate_file_path(self, file_path: str) -> bool:
        """Validate that a file path exists and is readable.

        Args:
            file_path: Path to validate

        Returns:
            True if valid, False otherwise
        """
        from pathlib import Path

        path = Path(file_path)
        return path.exists() and path.is_file()

    def validate_url(self, url: str) -> bool:
        """Validate URL format.

        Args:
            url: URL to validate

        Returns:
            True if valid, False otherwise
        """
        import re

        url_pattern = re.compile(
            r"^https?://"  # http:// or https://
            # domain...
            r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|"
            r"localhost|"  # localhost...
            r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"  # ...or ip
            r"(?::\d+)?"  # optional port
            r"(?:/?|[/?]\S+)$",
            re.IGNORECASE,
        )

        return url_pattern.match(url) is not None
