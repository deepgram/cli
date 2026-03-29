"""Init command for deepctl — scaffold Deepgram starter apps."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from deepctl_core import (
    AuthManager,
    BaseCommand,
    Config,
    DeepgramClient,
)
from deepctl_core.output import _agentic
from rich.console import Console
from rich.table import Table

from . import lifecycle, templates_api
from .models import InitResult, TemplateDetail, TemplateInfo

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
        "dg init --list",
        "dg init --list --search flask",
    ]
    agent_help = (
        "Scaffold a Deepgram starter app from the templates gallery. "
        "Pass the template name/slug directly (e.g. 'node-transcription'). "
        "Use --list to enumerate available templates. "
        "After cloning, runs make check-prereqs then make init automatically. "
        "Use --no-install to skip setup. "
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
                "help": "Run setup (make check-prereqs && make init) without prompting",
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
            # In agent mode, require an explicit template name
            return InitResult(
                status="error",
                message=(
                    "In agent mode, pass the template slug directly, e.g.: "
                    "dg init node-transcription\n"
                    "Use dg init --list to see available templates."
                ),
            )
        else:
            detail = self._interactive_pick(search)
            if not detail:
                return InitResult(status="cancelled", message="No template selected")

        # ── Resolve target directory ───────────────────────────────────────────
        dest = Path(target_dir) if target_dir else Path(f"./{detail.name}")
        dest = dest.resolve()

        # ── Confirm ────────────────────────────────────────────────────────────
        self._print_template_summary(detail)
        console.print(f"[dim]Destination:[/dim] {dest}")
        console.print()

        if not _agentic and not self.confirm("Clone this template?", default=True):
            return InitResult(status="cancelled", message="Clone cancelled")

        # ── Clone ──────────────────────────────────────────────────────────────
        github_url = detail.links.get("github")
        if not github_url:
            return InitResult(status="error", message="Template has no GitHub URL")

        console.print()
        with console.status("[blue]Cloning repository...[/blue]"):
            try:
                lifecycle.clone_template(github_url, dest)
            except RuntimeError as e:
                return InitResult(status="error", message=str(e))

        console.print(f"[green]✓[/green] Cloned to [cyan]{dest}[/cyan]")
        console.print(
            "[dim]The cloned directory is a full git repository — "
            "you can add your own remote and push.[/dim]"
        )

        # ── Inject API key ─────────────────────────────────────────────────────
        try:
            api_key = auth_manager.get_api_key()
            if api_key:
                lifecycle.inject_env(dest, api_key)
        except Exception:
            pass  # Auth is optional

        # ── Setup (make check-prereqs && make init) ────────────────────────────
        installed = False
        has_makefile = (dest / "Makefile").exists()

        if not no_install:
            if _agentic or do_install:
                run_setup = True
            elif has_makefile:
                console.print()
                run_setup = self.confirm(
                    "Run setup? (make check-prereqs && make init)", default=True
                )
            else:
                run_setup = False

            if run_setup:
                console.print()
                if has_makefile:
                    installed = lifecycle.run_make_init(dest)
                elif detail.config and detail.config.install:
                    # Fallback: TOML lifecycle for repos without a Makefile
                    installed = lifecycle.run_lifecycle_step(
                        detail.config.install, dest
                    )

        # ── Start step (optional) ──────────────────────────────────────────────
        started = False
        if installed and detail.config and detail.config.start and not no_start:
            if _agentic or do_start:
                run_start = True
            else:
                console.print()
                run_start = self.confirm("Run start step?", default=False)

            if run_start:
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
