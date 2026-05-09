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

    def test_init_enables_auto_session_tracking(self) -> None:
        with patch("deepctl_telemetry.client.atexit.register"), patch(
            "sentry_sdk.init"
        ) as mock_init, patch("sentry_sdk.set_tag"), patch(
            "sentry_sdk.start_session"
        ):
            init_telemetry(_config(True))

        assert mock_init.called
        kwargs = mock_init.call_args.kwargs
        assert kwargs["auto_session_tracking"] is True
        assert kwargs["traces_sample_rate"] == 0.0
        assert kwargs["profiles_sample_rate"] == 0.0
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
