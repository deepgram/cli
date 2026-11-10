"""Streaming audio to stdout and players as it arrives.

Also the stdout failure modes: EPIPE ends quietly, any other write error
is ``output_unwritable`` with fd 1 silenced.
"""

from __future__ import annotations

import errno
import os
import sys
from contextlib import contextmanager
from typing import Any
from unittest.mock import Mock, patch

import pytest
from deepctl_cmd_speak import command as speak
from deepctl_cmd_speak.command import SpeakCommand
from deepctl_cmd_speak.models import SpeakCodedError, SpeakResult
from deepctl_core import AuthManager, Config, DeepgramClient


class _Recorder:
    """An event log shared by a fake stream, a fake stdout and a fake player."""

    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []
        self.closed = False


class _Stream:
    """An SDK-like audio iterator that logs each hand-out and its close()."""

    def __init__(self, rec: _Recorder, chunks: list[bytes]) -> None:
        self._rec = rec
        self._chunks = list(chunks)

    def __iter__(self) -> _Stream:
        return self

    def __next__(self) -> bytes:
        if not self._chunks:
            raise StopIteration
        chunk = self._chunks.pop(0)
        self._rec.events.append(("yield", chunk))
        return chunk

    def close(self) -> None:
        self._rec.closed = True


class _Sink:
    """A file-like stdout/player stdin that logs writes and flushes."""

    def __init__(self, rec: _Recorder, fail_on: int | None = None, exc=None) -> None:
        self._rec = rec
        self._fail_on = fail_on
        self._exc = exc
        self._writes = 0

    def write(self, data: bytes) -> int:
        self._writes += 1
        if self._fail_on is not None and self._writes >= self._fail_on:
            raise self._exc
        self._rec.events.append(("write", data))
        return len(data)

    def flush(self) -> None:
        self._rec.events.append(("flush", None))


def _run(cmd: SpeakCommand, client: Mock, **kwargs: Any):
    base = {
        "text": "Hello",
        "output": None,
        "model": "aura-2-asteria-en",
        "encoding": None,
        "container": None,
        "sample_rate": None,
        "file": None,
    }
    base.update(kwargs)
    return cmd.handle(
        config=Mock(spec=Config),
        auth_manager=Mock(spec=AuthManager),
        client=client,
        **base,
    )


@pytest.fixture
def fake_sys():
    with patch("deepctl_cmd_speak.command.sys") as mock_sys:
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = False
        mock_sys.argv = ["dg", "speak", "Hello"]
        yield mock_sys


@pytest.fixture
def silenced():
    with patch("deepctl_cmd_speak.command._silence_stdout") as fn:
        yield fn


class TestV1ToStdout:
    def test_each_chunk_is_written_and_flushed_before_the_next(self, fake_sys):
        rec = _Recorder()
        fake_sys.stdout.buffer = _Sink(rec)
        client = Mock(spec=DeepgramClient)
        client.speak_text.return_value = _Stream(rec, [b"a", b"b", b"c"])

        assert _run(SpeakCommand(), client) is None
        assert rec.events == [
            ("yield", b"a"),
            ("write", b"a"),
            ("flush", None),
            ("yield", b"b"),
            ("write", b"b"),
            ("flush", None),
            ("yield", b"c"),
            ("write", b"c"),
            ("flush", None),
        ]
        assert rec.closed

    def test_epipe_stops_closes_the_response_and_ends_quietly(
        self, fake_sys, silenced, capsys
    ):
        rec = _Recorder()
        fake_sys.stdout.buffer = _Sink(rec, fail_on=2, exc=BrokenPipeError())
        client = Mock(spec=DeepgramClient)
        client.speak_text.return_value = _Stream(rec, [b"a", b"b", b"c", b"d"])
        cmd = SpeakCommand()

        result = _run(cmd, client)

        assert result is None
        assert cmd.exit_code_for(result) == 0
        # Stopped at the failed write: "c" and "d" were never requested.
        assert [e for e in rec.events if e[0] == "yield"] == [
            ("yield", b"a"),
            ("yield", b"b"),
        ]
        assert rec.closed
        silenced.assert_called_once()
        assert "Error" not in capsys.readouterr().err

    def test_einval_on_a_windows_pipe_ends_quietly(
        self, fake_sys, silenced, capsys, monkeypatch
    ):
        """Windows: a closed pipe is EINVAL, not EPIPE."""

        class PipeSink(_Sink):
            def fileno(self) -> int:
                return w

        monkeypatch.setattr(sys, "platform", "win32")
        r, w = os.pipe()
        rec = _Recorder()
        exc = OSError(errno.EINVAL, "Invalid argument")
        fake_sys.stdout.buffer = PipeSink(rec, fail_on=2, exc=exc)
        client = Mock(spec=DeepgramClient)
        client.speak_text.return_value = _Stream(rec, [b"a", b"b", b"c"])
        cmd = SpeakCommand()
        try:
            result = _run(cmd, client)
        finally:
            os.close(r)
            os.close(w)

        assert result is None
        assert cmd.exit_code_for(result) == 0
        assert [e for e in rec.events if e[0] == "yield"] == [
            ("yield", b"a"),
            ("yield", b"b"),
        ]
        silenced.assert_called_once()
        assert "Error" not in capsys.readouterr().err

    @pytest.mark.parametrize("err", [errno.ENOSPC, errno.EIO, errno.EFBIG])
    def test_other_write_errors_are_output_unwritable(
        self, fake_sys, silenced, capsys, err
    ):
        rec = _Recorder()
        exc = OSError(err, __import__("os").strerror(err))
        fake_sys.stdout.buffer = _Sink(rec, fail_on=1, exc=exc)
        client = Mock(spec=DeepgramClient)
        client.speak_text.return_value = _Stream(rec, [b"a", b"b"])
        cmd = SpeakCommand()

        result = _run(cmd, client)

        assert isinstance(result, SpeakCodedError)
        assert cmd.exit_code_for(result) == 1
        assert result.code == "output_unwritable"
        assert result.next_step is None
        assert result.details == {"path": "-", "reason": exc.strerror}
        assert result.error == (f"Couldn't write the output to stdout: {exc.strerror}.")
        assert rec.closed
        # fd 1 is pointed at the null device before anything else is said,
        # so the envelope can't land on stdout and the exit flush can't fail.
        silenced.assert_called_once()
        err_lines = [
            line for line in capsys.readouterr().err.splitlines() if line.strip()
        ]
        assert any(result.error in line for line in err_lines)
        assert any(result.hint in line for line in err_lines)
        assert cmd.stdout_is_protocol is True

    def test_error_text_is_sanitized(self, fake_sys, silenced):
        rec = _Recorder()
        exc = OSError(errno.EIO, "bad\x1b[31m\nthing" + "x" * 300)
        fake_sys.stdout.buffer = _Sink(rec, fail_on=1, exc=exc)
        client = Mock(spec=DeepgramClient)
        client.speak_text.return_value = _Stream(rec, [b"a"])

        result = _run(SpeakCommand(), client)

        reason = result.details["reason"]
        assert "\x1b" not in reason and "\n" not in reason
        assert len(reason) <= 200


class TestFluxToStdout:
    def test_header_then_each_frame_flushed(self, fake_sys):
        rec = _Recorder()
        fake_sys.stdout.buffer = _Sink(rec)
        client = Mock(spec=DeepgramClient)
        client.speak_text_stream.return_value = _Stream(rec, [b"\x01\x00", b"\x02\x00"])

        assert _run(SpeakCommand(), client, model="flux-alexis-en") is None
        writes = [e for e in rec.events if e[0] != "yield"]
        assert writes[0][0] == "write" and writes[0][1][:4] == b"RIFF"
        assert writes[1:] == [
            ("write", b"\x01\x00"),
            ("flush", None),
            ("write", b"\x02\x00"),
            ("flush", None),
        ]

    def test_enospc_is_output_unwritable(self, fake_sys, silenced):
        rec = _Recorder()
        exc = OSError(errno.ENOSPC, "No space left on device")
        fake_sys.stdout.buffer = _Sink(rec, fail_on=1, exc=exc)
        client = Mock(spec=DeepgramClient)
        client.speak_text_stream.return_value = _Stream(rec, [b"\x01\x00"])
        cmd = SpeakCommand()

        result = _run(cmd, client, model="flux-alexis-en")

        assert isinstance(result, SpeakCodedError)
        assert result.code == "output_unwritable"
        assert cmd.exit_code_for(result) == 1
        silenced.assert_called_once()

    def test_epipe_ends_quietly(self, fake_sys, silenced):
        rec = _Recorder()
        fake_sys.stdout.buffer = _Sink(rec, fail_on=1, exc=BrokenPipeError())
        client = Mock(spec=DeepgramClient)
        client.speak_text_stream.return_value = _Stream(rec, [b"\x01\x00", b"\x02"])
        cmd = SpeakCommand()

        result = _run(cmd, client, model="flux-alexis-en")

        assert result is None
        assert cmd.exit_code_for(result) == 0
        silenced.assert_called_once()


class TestV1Play:
    @pytest.fixture
    def player(self, monkeypatch):
        """A stdin player that logs its start and every write."""
        rec = _Recorder()
        state: dict[str, Any] = {"fail_on": None}

        @contextmanager
        def fake_player_stdin(name):
            rec.events.append(("spawn", name))
            yield _Sink(rec, fail_on=state["fail_on"], exc=BrokenPipeError())
            rec.events.append(("waited", name))

        monkeypatch.setattr(speak, "_player_stdin", fake_player_stdin)
        monkeypatch.setattr(speak, "_find_audio_player", lambda: "ffplay")
        return rec, state

    def test_player_starts_first_and_gets_each_chunk_as_it_arrives(
        self, fake_sys, player
    ):
        rec, _ = player
        client = Mock(spec=DeepgramClient)
        client.speak_text.return_value = _Stream(rec, [b"a", b"b"])

        result = _run(SpeakCommand(), client, play=True, encoding="linear16")

        assert isinstance(result, SpeakResult)
        assert result.played is True
        assert result.bytes_written == 2
        assert rec.events == [
            ("spawn", "ffplay"),
            ("yield", b"a"),
            ("write", b"a"),
            ("flush", None),
            ("yield", b"b"),
            ("write", b"b"),
            ("flush", None),
            ("waited", "ffplay"),
        ]
        assert rec.closed

    def test_player_quit_stops_and_closes_the_response(self, fake_sys, player):
        rec, state = player
        state["fail_on"] = 1
        client = Mock(spec=DeepgramClient)
        client.speak_text.return_value = _Stream(rec, [b"a", b"b", b"c"])

        result = _run(SpeakCommand(), client, play=True, encoding="linear16")

        assert isinstance(result, SpeakResult)
        assert [e for e in rec.events if e[0] == "yield"] == [("yield", b"a")]
        assert rec.closed

    def test_no_audio_fails(self, fake_sys, player):
        rec, _ = player
        client = Mock(spec=DeepgramClient)
        client.speak_text.return_value = _Stream(rec, [])
        cmd = SpeakCommand()

        result = _run(cmd, client, play=True, encoding="linear16")

        assert result.status == "error"
        assert "no audio" in result.message


class TestResultOnStdout:
    """The SpeakResult itself can't be written to stdout."""

    def _result(self):
        return SpeakResult(status="success", output_path="a.wav", bytes_written=3)

    def test_full_stdout_is_output_unwritable_and_exits_1(self, silenced, capsys):
        cmd = SpeakCommand()
        with patch.object(
            speak.BaseCommand,
            "output_result",
            side_effect=OSError(errno.ENOSPC, "No space left on device"),
        ):
            cmd.output_result(self._result(), Mock(spec=Config))
        silenced.assert_called_once()
        assert cmd.exit_code_for(self._result()) == 1
        err = capsys.readouterr().err
        assert "Couldn't write the output to stdout: No space left on device." in err

    def test_epipe_on_the_result_is_quiet_and_exits_0(self):
        """The real path: rich's own EPIPE handling under -o json, with the
        reader of stdout already gone."""
        import subprocess
        import sys as _sys

        code = (
            "import sys\n"
            "from deepctl_core.output import setup_output\n"
            "setup_output('json')\n"
            "from deepctl_cmd_speak.command import SpeakCommand\n"
            "from deepctl_cmd_speak.models import SpeakResult\n"
            "cmd = SpeakCommand()\n"
            "r = SpeakResult(status='success', output_path='a.wav', message='x' * 70000)\n"
            "cmd.output_result(r, None)\n"
            "sys.stderr.write(f'RC={cmd.exit_code_for(r)}\\n')\n"
        )
        p = subprocess.Popen(
            [_sys.executable, "-c", code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**__import__("os").environ, "DEEPCTL_TELEMETRY_DISABLED": "1"},
        )
        p.stdout.close()  # the reader is gone before the result is written
        err = p.stderr.read().decode()
        assert p.wait(60) == 0, err
        assert "RC=0" in err
        assert "Traceback" not in err and "Exception ignored" not in err

    def _fail_on_stdout(self, monkeypatch, fd, code):
        """The result write fails with ``code``; fd 1 is ``fd``."""
        monkeypatch.setattr(
            speak.sys, "stdout", type("Out", (), {"fileno": lambda _self: fd})()
        )
        cmd = SpeakCommand()
        with patch.object(
            speak.BaseCommand,
            "output_result",
            side_effect=OSError(code, os.strerror(code)),
        ):
            cmd.output_result(self._result(), Mock(spec=Config))
        return cmd

    def test_einval_on_a_windows_pipe_is_quiet_and_exits_0(
        self, silenced, capsys, monkeypatch
    ):
        """Windows reports a write to a pipe whose reader closed as EINVAL
        ("Invalid argument"), not EPIPE."""
        monkeypatch.setattr(speak.sys, "platform", "win32")
        r, w = os.pipe()
        try:
            cmd = self._fail_on_stdout(monkeypatch, w, errno.EINVAL)
        finally:
            os.close(r)
            os.close(w)
        silenced.assert_called_once()
        assert cmd.exit_code_for(self._result()) == 0
        assert capsys.readouterr().err == ""

    def test_einval_on_a_windows_file_is_output_unwritable(
        self, silenced, capsys, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(speak.sys, "platform", "win32")
        fd = os.open(tmp_path / "out", os.O_WRONLY | os.O_CREAT)
        try:
            cmd = self._fail_on_stdout(monkeypatch, fd, errno.EINVAL)
        finally:
            os.close(fd)
        assert cmd.exit_code_for(self._result()) == 1
        assert "Couldn't write the output to stdout:" in capsys.readouterr().err

    def test_a_coded_error_is_not_reported_twice(self, silenced, capsys):
        cmd = SpeakCommand()
        coded = SpeakCodedError(error="x", code="output_unwritable", hint="h")
        with patch.object(
            speak.BaseCommand, "output_result", side_effect=OSError(errno.EIO, "I/O")
        ):
            cmd.output_result(coded, Mock(spec=Config))
        silenced.assert_called_once()
        assert capsys.readouterr().err == ""


class TestCloseIsBounded:
    def test_a_hanging_close_is_abandoned_after_the_timeout(self):
        import threading
        import time

        release = threading.Event()

        class Hangs:
            def close(self):
                release.wait(5)

        t0 = time.monotonic()
        speak._close(Hangs(), timeout=0.2)
        assert time.monotonic() - t0 < 3.0  # the close itself hangs 5 s
        release.set()

    def test_several_closes_share_one_timeout(self):
        """Closing the stream and the response behind it waits once, not
        once per close."""
        import threading
        import time

        release = threading.Event()
        closed: list[str] = []

        class Hangs:
            def __init__(self, name):
                self.name = name

            def close(self):
                closed.append(self.name)
                release.wait(10)

        # One 2 s wait; one per close would be 4 s.
        t0 = time.monotonic()
        speak._close(Hangs("a"), Hangs("b"), timeout=2.0)
        assert time.monotonic() - t0 < 3.5
        assert closed == ["a"]  # in order; b waits behind the stalled a
        release.set()

    def test_close_errors_are_quiet(self):
        class Fails:
            def close(self):
                raise RuntimeError("boom")

        speak._close(Fails())
