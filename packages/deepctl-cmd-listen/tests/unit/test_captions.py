"""Tests for the captions module — timestamp formatters, StreamingCaptionWriter, and batch helpers."""

from __future__ import annotations

import pytest
from deepctl_cmd_listen.captions import (
    StreamingCaptionWriter,
    _fmt_srt,
    _fmt_webvtt,
    captions_from_words,
)


def _words(*entries: tuple[str, float, float]) -> list[dict]:
    """Build word dicts from (text, start, end) tuples."""
    return [
        {"word": t, "punctuated_word": t, "start": s, "end": e}
        for t, s, e in entries
    ]


def _words_with_speaker(*entries: tuple[str, float, float, int]) -> list[dict]:
    """Build word dicts with speaker tags."""
    return [
        {"word": t, "punctuated_word": t, "start": s, "end": e, "speaker": sp}
        for t, s, e, sp in entries
    ]


# ── Timestamp formatters ──────────────────────────────────────────────────────


class TestFmtWebvtt:
    def test_zero(self):
        assert _fmt_webvtt(0.0) == "00:00:00.000"

    def test_subsecond(self):
        assert _fmt_webvtt(0.08) == "00:00:00.080"

    def test_seconds(self):
        assert _fmt_webvtt(1.5) == "00:00:01.500"

    def test_minutes(self):
        assert _fmt_webvtt(65.4) == "00:01:05.400"

    def test_hours(self):
        assert _fmt_webvtt(3661.0) == "01:01:01.000"

    def test_uses_dot_separator(self):
        ts = _fmt_webvtt(1.234)
        assert "." in ts
        assert "," not in ts

    def test_negative_clamped_to_zero(self):
        assert _fmt_webvtt(-5.0) == "00:00:00.000"

    def test_six_digit_format(self):
        ts = _fmt_webvtt(1.0)
        # HH:MM:SS.mmm
        parts = ts.split(":")
        assert len(parts) == 3
        assert "." in parts[2]


class TestFmtSrt:
    def test_zero(self):
        assert _fmt_srt(0.0) == "00:00:00,000"

    def test_subsecond(self):
        assert _fmt_srt(0.08) == "00:00:00,080"

    def test_seconds(self):
        assert _fmt_srt(1.5) == "00:00:01,500"

    def test_minutes(self):
        assert _fmt_srt(65.4) == "00:01:05,400"

    def test_uses_comma_separator(self):
        ts = _fmt_srt(1.234)
        assert "," in ts
        assert "." not in ts

    def test_negative_clamped_to_zero(self):
        assert _fmt_srt(-5.0) == "00:00:00,000"


# ── StreamingCaptionWriter — WebVTT ──────────────────────────────────────────


class TestStreamingCaptionWriterWebVTT:
    def test_header_outputs_webvtt(self, capsys):
        writer = StreamingCaptionWriter("webvtt")
        writer.print_header()
        assert "WEBVTT" in capsys.readouterr().out

    def test_entry_outputs_timestamp(self, capsys):
        writer = StreamingCaptionWriter("webvtt")
        writer.write_entry(_words(("Hello", 0.08, 0.5)), 0.08, 0.5)
        out = capsys.readouterr().out
        assert "00:00:00.080 --> 00:00:00.500" in out

    def test_entry_outputs_text(self, capsys):
        writer = StreamingCaptionWriter("webvtt")
        writer.write_entry(_words(("Hello", 0.0, 0.5)), 0.0, 0.5)
        assert "Hello" in capsys.readouterr().out

    def test_speaker_label_voice_tag(self, capsys):
        writer = StreamingCaptionWriter("webvtt")
        writer.write_entry(_words_with_speaker(("Hi", 0.0, 0.5, 1)), 0.0, 0.5)
        assert "<v Speaker 1>" in capsys.readouterr().out

    def test_no_voice_tag_without_speaker(self, capsys):
        writer = StreamingCaptionWriter("webvtt")
        writer.write_entry(_words(("Hi", 0.0, 0.5)), 0.0, 0.5)
        assert "<v" not in capsys.readouterr().out

    def test_blank_line_after_entry(self, capsys):
        writer = StreamingCaptionWriter("webvtt")
        writer.write_entry(_words(("Hi", 0.0, 0.5)), 0.0, 0.5)
        out = capsys.readouterr().out
        assert out.endswith("\n\n")

    def test_empty_words_produces_no_output(self, capsys):
        writer = StreamingCaptionWriter("webvtt")
        writer.write_entry([], 0.0, 1.0)
        assert capsys.readouterr().out == ""

    def test_accumulates_words(self):
        writer = StreamingCaptionWriter("webvtt")
        words = _words(("hello", 0.0, 0.5), ("world", 0.6, 1.0))
        writer.write_entry(words, 0.0, 1.0)
        assert writer.accumulated_words == words

    def test_accumulates_across_entries(self, capsys):
        writer = StreamingCaptionWriter("webvtt")
        w1 = _words(("one", 0.0, 0.5))
        w2 = _words(("two", 1.0, 1.5))
        writer.write_entry(w1, 0.0, 0.5)
        writer.write_entry(w2, 1.0, 1.5)
        capsys.readouterr()  # discard output
        assert len(writer.accumulated_words) == 2


# ── StreamingCaptionWriter — SRT ─────────────────────────────────────────────


class TestStreamingCaptionWriterSRT:
    def test_no_header_output(self, capsys):
        writer = StreamingCaptionWriter("srt")
        writer.print_header()
        assert capsys.readouterr().out == ""

    def test_entry_starts_with_sequence_number(self, capsys):
        writer = StreamingCaptionWriter("srt")
        writer.write_entry(_words(("Hi", 0.0, 0.5)), 0.0, 0.5)
        out = capsys.readouterr().out
        assert out.startswith("1\n")

    def test_sequential_numbering(self, capsys):
        writer = StreamingCaptionWriter("srt")
        words = _words(("A", 0.0, 0.5))
        writer.write_entry(words, 0.0, 0.5)
        writer.write_entry(words, 1.0, 1.5)
        out = capsys.readouterr().out
        assert "1\n" in out
        assert "2\n" in out

    def test_timestamp_uses_comma(self, capsys):
        writer = StreamingCaptionWriter("srt")
        writer.write_entry(_words(("Hi", 0.08, 0.5)), 0.08, 0.5)
        out = capsys.readouterr().out
        assert "00:00:00,080 --> 00:00:00,500" in out

    def test_speaker_bracket_label(self, capsys):
        writer = StreamingCaptionWriter("srt")
        writer.write_entry(_words_with_speaker(("Hi", 0.0, 0.5, 0)), 0.0, 0.5)
        assert "[Speaker 0]" in capsys.readouterr().out

    def test_no_speaker_label_without_speaker(self, capsys):
        writer = StreamingCaptionWriter("srt")
        writer.write_entry(_words(("Hi", 0.0, 0.5)), 0.0, 0.5)
        assert "[Speaker" not in capsys.readouterr().out

    def test_blank_line_after_entry(self, capsys):
        writer = StreamingCaptionWriter("srt")
        writer.write_entry(_words(("Hi", 0.0, 0.5)), 0.0, 0.5)
        assert capsys.readouterr().out.endswith("\n\n")


# ── captions_from_words (batch / end-of-stream) ───────────────────────────────


class TestCaptionsFromWords:
    def test_empty_words_returns_webvtt_header(self):
        result = captions_from_words([], "webvtt")
        assert "WEBVTT" in result

    def test_empty_words_returns_empty_srt(self):
        result = captions_from_words([], "srt")
        assert result == ""

    def test_webvtt_output_contains_webvtt_header(self):
        words = [
            {"word": "hello", "punctuated_word": "Hello", "start": 0.08, "end": 0.5},
            {"word": "world", "punctuated_word": "world.", "start": 0.6, "end": 1.0},
        ]
        result = captions_from_words(words, "webvtt")
        assert result.startswith("WEBVTT")

    def test_webvtt_output_contains_timestamp(self):
        words = [{"word": "hi", "punctuated_word": "Hi", "start": 0.08, "end": 0.5}]
        result = captions_from_words(words, "webvtt")
        assert "-->" in result

    def test_srt_output_contains_sequence_number(self):
        words = [{"word": "hi", "punctuated_word": "Hi", "start": 0.08, "end": 0.5}]
        result = captions_from_words(words, "srt")
        assert result.strip().startswith("1")

    def test_srt_timestamp_uses_comma(self):
        words = [{"word": "hi", "punctuated_word": "Hi", "start": 0.08, "end": 0.5}]
        result = captions_from_words(words, "srt")
        assert "00:00:00,080" in result
