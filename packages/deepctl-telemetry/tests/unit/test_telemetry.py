"""Tests for deepctl_telemetry."""

from __future__ import annotations

from unittest.mock import Mock

from deepctl_telemetry import is_enabled, render_notice
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
