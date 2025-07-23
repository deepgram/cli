"""Usage command for deepctl."""

from datetime import datetime, timedelta
from typing import Any

from deepctl_core import (
    AuthManager,
    BaseCommand,
    BaseResult,
    Config,
    DeepgramClient,
)
from deepctl_shared_utils import validate_date_format
from rich.console import Console

from .models import UsageBucket, UsageResult

console = Console()


class UsageCommand(BaseCommand):
    """Command for viewing Deepgram usage statistics."""

    name = "usage"
    help = "View Deepgram usage statistics"
    short_help = "View usage statistics"

    # Usage requires authentication and project
    requires_auth = True
    requires_project = True
    ci_friendly = True

    def get_arguments(self) -> list[dict[str, Any]]:
        """Get command arguments and options."""
        return [
            {
                "names": ["--project-id", "-p"],
                "help": "Project ID (uses configured project if not provided)",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--start-date", "-s"],
                "help": "Start date (YYYY-MM-DD or ISO format)",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--end-date", "-e"],
                "help": "End date (YYYY-MM-DD or ISO format)",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--last-week"],
                "help": "Show usage for last week",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--last-month"],
                "help": "Show usage for last month",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--current-month"],
                "help": "Show usage for current month",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--summary"],
                "help": "Show summary only",
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
        """Handle usage command."""
        project_id = kwargs.get("project_id")
        start_date = kwargs.get("start_date")
        end_date = kwargs.get("end_date")
        last_week = kwargs.get("last_week", False)
        last_month = kwargs.get("last_month", False)
        current_month = kwargs.get("current_month", False)
        summary_only = kwargs.get("summary", False)

        try:
            # Determine date range
            if last_week:
                start_date, end_date = self._get_last_week_range()
                console.print("[blue]Fetching usage for last week...[/blue]")
            elif last_month:
                start_date, end_date = self._get_last_month_range()
                console.print("[blue]Fetching usage for last month...[/blue]")
            elif current_month:
                start_date, end_date = self._get_current_month_range()
                console.print(
                    "[blue]Fetching usage for current month...[/blue]"
                )
            elif start_date or end_date:
                # Validate custom date range
                if start_date and not validate_date_format(start_date):
                    return BaseResult(
                        status="error",
                        message=f"Invalid start date format: {start_date}",
                    )
                if end_date and not validate_date_format(end_date):
                    return BaseResult(
                        status="error",
                        message=f"Invalid end date format: {end_date}",
                    )

                console.print(
                    f"[blue]Fetching usage from "
                    f"{start_date or 'beginning'} to "
                    f"{end_date or 'now'}...[/blue]"
                )
            else:
                # Default to current month
                start_date, end_date = self._get_current_month_range()
                console.print(
                    "[blue]Fetching usage for current month...[/blue]"
                )

            # Get usage data
            result = client.get_usage(project_id, start_date, end_date)

            # Process and display results
            return self._process_usage_result(
                result, summary_only, start_date, end_date
            )

        except Exception as e:
            console.print(f"[red]Error fetching usage:[/red] {e}")
            return BaseResult(status="error", message=str(e))

    def _get_last_week_range(self) -> tuple[str, str]:
        """Get date range for last week."""
        today = datetime.now()
        last_week = today - timedelta(days=7)
        return last_week.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")

    def _get_last_month_range(self) -> tuple[str, str]:
        """Get date range for last month."""
        today = datetime.now()
        last_month = today - timedelta(days=30)
        return last_month.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")

    def _get_current_month_range(self) -> tuple[str, str]:
        """Get date range for current month."""
        today = datetime.now()
        first_day = today.replace(day=1)
        return first_day.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")

    def _process_usage_result(
        self,
        result: Any,
        summary_only: bool,
        start_date: str | None,
        end_date: str | None,
    ) -> BaseResult:
        """Process usage result and display formatted output."""
        try:
            # Handle UsageSummaryResponse object - try different
            # conversion methods
            result_dict = None

            if hasattr(result, "to_dict"):
                result_dict = result.to_dict()
            elif hasattr(result, "dict"):
                result_dict = result.dict()
            elif hasattr(result, "__dict__"):
                # For SDK response objects, we need to access attributes
                # directly
                result_dict = {
                    "results": (
                        result.results if hasattr(result, "results") else []
                    ),
                    "start": (
                        result.start
                        if hasattr(result, "start")
                        else start_date
                    ),
                    "end": result.end if hasattr(result, "end") else end_date,
                    "project_id": (
                        result.project_id
                        if hasattr(result, "project_id")
                        else ""
                    ),
                }
            else:
                result_dict = result

            # Extract usage data
            if (
                result_dict
                and "results" in result_dict
                and isinstance(result_dict["results"], list)
            ):
                # Calculate totals from results array
                total_hours = 0
                total_requests = 0
                total_tts_characters = 0
                total_tokens_out = 0

                buckets: list[UsageBucket] = []

                for item in result_dict["results"]:
                    # Sum up totals
                    if "total_hours" in item:
                        total_hours += item["total_hours"]
                    if "requests" in item:
                        total_requests += item["requests"]
                    if "tts" in item and "characters" in item["tts"]:
                        total_tts_characters += item["tts"]["characters"]
                    if "tokens" in item and "out" in item["tokens"]:
                        total_tokens_out += item["tokens"]["out"]

                    # Create bucket for each day
                    buckets.append(
                        UsageBucket(
                            start=item.get("start", ""),
                            end=item.get("end", ""),
                            hours=float(item.get("total_hours", 0)),
                        )
                    )

                # Display summary
                console.print(
                    f"\n[green]Usage Summary ({start_date} to "
                    f"{end_date}):[/green]"
                )
                console.print(f"  Total Hours: {total_hours:,.1f}")
                console.print(f"  Total Requests: {total_requests:,}")

                if total_tts_characters > 0:
                    console.print(
                        f"  TTS Characters: {total_tts_characters:,}"
                    )

                if total_tokens_out > 0:
                    console.print(f"  Tokens Out: {total_tokens_out:,}")

                # Display detailed breakdown if not summary only
                if not summary_only and result_dict["results"]:
                    console.print("\n[blue]Daily Breakdown:[/blue]")
                    for item in result_dict["results"]:
                        item_date = item.get("start", "Unknown")
                        hours = item.get("total_hours", 0)
                        requests = item.get("requests", 0)

                        console.print(f"\n  {item_date}:")
                        console.print(f"    Hours: {hours}")
                        console.print(f"    Requests: {requests}")

                        if "tts" in item:
                            console.print(
                                f"    TTS Characters: "
                                f"{item['tts'].get('characters', 0):,}"
                            )
                            console.print(
                                f"    TTS Requests: "
                                f"{item['tts'].get('requests', 0):,}"
                            )

                        if (
                            "tokens" in item
                            and item["tokens"].get("out", 0) > 0
                        ):
                            console.print(
                                f"    Tokens Out: "
                                f"{item['tokens'].get('out', 0):,}"
                            )

                project_id = result_dict.get("project_id", "")
                return UsageResult(
                    status="success",
                    project_id=project_id,
                    buckets=buckets,
                    total_hours=float(total_hours),
                )
            else:
                console.print(
                    "[yellow]No usage data found for the specified "
                    "period[/yellow]"
                )
                return BaseResult(status="info", message="No usage data found")

        except Exception as e:
            console.print(f"[red]Error processing usage data:[/red] {e}")
            import traceback

            traceback.print_exc()
            return BaseResult(status="error", message=str(e))

    def _extract_usage_data(self, result: dict[str, Any]) -> dict[str, Any]:
        """Extract usage data from API response."""
        if "usage" in result:
            return dict(result["usage"])
        elif "results" in result:
            return dict(result["results"])
        else:
            return result

    def _display_usage_summary(
        self, usage_data: dict[str, Any], start_date: str, end_date: str
    ) -> None:
        """Display usage summary."""
        console.print(
            f"\n[green]Usage Summary ({start_date} to " f"{end_date}):[/green]"
        )

        # Try to extract common usage metrics
        total_requests = usage_data.get("requests", 0)
        total_duration = usage_data.get("duration", 0)
        total_cost = usage_data.get("cost", 0)

        if total_requests:
            console.print(f"  Total Requests: {total_requests:,}")

        if total_duration:
            if isinstance(total_duration, int | float):
                hours = total_duration / 3600
                console.print(
                    f"  Total Duration: {hours:.2f} hours "
                    f"({total_duration:,} seconds)"
                )
            else:
                console.print(f"  Total Duration: {total_duration}")

        if total_cost:
            console.print(f"  Total Cost: ${total_cost:.2f}")

        # Display any other summary metrics
        for key, value in usage_data.items():
            if key not in [
                "requests",
                "duration",
                "cost",
                "details",
                "breakdown",
            ]:
                if isinstance(value, int | float):
                    console.print(
                        f"  {key.replace('_', ' ').title()}: " f"{value:,}"
                    )
                else:
                    console.print(
                        f"  {key.replace('_', ' ').title()}: {value}"
                    )

    def _display_usage_details(self, usage_data: dict[str, Any]) -> None:
        """Display detailed usage breakdown."""
        console.print("\n[blue]Detailed Breakdown:[/blue]")

        # Look for detailed breakdown data
        details = usage_data.get("details", usage_data.get("breakdown", {}))

        if details and isinstance(details, dict):
            for category, data in details.items():
                console.print(f"\n  {category.replace('_', ' ').title()}:")

                if isinstance(data, dict):
                    for key, value in data.items():
                        if isinstance(value, int | float):
                            console.print(
                                f"    {key.replace('_', ' ').title()}: "
                                f"{value:,}"
                            )
                        else:
                            console.print(
                                f"    {key.replace('_', ' ').title()}: {value}"
                            )
                else:
                    console.print(f"    {data}")
        else:
            console.print("  No detailed breakdown available")

    def _format_duration(self, seconds: float) -> str:
        """Format duration in seconds to human readable format."""
        if seconds < 60:
            return f"{seconds:.1f} seconds"
        elif seconds < 3600:
            minutes = seconds / 60
            return f"{minutes:.1f} minutes"
        else:
            hours = seconds / 3600
            return f"{hours:.2f} hours"

    def _format_cost(self, cost: float) -> str:
        """Format cost with currency symbol."""
        return f"${cost:.2f}"
