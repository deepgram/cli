"""The speak modules import no network stack at module level.

The speak package's copy of ``tests/unit/test_command_imports.py``, which
runs the same check for every built-in command. ``dg speak --help`` imports these modules, so
a module-level ``asyncio``, ``ssl``, ``httpx``, ``websockets`` or SDK
import would be paid by every help and every run.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

FORBIDDEN = ("asyncio", "ssl", "httpx", "websockets", "deepgram")

CHECK = (
    "import importlib, sys; importlib.import_module(sys.argv[1]); "
    "bad = sorted({m for m in sys.modules for f in sys.argv[2:] "
    "if m == f or m.startswith(f + '.')}); "
    "print(' '.join(bad)); sys.exit(1 if bad else 0)"
)


def _env(tmp_path: Path) -> dict[str, str]:
    env = {
        k: v for k, v in os.environ.items() if not k.startswith(("DEEPGRAM_", "PYTHON"))
    }
    env.update(
        HOME=str(tmp_path),
        DEEPCTL_TELEMETRY_DISABLED="1",
        DEEPCTL_TELEMETRY_DSN="http://k@127.0.0.1:9/1",
    )
    return env


@pytest.mark.parametrize(
    "module",
    [
        "deepctl_cmd_speak.command",
        "deepctl_cmd_speak.sinks",
        "deepctl_cmd_speak.messages",
        "deepctl_cmd_speak.models",
    ],
)
def test_speak_module_imports_no_network_stack(module: str, tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", CHECK, module, *FORBIDDEN],
        capture_output=True,
        text=True,
        env=_env(tmp_path),
        timeout=120,
    )
    assert proc.returncode == 0, f"{module} imports: {proc.stdout}{proc.stderr[-1500:]}"
