"""Live end-to-end tests for Flux TTS / STT and Aura multilingual.

Each test calls a command's ``handle()`` in-process against the real Deepgram
API, exercising the full transport (SDK WebSocket for Flux TTS, raw WebSocket
for Flux/nova STT, REST for Aura) plus the CLI's own parsing and assembly.

Live execution requires ``DEEPGRAM_API_KEY``, ``RUN_LIVE_E2E=1``, and an
explicit target. Set ``DEEPGRAM_BASE_URL`` for staging/custom testing. To use
the default production endpoint, set ``RUN_LIVE_E2E_PRODUCTION=1`` as a second
confirmation. See conftest for the complete gate. ASR wording is
non-deterministic, so assertions stay loose: transcripts must be non-empty and
show the specific transformation under test (digits for numerals, ``*`` for
number redaction). Each live case has a two-minute deadline so a stalled
transport cannot occupy the runner indefinitely.
"""

from __future__ import annotations

import pytest
from deepctl_cmd_listen.command import ListenCommand
from deepctl_cmd_speak.command import SpeakCommand
from deepctl_cmd_speak.models import SpeakResult

from .conftest import requires_live_e2e

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_auth,
    pytest.mark.requires_network,
    pytest.mark.slow,
    pytest.mark.timeout(120),
    requires_live_e2e,
]

# Flux TTS emits 24 kHz linear16; feed STT the same rate so no resampling is
# needed and the tests carry no ffmpeg dependency.
SAMPLE_RATE = 24000
NUMBERS_PHRASE = "My account number is four five six seven."


def _synth_pcm(client, text: str) -> bytes:
    """Synthesize raw linear16 PCM via Flux TTS (used as STT input)."""
    pcm = bytearray()
    for chunk in client.speak_text_stream(
        text=text,
        model="flux-alexis-en",
        encoding="linear16",
        sample_rate=float(SAMPLE_RATE),
    ):
        pcm.extend(chunk)
    return bytes(pcm)


# ── Speak (Flux TTS + Aura) ────────────────────────────────────────────────


def test_speak_flux_speed_and_expressivity(live_client, tmp_path):
    """dg speak with Flux + --speed/--expressivity writes a valid WAV."""
    config, auth, client = live_client
    out = tmp_path / "flux.wav"

    result = SpeakCommand().handle(
        config=config,
        auth_manager=auth,
        client=client,
        text="Testing Flux speed and expressivity end to end.",
        model="flux-alexis-en",
        speed=0.9,
        expressivity=2,
        output=str(out),
    )

    assert isinstance(result, SpeakResult)
    assert result.status == "success"
    assert result.bytes_written > 1000
    data = out.read_bytes()
    assert data[:4] == b"RIFF"
    assert data[8:12] == b"WAVE"


def test_speak_aura_spanish_voice(live_client, tmp_path):
    """Aura-2 Spanish voice (7.6.0) round-trips over the REST path to MP3."""
    config, auth, client = live_client
    out = tmp_path / "hola.mp3"

    result = SpeakCommand().handle(
        config=config,
        auth_manager=auth,
        client=client,
        text="Hola, bienvenido a Deepgram.",
        model="aura-2-selena-es",
        output=str(out),
    )

    assert result.status == "success"
    data = out.read_bytes()
    assert len(data) > 1000
    # MP3: ID3 tag or an MPEG audio frame sync (0xFF Ex/Fx).
    assert data[:3] == b"ID3" or (data[0] == 0xFF and data[1] & 0xE0 == 0xE0)


# ── Listen (Flux STT v2 + nova v1) ─────────────────────────────────────────


def test_listen_flux_numerals(live_client, feed_stdin):
    """Flux STT (v2) streaming with --numerals spells numbers as digits."""
    config, auth, client = live_client
    feed_stdin(_synth_pcm(client, NUMBERS_PHRASE))

    result = ListenCommand().handle(
        config=config,
        auth_manager=auth,
        client=client,
        source="-",
        model="flux-general-en",
        encoding="linear16",
        sample_rate=SAMPLE_RATE,
        numerals=True,
    )

    assert result.status == "success"
    assert result.transcript.strip()
    assert any(ch.isdigit() for ch in result.transcript), result.transcript


def test_listen_flux_numerals_and_redact(live_client, feed_stdin):
    """--redact numbers replaces the digits (Deepgram uses ``*``)."""
    config, auth, client = live_client
    feed_stdin(_synth_pcm(client, NUMBERS_PHRASE))

    result = ListenCommand().handle(
        config=config,
        auth_manager=auth,
        client=client,
        source="-",
        model="flux-general-en",
        encoding="linear16",
        sample_rate=SAMPLE_RATE,
        numerals=True,
        redact="numbers",
    )

    assert result.status == "success"
    assert result.transcript.strip()
    assert "*" in result.transcript, result.transcript


def test_listen_nova3_v1_baseline(live_client, feed_stdin):
    """nova-3 (v1) streaming still works — guards against a v2-fix regression."""
    config, auth, client = live_client
    feed_stdin(_synth_pcm(client, NUMBERS_PHRASE))

    result = ListenCommand().handle(
        config=config,
        auth_manager=auth,
        client=client,
        source="-",
        model="nova-3",
        encoding="linear16",
        sample_rate=SAMPLE_RATE,
    )

    assert result.status == "success"
    assert result.transcript.strip()


def test_listen_flux_srt_captions_have_real_timestamps(live_client, feed_stdin, capsys):
    """Flux STT (v2) --srt emits well-formed cues with non-zero timestamps.

    Flux ``TurnInfo`` words carry no per-word timings, so captions must key off
    the turn's ``audio_window_*``; a regression would print ``00:00:00,000`` for
    every cue (or crash the end-of-stream save with ``KeyError: 'start'``).
    """
    import re

    config, auth, client = live_client
    feed_stdin(_synth_pcm(client, NUMBERS_PHRASE))

    result = ListenCommand().handle(
        config=config,
        auth_manager=auth,
        client=client,
        source="-",
        model="flux-general-en",
        encoding="linear16",
        sample_rate=SAMPLE_RATE,
        srt=True,
    )

    assert result.status == "success"
    out = capsys.readouterr().out
    stamps = re.findall(r"\d\d:\d\d:\d\d,\d\d\d", out)
    assert " --> " in out, out
    assert stamps, out
    # The audio window is real, so at least one boundary must be non-zero.
    assert any(s != "00:00:00,000" for s in stamps), out
