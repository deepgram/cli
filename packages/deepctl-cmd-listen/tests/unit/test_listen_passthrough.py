"""Prerecorded ``listen`` output (0.5.0): the server's body for ``-o json``,
``-o yaml`` and ``-o text``, ``DEEPCTL_LISTEN_JSON=sdk``, and agent-mode
compaction."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
import yaml
from deepctl_cmd_listen import command as listen_cmd
from deepctl_cmd_listen.command import COMPACT_THRESHOLD, ListenCommand
from deepctl_core import Config, login_state, output, transport

SPARSE = (
    b'{"metadata":{"request_id":"r-1","created":"2026-10-31T01:02:03.456Z",'
    b'"duration":1.5},"results":{"channels":[{"alternatives":[{"transcript":'
    b'"hello world","words":[{"word":"hello","start":0.1,"end":0.5,'
    b'"speaker":0,"punctuated_word":"Hello"}]}]}]}}'
)


def big_body(words: int = 4000) -> bytes:
    ws = [
        {"word": f"w{i}", "start": i * 0.3, "end": i * 0.3 + 0.2, "speaker": 0}
        for i in range(words)
    ]
    text = " ".join(w["word"] for w in ws)
    return json.dumps(
        {
            "metadata": {"request_id": "r-big", "duration": 12.5},
            "results": {
                "channels": [{"alternatives": [{"transcript": text, "words": ws}]}]
            },
        }
    ).encode()


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setitem(output._output_config, "agentic", False)
    monkeypatch.setitem(output._output_config, "quiet", False)
    monkeypatch.setitem(output._output_config, "format", "json")
    monkeypatch.setitem(output._output_config, "format_source", "explicit")
    monkeypatch.setattr(listen_cmd, "is_ai_agent", lambda: False)
    monkeypatch.setattr(transport, "prog", lambda: "dg")
    monkeypatch.setattr(listen_cmd, "_prog", lambda: "dg")
    monkeypatch.setattr(listen_cmd, "_listen_json_warned", False)
    monkeypatch.delenv("DEEPCTL_LISTEN_JSON", raising=False)
    monkeypatch.setattr(login_state, "state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(
        "tempfile.gettempdir", lambda: str(tmp_path / "tmp"), raising=True
    )
    (tmp_path / "tmp").mkdir()


def fmt(monkeypatch, value: str, source: str = "explicit") -> None:
    monkeypatch.setitem(output._output_config, "format", value)
    monkeypatch.setitem(output._output_config, "format_source", source)


def rest_client(body: bytes = SPARSE) -> Mock:
    client = Mock()
    client.transport.backend.name = "rest"
    client.transcribe_body.return_value = body
    client.transcribe_file.return_value = {"sdk": True}
    return client


def run(client: Mock, *, full_output: bool = False, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "is_url": False,
        "model": "nova-3",
        "language": "en-US",
        "api_version": 1,
        "diarize": False,
        "smart_format": True,
        "punctuate": True,
        "summarize": False,
        "topics": False,
        "sentiment": False,
        "redact": (),
        "numerals": False,
        "save_to": None,
        "probe": False,
        "no_validate": True,
        "caption_format": None,
        "config": Mock(spec=Config),
    }
    kwargs.update(overrides)
    command = ListenCommand()
    command._full_output = full_output
    result = command._prerecorded(client, "audio.wav", **kwargs)
    command.output_result(result, Mock(spec=Config))
    return command, result


# ── The server's body ─────────────────────────────────────────────────


def test_json_prints_the_body_byte_for_byte(capsysbinary):
    client = rest_client()
    _, result = run(client)
    assert capsysbinary.readouterr().out == SPARSE + b"\n"
    client.transcribe_file.assert_not_called()
    # Parsed to check it, printed as read.
    assert result.full_result == json.loads(SPARSE)


def test_output_is_always_the_body_plus_one_newline(capsysbinary):
    run(rest_client(SPARSE + b"\n"))
    assert capsysbinary.readouterr().out == SPARSE + b"\n\n"


def test_implied_json_is_the_same_body(monkeypatch, capsysbinary):
    fmt(monkeypatch, "json", "soft")
    run(rest_client())
    assert capsysbinary.readouterr().out == SPARSE + b"\n"


def test_sdk_env_restores_the_model_dump(monkeypatch, capsysbinary):
    monkeypatch.setenv("DEEPCTL_LISTEN_JSON", "sdk")
    client = rest_client()
    run(client)
    client.transcribe_body.assert_not_called()
    out = capsysbinary.readouterr().out
    assert json.loads(out) == {"sdk": True}
    assert out.endswith(b"}\n") and out.startswith(b"{\n  ")


def test_unknown_env_value_warns_once_and_is_ignored(monkeypatch, capsysbinary):
    monkeypatch.setenv("DEEPCTL_LISTEN_JSON", "fast")
    run(rest_client())
    run(rest_client())
    captured = capsysbinary.readouterr()
    assert captured.out == (SPARSE + b"\n") * 2
    assert captured.err.count(b"DEEPCTL_LISTEN_JSON=fast") == 1


def test_another_backend_keeps_the_sdk_path(capsysbinary):
    client = rest_client()
    client.transport.backend.name = "sagemaker"
    run(client)
    client.transcribe_body.assert_not_called()
    assert json.loads(capsysbinary.readouterr().out) == {"sdk": True}


def test_table_output_keeps_the_sdk_path(monkeypatch, capsys):
    fmt(monkeypatch, "default", "default")
    client = rest_client()
    client.transcribe_file.return_value = json.loads(SPARSE)
    run(client)
    client.transcribe_body.assert_not_called()
    assert "hello world" in capsys.readouterr().out


def test_yaml_is_the_body_as_yaml(monkeypatch, capsysbinary):
    fmt(monkeypatch, "yaml")
    run(rest_client())
    out = capsysbinary.readouterr().out
    assert yaml.safe_load(out) == json.loads(SPARSE)
    assert out.startswith(b"metadata:\n")


def test_text_is_only_the_transcript(monkeypatch, capsysbinary):
    fmt(monkeypatch, "text")
    run(rest_client())
    assert capsysbinary.readouterr().out == b"hello world\n"


def test_text_with_diarize_labels_speakers(monkeypatch, capsysbinary):
    fmt(monkeypatch, "text")
    run(rest_client(), diarize=True)
    assert capsysbinary.readouterr().out.startswith(b"[Speaker 0]")


def test_captions_still_print_under_json(capsys):
    run(rest_client(), caption_format="srt")
    assert capsys.readouterr().out.startswith("1\n00:00:00,100 --> ")


def test_save_to_parses_the_body(tmp_path, capsysbinary):
    target = tmp_path / "t.txt"
    run(rest_client(), save_to=str(target))
    assert target.read_text().strip() == "hello world"
    assert capsysbinary.readouterr().out == SPARSE + b"\n"


def test_a_closed_reader_ends_quietly(monkeypatch):
    command = ListenCommand()
    stream = Mock()
    stream.buffer.write.side_effect = BrokenPipeError()
    monkeypatch.setattr(listen_cmd, "_silence_stdout", lambda: None)
    monkeypatch.setattr(sys, "stdout", stream)
    command._write_stdout(b"{}\n")
    assert command._output_failed is False


def test_a_full_disk_is_output_unwritable(monkeypatch, capsys):
    command = ListenCommand()
    stream = Mock()
    stream.buffer.write.side_effect = OSError(28, "No space left on device")
    monkeypatch.setattr(listen_cmd, "_silence_stdout", lambda: None)
    monkeypatch.setattr(sys, "stdout", stream)
    command._write_stdout(b"{}\n")
    assert command._output_failed is True
    assert command.exit_code_for(None) == 1


def test_a_body_that_is_not_an_object_is_unreadable(monkeypatch):
    fmt(monkeypatch, "yaml")
    with pytest.raises(transport.TransportError) as err:
        run(rest_client(b"<html>portal</html>"))
    assert err.value.code == "network_unavailable"


# ── Compaction ────────────────────────────────────────────────────────


def agent(monkeypatch) -> None:
    fmt(monkeypatch, "json", "hard_agent")
    monkeypatch.setattr(listen_cmd, "is_ai_agent", lambda: True)


def test_large_result_under_an_agent_is_compacted(monkeypatch, capsysbinary, tmp_path):
    agent(monkeypatch)
    body = big_body()
    assert len(body) + 1 > COMPACT_THRESHOLD
    run(rest_client(body))
    captured = capsysbinary.readouterr()
    assert len(captured.out) <= 4096
    compact = json.loads(captured.out)
    assert list(compact) == [
        "schema_version",
        "status",
        "truncated",
        "full_result_path",
        "full_result_bytes",
        "transcript_path",
        "transcript_chars",
        "transcript_preview",
        "request_id",
        "duration_s",
        "hint",
        "next_step",
    ]
    assert compact["truncated"] is True and compact["next_step"] is None
    assert compact["request_id"] == "r-big" and compact["duration_s"] == 12.5
    full = compact["full_result_path"]
    assert Path(full).read_bytes() == body + b"\n"
    assert compact["full_result_bytes"] == len(body) + 1
    assert stat.S_IMODE(os.stat(full).st_mode) == 0o600
    text = json.loads(body)["results"]["channels"][0]["alternatives"][0]["transcript"]
    assert Path(compact["transcript_path"]).read_text() == text + "\n"
    assert compact["transcript_chars"] == len(text)
    assert len(compact["transcript_preview"].encode()) <= 2048
    assert text.startswith(compact["transcript_preview"])
    assert full.startswith(str((tmp_path / "state" / "results").resolve()))
    notice = [line for line in captured.err.decode().splitlines() if "saved to" in line]
    assert len(notice) == 1
    assert notice[0].startswith("INFO: The result is 299.0 KB so it was saved to ")
    assert full in notice[0] and "--full-output" in notice[0]


def test_preview_never_splits_a_code_point(monkeypatch, capsysbinary):
    agent(monkeypatch)
    text = "é" * 40000
    body = json.dumps(
        {"results": {"channels": [{"alternatives": [{"transcript": text}]}]}},
        ensure_ascii=False,
    ).encode()
    run(rest_client(body))
    preview = json.loads(capsysbinary.readouterr().out)["transcript_preview"]
    assert preview == "é" * 1024


@pytest.mark.parametrize(
    "case",
    ["small", "explicit", "soft", "full_output", "regular_file", "no_agent", "text"],
)
def test_compaction_boundaries(monkeypatch, capsysbinary, case):
    agent(monkeypatch)
    body = big_body()
    full_output = False
    if case == "small":
        body = SPARSE
    elif case == "explicit":
        fmt(monkeypatch, "json", "explicit")
    elif case == "soft":
        fmt(monkeypatch, "json", "soft")
        monkeypatch.setattr(listen_cmd, "is_ai_agent", lambda: False)
    elif case == "full_output":
        full_output = True
    elif case == "regular_file":
        monkeypatch.setattr(
            "deepctl_core.result_store.stdout_is_file_or_null", lambda: True
        )
    elif case == "no_agent":
        monkeypatch.setattr(listen_cmd, "is_ai_agent", lambda: False)
    elif case == "text":
        fmt(monkeypatch, "text", "explicit")
    run(rest_client(body), full_output=full_output)
    out = capsysbinary.readouterr().out
    if case == "text":
        assert not out.startswith(b"{")
    else:
        assert out == body + b"\n"


def test_exactly_the_threshold_is_not_compacted(monkeypatch, capsysbinary):
    agent(monkeypatch)
    body = b'{"x":"' + b"a" * (COMPACT_THRESHOLD - 9) + b'"}'
    assert len(body) + 1 == COMPACT_THRESHOLD
    run(rest_client(body))
    assert capsysbinary.readouterr().out == body + b"\n"


def test_sdk_shape_is_compacted_too(monkeypatch, capsysbinary):
    agent(monkeypatch)
    monkeypatch.setenv("DEEPCTL_LISTEN_JSON", "sdk")
    client = rest_client()
    client.transcribe_file.return_value = json.loads(big_body())
    run(client)
    compact = json.loads(capsysbinary.readouterr().out)
    saved = Path(compact["full_result_path"]).read_bytes()
    assert saved.startswith(b"{\n  ") and saved.endswith(b"}\n")


def test_nowhere_to_save_is_state_unwritable(monkeypatch, capsysbinary, tmp_path):
    from deepctl_core import result_store

    agent(monkeypatch)

    def fail(*a: Any, **kw: Any) -> Any:
        raise result_store.ResultStoreError(tmp_path / "state")

    monkeypatch.setattr(result_store, "save", fail)
    command, _ = run(rest_client(big_body()))
    captured = capsysbinary.readouterr()
    payload = json.loads(captured.out)
    assert payload["code"] == "state_unwritable"
    assert payload["next_step"] is None
    assert payload["details"] == {"dir": str(tmp_path / "state")}
    assert "too large to print here" in payload["error"]
    assert b"word" not in captured.out  # never the full result
    assert command.exit_code_for(None) == 1


def test_root_full_output_is_read_from_the_context():
    import click
    from deepctl_core import result_store

    @click.command()
    @click.pass_context
    def probe(ctx: click.Context) -> None:
        ctx.meta[result_store.FULL_OUTPUT_META] = True
        assert listen_cmd._root_full_output() is True

    probe.main([], standalone_mode=False)
    assert listen_cmd._root_full_output() is False


@pytest.mark.parametrize(
    ("n", "text"), [(65537, "65.5 KB"), (999_999, "1000.0 KB"), (7_658_261, "7.7 MB")]
)
def test_human_size(n, text):
    assert listen_cmd._human_bytes(n) == text
