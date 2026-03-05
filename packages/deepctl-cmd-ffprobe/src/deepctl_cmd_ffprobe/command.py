"""FFprobe configuration command for deepctl."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any

from deepctl_core import AuthManager, BaseCommand, BaseResult, Config, DeepgramClient
from deepctl_shared_utils import (
    get_ffprobe_path,
    print_ffprobe_install_instructions,
)
from rich.console import Console

from .models import FfprobeResult

console = Console()


class FfprobeCommand(BaseCommand):
    """Manage ffprobe configuration for audio analysis."""

    name = "ffprobe"
    help = (
        "Show ffprobe status or configure a custom path. "
        "ffprobe is used for audio analysis in transcribe and debug commands."
    )
    short_help = "Configure ffprobe"

    requires_auth = False
    requires_project = False
    ci_friendly = True

    examples = [
        "dg ffprobe",
        "dg ffprobe --path /opt/homebrew/bin/ffprobe",
        "dg ffprobe --path $(which ffprobe)",
        "dg ffprobe --reset",
    ]
    agent_help = (
        "Show ffprobe detection status or set a custom path. "
        "ffprobe (part of FFmpeg) is used for audio file analysis. "
        "Use --path to store a custom binary path, --reset to clear it."
    )

    def get_arguments(self) -> list[dict[str, Any]]:
        """Get command arguments and options."""
        return [
            {
                "names": ["--path"],
                "help": "Set custom ffprobe binary path",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--reset"],
                "help": "Clear stored path, revert to auto-detection",
                "is_flag": True,
                "is_option": True,
            },
        ]

    def handle(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> BaseResult:
        """Handle ffprobe command."""
        path = kwargs.get("path")
        reset = kwargs.get("reset", False)

        if path and reset:
            console.print("[red]Error:[/red] Cannot use --path and --reset together")
            return BaseResult(
                status="error",
                message="Cannot use --path and --reset together",
            )

        if reset:
            return self._handle_reset(config)

        if path:
            return self._handle_set_path(config, path)

        return self._handle_status(config)

    def _handle_set_path(self, config: Config, path: str) -> BaseResult:
        """Store a custom ffprobe path."""
        # Validate the path
        if not os.path.isfile(path):
            console.print(f"[red]Error:[/red] File not found: {path}")
            return BaseResult(status="error", message=f"File not found: {path}")

        if not os.access(path, os.X_OK):
            console.print(f"[red]Error:[/red] File is not executable: {path}")
            return BaseResult(status="error", message=f"File is not executable: {path}")

        # Store in config
        config._config.tools.ffprobe_path = path
        config.save()

        version = self._get_version(path)
        console.print(f"[green]✓[/green] ffprobe path stored: {path}")
        if version:
            console.print(f"  Version: {version}")

        return FfprobeResult(
            status="success",
            message=f"ffprobe path set to {path}",
            stored_path=path,
            version=version,
            available=True,
        )

    def _handle_reset(self, config: Config) -> BaseResult:
        """Clear stored ffprobe path."""
        config._config.tools.ffprobe_path = None
        config.save()

        console.print("[green]✓[/green] Stored ffprobe path cleared")

        # Show auto-detected path
        auto_path = shutil.which("ffprobe")
        if auto_path:
            console.print(f"  Auto-detected: {auto_path}")
        else:
            console.print("  [yellow]ffprobe not found in PATH[/yellow]")

        return FfprobeResult(
            status="success",
            message="Stored ffprobe path cleared",
            detected_path=auto_path,
            available=auto_path is not None,
        )

    def _handle_status(self, config: Config) -> BaseResult:
        """Show ffprobe status."""
        stored_path = config.get("tools.ffprobe_path")
        auto_path = shutil.which("ffprobe")
        effective_path = get_ffprobe_path(config)

        if stored_path:
            console.print(f"  Stored path:    {stored_path}")
        if auto_path:
            console.print(f"  Auto-detected:  {auto_path}")

        if effective_path:
            version = self._get_version(effective_path)
            console.print(f"  Active path:    [green]{effective_path}[/green]")
            if version:
                console.print(f"  Version:        {version}")
            console.print("\n[green]✓[/green] ffprobe is available")

            return FfprobeResult(
                status="success",
                message="ffprobe is available",
                stored_path=stored_path,
                detected_path=auto_path,
                version=version,
                available=True,
            )
        else:
            print_ffprobe_install_instructions()

            return FfprobeResult(
                status="error",
                message="ffprobe not found",
                available=False,
            )

    def _get_version(self, ffprobe_path: str) -> str | None:
        """Get ffprobe version string."""
        try:
            result = subprocess.run(
                [ffprobe_path, "-version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                # First line is typically "ffprobe version X.Y.Z ..."
                first_line = result.stdout.strip().split("\n")[0]
                return first_line
        except (subprocess.TimeoutExpired, OSError):
            pass
        return None
