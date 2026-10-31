"""`dg api` attaches the API key only to hosts that may receive it."""

from __future__ import annotations

import json

import httpx
import pytest
import yaml
from deepctl_cmd_api import command as api_module
from deepctl_cmd_api.command import ApiCommand
from deepctl_core import AuthManager, Config, DeepgramClient, hosts, output
from deepctl_core.hosts import CredentialHostError


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # This test's user config is the one at the default location.
    monkeypatch.setattr(
        "deepctl_core.config._default_config_file", lambda: tmp_path / "user.yaml"
    )
    # In agent sessions too (location rules: test_config_location).
    monkeypatch.setattr(
        "deepctl_core.config._trust_root_location", lambda: (tmp_path, tmp_path / "user.yaml")
    )
    monkeypatch.setattr(hosts, "_reported", set())
    monkeypatch.setattr(output, "is_agentic", lambda: False)
    monkeypatch.setattr(output, "is_ai_agent", lambda: False)
    monkeypatch.setattr(api_module, "is_agentic", lambda: False)
    monkeypatch.setattr(api_module, "is_ai_agent", lambda: False)
    output._output_config.update(agentic=False, quiet=False, format="default")
    monkeypatch.setattr(output.stderr_console, "_width", 400)


@pytest.fixture
def agentic(monkeypatch):
    monkeypatch.setattr(output, "is_agentic", lambda: True)
    monkeypatch.setattr(output, "is_ai_agent", lambda: True)
    monkeypatch.setattr(api_module, "is_agentic", lambda: True)
    monkeypatch.setattr(api_module, "is_ai_agent", lambda: True)
    output._output_config["agentic"] = True


@pytest.fixture
def sent(monkeypatch):
    """Requests that reached the transport; nothing leaves the process."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        # A streamed body, so httpx records .elapsed as on a real response.
        if request.url.path.startswith("/redir"):
            return httpx.Response(
                302,
                headers={"Location": "https://evil.example/x"},
                stream=httpx.ByteStream(b""),
            )
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            stream=httpx.ByteStream(b'{"ok": true}'),
        )

    class Shared(httpx.Client):
        """The shared client; `dg api` must ask for no redirects per request."""

        def request(self, *args, **kwargs):
            assert kwargs["follow_redirects"] is False
            return super().request(*args, **kwargs)

    from deepctl_core import transport

    monkeypatch.setattr(transport, "_client", None)
    transport._set_client(Shared(transport=httpx.MockTransport(handler)))
    return seen


def run(tmp_path, endpoint, *, user=None, flag=None, **kwargs):
    path = tmp_path / "user.yaml"
    data = {"profiles": {"default": {"api_key": "sk-USERKEY"}}}
    data.update(user or {})
    path.write_text(yaml.safe_dump(data))
    path.chmod(0o600)  # as dg writes it, whatever the umask
    config = Config(config_path=str(path), base_url=flag)
    auth = AuthManager(config)
    return ApiCommand().handle(
        config, auth, DeepgramClient(config, auth), endpoint=endpoint, **kwargs
    )


def auth_of(request: httpx.Request) -> str | None:
    return request.headers.get("Authorization")


def test_relative_path_uses_profile_base_url(tmp_path, sent):
    user = {
        "profiles": {
            "default": {
                "api_key": "sk-USERKEY",
                "base_url": "https://api.eu.deepgram.com",
            }
        }
    }
    result = run(tmp_path, "/v1/projects", user=user)
    assert result.status == "success"
    assert str(sent[0].url) == "https://api.eu.deepgram.com/v1/projects"
    assert auth_of(sent[0]) == "Token sk-USERKEY"


def test_relative_path_against_loopback_env(tmp_path, sent, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_BASE_URL", "http://127.0.0.1:8080")
    run(tmp_path, "/v1/projects")
    assert sent[0].url.host == "127.0.0.1"
    assert auth_of(sent[0]) == "Token sk-USERKEY"


def test_foreign_flag_base_refused_before_request(tmp_path, sent):
    with pytest.raises(CredentialHostError) as err:
        run(tmp_path, "/v1/projects", flag="https://evil.example")
    assert err.value.code == "foreign_host"
    assert sent == []


def test_third_party_host_gets_no_key(tmp_path, sent, capsys):
    result = run(tmp_path, "https://evil.example/anything")
    assert result.status == "success"
    assert auth_of(sent[0]) is None
    err = capsys.readouterr().err
    assert "Sending the request to evil.example without your API key" in err
    assert "--allow-foreign-host=evil.example" in err


def test_same_host_other_port_gets_no_key(tmp_path, sent, monkeypatch):
    """Configured loopback :8001 doesn't approve :8000 (sec/run_api.sh)."""
    monkeypatch.setenv("DEEPGRAM_BASE_URL", "http://127.0.0.1:8001")
    run(tmp_path, "http://127.0.0.1:8000/anything")
    assert auth_of(sent[0]) is None


def test_noauth_warning_ignores_quiet(tmp_path, sent, capsys):
    output._output_config["quiet"] = True
    run(tmp_path, "https://evil.example/x")
    assert "without your API key" in capsys.readouterr().err


def test_regional_absolute_url_gets_key(tmp_path, sent):
    run(tmp_path, "https://api.au.deepgram.com/v1/projects")
    assert auth_of(sent[0]) == "Token sk-USERKEY"


def test_allowlisted_absolute_url_gets_key(tmp_path, sent):
    run(
        tmp_path,
        "https://staging.example/v1/x",
        user={"api": {"allowed_hosts": ["staging.example"]}},
    )
    assert auth_of(sent[0]) == "Token sk-USERKEY"


def test_override_matching_host_sends_key_over_https(tmp_path, sent):
    run(tmp_path, "https://evil.example/x", allow_foreign_host="evil.example")
    assert auth_of(sent[0]) == "Token sk-USERKEY"


def test_override_over_http_refused(tmp_path, sent, capsys):
    result = run(
        tmp_path, "http://evil.example/x?token=abc", allow_foreign_host="evil.example"
    )
    assert sent == []
    assert result.code == "insecure_base_url"
    assert result.url == "http://evil.example/x"
    assert (
        "keys are sent over plain http only to a loopback host you configured "
        "or approved" in capsys.readouterr().err
    )


def test_override_loopback_http_refused(tmp_path, sent):
    """The override sends the key over https only, even to loopback."""
    result = run(tmp_path, "http://127.0.0.1:8000/x", allow_foreign_host="127.0.0.1")
    assert sent == []
    assert result.code == "insecure_base_url"
    assert result.message == (
        "Not sending your API key to http://127.0.0.1:8000: keys are sent over "
        "plain http only to a loopback host you configured or approved. Nothing "
        "was sent."
    )


def test_allowlisted_loopback_http_gets_key(tmp_path, sent):
    run(
        tmp_path,
        "http://127.0.0.1:8000/x",
        user={"api": {"allowed_hosts": ["127.0.0.1"]}},
    )
    assert auth_of(sent[0]) == "Token sk-USERKEY"


def test_override_mismatch_refused(tmp_path, sent):
    result = run(tmp_path, "https://evil.example/x", allow_foreign_host="other.example")
    assert sent == []
    assert result.status == "error"
    assert result.code == "foreign_host"
    assert result.message == (
        "--allow-foreign-host=other.example does not match the request host "
        "evil.example. Nothing was sent."
    )


def test_agent_override_refused(tmp_path, sent, agentic, capsys):
    result = run(tmp_path, "http://127.0.0.1:8000/x", allow_foreign_host="127.0.0.1")
    assert sent == []
    assert result.code == "foreign_host"
    assert result.hint == (
        "A person must approve 127.0.0.1 in the deepctl user config. If you are "
        "an AI agent, ask the user."
    )
    payload = json.dumps(result.model_dump())
    captured = capsys.readouterr()
    for text in (captured.out, captured.err, payload):
        assert "allow-foreign-host" not in text


def test_agent_override_allowed_when_allowlisted(tmp_path, sent, agentic):
    run(
        tmp_path,
        "https://evil.example/x",
        allow_foreign_host="evil.example",
        user={"api": {"allowed_hosts": ["evil.example"]}},
    )
    assert auth_of(sent[0]) == "Token sk-USERKEY"


def test_agent_noauth_warning_does_not_name_flag(tmp_path, sent, agentic, capsys):
    run(tmp_path, "https://evil.example/x")
    captured = capsys.readouterr()
    assert "without your API key" in captured.err
    assert "allow-foreign-host" not in captured.err + captured.out


def test_agent_env_base_url_refused(tmp_path, sent, agentic, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_BASE_URL", "https://evil.example")
    with pytest.raises(CredentialHostError):
        run(tmp_path, "/v1/projects")
    assert sent == []


def test_project_file_allowlist_ignored(tmp_path, sent):
    (tmp_path / "deepgram.yaml").write_text("api:\n  allowed_hosts: [evil.example]\n")
    run(tmp_path, "https://evil.example/x")
    assert auth_of(sent[0]) is None


def test_saved_foreign_profile_base_url_refused(tmp_path, sent):
    user = {
        "profiles": {
            "default": {"api_key": "sk-USERKEY", "base_url": "http://127.0.0.1:8000"}
        }
    }
    with pytest.raises(CredentialHostError) as err:
        run(tmp_path, "/v1/projects", user=user)
    assert err.value.message_id == "E-FH-PROFILE"
    assert sent == []


def test_redirect_not_followed(tmp_path, sent, capsys):
    result = run(tmp_path, "/redir")
    assert len(sent) == 1
    assert result.status_code == 302
    assert (
        "https://api.deepgram.com answered with a redirect to https://evil.example. "
        "dg api doesn't follow redirects."
    ) in capsys.readouterr().err


def test_error_payload_shape(tmp_path, sent):
    result = run(
        tmp_path,
        "https://user:pw@evil.example/x?q=1#f",
        allow_foreign_host="nope.example",
        method="POST",
    )
    assert list(result.model_dump()) == [
        "status",
        "message",
        "method",
        "url",
        "status_code",
        "response_body",
        "elapsed_ms",
        "code",
        "hint",
    ]
    assert result.url == "https://evil.example/x"
    assert result.status_code == 0


def test_flag_registered():
    names = [n for a in ApiCommand().get_arguments() for n in a.get("names", [])]
    assert "--allow-foreign-host" in names


def test_agent_override_refused_before_credential_check(
    tmp_path, sent, agentic, capsys, monkeypatch
):
    """The refusal comes first: not even the key check request is made."""
    from unittest.mock import patch

    import click

    path = tmp_path / "user.yaml"
    path.write_text(yaml.safe_dump({"profiles": {"default": {"api_key": "sk-U"}}}))
    path.chmod(0o600)
    config = Config(config_path=str(path))
    output._output_config["format"] = "json"
    ctx = click.Context(click.Command("api"), obj={"config": config})
    with (
        patch.object(AuthManager, "guard", side_effect=AssertionError("no")),
        pytest.raises(SystemExit) as exc,
    ):
        ApiCommand().execute(
            ctx,
            endpoint="http://127.0.0.1:8000/x",
            allow_foreign_host="127.0.0.1",
            method="GET",
        )
    assert exc.value.code == 1
    assert sent == []
    out = capsys.readouterr().out
    assert '"code": "foreign_host"' in out
    assert "allow-foreign-host" not in out


BANNED = ("allowed_hosts", "DEEPGRAM_BASE_URL", "--allow-foreign-host")


def test_no_agent_hint_names_a_way_to_approve_a_host():
    """Agent-mode hints never teach a key, variable or flag."""
    from deepctl_cmd_api import messages as api_msg
    from deepctl_core import messages as core_msg

    hints = [
        getattr(module, name)
        for module in (core_msg, api_msg)
        for name in dir(module)
        if "AGENT" in name and name.endswith("HINT")
    ]
    for table in (core_msg.W_PF_MESSAGES, core_msg.W_CF_MESSAGES):
        hints += [agent for _, _, agent in table.values() if agent]
    assert len(hints) >= 8
    for hint in hints:
        for banned in BANNED:
            assert banned not in hint, (banned, hint)
