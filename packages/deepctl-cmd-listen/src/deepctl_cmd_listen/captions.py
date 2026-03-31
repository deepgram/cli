"""Caption generation for the listen command.

Strategy:
  - Pre-recorded responses: pass full API result directly to deepgram-captions
    library (DeepgramConverter), which handles utterances, chunking, and speaker
    labels according to the WebVTT/SRT spec.

  - Live streaming (real-time): StreamingCaptionWriter outputs one caption
    block per final utterance as it arrives, using word-level timestamps from
    the WebSocket Results message. Suitable for piping to a live player or
    progressive file writes.

  - Live streaming (end-of-stream batch): captions_from_words() accumulates
    all word objects from the stream, builds a synthetic prerecorded response
    dict, and passes it through the library — so chunking and formatting is
    identical to prerecorded output.
"""

from __future__ import annotations

from typing import Any

from deepgram_captions import DeepgramConverter
from deepgram_captions import srt as _srt
from deepgram_captions import webvtt as _webvtt

# ── Pre-recorded ───────────────────────────────────────────────────────────────


def captions_from_prerecorded(api_result: dict[str, Any], fmt: str) -> str:
    """Generate SRT or WebVTT from a complete Deepgram prerecorded API response.

    Args:
        api_result: Raw dict from the Deepgram REST API (the full JSON response).
        fmt: "webvtt" or "srt".

    Returns:
        Formatted caption string.
    """
    converter = DeepgramConverter(api_result, use_exception=False)
    return str(_webvtt(converter) if fmt == "webvtt" else _srt(converter))


# ── Live streaming — batch (end-of-stream) ─────────────────────────────────────


def captions_from_words(words: list[dict[str, Any]], fmt: str) -> str:
    """Generate SRT or WebVTT from accumulated live-stream word objects.

    Builds a synthetic prerecorded response so the library's chunking and
    formatting logic is applied identically to both prerecorded and live output.

    Args:
        words: List of word dicts collected from streaming Results messages.
               Each must have at minimum: word, start (float), end (float).
               speaker (int) is used for diarization if present.
        fmt:   "webvtt" or "srt".

    Returns:
        Formatted caption string, or a bare header if no words.
    """
    if not words:
        return "WEBVTT\n" if fmt == "webvtt" else ""

    transcript = " ".join(w.get("punctuated_word") or w.get("word", "") for w in words)
    synthetic: dict[str, Any] = {
        "results": {
            "channels": [
                {
                    "alternatives": [
                        {
                            "transcript": transcript,
                            "words": words,
                        }
                    ]
                }
            ]
        }
    }
    return captions_from_prerecorded(synthetic, fmt)


# ── Live streaming — real-time ─────────────────────────────────────────────────


class StreamingCaptionWriter:
    """Writes one caption block per final streaming utterance to stdout.

    Designed for live display or real-time pipe targets. Each call to
    write_entry() immediately prints a complete, properly-formatted caption
    block. The accumulated words are available at the end for batch output
    (e.g. saving a clean .vtt/.srt file via captions_from_words()).

    Supports WebVTT <v Speaker N> voice tags and SRT [Speaker N] labels when
    word-level speaker data is present.
    """

    def __init__(self, fmt: str) -> None:
        self.fmt = fmt  # "webvtt" | "srt"
        self._count = 0
        self._all_words: list[dict[str, Any]] = []

    def print_header(self, note: str | None = None) -> None:
        """Print the format header. Call once before any entries.

        For WebVTT, an optional NOTE block can carry metadata (model, language,
        etc.) in a spec-compliant way. SRT has no header equivalent.
        """
        if self.fmt == "webvtt":
            print("WEBVTT")
            print()
            if note:
                print("NOTE")
                print(note)
                print()

    def write_entry(
        self,
        words: list[dict[str, Any]],
        start: float,
        end: float,
    ) -> None:
        """Print one caption block immediately.

        Args:
            words: Word objects for this utterance (need 'word'/'punctuated_word').
            start: Caption start time in seconds.
            end:   Caption end time in seconds.
        """
        if not words:
            return

        self._count += 1
        self._all_words.extend(words)

        text = " ".join(w.get("punctuated_word") or w.get("word", "") for w in words)
        first_speaker: int | None = words[0].get("speaker") if words else None

        if self.fmt == "webvtt":
            print(f"{_fmt_webvtt(start)} --> {_fmt_webvtt(end)}")
            prefix = f"<v Speaker {first_speaker}>" if first_speaker is not None else ""
            print(f"{prefix}{text}")
            print()
        else:
            print(self._count)
            print(f"{_fmt_srt(start)} --> {_fmt_srt(end)}")
            if first_speaker is not None:
                print(f"[Speaker {first_speaker}]")
            print(text)
            print()

    @property
    def accumulated_words(self) -> list[dict[str, Any]]:
        """All words received so far — use with captions_from_words() for batch output."""
        return self._all_words


# ── Timestamp helpers ──────────────────────────────────────────────────────────


def _fmt_webvtt(seconds: float) -> str:
    """Format seconds as a WebVTT timestamp: HH:MM:SS.mmm"""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def _fmt_srt(seconds: float) -> str:
    """Format seconds as an SRT timestamp: HH:MM:SS,mmm"""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = round((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
