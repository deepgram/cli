"""Projects command for managing Deepgram projects."""

from typing import Any, List, Dict, Optional

import click
from rich.console import Console

from .base import BaseCommand
from ..core.config import Config
from ..core.auth import AuthManager
from ..core.client import DeepgramClient
from ..models import ProjectsResult, ProjectInfo, BaseResult

console = Console()


class ProjectsCommand(BaseCommand):
    """Command for managing Deepgram projects."""

    name = "projects"
    help = "Manage Deepgram projects"
    short_help = "Manage projects"

    # Projects require authentication
    requires_auth = True
    requires_project = False  # Project ID is optional for listing
    ci_friendly = True

    def get_arguments(self) -> List[Dict[str, Any]]:
        """Get command arguments and options."""
        return [
            {
                "names": ["--list", "-l"],
                "help": "List all projects",
                "is_flag": True,
                "is_option": True
            },
            {
                "names": ["--create", "-c"],
                "help": "Create a new project",
                "type": str,
                "is_option": True
            },
            {
                "names": ["--show", "-s"],
                "help": "Show project details",
                "type": str,
                "is_option": True
            },
            {
                "names": ["--company"],
                "help": "Company name for new project",
                "type": str,
                "is_option": True
            },
            {
                "names": ["--current"],
                "help": "Show current project",
                "is_flag": True,
                "is_option": True
            },
            {
                "names": ["--set-default"],
                "help": "Set project as default",
                "type": str,
                "is_option": True
            }
        ]

    def handle(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs
    ) -> BaseResult:
        """Handle projects command."""
        list_projects = kwargs.get("list", False)
        create_project = kwargs.get("create")
        show_project = kwargs.get("show")
        company = kwargs.get("company")
        show_current = kwargs.get("current", False)
        set_default = kwargs.get("set_default")

        try:
            if list_projects:
                return self._list_projects(client)
            elif create_project:
                return self._create_project(client, create_project, company)
            elif show_project:
                return self._show_project(client, show_project)
            elif show_current:
                return self._show_current_project(config, auth_manager, client)
            elif set_default:
                return self._set_default_project(config, auth_manager, set_default)
            else:
                # Default behavior - list projects
                return self._list_projects(client)

        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            return BaseResult(status="error", message=str(e))

    def _list_projects(self, client: DeepgramClient) -> ProjectsResult | BaseResult:
        """List all projects."""
        console.print("[blue]Fetching projects...[/blue]")

        try:
            result = client.get_projects()

            projects_raw = result.get("projects", [])

            if not projects_raw:
                console.print("[yellow]No projects found[/yellow]")
                return ProjectsResult(status="info", message="No projects found", projects=[], count=0)

            project_models: list[ProjectInfo] = []
            console.print(
                f"[green]Found {len(projects_raw)} project(s):[/green]")

            for proj in projects_raw:
                info = ProjectInfo(
                    project_id=proj.get("project_id", "N/A"),
                    name=proj.get("name", "Unnamed"),
                    company=proj.get("company"),
                )
                project_models.append(info)

                console.print(f"  • {info.name}")
                console.print(f"    ID: {info.project_id}")
                console.print(f"    Company: {info.company or 'N/A'}")
                console.print()

            return ProjectsResult(status="success", projects=project_models, count=len(project_models))

        except Exception as e:
            console.print(f"[red]Failed to list projects:[/red] {e}")
            return BaseResult(status="error", message=str(e))

    def _create_project(self, client: DeepgramClient, name: str, company: Optional[str]) -> ProjectsResult | BaseResult:
        """Create a new project."""
        console.print(f"[blue]Creating project:[/blue] {name}")

        if company:
            console.print(f"[dim]Company:[/dim] {company}")

        try:
            result = client.create_project(name, company)

            if "project_id" in result:
                project_id = result["project_id"]
                console.print(f"[green]✓[/green] Project created successfully")
                console.print(f"[dim]Project ID:[/dim] {project_id}")

                proj = ProjectInfo(project_id=project_id,
                                   name=name, company=company)
                return ProjectsResult(status="success", message="Project created successfully", projects=[proj], count=1)
            else:
                console.print(
                    "[yellow]Project creation response missing project_id[/yellow]")
                return ProjectsResult(status="warning", message="Created but missing project_id", projects=[], count=0)

        except Exception as e:
            console.print(f"[red]Failed to create project:[/red] {e}")
            return BaseResult(status="error", message=str(e))

    def _show_project(self, client: DeepgramClient, project_id: str) -> ProjectsResult | BaseResult:
        """Show details for a specific project."""
        console.print(f"[blue]Fetching project details:[/blue] {project_id}")

        try:
            result = client.get_project(project_id)

            if "name" in result:
                name = result.get("name", "N/A")
                company = result.get("company", "N/A")

                console.print(f"[green]Project Details:[/green]")
                console.print(f"  Name: {name}")
                console.print(f"  ID: {project_id}")
                console.print(f"  Company: {company}")

                proj = ProjectInfo(project_id=project_id,
                                   name=name, company=company)
                return ProjectsResult(status="success", projects=[proj], count=1)
            else:
                console.print("[yellow]Project details incomplete[/yellow]")
                proj = ProjectInfo(project_id=project_id,
                                   name=name, company=company)
                return ProjectsResult(status="warning", message="Incomplete project data", projects=[proj], count=1)

        except Exception as e:
            console.print(f"[red]Failed to get project details:[/red] {e}")
            return BaseResult(status="error", message=str(e))

    def _show_current_project(self, config: Config, auth_manager: AuthManager, client: DeepgramClient) -> ProjectsResult | BaseResult:
        """Show current project details."""
        project_id = auth_manager.get_project_id()

        if not project_id:
            console.print("[yellow]No current project set[/yellow]")
            console.print(
                "Set a project ID with: deepctl login --project-id <project_id>")
            console.print("Or use environment variable: DEEPGRAM_PROJECT_ID")
            return BaseResult(status="info", message="No current project set")

        console.print(f"[blue]Current project ID:[/blue] {project_id}")
        return self._show_project(client, project_id)

    def _set_default_project(self, config: Config, auth_manager: AuthManager, project_id: str) -> BaseResult:
        """Set default project ID."""
        console.print(f"[blue]Setting default project:[/blue] {project_id}")

        try:
            # Update current profile
            profile_name = config.profile or "default"
            current_profile = config.get_profile(profile_name)

            # Test if project exists by trying to get it
            client = DeepgramClient(config, auth_manager)
            try:
                client.get_project(project_id)
                console.print("[green]✓[/green] Project ID validated")
            except Exception as e:
                console.print(
                    f"[yellow]Warning:[/yellow] Could not validate project: {e}")
                if not self.confirm("Continue anyway?", default=False):
                    return BaseResult(status="cancelled", message="Cancelled by user")

            # Update profile
            config.create_profile(
                profile_name,
                api_key=current_profile.api_key,
                project_id=project_id,
                base_url=current_profile.base_url
            )

            console.print(
                f"[green]✓[/green] Default project set to: {project_id}")

            return BaseResult(status="success", message="Default project updated",)

        except Exception as e:
            console.print(f"[red]Failed to set default project:[/red] {e}")
            return BaseResult(status="error", message=str(e))
