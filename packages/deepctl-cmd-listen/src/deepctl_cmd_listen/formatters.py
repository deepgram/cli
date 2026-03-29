"""Output formatters for listen/transcribe results."""

from __future__ import annotations

from typing import Any


def format_diarized_transcript(api_result: dict[str, Any]) -> str:
    """Extract word-level speaker data and format as labelled paragraphs.

    Groups consecutive words by speaker into lines:
        [Speaker 0] Welcome to our annual product keynote.
        [Speaker 1] This year we ship the fastest model yet.
    """
    try:
        results = api_result.get("results") or {}
        channels = results.get("channels") or []
        if not channels:
            return ""
        words = (channels[0].get("alternatives") or [{}])[0].get("words") or []
        return _words_to_speaker_lines(words)
    except Exception:
        return ""


def format_diarized_words(words: list[dict[str, Any]]) -> str:
    """Format a list of word objects (from a streaming message) into speaker lines."""
    return _words_to_speaker_lines(words)


def _words_to_speaker_lines(words: list[dict[str, Any]]) -> str:
    if not words:
        return ""

    lines: list[str] = []
    current_speaker: int | None = None
    current_words: list[str] = []

    for word_obj in words:
        speaker = word_obj.get("speaker", 0)
        # Prefer punctuated form if available
        text = word_obj.get("punctuated_word") or word_obj.get("word", "")
        if not text:
            continue
        if speaker != current_speaker:
            if current_words and current_speaker is not None:
                lines.append(f"[Speaker {current_speaker}] {' '.join(current_words)}")
            current_speaker = speaker
            current_words = [text]
        else:
            current_words.append(text)

    if current_words and current_speaker is not None:
        lines.append(f"[Speaker {current_speaker}] {' '.join(current_words)}")

    return "\n".join(lines)


def extract_plain_transcript(api_result: dict[str, Any]) -> str:
    """Extract flat transcript text, preferring paragraphs form.

    Uses ``or`` patterns instead of ``get(key, default)`` so that Pydantic
    ``model_dump()`` responses with ``key: None`` (key present, value None)
    are treated the same as missing keys.
    """
    try:
        results = api_result.get("results") or {}
        channels = results.get("channels") or []
        if not channels:
            return str(api_result.get("transcript") or "")
        alt = (channels[0].get("alternatives") or [{}])[0]
        # Paragraphs transcript is best-formatted
        paragraphs = alt.get("paragraphs") or {}
        if paragraphs and "transcript" in paragraphs:
            return str(paragraphs["transcript"] or "")
        return str(alt.get("transcript") or "")
    except Exception:
        return ""


def extract_summary(api_result: dict[str, Any]) -> str:
    """Extract summary text if present in the response."""
    try:
        results = api_result.get("results") or {}
        summary = results.get("summary") or {}
        return str(summary.get("short") or summary.get("text") or "")
    except Exception:
        return ""


def extract_topics(api_result: dict[str, Any]) -> list[str]:
    """Extract detected topic strings from the response."""
    try:
        segments = api_result.get("results", {}).get("topics", {}).get("segments", [])
        seen: set[str] = set()
        out: list[str] = []
        for seg in segments:
            for topic in seg.get("topics", []):
                name = topic.get("topic", "")
                if name and name not in seen:
                    seen.add(name)
                    confidence = topic.get("confidence_score", 0.0)
                    out.append(f"{name} ({confidence:.0%})")
        return out
    except Exception:
        return []
