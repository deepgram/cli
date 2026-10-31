"""ListenCommand's live path: stream failures, SIGTERM, host checks."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from deepctl_cmd_listen import live
from deepctl_cmd_listen import messages as msg
from deepctl_cmd_listen.command import ListenCommand
from deepctl_cmd_listen.models import ListenCodedError, ListenResult

from .test_listen_command import _EndOnCloseStream, _install_websockets, _stdin_pipe


@pytest.fixture
def command():
    return ListenCommand()


def _client():
    client = MagicMock()
    client.config.get_profile.return_value.base_url = "https://api.deepgram.com"
    client.auth_manager.get_api_key.return_value = "k"
    return client


def _results(*texts):
    return [
        json.dumps(
            {
                "type": "Results",
                "is_final": True,
                "start": 0,
                "duration": 1,
                "channel": {"alternatives": [{"transcript": t}]},
            }
        )
        for t in texts
    ]


def _stream(command, **over):
    kw = {
        "model": "nova-3",
        "language": "en-US",
        "api_version": 1,
        "diarize": False,
        "smart_format": True,
        "punctuate": True,
        "interim": True,
        "redact": (),
        "numerals": False,
        "encoding": "linear16",
        "sample_rate": 16000,
        "channels": 1,
        "save_to": None,
        "caption_format": None,
    }
    kw.update(over)
    return command._stream_stdin(_client(), **kw)


# ── Drain timeout and server errors ────────────────────────────────────────


class _Silent(_EndOnCloseStream):
    """Never answers CloseStream."""

    async def send(self, data):
        self.sent.append(data)


def test_drain_timeout_is_coded_and_keeps_save_to(
    command, monkeypatch, capsys, tmp_path
):
    monkeypatch.setattr(live, "EOF_IDLE_S", 0.2)

    class _OneThenSilent(_Silent):
        def __init__(self):
            super().__init__()
            self.frames = _results("kept")

        async def __anext__(self):
            if self.frames:
                return self.frames.pop(0)
            return await super().__anext__()

    _stdin_pipe(monkeypatch, b"\0" * 3200)
    _install_websockets(monkeypatch, _OneThenSilent())
    save = tmp_path / "t.txt"
    result = _stream(command, save_to=str(save))
    assert isinstance(result, ListenCodedError)
    assert result.code == "stream_drain_timeout"
    assert result.next_step is None
    assert result.details == {
        "reason": "idle",
        "limit_s": 0.2,
        "transcript": "kept",
        "transcript_chars": 4,
        "saved_to": str(save),
    }
    assert list(result.model_dump()) == [
        "error",
        "status",
        "code",
        "hint",
        "next_step",
        "schema_version",
        "details",
    ]
    assert command.exit_code_for(result) == 1
    assert save.read_text() == "kept"
    err = capsys.readouterr().err
    assert "after the audio ended, so results may be incomplete" in err
    assert msg.H_DRAIN_TIMEOUT in err


def test_server_error_result_exits_one(command, monkeypatch, capsys):
    import asyncio

    class _Boom(_EndOnCloseStream):
        async def __anext__(self):
            await asyncio.sleep(0.01)
            raise RuntimeError("socket exploded")

    _stdin_pipe(monkeypatch, b"", close=False)
    _install_websockets(monkeypatch, _Boom())
    result = _stream(command)
    assert isinstance(result, ListenCodedError)
    assert result.code == "network_unavailable"
    assert result.details["transcript"] == ""
    assert result.details["reason"] == "connection lost"
    assert command.exit_code_for(result) == 1
    assert "socket exploded" in capsys.readouterr().err


def test_untrusted_host_never_connects(command, monkeypatch):
    """The WebSocket URL comes only from the checked base URL; a refused
    host raises before any connection is attempted."""
    from deepctl_core.hosts import CredentialHostError

    connect = MagicMock()
    import sys
    import types

    fake = types.ModuleType("websockets")
    fake.connect = connect
    monkeypatch.setitem(sys.modules, "websockets", fake)
    client = _client()
    client.auth_manager.check_base_url.side_effect = CredentialHostError(
        "E-FH-FLAG", "foreign_host", "no", "hint"
    )
    _stdin_pipe(monkeypatch, b"")
    with pytest.raises(CredentialHostError):
        command._stream_stdin(
            client,
            model="nova-3",
            language="en-US",
            api_version=1,
            diarize=False,
            smart_format=True,
            punctuate=True,
            interim=False,
            redact=(),
            numerals=False,
            encoding="linear16",
            sample_rate=16000,
            channels=1,
            save_to=None,
            caption_format=None,
        )
    connect.assert_not_called()


# ── A server error close keeps the transcript in details ────────────────────


def test_server_error_close_is_coded_with_transcript(command, monkeypatch, capsys):
    from websockets.exceptions import ConnectionClosedError
    from websockets.frames import Close

    class _CloseAfter(_EndOnCloseStream):
        def __init__(self):
            super().__init__()
            self.frames = _results("one", "two")

        async def __anext__(self):
            if self.frames:
                return self.frames.pop(0)
            raise ConnectionClosedError(Close(1011, "boom Token k"), None)

    _stdin_pipe(monkeypatch, b"", close=False)
    _install_websockets(monkeypatch, _CloseAfter())
    result = _stream(command)
    assert result.code == "server_error"
    assert result.details["close_code"] == 1011
    assert result.details["transcript"] == "one two"
    assert result.details["transcript_chars"] == 7
    assert "saved_to" not in result.details
    err = capsys.readouterr().err
    assert "(1011)" in err and "Token k" not in err


# ── A SIGTERM drain ends the process by SIGTERM ────────────────────────────


def test_exit_code_for_reraises_sigterm_after_drain(command, monkeypatch):
    import signal

    died = []
    monkeypatch.setattr(live, "die_by_signal", lambda s: died.append(s))
    command._reraise_signal = signal.SIGTERM
    r = ListenResult(status="cancelled", source="stdin", mode="live")
    command.exit_code_for(r)
    assert died == [signal.SIGTERM]
    assert command._reraise_signal is None


# ── Round 2: stdin that can't be read is invalid_argument ──────────────────


def test_unreadable_stdin_is_invalid_argument(command, monkeypatch, capsys):
    import os

    r, w = os.pipe()
    os.close(w)
    os.close(r)  # reading a closed descriptor: EBADF
    monkeypatch.setattr("deepctl_cmd_listen.command._stdin_fd", lambda: r)
    _install_websockets(monkeypatch, _EndOnCloseStream())
    result = _stream(command)
    assert isinstance(result, ListenCodedError)
    assert result.code == "invalid_argument"
    assert result.error.startswith("Couldn't read audio from stdin: ")
    assert result.error.endswith(".")
    assert result.next_step.endswith(" listen --help")
    assert result.hint in (msg.H_LISTEN_STDIN_UNREADABLE, msg.A_LISTEN_STDIN_UNREADABLE)
    assert result.details == {"transcript": "", "transcript_chars": 0}
    assert command.exit_code_for(result) == 1
    assert "Couldn't read audio from stdin" in capsys.readouterr().err
