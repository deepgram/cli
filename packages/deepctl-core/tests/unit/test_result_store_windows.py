"""Whether stdout is a file or the null device, where the OS is Windows.

Runs on every OS: Windows reports device number 0 for every character
device, so NUL is told from a console by GetConsoleMode, stubbed here.
"""

from __future__ import annotations

import os
import stat
import types

import pytest
from deepctl_core import result_store


@pytest.mark.parametrize(("console", "expected"), [(True, False), (False, True)])
def test_windows_character_device(monkeypatch, console, expected):
    fake_sys = types.SimpleNamespace(
        platform="win32", stdout=types.SimpleNamespace(fileno=lambda: 1)
    )
    monkeypatch.setattr(result_store, "sys", fake_sys)
    chr_dev = os.stat_result((stat.S_IFCHR, 0, 0, 1, 0, 0, 0, 0, 0, 0))
    monkeypatch.setattr(result_store.os, "fstat", lambda _fd: chr_dev)
    monkeypatch.setattr(result_store, "_windows_console", lambda _fd: console)
    assert result_store.stdout_is_file_or_null() is expected


def test_console_check_off_windows_says_console():
    """Where GetConsoleMode can't be called, a device counts as a console,
    so a large result is still saved to a file."""
    if os.name == "nt":
        pytest.skip("calls the real GetConsoleMode")
    assert result_store._windows_console(1) is True
