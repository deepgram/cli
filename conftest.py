"""Root conftest.py — shared fixtures for all tests.

Ensures tests run in a clean environment by stripping DEEPGRAM_*
environment variables before each test. Tests that need specific
env vars should use @patch.dict("os.environ", {"DEEPGRAM_API_KEY": "..."}).
"""

import os

import pytest


@pytest.fixture(autouse=True)
def _clean_deepgram_env(monkeypatch):
    """Remove all DEEPGRAM_* environment variables for test isolation."""
    for key in list(os.environ):
        if key.startswith("DEEPGRAM_"):
            monkeypatch.delenv(key, raising=False)
