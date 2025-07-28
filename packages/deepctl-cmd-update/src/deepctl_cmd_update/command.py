"""Update command implementation."""

import asyncio
import subprocess
from typing import Any, Dict
from deepctl_core import (
    BaseCommand,
    Config,
    get_console,
    print_error,
    print_info,
    print_success,
    print_warning,
)

from .installation import InstallationDetector
from .version_check import VersionChecker, format_version_message
from rich.panel import Panel
from rich.prompt import Confirm

from .models import UpdateResult


class UpdateCommand(BaseCommand):
    """Check for and install updates."""

    name = "update"
    help = "Check for and install updates to deepctl"
    requires_auth = False  # Update command doesn't need authentication

    def get_arguments(self) -> list[dict[str, Any]]:
        """Get command arguments and options."""
        return [
            {
                "names": ["--check-only"],
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
    ) -> Dict[str, Any]:
        """Handle the update command execution."""
        console = get_console()

        # Extract arguments from kwargs
        check_only = kwargs.get("check_only", False)
        force = kwargs.get("force", False)
        yes = kwargs.get("yes", False)

        # Initialize version checker
        # Get current version from package metadata if possible
        try:
            import deepctl
            current_version = getattr(deepctl, "__version__", "0.1.5")
        except ImportError:
            current_version = "0.1.5"

        version_checker = VersionChecker(config, current_version)

        # Check for updates
        with console.status("Checking for updates..."):
            try:
                version_info = asyncio.run(
                    version_checker.check_version(force=True))
            except Exception as e:
                print_error(f"Failed to check for updates: {e}")
                return UpdateResult(
                    success=False,
                    message=f"Failed to check for updates: {e}",
                ).model_dump()

        # Display version info
        message = format_version_message(version_info)

        if version_info.update_available:
            console.print(
                Panel(message, title="Update Available", border_style="yellow"))
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

        # Store installation info for future use
        config._set_config_value(
            "update.installation_method", install_info.method)
        config._set_config_value("update.installation_path", install_info.path)
        config.save()

        # Display installation info
        console.print(
            f"Installation method: [cyan]{install_info.method}[/cyan]")
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
                installation_method=install_info.method,
            ).model_dump()

        # Show update command
        console.print(f"\nUpdate command: [green]{update_command}[/green]")

        # Confirm update
        if not yes:
            if not Confirm.ask("\nDo you want to proceed with the update?"):
                print_info("Update cancelled")
                return UpdateResult(
                    success=False,
                    message="Update cancelled by user",
                    current_version=version_info.current_version,
                    latest_version=version_info.latest_version,
                    update_available=version_info.update_available,
                    installation_method=install_info.method,
                ).model_dump()

                # Execute update
        print_info("Updating deepctl...")
        try:
            # Run the update command synchronously
            result = subprocess.run(
                update_command,
                shell=True,
                capture_output=True,
                text=True,
            )

            if result.returncode == 0:
                print_success(
                    f"Successfully updated to version {version_info.latest_version}")

                # Clear version cache
                config._set_config_value("update.cached_version_info", None)
                config.save()

                return UpdateResult(
                    success=True,
                    message=f"Successfully updated to version {version_info.latest_version}",
                    current_version=version_info.current_version,
                    latest_version=version_info.latest_version,
                    update_available=False,
                    installation_method=install_info.method,
                ).model_dump()
            else:
                error_msg = result.stderr if result.stderr else "Unknown error"
                print_error(f"Update failed: {error_msg}")

                # Provide fallback instructions
                print_info("\nYou can try updating manually:")
                console.print(f"[yellow]{update_command}[/yellow]")

                return UpdateResult(
                    success=False,
                    message=f"Update failed: {error_msg}",
                    current_version=version_info.current_version,
                    latest_version=version_info.latest_version,
                    update_available=version_info.update_available,
                    installation_method=install_info.method,
                ).model_dump()

        except Exception as e:
            print_error(f"Failed to execute update: {e}")

            # Provide fallback instructions
            print_info("\nYou can try updating manually:")
            console.print(f"[yellow]{update_command}[/yellow]")

            return UpdateResult(
                success=False,
                message=f"Failed to execute update: {e}",
                current_version=version_info.current_version,
                latest_version=version_info.latest_version,
                update_available=version_info.update_available,
                installation_method=install_info.method,
            ).model_dump()
