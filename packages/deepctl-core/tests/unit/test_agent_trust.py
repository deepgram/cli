"""Trust follows the raw AI-agent markers, never DG_AGENT.

Every hard signal in the detection table turns on the agent-session config
rules: a user config others can change, or another user owns, is ignored,
its API key unused and never saved to, and the base URL rules for agents
apply. DG_AGENT=0 doesn't turn them off, and DG_AGENT=1 alone doesn't turn
them on. These tests set real environment variables; nothing patches the
detection functions.
"""

from __future__ import annotations

import os
import sys

import pytest
import yaml
from deepctl_core import agents, hosts
from deepctl_core import config as config_module
from deepctl_core.config import Config

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="XDG layout (Linux)"
)

ALLOWED = "127.0.0.1:8000"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    for name in (*agents.HARD_ENV_VARS, agents.OVERRIDE_VAR):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin:/bin")
    monkeypatch.setattr(agents, "DEVIN_MARKER", str(tmp_path / "no-devin"))
    monkeypatch.setattr(config_module, "_pending_notices", [])
    monkeypatch.setattr(config_module, "_seen_notices", set())
    monkeypatch.setattr(hosts, "_reported", set())
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setattr(config_module, "_real_home", lambda: str(root))
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return root


def plant(home, mode: int):
    """A user config holding a key, that other users can change."""
    cfg = home / ".config" / "deepctl" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        yaml.safe_dump(
            {
                "api": {"allowed_hosts": [ALLOWED]},
                "profiles": {"default": {"api_key": "k-planted"}},
            }
        )
    )
    cfg.chmod(mode)
    return cfg


def assert_agent_rules(home, monkeypatch) -> None:
    """0.4.1's agent-session rules: key unused, file untouched and unsaved,
    an env base URL off the allowlist refused."""
    cfg = plant(home, 0o666)
    before = cfg.read_bytes()
    config = Config()
    assert config.get_profile().api_key is None
    assert config.api_key_untrusted is True
    assert config.trusted_file is False
    with pytest.raises(config_module.ConfigSaveRefused):
        config.update_profile("default", project_id="p-agent")
    assert cfg.read_bytes() == before
    assert (cfg.stat().st_mode & 0o777) == 0o666
    monkeypatch.setenv("DEEPGRAM_BASE_URL", "https://evil.example")
    with pytest.raises(hosts.CredentialHostError):
        hosts.check_credential_host(Config())


def assert_person_rules(home, monkeypatch) -> None:
    """A person's session: the file is tightened to 0600 and trusted, the
    key used, and an env base URL used with a warning."""
    cfg = plant(home, 0o666)
    config = Config()
    assert config.get_profile().api_key == "k-planted"
    assert config.trusted_file is True
    assert (cfg.stat().st_mode & 0o777) == 0o600
    config.update_profile("default", project_id="p-person")
    assert yaml.safe_load(cfg.read_text())["profiles"]["default"]["project_id"] == (
        "p-person"
    )
    monkeypatch.setenv("DEEPGRAM_BASE_URL", "https://evil.example")
    assert hosts.check_credential_host(Config()) == "https://evil.example"


def test_no_marker_is_a_person(home, monkeypatch):
    assert_person_rules(home, monkeypatch)


def test_dg_agent_0_does_not_loosen_trust(home, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("DG_AGENT", "0")
    assert agents.agent_mode() is False  # presentation: a person
    assert_agent_rules(home, monkeypatch)  # trust: still an agent session


def test_dg_agent_0_with_ai_agent_does_not_loosen_trust(home, monkeypatch):
    monkeypatch.setenv("AI_AGENT", "some-agent")
    monkeypatch.setenv("DG_AGENT", "0")
    assert_agent_rules(home, monkeypatch)


@pytest.mark.parametrize("name", ["CLAUDECODE", "AI_AGENT"])
@pytest.mark.parametrize("value", [" ", "\t"])
def test_blank_marker_keeps_trust_strict(home, monkeypatch, name, value):
    """A marker set to whitespace is still set: as before this release, and
    as cli/cli reads it."""
    monkeypatch.setenv(name, value)
    assert agents.is_ai_agent() is True
    assert_agent_rules(home, monkeypatch)


def test_dg_agent_1_alone_keeps_person_trust(home, monkeypatch):
    monkeypatch.setenv("DG_AGENT", "1")
    assert agents.agent_mode() is True
    assert_person_rules(home, monkeypatch)


# The hard signals this release adds to the trust rules (0.4.1 had AI_AGENT,
# CLAUDECODE, CLAUDE_CODE_ENTRYPOINT, CODEX_SANDBOX,
# CODEX_SANDBOX_NETWORK_DISABLED, CURSOR_AGENT, GEMINI_CLI and Aider's two).
NEW_SIGNALS = [
    {"AGENT": "amp"},
    {"AGENT": "goose"},
    {"GOOSE_TERMINAL": "1"},
    {"CLAUDE_CODE": "1"},
    {"CLAUDE_CODE_IS_COWORK": "1"},
    {"CODEX_CI": "1"},
    {"CODEX_THREAD_ID": "t-1"},
    {"COPILOT_CLI": "1"},
    {"OPENCODE": "1"},
    {"ANTIGRAVITY_AGENT": "1"},
    {"AUGMENT_AGENT": "1"},
    {"CURSOR_EXTENSION_HOST_ROLE": "agent-exec"},
    {"PATH": "/home/u/.pi/agent/bin:/usr/bin:/bin"},
]


@pytest.mark.parametrize("env", NEW_SIGNALS, ids=lambda e: "-".join(e))
def test_each_new_signal_applies_the_agent_rules(home, monkeypatch, env):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert_agent_rules(home, monkeypatch)


@pytest.mark.parametrize("env", NEW_SIGNALS, ids=lambda e: "-".join(e))
def test_each_new_signal_with_dg_agent_0_keeps_the_agent_rules(home, monkeypatch, env):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("DG_AGENT", "0")
    assert_agent_rules(home, monkeypatch)


def test_devin_marker_applies_the_agent_rules(home, monkeypatch, tmp_path):
    marker = tmp_path / ".devin"
    marker.write_text("")
    monkeypatch.setattr(agents, "DEVIN_MARKER", str(marker))
    assert_agent_rules(home, monkeypatch)


def test_trust_reads_the_detection_table():
    """Every trust check goes through the same function the table feeds."""
    from deepctl_core import output

    assert output.is_ai_agent is agents.is_ai_agent
    assert os.environ.get("DG_AGENT") is None
