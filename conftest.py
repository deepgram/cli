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
    agent trust mode and its agent output rules, and a developer's own shell
    may set them (CLAUDECODE=1). Tests that cover agent mode set them, or
    patch is_ai_agent or agent_mode, themselves. DG_AGENT is cleared with
    the DEEPGRAM_* variables below.
    """
    from deepctl_core.output import AI_AGENT_ENV_MARKERS

    for key in (*AI_AGENT_ENV_MARKERS, "OR_APP_NAME", "OR_SITE_URL"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _clean_deepgram_env(monkeypatch):
    """Remove all DEEPGRAM_* environment variables, and the DG_AGENT
    override, for test isolation."""
    for key in list(os.environ):
        if key.startswith("DEEPGRAM_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("DG_AGENT", raising=False)


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


# ── Tests that send this process a real signal run in a child process ──────
#
# A test marked ``signals_in_child`` sends SIGTERM, SIGINT or SIGHUP to its
# own process. If the handler it relies on isn't installed (a bug, or a
# signal that arrives late), that signal would end or interrupt the whole
# pytest run. So the test runs in a pytest child of its own; this process
# only checks that the child passed.

_CHILD_NODE_ENV = "DEEPCTL_TEST_CHILD_NODE"
_BASE_ENV = dict(os.environ)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "signals_in_child: the test sends its own process a real signal; "
        "it runs in a child pytest process",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    if pyfuncitem.get_closest_marker("signals_in_child") is None:
        return None
    if os.environ.get(_CHILD_NODE_ENV) == pyfuncitem.nodeid:
        return None  # this is the child: run the test here
    import subprocess
    import sys

    config = pyfuncitem.config
    node = str(pyfuncitem.path) + "::" + pyfuncitem.nodeid.split("::", 1)[1]
    argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    argv += ["-p", "no:warnings"]
    argv += ["--rootdir", str(config.rootpath)]
    if config.inipath is not None:
        argv += ["-c", str(config.inipath)]
    argv += [node]
    proc = subprocess.run(
        argv,
        cwd=config.invocation_params.dir,
        env={**_BASE_ENV, _CHILD_NODE_ENV: pyfuncitem.nodeid},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=300,
    )
    output = proc.stdout[-5000:] + proc.stderr[-1000:]
    if proc.returncode < 0:
        pytest.fail(f"the child test process was killed by signal {-proc.returncode}:\n{output}")
    if proc.returncode != 0 or " passed" not in proc.stdout:
        pytest.fail(f"the child test process failed ({proc.returncode}):\n{output}")
    return True
