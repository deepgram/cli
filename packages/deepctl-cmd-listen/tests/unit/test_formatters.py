"""Tests for listen command formatters."""

from __future__ import annotations

from deepctl_cmd_listen.formatters import (
    extract_plain_transcript,
    extract_summary,
    extract_topics,
    format_diarized_transcript,
    format_diarized_words,
)


def _words(*entries: tuple[str, float, float, int]) -> list[dict]:
    """Build word dicts from (text, start, end, speaker) tuples."""
    return [
        {
            "word": t,
            "punctuated_word": t,
            "start": s,
            "end": e,
            "speaker": sp,
        }
        for t, s, e, sp in entries
    ]


# ── format_diarized_words ─────────────────────────────────────────────────────


class TestFormatDiarizedWords:
    def test_single_speaker(self):
        words = _words(("Hello", 0.0, 0.5, 0), ("world", 0.6, 1.0, 0))
        assert format_diarized_words(words) == "[Speaker 0] Hello world"

    def test_two_speakers(self):
        words = _words(
            ("Hi", 0.0, 0.5, 0),
            ("there", 0.6, 1.0, 0),
            ("Hey", 1.2, 1.5, 1),
            ("back", 1.6, 2.0, 1),
        )
        result = format_diarized_words(words)
        lines = result.splitlines()
        assert lines[0] == "[Speaker 0] Hi there"
        assert lines[1] == "[Speaker 1] Hey back"

    def test_speaker_change_mid_sequence(self):
        words = _words(("A", 0.0, 0.5, 0), ("B", 0.6, 1.0, 1))
        lines = format_diarized_words(words).splitlines()
        assert len(lines) == 2

    def test_uses_punctuated_word_when_available(self):
        words = [
            {"word": "hello", "punctuated_word": "Hello,", "start": 0.0, "end": 0.5, "speaker": 0}
        ]
        assert "Hello," in format_diarized_words(words)

    def test_falls_back_to_word_key(self):
        words = [{"word": "hi", "start": 0.0, "end": 0.5, "speaker": 0}]
        assert "hi" in format_diarized_words(words)

    def test_empty_words_returns_empty_string(self):
        assert format_diarized_words([]) == ""

    def test_skips_words_with_no_text(self):
        words = [{"word": "", "punctuated_word": "", "start": 0.0, "end": 0.5, "speaker": 0}]
        assert format_diarized_words(words) == ""

    def test_multiple_speaker_changes(self):
        words = _words(
            ("A", 0.0, 0.5, 0),
            ("B", 0.6, 1.0, 1),
            ("C", 1.2, 1.5, 0),
        )
        result = format_diarized_words(words)
        assert result.count("[Speaker") == 3


# ── format_diarized_transcript ────────────────────────────────────────────────


class TestFormatDiarizedTranscript:
    def _api_result(self, words: list[dict]) -> dict:
        return {
            "results": {
                "channels": [{"alternatives": [{"words": words}]}]
            }
        }

    def test_extracts_and_formats_speakers(self):
        words = _words(("Hello", 0.0, 0.5, 0), ("Hi", 0.6, 1.0, 1))
        result = format_diarized_transcript(self._api_result(words))
        assert "[Speaker 0]" in result
        assert "[Speaker 1]" in result

    def test_returns_empty_on_no_channels(self):
        assert format_diarized_transcript({}) == ""

    def test_returns_empty_on_empty_words(self):
        assert format_diarized_transcript(self._api_result([])) == ""

    def test_returns_empty_on_malformed_result(self):
        assert format_diarized_transcript({"results": "bad"}) == ""


# ── extract_plain_transcript ──────────────────────────────────────────────────


class TestExtractPlainTranscript:
    def test_extracts_from_channel_alternatives(self):
        result = {
            "results": {
                "channels": [{"alternatives": [{"transcript": "Hello world", "words": []}]}]
            }
        }
        assert extract_plain_transcript(result) == "Hello world"

    def test_prefers_paragraphs_transcript(self):
        result = {
            "results": {
                "channels": [
                    {
                        "alternatives": [
                            {
                                "transcript": "flat",
                                "words": [],
                                "paragraphs": {"transcript": "Better formatted text."},
                            }
                        ]
                    }
                ]
            }
        }
        assert extract_plain_transcript(result) == "Better formatted text."

    def test_falls_back_to_top_level_transcript(self):
        assert extract_plain_transcript({"transcript": "fallback"}) == "fallback"

    def test_returns_empty_string_on_empty_result(self):
        assert extract_plain_transcript({}) == ""

    def test_returns_empty_on_missing_alternatives(self):
        result = {"results": {"channels": [{"alternatives": []}]}}
        assert extract_plain_transcript(result) == ""


# ── extract_summary ───────────────────────────────────────────────────────────


class TestExtractSummary:
    def test_extracts_short_field(self):
        result = {"results": {"summary": {"short": "Short summary."}}}
        assert extract_summary(result) == "Short summary."

    def test_falls_back_to_text_field(self):
        result = {"results": {"summary": {"text": "Text summary."}}}
        assert extract_summary(result) == "Text summary."

    def test_short_takes_priority_over_text(self):
        result = {"results": {"summary": {"short": "short", "text": "text"}}}
        assert extract_summary(result) == "short"

    def test_returns_empty_if_no_summary(self):
        assert extract_summary({"results": {}}) == ""

    def test_returns_empty_on_empty_result(self):
        assert extract_summary({}) == ""


# ── extract_topics ────────────────────────────────────────────────────────────


class TestExtractTopics:
    def test_extracts_topics_with_confidence(self):
        result = {
            "results": {
                "topics": {
                    "segments": [
                        {"topics": [{"topic": "AI", "confidence_score": 0.98}]},
                        {"topics": [{"topic": "ML", "confidence_score": 0.85}]},
                    ]
                }
            }
        }
        topics = extract_topics(result)
        assert len(topics) == 2
        assert any("AI" in t for t in topics)
        assert any("98%" in t for t in topics)

    def test_deduplicates_repeated_topics(self):
        result = {
            "results": {
                "topics": {
                    "segments": [
                        {"topics": [{"topic": "AI", "confidence_score": 0.9}]},
                        {"topics": [{"topic": "AI", "confidence_score": 0.8}]},
                    ]
                }
            }
        }
        assert len(extract_topics(result)) == 1

    def test_returns_empty_list_on_no_topics(self):
        assert extract_topics({}) == []
        assert extract_topics({"results": {}}) == []

    def test_returns_empty_on_malformed_input(self):
        assert extract_topics({"results": {"topics": "bad"}}) == []
