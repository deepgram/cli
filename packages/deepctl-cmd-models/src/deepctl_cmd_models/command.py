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

# Models the API still lists but which are legacy. The catalog flags them so
# that nobody — human or coding agent — treats them as a current model family
# or composes new model names from them. (Paying accounts have requested the
# nonexistent "nova-3-conversational", a natural composition of "nova-3" and
# the legacy name below.)
DEPRECATED_MODELS: dict[str, str] = {
    "conversationalai": (
        "Legacy model. For conversational audio use 'nova-3'. There is no "
        "model named 'nova-3-conversational'."
    ),
    "2-conversationalai": (
        "Legacy model. For conversational audio use 'nova-3'. There is no "
        "model named 'nova-3-conversational'."
    ),
}


def _build_model_info(m: dict[str, Any], model_type: str) -> ModelInfo:
    """Map one API model entry onto ModelInfo.

    The Deepgram Python SDK's generated response classes rename the API's
    ``uuid`` field to ``uuid_`` (to avoid shadowing), so ``model_dump()``
    emits ``uuid_`` — both spellings are read here. The API also reports
    ``languages`` as a list; the older singular ``language`` key is kept as
    a fallback for compatibility.
    """
    model_id = m.get("uuid_") or m.get("uuid") or m.get("model_id") or ""

    raw_languages = m.get("languages") or []
    if not isinstance(raw_languages, list):
        raw_languages = [raw_languages]
    languages = [str(lang) for lang in raw_languages]
    primary_language = str(m.get("language") or (languages[0] if languages else ""))

    name = str(m.get("name") or "")
    deprecation_note = DEPRECATED_MODELS.get(name.lower(), "")

    return ModelInfo(
        model_id=str(model_id),
        name=name,
        canonical_name=str(m.get("canonical_name") or ""),
        architecture=str(m.get("architecture") or ""),
        version=str(m.get("version") or ""),
        language=primary_language,
        languages=languages,
        model_type=model_type,
        deprecated=bool(deprecation_note),
        deprecation_note=deprecation_note,
    )


def _format_languages(languages: list[str], limit: int = 4) -> str:
    """Join a language list for table display, truncating long lists."""
    if len(languages) <= limit:
        return ", ".join(languages)
    return ", ".join(languages[:limit]) + f" +{len(languages) - limit}"


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
        "Use the canonical_name field as the `model` request parameter. "
        "Entries flagged deprecated are legacy: do not use them or derive "
        "new model names from them. Requires authentication."
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

            # `or []` also covers an explicit null in the response body.
            stt_models = result.get("stt") or []
            tts_models = result.get("tts") or []

            all_models: list[ModelInfo] = []

            if model_type != "tts":
                for m in stt_models:
                    all_models.append(_build_model_info(m, "stt"))
                if not stt_models:
                    status_console.print(
                        "[yellow]Warning: the API returned zero speech-to-text "
                        "models. Deepgram publishes speech-to-text models "
                        "(Nova-3, Flux), so an empty list usually means an API "
                        "or account problem, not an empty catalog.[/yellow]"
                    )

            if model_type != "stt":
                for m in tts_models:
                    all_models.append(_build_model_info(m, "tts"))
                if not tts_models:
                    status_console.print(
                        "[yellow]Warning: the API returned zero text-to-speech "
                        "models. Deepgram publishes text-to-speech models "
                        "(Aura-2), so an empty list usually means an API or "
                        "account problem, not an empty catalog.[/yellow]"
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
                table.add_column("Canonical name", style="green")
                table.add_column("Type", style="cyan")
                table.add_column("Languages")
                table.add_column("Version")
                table.add_column("ID", style="dim")

                for m in all_models:
                    display_name = m.name
                    if m.deprecated:
                        display_name = f"{m.name} [yellow](deprecated)[/yellow]"
                    table.add_row(
                        display_name,
                        m.canonical_name,
                        m.model_type.upper(),
                        _format_languages(m.languages),
                        m.version,
                        m.model_id,
                    )

                console.print(table)
                console.print(f"\n[dim]{len(all_models)} model(s) found[/dim]")

                deprecated_notes = {
                    m.name: m.deprecation_note for m in all_models if m.deprecated
                }
                for name, note in sorted(deprecated_notes.items()):
                    console.print(f"[yellow]Deprecated:[/yellow] {name} — {note}")

            return ModelsResult(
                status="success",
                models=all_models,
                count=len(all_models),
            )

        except Exception as e:
            status_console.print(f"[red]Error listing models:[/red] {e}")
            return BaseResult(status="error", message=str(e))
