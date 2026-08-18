"""Models command for deepctl."""

from __future__ import annotations

from typing import Any

from deepctl_core import (
    AuthManager,
    BaseCommand,
    BaseResult,
    Config,
    DeepgramClient,
    get_output_format,
    get_status_console,
)
from rich.console import Console
from rich.table import Table

from .models import ModelInfo, ModelsResult

console = Console()
# Status/progress chrome must never touch stdout, or it corrupts JSON/CSV
# output that callers pipe into jq and friends.
status_console = get_status_console()


class ModelsCommand(BaseCommand):
    """Command for listing available Deepgram models."""

    name = "models"
    help = "List available Deepgram models"
    short_help = "List models"

    requires_auth = True
    requires_project = False
    ci_friendly = True

    examples = [
        "dg models",
        "dg models --type stt",
        "dg models --type tts",
        "dg models --include-outdated",
    ]
    agent_help = (
        "List available Deepgram speech-to-text and text-to-speech models. "
        "Filter by type (stt/tts) and optionally include outdated versions. "
        "Requires authentication."
    )

    def get_arguments(self) -> list[dict[str, Any]]:
        return [
            {
                "names": ["--type", "-t"],
                "help": "Filter by model type (stt, tts)",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--include-outdated"],
                "help": "Include outdated model versions",
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
        model_type = kwargs.get("type")
        include_outdated = kwargs.get("include_outdated", False)

        try:
            result = client.list_models(include_outdated=include_outdated)

            stt_models = result.get("stt", [])
            tts_models = result.get("tts", [])

            all_models: list[ModelInfo] = []

            if model_type != "tts":
                for m in stt_models:
                    all_models.append(
                        ModelInfo(
                            model_id=m.get("uuid", m.get("model_id", "")),
                            name=m.get("name", ""),
                            version=m.get("version", ""),
                            language=m.get("language", ""),
                            model_type="stt",
                        )
                    )

            if model_type != "stt":
                for m in tts_models:
                    all_models.append(
                        ModelInfo(
                            model_id=m.get("uuid", m.get("model_id", "")),
                            name=m.get("name", ""),
                            version=m.get("version", ""),
                            language=m.get("language", ""),
                            model_type="tts",
                        )
                    )

            if not all_models:
                status_console.print("[yellow]No models found[/yellow]")
                return ModelsResult(status="info", message="No models found")

            # Render the human table only in default mode. For json/yaml/csv
            # the framework serialises the returned result to stdout, so
            # printing the table here would corrupt that output for piping.
            if get_output_format() == "default":
                table = Table(
                    title="Deepgram Models", show_header=True, header_style="bold blue"
                )
                table.add_column("Name", style="green")
                table.add_column("Type", style="cyan")
                table.add_column("Language")
                table.add_column("Version")
                table.add_column("ID", style="dim")

                for m in all_models:
                    table.add_row(
                        m.name, m.model_type.upper(), m.language, m.version, m.model_id
                    )

                console.print(table)
                console.print(f"\n[dim]{len(all_models)} model(s) found[/dim]")

            return ModelsResult(
                status="success",
                models=all_models,
                count=len(all_models),
            )

        except Exception as e:
            status_console.print(f"[red]Error listing models:[/red] {e}")
            return BaseResult(status="error", message=str(e))
