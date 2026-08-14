"""Fixtures and opt-in gate for the live end-to-end suite.

These tests drive real command ``handle()`` methods against a live Deepgram API
(in-process, not via subprocess), so they require credentials and network
access. They run only when ``DEEPGRAM_API_KEY`` and ``RUN_LIVE_E2E=1`` are set.
The target must also be explicit: set ``DEEPGRAM_BASE_URL`` for staging or a
custom endpoint, or set ``RUN_LIVE_E2E_PRODUCTION=1`` to confirm use of the
default production endpoint. A normally exported API key alone is never enough
to enable this suite.
"""

from __future__ import annotations

import io
import os
import types
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Mapping

# Capture credentials at import time — the root autouse ``_clean_deepgram_env``
# fixture strips every ``DEEPGRAM_*`` var before each test runs, so reading them
# inside a test/fixture would always come back empty. We re-inject the captured
# values per test in ``live_client`` below.
LIVE_API_KEY = os.environ.get("DEEPGRAM_API_KEY")
LIVE_BASE_URL = os.environ.get("DEEPGRAM_BASE_URL")


def _live_e2e_skip_reason(environ: Mapping[str, str]) -> str | None:
    """Return why live e2e is disabled, without exposing environment values."""
    if not environ.get("DEEPGRAM_API_KEY"):
        return "DEEPGRAM_API_KEY is not set; live e2e tests require credentials"
    if environ.get("RUN_LIVE_E2E") != "1":
        return "RUN_LIVE_E2E must be set to 1; live e2e tests are disabled"
    if (
        not environ.get("DEEPGRAM_BASE_URL")
        and environ.get("RUN_LIVE_E2E_PRODUCTION") != "1"
    ):
        return (
            "set DEEPGRAM_BASE_URL for a staging/custom target or set "
            "RUN_LIVE_E2E_PRODUCTION=1 to confirm production"
        )
    return None


LIVE_E2E_SKIP_REASON = _live_e2e_skip_reason(os.environ)

# Applied at module level by each e2e test module.
requires_live_e2e = pytest.mark.skipif(
    LIVE_E2E_SKIP_REASON is not None,
    reason=LIVE_E2E_SKIP_REASON or "live e2e gate satisfied",
)


@pytest.fixture
def live_client(monkeypatch):
    """A real (Config, AuthManager, DeepgramClient) wired to the live API.

    Re-injects the credentials the root autouse fixture stripped, then builds
    the same object graph the CLI framework constructs at runtime.
    """
    if LIVE_E2E_SKIP_REASON:
        pytest.skip(LIVE_E2E_SKIP_REASON)

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
