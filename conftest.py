"""Root conftest.py — shared fixtures for all tests.

Ensures tests run in a clean environment by stripping DEEPGRAM_*
environment variables before each test. Tests that need specific
env vars should use @patch.dict("os.environ", {"DEEPGRAM_API_KEY": "..."}).

Telemetry is disabled for every test so a test that drives `main()` never
initializes the real telemetry client. Telemetry's own tests opt back in.
"""

import os

import pytest


@pytest.fixture(autouse=True)
def _reset_config_parse_cache():
    """Config files are parsed once per process; each test is its own run."""
    from deepctl_core import config

    config._parse_cache.clear()
    yield
    config._parse_cache.clear()


@pytest.fixture(autouse=True)
def _clean_deepgram_env(monkeypatch):
    """Remove all DEEPGRAM_* environment variables for test isolation."""
    for key in list(os.environ):
        if key.startswith("DEEPGRAM_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _disable_telemetry(monkeypatch):
    """Keep tests that call `main()` from initializing real telemetry.

    The destination also points at a closed loopback port, so a test that
    re-enables telemetry can never reach a real endpoint.
    """
    monkeypatch.setenv("DEEPCTL_TELEMETRY_DISABLED", "1")
    monkeypatch.setenv("DEEPCTL_TELEMETRY_DSN", "http://k@127.0.0.1:9/1")
