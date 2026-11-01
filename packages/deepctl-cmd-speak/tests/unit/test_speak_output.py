"""``--out-file``, ``--force`` and the deprecated ``-o``.

The process-level checks (fake API request counts, a real ``/dev/stdout``)
run in a Docker harness; these cover the logic.
"""

from __future__ import annotations

import errno
import os
import shlex
import stat
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest
from deepctl_cmd_speak import command as speak
from deepctl_cmd_speak import sinks
from deepctl_cmd_speak.command import SpeakCommand
from deepctl_cmd_speak.models import SpeakCodedError, SpeakResult
from deepctl_core import AuthManager, Config, DeepgramClient


class _Cut(Exception):
    """Stands in for the SDK error a dropped connection raises."""


def _stream(chunks: list[bytes], *, fail_after: bool = False, check=None):
    for i, chunk in enumerate(chunks):
        if check is not None:
            check(i)
        yield chunk
    if fail_after:
        raise _Cut("connection reset")


def _run(cmd: SpeakCommand, client: Mock, **kwargs: Any):
    base: dict[str, Any] = {
        "text": "Hello",
        "output": None,
        "out_file": None,
        "force": False,
        "model": "aura-2-asteria-en",
        "encoding": None,
        "container": None,
        "sample_rate": None,
        "file": None,
    }
    base.update(kwargs)
    planned = cmd.pre_auth_check(Mock(spec=Config), **base)
    if planned is not None:
        return planned
    return cmd.handle(
        config=Mock(spec=Config),
        auth_manager=Mock(spec=AuthManager),
        client=client,
        **base,
    )


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(speak, "_deprecation_shown", False)
    monkeypatch.setattr(speak, "_agentic", False)
    monkeypatch.setattr(speak, "agent_mode", lambda: False)
    # stdout is "a pipe" unless a test says otherwise.
    monkeypatch.setattr(sinks, "stdout_stat", lambda: None)
    # The signal guard re-delivers a signal after _CLOSE_TIMEOUT_S if the
    # run is still unwinding. A loaded test host must never get there by
    # accident: a timer that fired after the guard restored SIG_DFL would
    # end pytest itself. The one test of the timer sets its own value.
    monkeypatch.setattr(speak, "_SIGNAL_GRACE_S", 600.0)
    import signal

    getsignal = signal.getsignal  # the real one, even if a test patches it
    watched = [getattr(signal, n) for n in ("SIGINT", "SIGTERM", "SIGHUP")]
    handlers = {sig: getsignal(sig) for sig in watched}
    old = os.umask(0o022)
    yield
    os.umask(old)
    # No test may leave a signal handler, a section or a deferred signal.
    assert {sig: getsignal(sig) for sig in watched} == handlers
    assert sinks._critical == 0
    assert not sinks._deferred


@pytest.fixture
def fake_sys():
    with patch("deepctl_cmd_speak.command.sys") as mock_sys:
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = False
        mock_sys.argv = ["dg", "speak", "Hello"]
        yield mock_sys


@pytest.fixture
def client():
    return Mock(spec=DeepgramClient)


# ── plan_target (before the request) ────────────────────────────────────


class TestPlanTarget:
    def test_missing_file_in_existing_dir_is_new(self, tmp_path):
        assert sinks.plan_target(str(tmp_path / "a.wav"), overwrite=False) == (
            sinks.Target(str(tmp_path / "a.wav"), "new")
        )

    def test_existing_regular_file_is_refused_without_overwrite(self, tmp_path):
        f = tmp_path / "a.wav"
        f.write_bytes(b"old")
        assert sinks.plan_target(str(f), overwrite=False) == sinks.Refusal("exists")
        assert sinks.plan_target(str(f), overwrite=True).kind == "replace"

    @pytest.mark.parametrize("dangling", [False, True])
    def test_symlink_is_refused_without_overwrite(self, tmp_path, dangling):
        link = tmp_path / "a.wav"
        target = tmp_path / "elsewhere.wav"
        if not dangling:
            target.write_bytes(b"precious")
        link.symlink_to(target)
        assert sinks.plan_target(str(link), overwrite=False) == sinks.Refusal("exists")
        assert sinks.plan_target(str(link), overwrite=True).kind == "replace"

    def test_directory_is_unwritable(self, tmp_path):
        refusal = sinks.plan_target(str(tmp_path), overwrite=True)
        assert isinstance(refusal, sinks.Refusal)
        assert refusal.reason == "unwritable"
        assert refusal.error.errno == errno.EISDIR

    def test_missing_parent_is_unwritable(self, tmp_path):
        refusal = sinks.plan_target(str(tmp_path / "nope" / "a.wav"), overwrite=False)
        assert refusal.reason == "unwritable"
        assert refusal.error.errno == errno.ENOENT

    def test_parent_that_is_a_file_is_unwritable(self, tmp_path):
        (tmp_path / "f").write_text("x")
        refusal = sinks.plan_target(str(tmp_path / "f" / "a.wav"), overwrite=False)
        assert refusal.reason == "unwritable"

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")
    def test_unwritable_parent_is_unwritable(self, tmp_path):
        d = tmp_path / "ro"
        d.mkdir()
        d.chmod(0o555)
        try:
            refusal = sinks.plan_target(str(d / "a.wav"), overwrite=False)
            assert refusal.reason == "unwritable"
            assert refusal.error.errno == errno.EACCES
        finally:
            d.chmod(0o755)

    def test_character_device_is_special_and_opened_now(self):
        target = sinks.plan_target(os.devnull, overwrite=False)
        assert target.kind == "special"
        assert target.fd is not None
        assert os.get_blocking(target.fd)  # O_NONBLOCK cleared for the writes
        os.close(target.fd)

    def test_fifo_with_a_reader_is_special(self, tmp_path):
        fifo = tmp_path / "f"
        os.mkfifo(fifo)
        reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        try:
            target = sinks.plan_target(str(fifo), overwrite=False)
            assert target.kind == "special"
            os.close(target.fd)
        finally:
            os.close(reader)

    def test_fifo_with_no_reader_is_refused_not_waited_on(self, tmp_path):
        """An open() that would block until a reader comes is ENXIO."""
        fifo = tmp_path / "f"
        os.mkfifo(fifo)
        refusal = sinks.plan_target(str(fifo), overwrite=False)
        assert refusal.reason == "unwritable"
        assert refusal.error.errno == errno.ENXIO
        assert refusal.error.strerror == "no program is reading from that pipe"

    def test_socket_is_refused(self, tmp_path):
        """Only character devices and FIFOs are written as they are."""
        import socket

        path = tmp_path / "s"
        sock = socket.socket(socket.AF_UNIX)
        sock.bind(str(path))
        try:
            refusal = sinks.plan_target(str(path), overwrite=True)
            assert refusal.reason == "unwritable"
            assert refusal.error.strerror == (
                "it isn't a file, a pipe or a device like /dev/null"
            )
        finally:
            sock.close()

    def test_symlink_to_a_device_is_special(self, tmp_path):
        link = tmp_path / "a.wav"
        link.symlink_to(os.devnull)
        target = sinks.plan_target(str(link), overwrite=False)
        assert target.kind == "special"
        os.close(target.fd)

    def test_special_path_swapped_before_the_open_is_refused(
        self, tmp_path, monkeypatch
    ):
        """The opened file must be the device that was checked."""
        victim = tmp_path / "victim"
        victim.write_bytes(b"VICTIM")
        link = tmp_path / "a.wav"
        link.symlink_to(os.devnull)
        real_open = os.open

        def swap_then_open(path, flags, *a):
            if str(path) == str(link):
                link.unlink()
                link.symlink_to(victim)
            return real_open(path, flags, *a)

        monkeypatch.setattr(sinks.os, "open", swap_then_open)
        refusal = sinks.plan_target(str(link), overwrite=False)
        assert refusal.reason == "unwritable"
        assert refusal.error.strerror == "something replaced it before writing started"
        assert victim.read_bytes() == b"VICTIM"

    def test_replace_keeps_the_old_mode(self, tmp_path):
        f = tmp_path / "a.wav"
        f.write_bytes(b"old")
        f.chmod(0o600)
        assert sinks.plan_target(str(f), overwrite=True).mode == 0o600

    def test_replace_of_a_symlink_has_no_mode_to_keep(self, tmp_path):
        (tmp_path / "t").write_bytes(b"x")
        (tmp_path / "a.wav").symlink_to("t")
        assert sinks.plan_target(str(tmp_path / "a.wav"), overwrite=True).mode is None

    def test_same_file_as_stdout_is_stdout(self, tmp_path, monkeypatch):
        f = tmp_path / "out.wav"
        f.write_bytes(b"")
        monkeypatch.setattr(sinks, "stdout_stat", lambda: os.stat(f))
        # Even an existing regular file: it's stdout, not an overwrite.
        assert sinks.plan_target(str(f), overwrite=False).kind == "stdout"


# ── AudioFile (at and after the first chunk) ────────────────────────────


class TestAudioFile:
    def test_new_file_appears_on_first_write_with_umask_mode(self, tmp_path):
        path = tmp_path / "a.wav"
        out = sinks.AudioFile(sinks.Target(str(path), "new"))
        assert not path.exists()
        out.write(b"abc")
        assert path.exists()
        out.commit()
        assert path.read_bytes() == b"abc"
        assert stat.S_IMODE(path.stat().st_mode) == 0o644

    def test_abort_removes_the_partial_file(self, tmp_path):
        path = tmp_path / "a.wav"
        out = sinks.AudioFile(sinks.Target(str(path), "new"))
        out.write(b"abc")
        out.abort()
        assert not path.exists()

    def test_o_excl_race_raises_exists_and_leaves_their_file(self, tmp_path):
        path = tmp_path / "a.wav"
        out = sinks.AudioFile(sinks.Target(str(path), "new"))
        path.write_bytes(b"theirs")  # created between the check and the open
        with pytest.raises(sinks.Exists):
            out.write(b"abc")
        out.abort()
        assert path.read_bytes() == b"theirs"

    def test_replace_writes_a_0600_temp_then_replaces(self, tmp_path):
        path = tmp_path / "a.wav"
        path.write_bytes(b"old")
        out = sinks.AudioFile(sinks.Target(str(path), "replace"))
        out.write(b"new")
        temps = [p for p in tmp_path.iterdir() if p.name.endswith(".part")]
        assert len(temps) == 1
        assert stat.S_IMODE(temps[0].stat().st_mode) == 0o600
        assert path.read_bytes() == b"old"  # untouched until commit
        out.commit()
        assert path.read_bytes() == b"new"
        assert stat.S_IMODE(path.stat().st_mode) == 0o666 & ~sinks._UMASK
        assert [p.name for p in tmp_path.iterdir()] == ["a.wav"]

    def test_replace_swaps_a_symlink_and_leaves_its_target(self, tmp_path):
        target = tmp_path / "precious.txt"
        target.write_bytes(b"precious")
        link = tmp_path / "a.wav"
        link.symlink_to(target)
        out = sinks.AudioFile(sinks.Target(str(link), "replace"))
        out.write(b"audio")
        out.commit()
        assert not link.is_symlink()
        assert link.read_bytes() == b"audio"
        assert target.read_bytes() == b"precious"

    def test_replace_keeps_a_0600_file_0600(self, tmp_path):
        """Replacing doesn't widen an existing file's mode."""
        path = tmp_path / "a.wav"
        path.write_bytes(b"old")
        path.chmod(0o600)
        out = sinks.AudioFile(sinks.plan_target(str(path), overwrite=True))
        out.write(b"new")
        out.commit()
        assert path.read_bytes() == b"new"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_abort_leaves_a_file_that_replaced_ours(self, tmp_path):
        """Only the file this run created is removed."""
        path = tmp_path / "a.wav"
        out = sinks.AudioFile(sinks.Target(str(path), "new"))
        out.write(b"partial")
        path.unlink()
        path.write_bytes(b"theirs")
        out.abort()
        assert path.read_bytes() == b"theirs"

    def test_failed_replace_keeps_the_old_file(self, tmp_path):
        path = tmp_path / "a.wav"
        path.write_bytes(b"old")
        out = sinks.AudioFile(sinks.Target(str(path), "replace"))
        out.write(b"partial")
        out.abort()
        assert path.read_bytes() == b"old"
        assert [p.name for p in tmp_path.iterdir()] == ["a.wav"]

    def test_write_error_is_write_failed(self, tmp_path, monkeypatch):
        path = tmp_path / "a.wav"
        out = sinks.AudioFile(sinks.Target(str(path), "new"))

        def full(fd, data):
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(sinks.os, "write", full)
        with pytest.raises(sinks.WriteFailed) as info:
            out.write(b"abc")
        assert info.value.error.errno == errno.ENOSPC
        assert info.value.stdout is False

    def test_special_file_is_not_removed(self, tmp_path):
        fifo_like = sinks.AudioFile(sinks.plan_target(os.devnull, overwrite=False))
        fifo_like.write(b"abc")
        fifo_like.abort()
        assert os.path.exists(os.devnull)

    def test_nothing_written_means_nothing_created(self, tmp_path):
        path = tmp_path / "a.wav"
        out = sinks.AudioFile(sinks.Target(str(path), "new"))
        out.write(b"")
        out.abort()
        assert not path.exists()


# ── The command: checks before the request ──────────────────────────────


class TestPreRequest:
    def test_existing_out_file_is_refused_with_no_request(self, fake_sys, client):
        Path("a.wav").write_bytes(b"old")
        cmd = SpeakCommand()
        result = _run(cmd, client, out_file="a.wav")
        assert isinstance(result, SpeakCodedError)
        assert result.code == "invalid_argument"
        assert result.error == "a.wav already exists; dg won't overwrite it."
        assert result.hint == "Choose a new file name, or pass --force to overwrite."
        assert result.next_step is None
        client.speak_text.assert_not_called()
        client.speak_text_stream.assert_not_called()
        assert Path("a.wav").read_bytes() == b"old"

    def test_existing_symlink_is_refused(self, fake_sys, client):
        Path("t").write_bytes(b"precious")
        Path("a.wav").symlink_to("t")
        result = _run(SpeakCommand(), client, out_file="a.wav")
        assert result.code == "invalid_argument"
        client.speak_text.assert_not_called()

    def test_force_replaces_the_file(self, fake_sys, client):
        Path("a.wav").write_bytes(b"old")
        client.speak_text.return_value = _stream([b"ne", b"w"])
        result = _run(SpeakCommand(), client, out_file="a.wav", force=True)
        assert isinstance(result, SpeakResult)
        assert result.output_path == "a.wav"
        assert result.bytes_written == 3
        assert Path("a.wav").read_bytes() == b"new"

    def test_force_replaces_a_symlink_not_its_target(self, fake_sys, client):
        Path("t").write_bytes(b"precious")
        Path("a.wav").symlink_to("t")
        client.speak_text.return_value = _stream([b"audio"])
        _run(SpeakCommand(), client, out_file="a.wav", force=True)
        assert not Path("a.wav").is_symlink()
        assert Path("a.wav").read_bytes() == b"audio"
        assert Path("t").read_bytes() == b"precious"

    def test_missing_parent_is_output_unwritable_with_no_request(
        self, fake_sys, client
    ):
        result = _run(SpeakCommand(), client, out_file="nope/a.wav")
        assert isinstance(result, SpeakCodedError)
        assert result.code == "output_unwritable"
        assert result.next_step is None
        assert result.details["path"] == "nope/a.wav"
        assert result.details["reason"] == os.strerror(errno.ENOENT)
        client.speak_text.assert_not_called()

    def test_directory_target_is_output_unwritable(self, fake_sys, client):
        Path("d").mkdir()
        result = _run(SpeakCommand(), client, out_file="d")
        assert result.code == "output_unwritable"
        assert result.details["reason"] == os.strerror(errno.EISDIR)

    def test_out_file_and_o_conflict(self, fake_sys, client, capsys):
        cmd = SpeakCommand()
        result = _run(cmd, client, out_file="a.wav", output="b.wav")
        assert result.code == "invalid_argument"
        assert result.error == (
            "Use either --out-file or -o/--output for the audio file, not both."
        )
        assert result.next_step == "dg speak --help"
        assert "DEPRECATED" not in capsys.readouterr().err
        client.speak_text.assert_not_called()

    @pytest.mark.parametrize("value", ["json", "yaml", "table", "csv", "JSON"])
    def test_o_format_value_is_refused_with_a_runnable_next_step(
        self, fake_sys, client, monkeypatch, value
    ):
        monkeypatch.setattr(
            speak,
            "_argv",
            lambda: ["--profile", "work", "speak", "Q&A at 5", "-o", value],
        )
        result = _run(SpeakCommand(), client, output=value, text="Q&A at 5")
        assert result.code == "invalid_argument"
        assert result.error == (
            f"dg speak -o is the audio file name, so -o {value} would write a "
            f"file named {value}."
        )
        ns = result.next_step
        assert ns is not None
        assert shlex.join(shlex.split(ns)) == ns
        tokens = shlex.split(ns)
        assert tokens[:6] == ["dg", "--profile", "work", "-o", value, "speak"]
        assert tokens[6:8] == ["Q&A at 5", "--out-file"]
        assert tokens[8].startswith("speech-") and tokens[8].endswith(".mp3")
        assert len(tokens) == 9
        client.speak_text.assert_not_called()

    def test_o_text_points_at_out_file(self, fake_sys, client, monkeypatch):
        """There's no root text format: the fix drops -o and adds --out-file."""
        monkeypatch.setattr(speak, "_argv", lambda: ["-q", "speak", "hi", "-o", "text"])
        result = _run(SpeakCommand(), client, output="text", text="hi")
        assert result.code == "invalid_argument"
        assert result.error == (
            "dg speak -o text would write the audio to a file named text."
        )
        assert result.hint == (
            "Pass --out-file FILE to save the audio. There's no text output format."
        )
        tokens = shlex.split(result.next_step)
        assert tokens[:5] == ["dg", "-q", "speak", "hi", "--out-file"]
        assert tokens[5].startswith("speech-") and len(tokens) == 6
        client.speak_text.assert_not_called()

    def test_o_text_next_step_is_null_with_a_key_on_argv(
        self, fake_sys, client, monkeypatch
    ):
        monkeypatch.setattr(
            speak, "_argv", lambda: ["-k", "sk_sentinel", "speak", "hi", "-o", "text"]
        )
        result = _run(SpeakCommand(), client, output="text", text="hi")
        assert result.error.startswith("dg speak -o text")
        assert result.next_step is None

    def test_o_format_next_step_is_null_with_a_key_on_argv(
        self, fake_sys, client, monkeypatch
    ):
        monkeypatch.setattr(
            speak,
            "_argv",
            lambda: ["--api-key", "sk_sentinel", "speak", "hi", "-o", "json"],
        )
        result = _run(SpeakCommand(), client, output="json", text="hi")
        assert result.next_step is None
        assert "sk_sentinel" not in result.model_dump_json()

    def test_o_file_is_deprecated_once_and_overwrites(
        self, fake_sys, client, capsys, monkeypatch
    ):
        Path("a.wav").write_bytes(b"old")
        client.speak_text.side_effect = lambda **_: _stream([b"new"])
        cmd = SpeakCommand()
        _run(cmd, client, output="a.wav")
        _run(cmd, client, output="a.wav")
        err = capsys.readouterr().err
        assert err.count("Deprecated:") == 1
        assert (
            "dg speak -o/--output FILE will set the output format in 1.0. Use "
            "--out-file FILE instead (add --force to overwrite, as -o does)." in err
        )
        assert Path("a.wav").read_bytes() == b"new"

    def test_deprecation_prints_under_quiet_in_agent_grammar(
        self, fake_sys, client, capsys, monkeypatch
    ):
        from deepctl_core import output

        monkeypatch.setattr(speak, "_agentic", True)
        monkeypatch.setitem(output._output_config, "quiet", True)
        client.speak_text.return_value = _stream([b"new"])
        _run(SpeakCommand(), client, output="a.wav")
        lines = [
            ln for ln in capsys.readouterr().err.splitlines() if "DEPRECATED" in ln
        ]
        assert lines == [
            "DEPRECATED: dg speak -o/--output FILE will set the output format "
            "in 1.0. Use --out-file FILE instead (add --force to overwrite, as "
            "-o does)."
        ]

    @pytest.mark.parametrize("fmt", ["json", "yaml"])
    def test_f1_with_explicit_json_is_refused(self, fake_sys, client, monkeypatch, fmt):
        f = Path("out.wav")
        f.write_bytes(b"")
        monkeypatch.setattr(sinks, "stdout_stat", lambda: os.stat(f))
        monkeypatch.setattr(speak, "get_output_format", lambda: fmt)
        monkeypatch.setattr(speak, "format_source", lambda: "explicit")
        result = _run(SpeakCommand(), client, out_file="out.wav")
        assert result.code == "invalid_argument"
        name = fmt.upper()
        assert result.error == (
            "dg speak would write the audio to stdout, so there's no room for "
            f"the {name} result."
        )
        assert result.hint == (
            f"Give --out-file a different file from stdout, or don't ask for "
            f"{name} output."
        )
        assert result.next_step == "dg speak --help"
        client.speak_text.assert_not_called()

    def test_f1_with_implicit_json_writes_audio_only(
        self, fake_sys, client, monkeypatch, capsys
    ):
        f = Path("out.wav")
        f.write_bytes(b"")
        monkeypatch.setattr(sinks, "stdout_stat", lambda: os.stat(f))
        monkeypatch.setattr(speak, "get_output_format", lambda: "json")
        monkeypatch.setattr(speak, "format_source", lambda: "hard_agent")
        monkeypatch.setattr(speak, "_agentic", True)
        written: list[bytes] = []
        stdout = Mock()
        stdout.write.side_effect = written.append
        monkeypatch.setattr(sinks.sys, "stdout", Mock(buffer=stdout))
        client.speak_text.return_value = _stream([b"RIFF", b"data"])

        cmd = SpeakCommand()
        result = _run(cmd, client, out_file="out.wav")

        assert result is None  # no document after the audio
        assert cmd.stdout_is_protocol is True
        assert written == [b"RIFF", b"data"]
        assert "OK: Wrote 8 bytes to stdout" in capsys.readouterr().err


# ── The command: no file on any error ───────────────────────────────────


class TestNoPartialFile:
    def test_request_error_leaves_no_file(self, fake_sys, client):
        def unauthorized(**_):
            raise RuntimeError("status_code: 401")
            yield b""  # pragma: no cover

        client.speak_text.side_effect = lambda **kw: unauthorized(**kw)
        with pytest.raises(Exception, match="401"):
            _run(SpeakCommand(), client, out_file="a.wav")
        assert not Path("a.wav").exists()

    def test_cut_stream_leaves_no_partial_file(self, fake_sys, client):
        client.speak_text.return_value = _stream([b"a", b"b"], fail_after=True)
        with pytest.raises(Exception, match="connection reset"):
            _run(SpeakCommand(), client, out_file="a.wav")
        assert list(Path().iterdir()) == []

    def test_cut_stream_keeps_the_old_o_file(self, fake_sys, client):
        Path("a.wav").write_bytes(b"old bytes")
        client.speak_text.return_value = _stream([b"a"], fail_after=True)
        with pytest.raises(Exception, match="connection reset"):
            _run(SpeakCommand(), client, output="a.wav")
        assert Path("a.wav").read_bytes() == b"old bytes"
        assert [p.name for p in Path().iterdir()] == ["a.wav"]

    def test_zero_chunks_leave_no_file(self, fake_sys, client):
        client.speak_text.return_value = _stream([])
        result = _run(SpeakCommand(), client, out_file="a.wav")
        assert result.status == "error"
        assert not Path("a.wav").exists()

    def test_file_write_error_is_output_unwritable_with_no_partial_file(
        self, fake_sys, client, monkeypatch
    ):
        real_write = os.write
        calls = {"n": 0}

        def fills_up(fd, data):
            calls["n"] += 1
            if calls["n"] > 1:
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_write(fd, data)

        monkeypatch.setattr(sinks.os, "write", fills_up)
        client.speak_text.return_value = _stream([b"a", b"b", b"c"])
        cmd = SpeakCommand()

        result = _run(cmd, client, out_file="a.wav")

        assert isinstance(result, SpeakCodedError)
        assert result.code == "output_unwritable"
        assert result.error == (
            "Couldn't write the output to a.wav: No space left on device."
        )
        assert result.details == {"path": "a.wav", "reason": "No space left on device"}
        assert cmd.exit_code_for(result) == 1
        assert not Path("a.wav").exists()

    def test_flux_file_is_created_on_the_first_frame(self, fake_sys, client):
        seen: list[bool] = []

        def check(i):
            if i == 1:
                seen.append(Path("a.wav").exists())

        client.speak_text_stream.return_value = _stream(
            [b"\x01\x00", b"\x02\x00"], check=check
        )
        result = _run(SpeakCommand(), client, out_file="a.wav", model="flux-alexis-en")
        assert seen == [True]
        assert isinstance(result, SpeakResult)
        assert Path("a.wav").read_bytes()[:4] == b"RIFF"

    def test_flux_cut_stream_leaves_no_file(self, fake_sys, client):
        client.speak_text_stream.return_value = _stream([b"\x01\x00"], fail_after=True)
        with pytest.raises(Exception, match="connection reset"):
            _run(SpeakCommand(), client, out_file="a.wav", model="flux-alexis-en")
        assert list(Path().iterdir()) == []

    def test_machine_format_result_keeps_the_040_keys(self, fake_sys, client):
        client.speak_text.return_value = _stream([b"abc"])
        result = _run(SpeakCommand(), client, out_file="a.wav")
        assert list(result.model_dump()) == [
            "status",
            "message",
            "output_path",
            "model",
            "bytes_written",
            "played",
        ]


# ── SIGTERM / SIGHUP mid-stream ─────────────────────────────────────────


class TestTerminateMidStream:
    @pytest.fixture
    def died(self, monkeypatch):
        calls: list[int] = []

        def fake_die(signum):
            calls.append(signum)
            raise SystemExit(128 + signum)

        monkeypatch.setattr(speak, "_die_by_signal", fake_die)
        return calls

    def _signalling_stream(self, sig, chunks):
        import signal as _signal

        def gen():
            for i, chunk in enumerate(chunks):
                yield chunk
                if i == 0:
                    os.kill(os.getpid(), sig)
            raise AssertionError("the signal should have stopped the stream")

        return gen(), _signal

    @pytest.mark.parametrize("signame", ["SIGTERM", "SIGHUP"])
    @pytest.mark.parametrize("model", ["aura-2-asteria-en", "flux-alexis-en"])
    @pytest.mark.parametrize("force", [False, True])
    def test_cleanup_runs_then_the_signal_ends_the_process(
        self, fake_sys, client, died, signame, model, force
    ):
        import signal

        sig = getattr(signal, signame)
        if force:
            Path("a.wav").write_bytes(b"OLD")
        stream, _ = self._signalling_stream(
            sig, [b"\x01\x00", b"\x02\x00", b"\x03\x00"]
        )
        if model.startswith("flux"):
            client.speak_text_stream.return_value = stream
        else:
            client.speak_text.return_value = stream
        before = signal.getsignal(sig)

        with pytest.raises(SystemExit) as info:
            _run(SpeakCommand(), client, out_file="a.wav", force=force, model=model)

        assert info.value.code == 128 + sig
        assert died == [sig]
        if force:
            assert Path("a.wav").read_bytes() == b"OLD"
            assert [p.name for p in Path().iterdir()] == ["a.wav"]
        else:
            assert list(Path().iterdir()) == []
        assert signal.getsignal(sig) == before  # handler restored

    def test_a_stalled_unwind_is_cut_short_by_the_second_signal(
        self, died, monkeypatch
    ):
        """If unwinding hangs (an unanswered close handshake), the
        re-delivered signal runs the cleanup and ends the process."""
        import signal

        monkeypatch.setattr(speak, "_SIGNAL_GRACE_S", 0.05)
        cleaned: list[bool] = []
        with (
            pytest.raises(SystemExit),
            speak._terminate_cleanly(lambda: cleaned.append(True)),
        ):
            try:
                os.kill(os.getpid(), signal.SIGTERM)
                time.sleep(1)  # unreachable: the handler raises
            except speak._Terminated:
                # "Stuck" unwinding: wait until the re-delivered signal ends
                # it (the handler raises SystemExit through this wait).
                threading.Event().wait(60)
        assert cleaned == [True]
        assert died == [signal.SIGTERM]

    def test_second_ctrl_c_cleans_up_and_cancels_like_the_first(self, monkeypatch):
        """The second Ctrl-C (or the timer) unwinds by KeyboardInterrupt
        again, so the usual cancel path, exit 2 and exit handlers follow;
        the hard-exit backstop is cancelled with the guard."""
        import signal

        exits: list[int] = []
        monkeypatch.setattr(speak.os, "_exit", exits.append)
        cleaned: list[bool] = []
        with (
            pytest.raises(KeyboardInterrupt),
            speak._terminate_cleanly(lambda: cleaned.append(True)),
        ):
            try:
                os.kill(os.getpid(), signal.SIGINT)
            except KeyboardInterrupt:
                os.kill(os.getpid(), signal.SIGINT)
        assert cleaned == [True]
        assert exits == []
        timers = [t for t in threading.enumerate() if isinstance(t, threading.Timer)]
        assert all(t.finished.is_set() for t in timers)  # cancelled
        assert signal.getsignal(signal.SIGINT) is signal.default_int_handler


# ── Round 3: ignored signals, special bits, critical sections, fd paths ──


class TestIgnoredSignalsStayIgnored:
    """nohup, background jobs and `trap ''` keep their dispositions."""

    @pytest.mark.parametrize("signame", ["SIGHUP", "SIGTERM", "SIGINT"])
    def test_sig_ign_is_left_alone(self, signame):
        import signal

        sig = getattr(signal, signame)
        before = signal.signal(sig, signal.SIG_IGN)
        try:
            with speak._terminate_cleanly(lambda: None):
                assert signal.getsignal(sig) is signal.SIG_IGN
                os.kill(os.getpid(), sig)  # ignored: nothing raises
            assert signal.getsignal(sig) is signal.SIG_IGN
        finally:
            signal.signal(sig, before)

    def test_a_handler_python_cant_restore_is_not_replaced(self, monkeypatch):
        import signal

        installed: list[int] = []
        real_signal = signal.signal
        real_getsignal = signal.getsignal
        monkeypatch.setattr(
            speak.signal,
            "getsignal",
            lambda sig: None if sig == signal.SIGHUP else real_getsignal(sig),
        )

        def recording(sig, handler):
            installed.append(sig)
            return real_signal(sig, handler)

        monkeypatch.setattr(speak.signal, "signal", recording)
        with speak._terminate_cleanly(lambda: None):
            pass
        assert signal.SIGHUP not in installed

    @pytest.mark.parametrize("model", ["aura-2-asteria-en", "flux-alexis-en"])
    @pytest.mark.parametrize("out_file", [True, False])
    def test_the_stream_completes_through_an_ignored_sighup(
        self, fake_sys, client, monkeypatch, model, out_file
    ):
        import signal

        written: list[bytes] = []
        fake_sys.stdout.buffer = Mock(write=written.append)

        def gen():
            yield b"\x01\x00"
            os.kill(os.getpid(), signal.SIGHUP)
            yield b"\x02\x00"

        if model.startswith("flux"):
            client.speak_text_stream.return_value = gen()
        else:
            client.speak_text.return_value = gen()
        before = signal.signal(signal.SIGHUP, signal.SIG_IGN)
        try:
            kw = {"out_file": "a.wav"} if out_file else {}
            result = _run(SpeakCommand(), client, model=model, **kw)
        finally:
            signal.signal(signal.SIGHUP, before)
        if out_file:
            assert isinstance(result, SpeakResult)
            assert Path("a.wav").exists()
        else:
            assert result is None
            assert b"\x02\x00" in written


class TestRound3Sinks:
    def test_replace_drops_setuid_setgid_and_sticky(self, tmp_path):
        path = tmp_path / "a.wav"
        path.write_bytes(b"old")
        path.chmod(0o4755)
        out = sinks.AudioFile(sinks.plan_target(str(path), overwrite=True))
        out.write(b"new")
        out.commit()
        assert stat.S_IMODE(path.stat().st_mode) == 0o755

    def test_a_signal_inside_the_rename_leaves_no_temp_file(
        self, tmp_path, monkeypatch
    ):
        """os.replace interrupted; abort still
        removes the temp file."""
        path = tmp_path / "a.wav"
        path.write_bytes(b"old")
        out = sinks.AudioFile(sinks.plan_target(str(path), overwrite=True))
        out.write(b"new")

        def boom(*a):
            raise speak._Terminated(15)

        monkeypatch.setattr(sinks.os, "replace", boom)
        with pytest.raises(speak._Terminated):
            out.commit()
        out.abort()
        assert [p.name for p in tmp_path.iterdir()] == ["a.wav"]
        assert path.read_bytes() == b"old"

    def test_signals_wait_for_the_end_of_a_critical_section(self):
        import signal

        seen: list[str] = []

        with (
            pytest.raises(speak._Terminated),
            speak._terminate_cleanly(lambda: None),
        ):
            with sinks.critical_section():
                os.kill(os.getpid(), signal.SIGTERM)
                seen.append("still inside")
            seen.append("unreachable")
        assert seen == ["still inside"]

    def test_stdout_target_is_checked_on_fd_1_without_reopening(
        self, tmp_path, monkeypatch
    ):
        f = tmp_path / "out.wav"
        f.write_bytes(b"")
        monkeypatch.setattr(sinks, "stdout_stat", lambda: os.stat(f))
        target = sinks.plan_target(str(f), overwrite=False)
        assert target.kind == "stdout"
        opened: list[str] = []
        real_open = os.open
        monkeypatch.setattr(
            sinks.os, "open", lambda p, *a: opened.append(p) or real_open(p, *a)
        )
        other = tmp_path / "other"
        other.write_bytes(b"")
        monkeypatch.setattr(sinks, "stdout_stat", lambda: os.stat(other))
        with pytest.raises(sinks.Exists):
            sinks.AudioFile(target).open()
        assert opened == []

    @pytest.mark.skipif(not os.path.isdir("/dev/fd"), reason="no /dev/fd")
    def test_fd_path_with_force_is_refused_before_the_request(self, tmp_path):
        victim = tmp_path / "v"
        victim.write_bytes(b"VICTIM")
        fd = os.open(victim, os.O_WRONLY | os.O_APPEND)
        try:
            path = f"/dev/fd/{fd}"
            refusal = sinks.plan_target(path, overwrite=True)
            assert refusal.reason == "unwritable"
            assert refusal.error.strerror == (
                "--force can't replace a file descriptor path"
            )
            assert sinks.plan_target(path, overwrite=False) == sinks.Refusal("exists")
        finally:
            os.close(fd)
        assert victim.read_bytes() == b"VICTIM"

    def test_stderr_is_refused(self, tmp_path, monkeypatch):
        f = tmp_path / "err.log"
        f.write_bytes(b"")
        monkeypatch.setattr(sinks, "_stderr_stat", lambda: os.stat(f))
        refusal = sinks.plan_target(str(f), overwrite=False)
        assert refusal.reason == "unwritable"
        assert refusal.error.strerror == (
            "it's stderr, which carries this command's messages"
        )


class TestSignalGuardEdges:
    """Windows re-delivery, the restore window, masks left blocked, bursts, and
    cleanup outside the guard."""

    def test_signal_inside_importlib_comes_again_later(self, monkeypatch):
        """A raise inside importlib's lock bookkeeping can leave the import
        lock held (a hang), so the guard lets the signal come again."""
        import signal

        from deepctl_core import signals

        later: list[int] = []
        inside = [True]
        monkeypatch.setattr(signals, "in_importlib", lambda frame: inside[0])
        monkeypatch.setattr(signals, "deliver_later", later.append)
        signals._deferred_since.clear()
        cleaned: list[bool] = []
        with (
            pytest.raises(speak._Terminated),
            speak._terminate_cleanly(lambda: cleaned.append(True)),
        ):
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(0.05)  # handled, deferred: no raise
            assert later == [signal.SIGTERM]
            inside[0] = False
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(1)  # unreachable: the handler raises
        assert cleaned == []  # first signal: unwinding, no forced cleanup
        signals._deferred_since.clear()

    @staticmethod
    def _importlib_frame():
        import importlib
        import sys

        seen = []

        class Finder:
            def find_spec(self, name, path=None, target=None):
                if name == "deepctl_speak_probe_xyz":
                    seen.append(sys._getframe(1))

        finder = Finder()
        sys.meta_path.insert(0, finder)
        try:
            with pytest.raises(ModuleNotFoundError):
                importlib.import_module("deepctl_speak_probe_xyz")
        finally:
            sys.meta_path.remove(finder)
        return seen[0]

    def test_guard_handler_defers_over_an_importlib_frame(self, monkeypatch):
        """The guard's own handler holds a signal back inside importlib."""
        import signal

        from deepctl_core import signals

        later: list[int] = []
        monkeypatch.setattr(signals, "deliver_later", later.append)
        signals._deferred_since.clear()
        frame = self._importlib_frame()
        with speak._terminate_cleanly(lambda: None):
            installed = signal.getsignal(signal.SIGTERM)
            installed(signal.SIGTERM, frame)  # no raise: deferred
        assert later == [signal.SIGTERM]
        signals._deferred_since.clear()

    def test_guard_handler_coalesces_a_nested_signal(self, monkeypatch):
        """The guard's handler uses core's coalescing, so a signal that
        lands while it runs isn't judged by the handler's own frame."""
        import signal
        import sys

        from deepctl_core import signals

        calls: list[int] = []

        def nested_then_defer(signum, frame):
            calls.append(signum)
            if len(calls) == 1:
                signal.getsignal(signal.SIGTERM)(signum, sys._getframe())
            return True

        monkeypatch.setattr(speak, "_defer_in_importlib", nested_then_defer)
        with speak._terminate_cleanly(lambda: None):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert calls == [signal.SIGTERM]  # the nested call didn't run the body
        assert signals._in_handler == []

    def test_windows_never_kills_its_own_process(self, monkeypatch):
        import signal

        own = os.getpid()
        killed: list[tuple[int, int]] = []
        monkeypatch.setattr(sinks.os, "kill", lambda p, s: killed.append((p, s)))
        interrupted: list[object] = []
        import _thread

        monkeypatch.setattr(_thread, "interrupt_main", lambda *a: interrupted.append(a))
        monkeypatch.setattr(speak.os, "name", "nt")
        speak._send_to_main(signal.SIGTERM)
        assert interrupted == [(signal.SIGTERM,)]
        with pytest.raises(SystemExit) as info:
            speak._end_by_signal(signal.SIGTERM)
        assert info.value.code == 128 + signal.SIGTERM
        assert not [k for k in killed if k[0] == own]

    def test_deferred_signal_is_raised_not_killed(self, monkeypatch):
        import signal

        def no_kill(pid, sig):
            raise AssertionError("os.kill on our own pid")

        monkeypatch.setattr(sinks.os, "kill", no_kill)
        seen: list[int] = []

        def h(signum, frame):
            if sinks.defer_signal(signum):
                return
            seen.append(signum)

        h._deepctl_defers = True
        before = signal.signal(signal.SIGHUP, h)
        try:
            with sinks.critical_section():
                sinks.defer_signal(signal.SIGHUP)
            assert seen == [signal.SIGHUP]
        finally:
            signal.signal(signal.SIGHUP, before)

    def test_most_severe_deferred_signal_wins(self):
        """TERM then INT during a section ends by TERM."""
        import signal

        seen: list[int] = []

        def h(signum, frame):
            if sinks.defer_signal(signum):
                return
            seen.append(signum)

        h._deepctl_defers = True
        saved = {s: signal.signal(s, h) for s in (signal.SIGINT, signal.SIGTERM)}
        try:
            with sinks.critical_section():
                sinks.defer_signal(signal.SIGTERM)
                sinks.defer_signal(signal.SIGINT)
            assert seen == [signal.SIGTERM]
        finally:
            for s, old in saved.items():
                signal.signal(s, old)

    @pytest.mark.parametrize("signame", ["SIGINT", "SIGTERM"])
    def test_a_signal_during_the_restore_reaches_the_restored_handler(
        self, monkeypatch, signame
    ):
        """Closed guard, original handlers back, no live timer, no
        cleanup; the signal goes to the handler that was there before."""
        import signal

        sig = getattr(signal, signame)
        outer: list[int] = []
        saved = {
            s: signal.signal(s, lambda n, f: outer.append(n))
            for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
        }
        mine = {s: signal.getsignal(s) for s in saved}
        real = signal.signal
        armed = [True]

        def wrapped(s, h):
            if armed[0] and getattr(h, "__name__", "") != "handler":
                armed[0] = False
                os.kill(os.getpid(), sig)  # arrives during the restore
            return real(s, h)

        cleaned: list[bool] = []
        try:
            monkeypatch.setattr(speak.signal, "signal", wrapped)
            with speak._terminate_cleanly(lambda: cleaned.append(True)):
                pass
            monkeypatch.setattr(speak.signal, "signal", real)

            def settled() -> bool:
                timers = [
                    t for t in threading.enumerate() if isinstance(t, threading.Timer)
                ]
                return bool(outer) and all(t.finished.is_set() for t in timers)

            # Wait for the signal and any timer, up to a generous deadline.
            deadline = time.monotonic() + 5.0
            while not settled() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert outer == [sig]
            assert cleaned == []
            assert {s: signal.getsignal(s) for s in saved} == mine
            live = [t for t in threading.enumerate() if isinstance(t, threading.Timer)]
            assert all(t.finished.is_set() for t in live)
        finally:
            for s, old in saved.items():
                real(s, old)

    @pytest.mark.skipif(
        not hasattr(__import__("signal"), "pthread_sigmask"), reason="POSIX"
    )
    def test_mask_is_restored_under_a_signal_flood(self):
        """The mask is always restored, even when a pending handler
        raises inside the section's own setup. Run in a child process: the
        flood can land on any bytecode."""
        import subprocess
        import sys as _sys

        code = """
import os, signal, threading, time
from deepctl_cmd_speak import sinks
class Boom(BaseException): pass
stop = threading.Event()
def h(signum, frame):
    if sinks.defer_signal(signum) or stop.is_set():
        return
    raise Boom
h._deepctl_defers = True
signal.signal(signal.SIGTERM, h)
go = threading.Event()
def flood():
    go.wait()
    while not stop.is_set():
        os.kill(os.getpid(), signal.SIGTERM); time.sleep(0.0002)
threading.Thread(target=flood, daemon=True).start()
stuck = raises = 0
go.set()
end = time.monotonic() + 1.5
while True:
    try:
        while time.monotonic() < end:
            try:
                with sinks.critical_section():
                    pass
            except Boom:
                raises += 1
                if signal.SIGTERM in signal.pthread_sigmask(signal.SIG_BLOCK, set()):
                    stuck += 1
                    signal.pthread_sigmask(signal.SIG_SETMASK, set())
        stop.set()
        break
    except Boom:
        raises += 1
time.sleep(0.05)
print(f"raises={raises} masks_left_blocked={stuck} critical={sinks._critical}")
"""
        p = subprocess.run(
            [_sys.executable, "-c", code], capture_output=True, text=True, timeout=60
        )
        assert p.returncode == 0, p.stderr[-2000:]
        assert "masks_left_blocked=0 critical=0" in p.stdout, p.stdout
        assert "raises=0" not in p.stdout

    def test_abort_outside_the_guard_still_removes_the_temp_file(self, tmp_path):
        """A Ctrl-C taken by another thread while abort() runs with no
        guard installed is held until the removal is done."""
        import signal

        path = tmp_path / "a.wav"
        path.write_bytes(b"OLD")
        out = sinks.AudioFile(sinks.plan_target(str(path), overwrite=True))
        out.write(b"x" * 100)
        real_close = os.close
        fd = out._fd

        def close_then_signal(n):
            real_close(n)
            if n == fd:
                t = threading.Thread(target=os.kill, args=(os.getpid(), signal.SIGINT))
                t.start()
                t.join()
                time.sleep(0.05)

        sinks.os.close = close_then_signal
        try:
            with pytest.raises(KeyboardInterrupt):
                out.abort()  # delivered after the section, not inside it
        finally:
            sinks.os.close = real_close
        assert [p.name for p in tmp_path.iterdir()] == ["a.wav"]
        assert path.read_bytes() == b"OLD"
        assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
