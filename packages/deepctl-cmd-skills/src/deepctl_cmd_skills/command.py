"""Skills command for managing AI coding assistant integrations."""

from __future__ import annotations

import importlib.metadata
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
from deepctl_core.auth import AuthManager
from deepctl_core.base_group_command import BaseGroupCommand
from deepctl_core.client import DeepgramClient
from deepctl_core.config import Config
from deepctl_core.output import print_info, print_success, print_warning
from deepctl_core.skill_bundle import (
    DEFAULT_SKILLS_REF,
    REF_ENV_VAR,
    SkillFetchError,
)
from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from deepctl_core.skill_bundle import RepoSkill
    from deepctl_core.skill_generator import SkillInstallReport

console = Console()

#: Printed after a successful install: the one skill that needs a follow-up.
_MCP_HINT = (
    "One of the installed skills is 'setup-mcp' — ask your assistant to "
    "set up the Deepgram MCP server, or run 'dg mcp' to start it directly."
)


class SkillsCommand(BaseGroupCommand):
    """AI coding assistant skill management."""

    name = "skills"
    help = "Install Deepgram agent skills into AI coding assistants"
    examples = [
        "dg skills status",
        "dg skills install",
        "dg skills install --all",
        "dg skills install --all --ref main",
        "dg skills update",
        "dg skills remove --all",
    ]
    agent_help = (
        "Install the Deepgram agent skills from github.com/deepgram/skills "
        "into AI coding assistants (Claude Code, Codex, Gemini CLI, Cursor, "
        "OpenCode, Cline). Each skill is copied as a folder into the "
        "user-scope skills directory that tool reads. Use 'skills status' to "
        "see which tools are present and where their skills go, "
        "'skills install' to install, 'skills list' to see what is installed "
        "and from which upstream ref, and 'skills update' to reinstall. "
        "Installs are pinned to a released deepgram/skills tag; override with "
        "--ref or DEEPCTL_SKILLS_REF. A fetch failure exits non-zero with "
        "nothing written rather than installing a subset. Those skills "
        "directories are shared with the user's own skills and other "
        "publishers', so every subcommand operates only on the folders "
        "deepctl recorded installing: install refuses to overwrite an "
        "unrecorded folder of the same name and remove never deletes one. "
        "'skills setup' runs the same install, so the same rules apply to it. "
        "A recorded path that is now a symlink is not deepctl's either — "
        "install and update exit non-zero naming it, and remove reports it "
        "instead of deleting through it. A recorded folder remove cannot "
        "delete stays recorded and remove exits non-zero, so a later remove "
        "or update can still reach it. 'dg login' and 'dg plugin' follow the "
        "same ownership rules but warn and exit 0 instead of failing."
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
        @click.option(
            "--ref",
            "ref",
            metavar="REF",
            help=(
                "Install from this deepgram/skills git ref instead of the "
                f"pinned release ({DEFAULT_SKILLS_REF}). Also settable with "
                f"{REF_ENV_VAR}."
            ),
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
            help="Reinstall every installed tool's skills from upstream",
        )
        @click.option(
            "--ref",
            "ref",
            metavar="REF",
            help=(
                "Install from this deepgram/skills git ref instead of the "
                f"pinned release ({DEFAULT_SKILLS_REF})."
            ),
        )
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
            help=("Remove only the skill folders deepctl installed"),
        )
        @click.option(
            "--all",
            "remove_all",
            is_flag=True,
            help="Remove deepctl's skills from every tool it installed into",
        )
        @click.option(
            "--cli",
            "cli_name",
            help="Remove deepctl's skills for a specific AI CLI",
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
            help="Show what is installed, from which upstream ref, and where",
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
        @click.option(
            "--ref",
            "ref",
            metavar="REF",
            help=(
                "Install from this deepgram/skills git ref instead of the "
                f"pinned release ({DEFAULT_SKILLS_REF})."
            ),
        )
        def setup_cmd(**kwargs: Any) -> None:
            pass

        setup_cmd.callback = context_wrapper(
            lambda config, auth_manager, client, **kw: self._handle_setup(**kw)
        )
        return setup_cmd

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _installed_records(state: dict[str, Any]) -> dict[str, Any] | None:
        """``installed_skills`` as a map of tool to record, or None.

        A hand-edited ``skills.json`` can carry a list or a string here,
        and every handler below iterates it. None means the file cannot
        be read as records, so the caller says which file is wrong --
        ``main.py`` would otherwise turn the attribute error into
        "Error: 'list' object has no attribute 'keys'", which names a
        Python type rather than the file to fix.
        """
        installed = state.get("installed_skills")
        return installed if isinstance(installed, dict) else None

    @staticmethod
    def _unreadable_records(consequence: str) -> str:
        """Message for a ``skills.json`` whose records are not a map."""
        from deepctl_core.skill_generator import _STATE_FILE

        return (
            "deepctl cannot read its own records: 'installed_skills' in "
            f"{_STATE_FILE} is not a set of entries. {consequence}"
        )

    def _fetch_skills(self, ref: str | None) -> list[RepoSkill]:
        """Fetch the upstream skills, or fail the command outright.

        A partial install is worse than none: once the files are on disk
        there is nothing to tell the user that four of fourteen skills
        arrived. ClickException is what main.py turns into exit 1.
        """
        from deepctl_core.skill_generator import fetch_repo_skills

        try:
            return fetch_repo_skills(ref, force=True)
        except SkillFetchError as exc:
            raise click.ClickException(
                f"{exc}\n\nNo skills were installed. Retry when the "
                "network is available, or pass --ref to pick another "
                "deepgram/skills revision."
            )

    def _install_for(
        self,
        generators: list[Any],
        state: dict[str, Any],
        ref: str | None,
    ) -> SkillInstallReport:
        """Install for every selected tool, then report what landed.

        The ownership contract lives in
        :func:`deepctl_core.skill_generator.install_skills_for`, which
        login and the plugin refresh call too. All this adds is the exit
        code: a collision is a failed command, not a warning, so it
        becomes the ClickException main.py turns into exit 1.
        """
        from deepctl_core.skill_generator import (
            SkillOwnershipError,
            collect_command_metadata,
            install_skills_for,
        )

        def announce(gen: Any, paths: list[Path]) -> None:
            # Printed as each tool lands, not once they all have: a later
            # tool failing must not hide the ones that did install and
            # are now recorded as deepctl's.
            print_success(
                f"  {gen.display_name}: {len(paths)} skills -> {gen.skills_root()}"
            )

        try:
            report = install_skills_for(
                generators,
                state,
                commands=collect_command_metadata(),
                version=_deepctl_version(),
                ref=ref,
                fetch=lambda: self._fetch_skills(ref),
                on_installed=announce,
            )
        except SkillOwnershipError as exc:
            raise click.ClickException(str(exc))

        for gen in report.unsupported:
            print_warning(f"  {gen.manual_hint()}")
        return report

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _handle_status(self) -> None:
        """Show detected AI CLIs and whether skills are installed."""
        from deepctl_core.skill_generator import (
            SKILLS_CLI_HINT,
            get_all_generators,
            get_skills_state,
            recorded_skill_paths,
        )

        generators = get_all_generators()
        state = get_skills_state()
        installed = self._installed_records(state)

        table = Table(title="AI Coding Assistant Status")
        table.add_column("CLI", style="cyan", no_wrap=True)
        table.add_column("Detected", style="white")
        table.add_column("Deepgram Skills", style="white")
        table.add_column("Skills Directory", style="dim")

        for gen in generators:
            detected = gen.detect()
            root = gen.skills_root()
            # Deepgram's own skills only. These directories are shared, so
            # counting every folder in them would report the user's skills
            # and other publishers' skills as deepctl installs.
            count = len(
                gen.installed_skill_paths(recorded_skill_paths(state, gen.cli_name))
            )
            if root is None:
                installed_cell = "[dim]n/a[/dim]"
                root_cell = "[dim]no skills directory[/dim]"
            else:
                installed_cell = f"[green]{count}[/green]" if count else "[dim]No[/dim]"
                root_cell = _tilde(root)
            table.add_row(
                gen.display_name,
                "[green]Yes[/green]" if detected else "[dim]No[/dim]",
                installed_cell,
                root_cell,
            )

        console.print(table)

        if installed is None:
            # The table above is still worth printing -- it is how a user
            # finds which tools are present and where their skills go --
            # but every count in it read as zero, so say why.
            print_warning(
                self._unreadable_records(
                    "Nothing is counted as deepctl's until that file is "
                    "fixed or deleted."
                )
            )

        if any(g.detect() and g.skills_root() is None for g in generators):
            print_info(
                f"Tools with no skills directory: install with '{SKILLS_CLI_HINT}'."
            )

        detected_count = sum(1 for g in generators if g.detect())
        if detected_count > 0 and not installed:
            print_info(
                "\nRun 'deepctl skills install' to set up AI assistant integrations."
            )

    def _handle_install(
        self,
        install_all: bool = False,
        cli_name: str | None = None,
        ref: str | None = None,
    ) -> None:
        """Detect AI CLIs, prompt the user, and install the Deepgram skills."""
        from deepctl_core.skill_generator import (
            detect_ai_clis,
            get_all_generators,
            get_skills_state,
            save_skills_state,
        )

        # If a specific CLI was requested, filter
        if cli_name:
            generators = [g for g in get_all_generators() if g.cli_name == cli_name]
            if not generators:
                # ClickException, not print_error + return: a bare return
                # exits 0, and the README documents 1 for a command that
                # fails. main.py prints the message and exits 1.
                raise click.ClickException(
                    f"Unknown AI CLI: {cli_name}. "
                    "Run 'deepctl skills status' to see supported CLIs."
                )
            if not generators[0].detect():
                print_warning(
                    f"{generators[0].display_name} was not detected on this system."
                )
                if not self.confirm("Install anyway?", default=False):
                    # Abort rather than return: these subcommands are plain
                    # click callbacks, so nothing maps a returned result to an
                    # exit code and a bare return exits 0 -- indistinguishable
                    # from a successful install. main.py turns Abort into
                    # exit 2, which it reserves for user cancellation.
                    raise click.Abort()
        else:
            generators = detect_ai_clis()

        if not generators:
            print_info("No AI coding assistants detected.")
            print_info("Supported CLIs:")
            for g in get_all_generators():
                print_info(f"  - {g.display_name}")
            return

        selected = [
            g
            for g in generators
            if install_all
            or cli_name
            or self.confirm(
                f"Install Deepgram skills for {g.display_name}?",
                default=True,
            )
        ]

        state = get_skills_state()
        report = self._install_for(selected, state, ref)
        save_skills_state(state)

        if report.total_written:
            print_success(
                f"\nInstalled {report.total_written} skill folder(s) "
                f"from deepgram/skills@{report.ref}"
            )
            print_info(_MCP_HINT)
        elif not report.unsupported:
            print_info("No skills were installed.")

    def _handle_update(self, ref: str | None = None) -> None:
        """Reinstall every installed tool's skills from upstream."""
        from deepctl_core.skill_generator import (
            get_all_generators,
            get_skills_state,
            save_skills_state,
        )

        state = get_skills_state()
        installed = self._installed_records(state)

        if installed is None:
            print_info(
                self._unreadable_records(
                    "There is no list of tools to update. Fix or delete "
                    "that file, then run 'dg skills install'."
                )
            )
            return

        if not installed:
            print_info("No skills installed. Run 'deepctl skills install' first.")
            return

        generators = {g.cli_name: g for g in get_all_generators()}
        targets = []
        for cli_key in list(installed.keys()):
            gen = generators.get(cli_key)
            if gen is None:
                print_warning(f"Unknown CLI '{cli_key}', skipping.")
                continue
            # A tool with no skills directory is handed on rather than
            # filtered out here. install_skills_for() is what cleans up
            # its deepctl <= 0.3.0 files and drops the record that should
            # never have existed; skipping it meant update printed the
            # same "no skills directory" warning on every run forever,
            # with no command on this path that would ever resolve it.
            targets.append(gen)

        if not targets:
            print_info("Nothing to update.")
            return

        report = self._install_for(targets, state, ref)
        save_skills_state(state)
        if report.written:
            print_success(
                f"Updated {len(report.written)} tool(s) from "
                f"deepgram/skills@{report.ref}"
            )
            print_info(_MCP_HINT)
        elif not report.unsupported:
            print_info("Nothing to update.")

    def _handle_remove(
        self,
        remove_all: bool = False,
        cli_name: str | None = None,
    ) -> None:
        """Remove the skill folders deepctl recorded installing.

        Only those. These are shared directories, so a folder deepctl has
        no record of installing belongs to the user or another publisher
        and is never deleted — including when ``skills.json`` is gone, in
        which case there is nothing deepctl can prove it owns.
        """
        from deepctl_core.skill_generator import (
            get_all_generators,
            get_skills_state,
            recorded_skill_paths,
            save_skills_state,
        )

        state = get_skills_state()
        installed = self._installed_records(state)

        # Reported separately from "nothing installed": the records were
        # not deleted, the file is unreadable, and only one of those two
        # is fixed by deleting folders.
        if installed is None:
            print_info(
                self._unreadable_records(
                    "It will not guess which folders are its, so nothing "
                    "was removed. Fix or delete that file, then remove "
                    "the skill folders by hand."
                )
            )
            return

        if not installed:
            print_info(
                "No skills are installed according to deepctl's records. "
                "deepctl only removes folders it recorded installing, so if "
                "those records were deleted, remove the skill folders by hand."
            )
            return

        generators = {g.cli_name: g for g in get_all_generators()}

        if cli_name:
            targets = [cli_name] if cli_name in installed else []
            if not targets:
                # Same contract as the unknown-CLI path above: asking to
                # remove something that is not there is a failed command,
                # which the README documents as exit 1, not 0.
                raise click.ClickException(f"No skills installed for '{cli_name}'.")
        elif remove_all:
            targets = list(installed.keys())
        else:
            # A usage error, which main.py turns into exit 1. Printing
            # the hint and exiting 0 made "you forgot a flag"
            # indistinguishable from "everything was removed".
            raise click.UsageError("Specify --all to remove all, or --cli NAME.")

        total_removed = 0
        tools_cleaned = 0
        stranded_total = 0
        cleaned_in_place = 0
        # A filesystem call in here can raise -- clean_legacy unlinks
        # and rewrites files without a guard. The records already
        # updated describe deletions that have happened, so they are
        # saved either way rather than thrown away with the traceback.
        try:
            for cli_key in targets:
                gen = generators.get(cli_key)
                if gen is None:
                    # No generator, so no skills root to check a path against
                    # and nothing deepctl can prove about these folders. The
                    # record is the only thing it can honestly drop.
                    print_warning(
                        f"  Unknown CLI '{cli_key}': dropping its record. Delete "
                        "any folders it left behind by hand."
                    )
                    del state["installed_skills"][cli_key]
                    continue

                recorded = recorded_skill_paths(state, cli_key)
                owned = gen.owned_skill_paths(recorded)
                removed = gen.remove(recorded)
                # remove() also reports the deepctl <= 0.3.0 artifacts it
                # cleaned up, and those do not always go away: a shared
                # context file keeps the user's own text, and the legacy
                # command directory keeps a command they added. A bare
                # "Removed" would name a path they can still see.
                deleted = [p for p in removed if not p.exists()]
                for p in removed:
                    if p.exists():
                        print_info(f"  Removed deepctl's content from {p}")
                    else:
                        print_info(f"  Removed {p}")
                total_removed += len(deleted)
                # Counted apart from total_removed, which is a count of
                # *folders that are gone*. A path deepctl only cut its own
                # content out of is still there, so it must not inflate
                # that number -- but it did happen, and the closing
                # "Nothing was removed." would contradict the line naming
                # it that was just printed.
                cleaned_in_place += len(removed) - len(deleted)
                if deleted:
                    tools_cleaned += 1

                # A recorded path deepctl can no longer claim — the folder
                # was replaced by a symlink, or the entry was hand-edited to
                # point outside the skills root. Never deleted, so say where
                # it is instead of dropping the record silently.
                for path in self._unownable(recorded, owned):
                    print_warning(
                        f"  {gen.display_name}: {path} is no longer deepctl's to "
                        "delete. Remove it by hand."
                    )

                # Ownership outlives a failed deletion. rmtree can lose to a
                # permission error or a read-only mount, and dropping the
                # record then would strand Deepgram's own folders: the next
                # update refuses to overwrite what it cannot prove is its,
                # and the next remove has nothing left to act on.
                stranded = [p for p in owned if p.exists()]
                if stranded:
                    stranded_total += len(stranded)
                    entry = state["installed_skills"][cli_key]
                    entry["paths"] = [str(p) for p in stranded]
                    entry["skills"] = [p.name for p in stranded]
                    print_warning(
                        f"  {gen.display_name}: {len(stranded)} folder(s) could not "
                        "be removed and are still recorded as deepctl's. Fix the "
                        f"permissions and run 'dg skills remove --cli {cli_key}' "
                        "again."
                    )
                else:
                    del state["installed_skills"][cli_key]
                    if not removed:
                        print_warning(f"  {gen.display_name}: nothing left to remove.")
        finally:
            save_skills_state(state)

        if total_removed:
            print_success(
                f"Removed {total_removed} folder(s) from {tools_cleaned} tool(s)."
            )
        elif not stranded_total and not cleaned_in_place:
            print_info("Nothing was removed.")

        if stranded_total:
            # The command did not do what was asked, and the README
            # documents 1 for a failed command. Raised after the state
            # is saved, so the retained ownership survives the failure.
            raise click.ClickException(
                f"{stranded_total} recorded skill folder(s) could not be "
                "removed. They are still recorded as deepctl's, so fix the "
                "permissions and run the remove again."
            )

    @staticmethod
    def _unownable(recorded: list[str], owned: list[Path]) -> list[Path]:
        """Recorded paths still on disk that deepctl may no longer touch.

        ``owned`` has already dropped them — a symlink standing where a
        skill folder was, or an entry pointing outside the skills root.
        Reported rather than deleted, because deleting either one is how
        deepctl would destroy something that is not its.

        Both sides are compared after ``expanduser()``, the same form
        :meth:`SkillGenerator.owned_skill_paths` keeps, so a record
        written as ``~/.claude/skills/api`` is not mistaken for a path
        deepctl may no longer touch. A relative entry is skipped for the
        same reason that method drops it: it names nothing deepctl can
        resolve, and resolving it against the working directory would
        point this warning at an unrelated file.
        """
        keep = {str(p) for p in owned}
        out = []
        seen = set(keep)
        for entry in recorded:
            path = Path(entry).expanduser()
            if not path.is_absolute() or str(path) in seen:
                continue
            seen.add(str(path))
            if path.is_symlink() or path.exists():
                out.append(path)
        return out

    def _handle_list(self) -> None:
        """Show installed skills with locations, versions and upstream ref."""
        from deepctl_core.skill_generator import get_skills_state

        state = get_skills_state()
        installed = self._installed_records(state)

        if installed is None:
            print_info(
                self._unreadable_records(
                    "There is nothing it can list. Fix or delete that "
                    "file, then run 'dg skills install'."
                )
            )
            return

        if not installed:
            print_info(
                "No skills installed. Run 'deepctl skills install' to get started."
            )
            return

        table = Table(title="Installed Skills")
        table.add_column("CLI", style="cyan", no_wrap=True)
        table.add_column("deepctl", style="green")
        table.add_column("Skills ref", style="green")
        table.add_column("Skills", style="white")
        table.add_column("Location", style="dim")

        for cli_key, info in installed.items():
            names = info.get("skills") or []
            paths = info.get("paths") or []
            location = _tilde(Path(paths[0]).parent) if paths else "?"
            table.add_row(
                cli_key,
                info.get("version", "?"),
                info.get("skills_ref", "?"),
                f"{len(names) or len(paths)}",
                location,
            )

        console.print(table)
        print_info("[dim]Run 'dg skills update' to reinstall from upstream.[/dim]")

    def _handle_setup(self, install_all: bool = False, ref: str | None = None) -> None:
        """Interactive first-run setup: detect AI tools and install skills.

        Fetches the Deepgram skills from the deepgram/skills repo and
        installs each one, as a folder, into the skills directory the
        selected tool actually reads.
        """
        import sys

        from deepctl_core.skill_generator import (
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

        # 3. Install the upstream skills for each selected tool
        console.print("\n[blue]Installing Deepgram skills...[/blue]")

        state = get_skills_state()
        report = self._install_for(selected, state, ref)
        save_skills_state(state)

        if report.total_written:
            console.print()
            print_success(
                f"Setup complete - {report.total_written} skill folder(s) from "
                f"deepgram/skills@{report.ref}"
            )
            print_info(_MCP_HINT)
        elif not report.unsupported:
            print_info("No skills were installed.")


def _tilde(path: Path) -> str:
    """Render a path under the user's home as ~/... so tables stay readable."""
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)


def _deepctl_version() -> str:
    """Return the installed deepctl version, or a placeholder."""
    try:
        return importlib.metadata.version("deepctl")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0"
