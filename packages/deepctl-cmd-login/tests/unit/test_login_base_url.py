"""`dg login` never saves a base URL from --base-url or the environment,
except through --save-base-url confirmed by a person in a terminal."""

from __future__ import annotations

from unittest.mock import patch

import click
import pytest
import yaml
from deepctl_cmd_login import command as login_module
from deepctl_cmd_login.command import LoginCommand
from deepctl_core import AuthManager, Config, DeepgramClient, hosts, output


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # This test's user config is the one at the default location.
    monkeypatch.setattr(
        "deepctl_core.config._default_config_file",
        lambda: tmp_path / "user" / "config.yaml",
    )
    # In agent sessions too (location rules: test_config_location).
    monkeypatch.setattr(
        "deepctl_core.config._trust_root_location", lambda: (tmp_path, tmp_path / "user" / "config.yaml")
    )
    monkeypatch.setattr(hosts, "_reported", set())
    monkeypatch.setattr(output, "is_agentic", lambda: False)
    monkeypatch.setattr(output, "is_ai_agent", lambda: False)
    monkeypatch.setattr(login_module, "is_agentic", lambda: False)
    output._output_config.update(agentic=False, quiet=False, format="default")
    monkeypatch.setattr(output.stderr_console, "_width", 400)
    # No keyring backend: the key falls back to the config file.
    from deepctl_core import auth as auth_module

    def no_keyring(*args, **kwargs):
        raise RuntimeError("no keyring backend")

    monkeypatch.setattr(auth_module.keyring, "set_password", no_keyring)
    monkeypatch.setattr(auth_module.keyring, "get_password", lambda *a: None)
    monkeypatch.setattr(auth_module.keyring, "delete_password", no_keyring)
    monkeypatch.setattr(LoginCommand, "_maybe_prompt_skills_setup", lambda self: None)


@pytest.fixture
def tty(monkeypatch):
    monkeypatch.setattr(login_module, "_interactive_terminal", lambda: True)


@pytest.fixture
def verified():
    """Login's key check succeeds without a network call; records the host."""
    hosts_seen: list[str] = []

    def verify(self, api_key=None, project_id=None):
        hosts_seen.append(self.check_base_url())
        return True, "ok", None

    with patch.object(AuthManager, "verify_credentials", verify):
        yield hosts_seen


def login(tmp_path, *, user=None, flag=None, **kwargs):
    path = tmp_path / "user" / "config.yaml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(yaml.safe_dump(user or {}))
    config = Config(config_path=str(path), base_url=flag)
    auth = AuthManager(config)
    kwargs.setdefault("api_key", "sk-LOGINKEY")
    kwargs.setdefault("force_write", True)
    result = LoginCommand().handle(config, auth, DeepgramClient(config, auth), **kwargs)
    return result, yaml.safe_load(path.read_text()) or {}


def test_foreign_flag_refused_without_save(tmp_path, verified, capsys):
    result, saved = login(tmp_path, flag="https://evil.example")
    assert result.status == "error"
    assert result.code == "foreign_host"
    assert verified == []
    assert saved == {}
    assert capsys.readouterr().err.count("Not sending your API key") == 1


def test_trusted_flag_used_but_not_saved(tmp_path, verified, capsys):
    result, saved = login(
        tmp_path,
        flag="https://api.eu.deepgram.com",
    )
    assert result.status == "success"
    assert verified == ["https://api.eu.deepgram.com"]
    assert "base_url" not in saved["profiles"]["default"]
    err = capsys.readouterr().err
    assert (
        'Used https://api.eu.deepgram.com for this login only. Profile "default" '
        "still uses https://api.deepgram.com."
    ) in err
    assert (
        "To save it, run: dg --base-url https://api.eu.deepgram.com login "
        "--save-base-url"
    ) in err


def test_env_base_url_not_saved(tmp_path, verified, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_BASE_URL", "http://127.0.0.1:8000")
    result, saved = login(tmp_path)
    assert result.status == "success"
    assert verified == ["http://127.0.0.1:8000"]
    assert "127.0.0.1" not in yaml.safe_dump(saved)


def test_save_needs_a_url(tmp_path, verified):
    with pytest.raises(click.UsageError, match="--save-base-url needs --base-url URL"):
        login(tmp_path, save_base_url=True)


def test_save_refused_without_tty(tmp_path, verified, monkeypatch):
    monkeypatch.setattr(login_module, "_interactive_terminal", lambda: False)
    result, saved = login(tmp_path, flag="https://evil.example", save_base_url=True)
    assert result.code == "confirmation_required"
    assert result.hint == "Run the command in an interactive terminal."
    assert verified == []
    assert saved == {}


def test_save_refused_when_agentic(tmp_path, verified, tty, monkeypatch):
    monkeypatch.setattr(login_module, "is_agentic", lambda: True)
    result, saved = login(tmp_path, flag="https://evil.example", save_base_url=True)
    assert result.code == "confirmation_required"
    assert result.hint == "A person must run this command in an interactive terminal."
    assert verified == []
    assert saved == {}


def test_save_confirmed_adds_host_to_allowlist(tmp_path, verified, tty, capsys):
    with patch.object(login_module.click, "confirm", return_value=True) as confirm:
        result, saved = login(
            tmp_path, flag="https://staging.example", save_base_url=True
        )
    assert result.status == "success"
    assert confirm.call_args.kwargs["default"] is False
    assert saved["profiles"]["default"]["base_url"] == "https://staging.example"
    assert saved["api"]["allowed_hosts"] == ["staging.example"]
    assert verified == ["https://staging.example"]
    err = capsys.readouterr().err
    assert (
        "staging.example is not a Deepgram API host. Saving it also adds it to "
        "api.allowed_hosts."
    ) in err
    assert (
        'Saved https://staging.example as the API URL for profile "default". '
        "Added staging.example to api.allowed_hosts."
    ) in err


def test_save_regional_resets_profile(tmp_path, verified, tty, capsys):
    """The documented reset for a profile pointing at a foreign host."""
    user = {"profiles": {"default": {"base_url": "http://127.0.0.1:8000"}}}
    with patch.object(login_module.click, "confirm", return_value=True):
        result, saved = login(
            tmp_path,
            user=user,
            flag="https://api.deepgram.com",
            save_base_url=True,
        )
    assert result.status == "success"
    assert saved["profiles"]["default"]["base_url"] == "https://api.deepgram.com"
    assert "api" not in saved
    assert "not a Deepgram API host" not in capsys.readouterr().err


def test_save_loopback_with_port_is_exact(tmp_path, verified, tty):
    with patch.object(login_module.click, "confirm", return_value=True):
        _, saved = login(tmp_path, flag="http://127.0.0.1:8000", save_base_url=True)
    assert saved["api"]["allowed_hosts"] == ["127.0.0.1:8000"]


def test_save_insecure_refused(tmp_path, verified, tty):
    with patch.object(login_module.click, "confirm") as confirm:
        result, saved = login(
            tmp_path, flag="http://staging.example", save_base_url=True
        )
    assert result.code == "insecure_base_url"
    confirm.assert_not_called()
    assert saved == {}


@pytest.mark.parametrize("answer", [False, click.Abort()])
def test_save_declined(tmp_path, verified, tty, capsys, answer):
    kwargs = (
        {"side_effect": answer}
        if isinstance(answer, Exception)
        else {"return_value": answer}
    )
    with patch.object(login_module.click, "confirm", **kwargs):
        result, saved = login(
            tmp_path, flag="https://staging.example", save_base_url=True
        )
    assert result.status == "cancelled"
    assert LoginCommand().exit_code_for(result) == 2
    assert verified == []
    assert saved == {}
    assert "Not saved. Nothing was sent to staging.example." in capsys.readouterr().err


@pytest.mark.parametrize("profile", ["default", "staging"])
def test_profile_fix_command_restores_profile_with_key(
    tmp_path, tty, monkeypatch, profile, capsys
):
    """The E-FH-PROFILE hint command works on a profile that holds a key:
    dg --base-url https://api.deepgram.com login --profile P --save-base-url
    --force-write"""
    hosts_seen: list[str] = []

    def verify_key(self, api_key=None):
        hosts_seen.append(self.check_base_url())
        assert api_key == "sk-OLDKEY"
        return True, "ok", None

    monkeypatch.setattr(AuthManager, "verify_api_key", verify_key)
    user = {
        "profiles": {
            profile: {"api_key": "sk-OLDKEY", "base_url": "http://127.0.0.1:8000"}
        }
    }
    with patch.object(login_module.click, "confirm", return_value=True):
        result, saved = login(
            tmp_path,
            user=user,
            flag="https://api.deepgram.com",
            api_key=None,
            profile=profile,
            save_base_url=True,
            force_write=True,
        )
    assert result.status == "success"
    assert hosts_seen == ["https://api.deepgram.com"]
    assert saved["profiles"][profile] == {
        "api_key": "sk-OLDKEY",
        "base_url": "https://api.deepgram.com",
    }
    assert (
        f'Saved https://api.deepgram.com as the API URL for profile "{profile}".'
    ) in capsys.readouterr().err


def test_profile_fix_not_saved_when_key_check_fails(tmp_path, tty, monkeypatch):
    monkeypatch.setattr(
        AuthManager, "verify_api_key", lambda self, k=None: (False, "bad key", "auth")
    )
    user = {
        "profiles": {
            "default": {"api_key": "sk-OLD", "base_url": "http://127.0.0.1:8000"}
        }
    }
    with patch.object(login_module.click, "confirm", return_value=True):
        result, saved = login(
            tmp_path,
            user=user,
            flag="https://api.deepgram.com",
            api_key=None,
            save_base_url=True,
        )
    assert result.status == "error"
    assert saved == user


def test_save_refused_with_custom_config(tmp_path, tty, monkeypatch):
    other = tmp_path / "dot" / "dg.yaml"
    other.parent.mkdir()
    other.write_text("{}\n")
    config = Config(config_path=str(other), base_url="https://staging.example")
    auth = AuthManager(config)
    with patch.object(login_module.click, "confirm") as confirm:
        with pytest.raises(click.UsageError) as err:
            LoginCommand().handle(
                config, auth, DeepgramClient(config, auth), save_base_url=True
            )
    confirm.assert_not_called()
    assert err.value.message == (
        "--save-base-url can't be used with --config: only the default user "
        f"config ({tmp_path / 'user' / 'config.yaml'}) can approve API hosts. "
        "Run the command without --config."
    )


@pytest.mark.parametrize("marker", ["is_agentic", "is_ai_agent"])
def test_save_refused_for_agent_with_tty(tmp_path, verified, tty, monkeypatch, marker):
    """An agent driving a pty still can't approve a host."""
    monkeypatch.setattr(login_module, marker, lambda: True)
    with patch.object(login_module.click, "confirm") as confirm:
        result, saved = login(tmp_path, flag="https://evil.test", save_base_url=True)
    confirm.assert_not_called()
    assert result.code == "confirmation_required"
    assert result.hint == "A person must run this command in an interactive terminal."
    assert saved == {}


def test_device_login_refusal_is_structured(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(output, "is_ai_agent", lambda: True)
    monkeypatch.setenv("DEEPGRAM_CLI_BASE_URL", "https://id.evil.example")
    output._output_config["format"] = "json"
    result, saved = login(tmp_path, api_key=None)
    assert result.code == "foreign_host"
    assert result.error == (
        "Not logging in through id.evil.example (from DEEPGRAM_CLI_BASE_URL): it "
        "is not a Deepgram login host."
    )
    assert capsys.readouterr().out == ""
    assert saved == {}
