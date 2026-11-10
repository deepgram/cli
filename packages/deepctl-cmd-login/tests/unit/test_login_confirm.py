"""`dg login` takes any key the API accepts, and a confirmation nobody can
answer is `confirmation_required` naming --yes, never a silent cancel."""

from __future__ import annotations

import json
import sys
import types

import pytest
import yaml
from deepctl_cmd_login import command as login_module
from deepctl_cmd_login.command import LoginCommand
from deepctl_cmd_login.models import LoginCodedError
from deepctl_core import AuthManager, Config, DeepgramClient, hosts, output
from deepctl_core import auth as auth_module

HEX_KEY = "0123456789abcdef0123456789abcdef01234567"  # 40 hex, no prefix
SK_KEY = "sk-LGNFAKEKEY0000000000000000"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        "deepctl_core.config._default_config_file",
        lambda: tmp_path / "user" / "config.yaml",
    )
    monkeypatch.setattr(
        "deepctl_core.config._trust_root_location",
        lambda: (tmp_path, tmp_path / "user" / "config.yaml"),
    )
    monkeypatch.setattr(hosts, "_reported", set())
    monkeypatch.setattr(output, "_hints_shown", set())
    for name in ("is_agentic", "is_ai_agent"):
        monkeypatch.setattr(output, name, lambda: False)
        monkeypatch.setattr(login_module, name, lambda: False)
    monkeypatch.setattr(login_module, "_interactive_terminal", lambda: False)
    monkeypatch.setattr(login_module, "_prog", lambda: "dg")
    monkeypatch.setattr(sys, "argv", ["dg", "login"])
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPGRAM_PROJECT_ID", raising=False)
    saved = dict(output._output_config)
    output._output_config.update(agentic=True, quiet=False, format="json")
    monkeypatch.setattr(output.stderr_console, "_width", 400)
    stored: dict[str, str] = {}
    monkeypatch.setattr(
        auth_module.keyring,
        "set_password",
        lambda service, name, value: stored.__setitem__(name, value),
    )
    monkeypatch.setattr(
        auth_module.keyring, "get_password", lambda service, name: stored.get(name)
    )
    monkeypatch.setattr(
        auth_module.keyring, "delete_password", lambda service, name: stored.pop(name)
    )
    monkeypatch.setattr(LoginCommand, "_maybe_prompt_skills_setup", lambda self: None)
    monkeypatch.setattr(
        LoginCommand, "confirm", lambda *a, **k: pytest.fail("prompted")
    )
    yield stored
    output._output_config.clear()
    output._output_config.update(saved)


@pytest.fixture
def keyring_store(_isolated):
    return _isolated


@pytest.fixture
def verified(monkeypatch):
    """The key check succeeds without a network call; records each key."""
    keys: list[str] = []

    def verify(self, api_key=None, project_id=None):
        keys.append(api_key)
        return True, "ok", None

    monkeypatch.setattr(AuthManager, "verify_credentials", verify)
    return keys


def run(tmp_path, *, user=None, **kwargs):
    path = tmp_path / "user" / "config.yaml"
    path.parent.mkdir(exist_ok=True)
    if user is not None or not path.exists():
        path.write_text(yaml.safe_dump(user or {}))
    config = Config(config_path=str(path))
    auth = AuthManager(config)
    cmd = LoginCommand()
    result = cmd.handle(config, auth, DeepgramClient(config, auth), **kwargs)
    return cmd, result, path


def payload(result):
    """The JSON a run writes to stdout for ``result``."""
    return json.loads(json.dumps(result.model_dump()))


# ── No key-prefix heuristic ──────────────────────────────────────


@pytest.mark.parametrize("key", [HEX_KEY, SK_KEY])
def test_any_key_the_api_accepts_logs_in(
    tmp_path, verified, keyring_store, key, capsys
):
    _, result, _ = run(tmp_path, api_key=key)
    assert result.status == "success"
    assert verified == [key]
    assert keyring_store == {"api-key.default": key}
    captured = capsys.readouterr()
    assert "format" not in captured.out + captured.err
    assert key not in captured.out + captured.err


def test_auth_manager_has_no_prefix_warning(tmp_path, verified, capsys):
    config = Config(config_path=str(tmp_path / "c.yaml"))
    AuthManager(config).login_with_api_key(HEX_KEY)
    captured = capsys.readouterr()
    assert "pattern" not in captured.out + captured.err


# ── The env-key and re-login traps ───────────────────────────────


def test_env_key_with_api_key_flag_is_confirmation_required(
    tmp_path, verified, keyring_store, monkeypatch, capsys
):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "envkey")
    monkeypatch.setattr(sys, "argv", ["dg", "login", "--api-key", HEX_KEY])
    cmd, result, path = run(tmp_path, api_key=HEX_KEY)
    assert isinstance(result, LoginCodedError)
    assert payload(result) == {
        "error": (
            "DEEPGRAM_API_KEY is set, and logging in saves a profile that dg will "
            "use instead of it."
        ),
        "status": "error",
        "code": "confirmation_required",
        # The key is on argv, so no runnable command can be given.
        "hint": (
            "This job's DEEPGRAM_API_KEY is used without a login. Drop the login "
            "step, or add --yes if a saved profile is intended."
        ),
        "next_step": None,
        "schema_version": 1,
        "details": {"flag": "--yes"},
    }
    assert list(payload(result)) == [
        "error",
        "status",
        "code",
        "hint",
        "next_step",
        "schema_version",
        "details",
    ]
    assert cmd.exit_code_for(result) == 1
    assert verified == [] and keyring_store == {}
    assert yaml.safe_load(path.read_text()) == {}
    err = capsys.readouterr().err
    assert err.startswith("ERROR: DEEPGRAM_API_KEY is set")
    assert "HINT: This job's DEEPGRAM_API_KEY" in err
    assert HEX_KEY not in err


def test_env_key_bare_login_names_login_yes(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "envkey")
    monkeypatch.setattr(login_module, "is_ai_agent", lambda: True)
    _, result, _ = run(tmp_path)
    assert result.code == "confirmation_required"
    assert result.next_step == "dg login --yes"
    assert result.details == {"flag": "--yes"}
    assert result.hint == (
        "Run next_step only if the user agrees: the saved login will then take "
        "precedence over DEEPGRAM_API_KEY."
    )


def saved_login(tmp_path):
    return {"profiles": {"default": {"api_key": "saved-key-0000"}}}


def test_relogin_is_confirmation_required(tmp_path, verified, monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["dg", "-o", "json", "login", "--profile", "default"]
    )
    _, result, path = run(tmp_path, user=saved_login(tmp_path), profile="default")
    assert result.code == "confirmation_required"
    assert result.error == 'Profile "default" already has a saved login.'
    assert result.next_step == "dg -o json login --profile default --yes"
    assert result.details == {"flag": "--yes"}
    assert result.hint == "Run the command in next_step only if the user agrees."
    assert verified == []
    assert yaml.safe_load(path.read_text()) == saved_login(tmp_path)


def test_relogin_with_key_on_argv_has_no_next_step(tmp_path, verified, monkeypatch):
    monkeypatch.setattr(login_module, "is_ai_agent", lambda: True)
    monkeypatch.setattr(sys, "argv", ["dg", "login", "-k", HEX_KEY])
    _, result, _ = run(tmp_path, user=saved_login(tmp_path), api_key=HEX_KEY)
    assert result.next_step is None
    assert result.hint == (
        "Ask the user to rerun the command with --yes in their own terminal if "
        "they agree."
    )
    assert HEX_KEY not in json.dumps(payload(result))


def test_human_without_a_terminal_is_told_to_add_yes(tmp_path, monkeypatch, capsys):
    output._output_config.update(agentic=False, format="default")
    _, result, _ = run(tmp_path, user=saved_login(tmp_path))
    assert result.code == "confirmation_required"
    assert result.hint == "To go ahead, run the command again with --yes."
    err = capsys.readouterr().err
    assert "already has a saved login" in err


@pytest.mark.parametrize("flag", ["yes", "force_write"])
def test_yes_and_force_write_answer_both_traps(
    tmp_path, verified, keyring_store, monkeypatch, flag
):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "envkey")
    _, result, _ = run(
        tmp_path, user=saved_login(tmp_path), api_key=HEX_KEY, **{flag: True}
    )
    assert result.status == "success"
    assert keyring_store == {"api-key.default": HEX_KEY}


def test_no_trap_ever_returns_cancelled(tmp_path, monkeypatch):
    """Ledger 18: without a person saying No, nothing is `cancelled`."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "envkey")
    for user in ({}, saved_login(tmp_path)):
        _, result, _ = run(tmp_path, user=user, api_key=HEX_KEY)
        assert result.status == "error" and result.code == "confirmation_required"


def test_a_person_still_gets_the_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "envkey")
    asked: list[str] = []

    def confirm(self, message, default=False):
        asked.append(message)
        return False

    monkeypatch.setattr(LoginCommand, "confirm", confirm)
    monkeypatch.setattr(LoginCommand, "_can_ask", lambda self: True)
    _, result, _ = run(tmp_path, api_key=HEX_KEY)
    assert asked == ["Do you still want to login with a profile?"]
    assert result.status == "cancelled"  # the person said No


def test_can_ask_needs_a_person_at_a_terminal(monkeypatch):
    cmd = LoginCommand()
    monkeypatch.setattr(login_module, "_interactive_terminal", lambda: True)
    monkeypatch.setattr(LoginCommand, "can_prompt", lambda self: True)
    assert cmd._can_ask() is True
    monkeypatch.setattr(login_module, "is_ai_agent", lambda: True)
    assert cmd._can_ask() is False
    monkeypatch.setattr(login_module, "is_ai_agent", lambda: False)
    monkeypatch.setattr(login_module, "_interactive_terminal", lambda: False)
    assert cmd._can_ask() is False


# ── next_step construction ───────────────────────────────────────


@pytest.mark.parametrize(
    "argv",
    [
        ["login", "--api-key", "K"],
        ["login", "--api-key=K"],
        ["login", "-k", "K"],
        ["login", "-kK"],
        ["--api-key", "K", "login"],
        ["--base-url", "https://user:pw@example.com", "login"],
        ["--base-url", "https://example.com/?token=1", "login"],
    ],
)
def test_secret_bearing_argv_has_no_next_step(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["dg", *argv])
    assert login_module._secret_in(argv) is True
    assert login_module._rerun_with_yes("dg") is None


def _as_os(monkeypatch, name):
    """Make the command module see ``os.name == name``: only its own ``os``
    is swapped, not the process's, so pathlib and the rest keep working."""
    monkeypatch.setattr(login_module, "os", types.SimpleNamespace(name=name))


@pytest.mark.parametrize(
    ("os_name", "profile", "expected"),
    [
        # POSIX quotes with shlex.
        ("posix", "my work", "dg login --profile 'my work' --yes"),
        ("posix", "work", "dg login --profile work --yes"),
        # Windows has no shell quoting to rely on: None when a token needs
        # quoting, the plain command when none does.
        ("nt", "my work", None),
        ("nt", "work", "dg login --profile work --yes"),
    ],
)
def test_rerun_quotes_or_refuses_by_platform(monkeypatch, os_name, profile, expected):
    monkeypatch.setattr(sys, "argv", ["dg", "login", "--profile", profile])
    _as_os(monkeypatch, os_name)
    assert login_module._rerun_with_yes("dg") == expected


@pytest.mark.parametrize("os_name", ["posix", "nt"])
def test_rerun_refuses_unsafe_tokens(monkeypatch, os_name):
    _as_os(monkeypatch, os_name)
    monkeypatch.setattr(sys, "argv", ["dg", "login", "--profile", "=x"])
    assert login_module._rerun_with_yes("dg") is None
    monkeypatch.setattr(sys, "argv", ["dg", "login"])
    assert login_module._rerun_with_yes(None) is None


def test_details_is_left_out_when_empty():
    err = LoginCodedError(error="e", code="c", hint="h", next_step=None)
    assert list(err.model_dump()) == [
        "error",
        "status",
        "code",
        "hint",
        "next_step",
        "schema_version",
    ]
