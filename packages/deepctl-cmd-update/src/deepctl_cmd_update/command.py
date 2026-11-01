"""Update command implementation."""

import importlib.metadata
import shlex
import subprocess
from typing import Any

from deepctl_core import (
    BaseCommand,
    Config,
    get_console,
    print_error,
    print_info,
    print_success,
    print_warning,
)
from rich.panel import Panel
from rich.prompt import Confirm

from .installation import InstallationDetector
from .models import UpdateResult
from .version_check import VersionChecker, format_version_message


# asyncio (and with it ssl) loads only when an update check runs, so
# `dg update --help` doesn't import it.
def __getattr__(name: str) -> Any:
    if name == "asyncio":
        import asyncio

        return asyncio
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class UpdateCommand(BaseCommand):
    """Check for and install updates."""

    name = "update"
    help = "Check for and install updates to deepctl"
    requires_auth = False  # Update command doesn't need authentication

    examples = [
        "dg update",
        "dg update --check-only",
    ]
    agent_help = (
        "Check for and install updates to deepctl. Can check PyPI for newer "
        "versions and apply the update using the appropriate method (pip, "
        "pipx, uv, or Homebrew)."
    )

    def get_arguments(self) -> list[dict[str, Any]]:
        """Get command arguments and options."""
        return [
            {
                "names": ["--check-only", "--check"],
                "help": "Only check for updates without installing",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--force"],
                "help": "Force update even if already up to date",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--yes", "-y"],
                "help": "Skip confirmation prompt",
                "is_flag": True,
                "is_option": True,
            },
        ]

    def handle(
        self,
        config: Config,
        auth_manager: Any,  # Not used for update command
        client: Any,  # Not used for update command
        **kwargs: Any,
    ) -> UpdateResult | dict[str, Any]:
        """Handle the update command execution.

        Returns the model rather than a dict wherever the exit code has to
        be non-zero: BaseCommand.exit_code_for reads `.status` off the
        result, and a dict has none, so a dict always exits 0. The three
        failure paths carry status="error" (exit 1) and the decline path
        status="cancelled" (exit 2). output_result unwraps either, so the
        JSON payload is the same shape from both.
        """
        import asyncio

        console = get_console()

        # Extract arguments from kwargs
        check_only = kwargs.get("check_only", False)
        force = kwargs.get("force", False)
        yes = kwargs.get("yes", False)

        # Initialize version checker
        # Get current version from package metadata if possible
        try:
            current_version = importlib.metadata.version("deepctl")
        except importlib.metadata.PackageNotFoundError:
            current_version = "0.0.0"

        version_checker = VersionChecker(config, current_version)

        # Check for updates
        with console.status("Checking for updates..."):
            try:
                version_info = asyncio.run(version_checker.check_version(force=True))
            except Exception as e:
                print_error(f"Failed to check for updates: {e}")
                # status="error" on the model, not a dict: see the docstring.
                # A command that failed has to exit 1, as the README says.
                return UpdateResult(
                    status="error",
                    success=False,
                    message=f"Failed to check for updates: {e}",
                )

        # Display version info
        message = format_version_message(version_info)

        if version_info.update_available:
            console.print(
                Panel(message, title="Update Available", border_style="yellow")
            )
        else:
            print_success(message)
            if not force:
                return UpdateResult(
                    success=True,
                    message=message,
                    current_version=version_info.current_version,
                    latest_version=version_info.latest_version,
                    update_available=False,
                ).model_dump()

        # If check-only, stop here
        if check_only:
            return UpdateResult(
                success=True,
                message=message,
                current_version=version_info.current_version,
                latest_version=version_info.latest_version,
                update_available=version_info.update_available,
            ).model_dump()

        # Detect installation method
        print_info("Detecting installation method...")
        detector = InstallationDetector()
        install_info = detector.detect()

        # Display installation info
        console.print(f"Installation method: [cyan]{install_info.method.value}[/cyan]")
        console.print(f"Installation path: [dim]{install_info.path}[/dim]")
        if install_info.virtual_env:
            console.print("[yellow]Virtual environment detected[/yellow]")

        # Get update command
        update_command = detector.get_update_command(install_info.method)

        if not update_command:
            # Special handling for system/development installations
            instructions = detector.get_update_instructions(install_info)
            print_warning(instructions)
            return UpdateResult(
                success=False,
                message=instructions,
                current_version=version_info.current_version,
                latest_version=version_info.latest_version,
                update_available=version_info.update_available,
                installation_method=install_info.method.value,
            ).model_dump()

        # Show update command
        update_display = shlex.join(update_command)
        console.print(f"\nUpdate command: [green]{update_display}[/green]")

        # Confirm update
        if not yes and not Confirm.ask("\nDo you want to proceed with the update?"):
            print_info("Update cancelled")
            # Return the model, not .model_dump(): BaseCommand.exit_code_for
            # reads `.status` off the result, and a plain dict has none -- a
            # declined update used to exit 0 while its own payload said
            # "Update cancelled by user". status="cancelled" is the documented
            # exit 2.
            return UpdateResult(
                status="cancelled",
                success=False,
                message="Update cancelled by user",
                current_version=version_info.current_version,
                latest_version=version_info.latest_version,
                update_available=version_info.update_available,
                installation_method=install_info.method.value,
            )

            # Execute update
        print_info("Updating deepctl...")
        try:
            # Run the update command synchronously
            result = subprocess.run(
                update_command,
                capture_output=True,
                text=True,
            )

            if result.returncode == 0:
                print_success(
                    f"Successfully updated to version {version_info.latest_version}"
                )

                return UpdateResult(
                    success=True,
                    message=f"Successfully updated to version {version_info.latest_version}",
                    current_version=version_info.current_version,
                    latest_version=version_info.latest_version,
                    update_available=False,
                    installation_method=install_info.method.value,
                ).model_dump()
            else:
                error_msg = result.stderr if result.stderr else "Unknown error"
                print_error(f"Update failed: {error_msg}")

                # Provide fallback instructions
                print_info("\nYou can try updating manually:")
                console.print(f"[yellow]{shlex.join(update_command)}[/yellow]")

                return UpdateResult(
                    status="error",
                    success=False,
                    message=f"Update failed: {error_msg}",
                    current_version=version_info.current_version,
                    latest_version=version_info.latest_version,
                    update_available=version_info.update_available,
                    installation_method=install_info.method.value,
                )

        except Exception as e:
            print_error(f"Failed to execute update: {e}")

            # Provide fallback instructions
            print_info("\nYou can try updating manually:")
            console.print(f"[yellow]{update_command}[/yellow]")

            return UpdateResult(
                status="error",
                success=False,
                message=f"Failed to execute update: {e}",
                current_version=version_info.current_version,
                latest_version=version_info.latest_version,
                update_available=version_info.update_available,
                installation_method=install_info.method.value,
            )
