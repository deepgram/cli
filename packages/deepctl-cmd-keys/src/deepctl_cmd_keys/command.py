"""API keys management command for deepctl."""

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

from .models import KeyCreatedInfo, KeyInfo, KeysResult

# Human/status output — routed to stderr when -o json/yaml/csv owns stdout
console = get_status_console()


class KeysCommand(BaseCommand):
    """Command for managing Deepgram API keys."""

    name = "keys"
    help = "Manage Deepgram API keys"
    short_help = "Manage API keys"

    requires_auth = True
    requires_project = True
    ci_friendly = True

    examples = [
        "dg keys",
        "dg keys --list",
        "dg keys --create --comment 'staging key' --scopes member",
        "dg keys --show KEY_ID",
        "dg keys --delete KEY_ID",
        "dg keys --list --status active",
    ]
    agent_help = (
        "Manage Deepgram API keys for the current project. "
        "List, create, show, and delete API keys. "
        "Requires authentication and a project ID."
    )

    def get_arguments(self) -> list[dict[str, Any]]:
        return [
            {
                "names": ["--list", "-l"],
                "help": "List all API keys",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--create", "-c"],
                "help": "Create a new API key",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--show", "-s"],
                "help": "Show details for a specific key",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--delete", "-d"],
                "help": "Delete an API key",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--comment"],
                "help": "Comment/label for new key",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--scopes"],
                "help": "Comma-separated scopes for new key (default: member)",
                "type": str,
                "is_option": True,
                "default": "member",
            },
            {
                "names": ["--expiration-date"],
                "help": "Expiration date for new key (ISO format)",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--ttl"],
                "help": "Time to live in seconds for new key",
                "type": int,
                "is_option": True,
            },
            {
                "names": ["--tags"],
                "help": "Comma-separated tags for new key",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--status"],
                "help": "Filter keys by status (active, expired)",
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
        create_key = kwargs.get("create", False)
        show_key = kwargs.get("show")
        delete_key = kwargs.get("delete")
        project_id = kwargs.get("project_id")

        dry_run = kwargs.get("dry_run", False)

        try:
            if create_key:
                return self._create_key(client, project_id, dry_run=dry_run, **kwargs)
            elif show_key:
                return self._show_key(client, show_key, project_id)
            elif delete_key:
                return self._delete_key(
                    client,
                    delete_key,
                    project_id,
                    kwargs.get("yes", False),
                    dry_run=dry_run,
                )
            else:
                # Default: list keys
                return self._list_keys(client, project_id, kwargs.get("status"))

        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            return BaseResult(status="error", message=str(e))

    def _list_keys(
        self,
        client: DeepgramClient,
        project_id: str | None,
        status: str | None,
    ) -> BaseResult:
        console.print("[blue]Fetching API keys...[/blue]")
        result = client.list_keys(project_id=project_id, status=status)

        keys_raw = result.get("api_keys", result.get("keys", []))
        if not keys_raw:
            console.print("[yellow]No API keys found[/yellow]")
            return KeysResult(status="info", message="No keys found")

        key_models: list[KeyInfo] = []
        table = Table(title="API Keys", show_header=True, header_style="bold blue")
        table.add_column("Comment", style="green")
        table.add_column("Key ID", style="dim")
        table.add_column("Scopes")
        table.add_column("Created")
        table.add_column("Expires")

        for k in keys_raw:
            # SDK may nest key data under "api_key"
            key_data = k.get("api_key", k) if isinstance(k, dict) else k
            if hasattr(key_data, "__dict__"):
                key_data = key_data.__dict__

            info = KeyInfo(
                key_id=str(key_data.get("api_key_id", "")),
                comment=str(key_data.get("comment", "")),
                scopes=key_data.get("scopes", []),
                created=str(key_data.get("created", "")),
                expiration_date=str(key_data.get("expiration_date", "") or ""),
                tags=key_data.get("tags", []),
            )
            key_models.append(info)

            table.add_row(
                info.comment or "(no comment)",
                info.key_id,
                ", ".join(info.scopes) if info.scopes else "-",
                info.created[:10] if info.created else "-",
                info.expiration_date[:10] if info.expiration_date else "never",
            )

        console.print(table)
        console.print(f"\n[dim]{len(key_models)} key(s) found[/dim]")

        return KeysResult(status="success", keys=key_models, count=len(key_models))

    def _create_key(
        self,
        client: DeepgramClient,
        project_id: str | None,
        dry_run: bool = False,
        **kwargs: Any,
    ) -> BaseResult:
        comment = kwargs.get("comment", "")
        scopes_str = kwargs.get("scopes", "member")
        scopes = (
            [s.strip() for s in scopes_str.split(",")] if scopes_str else ["member"]
        )
        expiration = kwargs.get("expiration_date")
        ttl = kwargs.get("ttl")
        tags_str = kwargs.get("tags")
        tags = [t.strip() for t in tags_str.split(",")] if tags_str else None

        if dry_run:
            console.print("[yellow]Dry run — no changes made[/yellow]")
            console.print(f"  Would create key: comment='{comment or '(none)'}'")
            console.print(f"  Scopes:  {', '.join(scopes)}")
            if expiration:
                console.print(f"  Expires: {expiration}")
            if ttl:
                console.print(f"  TTL:     {ttl}s")
            if tags:
                console.print(f"  Tags:    {', '.join(tags)}")
            return BaseResult(status="dry_run", message="Dry run: key would be created")

        console.print("[blue]Creating API key...[/blue]")

        result = client.create_key(
            project_id=project_id,
            comment=comment,
            scopes=scopes,
            expiration_date=expiration,
            time_to_live=ttl,
            tags=tags,
        )

        key_id = result.get("api_key_id", "")
        key_value = result.get("key", "")

        console.print("[green]API key created successfully[/green]")
        console.print(f"  Key ID:  {key_id}")
        console.print(f"  Comment: {comment or '(none)'}")
        console.print(f"  Scopes:  {', '.join(scopes)}")
        if key_value:
            console.print(f"\n  [bold yellow]Key: {key_value}[/bold yellow]")
            console.print("[dim]  Save this key now — it won't be shown again.[/dim]")

        created = KeyCreatedInfo(
            key_id=key_id,
            key=key_value,
            comment=comment,
            scopes=scopes,
        )
        return KeysResult(status="success", message="Key created", created_key=created)

    def _show_key(
        self,
        client: DeepgramClient,
        key_id: str,
        project_id: str | None,
    ) -> BaseResult:
        console.print(f"[blue]Fetching key details:[/blue] {key_id}")
        result = client.get_key(key_id, project_id=project_id)

        key_data = result.get("api_key", result) if isinstance(result, dict) else result

        info = KeyInfo(
            key_id=str(key_data.get("api_key_id", "")),
            comment=str(key_data.get("comment", "")),
            scopes=key_data.get("scopes", []),
            created=str(key_data.get("created", "")),
            expiration_date=str(key_data.get("expiration_date", "") or ""),
            tags=key_data.get("tags", []),
        )

        console.print("[green]Key Details:[/green]")
        console.print(f"  Key ID:     {info.key_id}")
        console.print(f"  Comment:    {info.comment or '(none)'}")
        console.print(f"  Scopes:     {', '.join(info.scopes) if info.scopes else '-'}")
        console.print(f"  Created:    {info.created or '-'}")
        console.print(f"  Expires:    {info.expiration_date or 'never'}")
        if info.tags:
            console.print(f"  Tags:       {', '.join(info.tags)}")

        return KeysResult(status="success", keys=[info], count=1)

    def _delete_key(
        self,
        client: DeepgramClient,
        key_id: str,
        project_id: str | None,
        yes: bool = False,
        dry_run: bool = False,
    ) -> BaseResult:
        if dry_run:
            console.print("[yellow]Dry run — no changes made[/yellow]")
            console.print(f"  Would delete key: {key_id}")
            return BaseResult(
                status="dry_run", message=f"Dry run: key {key_id} would be deleted"
            )

        if not yes and not self.confirm(
            f"Delete API key {key_id}? This cannot be undone.", default=False
        ):
            return BaseResult(status="cancelled", message="Cancelled by user")

        console.print(f"[blue]Deleting API key:[/blue] {key_id}")
        client.delete_key(key_id, project_id=project_id)
        console.print(f"[green]API key {key_id} deleted[/green]")
        return BaseResult(status="success", message=f"Key {key_id} deleted")
