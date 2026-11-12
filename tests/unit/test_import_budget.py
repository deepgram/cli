"""Import budget for the fast paths.

``dg --version`` and the ``dg mcp`` handshake start without Click or
``deepctl_core``. Each path runs in a fresh interpreter with
``-X importtime`` and its imports are checked against the same allowlist and
forbidden lists as the benchmark gates (``bench/gates.json``
``imports``), mirrored below. When ``bench/gates.json`` is in the tree, the test
also checks that the two copies match.

The ``--help`` and ``<cmd> --help`` lists come with the lazy command group.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]

# Mirrors bench/gates.json "imports" exactly.
GATES_IMPORTS: dict[str, Any] = {
    "first_party_prefixes": ["deepctl"],
    "allowlist": {
        "paths": ["version", "mcp"],
        "first_party": ["deepctl", "deepctl.main", "deepctl.mcp", "deepctl.mcp._fast"],
        "stdlib_exclude": [
            "asyncio",
            "ssl",
            "socket",
            "concurrent",
            "threading",
            "http",
            "urllib",
            "email",
            "logging",
        ],
        "dotted_exclude": ["importlib.metadata"],
        "extra": [
            "__main__",
            "_distutils_hack",
            "_virtualenv",
            "sitecustomize",
            "usercustomize",
            # pywin32's .pth file imports these at startup on Windows (mcp
            # depends on pywin32 there).
            "pywin32_bootstrap",
            "pywin32_system32",
        ],
    },
    "forbidden": {
        "version": [
            "deepgram",
            "httpx",
            "keyring",
            "pydantic",
            "rich.traceback",
            "asyncio",
        ],
        "help": [
            "deepgram",
            "httpx",
            "keyring",
            "pydantic",
            "rich.traceback",
            "asyncio",
        ],
        "subhelp": [
            "deepgram",
            "httpx",
            "keyring",
            "rich.traceback",
            "asyncio",
            "ssl",
        ],
    },
}

# Checked here only: the public gates leave sentry_sdk out until
# 0.5.0 publishes.
UNIT_ONLY_FORBIDDEN = {"version": ["sentry_sdk"], "mcp": ["sentry_sdk"]}

# The console script's own code: `from deepctl.main import main; main()`.
ENTRY = "import sys; from deepctl.main import main; sys.exit(main())"

IMPORT_LINE = re.compile(r"^import time:\s+\d+\s+\|\s+\d+\s+\|\s?(\s*)(\S+)\s*$")

MCP_SESSION = [
    # mcp 2.x clients probe server/discover first (answered -32601 locally).
    {"jsonrpc": "2.0", "id": 0, "method": "server/discover", "params": {}},
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "import-budget", "version": "1"},
        },
    },
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
]


def _env(tmp_path: Path) -> dict[str, str]:
    env = {
        k: v for k, v in os.environ.items() if not k.startswith(("DEEPGRAM_", "PYTHON"))
    }
    env.update(
        HOME=str(tmp_path),
        DEEPCTL_TELEMETRY_DISABLED="1",
        DEEPCTL_TELEMETRY_DSN="http://k@127.0.0.1:9/1",
        CI="1",
    )
    return env


def _imports(
    args: list[str], tmp_path: Path, stdin: bytes = b""
) -> tuple[subprocess.CompletedProcess[bytes], list[str]]:
    proc = subprocess.run(
        [sys.executable, "-X", "importtime", "-c", ENTRY, *args],
        input=stdin,
        capture_output=True,
        env=_env(tmp_path),
        timeout=60,
    )
    modules = []
    for line in proc.stderr.decode("utf-8", "replace").splitlines():
        match = IMPORT_LINE.match(line)
        if match:
            modules.append(match.group(2))
    assert modules, "no -X importtime output"
    return proc, modules


def _forbidden_hits(modules: list[str], forbidden: list[str]) -> list[str]:
    return sorted(
        {f for f in forbidden for m in modules if m == f or m.startswith(f + ".")}
    )


def _allowlist_violations(modules: list[str]) -> list[str]:
    """bench/lib/measure.py classify(), for a path on the allowlist."""
    allow = GATES_IMPORTS["allowlist"]
    std = set(sys.stdlib_module_names)
    prefixes = tuple(GATES_IMPORTS["first_party_prefixes"])
    excluded = set(allow["stdlib_exclude"])
    dotted = tuple(allow["dotted_exclude"])
    violations = []
    for m in modules:
        top = m.split(".")[0]
        if m in allow["extra"]:
            continue
        if top.startswith(prefixes):
            if m not in allow["first_party"]:
                violations.append(m)
            continue
        if (
            top not in std
            or top in excluded
            or any(m == d or m.startswith(d + ".") for d in dotted)
        ):
            violations.append(m)
    return violations


def test_pywin32_startup_imports_are_allowed() -> None:
    """pywin32's .pth runs on every Windows interpreter start; the allowlist
    must not count those two modules against the fast paths."""
    assert _allowlist_violations(["pywin32_bootstrap", "pywin32_system32"]) == []
    assert _allowlist_violations(["pywin32_other"]) == ["pywin32_other"]


def test_mirror_matches_bench_gates() -> None:
    gates = REPO / "bench" / "gates.json"
    if not gates.exists():
        pytest.skip("bench/gates.json is not in this tree yet")
    assert json.loads(gates.read_text(encoding="utf-8"))["imports"] == GATES_IMPORTS


def test_version_path_imports(tmp_path: Path) -> None:
    proc, modules = _imports(["--version"], tmp_path)
    assert proc.returncode == 0, proc.stderr[-2000:]
    forbidden = GATES_IMPORTS["forbidden"]["version"] + UNIT_ONLY_FORBIDDEN["version"]
    assert _forbidden_hits(modules, forbidden) == []
    assert _allowlist_violations(modules) == []
    assert "click" not in modules
    assert "deepctl_core" not in modules


def test_mcp_handshake_imports(tmp_path: Path) -> None:
    session = b"".join((json.dumps(m) + "\n").encode() for m in MCP_SESSION)
    proc, modules = _imports(["mcp"], tmp_path, stdin=session)
    # stdin EOF before any hand-off: exit 0.
    assert proc.returncode == 0, proc.stderr[-2000:]
    replies = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [r["id"] for r in replies] == [0, 1, 2]
    assert replies[0]["error"]["code"] == -32601
    assert replies[2]["result"]["tools"]
    assert _forbidden_hits(modules, UNIT_ONLY_FORBIDDEN["mcp"]) == []
    assert _allowlist_violations(modules) == []
    assert "click" not in modules
    assert "deepctl_core" not in modules


def test_full_path_still_loads_click(tmp_path: Path) -> None:
    """Anything but the two fast paths runs the Click CLI."""
    proc, modules = _imports(["--version", "--quiet"], tmp_path)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "click" in modules
    assert proc.stdout.decode().startswith("deepctl, version ")


def test_root_help_imports(tmp_path: Path) -> None:
    """Root --help with the plugin discovery cache warm (the first run fills
    it): no pydantic, no Config, no command module."""
    first, _ = _imports(["--help"], tmp_path)
    assert first.returncode == 0, first.stderr[-2000:]
    proc, modules = _imports(["--help"], tmp_path)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout == first.stdout
    assert _forbidden_hits(modules, GATES_IMPORTS["forbidden"]["help"]) == []
    assert "deepctl_core.config" not in modules
    assert "deepctl_core.plugin_manager" not in modules
    assert not [m for m in modules if m.startswith("deepctl_cmd_")]


def test_version_imports_no_command_module(tmp_path: Path) -> None:
    _, modules = _imports(["--version"], tmp_path)
    assert not [m for m in modules if m.startswith(("deepctl_cmd_", "deepctl_plugin"))]


def test_subcommand_help_imports(tmp_path: Path) -> None:
    """`dg transcribe --help` (the T3 row): no network stack, no SDK, and no
    command module (its help comes from the manifest's help spec)."""
    proc, modules = _imports(["transcribe", "--help"], tmp_path)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert _forbidden_hits(modules, GATES_IMPORTS["forbidden"]["subhelp"]) == []
    assert not [m for m in modules if m.startswith("deepctl_cmd_")]


# Never on `dg listen --help` with no config files (bench row imports_subhelp).
SUBHELP_SPEC_FORBIDDEN = [
    "pydantic",
    "yaml",
    "rich",
    "deepctl_cmd_",
    "deepctl_core.base_command",
]


@pytest.mark.parametrize(
    "path", [["listen"], ["speak"], ["api"], ["debug", "probe"]], ids=" ".join
)
def test_subcommand_help_from_spec_imports(path: list[str], tmp_path: Path) -> None:
    proc, modules = _imports([*path, "--help"], tmp_path)
    assert proc.returncode == 0, proc.stderr[-2000:]
    hits = sorted(
        {
            m
            for m in modules
            for f in SUBHELP_SPEC_FORBIDDEN
            if m == f or m.startswith(f if f.endswith("_") else f + ".")
        }
    )
    assert hits == []
