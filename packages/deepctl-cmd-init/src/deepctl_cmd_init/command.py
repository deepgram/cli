"""Init command for deepctl — scaffold Deepgram starter apps."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click
from deepctl_core import (
    AuthManager,
    BaseCommand,
    Config,
    DeepgramClient,
)
from rich.console import Console
from rich.table import Table

from . import lifecycle, templates_api
from .models import InitResult, TemplateDetail, TemplateInfo

console = Console()


class InitCommand(BaseCommand):
    """Scaffold a Deepgram starter app from the templates gallery."""

    name = "init"
    help = (
        "Browse, clone, and set up Deepgram starter apps. "
        "Fetches templates from the Deepgram templates gallery and "
        "optionally runs install/start lifecycle steps."
    )
    short_help = "Scaffold a Deepgram starter app"

    requires_auth = False
    requires_project = False
    ci_friendly = True

    examples = [
        "dg init",
        "dg init --search python",
        "dg init node-transcription",
        "dg init node-transcription --dir ./my-app",
        "dg init node-transcription --install --start",
        "dg init --list",
        "dg init --list --search flask",
    ]
    agent_help = (
        "Scaffold a Deepgram starter app from the templates gallery. "
        "Can list available templates, search/filter them, or clone a "
        "specific template by name. After cloning, optionally runs "
        "install and start lifecycle steps defined in deepgram.toml. "
        "If authenticated, injects DEEPGRAM_API_KEY into .env automatically."
    )

    def get_arguments(self) -> list[dict[str, Any]]:
        """Get command arguments and options."""
        return [
            {
                "name": "template",
                "help": "Template name/slug to clone directly",
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
                "help": "Filter templates by search term",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--list", "-l"],
                "help": "List matching templates and exit",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--install"],
                "help": "Run install step without prompting",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--start"],
                "help": "Run start step without prompting",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--no-install"],
                "help": "Skip install step without prompting",
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
        do_start: bool = kwargs.get("start", False)
        no_install: bool = kwargs.get("no_install", False)
        no_start: bool = kwargs.get("no_start", False)

        # --- List mode ---
        if list_mode:
            return self._handle_list(search)

        # --- Direct template selection ---
        if template_slug:
            detail = self._fetch_template(template_slug)
            if not detail:
                return InitResult(
                    status="error", message=f"Template not found: {template_slug}"
                )
        else:
            # --- Interactive picker ---
            detail = self._interactive_pick(search)
            if not detail:
                return InitResult(status="cancelled", message="No template selected")

        # Resolve target directory
        dest = Path(target_dir) if target_dir else Path(f"./{detail.name}")
        dest = dest.resolve()

        # Confirm selection
        self._print_template_summary(detail)
        console.print(f"[dim]Directory:[/dim] {dest}")
        console.print()

        if not self.confirm("Clone this template?", default=True):
            return InitResult(status="cancelled", message="Clone cancelled")

        # Clone
        github_url = detail.links.get("github")
        if not github_url:
            return InitResult(status="error", message="Template has no GitHub URL")

        console.print()
        with console.status("[blue]Cloning template...[/blue]"):
            lifecycle.clone_template(github_url, dest)
        console.print(f"[green]Cloned to {dest}[/green]")

        # Inject API key if authenticated
        try:
            api_key = auth_manager.get_api_key()
            if api_key:
                lifecycle.inject_env(dest, api_key)
        except Exception:
            pass  # Auth is optional

        installed = False
        started = False

        # Run lifecycle steps if config exists
        if detail.config:
            # Install step
            if detail.config.install:
                should_install = do_install or (
                    not no_install and self.confirm("Run install step?", default=True)
                )
                if should_install:
                    console.print()
                    installed = lifecycle.run_lifecycle_step(
                        detail.config.install, dest
                    )

            # Start step
            if detail.config.start:
                should_start = do_start or (
                    not no_start and self.confirm("Run start step?", default=True)
                )
                if should_start:
                    console.print()
                    started = lifecycle.run_lifecycle_step(detail.config.start, dest)

        # Summary
        console.print()
        console.print(
            f"[green bold]Done![/green bold] Your app is ready at [cyan]{dest}[/cyan]"
        )
        if not installed and detail.config and detail.config.install:
            console.print(
                f"[dim]To install: cd {dest.name} && run the install command[/dim]"
            )
        if not started and detail.config and detail.config.start:
            console.print(
                f"[dim]To start: cd {dest.name} && run the start command[/dim]"
            )

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

        output_format = get_output_format()

        # Default format: the Rich table / console messages are already printed,
        # so skip the automatic JSON dump.
        if output_format in ("default",):
            return

        # Explicit --output json/yaml/table/csv: delegate to base
        super().output_result(result, config)

    def _handle_list(self, search: str | None) -> list[dict[str, Any]]:
        """List templates and return structured data."""
        with console.status("[blue]Fetching templates...[/blue]"):
            response = templates_api.list_templates(search=search)

        templates = response.items
        if not templates:
            console.print("[yellow]No templates found[/yellow]")
            return []

        self._print_templates_table(templates)
        console.print(f"\n[dim]{len(templates)} template(s) found[/dim]")

        return [t.model_dump() for t in templates]

    def _interactive_pick(self, search: str | None) -> TemplateDetail | None:
        """Show interactive picker and return selected template detail."""
        with console.status("[blue]Fetching templates...[/blue]"):
            response = templates_api.list_templates(search=search)

        templates = response.items
        if not templates:
            console.print("[yellow]No templates found[/yellow]")
            return None

        self._print_templates_table(templates)
        console.print()

        try:
            choice = click.prompt(
                "Select a template",
                type=click.IntRange(1, len(templates)),
            )
        except (click.Abort, EOFError):
            return None

        selected = templates[choice - 1]
        console.print()

        # Fetch full detail
        with console.status(f"[blue]Fetching {selected.name}...[/blue]"):
            return templates_api.get_template(selected.name)

    def _fetch_template(self, slug: str) -> TemplateDetail | None:
        """Fetch template detail by slug, handling errors."""
        try:
            with console.status(f"[blue]Fetching {slug}...[/blue]"):
                return templates_api.get_template(slug)
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            return None

    def _print_templates_table(self, templates: list[TemplateInfo]) -> None:
        """Print a numbered table of templates."""
        table = Table(show_header=True, header_style="bold blue")
        table.add_column("#", style="dim", width=4, justify="right")
        table.add_column("Name")
        table.add_column("Language")
        table.add_column("Framework")
        table.add_column("Category")

        for i, t in enumerate(templates, 1):
            table.add_row(
                str(i),
                t.name,
                t.language,
                t.framework or "—",
                t.category or "—",
            )

        console.print(table)

    def _print_template_summary(self, detail: TemplateDetail) -> None:
        """Print a summary of the selected template."""
        console.print()
        console.print(f"[bold]{detail.title}[/bold]")
        console.print(f"[dim]{detail.description}[/dim]")
        console.print(
            f"Language: [cyan]{detail.language}[/cyan]"
            + (
                f"  Framework: [cyan]{detail.framework}[/cyan]"
                if detail.framework
                else ""
            )
            + (f"  Stars: {detail.stats.stars}" if detail.stats.stars else "")
        )
