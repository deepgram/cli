"""Projects command for deepctl."""

from typing import Any

from deepctl_core import (
    AuthManager,
    BaseCommand,
    BaseResult,
    Config,
    DeepgramClient,
)
from rich.console import Console

from .models import ProjectInfo, ProjectsResult

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

    def get_arguments(self) -> list[dict[str, Any]]:
        """Get command arguments and options."""
        return [
            {
                "names": ["--list", "-l"],
                "help": "List all projects",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--create", "-c"],
                "help": "Create a new project",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--show", "-s"],
                "help": "Show project details",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--company"],
                "help": "Company name for new project",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--current"],
                "help": "Show current project",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--set-default"],
                "help": "Set project as default",
                "type": str,
                "is_option": True,
            },
        ]

    def handle(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
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
                return self._set_default_project(
                    config, auth_manager, set_default
                )
            else:
                # Default behavior - list projects
                return self._list_projects(client)

        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            return BaseResult(status="error", message=str(e))

    def _list_projects(
        self, client: DeepgramClient
    ) -> ProjectsResult | BaseResult:
        """List all projects."""
        console.print("[blue]Fetching projects...[/blue]")

        try:
            result = client.get_projects()

            # Handle ProjectsResponse object
            projects_raw = []
            if hasattr(result, "projects"):
                projects_raw = result.projects
            elif hasattr(result, "to_dict"):
                result_dict = result.to_dict()
                projects_raw = result_dict.get("projects", [])
            elif hasattr(result, "dict"):
                result_dict = result.dict()
                projects_raw = result_dict.get("projects", [])
            elif isinstance(result, dict):
                projects_raw = result.get("projects", [])

            if not projects_raw:
                console.print("[yellow]No projects found[/yellow]")
                return ProjectsResult(
                    status="info",
                    message="No projects found",
                    projects=[],
                    count=0,
                )

            project_models: list[ProjectInfo] = []
            console.print(
                f"[green]Found {len(projects_raw)} project(s):[/green]"
            )

            for proj in projects_raw:
                # Handle project objects that might not be dicts
                project_data = proj
                if hasattr(proj, "__dict__"):
                    project_data = proj.__dict__
                elif hasattr(proj, "to_dict"):
                    project_data = proj.to_dict()
                elif hasattr(proj, "dict"):
                    project_data = proj.dict()

                info = ProjectInfo(
                    project_id=project_data.get("project_id", "N/A"),
                    name=project_data.get("name", "Unnamed"),
                    company=project_data.get("company"),
                )
                project_models.append(info)

                console.print(f"  • {info.name}")
                console.print(f"    ID: {info.project_id}")
                console.print(f"    Company: {info.company or 'N/A'}")
                console.print()

            return ProjectsResult(
                status="success",
                projects=project_models,
                count=len(project_models),
            )

        except Exception as e:
            console.print(f"[red]Failed to list projects:[/red] {e}")
            return BaseResult(status="error", message=str(e))

    def _create_project(
        self, client: DeepgramClient, name: str, company: str | None
    ) -> ProjectsResult | BaseResult:
        """Create a new project."""
        console.print(f"[blue]Creating project:[/blue] {name}")

        if company:
            console.print(f"[dim]Company:[/dim] {company}")

        try:
            result = client.create_project(name, company)

            # Handle response object
            result_dict = result
            if hasattr(result, "to_dict"):
                result_dict = result.to_dict()
            elif hasattr(result, "dict"):
                result_dict = result.dict()
            elif hasattr(result, "__dict__"):
                result_dict = result.__dict__

            if isinstance(result_dict, dict) and "project_id" in result_dict:
                project_id = result_dict["project_id"]
                console.print("[green]✓[/green] Project created successfully")
                console.print(f"[dim]Project ID:[/dim] {project_id}")

                proj = ProjectInfo(
                    project_id=project_id, name=name, company=company
                )
                return ProjectsResult(
                    status="success",
                    message="Project created successfully",
                    projects=[proj],
                    count=1,
                )
            else:
                console.print(
                    "[yellow]Project creation response missing "
                    "project_id[/yellow]"
                )
                return ProjectsResult(
                    status="warning",
                    message="Created but missing project_id",
                    projects=[],
                    count=0,
                )

        except Exception as e:
            console.print(f"[red]Failed to create project:[/red] {e}")
            return BaseResult(status="error", message=str(e))

    def _show_project(
        self, client: DeepgramClient, project_id: str
    ) -> ProjectsResult | BaseResult:
        """Show details for a specific project."""
        console.print(f"[blue]Fetching project details:[/blue] {project_id}")

        try:
            result = client.get_project(project_id)

            # Handle response object
            result_dict = result
            if hasattr(result, "to_dict"):
                result_dict = result.to_dict()
            elif hasattr(result, "dict"):
                result_dict = result.dict()
            elif hasattr(result, "__dict__"):
                result_dict = result.__dict__

            if isinstance(result_dict, dict) and "name" in result_dict:
                name = result_dict.get("name", "N/A")
                company = result_dict.get("company", "N/A")

                console.print("[green]Project Details:[/green]")
                console.print(f"  Name: {name}")
                console.print(f"  ID: {project_id}")
                console.print(f"  Company: {company}")

                proj = ProjectInfo(
                    project_id=project_id, name=name, company=company
                )
                return ProjectsResult(
                    status="success", projects=[proj], count=1
                )
            else:
                console.print("[yellow]Project details incomplete[/yellow]")
                # Still try to create a project info with what we have
                name = (
                    result_dict.get("name", "Unknown")
                    if isinstance(result_dict, dict)
                    else "Unknown"
                )
                company = (
                    result_dict.get("company")
                    if isinstance(result_dict, dict)
                    else None
                )
                proj = ProjectInfo(
                    project_id=project_id, name=name, company=company
                )
                return ProjectsResult(
                    status="warning",
                    message="Incomplete project data",
                    projects=[proj],
                    count=1,
                )

        except Exception as e:
            console.print(f"[red]Failed to get project details:[/red] {e}")
            return BaseResult(status="error", message=str(e))

    def _show_current_project(
        self, config: Config, auth_manager: AuthManager, client: DeepgramClient
    ) -> ProjectsResult | BaseResult:
        """Show current project details."""
        project_id = auth_manager.get_project_id()

        if not project_id:
            console.print("[yellow]No current project set[/yellow]")
            console.print(
                "Set a project ID with: "
                "deepctl login --project-id <project_id>"
            )
            console.print("Or use environment variable: DEEPGRAM_PROJECT_ID")
            return BaseResult(status="info", message="No current project set")

        console.print(f"[blue]Current project ID:[/blue] {project_id}")
        return self._show_project(client, project_id)

    def _set_default_project(
        self, config: Config, auth_manager: AuthManager, project_id: str
    ) -> BaseResult:
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
                    f"[yellow]Warning:[/yellow] Could not validate "
                    f"project: {e}"
                )
                if not self.confirm("Continue anyway?", default=False):
                    return BaseResult(
                        status="cancelled", message="Cancelled by user"
                    )

            # Update profile
            config.create_profile(
                profile_name,
                api_key=current_profile.api_key,
                project_id=project_id,
                base_url=current_profile.base_url,
            )

            console.print(
                f"[green]✓[/green] Default project set to: {project_id}"
            )

            return BaseResult(
                status="success",
                message="Default project updated",
            )

        except Exception as e:
            console.print(f"[red]Failed to set default project:[/red] {e}")
            return BaseResult(status="error", message=str(e))
