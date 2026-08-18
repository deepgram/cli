"""Read (text intelligence) command for deepctl."""

from __future__ import annotations

import sys
from pathlib import Path
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

from .models import ReadResult

console = Console()
# Status/progress chrome must never touch stdout, or it corrupts JSON/CSV
# output that callers pipe into jq and friends.
status_console = Console(stderr=True)


class ReadCommand(BaseCommand):
    """Command for analyzing text using Deepgram's text intelligence."""

    name = "read"
    help = "Analyze text using Deepgram text intelligence"
    short_help = "Text intelligence"

    requires_auth = True
    requires_project = False
    ci_friendly = True

    examples = [
        'dg read "The product is amazing and works perfectly."',
        "dg read --file article.txt --summarize",
        'dg read --sentiment --topics --intents "Customer called about billing issue."',
        "cat review.txt | dg read --sentiment --summarize",
    ]
    agent_help = (
        "Analyze text using Deepgram's text intelligence API. "
        "Supports sentiment analysis, summarization, topic detection, and intent recognition. "
        "Text can be provided as an argument, from a file, or piped via stdin."
    )

    def get_arguments(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "text",
                "help": "Text to analyze",
                "required": False,
                "default": None,
            },
            {
                "names": ["--file", "-f"],
                "help": "Read text from file",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--sentiment"],
                "help": "Analyze sentiment",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--summarize"],
                "help": "Generate summary",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--topics"],
                "help": "Detect topics",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--intents"],
                "help": "Detect intents",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--language", "-l"],
                "help": "Language code (e.g., en)",
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
        text = kwargs.get("text")
        file_path = kwargs.get("file")
        sentiment = kwargs.get("sentiment", False)
        summarize = kwargs.get("summarize", False)
        topics = kwargs.get("topics", False)
        intents = kwargs.get("intents", False)
        language = kwargs.get("language")

        # Resolve text input: arg > --file > stdin
        if not text and file_path:
            path = Path(file_path)
            if not path.exists():
                return BaseResult(
                    status="error", message=f"File not found: {file_path}"
                )
            text = path.read_text().strip()
        elif not text and not sys.stdin.isatty():
            text = sys.stdin.read().strip()

        if not text:
            return BaseResult(
                status="error",
                message="No text provided. Pass text as argument, use --file, or pipe via stdin.",
            )

        # Default: enable all features if none specified
        if not any([sentiment, summarize, topics, intents]):
            sentiment = True
            summarize = True
            topics = True
            intents = True

        try:
            status_console.print("[blue]Analyzing text...[/blue]")

            result = client.analyze_text(
                text=text,
                sentiment=sentiment,
                summarize=summarize,
                topics=topics,
                intents=intents,
                language=language,
            )

            return self._display_results(result, sentiment, summarize, topics, intents)

        except Exception as e:
            status_console.print(f"[red]Error analyzing text:[/red] {e}")
            return BaseResult(status="error", message=str(e))

    def _display_results(
        self,
        result: dict[str, Any],
        show_sentiment: bool,
        show_summary: bool,
        show_topics: bool,
        show_intents: bool,
    ) -> ReadResult:
        results = result.get("results", {})
        read_result = ReadResult(status="success")

        # Human display only in default mode. For json/yaml/csv the framework
        # serialises the returned result to stdout, so anything printed here
        # would corrupt output that callers pipe into jq.
        show = get_output_format() == "default"

        # Summary
        if show_summary:
            summaries = results.get("summary", {})
            summary_text = (
                summaries.get("text", "") if isinstance(summaries, dict) else ""
            )
            if summary_text:
                if show:
                    console.print("\n[green]Summary:[/green]")
                    console.print(f"  {summary_text}")
                read_result.summary = summary_text

        # Sentiment
        if show_sentiment:
            sentiments = results.get("sentiments", {})
            if sentiments:
                average = sentiments.get("average", {})
                sentiment_val = average.get("sentiment", "")
                sentiment_score = average.get("sentiment_score", 0.0)
                if show:
                    console.print(
                        f"\n[green]Sentiment:[/green] "
                        f"{sentiment_val} ({sentiment_score:.2f})"
                    )
                read_result.sentiment = sentiment_val
                read_result.sentiment_score = float(sentiment_score)

        # Topics
        if show_topics:
            topics_data = results.get("topics", {})
            segments = (
                topics_data.get("segments", []) if isinstance(topics_data, dict) else []
            )
            if segments:
                if show:
                    console.print("\n[green]Topics:[/green]")
                seen_topics: set[str] = set()
                for seg in segments:
                    for topic in seg.get("topics", []):
                        topic_name = topic.get("topic", "")
                        if topic_name and topic_name not in seen_topics:
                            seen_topics.add(topic_name)
                            confidence = topic.get("confidence_score", 0.0)
                            if show:
                                console.print(f"  • {topic_name} ({confidence:.0%})")
                read_result.topics = segments

        # Intents
        if show_intents:
            intents_data = results.get("intents", {})
            segments = (
                intents_data.get("segments", [])
                if isinstance(intents_data, dict)
                else []
            )
            if segments:
                if show:
                    console.print("\n[green]Intents:[/green]")
                seen_intents: set[str] = set()
                for seg in segments:
                    for intent in seg.get("intents", []):
                        intent_name = intent.get("intent", "")
                        if intent_name and intent_name not in seen_intents:
                            seen_intents.add(intent_name)
                            confidence = intent.get("confidence_score", 0.0)
                            if show:
                                console.print(f"  • {intent_name} ({confidence:.0%})")
                read_result.intents = segments

        return read_result
