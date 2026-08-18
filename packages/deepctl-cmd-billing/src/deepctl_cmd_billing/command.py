"""Billing command for deepctl."""

from __future__ import annotations

from typing import Any

from deepctl_core import (
    AuthManager,
    BaseCommand,
    BaseResult,
    Config,
    DeepgramClient,
    get_output_format,
)
from rich.console import Console
from rich.table import Table

from .models import BalanceInfo, BillingResult

console = Console()
# Status/progress chrome must never touch stdout, or it corrupts JSON/CSV
# output that callers pipe into jq and friends.
status_console = Console(stderr=True)


class BillingCommand(BaseCommand):
    """Command for viewing billing information."""

    name = "billing"
    help = "View Deepgram billing and balances"
    short_help = "Billing info"

    requires_auth = True
    requires_project = True
    ci_friendly = True

    examples = [
        "dg billing",
        "dg billing --balances",
        "dg billing --breakdown --start 2025-01-01 --end 2025-01-31",
        "dg billing --breakdown --grouping tags",
    ]
    agent_help = (
        "View billing breakdown and account balances for the current project. "
        "Requires authentication and a project ID."
    )

    def get_arguments(self) -> list[dict[str, Any]]:
        return [
            {
                "names": ["--balances", "-b"],
                "help": "Show account balances",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--breakdown"],
                "help": "Show billing breakdown",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--start"],
                "help": "Start date for breakdown (YYYY-MM-DD)",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--end"],
                "help": "End date for breakdown (YYYY-MM-DD)",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--grouping"],
                "help": "Grouping for breakdown (accessor, deployment, line_item, tags)",
                "type": str,
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
        show_balances = kwargs.get("balances", False)
        show_breakdown = kwargs.get("breakdown", False)
        project_id = kwargs.get("project_id")

        try:
            # Default: show both balances and breakdown
            if not show_balances and not show_breakdown:
                show_balances = True
                show_breakdown = True

            result = BillingResult(status="success")

            if show_balances:
                self._show_balances(client, project_id, result)

            if show_breakdown:
                self._show_breakdown(
                    client,
                    project_id,
                    result,
                    start=kwargs.get("start"),
                    end=kwargs.get("end"),
                    grouping=kwargs.get("grouping"),
                )

            return result

        except Exception as e:
            status_console.print(f"[red]Error:[/red] {e}")
            return BaseResult(status="error", message=str(e))

    def _show_balances(
        self,
        client: DeepgramClient,
        project_id: str | None,
        result: BillingResult,
    ) -> None:
        status_console.print("[blue]Fetching balances...[/blue]")
        data = client.get_balances(project_id=project_id)

        balances_raw = data.get("balances", [])
        if not balances_raw:
            status_console.print("[yellow]No balances found[/yellow]")
            return

        # Always populate the returned result; only the human table render
        # is gated on default mode (json/yaml/csv are emitted by the framework).
        for b in balances_raw:
            b_data = (
                b
                if isinstance(b, dict)
                else (b.__dict__ if hasattr(b, "__dict__") else {})
            )
            info = BalanceInfo(
                balance_id=b_data.get("balance_id", ""),
                amount=float(b_data.get("amount", 0)),
                units=b_data.get("units", ""),
            )
            result.balances.append(info)

        if get_output_format() == "default":
            table = Table(
                title="Account Balances", show_header=True, header_style="bold blue"
            )
            table.add_column("Balance ID", style="dim")
            table.add_column("Amount", justify="right", style="green")
            table.add_column("Units")

            for info in result.balances:
                table.add_row(info.balance_id, f"{info.amount:,.2f}", info.units)

            console.print(table)

    def _show_breakdown(
        self,
        client: DeepgramClient,
        project_id: str | None,
        result: BillingResult,
        start: str | None = None,
        end: str | None = None,
        grouping: str | None = None,
    ) -> None:

        status_console.print("[blue]Fetching billing breakdown...[/blue]")
        data = client.get_billing_breakdown(
            project_id=project_id,
            start=start,
            end=end,
            grouping=grouping,
        )

        result.breakdown = data

        # Human display only in default mode; json/yaml/csv are emitted by the
        # framework from result.breakdown.
        if get_output_format() != "default":
            return

        # Display breakdown summary
        resolution = data.get("resolution", {})
        if resolution:
            period = resolution.get("period", "")
            amount = resolution.get("amount", 0)
            if period:
                status_console.print(
                    f"\n[green]Billing Period:[/green] {period} ({amount} units)"
                )

        results = data.get("results", [])
        if results:
            table = Table(
                title="Billing Breakdown", show_header=True, header_style="bold blue"
            )
            table.add_column("Period")
            table.add_column("Amount", justify="right", style="green")
            table.add_column("Units")

            for item in results:
                item_data = item if isinstance(item, dict) else {}
                table.add_row(
                    str(item_data.get("start", "-")),
                    f"{item_data.get('amount', 0):,.2f}",
                    str(item_data.get("units", "")),
                )

            console.print(table)
        else:
            status_console.print(
                "[dim]No breakdown data available for the specified period[/dim]"
            )
