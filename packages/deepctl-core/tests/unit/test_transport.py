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
    # Each test gets fresh shared clients; monkeypatch puts back what was
    # there before, and the ones a test made are closed below.
    monkeypatch.setattr(transport, "_client", None)
    monkeypatch.setattr(transport, "_verified", None)
    monkeypatch.setattr(transport, "_client_insecure", False)
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
    yield
    # A keep-alive connection to a test's fake server would otherwise stay
    # open until the collector finds it, in some later test.
    for client in (transport._client, transport._verified):
        if client is not None:
            client.close()


def facade(handler) -> tuple[DeepgramClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    # _set_client applies shared_client()'s settings (no redirects, no cookies).
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
    elif reason in ("connection reset", "connection closed"):
        # Post-connect: the request had started.
        assert err.messages == transport.NETWORK_DROPPED
        assert err.error == (
            "The connection to api.deepgram.com dropped before the response "
            f"was complete ({reason})."
        )
    else:
        assert err.messages == transport.CODES["network_unavailable"]
        assert err.error == f"Couldn't reach api.deepgram.com: {reason}."


@pytest.mark.parametrize(
    ("factory", "reason", "messages"),
    [
        # A reset or close while connecting is still "couldn't reach".
        (
            _chained(
                httpx.ConnectError, ConnectionResetError(54, "Connection reset by peer")
            ),
            "connection reset",
            "E-NETWORK",
        ),
        (
            lambda r: httpx.ConnectError("Connection reset by peer", request=r),
            "connection reset",
            "E-NETWORK",
        ),
        # TLS failing on a read is a TLS failure, not a drop.
        (
            _chained(httpx.ReadError, ssl.SSLError(1, "[SSL] record layer failure")),
            "TLS handshake failed",
            "E-NETWORK",
        ),
        # After connect: the upload was being written, or the response read.
        (
            _chained(httpx.WriteError, BrokenPipeError(32, "Broken pipe")),
            "connection reset",
            "E-NETWORK-DROPPED",
        ),
        (
            _chained(
                httpx.WriteError, ConnectionResetError(104, "Connection reset by peer")
            ),
            "connection reset",
            "E-NETWORK-DROPPED",
        ),
        (
            lambda r: httpx.ReadError("", request=r),
            "connection reset",
            "E-NETWORK-DROPPED",
        ),
    ],
)
def test_dropped_only_after_connect(factory, reason, messages):
    err = mapped(raising(factory))
    assert err.code == "network_unavailable"
    assert err.details == {"host": "api.deepgram.com", "reason": reason}
    assert err.messages[0] == messages


def _reset_after_full_body() -> tuple[int, threading.Thread]:
    """A loopback server that reads the whole request, then resets."""
    import struct

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def serve() -> None:
        conn, _ = listener.accept()
        data = b""
        while b"\r\n\r\n" not in data:
            data += conn.recv(65536)
        head, _, body = data.partition(b"\r\n\r\n")
        length = next(
            int(line.split(b":", 1)[1])
            for line in head.split(b"\r\n")
            if line.lower().startswith(b"content-length:")
        )
        while len(body) < length:
            body += conn.recv(65536)
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        conn.close()
        listener.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return listener.getsockname()[1], thread


def test_reset_after_the_full_body_is_dropped_not_unreachable():
    """The whole upload arrived, then the server reset. That isn't
    "couldn't reach": the request had started (E-NETWORK-DROPPED)."""
    port, thread = _reset_after_full_body()
    with httpx.Client() as client, pytest.raises(httpx.TransportError) as raised:
        client.post(f"http://127.0.0.1:{port}/v1/listen", content=b"x" * 3145729)
    thread.join(5)
    err = transport.map_exception(raised.value)
    assert err is not None and err.code == "network_unavailable"
    # macOS raises ReadError (reset); Linux may see EOF (closed) instead.
    assert err.details["reason"] in ("connection reset", "connection closed")
    assert err.messages == transport.NETWORK_DROPPED
    assert err.error.startswith("The connection to 127.0.0.1 dropped before")


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
        ("dropped", transport.NETWORK_DROPPED),
        ("cut", transport.NETWORK_CUT),
        ("unreadable", transport.NETWORK_UNREADABLE),
        ("redirect", transport.NETWORK_REDIRECT),
    ]:
        for message_id in ids:
            if message_id:
                assert message_id in transport.STRINGS, (code, message_id)


# What dg prints for a refused redirect: 0.4.1's text, origins only.
_REDIRECT_CASES = [
    (
        {"command": "dg listen"},
        "https://api.deepgram.com answered with a redirect to "
        "https://evil.example. dg listen doesn't follow redirects, so nothing "
        "was sent there.",
    ),
    (
        {"command": "dg listen", "payload": "audio"},
        "https://api.deepgram.com answered with a redirect to "
        "https://evil.example. dg listen doesn't follow redirects, so no audio "
        "was sent.",
    ),
    (
        {"command": "dg speak", "payload": "text"},
        "https://api.deepgram.com answered with a redirect to "
        "https://evil.example. dg speak doesn't follow redirects, so no text "
        "was sent.",
    ),
]


def _refused(**kwargs: Any) -> RedirectRefused:
    return transport.redirect_refused(
        "https://api.deepgram.com/v1/listen?model=nova-3",
        "https://evil.example/steal?k=1",
        **kwargs,
    )


@pytest.mark.parametrize(("kwargs", "printed"), _REDIRECT_CASES)
def test_redirect_refusal_prints_e_network_redirect(kwargs, printed):
    """The printed refusal is E-NETWORK-REDIRECT filled in, so the catalogue
    entry that holds that string is the text users see."""
    err = _refused(**kwargs)
    assert err.message == err.error == printed
    assert err.messages[0] == "E-NETWORK-REDIRECT"
    assert transport.STRINGS["E-NETWORK-REDIRECT"].format(**err.values) == printed


@pytest.mark.skipif(
    not os.environ.get("DG_ERROR_CODES_JSON"),
    reason="DG_ERROR_CODES_JSON names no error catalogue file",
)
@pytest.mark.parametrize(("kwargs", "printed"), _REDIRECT_CASES)
def test_catalogue_redirect_text_is_what_dg_prints(kwargs, printed):
    catalogue = json.loads(Path(os.environ["DG_ERROR_CODES_JSON"]).read_text())
    template = catalogue["strings"]["E-NETWORK-REDIRECT"]
    for name in ("origin", "location_origin", "command", "sent"):
        assert "{%s}" % name in catalogue["placeholders"], name
    assert template.format(**_refused(**kwargs).values) == printed


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
        (transport.NETWORK_DROPPED, transport.REASON_RESET),
        (transport.NETWORK_DROPPED, transport.REASON_CLOSED),
        (transport.NETWORK_CUT, "response cut off"),
        (transport.NETWORK_UNREADABLE, "unreadable response"),
        (transport.NETWORK_REDIRECT, transport.REASON_REDIRECT),
    ):
        message = next(m for m in entry["messages"] if m["id"] == extra[0])
        assert (message["hint_human"], message["hint_agent"]) == extra[1:3]
        assert "hint_automation" not in message
        assert reason in entry["details_values"][extra[0]]["reason"]
    # A 3xx with credentials isn't followed, and has its own message.
    assert transport.REASON_REDIRECT not in (
        entry["details_values"]["E-NETWORK-UNREADABLE"]["reason"]
    )
    assert catalogue["transport_mapping"][0][0].split("|")[-1] == "3xx"
    for message_id in ("W-ENV-PLACEHOLDER", "W-KEYRING-TIMEOUT"):
        from deepctl_core import credentials

        text = getattr(credentials, message_id.replace("-", "_"))
        assert catalogue["strings"][message_id] == text
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


@pytest.mark.skipif(sys.platform == "win32", reason="a bare name needs PATHEXT on Windows")
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
    with pytest.raises(TransportError) as err:
        client.list_models()
    assert [r.url.host for r in seen] == ["api.deepgram.com"]
    assert seen[0].headers["Authorization"] == f"Token {KEY}"
    assert err.value.details["reason"] == "unexpected redirect"


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


# ── One Transport ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("base", "rest", "ws"),
    [
        (
            "https://api.deepgram.com",
            "https://api.deepgram.com",
            "wss://api.deepgram.com",
        ),
        ("http://127.0.0.1:8080", "http://127.0.0.1:8080", "ws://127.0.0.1:8080"),
        ("ws://127.0.0.1:8080/x", "http://127.0.0.1:8080/x", "ws://127.0.0.1:8080/x"),
        ("wss://h.example:9", "https://h.example:9", "wss://h.example:9"),
        ("h.example", "https://h.example", "wss://h.example"),
    ],
)
def test_base_derivation(base, rest, ws):
    assert transport.rest_url(base) == rest
    assert transport.ws_url(base) == ws
    assert transport.ws_url(transport.rest_url(base)) == ws


def test_transport_bases_come_from_the_host_check(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_BASE_URL", "http://127.0.0.1:8080/")
    t = transport.Transport(Config(config_path=str(Path.cwd() / "none.yaml")))
    assert t.rest_base() == "http://127.0.0.1:8080"
    assert t.ws_base() == "ws://127.0.0.1:8080"
    assert t.auth_headers(KEY) == transport.request_headers(KEY)
    assert t.auth_headers(KEY)["Authorization"] == f"Token {KEY}"
    assert t.client is transport.shared_client()


def test_request_headers_are_the_sdk_clients_headers(monkeypatch):
    """dg's own requests and the SDK's carry the same User-Agent, X-Fern
    headers, session ID and Authorization: one run, one session ID."""
    monkeypatch.setenv("DEEPGRAM_BASE_URL", "http://127.0.0.1:8080/")
    t = transport.Transport(Config(config_path=str(Path.cwd() / "none.yaml")))
    sdk = t.sdk_client(KEY)
    assert sdk._client_wrapper.get_headers() == t.auth_headers(KEY)
    sid = t.auth_headers(KEY)[transport.SESSION_HEADER]
    assert sid == transport.session_id() == sdk.session_id
    assert len(sid) == 36
    assert t.auth_headers("sk-other")[transport.SESSION_HEADER] == sid


def test_no_header_for_a_refused_host(monkeypatch):
    monkeypatch.setattr(output, "is_ai_agent", lambda: True)
    monkeypatch.setenv("DEEPGRAM_BASE_URL", "http://127.0.0.1:8080")
    t = transport.Transport(Config(config_path=str(Path.cwd() / "none.yaml")))
    with pytest.raises(hosts.CredentialHostError):
        t.auth_headers(KEY)
    with pytest.raises(hosts.CredentialHostError):
        t.sdk_client(KEY)


def test_default_host_is_hosts_constant():
    assert transport.default_host() == "api.deepgram.com"
    assert f"https://{transport.default_host()}" == hosts.DEFAULT_BASE_URL


def test_backend_seam(monkeypatch):
    calls = []

    class Fake:
        name = "fake"

        def sdk_client(self, api_key, rest_base, ws_base):
            calls.append((api_key, rest_base, ws_base))
            return "sdk"

    monkeypatch.setenv("DEEPGRAM_BASE_URL", "http://127.0.0.1:8080")
    config = Config(config_path=str(Path.cwd() / "none.yaml"))
    client = DeepgramClient(config, AuthManager(config))
    assert isinstance(client.transport.backend, transport.RestBackend)
    client.transport.backend = Fake()
    assert client.client == "sdk"
    assert calls == [(KEY, "http://127.0.0.1:8080", "ws://127.0.0.1:8080")]


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.list_models(),
        lambda c: c.get_projects(),
    ],
)
def test_redirects_are_not_followed(code, call):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            code, headers={"location": "https://sink.example/v1/projects"}
        )

    client, seen = facade(handler)
    with pytest.raises(TransportError) as err:
        call(client)
    assert [r.url.host for r in seen] == ["api.deepgram.com"]
    assert err.value.code == "network_unavailable"
    assert err.value.messages == transport.NETWORK_REDIRECT
    assert err.value.details == {
        "host": "api.deepgram.com",
        "reason": "unexpected redirect",
    }
    assert err.value.error == (
        "https://api.deepgram.com answered with a redirect to "
        "https://sink.example. dg doesn't follow redirects, so nothing was "
        "sent there."
    )


@pytest.mark.parametrize(
    ("agentic", "hard", "hint"),
    [
        (
            False,
            False,
            "A network sign-in page or proxy may be redirecting requests. If "
            "api.deepgram.com is your own server, use the address it redirects to.",
        ),
        (
            True,
            True,
            "Tell the user api.deepgram.com redirected the request. Don't retry "
            "until they have checked the network or the configured URL.",
        ),
    ],
)
def test_redirect_hints(monkeypatch, agentic, hard, hint):
    err = mapped(status(302, headers={"location": "https://sink.example/"}))
    payload = _render(err, monkeypatch, agentic=agentic, hard=hard)
    assert payload["hint"] == hint
    assert payload["next_step"] is None


def test_redirect_on_upload_sends_the_body_once(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF" + b"\0" * 64)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(307, headers={"location": "https://sink.example/x"})

    client, seen = facade(handler)
    with pytest.raises(TransportError) as err:
        client.transcribe_file(audio)
    assert len(seen) == 1 and seen[0].url.path == "/v1/listen"
    assert err.value.details["reason"] == "unexpected redirect"


def test_shared_client_follows_no_redirects(monkeypatch):
    created = {}

    class Recorder(httpx.Client):
        def __init__(self, **kwargs):
            created.update(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx, "Client", Recorder)
    monkeypatch.delenv("DEEPGRAM_CLI_INSECURE", raising=False)
    client = transport.shared_client()
    assert created["follow_redirects"] is False
    assert created.get("verify", True) is True
    assert "mounts" not in created
    assert client.follow_redirects is False


@pytest.mark.parametrize(("agent", "loopback_mounts"), [(False, True), (True, False)])
def test_insecure_flag_only_for_a_person(monkeypatch, capsys, agent, loopback_mounts):
    created = {}

    class Recorder(httpx.Client):
        def __init__(self, **kwargs):
            created.update(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx, "Client", Recorder)
    monkeypatch.setenv("DEEPGRAM_CLI_INSECURE", "1")
    monkeypatch.setattr(output, "is_ai_agent", lambda: agent)
    transport.shared_client()
    # Verification stays on; a person gets unverified loopback mounts only
    # (test_auth_insecure.py checks hosts and dg api's verified client).
    assert created.get("verify", True) is True
    assert ("mounts" in created) is loopback_mounts
    err = capsys.readouterr().err
    assert ("Ignoring DEEPGRAM_CLI_INSECURE" in err) is agent


def test_cookies_are_never_sent_back():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"models": []} if "models" in request.url.path else {"projects": []},
            headers={"set-cookie": "sid=s3cr3t; Path=/; Domain=api.deepgram.com"},
        )

    client, seen = facade(handler)
    client.list_models()
    client.get_projects()
    client.list_models()
    assert len(seen) == 3
    assert all("cookie" not in r.headers for r in seen)
    assert len(transport.shared_client().cookies.jar) == 0


def test_no_code_sets_cookies_on_the_shared_client():
    """httpx copies the client's jar into each request, so a
    cookie set in code would be sent anyway. Nothing may set one."""
    import re

    root = Path(__file__).resolve().parents[3]  # packages/
    offenders = []
    files = list(root.glob("*/src/**/*.py"))
    assert len(files) > 50
    for path in files:
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), 1):
            if re.search(r"\.cookies\.set\(|\bcookies\s*=", line):
                offenders.append(f"{path.relative_to(root)}:{number}: {line.strip()}")
    assert offenders == []


# ── WebSocket handshake mapping ──────────────────────────────────────


def _ws_refused(code: int, headers: dict | None = None) -> BaseException:
    from websockets.datastructures import Headers
    from websockets.exceptions import InvalidStatus
    from websockets.http11 import Response

    return InvalidStatus(Response(code, "x", Headers(headers or {}), b"{}"))


@pytest.mark.parametrize(
    ("code", "want"),
    [
        (401, "auth_invalid"),
        (403, "auth_forbidden"),
        (429, "rate_limited"),
        (400, "invalid_argument"),
        (500, "server_error"),
        (503, "server_error"),
        (302, "network_unavailable"),
    ],
)
def test_ws_refused_upgrade_maps_by_status(code, want):
    exc = _ws_refused(code, {"retry-after": "7", "dg-request-id": "r-1"})
    mapped = transport.map_exception(exc)
    assert mapped is not None and mapped.code == want
    assert transport.map_ws_open(exc, host="api.deepgram.com").code == want
    if code == 429:
        assert mapped.details["retry_after"] == 7
    if code == 302:
        assert mapped.details["reason"] == "unexpected redirect"
        assert mapped.messages == transport.NETWORK_REDIRECT


def test_ws_legacy_invalid_status_code():
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from websockets.datastructures import Headers
        from websockets.exceptions import InvalidStatusCode

        exc = InvalidStatusCode(401, Headers())
    assert transport.map_exception(exc).code == "auth_invalid"


@pytest.mark.parametrize(
    ("inner", "code", "reason"),
    [
        (
            socket.gaierror(-2, "Name or service not known"),
            "network_unavailable",
            "name resolution failed",
        ),
        (
            ConnectionRefusedError(111, "refused"),
            "network_unavailable",
            "connection refused",
        ),
        (
            ssl.SSLError(1, "CERTIFICATE_VERIFY_FAILED"),
            "network_unavailable",
            "TLS handshake failed",
        ),
        (TimeoutError(), "timeout", None),
    ],
)
def test_ws_open_failures(inner, code, reason):
    try:
        try:
            raise inner
        except BaseException as cause:
            raise RuntimeError("open failed") from cause
    except RuntimeError as exc:
        mapped = transport.map_ws_open(exc, host="api.deepgram.com")
    assert mapped.code == code
    assert mapped.details["host"] == "api.deepgram.com"
    if reason:
        assert mapped.details["reason"] == reason
    else:
        assert mapped.details["timeout_s"] == transport._seconds(
            transport.WS_OPEN_TIMEOUT_S
        )


def test_unrelated_errors_are_not_ws_errors():
    assert transport.map_exception(ValueError("x")) is None
    assert transport.map_ws_open(ValueError("x"), host="h") is None


def test_module_aliases_kept_for_w24_w25():
    assert callable(transport.shared_client)
    assert callable(transport.map_exception)
    assert callable(transport.raise_for)
