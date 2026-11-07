"""The telemetry runtime contract.

Cross-tool opt-outs (DO_NOT_TRACK, CI), the DEEPCTL_TELEMETRY=log mode, the
flush that only waits for a queued event, and the published field schema in
docs/telemetry.md. Reports go to a local sink on loopback, never further.
"""

from __future__ import annotations

import json
import re
import socket
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
import sentry_sdk
from deepctl_telemetry import client
from sentry_sdk.transport import Transport

REPO = Path(__file__).resolve().parents[4]
DOC = REPO / "docs" / "telemetry.md"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Telemetry on (every opt-out cleared), fresh state, no user config."""
    for name in (
        client.DISABLE_ENV_VAR,
        client.DO_NOT_TRACK_ENV_VAR,
        client.CI_ENV_VAR,
        client.MODE_ENV_VAR,
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(client, "_state", client._fresh_state())
    default = Path(tempfile.mkdtemp()) / "default-config.yaml"
    monkeypatch.setattr(client, "_default_config_path", lambda: default)
    saved = (sys.excepthook, threading.excepthook)
    yield
    sys.excepthook, threading.excepthook = saved
    sentry_sdk.get_client().close()
    sentry_sdk.get_global_scope().set_client(None)


def _config() -> Any:
    return SimpleNamespace(config_path=Path(tempfile.mkdtemp()) / "config.yaml")


class _Sink:
    """An HTTP server on loopback that counts the reports it receives."""

    def __init__(self) -> None:
        self.posts: list[str] = []
        sink = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                sink.posts.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def dsn(self) -> str:
        return f"http://k@127.0.0.1:{self.port}/1"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def sink(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Sink]:
    server = _Sink()
    monkeypatch.setenv(client.DSN_ENV_VAR, server.dsn)
    yield server
    server.close()


def _fail() -> bool:
    try:
        raise RuntimeError("forced")
    except RuntimeError as exc:
        return client.report_exception(exc)


class TestCrossToolOptOuts:
    @pytest.mark.parametrize(
        ("name", "value", "source"),
        [
            ("DO_NOT_TRACK", "1", "do_not_track"),
            ("DO_NOT_TRACK", "true", "do_not_track"),
            ("CI", "true", "ci"),
            ("CI", "1", "ci"),
            ("CI", "woodpecker", "ci"),
        ],
    )
    def test_turns_it_off(
        self, monkeypatch: pytest.MonkeyPatch, name: str, value: str, source: str
    ) -> None:
        monkeypatch.setenv(name, value)
        assert client.init_telemetry(_config()) is False
        assert client._state["opt_out"] == source

    @pytest.mark.parametrize("name", ["DO_NOT_TRACK", "CI"])
    @pytest.mark.parametrize("value", ["", "0", "false", "No", " off "])
    def test_false_values_keep_it_on(
        self, monkeypatch: pytest.MonkeyPatch, name: str, value: str
    ) -> None:
        monkeypatch.setenv(name, value)
        assert client.init_telemetry(_config()) is True

    def test_dg_switch_is_named_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEEPCTL_TELEMETRY_DISABLED", "1")
        monkeypatch.setenv("DO_NOT_TRACK", "1")
        monkeypatch.setenv("CI", "true")
        client.init_telemetry(_config())
        assert client._state["opt_out"] == "env"

    def test_log_mode_does_not_turn_an_opt_out_back_on(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DO_NOT_TRACK", "1")
        monkeypatch.setenv("DEEPCTL_TELEMETRY", "log")
        assert client.init_telemetry(_config()) is False

    @pytest.mark.parametrize("name", ["DO_NOT_TRACK", "CI"])
    def test_forced_error_sends_nothing(
        self, monkeypatch: pytest.MonkeyPatch, sink: _Sink, name: str
    ) -> None:
        monkeypatch.setenv(name, "1")
        client.init_telemetry(_config())
        with patch("sentry_sdk.init") as init:
            assert _fail() is False
        init.assert_not_called()
        assert sink.posts == []

    def test_sink_receives_a_report_without_them(self, sink: _Sink) -> None:
        # Control: the same failure with no opt-out reaches the sink.
        client.init_telemetry(_config())
        assert _fail() is True
        sentry_sdk.get_client().close(timeout=2)
        assert len(sink.posts) == 1


class TestLogMode:
    def test_prints_the_event_and_sends_nothing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sink: _Sink,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("DEEPCTL_TELEMETRY", "LOG")
        client.init_telemetry(_config())
        with patch("sentry_sdk.flush") as flush:
            assert _fail() is True
        flush.assert_not_called()
        sentry_sdk.get_client().close(timeout=2)
        lines = [ln for ln in capsys.readouterr().err.splitlines() if ln.strip()]
        assert len(lines) == 1, lines
        event = json.loads(lines[0])
        assert event["exception"]["values"][0]["type"] == "RuntimeError"
        assert "forced" not in lines[0]
        assert sink.posts == []

    def test_no_destination_is_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DEEPCTL_TELEMETRY", "log")
        client.init_telemetry(_config())
        with patch("sentry_sdk.init", wraps=sentry_sdk.init) as init:
            _fail()
        assert init.call_args.kwargs["dsn"] is None
        transport = sentry_sdk.get_client().transport
        assert type(transport).__name__ == "StderrTransport"

    @pytest.mark.parametrize("value", ["off", "0", "false", "No", " disabled "])
    def test_off_values_turn_it_off(
        self, monkeypatch: pytest.MonkeyPatch, sink: _Sink, value: str
    ) -> None:
        monkeypatch.setenv("DEEPCTL_TELEMETRY", value)
        assert client.init_telemetry(_config()) is False
        assert client._state["opt_out"] == "env"
        with patch("sentry_sdk.init") as init:
            assert _fail() is False
        init.assert_not_called()
        assert sink.posts == []

    @pytest.mark.parametrize("value", ["on", "1", "true", "yes", "print"])
    def test_other_values_keep_the_default(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("DEEPCTL_TELEMETRY", value)
        assert client.init_telemetry(_config()) is True

    def test_other_values_send_as_usual(
        self, monkeypatch: pytest.MonkeyPatch, sink: _Sink
    ) -> None:
        monkeypatch.setenv("DEEPCTL_TELEMETRY", "print")
        client.init_telemetry(_config())
        assert _fail() is True
        sentry_sdk.get_client().close(timeout=2)
        assert len(sink.posts) == 1


class TestFlush:
    def test_no_flush_when_nothing_was_queued(self) -> None:
        client.init_telemetry(_config())
        with patch("sentry_sdk.flush") as flush:
            try:
                raise BrokenPipeError(32, "Broken pipe")
            except BrokenPipeError as exc:
                assert client.report_exception(exc) is False
        flush.assert_not_called()

    def test_refused_connection_does_not_wait_out_the_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A port nothing listens on: the one send fails at once, and the
        # flush returns as soon as it has, not at its timeout.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        # A 4 s flush timeout and budget: returning well before them shows
        # the flush ended with the failed send.
        monkeypatch.setattr(client, "FLUSH_TIMEOUT_SECONDS", 4.0)
        monkeypatch.setattr(client, "REPORT_BUDGET_SECONDS", 4.0)
        monkeypatch.setenv(client.DSN_ENV_VAR, f"http://k@127.0.0.1:{port}/1")
        client.init_telemetry(_config())
        started = time.monotonic()
        assert _fail() is True
        assert time.monotonic() - started < 2.5

    def test_unanswered_send_is_held_to_the_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Accepts the connection and never answers (blocked egress).
        with socket.socket() as hole:
            hole.bind(("127.0.0.1", 0))
            hole.listen(8)
            port = hole.getsockname()[1]
            # The budget (1.5 s) is under the flush timeout (6 s): the
            # call ends at the budget, not the timeout.
            monkeypatch.setattr(client, "FLUSH_TIMEOUT_SECONDS", 6.0)
            monkeypatch.setattr(client, "REPORT_BUDGET_SECONDS", 1.5)
            monkeypatch.setenv(client.DSN_ENV_VAR, f"http://k@127.0.0.1:{port}/1")
            client.init_telemetry(_config())
            started = time.monotonic()
            assert _fail() is True
            elapsed = time.monotonic() - started
        assert elapsed < 1.5 + 2.0


# ── Published schema ─────────────────────────────────────────────────


def _paths(value: Any, prefix: str = "") -> set[str]:
    """Every leaf of `value` as a dotted path; list items as `[]`."""
    if isinstance(value, dict):
        found: set[str] = set()
        for key, child in value.items():
            found |= _paths(child, f"{prefix}.{key}" if prefix else key)
        return found
    if isinstance(value, list):
        found = set()
        for child in value:
            found |= _paths(child, f"{prefix}[]")
        return found
    return {prefix}


def _documented() -> set[str]:
    text = DOC.read_text(encoding="utf-8")
    start = text.index("<!-- fields:start -->")
    end = text.index("<!-- fields:end -->")
    rows = [ln for ln in text[start:end].splitlines() if ln.startswith("| `")]
    return {re.match(r"\| `([^`]+)`", row).group(1) for row in rows}  # type: ignore[union-attr]


class _Capturing(Transport):
    """Keeps the envelopes the SDK hands over; sends nothing."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Any) -> None:
        for item in envelope.items:
            if item.type == "event":
                self.events.append(json.loads(item.get_bytes()))


def _raise_from() -> None:
    try:
        raise KeyError("inner")
    except KeyError as inner:
        raise ValueError("outer") from inner


def test_documented_fields_equal_what_a_report_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real error path, with every source of fields filled in: what the
    SDK is handed has exactly the fields docs/telemetry.md lists."""
    assert DOC.is_file(), f"{DOC} is missing: the field schema must be published"
    from deepctl_core import telemetry_context

    monkeypatch.setattr(
        telemetry_context,
        "command_context",
        lambda: {"command": "dg listen", "flags": "--model"},
    )
    capturing = _Capturing()
    real_init = sentry_sdk.init

    def init_with_capture(*args: Any, **kwargs: Any) -> Any:
        kwargs["transport"] = capturing
        return real_init(*args, **kwargs)

    monkeypatch.setattr(sentry_sdk, "init", init_with_capture)
    client.init_telemetry(_config())
    try:
        _raise_from()
    except ValueError as exc:
        assert client.report_exception(exc) is True
    (event,) = capturing.events
    assert len(event["exception"]["values"]) == 2
    assert _paths(event) == _documented()
