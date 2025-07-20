#!/usr/bin/env python3
"""Simple script to demonstrate credential verification functionality."""

from rich.console import Console
from deepctl_core import AuthManager, Config
import sys
import os
from pathlib import Path

# Add the src directory to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


console = Console()


def main():
    """Demonstrate credential verification."""
    console.print(
        "[bold blue]deepctl Credential Verification Demo[/bold blue]\n")
    console.print(
        "This script demonstrates how the CLI validates credentials.")

    # Create config and auth manager
    config = Config()
    auth_manager = AuthManager(config)

    # Check if credentials are available
    api_key = auth_manager.get_api_key()
    project_id = auth_manager.get_project_id()

    if not api_key:
        console.print("[yellow]No API key found.[/yellow]")
        console.print(
            "Please set DEEPGRAM_API_KEY environment variable or run 'deepctl login'")
        return

    if not project_id:
        console.print("[yellow]No project ID found.[/yellow]")
        console.print(
            "Please set DEEPGRAM_PROJECT_ID environment variable or run 'deepctl login --project-id <id>'")
        return

    # Display current credentials (masked)
    console.print(f"[dim]API Key:[/dim] ****{api_key[-4:]}")
    console.print(f"[dim]Project ID:[/dim] {project_id}")
    console.print()

    # Verify credentials
    console.print("[blue]Verifying credentials...[/blue]")
    success, message, error_type = auth_manager.verify_credentials()

    if success:
        console.print(f"[green]✓[/green] {message}")
        console.print("\n[green]Your credentials are valid![/green]")
    else:
        console.print(f"[red]✗[/red] {message}")

        if error_type == "auth":
            console.print("\n[yellow]This is an API key issue.[/yellow]")
            console.print("Your API key may have expired or been revoked.")
            console.print("Run 'deepctl login' to re-authenticate.")
        elif error_type == "project":
            console.print("\n[yellow]This is a project ID issue.[/yellow]")
            console.print(
                "The project may have been deleted or you may not have access.")
            console.print(
                "Run 'deepctl login --project-id <valid-id>' to set a valid project.")
        elif error_type == "network":
            console.print("\n[yellow]This is a network issue.[/yellow]")
            console.print(
                "Please check your internet connection and try again.")


if __name__ == "__main__":
    main()
