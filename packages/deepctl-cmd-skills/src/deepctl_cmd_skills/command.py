"""Skills command for managing AI coding assistant integrations."""

from __future__ import annotations

import contextlib
import importlib.metadata
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
from deepctl_core.auth import AuthManager
from deepctl_core.base_group_command import BaseGroupCommand
from deepctl_core.client import DeepgramClient
from deepctl_core.config import Config
from deepctl_core.output import print_error, print_info, print_success, print_warning
from rich.console import Console
from rich.markup import escape
from rich.table import Table

if TYPE_CHECKING:
    from collections.abc import Iterator

    from deepctl_core.skill_generator import SkillGenerator

console = Console()
_REF_OPTION = click.option("--ref", metavar="REF", help="deepgram/skills git ref")


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
        "Install the Deepgram skills as folders for AI coding assistants (Claude "
        "Code, Codex, Gemini CLI, Cursor, OpenCode, Cline). 'skills status' shows "
        "detected tools, 'skills install' adds the folders, 'skills update' "
        "replaces them and 'skills remove' deletes only folders deepctl installed."
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
            help="Detect AI CLIs and install the Deepgram skill folders",
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
        @_REF_OPTION
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
            help="Replace the installed skill folders deepctl owns",
        )
        @_REF_OPTION
        def update_cmd(**kwargs: Any) -> None:
            pass

        update_cmd.callback = context_wrapper(
            lambda config, auth_manager, client, **kw: self._handle_update(**kw)
        )
        return update_cmd

    def _create_remove_command(self, context_wrapper: Any) -> click.Command:
        """Create the remove subcommand."""

        @click.command(
            name="remove",
            help="Remove the skill folders deepctl installed",
        )
        @click.option(
            "--all",
            "remove_all",
            is_flag=True,
            help="Remove all installed skill folders",
        )
        @click.option(
            "--cli",
            "cli_name",
            help="Remove skill folders for a specific AI CLI",
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
            help="Show installed skill folders and their source",
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
        @_REF_OPTION
        def setup_cmd(**kwargs: Any) -> None:
            pass

        setup_cmd.callback = context_wrapper(
            lambda config, auth_manager, client, **kw: self._handle_setup(**kw)
        )
        return setup_cmd

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _install(self, plan: list[tuple[SkillGenerator, str]]) -> None:
        """Fetch, preflight every tool, then install tool by tool."""
        from deepctl_core import skill_bundle
        from deepctl_core import skill_generator as sg

        for gen, _ in plan:
            if gen.skills_root() is None:
                print_warning(escape(sg._msg("E15", gen)))
        plan = [(g, r) for g, r in plan if g.skills_root() is not None]
        bundles = {
            r: skill_bundle.fetch_skill_bundle(r)
            for r in dict.fromkeys(r for _, r in plan)
        }
        unproven, edited = list[Path](), list[Path]()
        for ref, skills in bundles.items():
            u, e = sg.install_conflicts([g for g, r in plan if r == ref], skills)
            unproven, edited = unproven + u, edited + e
        if unproven or edited:  # Nothing is written for any tool.
            raise sg.SkillOwnershipError(unproven, edited)
        count, tools, v = 0, 0, _version()
        for gen, ref in plan:
            try:
                paths, leftover = sg.install_tool(gen, bundles[ref], ref=ref, version=v)
            except sg.SkillInstallError as exc:
                if exc.leftover:
                    print_warning(escape(sg._msg("E12", staging=exc.leftover)))
                raise
            done = f"{gen.display_name}: installed {_n(len(paths), 'skill')} in {gen.skills_root()}."
            print_success(escape(done))
            if leftover:  # Only this run's staging (SF3).
                print_warning(escape(sg._msg("E12", staging=leftover)))
            count, tools = count + len(paths), tools + 1
        if count:
            labels = ", ".join(dict.fromkeys(_label(r) for _, r in plan))
            done = f"Installed {_n(count, 'skill folder')} for {_n(tools, 'tool')} from deepgram/skills {labels}."
            print_success(escape(done))
            if any(g.cli_name == "claude" for g, _ in plan):  # Every tool installed.
                print_info(
                    "In Claude Code, run /setup-mcp to configure the Deepgram MCP server."
                )
        else:
            print_info("No skills were installed.")

    def _handle_status(self) -> None:
        """Show detected AI CLIs and the skill folders deepctl installed."""
        from deepctl_core import skill_generator as sg

        with _clean_errors():
            state = sg.get_skills_state()
        recs, legacy = state.get("skill_folders", {}), state["installed_skills"]
        table = Table(title="AI Coding Assistant Status")
        for col in ("Tool", "Detected", "Skills folder", "Installed"):
            table.add_column(
                col, style="cyan" if col == "Tool" else "white", no_wrap=col == "Tool"
            )
        notes, old = list[str](), list[str]()
        detected = False
        for gen in sg.get_all_generators():
            st, found = sg.tool_status(gen, state), gen.detect()
            detected = detected or found
            table.add_row(
                gen.display_name,
                "[green]Yes[/green]" if found else "[dim]No[/dim]",
                escape(str(st.root or "none")),
                str(len(st.kinds["ok"])) if st.root else "-",
            )
            notes += [sg._msg("E14", dest=p) for p in st.kinds["unproven"]]
            notes += [sg._msg("E24", dest=p) for p in st.kinds["edited"]]
            notes += [sg._msg("E25", dest=p) for p in st.kinds["unreadable"]]
            notes += [sg._msg("E16", path=p) for p in st.leftovers]
            if found and st.root is None:
                notes.append(sg._msg("E15", gen))
            if st.root and gen.cli_name in legacy and gen.cli_name not in recs:
                old.append(gen.display_name)
        console.print(table)
        for note in dict.fromkeys(notes):
            print_warning(escape(note))
        if old:
            note = f"Files from deepctl 0.3.x are recorded for {', '.join(old)}; run 'dg skills update' to install the skill folders, and the old files stay until a later release."
            print_info(escape(note))
        if detected and not recs and not legacy:
            print_info("Run 'dg skills install' to set up AI assistant integrations.")

    def _handle_install(
        self,
        install_all: bool = False,
        cli_name: str | None = None,
        ref: str | None = None,
    ) -> None:
        """Detect AI CLIs, prompt the user, and install the skill folders."""
        from deepctl_core.skill_bundle import resolve_skills_ref
        from deepctl_core.skill_generator import (
            detect_ai_clis,
            get_all_generators,
            get_skills_state,
        )

        # If a specific CLI was requested, filter
        if cli_name:
            generators = [g for g in get_all_generators() if g.cli_name == cli_name]
            if not generators:
                # ClickException, not print_error + return: a bare return
                # exits 0, and the README documents 1 for a command that
                # fails. main.py prints the message and exits 1.
                raise click.ClickException(
                    f"Unknown AI CLI: {cli_name}; "
                    "run 'dg skills status' to see the supported CLIs."
                )
            if not generators[0].detect():
                print_warning(
                    f"{generators[0].display_name} was not detected on this system."
                )
                if not self.confirm("Install anyway?", default=False):
                    # Abort rather than return: these subcommands are plain
                    # click callbacks, so nothing maps a returned result to an
                    # exit code and a bare return exits 0 -- indistinguishable
                    # from a successful install. main.py turns Abort into the
                    # documented exit 2 for user cancellation.
                    raise click.Abort()
        else:
            generators = detect_ai_clis()

        if not generators:
            print_info("No AI coding assistants detected.")
            print_info("Supported CLIs:")
            for g in get_all_generators():
                print_info(f"  - {g.display_name}")
            return

        with _clean_errors():
            get_skills_state()  # A corrupt file fails before any prompt.
            selected = [
                gen
                for gen in generators
                if install_all
                or cli_name
                or self.confirm(
                    f"Install deepctl skills for {gen.display_name}?",
                    default=True,
                )
            ]
            ref = resolve_skills_ref(ref)
            self._install([(g, ref) for g in selected])

    def _handle_update(self, ref: str | None = None) -> None:
        """Replace the recorded skill folders from --ref, the env var or each recorded ref."""
        from deepctl_core import skill_generator as sg

        with _clean_errors():
            state = sg.get_skills_state()
            names = [*state.get("skill_folders", {}), *state["installed_skills"]]
            gens = {g.cli_name: g for g in sg.get_all_generators()}
            for name in dict.fromkeys(n for n in names if n not in gens):
                print_warning(escape(f"Unknown CLI '{name}', skipping."))
            targets = [g for g in gens.values() if g.cli_name in names]
            if not targets:
                hint = "No skills are installed, so there is nothing to update; run 'dg skills install' first."
                print_info(hint)
                return
            self._install([(g, sg._ref_for(g.cli_name, state, ref)) for g in targets])

    def _handle_remove(
        self,
        remove_all: bool = False,
        cli_name: str | None = None,
    ) -> None:
        """Remove the skill folders deepctl installed and can still prove."""
        from deepctl_core import skill_generator as sg

        with _clean_errors():
            state = sg.get_skills_state()
        recs, legacy = state.get("skill_folders", {}), state["installed_skills"]
        installed = list(dict.fromkeys([*recs, *legacy]))

        if not installed:
            print_info("No skills are installed.")
            return

        generators = {g.cli_name: g for g in sg.get_all_generators()}

        if cli_name:
            targets = [cli_name] if cli_name in installed else []
            if not targets:
                # Same contract as the unknown-CLI path above: asking to
                # remove something that is not there is a failed command,
                # which the README documents as exit 1, not 0.
                raise click.ClickException(f"No skills installed for '{cli_name}'.")
        elif remove_all:
            targets = installed
        else:
            print_info("Specify --all to remove all, or --cli NAME.")
            return

        removed, tools, failed = 0, 0, False
        with _clean_errors(), sg._state_lock():  # Once for all tools: no wait per tool.
            for cli_key in targets:
                gen = generators.get(cli_key)
                if gen is None:
                    print_warning(escape(f"Unknown CLI '{cli_key}', skipping."))
                    continue
                try:
                    res = sg.remove_tool(gen)
                except sg.SkillInstallError as exc:  # E18, E21, E9c: go on (N10).
                    print_error(escape(str(exc)))
                    failed = True
                    continue
                notes = [sg._msg("E26", dest=p) for p in res.left_alone]
                notes += [sg._msg("E23", dest=p) for p in res.edited]
                notes += [sg._msg("E4", dest=d, aside=a) for d, a in res.moved]
                notes += [sg._msg("E29", dest=d, aside=a) for d, a in res.stranded]
                notes += [sg._msg("E13", dest=d, reason=why) for d, why in res.kept]
                notes += [sg._msg("E12", staging=res.leftover)] if res.leftover else []
                for note in notes:
                    print_warning(escape(note))
                paths = legacy.get(cli_key, {}).get("paths", [])
                old = recs.get(cli_key, {}).get("v03") or any(
                    Path(p).parent != gen.skills_root() for p in paths
                )  # Not 0.3.x if every path is one of our folders.
                v03 = "files from deepctl 0.3.x stay until a later release."
                c10 = f"For {gen.display_name}, {v03}"
                if cli_key not in recs:
                    c10 = f"{gen.display_name} has no skill folders recorded, so nothing was removed{'; ' + v03 if old else '.'}"
                if old or cli_key not in recs:
                    print_info(escape(c10))
                failed = failed or bool(
                    res.kept or res.moved or res.stranded or res.leftover
                )
                removed, tools = removed + len(res.removed), tools + bool(res.removed)
        if failed:
            c11 = "Some skill folders were not fully removed; fix the problems listed above."
            raise click.ClickException(c11)
        done = f"Removed {_n(removed, 'skill folder')} from {_n(tools, 'tool')}."
        print_success(done)

    def _handle_list(self) -> None:
        """Show the recorded skill folders with their source ref."""
        from deepctl_core import skill_generator as sg

        with _clean_errors():
            state = sg.get_skills_state()
        recs = state.get("skill_folders", {})

        if not recs:
            print_info(
                "No skill folders are installed; run 'dg skills install' to get started."
            )
            return

        table = Table(title="Installed Skills")
        table.add_column("Tool", style="cyan", no_wrap=True)
        table.add_column("Ref", style="green")
        table.add_column("Skills", style="white")
        table.add_column("Folder", style="white")

        for gen in sg.get_all_generators():
            if gen.cli_name in recs:
                st = sg.tool_status(gen, state)
                ok = f"{len(st.kinds['ok'])}/{len(recs[gen.cli_name].get('folders', {}))}"
                ref = escape(_label(st.skills_ref) if st.skills_ref else "-")
                table.add_row(gen.display_name, ref, ok, escape(str(st.root)))

        console.print(table)

        auto = state.get("auto_update", True)
        if auto:
            print_info(
                "[dim]Auto-update is enabled — skills regenerate on plugin changes.[/dim]"
            )

    def _handle_setup(self, install_all: bool = False, ref: str | None = None) -> None:
        """Interactive first-run setup: detect AI tools and install skills.

        Downloads the deepgram/skills bundle (the pin unless --ref or the env
        var names another ref) and installs every skill as a folder in each
        selected AI coding tool's skills directory.
        """
        import sys

        from deepctl_core.skill_bundle import resolve_skills_ref
        from deepctl_core.skill_generator import detect_ai_clis, get_all_generators

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
        elif is_tty and self._guided:
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

        # 3. Install the skill folders for the selected tools
        console.print("\n[blue]Installing Deepgram skills...[/blue]")
        with _clean_errors():
            ref = resolve_skills_ref(ref)
            self._install([(g, ref) for g in selected])


def _version() -> str:
    try:
        return importlib.metadata.version("deepctl")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0"


def _n(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _label(ref: str) -> str:
    from deepctl_core import skill_bundle

    pinned = ref == skill_bundle.DEFAULT_SKILLS_COMMIT
    return skill_bundle.DEFAULT_SKILLS_RELEASE if pinned else ref


@contextlib.contextmanager
def _clean_errors() -> Iterator[None]:
    """Turn a skills error into a one-line ClickException (exit 1)."""
    from deepctl_core.skill_bundle import SkillFetchError
    from deepctl_core.skill_generator import SkillInstallError

    try:
        yield
    except (SkillInstallError, SkillFetchError) as exc:
        raise click.ClickException(escape(str(exc))) from exc  # main.py prints markup.
