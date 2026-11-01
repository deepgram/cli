"""Update notices: never in agent or automation sessions, plain text unless
a person's terminal allows colour, and the command named as invoked."""

from __future__ import annotations

import io
import sys
import threading
from unittest.mock import patch

import pytest
from deepctl_cmd_update import plugin_update_check, startup_check
from deepctl_core import output


@pytest.fixture(autouse=True)
def _person_session(monkeypatch):
    """Run as a person's session: the test runner has no terminal, which
    reads as automation and would skip every check."""
    from deepctl_cmd_update import plugin_update_check, startup_check

    monkeypatch.setattr(startup_check, "_is_agent", lambda: False)
    monkeypatch.setenv("DEEPCTL_PROG_NAME", "dg")


def _pending(module, result):
    module._result = result
    module._thread = threading.Thread(target=lambda: None)
    module._thread.start()
    # The notice prints only for a finished check.
    module._thread.join()


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _stderr(tty: bool) -> io.StringIO:
    return _Tty() if tty else io.StringIO()


@pytest.fixture
def person(monkeypatch):
    monkeypatch.setitem(output._output_config, "agentic", False)
    for name in ("NO_COLOR", "FORCE_COLOR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")


@pytest.mark.parametrize("module", [startup_check, plugin_update_check])
def test_no_check_in_an_agent_session(monkeypatch, module):
    monkeypatch.setattr(startup_check, "_is_agent", lambda: True)
    for name in ("_is_ci", "_is_oneshot"):
        monkeypatch.setattr(startup_check, name, lambda: False)
    monkeypatch.setattr(startup_check, "_stderr_is_terminal", lambda: True)
    module._thread = None
    if module is startup_check:
        module.check_and_notify(current_version="1.0.0")
    else:
        module.check_plugins_and_notify()
    assert module._thread is None


@pytest.mark.parametrize(
    ("tty", "env", "colour"),
    [
        (True, {}, True),
        (True, {"NO_COLOR": "1"}, False),
        (True, {"TERM": "dumb"}, False),
        (False, {}, False),
        (False, {"FORCE_COLOR": "1"}, True),
        (False, {"FORCE_COLOR": "1", "NO_COLOR": "1"}, False),
    ],
)
def test_update_notice_colour(person, monkeypatch, tty, env, colour):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("DEEPCTL_PROG_NAME", "dg")
    _pending(startup_check, {"latest": "2.0.0", "current": "1.0.0"})
    err = _stderr(tty)
    with patch.object(sys, "stderr", err):
        startup_check.print_pending_notification()
    text = err.getvalue()
    assert ("\033[" in text) is colour
    assert "run dg update to upgrade" in text.replace("\033[1m", "").replace(
        "\033[0m\033[33m", ""
    )


def test_plugin_notice_plain_without_terminal(person, monkeypatch):
    monkeypatch.setenv("DEEPCTL_PROG_NAME", "dg")
    _pending(
        plugin_update_check,
        {"updates": [{"name": "p", "current": "1", "latest": "2"}]},
    )
    err = _stderr(False)
    with patch.object(sys, "stderr", err):
        plugin_update_check.print_pending_plugin_notifications()
    assert err.getvalue() == (
        "Plugin update available: p 1 → 2  —  run dg plugin update p to upgrade\n"
    )
