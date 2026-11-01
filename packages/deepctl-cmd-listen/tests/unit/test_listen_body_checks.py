"""A 2xx body that isn't a whole JSON object is an error under every
prerecorded output format, ``-o json`` included: never printed, exit 1."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import Mock

import pytest
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


def _decoded_by_httpx(content: bytes, coding: str | None = None) -> bytes:
    """What the raw path returns for ``content`` sent with a complete
    transfer: httpx's gzip decoder doesn't notice a stream cut short."""
    import httpx

    headers = {"Content-Encoding": coding} if coding else {}
    return httpx.Response(200, content=content, headers=headers).read()


def _gzip_cut() -> bytes:
    import gzip

    data = gzip.compress(big_body(2000))
    return _decoded_by_httpx(data[: len(data) // 2], "gzip")


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"<html><body>Sign in to the network</body></html>", id="html"),
        pytest.param(_gzip_cut(), id="gzip-cut-matching-length-or-until-close"),
        pytest.param(big_body(2000)[:5000], id="identity-cut-until-close"),
        pytest.param(SPARSE[:20] + "é".encode()[:1], id="mid-utf8"),
        pytest.param(SPARSE[:-1] + b"\xff}", id="invalid-utf8"),
        pytest.param(b"[1,2]", id="top-level-array"),
        pytest.param(b"", id="empty"),
    ],
)
def test_json_never_prints_a_body_that_is_not_an_object(capsysbinary, body):
    with pytest.raises(transport.TransportError) as err:
        run(rest_client(body))
    assert err.value.code == "network_unavailable"
    assert err.value.messages == transport.NETWORK_UNREADABLE
    assert capsysbinary.readouterr().out == b""


def test_a_whole_gzip_body_still_prints_byte_for_byte(capsysbinary):
    import gzip

    body = big_body(2000)
    run(rest_client(_decoded_by_httpx(gzip.compress(body), "gzip")))
    assert capsysbinary.readouterr().out == body + b"\n"
