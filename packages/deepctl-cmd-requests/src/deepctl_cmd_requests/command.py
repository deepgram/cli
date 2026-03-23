"""Requests history command for deepctl."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from deepctl_core import (
    AuthManager,
    BaseCommand,
    BaseResult,
    Config,
    DeepgramClient,
)
from rich.console import Console
from rich.table import Table

from .models import RequestInfo, RequestsResult

console = Console()


class RequestsCommand(BaseCommand):
    """Command for viewing API request history."""

    name = "requests"
    help = "View Deepgram API request history"
    short_help = "Request history"

    requires_auth = True
    requires_project = True
    ci_friendly = True

    examples = [
        "dg requests",
        "dg requests --limit 50",
        "dg requests --status failed",
        "dg requests --endpoint listen --method streaming",
        "dg requests --show REQUEST_ID",
        "dg requests --last-week",
    ]
    agent_help = (
        "View API request history for the current project. "
        "Filter by status, endpoint, method, and date range. "
        "Requires authentication and a project ID."
    )

    def get_arguments(self) -> list[dict[str, Any]]:
        return [
            {
                "names": ["--show", "-s"],
                "help": "Show details for a specific request",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--status"],
                "help": "Filter by status (succeeded, failed)",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--endpoint"],
                "help": "Filter by endpoint (listen, read, speak, agent)",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--method"],
                "help": "Filter by method (sync, async, streaming)",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--limit"],
                "help": "Maximum number of results (default: 10)",
                "type": int,
                "is_option": True,
                "default": 10,
            },
            {
                "names": ["--page"],
                "help": "Page number for pagination",
                "type": int,
                "is_option": True,
            },
            {
                "names": ["--start-date"],
                "help": "Start date (YYYY-MM-DD)",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--end-date"],
                "help": "End date (YYYY-MM-DD)",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--last-week"],
                "help": "Show requests from last 7 days",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--last-day"],
                "help": "Show requests from last 24 hours",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--project-id", "-p"],
                "help": "Project ID (uses configured project if not provided)",
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
        show_request = kwargs.get("show")
        project_id = kwargs.get("project_id")

        try:
            if show_request:
                return self._show_request(client, show_request, project_id)
            else:
                return self._list_requests(
                    client,
                    project_id,
                    status=kwargs.get("status"),
                    endpoint=kwargs.get("endpoint"),
                    method=kwargs.get("method"),
                    limit=kwargs.get("limit"),
                    page=kwargs.get("page"),
                    start_date=kwargs.get("start_date"),
                    end_date=kwargs.get("end_date"),
                    last_week=kwargs.get("last_week", False),
                    last_day=kwargs.get("last_day", False),
                )
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            return BaseResult(status="error", message=str(e))

    def _list_requests(
        self,
        client: DeepgramClient,
        project_id: str | None,
        status: str | None = None,
        endpoint: str | None = None,
        method: str | None = None,
        limit: int | None = None,
        page: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        last_week: bool = False,
        last_day: bool = False,
    ) -> BaseResult:
        limit = limit or 10

        # Resolve date ranges
        start_dt = None
        end_dt = None
        if last_week:
            start_dt = datetime.now() - timedelta(days=7)
            end_dt = datetime.now()
        elif last_day:
            start_dt = datetime.now() - timedelta(days=1)
            end_dt = datetime.now()
        elif start_date:
            start_dt = datetime.fromisoformat(start_date)
            end_dt = datetime.fromisoformat(end_date) if end_date else datetime.now()

        console.print("[blue]Fetching request history...[/blue]")

        result = client.list_requests(
            project_id=project_id,
            start=start_dt,
            end=end_dt,
            limit=limit,
            page=page,
            status=status,
            endpoint=endpoint,
            method=method,
        )

        requests_raw = result.get("requests", [])
        if not requests_raw:
            console.print("[yellow]No requests found[/yellow]")
            return RequestsResult(status="info", message="No requests found")

        req_models: list[RequestInfo] = []
        table = Table(title="API Requests", show_header=True, header_style="bold blue")
        table.add_column("Time", style="dim")
        table.add_column("Endpoint", style="cyan")
        table.add_column("Method")
        table.add_column("Status")
        table.add_column("Duration")
        table.add_column("Request ID", style="dim")

        for r in requests_raw:
            req_data = (
                r
                if isinstance(r, dict)
                else (r.__dict__ if hasattr(r, "__dict__") else {})
            )

            info = RequestInfo(
                request_id=str(req_data.get("request_id", "")),
                created=str(req_data.get("created", "")),
                path=str(req_data.get("path", req_data.get("endpoint", ""))),
                method=req_data.get("method", ""),
                status="succeeded"
                if req_data.get("response", {}).get("code", 0) < 400
                else "failed",
                duration=float(req_data.get("duration", 0)),
            )
            req_models.append(info)

            status_style = "green" if info.status == "succeeded" else "red"
            table.add_row(
                info.created[:19] if info.created else "-",
                info.path or "-",
                info.method or "-",
                f"[{status_style}]{info.status}[/{status_style}]",
                f"{info.duration:.2f}s" if info.duration else "-",
                info.request_id[:12] + "..."
                if len(info.request_id) > 12
                else info.request_id,
            )

        console.print(table)
        console.print(f"\n[dim]{len(req_models)} request(s) shown[/dim]")

        return RequestsResult(
            status="success", requests=req_models, count=len(req_models)
        )

    def _show_request(
        self,
        client: DeepgramClient,
        request_id: str,
        project_id: str | None,
    ) -> BaseResult:
        console.print(f"[blue]Fetching request details:[/blue] {request_id}")
        result = client.get_request(request_id, project_id=project_id)

        console.print("[green]Request Details:[/green]")
        for key, value in result.items():
            if isinstance(value, dict):
                console.print(f"  {key}:")
                for k, v in value.items():
                    console.print(f"    {k}: {v}")
            else:
                console.print(f"  {key}: {value}")

        return BaseResult(status="success", message="Request details displayed")
