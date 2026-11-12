"""Live listen: a failed stdout write is output_unwritable, not a network
error, and the stdin catch-all only takes read errors."""

from __future__ import annotations

import errno
import os
from unittest.mock import Mock

import pytest
from deepctl_cmd_listen import command as listen_cmd
from deepctl_cmd_listen import live
from deepctl_cmd_listen.command import ListenCommand
from deepctl_cmd_listen.models import ListenCodedError, ListenResult
from deepctl_core import Config


@pytest.fixture
def command(monkeypatch):
    monkeypatch.setattr(listen_cmd, "agent_mode", lambda: True)
    return ListenCommand()


def _finish(command, outcome, **kw):
    return command._finish_live(
        outcome,
        source="stdin",
        model="nova-3",
        language="en",
        diarize=False,
        transcript_acc=kw.get("acc", ["hello", "world"]),
        caption_writer=None,
        caption_format=None,
        save_to=kw.get("save_to"),
        v2_state=kw.get("v2_state"),
    )


def test_output_outcome_is_output_unwritable_with_the_kept_finals(command, capsys):
    outcome = live.LiveOutcome(
        status="error",
        kind="output",
        error="Couldn't write the output to stdout: No space left on device.",
        os_error="No space left on device",
    )
    result = _finish(command, outcome)
    assert isinstance(result, ListenCodedError)
    assert result.code == "output_unwritable"
    assert (
        result.error == "Couldn't write the output to stdout: No space left on device."
    )
    assert result.hint == (
        "If you chose this destination, choose another one. Otherwise tell the "
        "user dg couldn't write its output (No space left on device), and don't "
        "retry until it's fixed."
    )
    assert result.next_step is None
    assert result.details == {
        "path": "-",
        "reason": "No space left on device",
        "transcript": "hello world",
        "transcript_chars": 11,
    }
    assert command.exit_code_for(result) == 1
    err = capsys.readouterr().err
    assert "network" not in err.lower()


def test_person_gets_the_human_hint(command, monkeypatch):
    monkeypatch.setattr(listen_cmd, "agent_mode", lambda: False)
    monkeypatch.setattr(listen_cmd, "_agentic", False)
    outcome = live.LiveOutcome(
        status="error", kind="output", os_error="Input/output error"
    )
    result = _finish(command, outcome)
    assert result.hint == (
        "Choose another destination, or fix this one (free space, permissions), "
        "then try again."
    )


def test_stdin_catch_all_takes_read_errors_only(command):
    read = _finish(
        command,
        live.LiveOutcome(
            status="error", kind="read", error="Couldn't read audio from stdin: x."
        ),
    )
    assert read.code == "invalid_argument"
    assert read.next_step == "dg listen --help"
    other = _finish(
        command, live.LiveOutcome(status="error", kind="mystery", error="boom")
    )
    assert other.code == "network_unavailable"
    assert other.next_step is None


def test_flux_flush_failure_is_output_unwritable(command, monkeypatch):
    silenced = []
    monkeypatch.setattr(live, "silence_stdout", lambda: silenced.append(True))

    def flush(*a, **kw):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(command, "_flush_v2", flush)
    result = _finish(command, live.LiveOutcome(), v2_state={"order": [], "turns": {}})
    assert result.code == "output_unwritable"
    assert silenced == [True]


def test_final_result_write_failure_is_reported_and_exits_1(
    command, monkeypatch, capsys
):
    silenced = []
    monkeypatch.setattr(live, "silence_stdout", lambda: silenced.append(True))
    monkeypatch.setattr(listen_cmd, "get_output_format", lambda: "json")
    stdout = Mock()
    stdout.write.side_effect = OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(listen_cmd.sys, "stdout", stdout)
    result = ListenResult(status="success", mode="live", transcript="hi there")

    command.output_result(result, Mock(spec=Config))

    assert silenced == [True]
    assert command.exit_code_for(result) == 1
    err = capsys.readouterr().err
    assert "Couldn't write the output to stdout: No space left on device." in err


def test_final_result_epipe_stays_quiet(command, monkeypatch, capsys):
    monkeypatch.setattr(live, "silence_stdout", lambda: None)
    monkeypatch.setattr(listen_cmd, "get_output_format", lambda: "json")
    stdout = Mock()
    stdout.write.side_effect = BrokenPipeError(errno.EPIPE, "Broken pipe")
    monkeypatch.setattr(listen_cmd.sys, "stdout", stdout)
    result = ListenResult(status="success", mode="live", transcript="hi")

    command.output_result(result, Mock(spec=Config))

    assert command.exit_code_for(result) == 0
    assert capsys.readouterr().err == ""


def _final_result_einval(command, monkeypatch, fd):
    """The final result's write fails with EINVAL on Windows; fd 1 is fd."""
    monkeypatch.setattr(listen_cmd.sys, "platform", "win32")
    monkeypatch.setattr(live, "silence_stdout", lambda: None)
    monkeypatch.setattr(listen_cmd, "get_output_format", lambda: "json")
    stdout = Mock()
    stdout.fileno.return_value = fd
    stdout.write.side_effect = OSError(errno.EINVAL, "Invalid argument")
    monkeypatch.setattr(listen_cmd.sys, "stdout", stdout)
    result = ListenResult(status="success", mode="live", transcript="hi")
    command.output_result(result, Mock(spec=Config))
    return command.exit_code_for(result)


def test_final_result_einval_on_a_windows_pipe_stays_quiet(
    command, monkeypatch, capsys
):
    """Windows reports a pipe whose reader closed as EINVAL, not EPIPE."""
    r, w = os.pipe()
    try:
        assert _final_result_einval(command, monkeypatch, w) == 0
    finally:
        os.close(r)
        os.close(w)
    assert capsys.readouterr().err == ""


def test_final_result_einval_on_a_windows_file_exits_1(
    command, monkeypatch, capsys, tmp_path
):
    fd = os.open(tmp_path / "out", os.O_WRONLY | os.O_CREAT)
    try:
        assert _final_result_einval(command, monkeypatch, fd) == 1
    finally:
        os.close(fd)
    assert "Couldn't write the output to stdout: Invalid argument." in (
        capsys.readouterr().err
    )


def test_coded_error_write_failure_silences_stdout(command, monkeypatch):
    silenced = []
    monkeypatch.setattr(live, "silence_stdout", lambda: silenced.append(True))
    monkeypatch.setattr(
        listen_cmd.BaseCommand,
        "output_result",
        Mock(side_effect=OSError(errno.ENOSPC, "No space left on device")),
    )
    result = ListenCodedError(error="x", code="server_error", hint="h")
    command.output_result(result, Mock(spec=Config))
    assert silenced == [True]


@pytest.mark.parametrize("exc", [OSError(errno.ENOSPC, "full"), BrokenPipeError()])
def test_verbose_stderr_failure_is_not_a_stdout_failure(command, monkeypatch, exc):
    """An INFO line that can't reach stderr leaves stdout and the
    session alone."""
    command._echo = False
    command._verbose = True
    monkeypatch.setattr(listen_cmd, "print_info", Mock(side_effect=exc))
    command._print_final("hello")  # does not raise


@pytest.mark.skipif(
    not os.environ.get("DG_ERROR_CODES_JSON"),
    reason="DG_ERROR_CODES_JSON names no error catalogue file",
)
def test_strings_match_catalogue():
    """Listen's messages, the output_unwritable texts shared with speak
    included, match the error catalogue."""
    import json
    import re
    from pathlib import Path

    from deepctl_cmd_listen import messages as msg

    catalogue = json.loads(Path(os.environ["DG_ERROR_CODES_JSON"]).read_text())
    for name, text in vars(msg).items():
        if re.fullmatch(r"[EHAD]_[A-Z0-9_]+", name):
            assert catalogue["strings"][name.replace("_", "-")] == text, name
    message = catalogue["codes"]["output_unwritable"]["messages"][0]
    assert (message["id"], message["hint_human"], message["hint_agent"]) == (
        "E-OUTPUT-UNWRITABLE",
        "H-OUTPUT-UNWRITABLE",
        "A-OUTPUT-UNWRITABLE",
    )
