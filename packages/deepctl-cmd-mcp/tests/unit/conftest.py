"""Keep the test process's own stdout: handle() would point fd 1 at stderr."""

from __future__ import annotations

import io

import pytest


@pytest.fixture(autouse=True)
def _no_fd_swap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("deepctl_cmd_mcp.command.isolate_stdout", lambda: io.BytesIO())
