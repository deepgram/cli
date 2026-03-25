"""Skills command for managing AI coding assistant integrations."""

from __future__ import annotations

import importlib.metadata
from datetime import datetime, timezone
from typing import Any

import click
from deepctl_core.auth import AuthManager
from deepctl_core.base_group_command import BaseGroupCommand
from deepctl_core.client import DeepgramClient
from deepctl_core.config import Config
from deepctl_core.output import print_error, print_info, print_success, print_warning
from rich.console import Console
from rich.table import Table

console = Console()


class SkillsCommand(BaseGroupCommand):
    """AI coding assistant skill management."""

    name = "skills"
    help = "Manage AI coding assistant integrations for deepctl"
    examples = [
        "dg skills status",
        "dg skills install",
        "dg skills install --all",
        "dg skills update",
        "dg skills remove --all",
    ]
    agent_help = (
        "Manage skill files that teach AI coding assistants (Claude Code, "
        "Codex, Gemini CLI, etc.) how to use deepctl. Use 'skills status' to "
        "detect which AI CLIs are installed, 'skills install' to generate "
        "integration files, and 'skills update' to regenerate after plugin changes."
    )

    def execute(self, ctx: click.Context, **kwargs: Any) -> None:
        """Execute skills group command."""
        config = ctx.obj.get("config") if ctx.obj else None
        if not config:
            config = Config()

        auth_manager = AuthManager(config)
        client = DeepgramClient(config, auth_manager)

        ctx.obj = ctx.obj or {}
        ctx.obj["config"] = config
        ctx.obj["auth_manager"] = auth_manager
        ctx.obj["client"] = client

        super().execute(ctx, **kwargs)

    def setup_commands(self) -> list[click.Command]:
        """Set up skills management subcommands."""

        def context_wrapper(func: Any) -> Any:
            """Wrap subcommand to provide config and auth."""

            @click.pass_context
            def wrapper(ctx: click.Context, /, **kwargs: Any) -> Any:
                if ctx.parent and ctx.parent.obj:
                    config = ctx.parent.obj.get("config")
                    auth_manager = ctx.parent.obj.get("auth_manager")
                    client = ctx.parent.obj.get("client")
                    if config and auth_manager and client:
                        return func(config, auth_manager, client, **kwargs)

                config = Config()
                auth_manager = AuthManager(config)
                client = DeepgramClient(config, auth_manager)
                return func(config, auth_manager, client, **kwargs)

            wrapper.__name__ = func.__name__
            wrapper.__doc__ = func.__doc__
            return wrapper

        return [
            self._create_status_command(context_wrapper),
            self._create_install_command(context_wrapper),
            self._create_update_command(context_wrapper),
            self._create_remove_command(context_wrapper),
            self._create_list_command(context_wrapper),
            self._create_setup_command(context_wrapper),
        ]

    # ------------------------------------------------------------------
    # Subcommand factories
    # ------------------------------------------------------------------

    def _create_status_command(self, context_wrapper: Any) -> click.Command:
        """Create the status subcommand."""

        @click.command(
            name="status",
            help="Show detected AI CLIs and skill installation status",
        )
        def status_cmd(**kwargs: Any) -> None:
            pass

        status_cmd.callback = context_wrapper(
            lambda config, auth_manager, client, **kw: self._handle_status()
        )
        return status_cmd

    def _create_install_command(self, context_wrapper: Any) -> click.Command:
        """Create the install subcommand."""

        @click.command(
            name="install",
            help="Detect AI CLIs and install skill files",
        )
        @click.option(
            "--all",
            "install_all",
            is_flag=True,
            help="Install for all detected CLIs without prompting",
        )
        @click.option(
            "--cli",
            "cli_name",
            help="Install for a specific AI CLI only",
        )
        def install_cmd(**kwargs: Any) -> None:
            pass

        install_cmd.callback = context_wrapper(
            lambda config, auth_manager, client, **kw: self._handle_install(**kw)
        )
        return install_cmd

    def _create_update_command(self, context_wrapper: Any) -> click.Command:
        """Create the update subcommand."""

        @click.command(
            name="update",
            help="Regenerate all installed skill files from current metadata",
        )
        def update_cmd(**kwargs: Any) -> None:
            pass

        update_cmd.callback = context_wrapper(
            lambda config, auth_manager, client, **kw: self._handle_update()
        )
        return update_cmd

    def _create_remove_command(self, context_wrapper: Any) -> click.Command:
        """Create the remove subcommand."""

        @click.command(
            name="remove",
            help="Remove installed skill files",
        )
        @click.option(
            "--all",
            "remove_all",
            is_flag=True,
            help="Remove all installed skill files",
        )
        @click.option(
            "--cli",
            "cli_name",
            help="Remove skill files for a specific AI CLI",
        )
        def remove_cmd(**kwargs: Any) -> None:
            pass

        remove_cmd.callback = context_wrapper(
            lambda config, auth_manager, client, **kw: self._handle_remove(**kw)
        )
        return remove_cmd

    def _create_list_command(self, context_wrapper: Any) -> click.Command:
        """Create the list subcommand."""

        @click.command(
            name="list",
            help="Show installed skills with paths and versions",
        )
        def list_cmd(**kwargs: Any) -> None:
            pass

        list_cmd.callback = context_wrapper(
            lambda config, auth_manager, client, **kw: self._handle_list()
        )
        return list_cmd

    def _create_setup_command(self, context_wrapper: Any) -> click.Command:
        """Create the setup subcommand — interactive first-run wizard."""

        @click.command(
            name="setup",
            help="Interactive setup: detect AI tools and install Deepgram skills",
        )
        @click.option(
            "--all",
            "install_all",
            is_flag=True,
            help="Install for all detected tools without prompting",
        )
        def setup_cmd(**kwargs: Any) -> None:
            pass

        setup_cmd.callback = context_wrapper(
            lambda config, auth_manager, client, **kw: self._handle_setup(**kw)
        )
        return setup_cmd

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _handle_status(self) -> None:
        """Show detected AI CLIs and whether skills are installed."""
        from deepctl_core.skill_generator import get_all_generators, get_skills_state

        generators = get_all_generators()
        state = get_skills_state()
        installed = state.get("installed_skills", {})

        table = Table(title="AI Coding Assistant Status")
        table.add_column("CLI", style="cyan", no_wrap=True)
        table.add_column("Detected", style="white")
        table.add_column("Skills Installed", style="white")

        for gen in generators:
            detected = gen.detect()
            has_skills = gen.cli_name in installed
            table.add_row(
                gen.display_name,
                "[green]Yes[/green]" if detected else "[dim]No[/dim]",
                "[green]Yes[/green]" if has_skills else "[dim]No[/dim]",
            )

        console.print(table)

        detected_count = sum(1 for g in generators if g.detect())
        if detected_count > 0 and not installed:
            print_info(
                "\nRun 'deepctl skills install' to set up AI assistant integrations."
            )

    def _handle_install(
        self,
        install_all: bool = False,
        cli_name: str | None = None,
    ) -> None:
        """Detect AI CLIs, prompt user, generate & install skill files."""
        from deepctl_core.skill_generator import (
            _commands_hash,
            collect_command_metadata,
            detect_ai_clis,
            get_all_generators,
            get_skills_state,
            save_skills_state,
        )

        # If a specific CLI was requested, filter
        if cli_name:
            generators = [g for g in get_all_generators() if g.cli_name == cli_name]
            if not generators:
                print_error(
                    f"Unknown AI CLI: {cli_name}. "
                    "Run 'deepctl skills status' to see supported CLIs."
                )
                return
            if not generators[0].detect():
                print_warning(
                    f"{generators[0].display_name} was not detected on this system."
                )
                if not click.confirm("Install anyway?", default=False):
                    return
        else:
            generators = detect_ai_clis()

        if not generators:
            print_info("No AI coding assistants detected.")
            print_info("Supported CLIs:")
            for g in get_all_generators():
                print_info(f"  - {g.display_name}")
            return

        # Collect metadata
        commands = collect_command_metadata()
        try:
            version = importlib.metadata.version("deepctl")
        except importlib.metadata.PackageNotFoundError:
            version = "0.0.0"

        state = get_skills_state()
        total_written: list[str] = []

        for gen in generators:
            if (
                not install_all
                and not cli_name
                and not click.confirm(
                    f"Install deepctl skills for {gen.display_name}?",
                    default=True,
                )
            ):
                continue

            paths = gen.install(commands, version)
            cmd_hash = _commands_hash(commands)
            state["installed_skills"][gen.cli_name] = {
                "paths": [str(p) for p in paths],
                "installed_at": datetime.now(timezone.utc).isoformat(),
                "version": version,
                "commands_hash": cmd_hash,
            }
            for p in paths:
                total_written.append(str(p))
                print_success(f"  Wrote {p}")

        save_skills_state(state)

        if total_written:
            print_success(f"\nInstalled skills: {len(total_written)} file(s)")
        else:
            print_info("No skills were installed.")

    def _handle_update(self) -> None:
        """Regenerate all installed skill files from current metadata."""
        from deepctl_core.skill_generator import (
            _commands_hash,
            collect_command_metadata,
            get_all_generators,
            get_skills_state,
            save_skills_state,
        )

        state = get_skills_state()
        installed = state.get("installed_skills", {})

        if not installed:
            print_info("No skills installed. Run 'deepctl skills install' first.")
            return

        commands = collect_command_metadata()
        try:
            version = importlib.metadata.version("deepctl")
        except importlib.metadata.PackageNotFoundError:
            version = "0.0.0"

        generators = {g.cli_name: g for g in get_all_generators()}
        updated_count = 0

        for cli_key in list(installed.keys()):
            gen = generators.get(cli_key)
            if gen is None:
                print_warning(f"Unknown CLI '{cli_key}', skipping.")
                continue

            paths = gen.install(commands, version)
            cmd_hash = _commands_hash(commands)
            state["installed_skills"][cli_key].update(
                {
                    "paths": [str(p) for p in paths],
                    "version": version,
                    "commands_hash": cmd_hash,
                }
            )
            updated_count += 1
            for p in paths:
                print_success(f"  Updated {p}")

        save_skills_state(state)
        print_success(f"Updated {updated_count} skill(s)")

    def _handle_remove(
        self,
        remove_all: bool = False,
        cli_name: str | None = None,
    ) -> None:
        """Remove installed skill files."""
        from deepctl_core.skill_generator import (
            get_all_generators,
            get_skills_state,
            save_skills_state,
        )

        state = get_skills_state()
        installed = state.get("installed_skills", {})

        if not installed:
            print_info("No skills are installed.")
            return

        generators = {g.cli_name: g for g in get_all_generators()}

        if cli_name:
            targets = [cli_name] if cli_name in installed else []
            if not targets:
                print_error(f"No skills installed for '{cli_name}'.")
                return
        elif remove_all:
            targets = list(installed.keys())
        else:
            print_info("Specify --all to remove all, or --cli NAME.")
            return

        for cli_key in targets:
            gen = generators.get(cli_key)
            if gen:
                removed = gen.remove()
                for p in removed:
                    print_info(f"  Removed {p}")
            del state["installed_skills"][cli_key]

        save_skills_state(state)
        print_success(f"Removed {len(targets)} skill(s).")

    def _handle_list(self) -> None:
        """Show installed skills with paths and versions."""
        from deepctl_core.skill_generator import get_skills_state

        state = get_skills_state()
        installed = state.get("installed_skills", {})

        if not installed:
            print_info(
                "No skills installed. Run 'deepctl skills install' to get started."
            )
            return

        table = Table(title="Installed Skills")
        table.add_column("CLI", style="cyan", no_wrap=True)
        table.add_column("Version", style="green")
        table.add_column("Paths", style="white")

        for cli_key, info in installed.items():
            paths = "\n".join(info.get("paths", []))
            table.add_row(cli_key, info.get("version", "?"), paths)

        console.print(table)

        auto = state.get("auto_update", True)
        if auto:
            print_info(
                "[dim]Auto-update is enabled — skills regenerate on plugin changes.[/dim]"
            )

    def _handle_setup(self, install_all: bool = False) -> None:
        """Interactive first-run setup: detect AI tools and install skills.

        Downloads Deepgram skills from the deepgram/skills GitHub repo and
        installs both the repo skills and the local deepctl command reference
        for each selected AI coding tool.
        """
        import sys

        from deepctl_core.skill_generator import (
            _commands_hash,
            collect_command_metadata,
            detect_ai_clis,
            get_all_generators,
            get_skills_state,
            save_skills_state,
        )

        is_tty = sys.stdout.isatty()

        # 1. Detect AI coding tools
        detected = detect_ai_clis()

        if not detected:
            print_info("No AI coding assistants detected on this system.")
            print_info("Supported tools:")
            for g in get_all_generators():
                print_info(f"  - {g.display_name}")
            return

        # 2. Interactive selection (or --all for CI)
        if install_all:
            selected = list(detected)
        elif is_tty:
            console.print("\n[bold]Detected AI coding tools:[/bold]\n")
            for i, g in enumerate(detected, 1):
                console.print(f"  [green]{i}.[/green] {g.display_name}")
            console.print()

            raw = click.prompt(
                "Install skills for (comma-separated numbers, all, or none)",
                default="all",
            )
            raw = raw.strip().lower()

            if raw in ("none", "n", "0"):
                print_info("No skills installed.")
                return

            if raw == "all":
                selected = list(detected)
            else:
                indices: set[int] = set()
                for part in raw.split(","):
                    part = part.strip()
                    if part.isdigit():
                        idx = int(part)
                        if 1 <= idx <= len(detected):
                            indices.add(idx - 1)
                selected = [detected[i] for i in sorted(indices)]

            if not selected:
                print_info("No valid tools selected.")
                return
        else:
            # Non-TTY without --all: install for all detected
            selected = list(detected)

        # 3. Collect command metadata and install for selected tools
        console.print("\n[blue]Installing Deepgram skills...[/blue]")
        commands = collect_command_metadata()
        try:
            version = importlib.metadata.version("deepctl")
        except importlib.metadata.PackageNotFoundError:
            version = "0.0.0"

        state = get_skills_state()
        total_written: list[str] = []

        for gen in selected:
            paths = gen.install(commands, version)
            cmd_hash = _commands_hash(commands)
            state["installed_skills"][gen.cli_name] = {
                "paths": [str(p) for p in paths],
                "installed_at": datetime.now(timezone.utc).isoformat(),
                "version": version,
                "commands_hash": cmd_hash,
            }
            for p in paths:
                total_written.append(str(p))
                print_success(f"  {gen.display_name} → {p}")

        save_skills_state(state)

        if total_written:
            console.print()
            print_success(f"Setup complete — {len(total_written)} file(s) installed")
            console.print(
                "[dim]Run 'dg skills update' after installing new plugins "
                "to keep skills current.[/dim]"
            )
        else:
            print_info("No skills were installed.")
