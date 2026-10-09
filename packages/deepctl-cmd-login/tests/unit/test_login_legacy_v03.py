"""dg login's skills step runs the deepctl 0.3.x cleanup and prints it on stderr."""

import os
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import click
import pytest
from deepctl_cmd_login import command as login_module
from deepctl_cmd_login.command import LoginCommand
from deepctl_core import output, skill_bundle
from deepctl_core import skill_generator as sg
from deepctl_core.skill_bundle import RepoSkill

FIX = Path(__file__).parents[3] / "deepctl-core" / "tests" / "unit" / "fixtures"
FIX = FIX / "legacy_v03"
NAMES = ("api", "docs", "setup-mcp", "starters")
pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="legacy cleanup is intentionally disabled on Windows"
)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(sg, "_SKILLS_DIR", home / ".deepctl" / "skills")
    monkeypatch.setattr(sg, "_STATE_FILE", home / ".deepctl" / "skills" / "skills.json")
    for con in (output.console, output.stderr_console, login_module.console):
        monkeypatch.setattr(con, "_width", 400)
    monkeypatch.delenv(skill_bundle.REF_ENV_VAR, raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: None)

    def fetch(ref=None):
        skills = []
        for name in NAMES:
            folder = tmp_path / "bundle" / "skills" / name
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "SKILL.md").write_bytes(f"---\nname: {name}\n---\n".encode())
            skills.append(RepoSkill(name, folder))
        return skills

    monkeypatch.setattr(skill_bundle, "fetch_skill_bundle", fetch)
    saved = dict(output._output_config)
    yield home
    output._output_config.clear()
    output._output_config.update(saved)


@pytest.mark.parametrize("agentic", [False, True])
def test_login_skills_step_cleans_up_on_stderr(monkeypatch, capsys, agentic):
    output._output_config.update(agentic=agentic, format="default", quiet=False)
    files = []
    for n in NAMES:
        path = Path.home() / ".claude" / "commands" / "deepgram" / f"{n}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((FIX / f"{n}.md").read_bytes())
        files.append(path)
    # Login offers skills only with no record, so these files are unrecorded
    # (skills.json was lost or deleted): the bytes are the proof, not the record.
    capsys.readouterr()
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    cmd = LoginCommand()
    cmd._guided = True
    with patch.object(login_module.Prompt, "ask", return_value="all"):
        cmd._maybe_prompt_skills_setup()
    out, err = (click.unstyle(s) for s in capsys.readouterr())
    assert not any(p.exists() for p in files)
    assert "0.3.x" not in out
    assert "Removed deepctl 0.3.x files for Claude Code" in " ".join(err.split())
