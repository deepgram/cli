"""Device login for agents: `dg login` returns the link and code at once,
`dg login --complete` finishes it, and nothing is requested when the login
couldn't be saved."""

from __future__ import annotations

import json
import os
import stat
import sys
import time

import pytest
import yaml
from deepctl_cmd_login import command as login_module
from deepctl_cmd_login.command import LoginCommand
from deepctl_cmd_login.models import LoginCodedError, LoginPendingResult
from deepctl_core import AuthManager, Config, DeepgramClient, hosts, login_state, output
from deepctl_core import auth as auth_module
from deepctl_core.auth import DeviceCodeResponse, DevicePoll, TokenResponse

# The real request method, before the fixture swaps in the fake.
_REAL_REQUEST = AuthManager._request_device_code

IDENTITY = "https://id.dx.deepgram.com"
LINK = "https://id.dx.deepgram.com/auth/cli"
CODE = "WDJB-MJHT"
KEY = "dgfinite0000000000000000000000000004567"


class FakeIdentity:
    """Stands in for the login server: records every request; answers polls in turn."""

    def __init__(self) -> None:
        self.requests: list[str] = []
        self.answers: list[DevicePoll] = []
        self.interval = 5

    def device_code(self, identity_url=None):
        self.requests.append(f"code {identity_url}")
        return DeviceCodeResponse(
            device_code="device-secret-1",
            user_code=CODE,
            verification_uri=LINK,
            expires_in=900,
            interval=self.interval,
        )

    def poll(self, device_code, identity_url=None):
        self.requests.append(f"poll {device_code} {identity_url}")
        return self.answers.pop(0) if self.answers else DevicePoll("pending")


def approved() -> DevicePoll:
    return DevicePoll(
        "token",
        token=TokenResponse(
            access_token="jwt", dg_token=KEY, dg_expires_in=3600, pid="proj-1"
        ),
    )


@pytest.fixture
def agent(monkeypatch):
    def set_agent(on: bool) -> None:
        for mod in (output, login_module):
            monkeypatch.setattr(mod, "is_ai_agent", lambda: on)
        output._output_config["agentic"] = on

    return set_agent


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, agent):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(login_state, "state_dir", lambda: home / ".deepctl")
    monkeypatch.setattr(login_module, "state_dir", lambda: home / ".deepctl")
    user = home / ".config" / "deepctl" / "config.yaml"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("deepctl_core.config._default_config_file", lambda: user)
    monkeypatch.setattr(
        "deepctl_core.config._trust_root_location", lambda: (home, user)
    )
    monkeypatch.setattr(hosts, "_reported", set())
    monkeypatch.setattr(output, "_hints_shown", set())
    monkeypatch.setattr(login_module, "is_agentic", lambda: False)
    monkeypatch.setattr(login_module, "_interactive_terminal", lambda: False)
    monkeypatch.setattr(login_module, "_prog", lambda: "dg")
    monkeypatch.setattr(sys, "argv", ["dg", "login"])
    for name in ("DEEPGRAM_API_KEY", "DEEPGRAM_PROJECT_ID", "DG_AGENT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("DEEPGRAM_CLI_BASE_URL", raising=False)
    saved = dict(output._output_config)
    output._output_config.update(quiet=False, verbose=False, format="json")
    agent(True)
    monkeypatch.setattr(output.stderr_console, "_width", 400)
    keyring: dict[str, str] = {}
    monkeypatch.setattr(
        auth_module.keyring,
        "set_password",
        lambda service, name, value: keyring.__setitem__(name, value),
    )
    monkeypatch.setattr(
        auth_module.keyring, "get_password", lambda service, name: keyring.get(name)
    )

    def delete(service, name):
        keyring.pop(name, None)

    monkeypatch.setattr(auth_module.keyring, "delete_password", delete)
    fake = FakeIdentity()
    monkeypatch.setattr(AuthManager, "_request_device_code", fake.device_code)
    monkeypatch.setattr(AuthManager, "poll_device_token", fake.poll)
    monkeypatch.setattr(LoginCommand, "_maybe_prompt_skills_setup", lambda self: None)
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda *a: pytest.fail("browser"))
    yield {"home": home, "user": user, "keyring": keyring, "fake": fake}
    output._output_config.clear()
    output._output_config.update(saved)


def write_user(env, data=None, mode=0o600):
    env["user"].parent.mkdir(parents=True, exist_ok=True)
    env["user"].write_text(yaml.safe_dump(data or {"output": {"format": "json"}}))
    os.chmod(env["user"], mode)


def run(env, **kwargs):
    config = Config(config_path=str(env["user"])) if env["user"].exists() else Config()
    auth = AuthManager(config)
    cmd = LoginCommand()
    result = cmd.handle(config, auth, DeepgramClient(config, auth), **kwargs)
    return cmd, result


def pending_path(env):
    return env["home"] / ".deepctl" / "login-pending.json"


def dumped(result):
    return json.loads(json.dumps(result.model_dump()))


# ── Bare login returns at once ───────────────────────────────────


def test_agent_login_returns_pending_at_once(env, capsys):
    start = time.monotonic()
    cmd, result = run(env)
    assert time.monotonic() - start < 5
    assert isinstance(result, LoginPendingResult)
    assert cmd.exit_code_for(result) == 0
    assert dumped(result) == {
        "status": "pending",
        "message": "Open the link and enter the code to finish logging in.",
        "verification_uri": LINK,
        "user_code": CODE,
        "expires_in": 900,
        "next_step": "dg login --complete",
        "schema_version": 1,
    }
    assert list(dumped(result)) == [
        "status",
        "message",
        "verification_uri",
        "user_code",
        "expires_in",
        "next_step",
        "schema_version",
    ]
    assert capsys.readouterr().err.splitlines() == [
        f"INFO: To finish logging in, open {LINK} and enter the code {CODE}.",
        "HINT: Give the user the link and code, then run dg login --complete after "
        "they approve.",
    ]
    path = pending_path(env)
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    saved = json.loads(path.read_text())
    assert saved["device_code"] == "device-secret-1"
    assert saved["host"] == IDENTITY and saved["profile"] == "default"
    assert env["fake"].requests == [f"code {IDENTITY}"]


def test_existing_state_dir_is_made_private(env):
    (env["home"] / ".deepctl").mkdir(mode=0o755)
    os.chmod(env["home"] / ".deepctl", 0o755)
    run(env)
    if sys.platform != "win32":
        assert stat.S_IMODE((env["home"] / ".deepctl").stat().st_mode) == 0o700


@pytest.mark.parametrize("why", ["no_tty", "no_browser", "dg_agent"])
def test_pending_without_an_agent_marker(env, agent, monkeypatch, why):
    agent(False)
    kwargs = {}
    if why == "no_tty":
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
    elif why == "no_browser":
        kwargs["no_browser"] = True
    else:
        monkeypatch.setenv("DG_AGENT", "1")
    _, result = run(env, **kwargs)
    assert isinstance(result, LoginPendingResult)


def test_dg_agent_0_waits_like_a_person(env, agent, monkeypatch):
    """DG_AGENT=0 restores the interactive login for a person whose shell
    carries an agent marker; trust still reads the marker."""
    agent(True)
    monkeypatch.setenv("DG_AGENT", "0")
    output._output_config["agentic"] = False  # as the process computes it
    output._output_config["format"] = "default"
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    calls = []
    monkeypatch.setattr(
        AuthManager,
        "login_with_device_flow",
        lambda self, open_browser=True: calls.append(open_browser),
    )
    run(env)
    assert calls == [True]
    assert not pending_path(env).exists()


def test_person_at_a_terminal_still_waits(env, agent, monkeypatch):
    agent(False)
    output._output_config["format"] = "default"
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    calls = []
    monkeypatch.setattr(
        AuthManager,
        "login_with_device_flow",
        lambda self, open_browser=True: calls.append(open_browser),
    )
    run(env)
    assert calls == [True]
    assert not pending_path(env).exists()


def test_wait_polls_until_approved(env, monkeypatch):
    monkeypatch.setattr(auth_module.time, "sleep", lambda s: None)
    env["fake"].answers = [DevicePoll("pending"), approved()]
    cmd, result = run(env, wait=True)
    assert result.status == "success"
    assert env["keyring"] == {"api-key.default": KEY}
    assert not pending_path(env).exists()
    assert [r.split()[0] for r in env["fake"].requests] == ["code", "poll", "poll"]


# ── --complete ───────────────────────────────────────────────────


def test_complete_before_approval_is_auth_pending(env, capsys):
    run(env)
    capsys.readouterr()
    cmd, result = run(env, complete=True)
    assert cmd.exit_code_for(result) == 1
    assert dumped(result) == {
        "error": "Login is waiting for approval in the browser.",
        "status": "error",
        "code": "auth_pending",
        "hint": (
            f"Wait 6 s for the user to approve the login at {LINK} with the code "
            f"{CODE}, then run dg login --complete."
        ),
        "next_step": "dg login --complete",
        "schema_version": 1,
        # The server's interval of 5 s is raised to dg's 6 s floor.
        "details": {"retry_after": 6, "verification_uri": LINK, "user_code": CODE},
    }
    assert pending_path(env).exists()


def test_complete_after_approval_saves_the_key(env, capsys):
    write_user(env)
    run(env)
    env["fake"].answers = [approved()]
    capsys.readouterr()
    cmd, result = run(env, complete=True)
    assert dumped(result) == {
        "status": "success",
        "message": "Successfully logged in via web authentication",
        "profile": "default",
        "api_key_masked": "****4567",
        "project_id": "proj-1",
        "config_path": str(env["user"]),
    }
    captured = capsys.readouterr()
    assert captured.err == ""  # mode A: no chrome (J1.4)
    assert env["keyring"] == {"api-key.default": KEY}
    saved = yaml.safe_load(env["user"].read_text())
    assert saved["profiles"]["default"]["project_id"] == "proj-1"
    assert saved["profiles"]["default"].get("api_key") is None
    assert not pending_path(env).exists()
    assert KEY not in captured.out + captured.err


def test_complete_saves_to_the_profile_login_started_with(env):
    write_user(env)
    run(env, profile="work")
    env["fake"].answers = [approved()]
    _, result = run(env, complete=True)
    assert result.profile == "work"
    assert env["keyring"] == {"api-key.work": KEY}


@pytest.mark.parametrize(
    ("answer", "code"),
    [("denied", "auth_denied"), ("expired", "auth_expired")],
)
def test_complete_denied_or_expired(env, answer, code):
    run(env)
    env["fake"].answers = [DevicePoll(answer)]
    _, result = run(env, complete=True)
    assert result.code == code
    assert result.next_step == (None if answer == "denied" else "dg login")
    assert not pending_path(env).exists()


def test_complete_after_local_expiry(env, monkeypatch):
    run(env)
    later = time.time() + 1000
    monkeypatch.setattr(login_state.time, "time", lambda: later)
    _, result = run(env, complete=True)
    assert result.code == "auth_expired"
    assert [r.split()[0] for r in env["fake"].requests] == ["code"]


def test_complete_without_a_pending_login(env, capsys):
    cmd, result = run(env, complete=True)
    assert dumped(result) == {
        "error": "No login is waiting to be completed, or it expired.",
        "status": "error",
        "code": "auth_expired",
        "hint": "Run dg login again and give the user the new link and code.",
        "next_step": "dg login",
        "schema_version": 1,
    }
    assert env["fake"].requests == []


def test_complete_never_sends_the_code_to_another_login_host(env, monkeypatch):
    run(env)
    monkeypatch.setenv("DEEPGRAM_CLI_BASE_URL", "https://id.other.example")
    output._output_config["agentic"] = False
    monkeypatch.setattr(output, "is_ai_agent", lambda: False)
    monkeypatch.setattr(login_module, "is_ai_agent", lambda: False)
    _, result = run(env, complete=True)
    assert result.code == "auth_expired"
    assert [r.split()[0] for r in env["fake"].requests] == ["code"]


def test_complete_with_api_key_is_a_usage_error(env):
    import click

    with pytest.raises(click.UsageError):
        run(env, complete=True, api_key="k")


# ── The pending file under an agent ──────────────────────────────


def plant_pending(env, mode=0o600):
    run(env)
    path = pending_path(env)
    os.chmod(path, mode)
    return path, path.read_bytes()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_writable_pending_file_is_ignored_and_kept(env, capsys):
    path, before = plant_pending(env, 0o666)
    capsys.readouterr()
    _, result = run(env, complete=True)
    assert result.code == "auth_expired"
    assert path.read_bytes() == before
    assert stat.S_IMODE(path.stat().st_mode) == 0o666
    warns = [
        line
        for line in capsys.readouterr().err.splitlines()
        if line.startswith("WARN:")
    ]
    assert warns == [
        "WARN: Ignored the pending login in ~/.deepctl/login-pending.json: in an AI "
        "agent session, dg doesn't use a file that can be changed by other users."
    ]
    assert [r.split()[0] for r in env["fake"].requests] == ["code"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX owners")
def test_foreign_pending_file_is_ignored(env, monkeypatch, capsys):
    path, before = plant_pending(env)
    other = os.geteuid() + 1
    monkeypatch.setattr(login_state.os, "geteuid", lambda: other)
    # The pre-flight's own owner checks use the real uid.
    monkeypatch.setattr(login_module, "_preflight", lambda config: None)
    capsys.readouterr()
    _, result = run(env, complete=True)
    assert result.code == "auth_expired"
    assert path.read_bytes() == before
    assert "is owned by another user" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks")
def test_symlinked_pending_file_is_ignored(env, tmp_path, capsys):
    run(env)
    path = pending_path(env)
    elsewhere = tmp_path / "planted.json"
    elsewhere.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(elsewhere)
    capsys.readouterr()
    _, result = run(env, complete=True)
    assert result.code == "auth_expired"
    assert path.is_symlink() and elsewhere.exists()
    assert "WARN:" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_writable_pending_file_is_used_without_an_agent(env, agent, capsys):
    path, _ = plant_pending(env, 0o666)
    agent(False)
    _, result = run(env, complete=True)
    assert result.code == "auth_pending"
    assert "WARN:" not in capsys.readouterr().err


def test_pending_file_never_holds_the_key(env):
    write_user(env)
    run(env)
    env["fake"].answers = [DevicePoll("pending")]
    run(env, complete=True)
    assert KEY not in pending_path(env).read_text()


# ── Unwritable state ─────────────────────────────────────────────


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0, reason="root ignores modes"
)
@pytest.mark.parametrize(
    "kwargs", [{}, {"api_key": "k", "yes": True}, {"complete": True}]
)
def test_unwritable_home_stops_before_any_request(env, monkeypatch, kwargs, capsys):
    verified = []
    monkeypatch.setattr(
        AuthManager, "verify_credentials", lambda self, *a, **k: verified.append(1)
    )
    os.chmod(env["home"], 0o500)
    try:
        cmd, result = run(env, **kwargs)
    finally:
        os.chmod(env["home"], 0o700)
    assert isinstance(result, LoginCodedError)
    assert result.code == "state_unwritable"
    assert result.next_step is None
    assert result.details == {"dir": str(env["home"] / ".deepctl")}
    assert result.hint == (
        "This environment can't store credentials. Ask the user to run dg login "
        "in their own terminal."
    )
    assert env["fake"].requests == [] and verified == []


def test_save_failure_after_the_request_is_state_unwritable(env, monkeypatch):
    def refuse(pending):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(login_module, "save_pending", refuse)
    _, result = run(env)
    assert result.code == "state_unwritable"


# ── An untrusted user config under an agent ──────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
@pytest.mark.parametrize(
    "kwargs", [{}, {"api_key": "k", "yes": True}, {"complete": True}]
)
@pytest.mark.parametrize("case", ["writable", "foreign"])
def test_untrusted_config_stops_before_any_request(
    env, monkeypatch, kwargs, case, capsys
):
    if kwargs.get("complete"):
        write_user(env)
        run(env)  # a valid pending login
        env["fake"].requests.clear()
    write_user(env, mode=0o666 if case == "writable" else 0o600)
    if case == "foreign":
        from deepctl_core import config as config_module

        other = os.getuid() + 1
        monkeypatch.setattr(config_module.os, "geteuid", lambda: other)
    before = env["user"].read_bytes()
    verified = []
    monkeypatch.setattr(
        AuthManager, "verify_credentials", lambda self, *a, **k: verified.append(1)
    )
    capsys.readouterr()
    cmd, result = run(env, **kwargs)
    why = (
        "can be changed by other users"
        if case == "writable"
        else "is owned by another user"
    )
    assert dumped(result) == {
        "error": (
            "In an AI agent session, dg can't save a login to "
            "~/.config/deepctl/config.yaml "
            f"because the file {why}. Nothing was sent or saved."
        ),
        "status": "error",
        "code": "state_unwritable",
        "hint": "A person must fix this file. If you are an AI agent, ask the user.",
        "next_step": None,
        "schema_version": 1,
        "details": {"reason": "untrusted_config", "path": str(env["user"])},
    }
    assert cmd.exit_code_for(result) == 1
    err = capsys.readouterr().err
    assert "HINT: A person must fix this file." in err
    assert env["user"].read_bytes() == before
    if case == "writable":
        assert stat.S_IMODE(env["user"].stat().st_mode) == 0o666
    assert env["fake"].requests == [] and verified == []
    assert env["keyring"] == {}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_untrusted_config_comes_before_the_env_key_trap(env, monkeypatch):
    write_user(env, mode=0o666)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "envkey")
    _, result = run(env)
    assert result.code == "state_unwritable"
    assert result.details["reason"] == "untrusted_config"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_writable_config_without_an_agent_still_logs_in(env, agent):
    agent(False)
    write_user(env, mode=0o666)
    _, result = run(env, no_browser=True)
    assert isinstance(result, LoginPendingResult)


# ── mode A stderr ────────────────────────────────────────────────


def test_mode_a_stderr_keeps_only_prefixed_lines():
    import io

    real = io.StringIO()
    filtered = login_module._ModeAStderr(real)
    for text in (
        "Starting web authentication...\n",
        "INFO: link\n",
        "✓ API key stored\n",
        "Warning: Project ID not provided\n",
        "Error: something only chrome says\n",
        "HINT: h\n",
    ):
        filtered.write(text)
    assert real.getvalue() == "INFO: link\nHINT: h\n"


def test_mode_a_stderr_verbose_keeps_chrome_as_info_lines():
    import io

    real = io.StringIO()
    filtered = login_module._ModeAStderr(real, verbose=True)
    filtered.write("Starting web authentication...\n")
    filtered.write("\nYou are now\nlogged in\n")
    filtered.write("WARN: w\n")
    assert real.getvalue() == (
        "INFO: Starting web authentication...\nINFO: You are now\nINFO: logged in\n"
        "WARN: w\n"
    )


def test_api_key_login_in_agent_mode_has_empty_stderr(env, monkeypatch, capsys):
    """J4.3: no project ID is chrome, not a warning, in mode A."""
    monkeypatch.setattr(
        AuthManager, "verify_credentials", lambda self, *a, **k: (True, "ok", None)
    )
    write_user(env)
    capsys.readouterr()
    _, result = run(env, api_key="0123456789abcdef0123456789abcdef01234567")
    assert result.status == "success"
    assert capsys.readouterr().err == ""


# ── Edge cases ───────────────────────────────────────────────────


@pytest.mark.parametrize("server_interval", [1, 0, 5])
def test_poll_interval_has_a_6_s_floor(env, server_interval):
    env["fake"].interval = server_interval
    run(env)
    assert json.loads(pending_path(env).read_text())["interval"] == 6
    _, result = run(env, complete=True)
    assert result.details["retry_after"] >= 6


def test_slow_down_honours_retry_after(env):
    run(env)
    env["fake"].answers = [DevicePoll("slow_down", retry_after=30)]
    _, result = run(env, complete=True)
    assert result.details["retry_after"] == 30
    assert json.loads(pending_path(env).read_text())["interval"] == 30


def test_slow_down_adds_5_s(env):
    run(env)
    env["fake"].answers = [DevicePoll("slow_down")]
    _, result = run(env, complete=True)
    assert result.details["retry_after"] == 11


def test_wait_backs_off_on_server_errors(env, monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(login_module.time, "sleep", slept.append)
    run(env)
    env["fake"].answers = [
        DevicePoll("unavailable", detail="HTTP 503", retry_after=60),
        DevicePoll("unavailable", detail="HTTP 502"),
        approved(),
    ]
    write_user(env)
    _, result = run(env, complete=True, wait=True)
    assert result.status == "success"
    assert slept[0] == 60 and slept[1] == 65


def test_server_error_without_wait_keeps_the_pending_login(env):
    run(env)
    env["fake"].answers = [DevicePoll("unavailable", detail="HTTP 503")]
    _, result = run(env, complete=True)
    assert result.status == "error"
    assert pending_path(env).exists()


def test_wait_stops_at_local_expiry(env, monkeypatch):
    clock = {"now": time.time()}
    monkeypatch.setattr(
        login_module.time, "sleep", lambda s: clock.__setitem__("now", clock["now"] + s)
    )
    monkeypatch.setattr(login_state.time, "time", lambda: clock["now"])
    monkeypatch.setattr(login_module.time, "time", lambda: clock["now"])
    run(env)
    env["fake"].answers = [DevicePoll("unavailable", retry_after=3600)] * 3
    _, result = run(env, complete=True, wait=True)
    assert result.code == "auth_expired"


@pytest.mark.parametrize("body", [[], None, "text", 7])
def test_unreadable_device_code_answer_is_coded(env, monkeypatch, capsys, body):
    from unittest.mock import Mock

    def device_code(self, identity_url=None):
        response = Mock(status_code=201)
        response.json.return_value = body
        self.client.post = Mock(return_value=response)
        return _REAL_REQUEST(self, identity_url)

    monkeypatch.setattr(AuthManager, "_request_device_code", device_code)
    for kwargs in ({}, {"wait": True}):
        capsys.readouterr()
        cmd, result = run(env, **kwargs)
        assert dumped(result) == {
            "error": "Got a response from id.dx.deepgram.com that dg couldn't read.",
            "status": "error",
            "code": "network_unavailable",
            "hint": (
                "Retry once. If it fails again, tell the user that something on this "
                "network (a proxy, captive portal or firewall) is answering in "
                "Deepgram's place."
            ),
            "next_step": None,
            "schema_version": 1,
            "details": {"host": "id.dx.deepgram.com", "reason": "unreadable response"},
        }
        assert cmd.exit_code_for(result) == 1
        assert "TypeError" not in capsys.readouterr().err
    assert not pending_path(env).exists()


@pytest.mark.parametrize("interval", [-3, "6", None, True, float("nan")])
def test_unusable_pending_interval_is_no_pending_login(env, interval):
    run(env)
    data = json.loads(pending_path(env).read_text())
    data["interval"] = interval
    pending_path(env).write_text(json.dumps(data))
    os.chmod(pending_path(env), 0o600)
    _, result = run(env, complete=True)
    assert result.code == "auth_expired"
    assert [r.split()[0] for r in env["fake"].requests] == ["code"]


def test_small_pending_interval_is_raised_to_the_floor(env):
    run(env)
    data = json.loads(pending_path(env).read_text())
    data["interval"] = 0
    pending_path(env).write_text(json.dumps(data))
    os.chmod(pending_path(env), 0o600)
    _, result = run(env, complete=True)
    assert result.details["retry_after"] == 6


def test_network_error_on_complete_is_coded(env, monkeypatch):
    import httpx
    from deepctl_core.transport import TransportError

    run(env)

    def unreachable(self, device_code, identity_url=None):
        request = httpx.Request("POST", f"{IDENTITY}/oauth/token")
        raise httpx.ConnectError("[Errno 111] Connection refused", request=request)

    monkeypatch.setattr(AuthManager, "poll_device_token", unreachable)
    with pytest.raises(TransportError) as err:
        run(env, complete=True)
    assert err.value.code == "network_unavailable"
    assert err.value.error == "Couldn't reach id.dx.deepgram.com: connection refused."
    assert "device-secret-1" not in err.value.error
    assert pending_path(env).exists()


def test_complete_with_another_profile_is_a_usage_error(env):
    import click

    run(env, profile="work")
    with pytest.raises(click.UsageError, match='pending login is for profile "work"'):
        run(env, complete=True, profile="other")
    assert [r.split()[0] for r in env["fake"].requests] == ["code"]
    env["fake"].answers = [DevicePoll("pending")]
    _, result = run(env, complete=True, profile="work")
    assert result.code == "auth_pending"


def test_other_profile_error_suggests_commands_that_run(env):
    import click

    run(env, profile="work")
    with pytest.raises(click.UsageError) as err:
        run(env, complete=True, profile="my team")
    assert err.value.message == (
        'The pending login is for profile "work". Run dg login --complete '
        'without --profile, or start a new login for "my team" with dg login '
        "--profile 'my team'."
    )


def test_complete_with_save_base_url_is_a_usage_error(env):
    import click

    with pytest.raises(click.UsageError):
        run(env, complete=True, save_base_url=True)
