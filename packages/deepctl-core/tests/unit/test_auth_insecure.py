"""DEEPGRAM_CLI_INSECURE never turns off TLS verification in an AI agent
session."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from deepctl_core import auth as auth_module
from deepctl_core import hosts, output
from deepctl_core.auth import AuthManager


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(hosts, "_reported", set())
    monkeypatch.setenv("DEEPGRAM_CLI_INSECURE", "1")
    monkeypatch.setattr(output.stderr_console, "_width", 400)


def verify_flag(config) -> bool:
    with patch.object(auth_module.httpx, "Client") as client:
        AuthManager(config)
    return client.call_args.kwargs["verify"]


def test_ignored_with_one_warning_for_agents(monkeypatch, capsys):
    monkeypatch.setattr(output, "is_ai_agent", lambda: True)
    assert verify_flag(object()) is True
    assert verify_flag(object()) is True
    assert capsys.readouterr().err.count("Ignoring DEEPGRAM_CLI_INSECURE") == 1


def test_unchanged_outside_agent_sessions(monkeypatch, capsys):
    monkeypatch.setattr(output, "is_ai_agent", lambda: False)
    assert verify_flag(object()) is False
    assert "DEEPGRAM_CLI_INSECURE" not in capsys.readouterr().err
