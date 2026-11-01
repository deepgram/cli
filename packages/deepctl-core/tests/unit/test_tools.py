"""External tools are found on PATH, never in the current directory."""

from __future__ import annotations

import os
import sys

import pytest
from deepctl_core.tools import find_executable, resolve_tool

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")


def make_tool(directory, name="ffprobe"):
    directory.mkdir(parents=True, exist_ok=True)
    tool = directory / name
    tool.write_text("#!/bin/sh\nexit 0\n")
    tool.chmod(0o755)
    return tool


def test_relative_empty_and_cwd_entries_skipped(tmp_path, monkeypatch):
    make_tool(tmp_path)
    make_tool(tmp_path / "bin")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", os.pathsep.join([".", "", "bin", str(tmp_path)]))
    assert find_executable("ffprobe") is None


def test_absolute_entry_found(tmp_path, monkeypatch):
    tool = make_tool(tmp_path / "bin")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert find_executable("ffprobe") == str(tool)


class FakeConfig:
    def __init__(self, value):
        self.value = value

    def get(self, key):
        return self.value


def test_resolve_tool_prefers_absolute_config_path(tmp_path, monkeypatch):
    tool = make_tool(tmp_path / "opt")
    monkeypatch.setenv("PATH", "")
    assert resolve_tool(FakeConfig(str(tool)), "ffprobe", "k") == str(tool)


def test_resolve_tool_ignores_relative_config_path(tmp_path, monkeypatch):
    make_tool(tmp_path / "tools")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "")
    assert resolve_tool(FakeConfig("./tools/ffprobe"), "ffprobe", "k") is None
