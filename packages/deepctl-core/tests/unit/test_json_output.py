"""Machine JSON off a terminal: one plain write with the text rich printed
(``json.dumps(indent=2, ensure_ascii=False)`` plus a newline)."""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from typing import Any

import pytest
from deepctl_core import output
from deepctl_core.base_command import BaseCommand
from rich.console import Console

DATA = {
    "name": "café [ci] runner",
    "items": [1, 2.5, None, True],
    "when": datetime(2026, 10, 31, 1, 2, 3, tzinfo=timezone.utc),
}


class _Cmd(BaseCommand):
    name = "probe"
    help = "probe"

    def handle(self, *a: Any, **kw: Any) -> Any:
        return None


def rich_text(data: Any) -> str:
    buf = io.StringIO()
    Console(file=buf, force_terminal=False, width=80).print_json(
        json.dumps(data, indent=2, default=str)
    )
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _plain(monkeypatch):
    monkeypatch.setitem(output._output_config, "quiet", False)
    monkeypatch.setitem(output._output_config, "format", "json")


def test_base_command_json_matches_what_rich_printed(capsys):
    plain = {k: (str(v.isoformat()) if k == "when" else v) for k, v in DATA.items()}
    _Cmd().output_result(DATA, None)  # type: ignore[arg-type]
    out = capsys.readouterr().out
    assert out == json.dumps(plain, indent=2, ensure_ascii=False) + "\n"
    assert out == rich_text(plain)


def test_base_command_json_respects_quiet(monkeypatch, capsys):
    monkeypatch.setitem(output._output_config, "quiet", True)
    _Cmd().output_result({"a": 1}, None)  # type: ignore[arg-type]
    assert capsys.readouterr().out == ""


def test_print_output_json_matches_what_rich_printed(capsys):
    data = {"a": "café", "b": [1, {"c": None}]}
    output.print_output(data, "json")
    out = capsys.readouterr().out
    assert out == json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    assert out == rich_text(data)


def test_print_output_json_string(capsys):
    output.print_output('{"a": 1}', "json")
    output.print_output("not json", "json")
    assert capsys.readouterr().out == '{\n  "a": 1\n}\nnot json\n'
