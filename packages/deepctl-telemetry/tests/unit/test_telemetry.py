"""Tests for deepctl_telemetry."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from deepctl_telemetry import init_telemetry, is_enabled, render_notice
from deepctl_telemetry.client import DISABLE_ENV_VAR


def _config(value: bool | None = True) -> Mock:
    config = Mock()
    config.get.return_value = value
    return config


class TestIsEnabled:
    def test_default_on(self) -> None:
        assert is_enabled(_config(True)) is True

    def test_config_off(self) -> None:
        assert is_enabled(_config(False)) is False

    def test_env_override(self, monkeypatch: object) -> None:
        monkeypatch.setenv(DISABLE_ENV_VAR, "1")  # type: ignore[attr-defined]
        assert is_enabled(_config(True)) is False


class TestRenderNotice:
    def test_on_message(self) -> None:
        notice = render_notice(_config(True))
        assert "Telemetry is on" in notice
        assert "telemetry.enabled false" in notice

    def test_off_message(self) -> None:
        notice = render_notice(_config(False))
        assert "Telemetry is off" in notice


class TestSessionFlush:
    """Verify init_telemetry enables session tracking and registers atexit flush."""

    @pytest.fixture(autouse=True)
    def reset_initialized(self) -> None:
        from deepctl_telemetry import client

        client._initialized = False
        yield
        client._initialized = False

    def test_init_enables_full_observability_stack(self) -> None:
        with patch("deepctl_telemetry.client.atexit.register"), patch(
            "sentry_sdk.init"
        ) as mock_init, patch("sentry_sdk.set_tag"), patch(
            "sentry_sdk.start_session"
        ):
            init_telemetry(_config(True))

        assert mock_init.called
        kwargs = mock_init.call_args.kwargs
        assert kwargs["auto_session_tracking"] is True
        assert kwargs["traces_sample_rate"] == 1.0
        assert kwargs["profiles_sample_rate"] == 1.0
        assert kwargs["enable_logs"] is True
        assert kwargs["attach_stacktrace"] is True
        assert kwargs["max_breadcrumbs"] == 100
        assert kwargs["send_default_pii"] is False
        assert "session_mode" not in kwargs

    def test_init_registers_atexit_flush(self) -> None:
        from deepctl_telemetry.client import _flush_on_exit

        with patch(
            "deepctl_telemetry.client.atexit.register"
        ) as mock_register, patch("sentry_sdk.init"), patch(
            "sentry_sdk.set_tag"
        ), patch("sentry_sdk.start_session"):
            init_telemetry(_config(True))

        mock_register.assert_called_once_with(_flush_on_exit)

    def test_init_starts_session(self) -> None:
        with patch("sentry_sdk.init"), patch("sentry_sdk.set_tag"), patch(
            "deepctl_telemetry.client.atexit.register"
        ), patch("sentry_sdk.start_session") as mock_start:
            init_telemetry(_config(True))

        mock_start.assert_called_once_with()

    def test_disabled_does_not_register_atexit(self) -> None:
        with patch(
            "deepctl_telemetry.client.atexit.register"
        ) as mock_register, patch("sentry_sdk.init") as mock_init:
            init_telemetry(_config(False))

        assert not mock_init.called
        assert not mock_register.called

    def test_flush_on_exit_calls_sentry_flush_with_2s_budget(self) -> None:
        from deepctl_telemetry.client import _flush_on_exit

        with patch("sentry_sdk.flush") as mock_flush:
            _flush_on_exit()

        mock_flush.assert_called_once_with(timeout=2.0)

    def test_flush_on_exit_swallows_exceptions(self) -> None:
        from deepctl_telemetry.client import _flush_on_exit

        with patch("sentry_sdk.flush", side_effect=RuntimeError("boom")):
            _flush_on_exit()


def _handled_log_event(logger: str, exc_type: str, value: str) -> dict:
    return {
        "logger": logger,
        "exception": {
            "values": [
                {
                    "type": exc_type,
                    "value": value,
                    "mechanism": {"type": "logging", "handled": True},
                }
            ]
        },
    }


def _message_only_event(logger: str) -> dict:
    return {"logger": logger}


class TestMcpNoiseFilter:
    """Drop handled MCP SDK log noise so Sentry stays actionable.

    Anchors: DX-CLI-1 (HTTPStatusError 5xx from streamable_http) and
    DX-CLI-3 (Invalid JSON: EOF from mcp.server.lowlevel.server).
    Unhandled exceptions are always kept regardless of logger.
    """

    def test_drops_streamable_http_503_log(self) -> None:
        from deepctl_telemetry.client import _is_mcp_transient_noise

        event = _handled_log_event(
            "mcp.client.streamable_http",
            "HTTPStatusError",
            "Server error '503 Service Unavailable' for url",
        )
        assert _is_mcp_transient_noise(event)

    def test_drops_lowlevel_server_eof_log(self) -> None:
        from deepctl_telemetry.client import _is_mcp_transient_noise

        event = _message_only_event("mcp.server.lowlevel.server")
        assert _is_mcp_transient_noise(event)

    def test_keeps_unhandled_exception_from_mcp_logger(self) -> None:
        """Unhandled crashes are real bugs even if the logger is MCP."""
        from deepctl_telemetry.client import _is_mcp_transient_noise

        event = {
            "logger": "mcp.client.streamable_http",
            "exception": {
                "values": [
                    {
                        "type": "RuntimeError",
                        "value": "boom",
                        "mechanism": {"type": "excepthook", "handled": False},
                    }
                ]
            },
        }
        assert not _is_mcp_transient_noise(event)

    def test_keeps_non_mcp_logger_events(self) -> None:
        from deepctl_telemetry.client import _is_mcp_transient_noise

        event = _handled_log_event(
            "deepctl_core.base_command",
            "HTTPStatusError",
            "Server error '503 Service Unavailable' for url",
        )
        assert not _is_mcp_transient_noise(event)

    def test_handles_missing_fields(self) -> None:
        from deepctl_telemetry.client import _is_mcp_transient_noise

        assert not _is_mcp_transient_noise({})
        assert not _is_mcp_transient_noise({"logger": None})
        assert not _is_mcp_transient_noise({"logger": "deepctl"})
        assert _is_mcp_transient_noise({"logger": "mcp.client.streamable_http"})

    def test_scrub_event_returns_none_for_mcp_noise(self) -> None:
        """The before_send hook drops MCP noise entirely."""
        from deepctl_telemetry.client import _scrub_event

        event = _handled_log_event(
            "mcp.client.streamable_http",
            "HTTPStatusError",
            "Server error '503 Service Unavailable' for url",
        )
        assert _scrub_event(event, {}) is None

    def test_scrub_event_still_scrubs_normal_request_headers(self) -> None:
        from deepctl_telemetry.client import _scrub_event

        event = {
            "logger": "deepctl",
            "request": {
                "headers": {"Authorization": "Bearer secret"},
                "cookies": {"sid": "abc"},
                "data": "{\"raw\":\"body\"}",
            },
            "user": {
                "email": "x@example.com",
                "ip_address": "1.2.3.4",
                "username": "x",
                "id": "user-1",
            },
        }
        result = _scrub_event(event, {})
        assert result is not None
        assert result["request"]["headers"] == {}
        assert result["request"]["cookies"] == {}
        assert result["request"]["data"] == "[Filtered]"
        assert "email" not in result["user"]
        assert "ip_address" not in result["user"]
        assert "username" not in result["user"]
        assert result["user"]["id"] == "user-1"
