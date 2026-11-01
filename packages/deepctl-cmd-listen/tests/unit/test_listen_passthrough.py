"""Prerecorded ``listen`` output (0.5.0): the server's body for ``-o json``
and ``-o yaml``, and ``DEEPCTL_LISTEN_JSON=sdk``."""

from __future__ import annotations

import json
import sys
from typing import Any
from unittest.mock import Mock

import pytest
import yaml
from deepctl_cmd_listen import command as listen_cmd
from deepctl_cmd_listen.command import ListenCommand
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
