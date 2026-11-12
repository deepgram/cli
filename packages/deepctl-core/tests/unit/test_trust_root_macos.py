"""Which file can approve hosts in an agent session on macOS.

Runs on any platform: ``sys.platform`` and the account's home are
patched, so this proves the choice of path, not a real macOS layout.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from deepctl_core import config as config_module

HOME = "/Users/someone"
LIBRARY = Path(HOME) / "Library" / "Application Support" / "deepctl" / "config.yaml"
DOT_CONFIG = Path(HOME) / ".config" / "deepctl" / "config.yaml"


@pytest.fixture
def macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(config_module, "_real_home", lambda: HOME)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)


def test_without_xdg_it_is_application_support(macos):
    assert config_module._real_default_config_file() == LIBRARY
    assert config_module._trust_root_location() == (Path(HOME), LIBRARY)


@pytest.mark.parametrize(
    "value", [f"{HOME}/.config", f"{HOME}/.config/", f"{HOME}//.config/."]
)
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths")
def test_xdg_default_follows_the_config(macos, monkeypatch, value):
    monkeypatch.setenv("XDG_CONFIG_HOME", value)
    assert config_module._real_default_config_file() == DOT_CONFIG
    assert config_module._trust_root_location() == (Path(HOME), DOT_CONFIG)


@pytest.mark.parametrize(
    "value",
    [
        "",
        ".config",  # relative: platformdirs ignores it too
        "/tmp/repo/.config",
        f"{HOME}/repo/.config",
        f"{HOME}/.config/../repo",
        "/Users/other/.config",
    ],
)
def test_any_other_xdg_keeps_application_support(macos, monkeypatch, value):
    monkeypatch.setenv("XDG_CONFIG_HOME", value)
    assert config_module._real_default_config_file() == LIBRARY


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths")
def test_xdg_default_needs_platformdirs_to_follow_it(macos, monkeypatch):
    """platformdirs before 4.6 ignores XDG_CONFIG_HOME on macOS and reads
    Application Support: the trust root stays the file dg reads."""
    monkeypatch.setenv("XDG_CONFIG_HOME", f"{HOME}/.config")
    monkeypatch.setattr(
        config_module.platformdirs,
        "user_config_dir",
        lambda *_a, **_k: str(LIBRARY.parent),
    )
    assert config_module._real_default_config_file() == LIBRARY


def test_home_env_never_counts(macos, monkeypatch):
    monkeypatch.setenv("HOME", "/tmp/fakehome")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/fakehome/.config")
    assert config_module._real_default_config_file() == LIBRARY


def test_linux_is_unchanged(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(config_module, "_real_home", lambda: "/home/someone")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/elsewhere")
    assert config_module._real_default_config_file() == Path(
        "/home/someone/.config/deepctl/config.yaml"
    )
