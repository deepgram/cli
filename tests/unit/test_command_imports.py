"""Command modules import no network stack at module level.

Running a command imports its module (its ``--help`` renders from the help
spec instead), so a module-level ``asyncio``, ``ssl``, ``httpx``,
``websockets`` or Deepgram SDK import is paid by every run of that command. Each built-in module in
the manifest is imported in a fresh interpreter and must leave all of them
out of ``sys.modules``; they belong inside the functions that use them.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from deepctl import _manifest

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
    "module", sorted({entry["module"] for entry in _manifest.commands().values()})
)
def test_command_module_imports_no_network_stack(module: str, tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", CHECK, module, *FORBIDDEN],
        capture_output=True,
        text=True,
        env=_env(tmp_path),
        timeout=120,
    )
    assert proc.returncode == 0, f"{module} imports: {proc.stdout}{proc.stderr[-1500:]}"


SUBHELP_FORBIDDEN = ("deepgram", "httpx", "keyring", "rich.traceback", "asyncio", "ssl")
ENTRY = "import sys; from deepctl.main import main; sys.exit(main())"


def _help_imports(path: list[str], tmp_path: Path) -> subprocess.CompletedProcess[str]:
    code = (
        "import sys; sys.argv = ['dg', *sys.argv[1:], '--help']; "
        "from deepctl.main import main\n"
        "try:\n    main()\nexcept SystemExit as e:\n    assert not e.code, e.code\n"
        f"bad = sorted({{m for m in sys.modules for f in {SUBHELP_FORBIDDEN!r} "
        "if (m == f or m.startswith(f + '.')) and sys.modules[m] is not None})\n"
        "sys.stderr.write('BAD ' + ' '.join(bad) + chr(10)); sys.exit(1 if bad else 0)"
    )
    return subprocess.run(
        [sys.executable, "-c", code, *path],
        capture_output=True,
        text=True,
        env=_env(tmp_path),
        stdin=subprocess.DEVNULL,
        timeout=120,
    )


@pytest.mark.parametrize("command", sorted(_manifest.top_level()))
def test_command_help_imports_no_network_stack(command: str, tmp_path: Path) -> None:
    """`dg <cmd> --help` for every built-in: the T3 forbidden list."""
    proc = _help_imports([command], tmp_path)
    assert proc.returncode == 0, proc.stderr[-1500:]


def _is_group(path: str) -> bool:
    return bool((_manifest.help_spec(path) or {}).get("group"))


@pytest.mark.parametrize(
    "path",
    [
        pytest.param(
            p.split(" "),
            marks=pytest.mark.xfail(
                strict=True,
                reason=(
                    "a subcommand group's help takes the full path, where the "
                    "parent group's callback builds an API client (httpx, ssl, "
                    "keyring) before the subcommand parses --help"
                ),
            )
            if _is_group(p)
            else (),
        )
        for p in sorted(_manifest.commands())
        if " " in p
    ],
    ids=lambda p: " ".join(p),
)
def test_subcommand_help_imports_no_network_stack(
    path: list[str], tmp_path: Path
) -> None:
    """`dg <group> <sub> --help` for every built-in subcommand."""
    proc = _help_imports(path, tmp_path)
    assert proc.returncode == 0, proc.stderr[-1500:]


@pytest.mark.parametrize(
    "path",
    sorted(
        p.split(" ")
        for p in _manifest.commands()
        if p not in _manifest.groups() and not _is_group(p)
    ),
    ids=lambda p: " ".join(p),
)
def test_leaf_help_imports_no_command_module(path: list[str], tmp_path: Path) -> None:
    """`dg <cmd> --help` for a built-in leaf renders from its help spec: no
    command module is imported."""
    code = (
        "import sys; sys.argv = ['dg', *sys.argv[1:], '--help']; "
        "from deepctl.main import main\n"
        "try:\n    main()\nexcept SystemExit as e:\n    assert not e.code, e.code\n"
        "bad = sorted(m for m in sys.modules if m.startswith('deepctl_cmd_'))\n"
        "sys.stderr.write('BAD ' + ' '.join(bad) + chr(10)); sys.exit(1 if bad else 0)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code, *path],
        capture_output=True,
        text=True,
        env=_env(tmp_path),
        stdin=subprocess.DEVNULL,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
