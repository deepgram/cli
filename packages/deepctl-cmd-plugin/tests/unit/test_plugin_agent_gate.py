"""Plugins run in every later dg command, so an AI agent session can't
install, update or remove them. Scripts and CI without an agent marker can."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from deepctl_cmd_plugin import command as plugin_module
from deepctl_cmd_plugin.command import PluginCommand
from deepctl_core import output


@pytest.fixture(autouse=True)
def _wide(monkeypatch):
    monkeypatch.setattr(output.stderr_console, "_width", 400)


HANDLERS = [
    ("_handle_install", {"package": "deepctl-plugin-evil"}),
    ("_handle_update", {"package": "deepctl-plugin-evil"}),
    ("_handle_remove", {"package": "deepctl-plugin-evil", "yes": True}),
]


@pytest.mark.parametrize(
    ("handler", "kwargs"),
    [
        ("_handle_install", {"package": "deepctl-plugin-evil"}),
        ("_handle_update", {"package": "deepctl-plugin-evil"}),
        ("_handle_remove", {"package": "deepctl-plugin-evil", "yes": True}),
    ],
)
def test_refused_in_agent_sessions(monkeypatch, capsys, handler, kwargs):
    monkeypatch.setattr(plugin_module, "is_ai_agent", lambda: True)
    command = PluginCommand()
    command.install_plugin = MagicMock()
    command.remove_plugin = MagicMock()
    with pytest.raises(SystemExit) as exc:
        getattr(command, handler)(MagicMock(), MagicMock(), MagicMock(), **kwargs)
    assert exc.value.code == 1
    command.install_plugin.assert_not_called()
    command.remove_plugin.assert_not_called()
    err = capsys.readouterr().err
    assert "doesn't run in an AI agent session. Nothing was changed." in err
    assert "A person must run this command. If you are an AI agent, ask the user." in err


@pytest.mark.parametrize(("handler", "kwargs"), HANDLERS)
def test_scripts_and_ci_without_an_agent_marker_may_run(
    monkeypatch, capsys, handler, kwargs
):
    """A non-TTY run or CI=true alone isn't refused; only hard AI-agent
    signals are."""
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(output, "is_agentic", lambda: True)
    monkeypatch.setattr(plugin_module, "is_ai_agent", lambda: False)
    command = PluginCommand()
    command.install_plugin = MagicMock()
    command.remove_plugin = MagicMock()
    command._maybe_update_skills = MagicMock()
    getattr(command, handler)(MagicMock(), MagicMock(), MagicMock(), **kwargs)
    assert "AI agent session" not in capsys.readouterr().err
    assert command.install_plugin.called or command.remove_plugin.called
