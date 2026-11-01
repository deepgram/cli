"""Output formatting utilities for deepctl."""

from __future__ import annotations

import json
from io import StringIO
from typing import TYPE_CHECKING, Any, cast

from . import agents

if TYPE_CHECKING:
    from rich.console import Console
    from rich.progress import Progress

# ── Agentic / non-TTY detection ──────────────────────────────────
# deepctl_core.agents holds the table; these names stay importable from here.
# is_agentic(): agent or automation, for output, prompts and notices (honors
# DG_AGENT). is_ai_agent(): a raw AI-agent marker, for trust decisions.
is_agentic = agents.is_agentic
is_ai_agent = agents.is_ai_agent
AI_AGENT_ENV_MARKERS: tuple[str, ...] = agents.HARD_ENV_VARS


def agent_mode() -> bool:
    """Hard agent mode for presentation, prompts and login: ``DG_AGENT``
    when it is 0 or 1, else :func:`is_ai_agent`. Never for trust."""
    forced = agents.override()
    if forced is not None:
        return forced == "1"
    return is_ai_agent()


_agentic = is_agentic()


class _LazyConsole:
    """A rich ``Console`` that is built, and rich imported, on first use.

    Importing rich costs every command, including ``--help``; most runs
    print through a console only on some paths. Attribute reads, writes and
    deletes go to the real console, so ``console.print(...)``,
    ``console.quiet = True`` and ``mock.patch.object(console, "print")``
    behave as before, and the module-level identity of ``console`` and
    ``stderr_console`` never changes.
    """

    __slots__ = ("_deepctl_console", "_deepctl_kwargs", "_deepctl_quiet")

    def __init__(self, **kwargs: Any) -> None:
        object.__setattr__(self, "_deepctl_kwargs", kwargs)
        object.__setattr__(self, "_deepctl_console", None)
        # ``quiet`` set before the console exists: applied when it's built,
        # so setup_output() doesn't import rich for a run that prints nothing.
        object.__setattr__(self, "_deepctl_quiet", None)

    def _deepctl_real(self) -> Console:
        real = object.__getattribute__(self, "_deepctl_console")
        if real is None:
            from rich.console import Console

            real = Console(**object.__getattribute__(self, "_deepctl_kwargs"))
            object.__setattr__(self, "_deepctl_console", real)
            quiet = object.__getattribute__(self, "_deepctl_quiet")
            if quiet is not None:
                real.quiet = quiet
        return cast("Console", real)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._deepctl_real(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if (
            name == "quiet"
            and object.__getattribute__(self, "_deepctl_console") is None
        ):
            object.__setattr__(self, "_deepctl_quiet", value)
            return
        setattr(self._deepctl_real(), name, value)

    def __delattr__(self, name: str) -> None:
        delattr(self._deepctl_real(), name)

    def __enter__(self) -> Console:
        return self._deepctl_real().__enter__()

    def __exit__(self, *exc_info: Any) -> None:
        self._deepctl_real().__exit__(*exc_info)

    def __repr__(self) -> str:
        return f"<lazy {object.__getattribute__(self, '_deepctl_kwargs')!r} console>"


# Global console instance — plain when agentic, rich when interactive
# Plain text (no colour and no bold or dim either) in agent mode and when
# NO_COLOR is set: rich's own NO_COLOR handling still writes styles. Never
# no_color=False, which would override NO_COLOR; otherwise rich reads
# FORCE_COLOR and TERM itself.
_plain = _agentic or agents.no_color()
_console_kwargs: dict[str, Any] = {"highlight": not _agentic}
if _plain:
    _console_kwargs.update(no_color=True, color_system=None)
console = cast("Console", _LazyConsole(**_console_kwargs))
stderr_console = cast("Console", _LazyConsole(stderr=True, **_console_kwargs))

# Global output configuration
_output_config: dict[str, Any] = {
    "format": "default",
    "quiet": False,
    "verbose": False,
    "color": not _plain,
    "agentic": _agentic,
    "format_source": "default",
}


def _format_source(requested: str) -> str:
    """Why the output format is what it is; see format_source()."""
    if requested != "default":
        return "explicit"
    if not _output_config["agentic"]:
        return "default"
    return "hard_agent" if agent_mode() else "soft"


def setup_output(
    format_type: str = "json", quiet: bool = False, verbose: bool = False
) -> None:
    """Setup global output configuration.

    When running in an agentic/non-TTY context and no explicit format is
    requested (format_type == "default"), auto-switch to JSON for
    machine-friendly output.
    """
    _output_config["format_source"] = _format_source(format_type)
    # Auto-default to JSON in agentic mode when user didn't pick a format
    if format_type == "default" and _output_config["agentic"]:
        format_type = "json"

    _output_config.update({"format": format_type, "quiet": quiet, "verbose": verbose})

    # Update console settings
    console.quiet = quiet


def update_output(
    format_type: str | None = None,
    quiet: bool | None = None,
    verbose: bool | None = None,
) -> None:
    """Update parts of the global output configuration in place.

    Unlike setup_output(), only the arguments actually provided change;
    every other setting keeps its current value. The per-command
    pass-through options use this so that ``dg models -o json`` has the
    same effect as ``dg -o json models`` without resetting the quiet or
    verbose state the root group already applied.
    """
    if format_type is not None:
        _output_config["format_source"] = _format_source(format_type)
        if format_type == "default" and _output_config["agentic"]:
            format_type = "json"
        _output_config["format"] = format_type
    if quiet is not None:
        _output_config["quiet"] = quiet
        console.quiet = quiet
    if verbose is not None:
        _output_config["verbose"] = verbose


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
                    return json.dumps({"result": data}, indent=2, ensure_ascii=False)
            else:
                return json.dumps(data, indent=2, ensure_ascii=False, default=str)
        except Exception as e:
            return json.dumps({"error": f"JSON formatting failed: {e}"}, indent=2)

    def _format_yaml(self, data: Any) -> str:
        """Format as YAML."""
        import yaml

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
                    yaml.dump(data, default_flow_style=False, allow_unicode=True)
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
                    dict_writer = csv.DictWriter(output, fieldnames=data[0].keys())
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
        from rich.table import Table

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
        from rich.table import Table

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
        from rich.table import Table

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
        if not console.is_terminal:
            # A pipe or file: plain JSON in one write, as rich would print
            # it but without its renderer's cost on a large result.
            stream = console.file
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except json.JSONDecodeError:
                    stream.write(data + "\n")
                    stream.flush()
                    return
            stream.write(
                json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n"
            )
            stream.flush()
            return

        from rich.json import JSON

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
        from rich.syntax import Syntax

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
    """Print success message."""
    if not _output_config["quiet"]:
        if _output_config["agentic"]:
            stderr_console.print(f"OK: {message}")
        else:
            console.print(f"[green]✓[/green] {message}")


def print_error(message: str) -> None:
    """Print error message."""
    if _output_config["agentic"]:
        stderr_console.print(f"ERROR: {message}", soft_wrap=True)
    else:
        stderr_console.print(f"[red]✗[/red] {message}", soft_wrap=True)


def print_security_warning(message: str) -> None:
    """Print a warning to stderr even under ``--quiet``.

    For warnings about where credentials go or what a project file tried
    to set; those must never be silenced.
    """
    if _output_config["agentic"]:
        stderr_console.print(f"WARN: {message}", soft_wrap=True)
    else:
        stderr_console.print(f"[yellow]⚠[/yellow] {message}", soft_wrap=True)


# Hints already printed in this process (one run of dg).
_hints_shown: set[str] = set()


def print_hint(message: str) -> None:
    """Print the hint line that follows an error or warning (always stderr).

    A hint prints once per run: a later warning or error with the same hint
    relies on the first one.
    """
    if message in _hints_shown:
        return
    _hints_shown.add(message)
    if _output_config["agentic"]:
        stderr_console.print(f"HINT: {message}", soft_wrap=True)
    else:
        stderr_console.print(f"  [dim]{message}[/dim]", soft_wrap=True)


def print_error_hint(error: BaseException) -> None:
    """Print the hint an error carries, if any (a refused config save says
    who fixes the file)."""
    hint = getattr(error, "hint", None)
    if hint:
        print_hint(str(hint))


def print_warning(
    message: str, *, stderr: bool = False, ignore_quiet: bool = False
) -> None:
    """Print a warning; ``stderr=True`` keeps it off stdout outside agentic mode too.

    ``ignore_quiet=True`` prints it under ``--quiet`` as well, for warnings
    the caller needs to read the result (``--quiet`` hides only
    INFO and OK lines).
    """
    if ignore_quiet or not _output_config["quiet"]:
        if _output_config["agentic"]:
            stderr_console.print(f"WARN: {message}", soft_wrap=True)
        else:
            (stderr_console if stderr else console).print(
                f"[yellow]⚠[/yellow] {message}", soft_wrap=True
            )


def print_info(message: str, *, stderr: bool = False) -> None:
    """Print info message."""
    if not _output_config["quiet"]:
        if _output_config["agentic"]:
            stderr_console.print(f"INFO: {message}", soft_wrap=True)
        else:
            (stderr_console if stderr else console).print(
                f"[blue]ℹ[/blue] {message}", soft_wrap=True
            )


def print_debug(message: str) -> None:
    """Print debug message (only in verbose mode).

    Args:
        message: Debug message
    """
    if _output_config["verbose"]:
        if _output_config["agentic"]:
            stderr_console.print(f"DEBUG: {message}")
        else:
            console.print(f"[dim]DEBUG:[/dim] {message}")


def create_progress_bar(description: str = "Processing...") -> Progress:
    """Create a progress bar.

    Args:
        description: Progress description

    Returns:
        Progress bar instance
    """
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
    )

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
    from rich.progress import Progress, SpinnerColumn, TextColumn

    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        disable=bool(_output_config["quiet"]),
    )


def print_panel(content: str, title: str = "", border_style: str = "blue") -> None:
    """Print content in a panel.

    Args:
        content: Panel content
        title: Panel title
        border_style: Border style
    """
    if not _output_config["quiet"]:
        from rich.panel import Panel

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
    if _output_config["quiet"] or _output_config["agentic"]:
        return default

    try:
        import click

        return click.confirm(message, default=default)
    except ImportError:
        # Fallback implementation
        response = input(f"{message} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
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
    if (_output_config["quiet"] or _output_config["agentic"]) and default is not None:
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


def get_output_format() -> str:
    """Get the current output format.

    Returns:
        Output format string ("default", "json", "yaml", "table", "csv")
    """
    return str(_output_config["format"])


def format_source() -> str:
    """Why the output format is what it is.

    ``"explicit"``: asked for with ``-o``. ``"hard_agent"``: JSON chosen
    implicitly in hard agent mode (``agents.agent_mode()``).
    ``"soft"``: JSON chosen implicitly by the TTY/CI heuristics alone.
    ``"default"``: no format was chosen.
    """
    return str(_output_config.get("format_source", "default"))


def get_console() -> Console:
    """Get the global console instance.

    Returns:
        Console instance
    """
    return console


def get_status_console() -> Console:
    """Get the console for status/progress chrome.

    Always writes to stderr, so anything printed through it can never corrupt
    the machine-readable payload a command writes to stdout. Commands should
    use this rather than declaring their own ``Console(stderr=True)`` — a
    per-command console silently misses the agentic no-color settings, and a
    command that reaches for a bare ``Console()`` reintroduces the stdout
    pollution this exists to prevent.

    Returns:
        Console instance writing to stderr
    """
    return stderr_console


def invoked_prog() -> str:
    """The program name to show in notices and messages.

    ``DEEPCTL_PROG_NAME`` when it is a plain program name (see
    ``deepctl.main.prog_name_from_env``); else the running executable as a
    command (``dg``, ``deepctl``, ``deepgram`` when that name runs this
    same file, else its path); else ``dg``.
    """
    try:
        from deepctl.main import prog_name_from_env
    except ImportError:  # deepctl-core without the CLI: no launcher name
        name = None
    else:
        name = prog_name_from_env()
    if name:
        return name
    from .transport import prog

    return prog() or "dg"


def stderr_color() -> bool:
    """Whether a raw notice written to stderr may use ANSI colour.

    Only for a person's session (never agent or automation output), with
    NO_COLOR unset or empty and TERM not ``dumb``; and only on a terminal,
    unless FORCE_COLOR is set.
    """
    import os
    import sys

    if _output_config["agentic"] or os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    try:
        return bool(sys.stderr.isatty())
    except (AttributeError, OSError, ValueError):
        return False
