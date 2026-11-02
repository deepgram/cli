"""Text that depends on the OS: how to end stdin, where mic access lives.

``sys.platform`` is patched, so this proves the wording each OS gets, not
the console's own behaviour.
"""

from __future__ import annotations

import sys

import pytest
from deepctl_cmd_listen import command
from deepctl_cmd_listen import messages as msg


@pytest.mark.parametrize(
    ("platform", "keys"),
    [("win32", "Ctrl+Z, then Enter,"), ("linux", "Ctrl+D"), ("darwin", "Ctrl+D")],
)
def test_finish_keys(monkeypatch, platform, keys):
    monkeypatch.setattr(sys, "platform", platform)
    assert command._finish_keys() == keys


@pytest.mark.parametrize(
    ("platform", "where"),
    [
        ("darwin", "macOS: System Settings → Privacy & Security → Microphone"),
        ("win32", "Windows: Settings → Privacy & security → Microphone"),
        ("linux", None),
    ],
)
def test_mic_denied_text(monkeypatch, platform, where):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(command, "_agentic", False)
    monkeypatch.setattr(command, "agent_mode", lambda: False)
    monkeypatch.setattr(command, "_prog", lambda: "dg")
    lines = command._mic_denied_text().split("\n")
    assert lines[0] == "dg can't use the microphone: access was denied."
    assert lines[1] == msg.H_MIC_DENIED
    if where is None:
        assert len(lines) == 2
    else:
        assert lines[2].startswith(where)
    assert "PortAudio" not in "\n".join(lines)


def test_mic_denied_text_for_an_agent(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(command, "agent_mode", lambda: True)
    assert command._mic_denied_text().split("\n")[1] == msg.A_MIC_DENIED
