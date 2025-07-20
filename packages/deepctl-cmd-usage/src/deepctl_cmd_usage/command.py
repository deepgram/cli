"""Usage command for deepctl."""

from typing import Optional, List, Dict, Any
from datetime import datetime, date, timedelta
from rich.console import Console
from deepgram import DeepgramError

from deepctl_core import Config, AuthManager, DeepgramClient, BaseCommand, BaseResult
from deepctl_shared_utils import validate_date_format
from .models import UsageResult, UsageBucket

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

    def get_arguments(self) -> List[Dict[str, Any]]:
        """Get command arguments and options."""
        return [
            {
                "names": ["--project-id", "-p"],
                "help": "Project ID (uses configured project if not provided)",
                "type": str,
                "is_option": True
            },
            {
                "names": ["--start-date", "-s"],
                "help": "Start date (YYYY-MM-DD or ISO format)",
                "type": str,
                "is_option": True
            },
            {
                "names": ["--end-date", "-e"],
                "help": "End date (YYYY-MM-DD or ISO format)",
                "type": str,
                "is_option": True
            },
            {
                "names": ["--last-week"],
                "help": "Show usage for last week",
                "is_flag": True,
                "is_option": True
            },
            {
                "names": ["--last-month"],
                "help": "Show usage for last month",
                "is_flag": True,
                "is_option": True
            },
            {
                "names": ["--current-month"],
                "help": "Show usage for current month",
                "is_flag": True,
                "is_option": True
            },
            {
                "names": ["--summary"],
                "help": "Show summary only",
                "is_flag": True,
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
                    "[blue]Fetching usage for current month...[/blue]")
            elif start_date or end_date:
                # Validate custom date range
                if start_date and not validate_date_format(start_date):
                    return BaseResult(status="error", message=f"Invalid start date format: {start_date}")
                if end_date and not validate_date_format(end_date):
                    return BaseResult(status="error", message=f"Invalid end date format: {end_date}")

                console.print(
                    f"[blue]Fetching usage from {start_date or 'beginning'} to {end_date or 'now'}...[/blue]")
            else:
                # Default to current month
                start_date, end_date = self._get_current_month_range()
                console.print(
                    "[blue]Fetching usage for current month...[/blue]")

            # Get usage data
            result = client.get_usage(project_id, start_date, end_date)

            # Process and display results
            return self._process_usage_result(result, summary_only, start_date, end_date)

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

    def _process_usage_result(self, result: dict, summary_only: bool, start_date: str, end_date: str) -> BaseResult:
        """Process usage result and display formatted output."""
        try:
            # Extract usage data
            usage_data = self._extract_usage_data(result)

            if not usage_data:
                console.print(
                    "[yellow]No usage data found for the specified period[/yellow]")
                return BaseResult(status="info", message="No usage data found")

            # Display summary
            self._display_usage_summary(usage_data, start_date, end_date)

            # Display detailed breakdown if not summary only
            if not summary_only:
                self._display_usage_details(usage_data)

            total_seconds = usage_data.get(
                "duration", 0) if isinstance(usage_data, dict) else 0
            hours = total_seconds / \
                3600 if isinstance(total_seconds, (int, float)) else 0

            buckets: list[UsageBucket] = []
            # simple: if details per day exist in usage_data["details"] with date and seconds
            details = usage_data.get("details", {}) if isinstance(
                usage_data, dict) else {}
            if isinstance(details, dict):
                for period, val in details.items():
                    if isinstance(val, dict) and "duration" in val:
                        dur = val["duration"]
                        buckets.append(UsageBucket(
                            start=period, end="", hours=dur/3600 if isinstance(dur, (int, float)) else 0))
            return UsageResult(status="success", project_id=result.get("project_id", ""), buckets=buckets, total_hours=hours)

        except Exception as e:
            console.print(f"[red]Error processing usage data:[/red] {e}")
            return BaseResult(status="error", message=str(e))

    def _extract_usage_data(self, result: dict) -> Dict[str, Any]:
        """Extract usage data from API response."""
        if "usage" in result:
            return result["usage"]
        elif "results" in result:
            return result["results"]
        else:
            return result

    def _display_usage_summary(self, usage_data: Dict[str, Any], start_date: str, end_date: str) -> None:
        """Display usage summary."""
        console.print(
            f"\n[green]Usage Summary ({start_date} to {end_date}):[/green]")

        # Try to extract common usage metrics
        total_requests = usage_data.get("requests", 0)
        total_duration = usage_data.get("duration", 0)
        total_cost = usage_data.get("cost", 0)

        if total_requests:
            console.print(f"  Total Requests: {total_requests:,}")

        if total_duration:
            if isinstance(total_duration, (int, float)):
                hours = total_duration / 3600
                console.print(
                    f"  Total Duration: {hours:.2f} hours ({total_duration:,} seconds)")
            else:
                console.print(f"  Total Duration: {total_duration}")

        if total_cost:
            console.print(f"  Total Cost: ${total_cost:.2f}")

        # Display any other summary metrics
        for key, value in usage_data.items():
            if key not in ["requests", "duration", "cost", "details", "breakdown"]:
                if isinstance(value, (int, float)):
                    console.print(
                        f"  {key.replace('_', ' ').title()}: {value:,}")
                else:
                    console.print(
                        f"  {key.replace('_', ' ').title()}: {value}")

    def _display_usage_details(self, usage_data: Dict[str, Any]) -> None:
        """Display detailed usage breakdown."""
        console.print("\n[blue]Detailed Breakdown:[/blue]")

        # Look for detailed breakdown data
        details = usage_data.get("details", usage_data.get("breakdown", {}))

        if details and isinstance(details, dict):
            for category, data in details.items():
                console.print(f"\n  {category.replace('_', ' ').title()}:")

                if isinstance(data, dict):
                    for key, value in data.items():
                        if isinstance(value, (int, float)):
                            console.print(
                                f"    {key.replace('_', ' ').title()}: {value:,}")
                        else:
                            console.print(
                                f"    {key.replace('_', ' ').title()}: {value}")
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
