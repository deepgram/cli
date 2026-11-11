"""Init command for deepctl — scaffold Deepgram starter apps."""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path
from typing import Any

from deepctl_core import (
    AuthenticationError,
    AuthManager,
    BaseCommand,
    Config,
    DeepgramClient,
)
from deepctl_core import messages as core_msg
from deepctl_core.output import _agentic, is_ai_agent, print_error, print_hint
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from . import clones, lifecycle, templates_api
from . import messages as msg
from .models import (
    InitCodedError,
    InitResult,
    LifecycleStep,
    TemplateDetail,
    TemplateInfo,
)

_MAKE_STEPS = ["make check-prereqs", "make init"]


def _hard_signal() -> bool:
    """An AI agent marked the environment, or ``DG_AGENT=1`` says one runs dg."""
    return is_ai_agent() or os.environ.get("DG_AGENT") == "1"


def _person_at_terminal() -> bool:
    """A person can answer here: no agent or automation signal, and stdin
    and stderr are terminals. Otherwise setup needs --yes and the start
    step --start."""
    if _agentic or _hard_signal():
        return False
    try:
        return sys.stdin.isatty() and sys.stderr.isatty()
    except (AttributeError, ValueError):
        return False


def _commands(step: LifecycleStep | None) -> list[str]:
    if step is None or step.command is None:
        return []
    return step.command if isinstance(step.command, list) else [step.command]


def _rerun(template: str, dest: Path, *flags: str) -> str | None:
    """``{prog} init {template} --dir {dest} {flags}``, or None when it
    can't be given as one runnable command."""
    from deepctl_core.transport import prog

    program = prog()
    if program is None:
        return None
    directory = str(dest)
    if os.name == "nt":
        from deepctl_core.nt_command import forward_slashes

        # Forward slashes need no quoting; init reads them back as dest.
        directory = forward_slashes(directory)
    tokens = ["init", template, "--dir", directory, *flags]
    if any(token.startswith("=") for token in tokens):
        return None  # zsh's EQUALS option would expand it
    if os.name == "nt":
        from deepctl_core.nt_command import join

        return join([program, *tokens])
    step = f"{program} {shlex.join(tokens)}"
    return step if shlex.join(shlex.split(step)) == step else None


def _coded(
    error: str,
    code: str,
    hint: str,
    next_step: str | None,
    details: dict[str, Any] | None = None,
) -> InitCodedError:
    """Report a coded error on stderr and return its payload."""
    print_error(escape(error))
    print_hint(escape(hint))
    return InitCodedError(
        error=error, code=code, hint=hint, next_step=next_step, details=details
    )


def _prog_text() -> str:
    """``{prog}`` in a message: the running command, else ``dg``."""
    from deepctl_core.transport import prog

    return prog() or "dg"


def _still_same(dest: Path, identity: tuple[int, int] | None) -> bool:
    """Whether ``dest`` is still the directory dg cloned into or resumed
    in: checked again right before the API key is written there and before
    any setup or start command runs, so a directory swapped in meanwhile
    is refused."""
    return identity is not None and lifecycle.same_directory(dest, *identity)


console = Console()
status_console = Console(stderr=True)


class InitCommand(BaseCommand):
    """Scaffold a Deepgram starter app from the templates gallery."""

    name = "init"
    help = (
        "Browse, clone, and set up Deepgram starter apps.\n\n"
        "Fetches templates from the Deepgram templates gallery. "
        "Run with no arguments for an interactive search-and-select picker, "
        "or pass a template name directly to clone immediately."
    )
    short_help = "Scaffold a Deepgram starter app (alpha)"

    requires_auth = False
    requires_project = False
    ci_friendly = True

    examples = [
        "dg init",
        "dg init --search python",
        "dg init node-transcription",
        "dg init node-transcription --dir ./my-app",
        "dg init node-transcription --install",
        "dg init node-transcription --no-install",
        "dg init node-transcription --yes",
        "dg init --list",
        "dg init --list --search flask",
    ]
    agent_help = (
        "Scaffold a Deepgram starter app from the templates gallery. "
        "Pass the template name/slug directly (e.g. 'node-transcription'). "
        "Use --list to enumerate available templates. "
        "Setup commands (make check-prereqs, make init, or the template's own) "
        "run only with --yes: show them to the user and pass --yes only if the "
        "user approves, or pass --no-install to skip them. The app is started "
        "only with --start. A rerun into the same --dir resumes there. "
        "Requires git, node, npm, make, and curl to be installed."
    )

    def get_arguments(self) -> list[dict[str, Any]]:
        """Get command arguments and options."""
        return [
            {
                "name": "template",
                "help": "Template name/slug to clone (e.g. node-transcription)",
                "type": str,
                "required": False,
                "default": None,
            },
            {
                "names": ["--dir", "-d"],
                "help": "Target directory (default: ./{template-name})",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--search", "-s"],
                "help": "Filter templates by keyword",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--list", "-l"],
                "help": "List available templates and exit",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--install"],
                "help": (
                    "Run the setup commands without asking (in an AI agent "
                    "session, use --yes)"
                ),
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--no-install"],
                "help": "Skip setup step without prompting",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--start"],
                "help": "Run start step after install without prompting",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--no-start"],
                "help": "Skip start step without prompting",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--yes", "-y"],
                "help": (
                    "Approve and run the template's setup commands without "
                    "asking; the only way in an AI agent session"
                ),
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
    ) -> Any:
        """Handle the init command."""
        template_slug: str | None = kwargs.get("template")
        target_dir: str | None = kwargs.get("dir")
        search: str | None = kwargs.get("search")
        list_mode: bool = kwargs.get("list", False)
        do_install: bool = kwargs.get("install", False)
        no_install: bool = kwargs.get("no_install", False)
        do_start: bool = kwargs.get("start", False)
        no_start: bool = kwargs.get("no_start", False)
        yes: bool = kwargs.get("yes", False)

        # ── List mode ──────────────────────────────────────────────────────────
        if list_mode:
            return self._handle_list(search)

        # ── Prerequisite check ─────────────────────────────────────────────────
        # Check before any clone attempt so users get a clear error with install
        # hints rather than a confusing git/make error mid-setup.
        missing = lifecycle.check_prereqs()
        if missing:
            console.print("[red bold]Missing required tools:[/red bold]")
            for _key, name, hint in missing:
                console.print(f"  [red]✗[/red] [bold]{name}[/bold]")
                console.print(f"    {hint}")
            console.print()
            console.print(
                "[dim]Install the tools above and re-run [bold]dg init[/bold].[/dim]"
            )
            return InitResult(
                status="error",
                message=f"Missing tools: {', '.join(k for k, _, _ in missing)}",
            )

        # ── Template selection ─────────────────────────────────────────────────
        if template_slug:
            detail = self._fetch_template(template_slug)
            if not detail:
                return InitResult(
                    status="error", message=f"Template not found: {template_slug}"
                )
        elif _agentic:
            # No picker in agent mode: the template is named on the command.
            return self._pick_needs_name()
        else:
            detail = self._interactive_pick(search)
            if not detail:
                return InitResult(status="cancelled", message="No template selected")

        # ── Check what the template service sent ───────────────────────────────
        # The repository and the template name come from the service; both
        # are checked before either is used in a path, a command or output.
        github_url = detail.links.get("github")
        if not github_url:
            return InitResult(status="error", message="Template has no GitHub URL")
        if not lifecycle.is_template_repo(github_url):
            error = msg.E_INIT_REPO_REFUSED.format(
                template=core_msg.display(detail.name)
            )
            print_error(escape(error))
            return InitResult(status="error", message=error)
        if not lifecycle.valid_name(detail.name):
            error = msg.E_INIT_NAME_REFUSED.format(
                template=core_msg.display(template_slug or detail.name)
            )
            print_error(escape(error))
            return InitResult(status="error", message=error)

        # ── Resolve target directory ───────────────────────────────────────────
        dest = Path(target_dir) if target_dir else Path(f"./{detail.name}")
        dest = dest.resolve()

        # ── Rerun contract: resume in a clone dg init made ─────────────────────
        # Only dg's own record (in its state directory) says a directory is a
        # clone dg made; nothing inside the directory is trusted for that,
        # and no git command runs in it until the record does.
        record: clones.CloneRecord | None = None
        if lifecycle.directory_in_use(dest):
            record = clones.load(os.path.realpath(dest))
            if record is None:
                return self._dir_error(msg.E_INIT_DIR_IN_USE, detail, dest)
            if record.template != detail.name:
                return self._dir_error(
                    msg.E_INIT_DIR_OTHER_TEMPLATE, detail, dest, record.template
                )
            # The same directory (not one made again at this path), owned by
            # this user, before git runs in it; then git checks HEAD.
            if (
                record.url != github_url
                or not lifecycle.same_directory(dest, record.dev, record.ino)
                or not lifecycle.clone_matches(dest, record.commit)
            ):
                return self._dir_error(msg.E_INIT_DIR_IN_USE, detail, dest)
        phases: list[str] = list(record.phases) if record else []
        commit = record.commit if record else None
        # The directory dg cloned into or resumes in, checked again right
        # before anything is written or run in it.
        identity = (record.dev, record.ino) if record else None
        person = _person_at_terminal()
        listed_steps = _commands(detail.config.install if detail.config else None)

        # An explicit --install approves setup, except under an AI agent,
        # where only --yes does. Setup commands the template service lists
        # are confirmed before anything is written.
        approved = yes or (do_install and not _hard_signal())
        if record is None and listed_steps and not (person or approved or no_install):
            return self._confirm_steps(detail, dest, listed_steps, cloned=False)

        if record is None:
            # ── Confirm ────────────────────────────────────────────────────────
            self._print_template_summary(detail)
            console.print(f"[dim]Destination:[/dim] {dest}")
            console.print()

            if person and not self.confirm("Clone this template?", default=True):
                return InitResult(status="cancelled", message="Clone cancelled")

            # ── Clone ──────────────────────────────────────────────────────────
            console.print()
            with console.status("[blue]Cloning repository...[/blue]"):
                try:
                    lifecycle.clone_template(github_url, dest)
                except RuntimeError as e:
                    return InitResult(status="error", message=str(e))
            phases = ["clone"]
            identity = lifecycle.directory_identity(dest)
            commit = lifecycle.head_commit(dest)
            self._save_progress(dest, detail.name, github_url, commit, phases, identity)

            console.print(f"[green]✓[/green] Cloned to [cyan]{dest}[/cyan]")
            console.print(
                "[dim]The cloned directory is a full git repository — "
                "you can add your own remote and push.[/dim]"
            )
        else:
            console.print(f"[dim]Resuming in[/dim] [cyan]{dest}[/cyan]")

        # ── Inject API key ─────────────────────────────────────────────────────
        if not _still_same(dest, identity):
            return self._dir_error(msg.E_INIT_DIR_CHANGED, detail, dest)
        try:
            api_key = auth_manager.get_api_key()
            if api_key:
                lifecycle.inject_env(dest, api_key)
        except (AuthenticationError, OSError) as exc:
            console.print(
                f"[yellow]Could not inject Deepgram credentials:[/yellow] {exc}"
            )

        # ── Setup (make check-prereqs && make init) ────────────────────────────
        installed = "setup" in phases
        has_makefile = (dest / "Makefile").exists()

        if not installed and not no_install:
            steps = _MAKE_STEPS if has_makefile else listed_steps
            if not steps:
                run_setup = False
            elif approved or (person and do_install):
                run_setup = True
            elif not person:
                return self._confirm_steps(detail, dest, steps, cloned=True)
            elif has_makefile:
                console.print()
                run_setup = self.confirm(
                    "Run setup? (make check-prereqs && make init)", default=True
                )
            else:
                run_setup = False

            if run_setup:
                if not _still_same(dest, identity):
                    return self._dir_error(msg.E_INIT_DIR_CHANGED, detail, dest)
                console.print()
                if has_makefile:
                    installed = lifecycle.run_make_init(dest)
                elif detail.config and detail.config.install:
                    # Fallback: TOML lifecycle for repos without a Makefile
                    installed = lifecycle.run_lifecycle_step(
                        detail.config.install, dest
                    )
                if installed:
                    phases.append("setup")
                    self._save_progress(
                        dest, detail.name, github_url, commit, phases, identity
                    )

        # ── Start step (optional) ──────────────────────────────────────────────
        # Never started unasked: --start, or a person's answer.
        started = False
        if installed and detail.config and detail.config.start and not no_start:
            if do_start:
                run_start = True
            elif person:
                console.print()
                run_start = self.confirm("Run start step?", default=False)
            else:
                return self._confirm_start(detail, dest)

            if run_start:
                if not _still_same(dest, identity):
                    return self._dir_error(msg.E_INIT_DIR_CHANGED, detail, dest)
                console.print()
                started = lifecycle.run_lifecycle_step(detail.config.start, dest)

        # ── Summary ────────────────────────────────────────────────────────────
        console.print()
        console.print(
            f"[green bold]Done![/green bold] "
            f"[cyan]{detail.title}[/cyan] is ready at [cyan]{dest}[/cyan]"
        )
        console.print()

        if not installed and has_makefile:
            console.print(f"[dim]To set up:  cd {dest.name} && make init[/dim]")
        if not started and detail.config and detail.config.start:
            console.print(f"[dim]To start:   cd {dest.name} && make start[/dim]")

        return InitResult(
            status="success",
            message=f"Cloned {detail.name}",
            template=detail.name,
            directory=str(dest),
            installed=installed,
            started=started,
        )

    # ── Confirmations and the rerun contract ──────────────────────────────────

    def _save_progress(
        self,
        dest: Path,
        template: str,
        url: str,
        commit: str | None,
        phases: list[str],
        identity: tuple[int, int] | None,
    ) -> None:
        """Record the clone and what is done in dg's state, so a rerun
        resumes. Without a commit there is nothing a rerun could check."""
        if commit is None or identity is None:
            return
        record = clones.CloneRecord(
            os.path.realpath(dest), template, url, commit, phases, *identity
        )
        try:
            clones.save(record)
        except OSError as exc:
            text = msg.W_INIT_PROGRESS_UNSAVED.format(
                exc=exc, prog=_prog_text(), dest=core_msg.display_path(dest)
            )
            console.print(f"[yellow]{escape(text)}[/yellow]")

    def _confirm_steps(
        self, detail: TemplateDetail, dest: Path, steps: list[str], *, cloned: bool
    ) -> InitCodedError:
        """Setup commands no one here can approve: confirmation_required."""
        next_step = _rerun(detail.name, dest, "--no-install", "--no-start")
        shown = core_msg.display_path(dest)
        if not _hard_signal():
            hint = (
                msg.H_CONFIRM_INIT_STEPS
                if cloned
                else msg.H_CONFIRM_INIT_STEPS_BEFORE_CLONE
            )
        elif cloned:
            hint = (
                msg.A_CONFIRM_INIT_STEPS if next_step else msg.A_CONFIRM_INIT_STEPS_NULL
            )
        else:
            hint = (
                msg.A_CONFIRM_INIT_STEPS_BEFORE_CLONE
                if next_step
                else msg.A_CONFIRM_INIT_STEPS_BEFORE_CLONE_NULL
            )
        # Before the clone, the repository's Makefile is unknown, and if it
        # has one its targets run instead of the listed commands; say so.
        template = core_msg.display(detail.name)
        listed = "; ".join(core_msg.display(step) for step in steps)
        if cloned:
            error = msg.E_CONFIRM_INIT_STEPS.format(template=template, steps=listed)
        else:
            error = msg.E_CONFIRM_INIT_STEPS_BEFORE_CLONE.format(
                template=template, steps=listed
            )
        return _coded(
            error,
            msg.CODE_CONFIRMATION_REQUIRED,
            hint.format(dest=shown, prog=_prog_text()),
            next_step,
            {"flag": msg.YES_FLAG},
        )

    def _confirm_start(self, detail: TemplateDetail, dest: Path) -> InitCodedError:
        """A start step no one here asked for: confirmation_required."""
        next_step = _rerun(detail.name, dest, "--no-start")
        if not _hard_signal():
            hint = msg.H_CONFIRM_INIT_START
        else:
            hint = (
                msg.A_CONFIRM_INIT_START if next_step else msg.A_CONFIRM_INIT_START_NULL
            )
        error = msg.E_CONFIRM_INIT_START.format(template=core_msg.display(detail.name))
        return _coded(
            error,
            msg.CODE_CONFIRMATION_REQUIRED,
            hint,
            next_step,
            {"flag": "--start"},
        )

    def _dir_error(
        self,
        message: str,
        detail: TemplateDetail,
        dest: Path,
        other: str | None = None,
    ) -> InitCodedError:
        """A directory dg init can't clone into or resume in."""
        from deepctl_core.transport import prog

        program = prog()
        error = message.format(
            dest=core_msg.display_path(dest),
            template=core_msg.display(detail.name),
            other=core_msg.display(other or ""),
            prog=_prog_text(),
        )
        return _coded(
            error,
            msg.CODE_INVALID_ARGUMENT,
            msg.H_INIT_DIR,
            f"{program} init --help" if program else None,
        )

    def _pick_needs_name(self) -> InitCodedError:
        """No template named where the picker can't be shown."""
        from deepctl_core.transport import prog

        program = prog()
        return _coded(
            msg.E_CONFIRM_INIT_PICK.format(prog=_prog_text()),
            msg.CODE_INVALID_ARGUMENT,
            msg.H_CONFIRM_INIT_PICK.format(prog=_prog_text()),
            f"{program} init --list" if program else None,
        )

    def output_result(self, result: Any, config: Config) -> None:
        """Only emit structured output when an explicit --output format is set."""
        from deepctl_core.output import get_output_format

        if get_output_format() in ("default",):
            return
        super().output_result(result, config)

    # ── List ──────────────────────────────────────────────────────────────────

    def _handle_list(self, search: str | None) -> list[dict[str, Any]]:
        """List templates and return structured data."""
        with console.status("[blue]Fetching templates...[/blue]"):
            response = templates_api.list_templates(search=search)

        templates = response.items
        if not templates:
            console.print("[yellow]No templates found[/yellow]")
            return []

        self._print_templates_table(templates)
        console.print(f"\n[dim]{len(templates)} template(s)[/dim]")
        return [t.model_dump() for t in templates]

    # ── Interactive picker ────────────────────────────────────────────────────

    def _interactive_pick(self, search: str | None) -> TemplateDetail | None:
        """Interactive search-and-select picker.

        The user can type a number to select, or type a search term to filter
        and re-display the list. Loops until a selection is made or cancelled.
        """
        with console.status("[blue]Fetching templates...[/blue]"):
            response = templates_api.list_templates()

        all_templates = response.items
        if not all_templates:
            console.print("[yellow]No templates found[/yellow]")
            return None

        # Apply initial search filter if provided
        filtered = (
            templates_api.filter_templates(all_templates, search)
            if search
            else all_templates
        )

        selected: TemplateInfo | None = None

        while selected is None:
            self._print_templates_table(filtered)
            console.print()

            if len(filtered) == 0:
                console.print("[yellow]No templates match your search.[/yellow]")
                filtered = all_templates
                continue

            console.print(
                "[dim]Type a [bold]number[/bold] to select, "
                "or a [bold]search term[/bold] to filter. "
                "Ctrl+C to cancel.[/dim]"
            )
            try:
                raw = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print()
                return None

            if not raw:
                if len(filtered) == 1:
                    selected = filtered[0]
                continue

            # Try numeric selection
            try:
                idx = int(raw)
                if 1 <= idx <= len(filtered):
                    selected = filtered[idx - 1]
                else:
                    console.print(
                        f"[yellow]Enter a number between 1 and {len(filtered)}.[/yellow]"
                    )
            except ValueError:
                # Treat as search term
                new_filtered = templates_api.filter_templates(all_templates, raw)
                if not new_filtered:
                    console.print(
                        f"[yellow]No templates match '{raw}'. Showing all.[/yellow]"
                    )
                    filtered = all_templates
                else:
                    filtered = new_filtered
                console.print()

        console.print()
        with console.status(f"[blue]Fetching {selected.name}...[/blue]"):
            return templates_api.get_template(selected.name)

    def _fetch_template(self, slug: str) -> TemplateDetail | None:
        """Fetch template detail by slug."""
        try:
            with console.status(f"[blue]Fetching {slug}...[/blue]"):
                return templates_api.get_template(slug)
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            return None

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _print_templates_table(self, templates: list[TemplateInfo]) -> None:
        """Print a numbered table of templates."""
        table = Table(show_header=True, header_style="bold blue")
        table.add_column("#", style="dim", width=4, justify="right")
        table.add_column("Name")
        table.add_column("Language")
        table.add_column("Framework")
        table.add_column("Use case")
        table.add_column("Description", no_wrap=False, max_width=50)

        for i, t in enumerate(templates, 1):
            table.add_row(
                str(i),
                t.name,
                t.language,
                t.framework or "—",
                t.category or "—",
                t.description,
            )

        console.print(table)

    def _print_template_summary(self, detail: TemplateDetail) -> None:
        """Print a one-line summary of the selected template."""
        console.print()
        console.print(f"[bold]{detail.title}[/bold]  [dim]{detail.name}[/dim]")
        console.print(f"[dim]{detail.description}[/dim]")
        lang_line = f"Language: [cyan]{detail.language}[/cyan]"
        if detail.framework:
            lang_line += f"  Framework: [cyan]{detail.framework}[/cyan]"
        if detail.stats.stars:
            lang_line += f"  ★ {detail.stats.stars}"
        console.print(lang_line)
