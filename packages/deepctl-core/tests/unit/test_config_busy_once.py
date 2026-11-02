"""A config file another process keeps locked costs one bounded wait per
run, not one per read."""

from __future__ import annotations

import os
import sys
import time

import pytest
import yaml
from deepctl_core import config as config_module
from deepctl_core import output

fcntl = pytest.importorskip("fcntl")

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="XDG layout (Linux)"
)

WAIT = 0.4


@pytest.fixture
def held(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "_pending_notices", [])
    monkeypatch.setattr(config_module, "_seen_notices", set())
    monkeypatch.setattr(config_module, "_busy_files", set())
    monkeypatch.setattr(config_module, "_LOCK_TIMEOUT", WAIT)
    home = tmp_path / "home"
    (home / ".config" / "deepctl").mkdir(parents=True)
    cfg = home / ".config" / "deepctl" / "config.yaml"
    cfg.write_text(yaml.safe_dump({"profiles": {"default": {"project_id": "p"}}}))
    os.chmod(cfg, 0o600)
    monkeypatch.setattr(config_module, "_real_home", lambda: str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(output, "is_ai_agent", lambda: False)
    # A writer stuck in the middle of a rewrite.
    fd = os.open(cfg, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    yield cfg
    os.close(fd)


def test_second_read_of_a_busy_file_does_not_wait_again(held):
    with config_module._open_config(held) as f:
        started = time.monotonic()
        with pytest.raises(config_module._ConfigBusy):
            f.load()
        first = time.monotonic() - started
    with config_module._open_config(held) as f:
        started = time.monotonic()
        with pytest.raises(config_module._ConfigBusy):
            f.load()
        second = time.monotonic() - started
    assert first >= WAIT
    assert second < WAIT / 2


def test_freed_lock_is_read_again(held, tmp_path):
    with config_module._open_config(held) as f, pytest.raises(
        config_module._ConfigBusy
    ):
        f.load()
    # A different file is not affected by the first one's wait.
    other = tmp_path / "other.yaml"
    other.write_text("a: 1\n")
    with config_module._open_config(other) as f:
        assert f.load() == {"a": 1}


def test_a_run_that_loads_the_config_twice_waits_once(held):
    from deepctl_core.auth import AuthManager
    from deepctl_core.config import Config

    started = time.monotonic()
    AuthManager(Config()).list_profiles()
    AuthManager(Config()).list_profiles()
    took = time.monotonic() - started
    # Before, every read waited the full bound: `dg profiles --list` took
    # two or three times the 5 s default.
    assert took < WAIT * 1.75, took
