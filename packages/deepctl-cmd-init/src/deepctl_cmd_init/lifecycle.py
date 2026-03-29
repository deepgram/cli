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

# ── Prerequisite tools ────────────────────────────────────────────────────────

#: Each entry: tool key → (display name, install hint)
PREREQS: dict[str, tuple[str, str]] = {
    "git": (
        "git",
        "https://git-scm.com/downloads",
    ),
    "node": (
        "Node.js",
        "https://nodejs.org",
    ),
    "npm": (
        "npm",
        "bundled with Node.js — reinstall from https://nodejs.org",
    ),
    "make": (
        "make",
        "macOS: xcode-select --install  |  Linux: sudo apt-get install make",
    ),
    "curl": (
        "curl",
        "macOS: brew install curl  |  Linux: sudo apt-get install curl",
    ),
}


def check_prereqs() -> list[tuple[str, str, str]]:
    """Check for required tools.

    Returns:
        List of ``(key, display_name, install_hint)`` tuples for every missing tool.
        Empty list means all tools are present.
    """
    missing: list[tuple[str, str, str]] = []
    for key, (name, hint) in PREREQS.items():
        if shutil.which(key) is None:
            missing.append((key, name, hint))
    return missing


# ── Git clone ─────────────────────────────────────────────────────────────────


def clone_template(github_url: str, target_dir: Path) -> None:
    """Clone a template repository, keeping the .git directory intact.

    The cloned directory is a fully functional git repository so users can
    commit changes, add their own remote, and push to their own repo.

    Args:
        github_url: Full GitHub URL to clone.
        target_dir: Local directory to clone into (must not exist).

    Raises:
        RuntimeError: If the directory already exists or git clone fails.
    """
    if target_dir.exists():
        raise RuntimeError(f"Directory already exists: {target_dir}")

    result = subprocess.run(
        ["git", "clone", github_url, str(target_dir)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git clone failed:\n{result.stderr.strip()}")


# ── .env injection ────────────────────────────────────────────────────────────


def inject_env(target_dir: Path, api_key: str) -> None:
    """Create or update DEEPGRAM_API_KEY in .env inside target_dir.

    Args:
        target_dir: Directory containing the cloned template.
        api_key: Deepgram API key to inject.
    """
    env_path = target_dir / ".env"

    if env_path.exists():
        content = env_path.read_text()
        if "DEEPGRAM_API_KEY=" in content and "DEEPGRAM_API_KEY=$" not in content:
            console.print("[dim]DEEPGRAM_API_KEY already set in .env[/dim]")
            return
        # Replace placeholder or append
        if "DEEPGRAM_API_KEY=" in content:
            lines = content.splitlines()
            new_lines = [
                f"DEEPGRAM_API_KEY={api_key}"
                if ln.startswith("DEEPGRAM_API_KEY=")
                else ln
                for ln in lines
            ]
            env_path.write_text("\n".join(new_lines) + "\n")
        else:
            with open(env_path, "a") as f:
                if not content.endswith("\n"):
                    f.write("\n")
                f.write(f"DEEPGRAM_API_KEY={api_key}\n")
    else:
        env_path.write_text(f"DEEPGRAM_API_KEY={api_key}\n")

    console.print("[green]✓[/green] Injected DEEPGRAM_API_KEY into .env")


# ── make init ─────────────────────────────────────────────────────────────────


def run_make_init(dest: Path) -> bool:
    """Run ``make check-prereqs && make init`` in the cloned directory.

    Used for starter repos that ship a Makefile with these standard targets.

    Args:
        dest: The cloned project directory.

    Returns:
        True if both commands succeeded, False otherwise.
    """
    makefile = dest / "Makefile"
    if not makefile.exists():
        return False

    for target in ("check-prereqs", "init"):
        result = subprocess.run(
            ["make", target],
            cwd=str(dest),
        )
        if result.returncode != 0:
            console.print(
                f"\n[red]✗[/red] [bold]make {target}[/bold] failed "
                f"(exit {result.returncode})"
            )
            if target == "check-prereqs":
                console.print(
                    "[dim]Fix the missing prerequisites above and run "
                    f"[bold]make init[/bold] inside [cyan]{dest.name}[/cyan][/dim]"
                )
            else:
                console.print(
                    f"[dim]Re-run manually: cd {dest.name} && make init[/dim]"
                )
            return False

    return True


# ── TOML lifecycle (fallback) ─────────────────────────────────────────────────


def run_lifecycle_step(step: LifecycleStep, cwd: Path) -> bool:
    """Execute a lifecycle step from deepgram.toml.

    Used as a fallback for repos that don't have a Makefile.

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
                f"[red]✗[/red] Command failed (exit {result.returncode}): {cmd}"
            )
            return False

    return True
