"""``dg help agent-mode``: the detection table with sources, what this
process matched, per-shell remedies and the trust rules. Offline text."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from deepctl_core import agents

from deepctl.help_topics import agent_mode

GOLDEN = Path(__file__).parent / "fixtures" / "help_agent_mode"
CONFIG = "~/.config/deepctl/config.yaml"
BASE_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "TERM": "xterm"}


@pytest.fixture(autouse=True)
def _no_devin(monkeypatch, tmp_path):
    monkeypatch.setattr(agents, "DEVIN_MARKER", "/opt/.devin")
    monkeypatch.setattr(agents, "_devin", lambda env: None)
    # The HardSignal rows hold the function itself; swap the row too.
    rows = tuple(
        signal._replace(match=lambda env: None)
        if signal.name == "/opt/.devin"
        else signal
        for signal in agents.HARD_SIGNALS
    )
    monkeypatch.setattr(agents, "HARD_SIGNALS", rows)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)


def _render(**env: str) -> str:
    return agent_mode.render("dg", CONFIG, {**BASE_ENV, **env}, [])


@pytest.mark.parametrize(
    ("golden", "env"),
    [
        ("claudecode.txt", {"CLAUDECODE": "1"}),
        ("person.txt", {}),
    ],
)
def test_golden(golden: str, env: dict[str, str]) -> None:
    expected = (GOLDEN / golden).read_text(encoding="utf-8")
    assert _render(**env) == expected


def test_every_row_and_source_is_listed() -> None:
    text = _render()
    for signal in agents.HARD_SIGNALS:
        assert signal.name in text
    for key, url in agents.SOURCES.items():
        assert f"  {key}" in text and url in text


@pytest.mark.parametrize(
    ("env", "row"),
    [
        ({"CLAUDECODE": "1"}, "CLAUDECODE "),
        ({"CLAUDECODE": " "}, "CLAUDECODE "),
        ({"AGENT": "goose"}, "AGENT "),
        ({"OR_SITE_URL": "https://aider.chat"}, "OR_APP_NAME / OR_SITE_URL"),
        ({"PATH": "/h/.pi/agent:/usr/bin"}, "PATH "),
    ],
)
def test_matched_row_says_yes(env: dict[str, str], row: str) -> None:
    here = {
        line[2:33].strip(): line[50:55].strip()
        for line in _render(**env).splitlines()
        if len(line) > 50 and line[2:33].strip() in _ROW_NAMES
    }
    assert here[row.strip()] == "yes"
    assert [name for name, mark in here.items() if mark == "yes"] == [row.strip()]


_ROW_NAMES = {signal.name for signal in agents.HARD_SIGNALS}


def test_remedies_name_the_matched_variable() -> None:
    text = _render(GEMINI_CLI="1")
    for line in (
        "sh, bash, zsh   unset GEMINI_CLI",
        "fish            set -e GEMINI_CLI",
        "PowerShell      Remove-Item Env:GEMINI_CLI",
        "cmd             set GEMINI_CLI=",
    ):
        assert f"    {line}" in text


def test_dg_agent_0_still_reports_the_marker_and_trust() -> None:
    text = " ".join(_render(CLAUDECODE="1", DG_AGENT="0").split())
    assert "Agent mode: off, because DG_AGENT=0." in text
    assert "An AI-agent signal is still set (CLAUDECODE)" in text
    assert "DG_AGENT=0 doesn't change this." in text
    assert f"chmod 600 {CONFIG}" in text


def test_dg_agent_1() -> None:
    assert "Agent mode: on, because DG_AGENT=1." in _render(DG_AGENT="1")


def test_session_rows() -> None:
    text = _render(PATH="/h/.pi/agent:/usr/bin")
    assert "Agent mode: on, because a .pi/agent entry in PATH matched." in text
    assert "unset NAME" in text


def test_ci_is_reported_as_automation() -> None:
    text = _render(CI="true")
    assert "Agent mode: off." in text
    assert "JSON output and no questions, because CI is set." in text


ENTRY = (
    "import sys; sys.argv[0] = '/opt/bin/dg'; "
    "from deepctl.main import main; sys.exit(main())"
)


def _env(home: Path, **extra: str) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("DEEPGRAM_", "PYTHON", "DEEPCTL_"))
        and k not in (*agents.HARD_ENV_VARS, "DG_AGENT")
    }
    env.update(
        HOME=str(home),
        XDG_CONFIG_HOME=str(home / ".config"),
        DEEPCTL_TELEMETRY_DISABLED="1",
        DEEPCTL_TELEMETRY_DSN="http://k@127.0.0.1:9/1",
        DEEPCTL_NO_UPDATE_CHECK="1",
        DEEPCTL_PROG_NAME="dg",
        PATH="/usr/local/bin:/usr/bin:/bin",
    )
    env.update(extra)
    return env


# Modules that only a background check or a connection would load.
CHECK_MODULES = (
    "httpx",
    "websockets",
    "deepctl_cmd_update.startup_check",
    "deepctl_cmd_update.plugin_update_check",
    "deepctl_cmd_skills.startup_check",
)
NETMODS = (
    "import atexit, sys\n"
    "atexit.register(lambda: sys.stderr.write('NETMODS=' + ','.join("
    f"sorted(m for m in {CHECK_MODULES!r} "
    "if m in sys.modules)) + '\\n'))\n" + ENTRY.replace("; ", "\n")
)


@pytest.mark.parametrize("args", [[], ["-o", "json"]])
def test_command_prints_text_offline(tmp_path: Path, args: list[str]) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", NETMODS, *args, "help", "agent-mode"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=_env(tmp_path, CLAUDECODE="1"),
        timeout=60,
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("AGENT MODE\n")
    assert "CONFIG FILES IN AGENT SESSIONS" in proc.stdout
    assert "Agent mode: on, because CLAUDECODE is set." in proc.stdout
    # No background check and no network stack: nothing to connect with.
    assert "NETMODS=\n" in proc.stderr, proc.stderr


@pytest.mark.skipif(os.name != "posix", reason="needs a pseudo-terminal")
def test_person_at_a_terminal_starts_no_checks(tmp_path: Path) -> None:
    """A person's terminal, with no opt-out: the update, plugin-update
    and skills checks would all run for another command, never for a
    help topic."""
    import pty

    env = _env(tmp_path, TERM="xterm")
    env.pop("DEEPCTL_NO_UPDATE_CHECK")
    from deepctl_cmd_update.startup_check import _CI_ENV_VARS

    for name in _CI_ENV_VARS:
        env.pop(name, None)
    controller, terminal = pty.openpty()
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", NETMODS, "help", "agent-mode"],
            stdin=terminal,
            stdout=terminal,
            stderr=terminal,
            env=env,
            cwd=tmp_path,
        )
        os.close(terminal)
        terminal = -1
        chunks = []
        while True:
            try:
                chunk = os.read(controller, 65536)
            except OSError:  # EIO: the child closed its side
                break
            if not chunk:
                break
            chunks.append(chunk)
        assert proc.wait(timeout=60) == 0
    finally:
        os.close(controller)
        if terminal >= 0:
            os.close(terminal)
    out = b"".join(chunks).decode("utf-8", "replace").replace("\r\n", "\n")
    assert "Agent mode: off." in out, out
    assert "NETMODS=\n" in out, out


def test_help_lists_the_topic(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", ENTRY, "help", "--help"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=_env(tmp_path),
        timeout=60,
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "agent-mode" in proc.stdout


def test_windows_gets_no_chmod(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setattr(sys, "platform", "win32")
    text = " ".join(_render(CLAUDECODE="1").split())
    assert "chmod" not in text
    assert "On Windows, dg doesn't check the file's owner or permissions." in text
