"""Clone template repos and run lifecycle steps from deepgram.toml."""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from pathlib import Path

    from .models import LifecycleStep

console = Console()


def clone_template(github_url: str, target_dir: Path) -> None:
    """Clone a template repo with depth 1, then remove .git.

    Args:
        github_url: Full GitHub URL to clone.
        target_dir: Local directory to clone into.

    Raises:
        RuntimeError: If git clone fails.
    """
    if target_dir.exists():
        raise RuntimeError(f"Directory already exists: {target_dir}")

    result = subprocess.run(
        ["git", "clone", "--depth", "1", github_url, str(target_dir)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git clone failed: {result.stderr.strip()}")

    # Remove .git so the user starts fresh
    git_dir = target_dir / ".git"
    if git_dir.exists():
        shutil.rmtree(git_dir)


def inject_env(target_dir: Path, api_key: str) -> None:
    """Create or append DEEPGRAM_API_KEY to .env in target_dir.

    Args:
        target_dir: Directory containing the cloned template.
        api_key: Deepgram API key to inject.
    """
    env_path = target_dir / ".env"

    # Check if key already present
    if env_path.exists():
        content = env_path.read_text()
        if "DEEPGRAM_API_KEY=" in content:
            console.print("[dim]DEEPGRAM_API_KEY already set in .env, skipping[/dim]")
            return
        # Append with newline separator
        with open(env_path, "a") as f:
            if not content.endswith("\n"):
                f.write("\n")
            f.write(f"DEEPGRAM_API_KEY={api_key}\n")
    else:
        env_path.write_text(f"DEEPGRAM_API_KEY={api_key}\n")

    console.print("[green]Injected DEEPGRAM_API_KEY into .env[/green]")


def run_lifecycle_step(step: LifecycleStep, cwd: Path) -> bool:
    """Execute a lifecycle step from deepgram.toml.

    Args:
        step: The lifecycle step definition.
        cwd: Working directory for command execution.

    Returns:
        True if the step succeeded, False otherwise.
    """
    if step.command is None:
        return True

    commands = step.command if isinstance(step.command, list) else [step.command]

    for cmd in commands:
        if step.message:
            console.print(f"[blue]{step.message}[/blue]")
        else:
            console.print(f"[dim]Running: {cmd}[/dim]")

        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            shell=True,
        )
        if result.returncode != 0:
            console.print(
                f"[red]Command failed (exit {result.returncode}):[/red] {cmd}"
            )
            return False

    return True
