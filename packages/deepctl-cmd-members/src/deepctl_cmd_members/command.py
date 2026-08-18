"""Members management command for deepctl."""

from __future__ import annotations

from typing import Any

from deepctl_core import (
    AuthManager,
    BaseCommand,
    BaseResult,
    Config,
    DeepgramClient,
)
from deepctl_core.output import get_status_console
from rich.table import Table

from .models import InviteInfo, MemberInfo, MembersResult

# Human/status output — routed to stderr when -o json/yaml/csv owns stdout
console = get_status_console()


class MembersCommand(BaseCommand):
    """Command for managing project members."""

    name = "members"
    help = "Manage Deepgram project members"
    short_help = "Manage members"

    requires_auth = True
    requires_project = True
    ci_friendly = True

    examples = [
        "dg members",
        "dg members --list",
        "dg members --invite user@example.com --scope member",
        "dg members --remove MEMBER_ID",
        "dg members --invites",
        "dg members --revoke-invite user@example.com",
    ]
    agent_help = (
        "Manage project members and invitations. "
        "List members, invite new ones, remove existing, and manage invites. "
        "Requires authentication and a project ID."
    )

    def get_arguments(self) -> list[dict[str, Any]]:
        return [
            {
                "names": ["--list", "-l"],
                "help": "List all project members",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--invite", "-i"],
                "help": "Invite a member by email",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--scope"],
                "help": "Scope for invite (e.g., member, admin)",
                "type": str,
                "is_option": True,
                "default": "member",
            },
            {
                "names": ["--remove", "-r"],
                "help": "Remove a member by ID",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--invites"],
                "help": "List pending invites",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--revoke-invite"],
                "help": "Revoke an invite by email",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--project-id", "-p"],
                "help": "Project ID (uses configured project if not provided)",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--yes", "-y"],
                "help": "Skip confirmation prompts",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--dry-run"],
                "help": "Show what would happen without making any changes",
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
    ) -> BaseResult:
        invite_email = kwargs.get("invite")
        remove_id = kwargs.get("remove")
        show_invites = kwargs.get("invites", False)
        revoke_email = kwargs.get("revoke_invite")
        project_id = kwargs.get("project_id")
        yes = kwargs.get("yes", False)
        dry_run = kwargs.get("dry_run", False)

        try:
            if invite_email:
                return self._invite_member(
                    client,
                    invite_email,
                    kwargs.get("scope", "member"),
                    project_id,
                    dry_run=dry_run,
                )
            elif remove_id:
                return self._remove_member(
                    client, remove_id, project_id, yes, dry_run=dry_run
                )
            elif show_invites:
                return self._list_invites(client, project_id)
            elif revoke_email:
                return self._revoke_invite(
                    client, revoke_email, project_id, yes, dry_run=dry_run
                )
            else:
                return self._list_members(client, project_id)

        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            return BaseResult(status="error", message=str(e))

    def _list_members(
        self, client: DeepgramClient, project_id: str | None
    ) -> BaseResult:
        console.print("[blue]Fetching project members...[/blue]")
        result = client.list_members(project_id=project_id)

        members_raw = result.get("members", [])
        if not members_raw:
            console.print("[yellow]No members found[/yellow]")
            return MembersResult(status="info", message="No members found")

        member_models: list[MemberInfo] = []
        table = Table(
            title="Project Members", show_header=True, header_style="bold blue"
        )
        table.add_column("Name", style="green")
        table.add_column("Email")
        table.add_column("Scopes")
        table.add_column("Member ID", style="dim")

        for m in members_raw:
            m_data = (
                m
                if isinstance(m, dict)
                else (m.__dict__ if hasattr(m, "__dict__") else {})
            )
            info = MemberInfo(
                member_id=m_data.get("member_id", ""),
                email=m_data.get("email", ""),
                first_name=m_data.get("first_name", ""),
                last_name=m_data.get("last_name", ""),
                scopes=m_data.get("scopes", []),
            )
            member_models.append(info)

            name = f"{info.first_name} {info.last_name}".strip() or "(no name)"
            table.add_row(
                name,
                info.email,
                ", ".join(info.scopes) if info.scopes else "-",
                info.member_id,
            )

        console.print(table)
        console.print(f"\n[dim]{len(member_models)} member(s)[/dim]")

        return MembersResult(
            status="success", members=member_models, count=len(member_models)
        )

    def _invite_member(
        self,
        client: DeepgramClient,
        email: str,
        scope: str,
        project_id: str | None,
        dry_run: bool = False,
    ) -> BaseResult:
        if dry_run:
            console.print("[yellow]Dry run — no changes made[/yellow]")
            console.print(f"  Would invite: {email}  (scope: {scope})")
            return BaseResult(
                status="dry_run", message=f"Dry run: would invite {email}"
            )

        console.print(f"[blue]Inviting {email} with scope '{scope}'...[/blue]")
        client.create_invite(email=email, scope=scope, project_id=project_id)
        console.print(f"[green]Invitation sent to {email}[/green]")
        return BaseResult(status="success", message=f"Invited {email}")

    def _remove_member(
        self,
        client: DeepgramClient,
        member_id: str,
        project_id: str | None,
        yes: bool = False,
        dry_run: bool = False,
    ) -> BaseResult:
        if dry_run:
            console.print("[yellow]Dry run — no changes made[/yellow]")
            console.print(f"  Would remove member: {member_id}")
            return BaseResult(
                status="dry_run", message=f"Dry run: would remove member {member_id}"
            )

        if not yes and not self.confirm(
            f"Remove member {member_id} from the project?", default=False
        ):
            return BaseResult(status="cancelled", message="Cancelled by user")

        console.print(f"[blue]Removing member {member_id}...[/blue]")
        client.remove_member(member_id, project_id=project_id)
        console.print(f"[green]Member {member_id} removed[/green]")
        return BaseResult(status="success", message=f"Member {member_id} removed")

    def _list_invites(
        self, client: DeepgramClient, project_id: str | None
    ) -> BaseResult:
        console.print("[blue]Fetching pending invites...[/blue]")
        result = client.list_invites(project_id=project_id)

        invites_raw = result.get("invites", [])
        if not invites_raw:
            console.print("[yellow]No pending invites[/yellow]")
            return MembersResult(status="info", message="No pending invites")

        invite_models: list[InviteInfo] = []
        table = Table(
            title="Pending Invites", show_header=True, header_style="bold blue"
        )
        table.add_column("Email", style="green")
        table.add_column("Scope")

        for inv in invites_raw:
            inv_data = (
                inv
                if isinstance(inv, dict)
                else (inv.__dict__ if hasattr(inv, "__dict__") else {})
            )
            info = InviteInfo(
                email=inv_data.get("email", ""),
                scope=inv_data.get("scope", ""),
            )
            invite_models.append(info)
            table.add_row(info.email, info.scope)

        console.print(table)

        return MembersResult(
            status="success", invites=invite_models, count=len(invite_models)
        )

    def _revoke_invite(
        self,
        client: DeepgramClient,
        email: str,
        project_id: str | None,
        yes: bool = False,
        dry_run: bool = False,
    ) -> BaseResult:
        if dry_run:
            console.print("[yellow]Dry run — no changes made[/yellow]")
            console.print(f"  Would revoke invite for: {email}")
            return BaseResult(
                status="dry_run",
                message=f"Dry run: would revoke invite for {email}",
            )

        if not yes and not self.confirm(f"Revoke invite for {email}?", default=False):
            return BaseResult(status="cancelled", message="Cancelled by user")

        console.print(f"[blue]Revoking invite for {email}...[/blue]")
        client.delete_invite(email, project_id=project_id)
        console.print(f"[green]Invite for {email} revoked[/green]")
        return BaseResult(status="success", message=f"Invite for {email} revoked")
