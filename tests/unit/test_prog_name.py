"""DEEPCTL_PROG_NAME counts only when it is a plain program name."""

from __future__ import annotations

import pytest

from deepctl import _cli
from deepctl.main import prog_name_from_env

PLAIN = ["dg", "deepgram", "deepctl", "my-dg", "dg.v2", "dg_2", "A1", "x" * 64]
NOT_PLAIN = [
    "",
    "   ",
    "/usr/local/bin/dg",
    "bin/dg",
    "..\\dg",
    "C:\\dg",
    ".",
    "..",
    ".dg",
    "-h",
    "--help",
    "_dg",
    "dg name",
    "dg\tx",
    "dg\nx",
    "dg\x1b[31m",
    "\x1b]0;title\x07",
    "dg\x00",
    "dg;rm -rf ~",
    "dg|x",
    "dg&x",
    "$(id)",
    "`id`",
    "dg>x",
    "dg'x",
    'dg"x',
    "dg*",
    "dg~",
    "[bold]dg[/bold]",
    "dgé",
    "dg\u202e",
    "x" * 65,
]


@pytest.mark.parametrize("name", PLAIN)
def test_plain_names_are_used(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv("DEEPCTL_PROG_NAME", f" {name}\n")
    assert prog_name_from_env() == name
    assert _cli.prog_name("fallback") == name


@pytest.mark.parametrize("name", NOT_PLAIN)
def test_anything_else_is_ignored(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    if "\x00" in name:
        pytest.skip("the environment can't hold a NUL")
    monkeypatch.setenv("DEEPCTL_PROG_NAME", name)
    assert prog_name_from_env() is None
    assert _cli.prog_name("fallback") == "fallback"
    assert _cli.prog_name() is None


def test_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPCTL_PROG_NAME", raising=False)
    assert prog_name_from_env() is None
    assert _cli.prog_name("dg") == "dg"
