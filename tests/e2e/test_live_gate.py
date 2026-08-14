"""Deterministic tests for the live e2e opt-in gate."""

from __future__ import annotations

import pytest

from .conftest import _live_e2e_skip_reason


@pytest.mark.parametrize(
    ("environ", "expected_reason"),
    [
        ({}, "DEEPGRAM_API_KEY is not set"),
        (
            {"DEEPGRAM_API_KEY": "test-key"},
            "RUN_LIVE_E2E must be set to 1",
        ),
        (
            {
                "DEEPGRAM_API_KEY": "test-key",
                "RUN_LIVE_E2E": "true",
                "DEEPGRAM_BASE_URL": "https://staging.example",
            },
            "RUN_LIVE_E2E must be set to 1",
        ),
        (
            {
                "DEEPGRAM_API_KEY": "test-key",
                "RUN_LIVE_E2E": "1",
            },
            "RUN_LIVE_E2E_PRODUCTION=1 to confirm production",
        ),
        (
            {
                "DEEPGRAM_API_KEY": "test-key",
                "RUN_LIVE_E2E": "1",
                "RUN_LIVE_E2E_PRODUCTION": "true",
            },
            "RUN_LIVE_E2E_PRODUCTION=1 to confirm production",
        ),
    ],
)
def test_live_e2e_gate_rejects_incomplete_opt_in(environ, expected_reason):
    reason = _live_e2e_skip_reason(environ)

    assert reason is not None
    assert expected_reason in reason
    assert "test-key" not in reason


@pytest.mark.parametrize(
    "environ",
    [
        {
            "DEEPGRAM_API_KEY": "test-key",
            "RUN_LIVE_E2E": "1",
            "DEEPGRAM_BASE_URL": "https://staging.example",
        },
        {
            "DEEPGRAM_API_KEY": "test-key",
            "RUN_LIVE_E2E": "1",
            "RUN_LIVE_E2E_PRODUCTION": "1",
        },
    ],
)
def test_live_e2e_gate_accepts_explicit_target(environ):
    assert _live_e2e_skip_reason(environ) is None
