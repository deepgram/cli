"""No command that saves config persists a base_url from --base-url or
DEEPGRAM_BASE_URL. Each run below uses a URL from the flag or the
environment, saves config through a real command, and then checks that the
file doesn't hold it and that a later flag-less run uses the default host.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import click
import pytest
import yaml
from deepctl_cmd_ffprobe.command import FfprobeCommand
from deepctl_cmd_login.command import LoginCommand, LogoutCommand, ProfilesCommand
from deepctl_cmd_projects.command import ProjectsCommand
from deepctl_cmd_update.version_check import VersionChecker, VersionInfo
from deepctl_core import AuthManager, Config, DeepgramClient, hosts, output
from deepctl_core.auth import TokenResponse

URL = "http://127.0.0.1:8000"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # This test's user config is the one at the default location.
    monkeypatch.setattr(
        "deepctl_core.config._default_config_file", lambda: tmp_path / "config.yaml"
    )
    # In agent sessions too (location rules: test_config_location).
    monkeypatch.setattr(
        "deepctl_core.config._trust_root_location", lambda: (tmp_path, tmp_path / "config.yaml")
    )
    monkeypatch.setattr(hosts, "_reported", set())
    monkeypatch.setattr(output, "is_agentic", lambda: False)
    monkeypatch.setattr(output, "is_ai_agent", lambda: False)
    output._output_config.update(agentic=False, quiet=True, format="default")
    from deepctl_core import auth as auth_module

    def no_keyring(*args, **kwargs):
        raise RuntimeError("no keyring backend")

    monkeypatch.setattr(auth_module.keyring, "set_password", no_keyring)
    monkeypatch.setattr(auth_module.keyring, "get_password", lambda *a: None)
    monkeypatch.setattr(auth_module.keyring, "delete_password", no_keyring)
    monkeypatch.setattr(LoginCommand, "_maybe_prompt_skills_setup", lambda self: None)
    # Nothing leaves the process.
    monkeypatch.setattr(
        AuthManager, "verify_credentials", lambda self, *a, **k: (True, "ok", None)
    )
    monkeypatch.setattr(DeepgramClient, "get_project", lambda self, pid: {})


def _login(config, auth, client):
    LoginCommand().handle(
        config, auth, client, api_key="sk-NEWKEY", project_id="P2", force_write=True
    )


def _logout_keep(config, auth, client):
    LogoutCommand().handle(config, auth, client, keep_config=True)


def _logout_all_keep(config, auth, client):
    LogoutCommand().handle(config, auth, client, all=True, keep_config=True)


def _set_default_project(config, auth, client):
    ProjectsCommand().handle(config, auth, client, set_default="P9")


def _switch_profile(config, auth, client):
    ProfilesCommand().handle(config, auth, client, switch="default")


def _ffprobe_set(config, auth, client):
    from deepctl_cmd_ffprobe import command as ffprobe_module

    with (
        patch.object(ffprobe_module, "is_ai_agent", return_value=False),
        patch("deepctl_cmd_ffprobe.command.os.access", return_value=True),
        patch("deepctl_cmd_ffprobe.command.os.path.isfile", return_value=True),
        patch("deepctl_cmd_ffprobe.command.subprocess.run", MagicMock()),
    ):
        FfprobeCommand().handle(config, auth, client, path="/usr/bin/ffprobe")


def _ffprobe_reset(config, auth, client):
    FfprobeCommand().handle(config, auth, client, reset=True)


def _version_cache(config, auth, client):
    VersionChecker(config)._cache_info(
        VersionInfo(
            current_version="0.4.0", latest_version="0.4.1", update_available=True
        )
    )


def _device_login(config, auth, client):
    auth._store_token(
        TokenResponse(
            access_token="jwt", project_id="P3", dg_token="dg-KEY", dg_expires_in=60
        )
    )


def _update_install_method(config, auth, client):
    config._set_config_value("update.installation_method", "pip")
    config.save()


def _delete_and_create(config, auth, client):
    config.create_profile("other", api_key="k")
    config.delete_profile("other")


SAVERS = [
    _login,
    _logout_keep,
    _logout_all_keep,
    _set_default_project,
    _switch_profile,
    _ffprobe_set,
    _ffprobe_reset,
    _version_cache,
    _device_login,
    _update_install_method,
    _delete_and_create,
]


@pytest.mark.parametrize("via", ["flag", "env"])
@pytest.mark.parametrize("saver", SAVERS, ids=lambda f: f.__name__.strip("_"))
def test_command_never_saves_runtime_base_url(tmp_path, monkeypatch, saver, via):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "profiles": {"default": {"api_key": "sk-OLDKEY", "project_id": "P1"}},
                # Approve the host so every command runs to its save.
                "api": {"allowed_hosts": ["127.0.0.1"]},
            }
        )
    )
    if via == "env":
        monkeypatch.setenv("DEEPGRAM_BASE_URL", URL)
        # Other env-layer values must stay out of the file too.
        monkeypatch.setenv("DEEPGRAM_API_KEY", "sk-ENVKEYSENTINEL")
        monkeypatch.setenv("DEEPGRAM_PROJECT_ID", "ENVPROJECTSENTINEL")
        config = Config(config_path=str(path))
    else:
        config = Config(config_path=str(path), base_url=URL)
    assert config.get_profile().base_url == URL
    auth = AuthManager(config)
    client = DeepgramClient(config, auth)

    # save() rewrites the file from data, so the comment disappears.
    path.write_text(path.read_text() + "# not saved yet\n")
    try:
        saver(config, auth, client)
    except (SystemExit, click.exceptions.Exit):
        pass
    after = path.read_text()

    assert "# not saved yet" not in after  # the command really saved
    assert "127.0.0.1:8000" not in after
    assert "ENVKEYSENTINEL" not in after
    assert "ENVPROJECTSENTINEL" not in after
    monkeypatch.delenv("DEEPGRAM_BASE_URL", raising=False)
    later = Config(config_path=str(path))
    assert later.get_profile().base_url == "https://api.deepgram.com"
