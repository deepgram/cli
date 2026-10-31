"""How a live stream's stop maps to its result and exit code, and the
listen command's lazy imports."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest
from deepctl_cmd_listen import live
from deepctl_cmd_listen.command import ListenCommand

from .test_listen_command import _EndOnCloseStream, _install_websockets


@pytest.fixture
def command():
    return ListenCommand()


def _client():
    client = MagicMock()
    client.config.get_profile.return_value.base_url = "https://api.deepgram.com"
    client.auth_manager.get_api_key.return_value = "k"
    return client


# ── Mic Ctrl-C ends the session (exit 0); other stops stay cancelled ───────


def _finish(command, source, signum):
    import signal

    outcome = live.LiveOutcome(status="cancelled", signal=getattr(signal, signum))
    return command._finish_live(
        outcome,
        source=source,
        model="nova-3",
        language="en",
        diarize=False,
        transcript_acc=["hello", "world"],
        caption_writer=None,
        caption_format=None,
        save_to=None,
        v2_state=None,
    )


@pytest.mark.parametrize(
    ("source", "signum", "status", "code"),
    [("mic", "SIGINT", "success", 0), ("stdin", "SIGINT", "cancelled", 2)],
)
def test_live_sigint_outcome_by_source(command, source, signum, status, code):
    result = _finish(command, source, signum)
    assert result.status == status
    assert result.transcript == "hello world"
    assert command.exit_code_for(result) == code


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM drain is POSIX only")
@pytest.mark.parametrize("source", ["mic", "stdin"])
def test_live_sigterm_is_cancelled_for_every_source(command, monkeypatch, source):
    import signal

    died = []
    monkeypatch.setattr(live, "die_by_signal", lambda s: died.append(s))
    result = _finish(command, source, "SIGTERM")
    assert result.status == "cancelled"
    assert result.transcript == "hello world"
    command.exit_code_for(result)
    assert died == [signal.SIGTERM]


def test_mic_sigterm_drains_and_stays_cancelled(command, monkeypatch):
    """A real SIGTERM during a mic stream drains like Ctrl-C, but the result
    stays "cancelled" and the process then ends by SIGTERM."""
    import asyncio
    import signal
    import types

    if sys.platform == "win32":
        pytest.skip("SIGTERM drain is POSIX only")

    class _FakeInputStream:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    fake_sd = types.ModuleType("sounddevice")
    fake_sd.RawInputStream = _FakeInputStream
    fake_np = types.ModuleType("numpy")
    fake_np.int16 = "int16"
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    monkeypatch.setitem(sys.modules, "numpy", fake_np)
    ws = _EndOnCloseStream()
    _install_websockets(monkeypatch, ws)
    died = []
    monkeypatch.setattr(live, "die_by_signal", lambda s: died.append(s))

    loop = asyncio.new_event_loop()
    loop.call_later(0.1, signal.raise_signal, signal.SIGTERM)
    try:
        result = loop.run_until_complete(
            command._ws_mic(
                _client(),
                model="nova-3",
                language="en",
                api_version=1,
                diarize=False,
                smart_format=True,
                punctuate=True,
                interim=False,
                redact=(),
                numerals=False,
                sample_rate=16000,
                channels=1,
            )
        )
    finally:
        loop.close()

    assert result.status == "cancelled"
    assert '{"type": "CloseStream"}' in ws.sent
    command.exit_code_for(result)
    assert died == [signal.SIGTERM]


# ── Importing the listen command doesn't import asyncio ────────────────────


def test_listen_command_import_is_lazy():
    import subprocess

    code = (
        "import sys\n"
        "from deepctl_cmd_listen.command import ListenCommand\n"
        "ListenCommand().get_arguments()\n"
        "bad = [m for m in ('asyncio', 'deepctl_cmd_listen.live') if m in sys.modules]\n"
        "print(','.join(bad))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == ""
