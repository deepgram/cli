"""Output formatting utilities for deepctl."""

from __future__ import annotations

import json
import os
import sys
from io import StringIO
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from rich.console import Console
    from rich.progress import Progress

# ── Agentic / non-TTY detection ──────────────────────────────────


def is_agentic() -> bool:
    """Detect if running inside an AI coding tool or non-interactive context.

    Uses a layered signal approach:
    - Hard signals: known AI-tool environment variables (high confidence)
    - Soft signals: score-based TTY/CI heuristics (3+ = probable agent)

    Returns True when the context is likely non-interactive or AI-driven.
    """
    env = os.environ

    # Hard signals: explicit non-interactive intent or known AI tool env vars
    if "--non-interactive" in sys.argv:
        return True
    if "--agent-friendly" in sys.argv:
        return True
    if env.get("CI") in ("true", "1"):
        return True
    if env.get("CLAUDECODE"):
        return True
    if env.get("CLAUDE_CODE_ENTRYPOINT"):
        return True
    if env.get("CODEX_SANDBOX"):
        return True
    if env.get("CODEX_SANDBOX_NETWORK_DISABLED"):
        return True
    if env.get("OR_APP_NAME") == "Aider":
        return True
    if "aider" in env.get("OR_SITE_URL", ""):
        return True

    # Soft signals — score-based; none is conclusive alone
    score = 0
    if not sys.stdin.isatty():
        score += 1
    if not sys.stdout.isatty():
        score += 1
    if not env.get("TERM") or env.get("TERM") == "dumb":
        score += 1
    if "NO_COLOR" in env:
        score += 1

    return score >= 3


# Environment markers set by AI coding agents themselves. Used, instead of
# is_agentic(), wherever the CLI decides whom to trust with the API key: a
# hard signal only. CI, a missing TTY or a missing TERM are never on this
# list, so CI jobs, cron and containers keep the environment they were given.
# An exact list; is_agentic() above is for output formatting and is broader.
# Variables a person's own shell sets too (Cursor's CURSOR_TRACE_ID, Replit's
# REPL_ID) are left out.
AI_AGENT_ENV_MARKERS: tuple[str, ...] = (
    "AI_AGENT",
    "ANTIGRAVITY_AGENT",
    "AUGMENT_AGENT",
    "CLAUDECODE",
    "CLAUDE_CODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_IS_COWORK",
    "CODEX_CI",
    "CODEX_SANDBOX",
    "CODEX_SANDBOX_NETWORK_DISABLED",
    "CODEX_THREAD_ID",
    "COPILOT_CLI",
    "CURSOR_AGENT",
    "GEMINI_CLI",
    "GOOSE_TERMINAL",
    "OPENCODE",
    "OPENCODE_CLIENT",
)
# Markers that count only with one of these values.
AI_AGENT_ENV_VALUES: dict[str, tuple[str, ...]] = {
    "AGENT": ("amp", "goose"),
    "CURSOR_EXTENSION_HOST_ROLE": ("agent-exec",),
}


def is_ai_agent() -> bool:
    """True only when an AI coding agent marked the environment.

    Hard signals: any non-empty variable in ``AI_AGENT_ENV_MARKERS``, a
    variable in ``AI_AGENT_ENV_VALUES`` with one of its values, or Aider
    (``OR_APP_NAME=Aider`` or "aider" in ``OR_SITE_URL``). Never ``CI``,
    TTY state, ``TERM`` or command-line flags.
    """
    env = os.environ
    if any(env.get(name) for name in AI_AGENT_ENV_MARKERS):
        return True
    if any(env.get(name) in values for name, values in AI_AGENT_ENV_VALUES.items()):
        return True
    return env.get("OR_APP_NAME") == "Aider" or "aider" in env.get("OR_SITE_URL", "")


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
console = cast("Console", _LazyConsole(no_color=_agentic, highlight=not _agentic))
stderr_console = cast(
    "Console",
    _LazyConsole(stderr=True, no_color=_agentic, highlight=not _agentic),
)

# Global output configuration
_output_config: dict[str, Any] = {
    "format": "default",
    "quiet": False,
    "verbose": False,
    "color": not _agentic,
    "agentic": _agentic,
    "format_source": "default",
}


def _format_source(requested: str) -> str:
    """Why the output format is what it is; see format_source()."""
    if requested != "default":
        return "explicit"
    if not _output_config["agentic"]:
        return "default"
    return "hard_agent" if is_ai_agent() else "soft"


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
    implicitly while an AI-agent marker (``is_ai_agent()``) is set.
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
