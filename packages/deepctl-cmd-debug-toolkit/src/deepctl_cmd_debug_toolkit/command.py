"""Toolkit subcommand group — runs field support scripts from deepgram/support-toolkit."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

import click
from deepctl_core import AuthManager, BaseGroupCommand, Config
from rich.console import Console

from .fetcher import get_cached_manifest, get_or_fetch_script, refresh_manifest
from .models import ToolkitScript

console = Console()


class ToolkitCommand(BaseGroupCommand):
    """Runs field engineering scripts from deepgram/support-toolkit."""

    name = "toolkit"
    help = (
        "Run field engineering diagnostic scripts fetched from "
        "deepgram/support-toolkit. Scripts are downloaded on first use, "
        "verified against their GitHub blob SHA, and cached locally."
    )
    short_help = "Field support scripts (deepgram/support-toolkit)"

    requires_auth = False
    requires_project = False
    ci_friendly = True
    invoke_without_command = False

    examples = [
        "dg debug toolkit refresh",
        "dg debug toolkit latency wss://api.deepgram.com/v1/listen",
        "dg debug toolkit latency wss://api.deepgram.com/v1/listen --duration=60",
    ]

    def setup_commands(self) -> list[click.Command]:
        """Return commands derived from the cached manifest.

        Intentionally never performs network I/O — reads disk cache only so
        that every `dg` invocation stays fast. Users run `refresh` once to
        populate the cache; individual script commands fetch lazily at invoke
        time.
        """
        commands: list[click.Command] = [_make_refresh_command()]

        manifest = get_cached_manifest()
        if manifest:
            for name, entry in manifest.commands.items():
                commands.append(_make_script_command(name, entry))

        return commands

    def handle_group(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: Any,
        **kwargs: Any,
    ) -> None:
        # BaseGroupCommand.handle() calls handle_group() even when a subcommand
        # is being invoked — guard here so we don't add noise to subcommand runs.
        if click.get_current_context().invoked_subcommand is not None:
            return

        manifest = get_cached_manifest()
        if not manifest:
            console.print(
                "[yellow]No toolkit scripts cached yet.[/yellow]\n"
                "Run [bold]dg debug toolkit refresh[/bold] to fetch the "
                "latest scripts from deepgram/support-toolkit."
            )
            return

        console.print("[bold]Available toolkit scripts:[/bold]\n")
        for name, entry in manifest.commands.items():
            console.print(f"  [bold]dg debug toolkit {name}[/bold]")
            console.print(f"  [dim]{entry.description}[/dim]\n")


def _make_refresh_command() -> click.Command:
    """Static 'refresh' command — always available regardless of cache state."""

    @click.command("refresh", help="Fetch the latest script list from deepgram/support-toolkit.")
    def _refresh() -> None:
        try:
            manifest = refresh_manifest(console)
        except Exception as exc:
            console.print(f"[red]Error fetching manifest:[/red] {exc}")
            sys.exit(1)

        console.print(
            f"\n[green]✓[/green] Toolkit manifest updated "
            f"({len(manifest.commands)} script(s) available):\n"
        )
        for name, entry in manifest.commands.items():
            console.print(f"  [bold]dg debug toolkit {name}[/bold]")
            console.print(f"  [dim]{entry.description}[/dim]\n")

    return _refresh


def _make_script_command(name: str, entry: ToolkitScript) -> click.Command:
    """Create a Click command that fetches, verifies, and runs a toolkit script."""

    @click.command(
        name,
        help=entry.description,
        # Forward all unrecognised args directly to the underlying script.
        context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    )
    @click.argument("script_args", nargs=-1, type=click.UNPROCESSED)
    def _run(script_args: tuple[str, ...]) -> None:
        try:
            script_path = get_or_fetch_script(entry, console)
        except Exception as exc:
            console.print(f"[red]Error fetching script:[/red] {exc}")
            sys.exit(1)

        env = os.environ.copy()

        if entry.pass_api_key:
            try:
                api_key = AuthManager(Config()).get_api_key()
                if api_key:
                    env["DEEPGRAM_API_KEY"] = api_key
            except Exception:
                pass  # Script will surface its own missing-key error

        result = subprocess.run(
            [sys.executable, str(script_path)] + list(script_args),
            env=env,
        )
        sys.exit(result.returncode)

    return _run
