"""``dg <cmd> --help`` from a help spec is byte-for-byte what the command
itself prints (stdout, stderr and exit code): against the normal lazy path
with the spec turned off, and against ``DEEPCTL_EAGER_LOAD=1``, which
imports every command up front.

One difference from eager mode predates help specs: with an unreadable HOME,
eager mode tries to load plugins up front and prints one
``WARN: Couldn't load plugins: ...`` line, which the lazy path never prints
for a built-in. That line, and only it, is allowed there.

The matrix: every built-in leaf, at three widths, under each output
environment, with each config fixture. The unit suite runs a sample that
covers every leaf, every environment and every fixture;
``DG_PARITY_FULL=1`` runs all of it. Each run gets its fixture built afresh,
since a run may fix a config file's mode.
"""

from __future__ import annotations

import itertools
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from deepctl import _manifest

ENTRY = "import sys; from deepctl.main import main; sys.exit(main())"
# The lazy path with help specs turned off (the command module is imported).
NO_SPEC = "import deepctl._cli as c; c.LazyGroup._help_command = lambda *a: None; "
FULL_ENTRY = NO_SPEC + ENTRY
FULL_MODULE = (
    NO_SPEC + "import runpy, sys; sys.argv[0] = 'deepctl'; "
    "runpy.run_module('deepctl', run_name='__main__', alter_sys=True)"
)
EAGER_WARN = "WARN: Couldn't load plugins: "
KEY = "fa1ce0000000000000000000000000000000000d"

LEAVES = sorted(
    [path] if " " not in path else path.split(" ")
    for path, entry in _manifest.commands().items()
    if path not in _manifest.groups()
    and not (_manifest.help_spec(path) or {}).get("group")
)
WIDTHS = ["50", "80", "200"]
VARIANTS = ["plain", "agent", "no-color", "prog-name", "module", "home-unreadable"]
FIXTURES = ["none", "trusted", "untrusted-agent", "project"]

TRUSTED = {
    "profiles": {"default": {"api_key": KEY, "base_url": "http://127.0.0.1:8000"}},
    "api": {"allowed_hosts": ["127.0.0.1:8000"]},
    "tools": {"ffprobe_path": "/usr/bin/ffprobe"},
    "plugins": {"allow_override": []},
}
PROJECT = {
    "api": {"allowed_hosts": ["evil.example"]},
    "tools": {"ffprobe_path": "/tmp/evil"},
    "telemetry": {"enabled": False},
    "update": {"check_enabled": False},
    "plugins": {"allow_override": ["listen"]},
    "api_key": KEY,
    "project_id": "p",
    "active_profile": "evil",
    "default_profile": "evil",
    "base_url": "http://evil.example",
    "output": {"format": "json", "bogus": 1},
    "profiles": {"x": {"api_key": KEY, "base_url": "http://evil.example"}},
    "other": 1,
}


def _cases() -> list[tuple[list[str], str, str, str]]:
    full = list(itertools.product(LEAVES, WIDTHS, VARIANTS, FIXTURES))
    if os.environ.get("DG_PARITY_FULL") == "1":
        return full
    # Every leaf once, then every variant with every fixture, each on a
    # different leaf and width.
    sample = [(leaf, "80", "plain", "none") for leaf in LEAVES]
    for index, (variant, fixture) in enumerate(itertools.product(VARIANTS, FIXTURES)):
        sample.append(
            (LEAVES[(index * 7) % len(LEAVES)], WIDTHS[index % 3], variant, fixture)
        )
    return sample


# Where the user config is under HOME: platformdirs' default for the OS (the
# runs clear XDG_CONFIG_HOME).
CONFIG = (
    Path("Library", "Application Support", "deepctl", "config.yaml")
    if sys.platform == "darwin"
    else Path(".config", "deepctl", "config.yaml")
)


def _fixture(root: Path, fixture: str, variant: str) -> tuple[Path, Path]:
    """Build ``root`` afresh: (home, cwd)."""
    if root.exists():
        for path in [root, *root.rglob("*")]:
            if path.is_dir() and not path.is_symlink():
                os.chmod(path, 0o700)
        shutil.rmtree(root)
    home, cwd = root / "home", root / "work"
    home.mkdir(parents=True)
    cwd.mkdir()
    if fixture in ("trusted", "untrusted-agent"):
        config = home / CONFIG
        config.parent.mkdir(parents=True)
        config.write_text(yaml.safe_dump(TRUSTED))
        # A key in a 0644 file: a person's run restricts it (I-PERM-FIXED).
        os.chmod(config, 0o644)
    if fixture == "project":
        (cwd / "deepgram.yaml").write_text(yaml.safe_dump(PROJECT))
    if variant == "home-unreadable":
        os.chmod(home, 0)
    return home, cwd


def _run(
    leaf: list[str], width: str, variant: str, fixture: str, root: Path, mode: str
) -> tuple[int, str, str]:
    """One run: ``mode`` is "spec" (the default path), "eager" or "full"
    (lazy, with help specs turned off)."""
    home, cwd = _fixture(root, fixture, variant)
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("DEEPGRAM_", "PYTHON", "DEEPCTL_", "XDG_"))
        and k not in ("AI_AGENT", "NO_COLOR", "CI", "CLAUDECODE", "COLUMNS")
    }
    env.update(
        HOME=str(home),
        COLUMNS=width,
        TERM="xterm",
        DEEPCTL_TELEMETRY_DISABLED="1",
        DEEPCTL_TELEMETRY_DSN="http://k@127.0.0.1:9/1",
    )
    if mode == "eager":
        env["DEEPCTL_EAGER_LOAD"] = "1"
    if variant == "agent" or fixture == "untrusted-agent":
        env["AI_AGENT"] = "1"
    if variant == "no-color":
        env["NO_COLOR"] = "1"
    if variant == "prog-name":
        env["DEEPCTL_PROG_NAME"] = "deepctl"
    if mode == "full":
        argv = [
            sys.executable,
            "-c",
            FULL_MODULE if variant == "module" else FULL_ENTRY,
        ]
    elif variant == "module":
        argv = [sys.executable, "-m", "deepctl"]
    else:
        argv = [sys.executable, "-c", ENTRY]
    proc = subprocess.run(
        [*argv, *leaf, "--help"],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )
    return proc.returncode, proc.stdout, proc.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
@pytest.mark.parametrize(
    ("leaf", "width", "variant", "fixture"),
    _cases(),
    ids=lambda v: " ".join(v) if isinstance(v, list) else str(v),
)
def test_help_from_spec_matches_eager(
    leaf: list[str], width: str, variant: str, fixture: str, tmp_path: Path
) -> None:
    if variant == "home-unreadable" and os.geteuid() == 0:
        pytest.skip("root can read any directory")
    root = tmp_path / "r"
    spec = _run(leaf, width, variant, fixture, root, "spec")
    full = _run(leaf, width, variant, fixture, root, "full")
    eager = _run(leaf, width, variant, fixture, root, "eager")
    assert spec == full
    if variant == "home-unreadable":
        lines = eager[2].splitlines(keepends=True)
        warns = [i for i, line in enumerate(lines) if line.startswith(EAGER_WARN)]
        assert len(warns) <= 1
        if warns:
            del lines[warns[0]]
        eager = (eager[0], eager[1], "".join(lines))
    assert spec == eager
    assert spec[0] == 0, spec[2][-1500:]
    assert spec[1].startswith("Usage: ")


@pytest.mark.skipif(sys.platform == "win32", reason="XDG config location and POSIX modes")
def test_matrix_reaches_the_notices(tmp_path: Path) -> None:
    """The fixtures print what they're there for on the spec path, and each
    deepgram.yaml warning prints exactly once, with its real text."""
    root = tmp_path / "r"
    _, _, err = _run(["listen"], "80", "plain", "project", root, "spec")
    lines = [line for line in err.splitlines() if line.startswith("⚠ Ignored ")]
    assert len(lines) == 14
    assert len(set(lines)) == len(lines)
    assert all(" in ./deepgram.yaml: project files " in line for line in lines)
    assert (
        "⚠ Ignored api.allowed_hosts in ./deepgram.yaml: project files can't "
        "approve hosts for your API key." in lines
    )
    _, _, err = _run(["listen"], "80", "plain", "untrusted-agent", root, "spec")
    agent_lines = [
        line for line in err.splitlines() if "in an AI agent session" in line
    ]
    assert len(agent_lines) == 4
    _, _, err = _run(["speak"], "80", "plain", "trusted", root, "spec")
    assert f"Restricted ~/{CONFIG.as_posix()} to owner-only access" in err


def test_leaves_cover_the_t3_rows() -> None:
    names = {" ".join(leaf) for leaf in LEAVES}
    assert {
        "transcribe",
        "listen",
        "speak",
        "api",
        "debug audio",
        "debug network",
        "debug probe",
        "debug browser",
    } <= names
    assert "debug" not in names
    assert "debug toolkit" not in names
