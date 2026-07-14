"""Base command class for deepctl commands."""

from abc import ABC, abstractmethod
from typing import Any, ClassVar

import click
from rich.console import Console

from .auth import AuthManager
from .client import DeepgramClient
from .config import Config
from .output import _agentic, print_error, print_info, print_warning, stderr_console
from .timing import TimingContext

console = Console()


class BaseCommand(ABC):
    """Base class for all deepctl commands."""

    # Command metadata (to be overridden by subclasses)
    name: str = ""
    help: str = ""
    short_help: str | None = None
    hidden: bool = False  # set True to hide from dg --help (e.g. deprecated aliases)

    # Command requirements
    requires_auth: bool = False
    requires_project: bool = False
    ci_friendly: bool = True

    # Agent-oriented metadata
    examples: ClassVar[list[str]] = []
    agent_help: str = ""

    def __init__(self) -> None:
        """Initialize base command."""
        if not self.name:
            raise ValueError("Command must have a name")
        if not self.help:
            raise ValueError("Command must have help text")
        self._guided: bool = True

    def execute(self, ctx: click.Context, **kwargs: Any) -> None:
        """Execute the command with Click context.

        Args:
            ctx: Click context
            **kwargs: Command arguments and options
        """
        with TimingContext(f"command_{self.name}_total"):
            with TimingContext("command_setup"):
                # Get configuration from context
                config = ctx.obj.get("config")
                if not config:
                    config = Config()

                # Extract explicit credentials from kwargs if provided
                # (or from the global --api-key option stored on ctx.obj)
                explicit_api_key = kwargs.get("api_key") or (
                    ctx.obj.get("api_key") if ctx.obj else None
                )
                explicit_project_id = kwargs.get("project_id")

                # Create auth manager with explicit credentials
                auth_manager = AuthManager(
                    config, explicit_api_key, explicit_project_id
                )

                # Create Deepgram client
                client = DeepgramClient(config, auth_manager)

            # Check authentication if required
            if self.requires_auth:
                with TimingContext("authentication_check"):
                    try:
                        auth_manager.guard()

                        # Log credential source and project ID for transparency
                        if not config.get("output.quiet", False):
                            source = auth_manager.get_credential_source()
                            project_id = auth_manager.get_project_id()

                            # Only log if not using a profile (i.e., using env vars or flags)
                            if source in [
                                "explicit flags",
                                "environment variables",
                            ]:
                                print_info(f"Using credentials from {source}")
                                if project_id:
                                    print_info(f"Affecting project: {project_id}")
                                else:
                                    print_warning("No project ID specified")

                    except Exception:
                        # guard() already printed helpful error messages;
                        # exit without duplicating them.
                        raise SystemExit(1)

            # Check project ID if required
            if self.requires_project:
                with TimingContext("project_validation"):
                    project_id = auth_manager.get_project_id()
                    if not project_id:
                        print_error(
                            "Project ID is required for this command. "
                            "Set DEEPGRAM_PROJECT_ID or configure via profile."
                        )
                        raise click.ClickException("Project ID required")

            self._guided = self.is_guided(ctx)
            self._tag_telemetry_start(ctx)

            # Execute the command
            try:
                with TimingContext(f"command_{self.name}_handler"):
                    result = self.handle(config, auth_manager, client, **kwargs)

                status = "ok"
                if result is not None and hasattr(result, "status"):
                    status = str(result.status)
                self._tag_telemetry_status(status)

                # Handle command result
                if result is not None:
                    with TimingContext("output_processing"):
                        try:
                            self.output_result(result, config)
                        except (BrokenPipeError, OSError):
                            # Downstream stream closed (e.g. an MCP host disconnected
                            # stdio after `dg mcp` finished). Nothing useful to log
                            # here because the logger writes to the same closed stream.
                            pass
                        except ValueError as exc:
                            if "closed file" not in str(exc):
                                raise

            except KeyboardInterrupt:
                self._tag_telemetry_status("cancelled")
                stderr_console.print("\n[yellow]Command cancelled by user[/yellow]")
                raise click.Abort()

            except Exception as e:
                self._tag_telemetry_status("error")
                print_error(f"Command failed: {e}")
                if config.get("output.verbose", False):
                    stderr_console.print_exception()
                raise click.ClickException(str(e))

    def is_guided(self, ctx: click.Context) -> bool:
        """True only when the user invoked this command with no input at all.

        'No input' = no positional arg, no flag, no option value, no env-var
        override. The single rule for every command: any user-provided signal
        means scripting intent — skip prompts. Only the bare invocation gets
        the guided/interactive flow.

        Returns False whenever telemetry's `_agentic` heuristic fires
        (CI=1, --agent-friendly, --non-interactive, AI tool env vars, etc.).
        """
        if _agentic:
            return False
        for param in ctx.command.params:
            if not param.name:
                continue
            src = ctx.get_parameter_source(param.name)
            if src is not None and src.name in ("COMMANDLINE", "ENVIRONMENT"):
                return False
        return True

    def _tag_telemetry_start(self, ctx: click.Context) -> None:
        """Annotate the active Sentry transaction with command-level usage signal.

        Sets the transaction name to the full Click command path
        (e.g. 'deepctl debug audio' instead of just 'debug') and tags it with
        the user-provided flag NAMES (never values), the requested output
        format, and the auth method. All values are bounded enums or
        already-public flag identifiers — no PII risk.

        Wrapped in a bare except so a Sentry hiccup, missing scope, or unknown
        Click parameter-source enum value can never crash the user's command.
        """
        try:
            import sentry_sdk

            scope = sentry_sdk.get_current_scope()
            transaction = getattr(scope, "transaction", None)
            if transaction is not None and ctx.command_path:
                transaction.name = ctx.command_path

            used: list[str] = []
            for param in ctx.command.params:
                if not param.name:
                    continue
                src = ctx.get_parameter_source(param.name)
                if src is not None and src.name == "COMMANDLINE":
                    used.append(param.name)
            scope.set_tag("cmd.flags", ",".join(sorted(used)) or "(none)")
            scope.set_tag("cmd.output_format", ctx.params.get("output") or "default")
        except Exception:
            pass

    def _tag_telemetry_status(self, status: str) -> None:
        """Tag the active Sentry transaction with the command outcome.

        Status is a bounded enum: 'ok', 'cancelled', 'error', or whatever the
        command's BaseResult.status returned (also bounded by the result
        model). Bare except so telemetry can't crash the command teardown.
        """
        try:
            import sentry_sdk

            sentry_sdk.get_current_scope().set_tag("cmd.status", status)
        except Exception:
            pass

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

        with TimingContext("result_formatting"):
            from .output import get_output_format

            output_format = get_output_format()

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

        with TimingContext(f"output_{output_format}"):
            if output_format == "default":
                # Commands handle their own display in default mode;
                # only emit structured output when explicitly requested.
                return
            elif output_format == "json":
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
        from datetime import date, datetime

        def _default(obj: Any) -> str:
            if isinstance(obj, (datetime, date)):
                return obj.isoformat()
            return str(obj)

        if isinstance(result, dict | list):
            console.print_json(json.dumps(result, indent=2, default=_default))
        else:
            console.print(
                json.dumps({"result": str(result)}, indent=2, default=_default)
            )

    def _output_yaml(self, result: Any) -> None:
        """Output result as YAML."""
        import yaml

        if isinstance(result, dict | list):
            console.print(yaml.dump(result, default_flow_style=False))
        else:
            console.print(yaml.dump({"result": str(result)}, default_flow_style=False))

    def _output_table(self, result: Any) -> None:
        """Output result as table."""
        from rich.table import Table

        if isinstance(result, list) and len(result) > 0 and isinstance(result[0], dict):
            # List of dictionaries - create table
            table = Table(show_header=True, header_style="bold blue")

            # Add columns
            if result:
                for key in result[0]:
                    table.add_column(key.replace("_", " ").title())

                # Add rows
                for item in result:
                    table.add_row(*[str(item.get(key, "")) for key in result[0]])

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

        if isinstance(result, list) and len(result) > 0 and isinstance(result[0], dict):
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
        if _agentic or not self.ci_friendly or not getattr(self, "_guided", True):
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
        if (
            _agentic or not self.ci_friendly or not getattr(self, "_guided", True)
        ) and default is not None:
            return default

        try:
            return str(click.prompt(message, default=default, hide_input=hide_input))
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
