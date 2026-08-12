"""Fixtures for the live end-to-end suite.

These tests drive the real command ``handle()`` methods against the live
Deepgram API (in-process, not via subprocess), so they need a real API key
and network access. They are skipped unless ``DEEPGRAM_API_KEY`` is set, so
they never run in the standard CI matrix (which has no Deepgram secret) — run
them locally with your key exported. Set ``DEEPGRAM_BASE_URL`` too to target
staging instead of production.
"""

from __future__ import annotations

import io
import os
import types

import pytest

# Capture credentials at import time — the root autouse ``_clean_deepgram_env``
# fixture strips every ``DEEPGRAM_*`` var before each test runs, so reading them
# inside a test/fixture would always come back empty. We re-inject the captured
# values per test in ``live_client`` below.
LIVE_API_KEY = os.environ.get("DEEPGRAM_API_KEY")
LIVE_BASE_URL = os.environ.get("DEEPGRAM_BASE_URL")

# Applied at module level by each e2e test module.
requires_live_key = pytest.mark.skipif(
    not LIVE_API_KEY,
    reason="DEEPGRAM_API_KEY not set — live e2e tests skipped",
)


@pytest.fixture
def live_client(monkeypatch):
    """A real (Config, AuthManager, DeepgramClient) wired to the live API.

    Re-injects the credentials the root autouse fixture stripped, then builds
    the same object graph the CLI framework constructs at runtime.
    """
    monkeypatch.setenv("DEEPGRAM_API_KEY", LIVE_API_KEY or "")
    if LIVE_BASE_URL:
        monkeypatch.setenv("DEEPGRAM_BASE_URL", LIVE_BASE_URL)

    from deepctl_core import AuthManager, Config, DeepgramClient

    config = Config()
    auth = AuthManager(config)
    client = DeepgramClient(config, auth)
    return config, auth, client


@pytest.fixture
def feed_stdin(monkeypatch):
    """Return a helper that pipes raw bytes into the listen command's stdin.

    The stdin streaming path reads ``sys.stdin.buffer`` (via the listen
    module's ``sys``); swap in a BytesIO-backed fake so an in-process run
    behaves like ``… | dg listen -``.
    """

    def _feed(pcm: bytes) -> None:
        fake_stdin = types.SimpleNamespace(
            buffer=io.BytesIO(pcm),
            isatty=lambda: False,
        )
        monkeypatch.setattr(
            "deepctl_cmd_listen.command.sys.stdin", fake_stdin, raising=False
        )

    return _feed
