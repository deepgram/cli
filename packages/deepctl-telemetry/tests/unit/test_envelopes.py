"""What actually leaves the process: envelopes from a capturing transport.

The SDK is initialized by the production error path with the production
settings, but a transport that records envelopes replaces the network.
Every test drives a real failure through ``report_exception`` (or the
installed hooks) and inspects the serialized payload.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import click
import pytest
import sentry_sdk
from click.testing import CliRunner
from deepctl_core.telemetry_context import record_command, reset_command_context
from deepctl_telemetry import client
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

ARGV = ["dg", "--api-key", "sk-TEST", "read", "--text", "secret"]
HEADER_SENTINEL = "HEADERSENTINEL-Bearer-sk-TEST"
MESSAGE_SENTINEL = "MSG-SENTINEL"

FORBIDDEN_KEYS = {
    "vars",
    "abs_path",
    "filename",
    "pre_context",
    "context_line",
    "post_context",
    "breadcrumbs",
    "modules",
    "extra",
    "contexts",
    "request",
    "user",
    "server_name",
    "message",
    "logentry",
    "logger",
    "value",
}
FORBIDDEN_ITEM_TYPES = {
    "transaction",
    "session",
    "sessions",
    "log",
    "client_report",
    "profile",
    "attachment",
}


class CapturingTransport(Transport):
    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.envelopes: list[Envelope] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        self.envelopes.append(envelope)


def _items(transport: CapturingTransport) -> list[tuple[str, dict[str, Any]]]:
    items = []
    for envelope in transport.envelopes:
        for item in envelope.items:
            items.append((item.type or "", json.loads(item.get_bytes())))
    return items


def _serialized(transport: CapturingTransport) -> str:
    return "\n".join(
        envelope.serialize().decode("utf-8", "replace")
        for envelope in transport.envelopes
    )


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        found = set(value)
        for child in value.values():
            found |= _keys(child)
        return found
    if isinstance(value, list):
        found = set()
        for child in value:
            found |= _keys(child)
        return found
    return set()


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Initialize with production options, swapping in a capturing transport."""
    capturing = CapturingTransport()
    real_init = sentry_sdk.init

    def init_with_capture(*args: Any, **kwargs: Any) -> Any:
        kwargs["transport"] = capturing
        return real_init(*args, **kwargs)

    saved_excepthook = sys.excepthook
    saved_thread_hook = threading.excepthook
    monkeypatch.setattr(sentry_sdk, "init", init_with_capture)
    monkeypatch.delenv(client.DISABLE_ENV_VAR, raising=False)
    monkeypatch.setenv(client.DSN_ENV_VAR, "http://public@127.0.0.1:9/1")
    monkeypatch.setattr(sys, "argv", list(ARGV))
    monkeypatch.setattr(client, "_state", client._fresh_state())
    default = Path(tempfile.mkdtemp()) / "default-config.yaml"
    monkeypatch.setattr(client, "_default_config_path", lambda: default)
    # Generous budget: these tests check content, not timing.
    monkeypatch.setattr(client, "FLUSH_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr(client, "REPORT_BUDGET_SECONDS", 5.0)
    reset_command_context()
    yield capturing
    sentry_sdk.get_client().close()
    sentry_sdk.get_global_scope().set_client(None)
    reset_command_context()
    sys.excepthook = saved_excepthook
    threading.excepthook = saved_thread_hook


def _config(enabled: bool = True) -> Any:
    path = Path(tempfile.mkdtemp()) / "config.yaml"
    flag = "true" if enabled else "false"
    path.write_text(f"telemetry:\n  enabled: {flag}\n", encoding="utf-8")
    return SimpleNamespace(config_path=path)


def _record_sentinel_command() -> None:
    """Record context from a real Click parse of the sentinel argv."""

    @click.group()
    @click.option("--api-key")
    def root(api_key: str | None) -> None:
        pass

    @root.command()
    @click.option("--text")
    @click.pass_context
    def read(ctx: click.Context, text: str | None) -> None:
        record_command(ctx)

    result = CliRunner().invoke(root, ARGV[1:], prog_name="dg")
    assert result.exit_code == 0, result.output


def _fail_with_sentinels() -> None:
    """Fail in a frame that holds sentinel values, then report it."""
    headers = {"Authorization": HEADER_SENTINEL}  # noqa: F841
    home = os.path.expanduser("~")
    try:
        raise RuntimeError(f"{MESSAGE_SENTINEL} {home} {socket.gethostname()}")
    except RuntimeError as exc:
        logging.getLogger("deepctl").error(
            "request failed %s %s", MESSAGE_SENTINEL, headers
        )
        logging.getLogger("deepctl").exception(MESSAGE_SENTINEL)
        client.report_exception(exc)


def test_error_envelope_contains_only_allowlisted_fields(
    transport: CapturingTransport,
) -> None:
    assert client.init_telemetry(_config(True)) is True
    _record_sentinel_command()
    _fail_with_sentinels()

    items = _items(transport)
    assert [kind for kind, _ in items] == ["event"]
    event = items[0][1]

    payload = _serialized(transport)
    for sentinel in (
        "sk-TEST",
        "secret",
        HEADER_SENTINEL,
        "HEADERSENTINEL",
        MESSAGE_SENTINEL,
        "Authorization",
        socket.gethostname(),
        os.path.expanduser("~"),
    ):
        assert sentinel not in payload, sentinel

    assert not (_keys(event) & FORBIDDEN_KEYS), _keys(event) & FORBIDDEN_KEYS
    assert set(event) <= {
        "event_id",
        "timestamp",
        "platform",
        "level",
        "release",
        "environment",
        "sdk",
        "tags",
        "exception",
    }

    (exc,) = event["exception"]["values"]
    assert exc["type"] == "RuntimeError"
    frame = exc["stacktrace"]["frames"][-1]
    # Test modules aren't one of dg's packages, so their names are replaced.
    assert frame["module"] == "<other>"
    assert frame["function"] == "<other>"
    assert isinstance(frame["lineno"], int)

    tags = event["tags"]
    assert tags["cmd.path"] == "dg read"
    assert tags["cmd.flags"] == "--api-key,--text"
    assert set(tags) == {"cmd.path", "cmd.flags", "cli.os", "cli.arch", "cli.python"}


def test_user_controlled_module_and_type_names_are_not_sent(
    transport: CapturingTransport,
) -> None:
    assert client.init_telemetry(_config(True)) is True
    odd_type = type("Odd_TYPENAME_SENTINEL_sk_live_abc", (Exception,), {})
    namespace: dict[str, Any] = {"__name__": "EXECMOD_SENTINEL_user", "E": odd_type}
    code = "def run_SENTINEL():\n    raise E('x')\n"
    exec(compile(code, "/home/user/HOMEPATH-SENTINEL/run.py", "exec"), namespace)
    try:
        namespace["run_SENTINEL"]()
    except Exception as exc:
        client.report_exception(exc)

    payload = _serialized(transport)
    assert [kind for kind, _ in _items(transport)] == ["event"]
    assert "SENTINEL" not in payload
    assert "sk_live" not in payload


def test_log_calls_alone_send_nothing(transport: CapturingTransport) -> None:
    assert client.init_telemetry(_config(True)) is True
    headers = {"Authorization": HEADER_SENTINEL}
    logging.getLogger("deepctl").error("failed %s %s", MESSAGE_SENTINEL, headers)
    logging.getLogger("deepctl").warning(MESSAGE_SENTINEL)

    assert transport.envelopes == []
    assert not sentry_sdk.get_client().is_active()


def test_after_a_report_logs_messages_and_transactions_send_nothing(
    transport: CapturingTransport,
) -> None:
    assert client.init_telemetry(_config(True)) is True
    _fail_with_sentinels()
    assert [kind for kind, _ in _items(transport)] == ["event"]

    logging.getLogger("deepctl").error("failed %s", MESSAGE_SENTINEL)
    sentry_sdk.capture_message(MESSAGE_SENTINEL)
    with sentry_sdk.start_transaction(name="dg read"):
        pass
    sentry_sdk.flush(timeout=2.0)

    kinds = [kind for kind, _ in _items(transport)]
    assert kinds == ["event"]
    assert not set(kinds) & FORBIDDEN_ITEM_TYPES
    assert MESSAGE_SENTINEL not in _serialized(transport)


def test_uncaught_exception_hook_reports_once(
    transport: CapturingTransport, capsys: pytest.CaptureFixture[str]
) -> None:
    assert client.init_telemetry(_config(True)) is True
    headers = {"Authorization": HEADER_SENTINEL}  # noqa: F841
    try:
        raise ValueError(MESSAGE_SENTINEL)
    except ValueError:
        sys.excepthook(*sys.exc_info())

    items = _items(transport)
    assert [kind for kind, _ in items] == ["event"]
    assert items[0][1]["exception"]["values"][0]["type"] == "ValueError"
    assert MESSAGE_SENTINEL not in _serialized(transport)
    # The previous hook still prints the traceback for the user.
    assert "ValueError" in capsys.readouterr().err


def test_two_thread_crashes_send_one_envelope(
    transport: CapturingTransport,
) -> None:
    assert client.init_telemetry(_config(True)) is True

    def work() -> None:
        raise LookupError(MESSAGE_SENTINEL)

    for _ in range(2):
        thread = threading.Thread(target=work)
        thread.start()
        thread.join()

    assert [kind for kind, _ in _items(transport)] == ["event"]
    # The SDK installs no hooks of its own: ours are still the ones in place.
    assert sys.excepthook.__module__ == client.__name__
    assert threading.excepthook.__module__ == client.__name__


def test_ctrl_c_during_flush_sends_one_envelope(
    transport: CapturingTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert client.init_telemetry(_config(True)) is True
    real_flush = sentry_sdk.flush

    def flush_then_interrupt(*args: Any, **kwargs: Any) -> None:
        real_flush(*args, **kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(sentry_sdk, "flush", flush_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        client.report_exception(RuntimeError(MESSAGE_SENTINEL))
    # The interrupt then reaches the process excepthook, as it would for real.
    try:
        raise KeyboardInterrupt
    except KeyboardInterrupt:
        sys.excepthook(*sys.exc_info())
    real_flush(timeout=2.0)

    assert [kind for kind, _ in _items(transport)] == ["event"]


def test_spotlight_env_var_receives_nothing(
    transport: CapturingTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    from http.server import BaseHTTPRequestHandler, HTTPServer

    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv(
            "SENTRY_SPOTLIGHT", f"http://127.0.0.1:{server.server_port}/stream"
        )
        assert client.init_telemetry(_config(True)) is True
        _fail_with_sentinels()
        sentry_sdk.flush(timeout=2.0)
        assert getattr(sentry_sdk.get_client(), "spotlight", None) is None
    finally:
        server.shutdown()
        server.server_close()

    assert [kind for kind, _ in _items(transport)] == ["event"]
    assert hits == []


def test_thread_exception_hook_reports(transport: CapturingTransport) -> None:
    assert client.init_telemetry(_config(True)) is True

    def work() -> None:
        raise LookupError(MESSAGE_SENTINEL)

    thread = threading.Thread(target=work)
    thread.start()
    thread.join()

    items = _items(transport)
    assert [kind for kind, _ in items] == ["event"]
    assert items[0][1]["exception"]["values"][0]["type"] == "LookupError"
    assert MESSAGE_SENTINEL not in _serialized(transport)


def test_disabled_config_sends_nothing(transport: CapturingTransport) -> None:
    assert client.init_telemetry(_config(False)) is False
    _fail_with_sentinels()

    assert transport.envelopes == []
    assert not sentry_sdk.get_client().is_active()
