"""Output formatting utilities for deepctl."""

import json
from io import StringIO
from typing import Any

import yaml
from rich.console import Console
from rich.json import JSON
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
)
from rich.syntax import Syntax
from rich.table import Table

# Global console instance
console = Console()
stderr_console = Console(stderr=True)

# Global output configuration
_output_config = {
    "format": "json",
    "quiet": False,
    "verbose": False,
    "color": True,
}


def setup_output(
    format_type: str = "json", quiet: bool = False, verbose: bool = False
) -> None:
    """Setup global output configuration.

    Args:
        format_type: Output format (json, yaml, table, csv)
        quiet: Suppress non-essential output
        verbose: Enable verbose output
    """
    _output_config.update(
        {"format": format_type, "quiet": quiet, "verbose": verbose}
    )

    # Update console settings
    console.quiet = quiet


class OutputFormatter:
    """Formatter for different output types."""

    def __init__(self, format_type: str = "json"):
        """Initialize formatter.

        Args:
            format_type: Output format
        """
        self.format_type = format_type

    def format(self, data: Any) -> str:
        """Format data according to the specified format.

        Args:
            data: Data to format

        Returns:
            Formatted string
        """
        if self.format_type == "json":
            return self._format_json(data)
        elif self.format_type == "yaml":
            return self._format_yaml(data)
        elif self.format_type == "table":
            return self._format_table(data)
        elif self.format_type == "csv":
            return self._format_csv(data)
        else:
            return self._format_json(data)

    def _format_json(self, data: Any) -> str:
        """Format as JSON."""
        try:
            if isinstance(data, str):
                # Try to parse as JSON first
                try:
                    parsed = json.loads(data)
                    return json.dumps(parsed, indent=2, ensure_ascii=False)
                except json.JSONDecodeError:
                    # If not JSON, wrap in object
                    return json.dumps(
                        {"result": data}, indent=2, ensure_ascii=False
                    )
            else:
                return json.dumps(
                    data, indent=2, ensure_ascii=False, default=str
                )
        except Exception as e:
            return json.dumps(
                {"error": f"JSON formatting failed: {e}"}, indent=2
            )

    def _format_yaml(self, data: Any) -> str:
        """Format as YAML."""
        try:
            if isinstance(data, str):
                # Try to parse as JSON first
                try:
                    parsed = json.loads(data)
                    return str(
                        yaml.dump(
                            parsed,
                            default_flow_style=False,
                            allow_unicode=True,
                        )
                    )
                except json.JSONDecodeError:
                    # If not JSON, wrap in object
                    return str(
                        yaml.dump(
                            {"result": data},
                            default_flow_style=False,
                            allow_unicode=True,
                        )
                    )
            else:
                return str(
                    yaml.dump(
                        data, default_flow_style=False, allow_unicode=True
                    )
                )
        except Exception as e:
            return str(
                yaml.dump(
                    {"error": f"YAML formatting failed: {e}"},
                    default_flow_style=False,
                )
            )

    def _format_table(self, data: Any) -> str:
        """Format as table."""
        try:
            if isinstance(data, list) and len(data) > 0:
                if isinstance(data[0], dict):
                    return self._create_table_from_dict_list(data)
                else:
                    return self._create_simple_table(data)
            elif isinstance(data, dict):
                return self._create_table_from_dict(data)
            else:
                return str(data)
        except Exception as e:
            return f"Table formatting failed: {e}"

    def _format_csv(self, data: Any) -> str:
        """Format as CSV."""
        import csv

        output = StringIO()

        try:
            if isinstance(data, list) and len(data) > 0:
                if isinstance(data[0], dict):
                    dict_writer = csv.DictWriter(
                        output, fieldnames=data[0].keys()
                    )
                    dict_writer.writeheader()
                    dict_writer.writerows(data)
                else:
                    writer = csv.writer(output)
                    writer.writerow(["Value"])
                    for item in data:
                        writer.writerow([item])
            elif isinstance(data, dict):
                writer = csv.writer(output)
                writer.writerow(["Key", "Value"])
                for key, value in data.items():
                    writer.writerow([key, value])
            else:
                writer = csv.writer(output)
                writer.writerow(["Result"])
                writer.writerow([str(data)])

            return output.getvalue()
        except Exception as e:
            return f"CSV formatting failed: {e}"

    def _create_table_from_dict_list(self, data: list[dict[str, Any]]) -> str:
        """Create table from list of dictionaries."""
        table = Table(show_header=True, header_style="bold blue")

        # Add columns
        if data:
            for key in data[0]:
                table.add_column(key.replace("_", " ").title())

            # Add rows
            for item in data:
                row = []
                for key in data[0]:
                    value = item.get(key, "")
                    if isinstance(value, dict | list):
                        row.append(json.dumps(value, default=str))
                    else:
                        row.append(str(value))
                table.add_row(*row)

        # Capture table output
        with console.capture() as capture:
            console.print(table)

        return capture.get()

    def _create_table_from_dict(self, data: dict[str, Any]) -> str:
        """Create table from dictionary."""
        table = Table(show_header=True, header_style="bold blue")
        table.add_column("Key")
        table.add_column("Value")

        for key, value in data.items():
            if isinstance(value, dict | list):
                value_str = json.dumps(value, default=str)
            else:
                value_str = str(value)
            table.add_row(key.replace("_", " ").title(), value_str)

        # Capture table output
        with console.capture() as capture:
            console.print(table)

        return capture.get()

    def _create_simple_table(self, data: list[Any]) -> str:
        """Create simple table from list."""
        table = Table(show_header=True, header_style="bold blue")
        table.add_column("Value")

        for item in data:
            table.add_row(str(item))

        # Capture table output
        with console.capture() as capture:
            console.print(table)

        return capture.get()


def print_output(data: Any, format_type: str | None = None) -> None:
    """Print data in the specified format.

    Args:
        data: Data to print
        format_type: Output format (uses global config if not specified)
    """
    if _output_config["quiet"]:
        return

    format_type = format_type or str(_output_config["format"])
    formatter = OutputFormatter(format_type)

    if format_type == "json":
        # Use Rich's JSON formatter for better display
        if isinstance(data, str):
            try:
                parsed = json.loads(data)
                console.print(JSON.from_data(parsed))
            except json.JSONDecodeError:
                console.print(data)
        else:
            console.print(JSON.from_data(data))
    elif format_type == "yaml":
        # Use Rich's syntax highlighting for YAML
        yaml_str = formatter.format(data)
        syntax = Syntax(yaml_str, "yaml", theme="monokai", line_numbers=False)
        console.print(syntax)
    elif format_type == "table":
        # Table is already formatted for Rich
        formatted = formatter.format(data)
        console.print(formatted, end="")
    else:
        # CSV and other formats
        console.print(formatter.format(data))


def print_success(message: str) -> None:
    """Print success message.

    Args:
        message: Success message
    """
    if not _output_config["quiet"]:
        console.print(f"[green]✓[/green] {message}")


def print_error(message: str) -> None:
    """Print error message.

    Args:
        message: Error message
    """
    stderr_console.print(f"[red]✗[/red] {message}")


def print_warning(message: str) -> None:
    """Print warning message.

    Args:
        message: Warning message
    """
    if not _output_config["quiet"]:
        console.print(f"[yellow]⚠[/yellow] {message}")


def print_info(message: str) -> None:
    """Print info message.

    Args:
        message: Info message
    """
    if not _output_config["quiet"]:
        console.print(f"[blue]ℹ[/blue] {message}")


def print_debug(message: str) -> None:
    """Print debug message (only in verbose mode).

    Args:
        message: Debug message
    """
    if _output_config["verbose"]:
        console.print(f"[dim]DEBUG:[/dim] {message}")


def create_progress_bar(description: str = "Processing...") -> Progress:
    """Create a progress bar.

    Args:
        description: Progress description

    Returns:
        Progress bar instance
    """
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
        disable=bool(_output_config["quiet"]),
    )


def create_spinner(description: str = "Processing...") -> Progress:
    """Create a spinner.

    Args:
        description: Spinner description

    Returns:
        Spinner instance
    """
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        disable=bool(_output_config["quiet"]),
    )


def print_panel(
    content: str, title: str = "", border_style: str = "blue"
) -> None:
    """Print content in a panel.

    Args:
        content: Panel content
        title: Panel title
        border_style: Border style
    """
    if not _output_config["quiet"]:
        panel = Panel(content, title=title, border_style=border_style)
        console.print(panel)


def print_separator(char: str = "─", length: int = 50) -> None:
    """Print a separator line.

    Args:
        char: Separator character
        length: Separator length
    """
    if not _output_config["quiet"]:
        console.print(char * length)


def confirm_action(message: str, default: bool = False) -> bool:
    """Confirm an action with the user.

    Args:
        message: Confirmation message
        default: Default value

    Returns:
        True if confirmed, False otherwise
    """
    if _output_config["quiet"]:
        return default

    try:
        import click

        return click.confirm(message, default=default)
    except ImportError:
        # Fallback implementation
        response = (
            input(f"{message} [{'Y/n' if default else 'y/N'}]: ")
            .strip()
            .lower()
        )
        if not response:
            return default
        return response in ("y", "yes")


def prompt_input(message: str, default: str | None = None) -> str:
    """Prompt for user input.

    Args:
        message: Prompt message
        default: Default value

    Returns:
        User input
    """
    if _output_config["quiet"] and default is not None:
        return default

    try:
        import click

        return str(click.prompt(message, default=default))
    except ImportError:
        # Fallback implementation
        prompt_text = f"{message}"
        if default:
            prompt_text += f" [{default}]"
        prompt_text += ": "

        response = input(prompt_text).strip()
        return response if response else (default or "")


def get_console() -> Console:
    """Get the global console instance.

    Returns:
        Console instance
    """
    return console
