"""The shared HTTP client and the catalogue's transport_mapping.

Every row of the mapping goes through the real SDK facade
(``DeepgramClient``) on an ``httpx.MockTransport``, so the test sees what a
command sees. A loopback fake server checks the request and connection count.
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import click
import httpx
import pytest
import yaml
from click.testing import CliRunner
from deepctl_core import hosts, output, transport
from deepctl_core.auth import AuthManager
from deepctl_core.base_command import BaseCommand
from deepctl_core.client import DeepgramClient
from deepctl_core.config import Config
from deepctl_core.models import BaseResult
from deepctl_core.transport import TransportError
from deepctl_core.websocket import RedirectRefused

KEY = "sk-W22SENTINELKEY"
_REAL_PROG = transport.prog


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "deepctl_core.config._default_config_file",
        lambda: tmp_path / "user" / "config.yaml",
    )
    monkeypatch.setattr(
        "deepctl_core.config._trust_root_location",
        lambda: (tmp_path, tmp_path / "user" / "config.yaml"),
    )
    monkeypatch.setattr(hosts, "_reported", set())
    monkeypatch.setattr(transport, "_client", None)
    monkeypatch.setattr(output, "is_agentic", lambda: False)
    monkeypatch.setattr(output, "is_ai_agent", lambda: False)
    monkeypatch.setitem(output._output_config, "agentic", False)
    monkeypatch.setitem(output._output_config, "verbose", False)
    monkeypatch.setitem(output._output_config, "quiet", False)
    monkeypatch.setitem(output._output_config, "format", "default")
    monkeypatch.setattr(output.stderr_console, "_width", 400)
    monkeypatch.setattr(transport, "prog", lambda: "dg")
    from deepctl_core import auth as auth_module

    monkeypatch.setattr(auth_module.keyring, "get_password", lambda *a: None)
    # The SDK retries 429/5xx and connect errors with a back-off sleep.
    import deepgram.core.http_client as sdk_http

    monkeypatch.setattr(sdk_http.time, "sleep", lambda s: None)
    monkeypatch.setenv("DEEPGRAM_API_KEY", KEY)


def facade(handler) -> tuple[DeepgramClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    # Same redirect setting as transport.shared_client().
    transport._set_client(
        httpx.Client(transport=httpx.MockTransport(record), follow_redirects=False)
    )
    config = Config(config_path=str(Path.cwd() / "none.yaml"))
    auth = AuthManager(config)
    return DeepgramClient(config, auth), seen


def raising(exc_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_factory(request)

    return handler


def status(code: int, body: Any = None, headers: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(body, (bytes, str)):
            return httpx.Response(code, content=body, headers=headers)
        return httpx.Response(code, json=body or {"err_msg": "x"}, headers=headers)

    return handler


def mapped(handler, call=lambda c: c.list_models()) -> TransportError:
    client, _ = facade(handler)
    with pytest.raises(TransportError) as err:
        call(client)
    return err.value


def _chained(outer, inner):
    def make(request):
        try:
            raise inner
        except BaseException as cause:
            raise outer(str(cause), request=request) from cause

    return make


# ── transport_mapping, row by row ─────────────────────────────────────


@pytest.mark.parametrize(
    ("code", "expected", "message"),
    [
        (401, "auth_invalid", "Deepgram rejected the API key (HTTP 401)."),
        (
            403,
            "auth_forbidden",
            "This API key isn't allowed to make this request (HTTP 403).",
        ),
        (404, "not_found", "Deepgram returned 404 Not Found for /v1/models."),
        (429, "rate_limited", "Deepgram is rate limiting requests (HTTP 429)."),
        (400, "invalid_argument", "Deepgram rejected the request (HTTP 400): x"),
        (422, "invalid_argument", "Deepgram rejected the request (HTTP 422): x"),
        (418, "invalid_argument", "Deepgram rejected the request (HTTP 418): x"),
        (500, "server_error", "Deepgram returned an error (HTTP 500)."),
        (503, "server_error", "Deepgram returned an error (HTTP 503)."),
    ],
)
def test_http_status_rows(code, expected, message):
    err = mapped(status(code))
    assert err.code == expected
    assert err.error == message
    assert err.details["http_status"] == code


def test_429_retry_after_and_500_request_id():
    err = mapped(status(429, headers={"Retry-After": "7"}))
    assert err.details == {"http_status": 429, "retry_after": 7}
    err = mapped(status(503, headers={"dg-request-id": "req-123"}))
    assert err.details == {"http_status": 503, "request_id": "req-123"}


def test_sdk_retries_before_mapping_5xx():
    """The SDK's own retries still run; the code comes from the last answer."""
    client, seen = facade(status(500))
    with pytest.raises(TransportError):
        client.list_models()
    assert len(seen) == 3


def test_invalid_request_message_is_sanitized():
    body = {
        "err_msg": f"bad \x1b[31mvalue {KEY} https://u:p@h.example/x?token=1 "
        + "y" * 300
    }
    err = mapped(status(400, body))
    assert KEY not in err.error and "\x1b" not in err.error
    assert "u:p@" not in err.error and "token=1" not in err.error
    api_message = err.values["api_message"]
    assert len(api_message) <= 200


def test_invalid_request_without_body_uses_reason_phrase():
    err = mapped(status(400, b""))
    assert err.error == "Deepgram rejected the request (HTTP 400): Bad Request"


@pytest.mark.parametrize(
    ("factory", "reason"),
    [
        (
            _chained(
                httpx.ConnectError, socket.gaierror(-2, "Name or service not known")
            ),
            "name resolution failed",
        ),
        (
            _chained(
                httpx.ConnectError, ConnectionRefusedError(111, "Connection refused")
            ),
            "connection refused",
        ),
        (
            _chained(
                httpx.ConnectError,
                ssl.SSLError(1, "[SSL: WRONG_VERSION_NUMBER] wrong version number"),
            ),
            "TLS handshake failed",
        ),
        (
            _chained(
                httpx.ReadError, ConnectionResetError(104, "Connection reset by peer")
            ),
            "connection reset",
        ),
        (
            lambda r: httpx.RemoteProtocolError(
                "peer closed connection without sending complete message body: incomplete chunked read",
                request=r,
            ),
            "response cut off",
        ),
        (
            lambda r: httpx.RemoteProtocolError(
                "Server disconnected without sending a response.", request=r
            ),
            "connection closed",
        ),
    ],
)
def test_network_rows(factory, reason):
    err = mapped(raising(factory))
    assert err.code == "network_unavailable"
    assert err.details == {"host": "api.deepgram.com", "reason": reason}
    if reason == "response cut off":
        assert err.messages == transport.NETWORK_CUT
        assert err.error == (
            "The connection to api.deepgram.com dropped before the response finished."
        )
    else:
        assert err.messages == transport.CODES["network_unavailable"]
        assert err.error == f"Couldn't reach api.deepgram.com: {reason}."


def test_unreadable_2xx_hint_names_host_and_prog(monkeypatch):
    err = mapped(status(200, b"<html>sign in</html>"))
    payload = _render(err, monkeypatch, agentic=False, hard=False)
    assert payload["hint"] == transport.STRINGS["H-NETWORK-UNREADABLE"].format(
        prog="dg", host="api.deepgram.com"
    )


def test_proxy_refusal_row():
    err = mapped(raising(lambda r: httpx.ProxyError("407 Proxy Auth", request=r)))
    assert err.code == "network_unavailable"
    assert err.details["reason"] == "the proxy refused the connection"


@pytest.mark.parametrize(
    "exc",
    [httpx.ReadTimeout, httpx.ConnectTimeout, httpx.WriteTimeout, httpx.PoolTimeout],
)
def test_timeout_row(exc):
    err = mapped(raising(lambda r: exc("timed out", request=r)))
    assert err.code == "timeout"
    assert err.details == {"host": "api.deepgram.com", "timeout_s": 60}
    assert err.error == "api.deepgram.com didn't respond within 60 s."


@pytest.mark.parametrize(
    "body", [b'{"models": [{"name": "nov', b"<html>captive portal</html>"]
)
def test_unreadable_2xx_body_row(body):
    """A 2xx whose body can't be read is a network failure, not success."""
    err = mapped(status(200, body))
    assert err.code == "network_unavailable"
    assert err.details == {"host": "api.deepgram.com", "reason": "unreadable response"}
    assert err.messages == transport.NETWORK_UNREADABLE
    assert err.error == "Got a response from api.deepgram.com that dg couldn't read."


def test_speak_rest_maps_while_reading():
    """The speak request happens while the command iterates the audio."""
    client, _ = facade(status(401))
    chunks = client.speak_text("hi")
    with pytest.raises(TransportError) as err:
        list(chunks)
    assert err.value.code == "auth_invalid"


def test_prerecorded_listen_maps(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF" + b"\0" * 64)
    err = mapped(status(401), call=lambda c: c.transcribe_file(audio))
    assert err.code == "auth_invalid"


def test_non_transport_errors_keep_legacy_text():
    """A failure that isn't a request failure keeps its 0.4.0 wording."""
    from deepgram.core.api_error import ApiError

    client, seen = facade(status(200, {"projects": []}))
    with pytest.raises(FileNotFoundError, match="Audio file not found"):
        client.transcribe_file("/does/not/exist.wav")
    client._resolve_project_id = lambda project_id=None: "p1"

    def broken(*args, **kwargs):
        raise LookupError("sdk bug")

    client.client.manage.v1.models.list = broken
    with pytest.raises(ApiError) as err:
        client.list_models()
    assert err.value.body == "Failed to list models: sdk bug"
    assert seen == []


def test_every_code_is_catalogued():
    for code, ids in [
        *transport.CODES.items(),
        ("cut", transport.NETWORK_CUT),
        ("unreadable", transport.NETWORK_UNREADABLE),
    ]:
        for message_id in ids:
            if message_id:
                assert message_id in transport.STRINGS, (code, message_id)


@pytest.mark.skipif(
    not os.environ.get("DG_ERROR_CODES_JSON"),
    reason="DG_ERROR_CODES_JSON names no error catalogue file",
)
def test_strings_match_catalogue():
    catalogue = json.loads(Path(os.environ["DG_ERROR_CODES_JSON"]).read_text())
    assert [row[1] for row in catalogue["transport_mapping"]] == [
        "network_unavailable",
        "timeout",
        "auth_invalid",
        "auth_forbidden",
        "not_found",
        "rate_limited",
        "invalid_argument",
        "server_error",
    ]
    for message_id, text in transport.STRINGS.items():
        assert catalogue["strings"][message_id] == text, message_id
    for code, (error_id, human, agent, automation) in transport.CODES.items():
        entry = catalogue["codes"][code]
        assert entry["exit"] == 1
        message = next(m for m in entry["messages"] if m["id"] == error_id)
        assert message["hint_human"] == human
        assert message["hint_agent"] == agent
        assert message.get("hint_automation") == automation
    entry = catalogue["codes"]["network_unavailable"]
    for extra, reason in (
        (transport.NETWORK_CUT, "response cut off"),
        (transport.NETWORK_UNREADABLE, "unreadable response"),
    ):
        message = next(m for m in entry["messages"] if m["id"] == extra[0])
        assert (message["hint_human"], message["hint_agent"]) == extra[1:3]
        assert "hint_automation" not in message
        assert entry["details_values"][extra[0]] == {"reason": [reason]}
    env = catalogue["codes"]["auth_invalid"]["env_key_variant"]
    assert env == {
        "hint_agent": "A-AUTH-INVALID-ENV",
        "hint_agent_if_next_step_null": "A-AUTH-INVALID-ENV-NULL",
    }


# ── Rendering: hint variant, next_step, key order ────────────────────


def _render(err, monkeypatch, *, agentic, hard, command_path="models", key_env=True):
    monkeypatch.setitem(output._output_config, "agentic", agentic)
    monkeypatch.setattr(output, "is_ai_agent", lambda: hard)
    if not key_env:
        monkeypatch.delenv("DEEPGRAM_API_KEY")
    config = Config(config_path=str(Path.cwd() / "none.yaml"))
    auth = AuthManager(config, explicit_api_key=None if key_env else KEY)
    return transport.render(
        err, command_path=command_path, auth_manager=auth
    ).model_dump()


def _401():
    return TransportError("auth_invalid", {}, {"http_status": 401})


def test_auth_invalid_hard_agent_env_key(monkeypatch):
    payload = _render(_401(), monkeypatch, agentic=True, hard=True)
    assert list(payload) == [
        "error",
        "status",
        "code",
        "hint",
        "next_step",
        "schema_version",
        "details",
    ]
    assert payload == {
        "error": "Deepgram rejected the API key (HTTP 401).",
        "status": "error",
        "code": "auth_invalid",
        "hint": transport.STRINGS["A-AUTH-INVALID-ENV"],
        "next_step": "dg login --yes",
        "schema_version": 1,
        "details": {"http_status": 401, "key_source": "DEEPGRAM_API_KEY (env)"},
    }


def test_auth_invalid_hard_agent_env_key_without_prog(monkeypatch):
    monkeypatch.setattr(transport, "prog", lambda: None)
    payload = _render(_401(), monkeypatch, agentic=True, hard=True)
    assert payload["next_step"] is None
    assert payload["hint"] == transport.STRINGS["A-AUTH-INVALID-ENV-NULL"].format(
        prog="dg"
    )


def test_auth_invalid_soft_only_ci(monkeypatch):
    payload = _render(_401(), monkeypatch, agentic=True, hard=False)
    assert payload["next_step"] is None
    assert payload["hint"] == transport.STRINGS["C-AUTH-INVALID"]


def test_auth_invalid_hard_agent_flag_key(monkeypatch):
    payload = _render(_401(), monkeypatch, agentic=True, hard=True, key_env=False)
    assert payload["next_step"] == "dg login"
    assert payload["hint"] == transport.STRINGS["A-AUTH-INVALID"].format(prog="dg")
    assert payload["details"]["key_source"] == "--api-key"


def test_auth_invalid_human(monkeypatch):
    payload = _render(_401(), monkeypatch, agentic=False, hard=False)
    assert payload["next_step"] == "dg login"
    assert payload["hint"] == transport.STRINGS["H-AUTH-INVALID"].format(prog="dg")


def test_key_source_follows_whoami_order(monkeypatch):
    """A keyring key equal to the env value is the keyring's, as whoami says."""
    from deepctl_core import auth as auth_module

    monkeypatch.setattr(auth_module.keyring, "get_password", lambda *a: KEY)
    payload = _render(_401(), monkeypatch, agentic=True, hard=True)
    assert payload["details"]["key_source"] == "system keyring"
    assert payload["next_step"] == "dg login"


def test_invalid_argument_next_step_is_command_help(monkeypatch):
    err = TransportError(
        "invalid_argument",
        {"http_status": 400, "api_message": "x"},
        {"http_status": 400},
    )
    payload = _render(err, monkeypatch, agentic=True, hard=True, command_path="keys")
    assert payload["next_step"] == "dg keys --help"
    assert payload["hint"] == transport.STRINGS["A-INVALID-REQUEST"].format(
        prog="dg", command_path="keys"
    )


@pytest.mark.parametrize(
    "code",
    [
        "auth_forbidden",
        "not_found",
        "rate_limited",
        "server_error",
        "timeout",
        "network_unavailable",
    ],
)
def test_other_codes_have_null_next_step(code, monkeypatch):
    values = {
        "host": "h",
        "reason": "r",
        "timeout_s": 60,
        "path": "/p",
        "http_status": 500,
    }
    err = TransportError(code, values, {"http_status": 1})
    payload = _render(err, monkeypatch, agentic=True, hard=True)
    assert payload["next_step"] is None and payload["schema_version"] == 1


def test_details_omitted_when_empty():
    cls = transport.result_class()
    payload = cls(error="e", code="c", hint="h").model_dump()
    assert list(payload) == [
        "error",
        "status",
        "code",
        "hint",
        "next_step",
        "schema_version",
    ]


def test_prog_bare_name_only_when_which_matches(monkeypatch, tmp_path):
    script = tmp_path / "bin" / "dg"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    monkeypatch.setattr(sys, "argv", [str(script)])
    monkeypatch.setenv("PATH", str(script.parent))
    assert _REAL_PROG() == "dg"
    monkeypatch.setenv("PATH", "/nonexistent")
    assert _REAL_PROG() == str(script)
    monkeypatch.setattr(sys, "argv", ["/x/__main__.py"])
    assert _REAL_PROG() is None


# ── Through BaseCommand: stdout payload, two stderr lines, exit 1 ─────


class _Models(BaseCommand):
    name = "models"
    help = "List models"
    requires_auth = True

    def handle(self, config, auth_manager, client, **kwargs):
        from deepctl_core.output import get_status_console

        get_status_console().print("[blue]Fetching models...[/blue]")
        try:
            client.list_models()
        except Exception as e:  # what every command does today
            get_status_console().print(f"[red]Error:[/red] {e}")
            return BaseResult(status="error", message=str(e))
        return BaseResult(status="success")


def _run(
    monkeypatch,
    handler,
    *,
    agentic,
    hard,
    fmt="json",
    config_path="none.yaml",
    name="models",
):
    facade(handler)
    monkeypatch.setitem(output._output_config, "agentic", agentic)
    monkeypatch.setitem(output._output_config, "format", fmt)
    monkeypatch.setattr(output, "is_ai_agent", lambda: hard)
    cmd = type("_Cmd", (_Models,), {"name": name})()

    @click.command(name)
    @click.pass_context
    def models(ctx):
        cmd.execute(ctx)

    @click.group("dg")
    @click.pass_context
    def root(ctx):
        ctx.obj = {"config": Config(config_path=str(Path.cwd() / config_path))}

    root.add_command(models)
    return CliRunner().invoke(root, [name])


def _split(result):
    return result.stdout, result.stderr


def test_mode_a_two_stderr_lines_and_coded_stdout(monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY")
    Path("user").mkdir()
    Path("user/config.yaml").write_text(
        yaml.safe_dump({"profiles": {"default": {"api_key": KEY}}})
    )
    Path("user/config.yaml").chmod(0o600)
    result = _run(
        monkeypatch,
        status(401),
        agentic=True,
        hard=True,
        config_path="user/config.yaml",
    )
    stdout, stderr = _split(result)
    assert result.exit_code == 1
    payload = json.loads(stdout)
    assert payload["code"] == "auth_invalid"
    assert payload["next_step"] == "dg login"
    assert payload["details"] == {"http_status": 401, "key_source": "config file"}
    assert stderr.splitlines() == [
        "ERROR: Deepgram rejected the API key (HTTP 401).",
        "HINT: " + transport.STRINGS["A-AUTH-INVALID"].format(prog="dg"),
    ]
    assert KEY not in stdout + stderr


def test_human_mode_renders_cross_and_hint(monkeypatch):
    result = _run(monkeypatch, status(403), agentic=False, hard=False, fmt="default")
    stdout, stderr = _split(result)
    assert result.exit_code == 1
    assert stdout == ""
    lines = [line for line in stderr.splitlines() if line.strip()]
    assert lines[-2:] == [
        "✗ This API key isn't allowed to make this request (HTTP 403).",
        "  " + transport.STRINGS["H-AUTH-FORBIDDEN"].format(prog="dg"),
    ]


def test_success_in_mode_a_drops_chrome_only(monkeypatch):
    result = _run(monkeypatch, status(200, {"models": []}), agentic=True, hard=False)
    stdout, stderr = _split(result)
    assert result.exit_code == 0
    assert "Fetching models" not in stderr
    assert json.loads(stdout)["status"] == "success"


def test_other_commands_keep_chrome_in_mode_a(monkeypatch):
    """Only commands that go through the Transport drop chrome: login's
    device code, dg api's errors and the like stay on stderr."""
    result = _run(
        monkeypatch, status(200, {"models": []}), agentic=True, hard=True, name="login"
    )
    assert "Fetching models" in result.stderr


def test_verbose_keeps_chrome(monkeypatch):
    monkeypatch.setitem(output._output_config, "verbose", True)
    result = _run(monkeypatch, status(200, {"models": []}), agentic=True, hard=False)
    assert "Fetching models" in result.stderr


def test_failure_after_stdout_payload_keeps_json_off_stdout(monkeypatch):
    """Once a command has written payload bytes to stdout (speak audio), a
    failure goes to stderr only."""

    class _Audio(_Models):
        def handle(self, config, auth_manager, client, **kwargs):
            import sys

            self.stdout_is_protocol = True
            sys.stdout.write("AUDIOBYTES")
            client.list_models()

    monkeypatch.setattr(sys.modules[__name__], "_Models", _Audio)
    result = _run(monkeypatch, status(200, b"cut"), agentic=True, hard=True)
    assert result.exit_code == 1
    assert result.stdout == "AUDIOBYTES"
    assert result.stderr.splitlines()[-2].startswith("ERROR: Got a response from")


def test_unmapped_failure_keeps_legacy_handling(monkeypatch):
    """Errors that aren't request failures still go through the command's
    own handler, prefixed lines intact."""

    def boom(request):
        raise ValueError("not a transport problem")

    result = _run(monkeypatch, boom, agentic=True, hard=False)
    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert json.loads(result.stdout)["status"] == "error"


# ── One connection, no pre-flight: a real loopback server ─────────────


class _Fake(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    log: ClassVar[list[tuple[str, str, int]]] = []

    def do_GET(self):
        _Fake.log.append(
            (self.command, self.path.split("?")[0], self.client_address[1])
        )
        body = json.dumps({"api_keys": [], "projects": [], "models": []}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_server():
    _Fake.log = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_one_request_one_connection_no_preflight(fake_server, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_BASE_URL", fake_server)
    monkeypatch.setenv("DEEPGRAM_PROJECT_ID", "p1")
    config = Config(config_path=str(Path.cwd() / "none.yaml"))
    auth = AuthManager(config)
    auth.guard()
    client = DeepgramClient(config, auth)
    client.list_keys()
    client.list_models()
    paths = [p for _, p, _ in _Fake.log]
    assert paths == ["/v1/projects/p1/keys", "/v1/models"]
    assert "/v1/projects" not in paths
    assert len({port for *_, port in _Fake.log}) == 1


def test_shared_client_keeps_no_cookies():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200, json={"models": []}, headers={"Set-Cookie": "sid=abc; Path=/"}
        )

    client, _ = facade(handler)
    client.list_models()
    client.list_models()
    assert "cookie" not in seen[1].headers
    assert not list(transport._client.cookies.jar)


def test_shared_client_settings():
    client = transport.shared_client()
    assert client.follow_redirects is False
    assert client.timeout.read == transport.DEFAULT_TIMEOUT_S
    assert "Authorization" not in client.headers
    assert str(client.base_url) == ""


def test_redirect_to_another_origin_is_not_followed():
    """The shared client follows no redirect, so neither the key nor a
    request body can reach another origin."""
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        if request.url.host == "api.deepgram.com":
            return httpx.Response(
                302, headers={"Location": "https://evil.example/v1/models"}
            )
        return httpx.Response(200, json={"models": []})

    client, _ = facade(handler)
    with pytest.raises(RedirectRefused):
        client.list_models()
    assert [r.url.host for r in seen] == ["api.deepgram.com"]
    assert seen[0].headers["Authorization"] == f"Token {KEY}"


def test_shared_client_is_lazy_and_single():
    code = (
        "import sys, deepctl_core.transport as t\n"
        "print('httpx' in sys.modules, 'ssl' in sys.modules)\n"
        "assert t.shared_client() is t.shared_client()\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "False False"


@pytest.mark.parametrize("ignore_quiet", [False, True])
def test_print_warning_ignore_quiet(ignore_quiet, monkeypatch, capsys):
    monkeypatch.setitem(output._output_config, "quiet", True)
    monkeypatch.setitem(output._output_config, "agentic", True)
    output.print_warning("needed", stderr=True, ignore_quiet=ignore_quiet)
    assert ("WARN: needed" in capsys.readouterr().err) is ignore_quiet
