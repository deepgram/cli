"""Agent detection: the table, its value rules, DG_AGENT, and the snapshots
of human terminals that must never count as an AI agent."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from deepctl_core import agents, output

FIXTURES = Path(__file__).parent / "fixtures" / "agent_env"

# Each variable alone, with a value that sets it, and the agent it names.
ROWS = [
    ("AI_AGENT", "github_copilot_vscode_agent", "github_copilot_vscode_agent"),
    ("AGENT", "amp", "amp"),
    ("AGENT", "goose", "goose"),
    ("GOOSE_TERMINAL", "1", "goose"),
    ("CLAUDECODE", "1", "claude-code"),
    ("CLAUDE_CODE", "1", "claude-code"),
    ("CLAUDE_CODE_IS_COWORK", "1", "cowork"),
    ("CLAUDE_CODE_ENTRYPOINT", "sdk-py", "claude-code"),
    ("CODEX_SANDBOX", "seatbelt", "codex"),
    ("CODEX_CI", "1", "codex"),
    ("CODEX_THREAD_ID", "t-1", "codex"),
    ("CODEX_SANDBOX_NETWORK_DISABLED", "1", "codex"),
    ("GEMINI_CLI", "1", "gemini-cli"),
    ("COPILOT_CLI", "1", "copilot-cli"),
    ("OPENCODE", "1", "opencode"),
    ("ANTIGRAVITY_AGENT", "1", "antigravity"),
    ("AUGMENT_AGENT", "1", "augment-cli"),
    ("CURSOR_AGENT", "1", "cursor-cli"),
    ("CURSOR_EXTENSION_HOST_ROLE", "agent-exec", "cursor-cli"),
    ("OR_APP_NAME", "Aider", "aider"),
    ("OR_SITE_URL", "https://aider.chat", "aider"),
]

# Set by people, IDEs or other tools; never a hard signal alone.
NOT_HARD = [
    {"OPENCODE_CLIENT": "vscode"},
    {"CURSOR_TRACE_ID": "abc"},
    {"TERM_PROGRAM": "kiro"},
    {"REPL_ID": "r"},
    {"COPILOT_MODEL": "gpt"},
    {"COPILOT_ALLOW_ALL": "1"},
    {"COPILOT_GITHUB_TOKEN": "t"},
    {"GOOSE_PROVIDER": "openai"},
    {"TERM_PROGRAM": "vscode", "VSCODE_INJECTION": "1"},
    {"ZED_TERM": "true"},
    {"TERMINAL_EMULATOR": "JetBrains-JediTerm"},
    {"WARP_IS_LOCAL_SHELL_SESSION": "1"},
    {"CI": "true", "GITHUB_ACTIONS": "true"},
    {"NO_COLOR": "1"},
    {"GEMINI_CLI_IDE_SERVER_PORT": "1"},
    {"AGENT": "claude"},
    {"CURSOR_EXTENSION_HOST_ROLE": "user"},
    {"OR_APP_NAME": "other"},
]


@pytest.fixture(autouse=True)
def _no_devin(monkeypatch, tmp_path):
    monkeypatch.setattr(agents, "DEVIN_MARKER", str(tmp_path / "no-devin"))


def _env(**values: str) -> dict[str, str]:
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", **values}


class TestTable:
    @pytest.mark.parametrize(("name", "value", "agent_id"), ROWS)
    def test_each_variable_alone_is_hard(self, name, value, agent_id):
        env = _env(**{name: value})
        found = agents.hard_match(env)
        assert found is not None
        assert (found.agent_id, found.reason) == (agent_id, name)
        assert agents.is_ai_agent(env) is True
        assert agents.agent_mode(env) is True

    @pytest.mark.parametrize("name", sorted({row[0] for row in ROWS}))
    def test_empty_is_not_set(self, name):
        assert agents.is_ai_agent(_env(**{name: ""})) is False

    @pytest.mark.parametrize(
        "name",
        sorted(
            {row[0] for row in ROWS}
            - {"AGENT", "CURSOR_EXTENSION_HOST_ROLE", "OR_APP_NAME", "OR_SITE_URL"}
        ),
    )
    @pytest.mark.parametrize("value", [" ", "   ", "\t"])
    def test_whitespace_is_set(self, name, value):
        # Present and not empty, like cli/cli: a blank marker stays a marker,
        # so trust never loosens on an odd value.
        assert agents.is_ai_agent(_env(**{name: value})) is True

    def test_blank_ai_agent_is_unknown(self):
        found = agents.hard_match(_env(AI_AGENT=" "))
        assert found is not None and found.agent_id == "unknown"

    def test_every_env_row_is_listed(self):
        assert {row[0] for row in ROWS} == set(agents.HARD_ENV_VARS)

    def test_every_row_names_a_known_source(self):
        for signal in agents.HARD_SIGNALS:
            assert signal.sources, signal.name
            assert set(signal.sources) <= set(agents.SOURCES), signal.name
        for url in agents.SOURCES.values():
            assert url.startswith("https://github.com/")

    @pytest.mark.parametrize("env", NOT_HARD)
    def test_not_hard(self, env):
        assert agents.is_ai_agent(_env(**env)) is False

    @pytest.mark.parametrize(
        ("value", "agent_id"),
        [
            ("claude-code_2-1-312_agent", "claude-code_2-1-312_agent"),
            ("has space", "unknown"),
            ("x" * 65, "unknown"),
            ("semi;colon", "unknown"),
        ],
    )
    def test_ai_agent_value_rule(self, value, agent_id):
        found = agents.hard_match(_env(AI_AGENT=value))
        assert found is not None and found.agent_id == agent_id

    def test_amp_wins_over_claudecode(self):
        found = agents.hard_match(_env(AGENT="amp", CLAUDECODE="1"))
        assert found is not None and found.agent_id == "amp"

    def test_ai_agent_comes_first(self):
        found = agents.hard_match(_env(AI_AGENT="x", CLAUDECODE="1"))
        assert found is not None and found.reason == "AI_AGENT"

    @pytest.mark.parametrize(
        "path",
        [
            "/home/u/.pi/agent/bin:/usr/bin",
            "C:\\Users\\u\\.pi\\agent\\bin;C:\\Windows",
        ],
    )
    def test_pi_path_entry(self, path):
        found = agents.hard_match({"PATH": path})
        assert found is not None
        assert (found.agent_id, found.session) == ("pi", True)
        assert found.reason == "a .pi/agent entry in PATH"

    def test_pi_needs_a_path_segment(self):
        assert agents.is_ai_agent({"PATH": "/opt/xpi/agent:/usr/bin"}) is False

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX marker file")
    def test_devin_marker_file(self, monkeypatch, tmp_path):
        marker = tmp_path / ".devin"
        marker.write_text("")
        monkeypatch.setattr(agents, "DEVIN_MARKER", str(marker))
        found = agents.hard_match(_env())
        assert found is not None
        assert (found.agent_id, found.reason, found.session) == (
            "devin",
            str(marker),
            True,
        )


class TestSnapshots:
    """Environments captured from (or documented for) real terminals."""

    @staticmethod
    def _load(name: str) -> tuple[dict, dict[str, str]]:
        data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
        return data["_meta"], data["env"]

    @pytest.mark.parametrize(
        "name", ["vscode", "cursor", "jetbrains", "warp", "zed", "github-actions"]
    )
    def test_human_terminals_and_ci_are_not_ai_agents(self, name):
        meta, env = self._load(name)
        assert meta["agent"] is None
        assert meta["sanitising"]
        assert agents.is_ai_agent(env) is False
        assert agents.agent_mode(env) is False
        assert agents.detect(env, []).agent_id is None

    def test_github_actions_is_soft(self):
        _, env = self._load("github-actions")
        detection = agents.detect(env, [])
        assert (detection.hard, detection.soft, detection.reason) == (
            False,
            True,
            "CI",
        )

    def test_claude_code_capture_is_hard(self):
        meta, env = self._load("claude-code-in-vscode")
        found = agents.hard_match(env)
        assert found is not None
        assert found.agent_id == meta["agent"]
        assert found.reason == "AI_AGENT"

    def test_fixtures_hold_no_secrets(self):
        for path in FIXTURES.glob("*.json"):
            env = json.loads(path.read_text(encoding="utf-8"))["env"]
            for name in env:
                assert not any(
                    word in name for word in ("KEY", "TOKEN", "SECRET", "DEEPGRAM_")
                ), (path.name, name)


class TestOverride:
    def test_dg_agent_1_turns_agent_mode_on(self):
        env = _env(DG_AGENT="1")
        assert agents.agent_mode(env) is True
        assert agents.is_agentic(env, []) is True
        detection = agents.detect(env, [])
        assert (detection.hard, detection.agent_id, detection.reason) == (
            True,
            "dg-agent-1",
            "DG_AGENT",
        )

    def test_dg_agent_1_does_not_mark_an_ai_agent_for_trust(self):
        assert agents.is_ai_agent(_env(DG_AGENT="1")) is False

    def test_dg_agent_0_turns_agent_mode_off(self):
        env = _env(DG_AGENT="0", CLAUDECODE="1", CI="true")
        assert agents.agent_mode(env) is False
        assert agents.is_agentic(env, ["--non-interactive"]) is False
        assert agents.detect(env, []).hard is False

    def test_dg_agent_0_keeps_trust_strict(self):
        # Trust reads the raw markers: DG_AGENT=0 never loosens it.
        env = _env(DG_AGENT="0", CLAUDECODE="1")
        assert agents.is_ai_agent(env) is True
        assert agents.detect(env, []).marker is not None

    @pytest.mark.parametrize("value", ["", "2", "yes", "true", "on"])
    def test_other_values_are_ignored(self, value):
        assert agents.override(_env(DG_AGENT=value)) is None
        assert agents.agent_mode(_env(DG_AGENT=value)) is False
        assert agents.agent_mode(_env(DG_AGENT=value, CLAUDECODE="1")) is True

    def test_output_agent_mode_follows_override(self, monkeypatch):
        monkeypatch.setattr(output, "is_ai_agent", lambda: True)
        monkeypatch.setenv("DG_AGENT", "0")
        assert output.agent_mode() is False
        monkeypatch.setattr(output, "is_ai_agent", lambda: False)
        monkeypatch.setenv("DG_AGENT", "1")
        assert output.agent_mode() is True
        monkeypatch.delenv("DG_AGENT")
        assert output.agent_mode() is False


class TestSoft:
    @pytest.fixture
    def tty(self, monkeypatch):
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    def test_flags_only_before_double_dash(self, tty):
        env = _env(TERM="xterm")
        assert agents.soft_reason(env, ["listen", "--non-interactive"]) == (
            "--non-interactive"
        )
        assert agents.soft_reason(env, ["api", "--", "--agent-friendly"]) is None

    def test_no_color_never_counts(self, monkeypatch):
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("sys.stdout.isatty", lambda: False)
        assert agents.soft_reason(_env(TERM="xterm", NO_COLOR="1"), []) is None
        assert agents.soft_reason(_env(TERM="dumb"), []) == "no terminal"


def test_stdlib_only():
    """Detection runs before anything heavy loads; it imports only stdlib."""
    code = (
        "import sys; before = set(sys.modules); import deepctl_core.agents; "
        "new = set(sys.modules) - before; "
        "bad = sorted(m for m in new if m.split('.')[0] in "
        "('rich','click','pydantic','httpx','yaml','deepgram','keyring')); "
        "print(','.join(bad))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == ""


@pytest.mark.skipif(sys.platform == "win32", reason="needs a pty")
@pytest.mark.parametrize(
    ("env", "ansi"),
    [
        ({}, True),
        ({"NO_COLOR": "1"}, False),
        ({"NO_COLOR": "1", "FORCE_COLOR": "1"}, False),
        ({"CLAUDECODE": "1"}, False),
        ({"CLAUDECODE": "1", "DG_AGENT": "0"}, True),
    ],
)
def test_console_on_a_terminal_honors_no_color(env, ansi):
    """On a terminal, colour and styles only for a person without NO_COLOR."""
    import os
    import pty

    code = (
        "from deepctl_core import output\n"
        "output.console.print('[red]x[/red] [dim]y[/dim] [bold]z[/bold]')\n"
        "output.stderr_console.print('[yellow]w[/yellow] [dim]v[/dim]')\n"
    )
    clean = {
        k: v
        for k, v in os.environ.items()
        if k not in (*agents.HARD_ENV_VARS, "DG_AGENT", "NO_COLOR", "FORCE_COLOR", "CI")
    }
    master, slave = pty.openpty()
    proc = subprocess.run(
        [sys.executable, "-c", code],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env={**clean, "TERM": "xterm-256color", "PATH": "/usr/bin:/bin", **env},
        timeout=60,
        check=True,
    )
    os.close(slave)
    out = b""
    while True:
        try:
            chunk = os.read(master, 4096)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
    os.close(master)
    assert proc.returncode == 0
    assert (b"\x1b[" in out) is ansi, out
