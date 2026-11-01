"""The project deepgram.yaml passes an allowlist: output settings and a
regional base_url only. Every other key is ignored with one warning that
names the key and never its value."""

from __future__ import annotations

import pytest
import yaml
from deepctl_core import config as config_module
from deepctl_core import output
from deepctl_core.config import Config, flush_config_notices
from deepctl_core.hosts import REGIONAL_API_HOSTS, SOURCE_PROJECT


@pytest.fixture(autouse=True)
def _fresh_notices(monkeypatch):
    monkeypatch.setattr(config_module, "_pending_notices", [])
    monkeypatch.setattr(config_module, "_seen_notices", set())
    monkeypatch.setattr(output, "is_agentic", lambda: False)
    monkeypatch.setattr(output, "is_ai_agent", lambda: False)
    output._output_config.update(agentic=False, quiet=False)
    monkeypatch.setattr(output.stderr_console, "_width", 400)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A cloned repository as the working directory, plus a clean user config."""
    work = tmp_path / "repo"
    work.mkdir()
    monkeypatch.chdir(work)
    # This test's user config is the one at the default location.
    monkeypatch.setattr(
        "deepctl_core.config._default_config_file",
        lambda: tmp_path / "user" / "config.yaml",
    )
    # In agent sessions too (location rules: test_config_location).
    monkeypatch.setattr(
        "deepctl_core.config._trust_root_location", lambda: (tmp_path, tmp_path / "user" / "config.yaml")
    )
    user = tmp_path / "user" / "config.yaml"
    user.parent.mkdir()
    user.write_text(
        yaml.safe_dump(
            {"profiles": {"default": {"api_key": "sk-USERKEY", "project_id": "P1"}}}
        )
    )
    user.chmod(0o600)
    return work, user


def load(repo, project: dict) -> Config:
    work, user = repo
    (work / "deepgram.yaml").write_text(yaml.safe_dump(project))
    return Config(config_path=str(user))


def notices() -> list[str]:
    return [text for _, text, _, _ in config_module._pending_notices]


@pytest.mark.parametrize(
    ("project", "key"),
    [
        ({"api_key": "sk-ATTACKER"}, "api_key"),
        ({"project_id": "ATTACKERPROJ"}, "project_id"),
        ({"active_profile": "evil"}, "active_profile"),
        ({"default_profile": "evil"}, "default_profile"),
        (
            {"profiles": {"default": {"api_key": "sk-ATTACKER"}}},
            "profiles.default.api_key",
        ),
        (
            {"profiles": {"default": {"project_id": "ATTACKERPROJ"}}},
            "profiles.default.project_id",
        ),
        (
            {
                "profiles": {
                    "default": {"dg_token_expires_at": "2999-01-01T00:00:00+00:00"}
                }
            },
            "profiles.default.dg_token_expires_at",
        ),
        ({"tools": {"ffprobe_path": "./tools/ffprobe"}}, "tools.ffprobe_path"),
        ({"telemetry": {"enabled": False}}, "telemetry.enabled"),
        ({"update": {"check_enabled": False}}, "update.check_enabled"),
        ({"plugins": {"enabled": ["evil"]}}, "plugins.enabled"),
        ({"api": {"allowed_hosts": ["evil.example"]}}, "api.allowed_hosts"),
        ({"something_new": 1}, "something_new"),
    ],
)
def test_forbidden_key_ignored_with_one_warning(repo, project, key):
    config = load(repo, project)
    profile = config.get_profile()

    assert profile.api_key == "sk-USERKEY"
    assert profile.project_id == "P1"
    assert profile.dg_token_expires_at is None
    assert config.profile == "default"
    assert config.get("tools.ffprobe_path") is None
    assert config.get("telemetry.enabled") is True
    assert config.get("update.check_enabled") is True
    assert config.get("plugins.enabled") == []
    assert config.allowed_hosts() == []

    warnings = [n for n in notices() if f"Ignored {key} in ./deepgram.yaml" in n]
    assert len(warnings) == 1
    # Never echo the value.
    for value in ("ATTACKER", "evil", "./tools/ffprobe", "2999"):
        assert all(value not in n for n in notices())


def test_whole_section_collapses_after_three_keys(repo):
    load(repo, {"telemetry": {"a": 1, "b": 2, "c": 3, "d": 4}})
    assert notices() == [
        "Ignored telemetry.* in ./deepgram.yaml: project files can't change "
        "telemetry settings."
    ]


def test_warning_printed_once_per_process(repo, capsys):
    load(repo, {"api_key": "sk-ATTACKER"})
    load(repo, {"api_key": "sk-ATTACKER"})
    flush_config_notices()
    err = capsys.readouterr().err
    assert err.count("Ignored api_key") == 1
    assert "Use dg login, or set DEEPGRAM_API_KEY." in err
    assert "sk-ATTACKER" not in err


def test_security_warnings_ignore_quiet(repo, capsys):
    output._output_config["quiet"] = True
    load(repo, {"tools": {"ffprobe_path": "/tmp/x"}})
    flush_config_notices()
    assert "Ignored tools.ffprobe_path" in capsys.readouterr().err


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000",
        "http://localhost:8080",
        "https://localhost",
        "https://[::1]:8443",
        "https://evil.example",
        "https://*.deepgram.com",
        "https://x.deepgram.com",
        "https://api.deepgram.com.evil.example",
        "http://api.deepgram.com",
        "https://api.deepgram.com:8443",
        "https://user@api.deepgram.com",
        "https://api.deepgram.com/v1?x=1",
        "wss://api.deepgram.com",
        "api.eu.deepgram.com",
        12345,
    ],
)
def test_non_regional_base_url_ignored(repo, url):
    config = load(repo, {"profiles": {"default": {"base_url": url}}})
    assert config.get_profile().base_url == "https://api.deepgram.com"
    assert config.base_url_source() == "default"
    warnings = [n for n in notices() if "profiles.default.base_url" in n]
    assert len(warnings) == 1
    assert "can only select a Deepgram regional API URL" in warnings[0]


@pytest.mark.parametrize("host", sorted(REGIONAL_API_HOSTS))
def test_regional_base_url_honored(repo, host):
    config = load(repo, {"profiles": {"default": {"base_url": f"https://{host}"}}})
    assert config.get_profile().base_url == f"https://{host}"
    assert config.base_url_source() == SOURCE_PROJECT
    assert notices() == []


def test_output_settings_allowed(repo):
    config = load(repo, {"output": {"format": "yaml", "color": False}})
    assert config.get("output.format") == "yaml"
    assert config.get("output.color") is False
    assert notices() == []


def test_agent_mode_hint_never_names_paths_or_variables(repo, monkeypatch):
    monkeypatch.setattr(output, "is_agentic", lambda: True)
    monkeypatch.setattr(output, "is_ai_agent", lambda: True)
    load(repo, {"profiles": {"default": {"base_url": "http://127.0.0.1:8000"}}})
    load(repo, {"api": {"allowed_hosts": ["x"]}})
    hints = [h for _, _, h, _ in config_module._pending_notices]
    assert hints
    for hint in hints:
        assert "DEEPGRAM_BASE_URL" not in hint
        assert "config.yaml" not in hint
        assert "ask the user" in hint.lower()


def test_project_file_never_saved(repo):
    work, user = repo
    config = load(
        repo,
        {
            "profiles": {"default": {"base_url": "https://api.eu.deepgram.com"}},
            "output": {"format": "yaml"},
        },
    )
    config._config.active_profile = "default"
    config.save()
    saved = yaml.safe_load(user.read_text())
    assert saved == {
        "profiles": {"default": {"api_key": "sk-USERKEY", "project_id": "P1"}},
        "active_profile": "default",
    }
