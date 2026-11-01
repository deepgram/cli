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
def _reset_shown_hints():
    """A hint prints once per run (process); each test is its own run."""
    from deepctl_core import output

    output._hints_shown.clear()
    yield
    output._hints_shown.clear()


@pytest.fixture(autouse=True)
def _reset_config_parse_cache():
    """Config files are parsed once per process; each test is its own run."""
    from deepctl_core import config

    config._parse_cache.clear()
    yield
    config._parse_cache.clear()


@pytest.fixture(autouse=True)
def _clean_ai_agent_env(monkeypatch):
    """Run as a person would, even when an AI coding agent launched pytest.

    Agent markers (AI_AGENT_ENV_MARKERS, Aider's OR_*) switch dg into its
    agent trust mode. Tests that cover that mode set them, or patch
    is_ai_agent, themselves.
    """
    from deepctl_core.output import AI_AGENT_ENV_MARKERS, AI_AGENT_ENV_VALUES

    names = (*AI_AGENT_ENV_MARKERS, *AI_AGENT_ENV_VALUES, "OR_APP_NAME", "OR_SITE_URL")
    for key in names:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _clean_deepgram_env(monkeypatch):
    """Remove all DEEPGRAM_* environment variables for test isolation."""
    for key in list(os.environ):
        if key.startswith("DEEPGRAM_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _speak_tests_run_as_a_person(request, monkeypatch):
    """Speak tests run as a person by default: the agent rules key off hard
    agent markers, which a developer's own shell may set (CLAUDECODE=1).
    Tests that need agent mode patch ``is_ai_agent`` themselves."""
    if "deepctl-cmd-speak" not in request.path.parts:
        return
    from deepctl_core.output import AI_AGENT_ENV_MARKERS

    for name in (*AI_AGENT_ENV_MARKERS, "OR_APP_NAME", "OR_SITE_URL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _disable_telemetry(monkeypatch):
    """Keep tests that call `main()` from initializing real telemetry.

    The destination also points at a closed loopback port, so a test that
    re-enables telemetry can never reach a real endpoint.
    """
    monkeypatch.setenv("DEEPCTL_TELEMETRY_DISABLED", "1")
    monkeypatch.setenv("DEEPCTL_TELEMETRY_DSN", "http://k@127.0.0.1:9/1")
    # A developer's DO_NOT_TRACK or DEEPCTL_TELEMETRY=log must not change
    # what telemetry's own tests see. (CI is cleared only by those tests:
    # other suites read it as an automation signal.)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("DEEPCTL_TELEMETRY", raising=False)


@pytest.fixture(autouse=True)
def _restore_sigint():
    """`main()` installs its own SIGINT handler; put pytest's back."""
    import signal

    before = signal.getsignal(signal.SIGINT)
    yield
    if before is not None:
        signal.signal(signal.SIGINT, before)


@pytest.fixture(autouse=True)
def _fresh_credentials_state():
    """Forget per-process resolver state (placeholder warnings, a keyring
    timeout) between tests, as each dg run starts without it."""
    from deepctl_core import credentials

    credentials._reset_for_tests()
    yield
    credentials._reset_for_tests()
