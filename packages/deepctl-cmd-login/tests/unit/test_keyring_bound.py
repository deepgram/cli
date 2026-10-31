"""Whoami, profiles --switch and profiles --list read the keyring
through the resolver's bound (2 s off a terminal), not directly."""

from __future__ import annotations

import os
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest
import yaml
from deepctl_core import auth as auth_module
from deepctl_core import config as config_module
from deepctl_core import credentials, hosts, output
from deepctl_core.auth import AuthManager
from deepctl_core.config import Config

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="XDG layout (Linux)"
)


class HangingKeyring:
    def __init__(self) -> None:
        self.release = threading.Event()
        self.reads = 0

    def get_password(self, service: str, username: str) -> str | None:
        self.reads += 1
        self.release.wait(10)
        return "k" * 40


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "_pending_notices", [])
    monkeypatch.setattr(config_module, "_seen_notices", set())
    monkeypatch.setattr(hosts, "_reported", set())
    home = tmp_path / "home"
    (home / ".config" / "deepctl").mkdir(parents=True)
    cfg = home / ".config" / "deepctl" / "config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {"profiles": {"default": {"project_id": "p"}, "work": {"api_key": "w" * 40}}}
        )
    )
    os.chmod(cfg, 0o600)
    monkeypatch.setattr(config_module, "_real_home", lambda: str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "e" * 40)
    monkeypatch.setattr(output, "is_ai_agent", lambda: False)
    monkeypatch.setattr(output.stderr_console, "_width", 400)
    monkeypatch.setattr(credentials, "KEYRING_BOUND_S", 0.3)
    monkeypatch.setattr(credentials, "_interactive", lambda: False)
    kr = HangingKeyring()
    monkeypatch.setattr(auth_module, "keyring", kr, raising=False)
    yield kr
    kr.release.set()


def _timed(fn):
    started = time.monotonic()
    result = fn()
    return result, time.monotonic() - started


def test_whoami_is_bounded(setup, capsys):
    from deepctl_cmd_login.command import WhoamiCommand

    config = Config()
    result, took = _timed(
        lambda: WhoamiCommand().handle(config, AuthManager(config), MagicMock())
    )
    assert took < 5.0  # the keyring hangs until the test ends
    assert result.key_source == "DEEPGRAM_API_KEY (env)"
    assert result.authenticated is True
    assert setup.reads == 1  # one bounded read, then cached
    assert capsys.readouterr().err.count("keyring didn't answer") == 1


def test_profiles_switch_is_bounded(setup, monkeypatch):
    from deepctl_cmd_login.command import ProfilesCommand

    config = Config()
    monkeypatch.setattr(config, "save", lambda *a, **k: None)
    result, took = _timed(
        lambda: ProfilesCommand().handle(
            config, AuthManager(config), MagicMock(), switch="work"
        )
    )
    assert took < 5.0  # the keyring hangs until the test ends
    assert result.status != "error"  # fell back to the profile's file key


def test_profiles_list_is_bounded(setup):
    config = Config()
    result, took = _timed(lambda: AuthManager(config).list_profiles())
    assert took < 5.0  # the keyring hangs until the test ends
    # A timeout falls back to the file, like a keyring error did in 0.4.2.
    assert result.profiles["work"].api_key == "****" + "w" * 4


def test_whoami_leaves_an_expired_entry_alone(tmp_path, monkeypatch):
    """Whoami is read-only, as in 0.4.2; key_source unchanged."""
    from deepctl_cmd_login.command import WhoamiCommand

    monkeypatch.setattr(config_module, "_pending_notices", [])
    monkeypatch.setattr(config_module, "_seen_notices", set())
    home = tmp_path / "home"
    (home / ".config" / "deepctl").mkdir(parents=True)
    cfg = home / ".config" / "deepctl" / "config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {"profiles": {"default": {"dg_token_expires_at": "2020-01-01T00:00:00+00:00"}}}
        )
    )
    os.chmod(cfg, 0o600)
    monkeypatch.setattr(config_module, "_real_home", lambda: str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "e" * 40)
    monkeypatch.setattr(output, "is_ai_agent", lambda: False)

    class Recording:
        def __init__(self) -> None:
            self.entries = {"api-key.default": "k" * 40}
            self.calls: list[str] = []

        def get_password(self, service, username):
            self.calls.append("get")
            return self.entries.get(username)

        def delete_password(self, service, username):
            self.calls.append("delete")
            self.entries.pop(username, None)

    kr = Recording()
    monkeypatch.setattr(auth_module, "keyring", kr, raising=False)
    config = Config()
    result = WhoamiCommand().handle(config, AuthManager(config), MagicMock())
    assert result.key_source == "DEEPGRAM_API_KEY (env)"
    assert "delete" not in kr.calls
    assert kr.entries == {"api-key.default": "k" * 40}
