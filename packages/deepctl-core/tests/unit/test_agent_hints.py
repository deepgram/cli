"""Agent-mode hints from the host check name no config key, environment
variable or flag an agent could use to approve its own host."""

from __future__ import annotations

import pytest
from deepctl_core import hosts, output
from deepctl_core.hosts import (
    SOURCE_ENV,
    SOURCE_FLAG,
    SOURCE_USER,
    CredentialHostError,
    evaluate,
)

BANNED = ("allowed_hosts", "DEEPGRAM_BASE_URL", "--allow-foreign-host")


@pytest.fixture(autouse=True)
def _agent(monkeypatch, tmp_path):
    monkeypatch.setattr(hosts, "_reported", set())
    monkeypatch.setattr(
        "deepctl_core.config._default_config_file",
        lambda: tmp_path / "config.yaml",
    )
    monkeypatch.setattr(output, "is_ai_agent", lambda: True)


@pytest.mark.parametrize("source", [SOURCE_FLAG, SOURCE_ENV, SOURCE_USER])
@pytest.mark.parametrize(
    "url", ["https://evil.example", "http://evil.example", "http://evil.example:80"]
)
def test_refusal_hints(source, url):
    with pytest.raises(CredentialHostError) as err:
        evaluate(url, source)
    for banned in BANNED:
        assert banned not in err.value.hint


def test_identity_refusal_hint(monkeypatch):
    with pytest.raises(CredentialHostError) as err:
        evaluate("https://id.evil.example", SOURCE_ENV, identity=True)
    for banned in BANNED:
        assert banned not in err.value.hint


def test_hint_texts():
    with pytest.raises(CredentialHostError) as err:
        evaluate("https://evil.example", SOURCE_FLAG)
    assert err.value.hint == (
        "A person must approve evil.example in the deepctl user config. If you "
        "are an AI agent, ask the user."
    )
    with pytest.raises(CredentialHostError) as err:
        evaluate("http://evil.example", SOURCE_ENV)
    assert err.value.hint == (
        "API URLs must use https. Ask the user to fix the configured URL."
    )


def test_env_warning_hint(capsys, monkeypatch):
    monkeypatch.setattr(output.stderr_console, "_width", 400)
    origin = hosts.parse_origin("https://proxy.example")
    hosts.warn_env_host(origin)
    err = capsys.readouterr().err
    assert (
        "To stop this warning, a person can approve proxy.example in the deepctl "
        "user config. If you are an AI agent, ask the user."
    ) in err
    for banned in ("allowed_hosts", "--allow-foreign-host"):
        assert banned not in err
