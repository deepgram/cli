"""ListenCommand's live surface: no-source refusal, machine output, drain timeout."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, Mock, patch

import pytest
from click.testing import CliRunner
from deepctl_cmd_listen import live
from deepctl_cmd_listen import messages as msg
from deepctl_cmd_listen.captions import StreamingCaptionWriter
from deepctl_cmd_listen.command import ListenCommand
from deepctl_cmd_listen.models import ListenCodedError, ListenResult
from deepctl_core import Config, PluginManager

from .test_listen_command import _EndOnCloseStream, _install_websockets, _stdin_pipe


@pytest.fixture
def command():
    return ListenCommand()


@pytest.fixture
def no_agent(monkeypatch):
    for name in (
        "AI_AGENT",
        "CLAUDECODE",
        "CLAUDE_CODE_ENTRYPOINT",
        "CODEX_SANDBOX",
        "CODEX_SANDBOX_NETWORK_DISABLED",
        "CURSOR_AGENT",
        "GEMINI_CLI",
        "OR_APP_NAME",
        "OR_SITE_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def _client():
    client = MagicMock()
    client.config.get_profile.return_value.base_url = "https://api.deepgram.com"
    client.auth_manager.get_api_key.return_value = "k"
    return client


def _invoke(command, args, *, env=None):
    config = Mock(spec=Config)
    config.get.side_effect = lambda key, default=None: (
        True if key == "output.quiet" else default
    )
    click_command = PluginManager()._create_click_command(command)
    with (
        patch("deepctl_core.base_command.AuthManager.guard") as guard,
        patch(
            "deepctl_core.base_command.AuthManager.check_base_url",
            return_value="https://api.deepgram.com",
        ),
    ):
        result = CliRunner().invoke(
            click_command, args, obj={"config": config}, env=env, catch_exceptions=False
        )
    return result, guard


# ── No source under a hard agent signal ────────────────────────────────────


def test_agent_without_source_is_refused_before_auth(command, monkeypatch, no_agent):
    monkeypatch.setenv("AI_AGENT", "x")
    connect = MagicMock()
    _install_websockets(monkeypatch, connect)
    result, guard = _invoke(command, [])
    assert result.exit_code == 1
    guard.assert_not_called()  # nothing authenticated, nothing sent
    assert (
        "listen needs a file, a URL, --mic, or - to read audio from stdin."
        in result.stderr
    )
    assert msg.H_LISTEN_NO_SOURCE in result.stderr


def test_agent_no_source_payload(command, monkeypatch, no_agent):
    monkeypatch.setenv("AI_AGENT", "x")
    early = command.pre_auth_check(Mock(spec=Config), source=None, mic=False)
    assert isinstance(early, ListenCodedError)
    data = early.model_dump()
    assert list(data) == [
        "error",
        "status",
        "code",
        "hint",
        "next_step",
        "schema_version",
    ]
    assert data["status"] == "error"
    assert data["code"] == "invalid_argument"
    assert data["next_step"].endswith(" listen --help")
    assert data["schema_version"] == 1
    assert command.exit_code_for(early) == 1


@pytest.mark.parametrize(
    "kwargs", [{"source": "-"}, {"source": "a.wav"}, {"mic": True}]
)
def test_agent_with_a_source_is_not_refused(command, monkeypatch, no_agent, kwargs):
    monkeypatch.setenv("AI_AGENT", "x")
    assert command.pre_auth_check(Mock(spec=Config), **kwargs) is None


def test_no_agent_keeps_auto_detection(command, no_agent):
    assert command.pre_auth_check(Mock(spec=Config), source=None, mic=False) is None


def test_ci_alone_is_not_a_hard_signal(command, monkeypatch, no_agent):
    monkeypatch.setenv("CI", "true")
    assert command.pre_auth_check(Mock(spec=Config), source=None, mic=False) is None


# ── A machine format keeps transcript lines off stdout ─────────────────────


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


class _Replay(_EndOnCloseStream):
    def __init__(self, frames):
        super().__init__()
        self.frames = list(frames)

    async def __anext__(self):
        if self.frames:
            return self.frames.pop(0)
        return await super().__anext__()


def _fmt(monkeypatch, fmt, source="explicit"):
    monkeypatch.setattr("deepctl_cmd_listen.command.get_output_format", lambda: fmt)
    monkeypatch.setattr("deepctl_core.output.get_output_format", lambda: fmt)
    monkeypatch.setattr("deepctl_cmd_listen.command.format_source", lambda: source)


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


def test_machine_format_prints_only_the_result(command, monkeypatch, capsys):
    _fmt(monkeypatch, "json")
    _stdin_pipe(monkeypatch, b"\0" * 3200)
    _install_websockets(monkeypatch, _Replay(_results("hello", "world")))
    result = _stream(command)
    assert result.status == "success"
    assert capsys.readouterr().out == ""  # nothing streamed
    command.output_result(result, Mock(spec=Config))
    out = capsys.readouterr().out
    payload = json.loads(out)  # exactly one document
    assert payload["transcript"] == "hello world"
    assert payload["status"] == "success"
    assert payload["mode"] == "live"


def test_human_format_streams_lines(command, monkeypatch, capsys):
    _fmt(monkeypatch, "default", "default")
    _stdin_pipe(monkeypatch, b"\0" * 3200)
    _install_websockets(monkeypatch, _Replay(_results("hello", "world")))
    result = _stream(command)
    command.output_result(result, Mock(spec=Config))
    assert capsys.readouterr().out == "hello\nworld\n"


def test_machine_format_result_on_empty_stdin(command, monkeypatch, capsys):
    """/dev/null on stdin: still exactly one Result document."""
    _fmt(monkeypatch, "json")
    _stdin_pipe(monkeypatch, b"")
    _install_websockets(monkeypatch, _Replay([]))
    result = _stream(command)
    command.output_result(result, Mock(spec=Config))
    payload = json.loads(capsys.readouterr().out)
    assert payload["transcript"] == ""


def test_machine_format_captions_not_streamed(command, monkeypatch, capsys, tmp_path):
    _fmt(monkeypatch, "json")
    _stdin_pipe(monkeypatch, b"\0" * 3200)
    frame = json.dumps(
        {
            "type": "Results",
            "is_final": True,
            "start": 0,
            "duration": 1,
            "channel": {
                "alternatives": [
                    {
                        "transcript": "hi",
                        "words": [{"word": "hi", "start": 0.0, "end": 0.5}],
                    }
                ]
            },
        }
    )
    _install_websockets(monkeypatch, _Replay([frame]))
    save = tmp_path / "c.vtt"
    result = _stream(command, caption_format="webvtt", save_to=str(save))
    assert capsys.readouterr().out == ""
    assert "WEBVTT" in save.read_text()
    assert result.caption_format == "webvtt"


def test_caption_writer_echo_false_collects_silently(capsys):
    w = StreamingCaptionWriter("srt", echo=False)
    w.print_header()
    w.write_entry([{"word": "a", "start": 0.0, "end": 1.0}], 0.0, 1.0)
    assert capsys.readouterr().out == ""
    assert w.accumulated_words == [{"word": "a", "start": 0.0, "end": 1.0}]


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


def test_cancelled_live_result_is_payload_under_json(command, monkeypatch, capsys):
    _fmt(monkeypatch, "json")
    r = ListenResult(status="cancelled", source="stdin", mode="live", transcript="x")
    command.output_result(r, Mock(spec=Config))
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "cancelled"
    assert payload["transcript"] == "x"


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


# ── Implicit JSON from soft signals only keeps live lines ──────────────────


@pytest.mark.parametrize(
    ("source", "lines"),
    [("soft", True), ("explicit", False), ("hard_agent", False)],
)
def test_live_lines_follow_format_source(command, monkeypatch, capsys, source, lines):
    _fmt(monkeypatch, "json", source)
    _stdin_pipe(monkeypatch, b"\0" * 3200)
    _install_websockets(monkeypatch, _Replay(_results("hello", "world")))
    result = _stream(command)
    streamed = capsys.readouterr().out
    assert (streamed == "hello\nworld\n") is lines
    command.output_result(result, Mock(spec=Config))
    payload = json.loads(capsys.readouterr().out)  # then one Result document
    assert payload["transcript"] == "hello world"


def test_format_source_values(monkeypatch):
    from deepctl_core import output

    monkeypatch.setitem(output._output_config, "agentic", False)
    output.setup_output("default")
    assert output.format_source() == "default"
    output.setup_output("json")
    assert output.format_source() == "explicit"
    monkeypatch.setitem(output._output_config, "agentic", True)
    monkeypatch.delenv("AI_AGENT", raising=False)
    for name in output.AI_AGENT_ENV_MARKERS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("OR_APP_NAME", raising=False)
    monkeypatch.delenv("OR_SITE_URL", raising=False)
    output.setup_output("default")
    assert output.format_source() == "soft"
    monkeypatch.setenv("AI_AGENT", "x")
    output.setup_output("default")
    assert output.format_source() == "hard_agent"
    assert output.get_output_format() == "json"
    output.setup_output("default")
    monkeypatch.setitem(output._output_config, "agentic", False)
    output.setup_output("default")


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


def test_live_result_into_a_closed_stdout_is_quiet(command, monkeypatch):
    """`dg -o json listen - | true`: the reader is gone before the final
    Result; dg points stdout at /dev/null and carries on."""
    import sys as _sys

    silenced = []
    monkeypatch.setattr(live, "silence_stdout", lambda: silenced.append(True))
    _fmt(monkeypatch, "json")

    def boom(_text):
        raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(_sys.stdout, "write", boom)
    r = ListenResult(status="success", source="stdin", mode="live", transcript="x")
    command.output_result(r, Mock(spec=Config))
    assert silenced == [True]


# ── A caption reader gone before the header: no capture, no upload ─────────


def _header_into_closed_pipe(monkeypatch):
    """`dg listen --webvtt ... | head -n0`: the WebVTT header hits EPIPE.
    Returns the record of what started: sockets, stdin reads, mic opens."""
    import sys as _sys
    import types

    _fmt(monkeypatch, "text", source="default")
    started: dict[str, int] = {"connect": 0, "stdin": 0, "mic": 0, "silenced": 0}

    def header(self, note=None):
        raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(StreamingCaptionWriter, "print_header", header)
    monkeypatch.setattr(
        live, "silence_stdout", lambda: started.__setitem__("silenced", 1)
    )

    def connect(*_args, **_kwargs):
        started["connect"] += 1
        raise AssertionError("the socket must not open")

    monkeypatch.setattr("deepctl_cmd_listen.live._open", connect)

    def stdin_fd():
        started["stdin"] += 1
        raise AssertionError("stdin must not be read")

    monkeypatch.setattr("deepctl_cmd_listen.command._stdin_fd", stdin_fd)

    class _Mic:
        def __init__(self, **_kwargs):
            started["mic"] += 1

        def start(self):
            raise AssertionError("the microphone must not open")

    fake_sd = types.ModuleType("sounddevice")
    fake_sd.RawInputStream = _Mic
    monkeypatch.setitem(_sys.modules, "sounddevice", fake_sd)
    return started


@pytest.mark.timeout(30)
def test_closed_caption_pipe_never_reads_stdin(command, monkeypatch):
    started = _header_into_closed_pipe(monkeypatch)
    result = _stream(command, caption_format="webvtt")
    assert isinstance(result, ListenResult)
    assert result.status == "success"
    assert result.transcript == ""
    assert started == {"connect": 0, "stdin": 0, "mic": 0, "silenced": 1}


@pytest.mark.timeout(30)
def test_closed_caption_pipe_never_opens_the_microphone(command, monkeypatch):
    started = _header_into_closed_pipe(monkeypatch)
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
        "sample_rate": 16000,
        "channels": 1,
        "save_to": None,
        "caption_format": "webvtt",
    }
    result = command._stream_mic(_client(), **kw)
    assert isinstance(result, ListenResult)
    assert result.status == "success"
    assert started == {"connect": 0, "stdin": 0, "mic": 0, "silenced": 1}
