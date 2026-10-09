"""dg plugin's skills refresh runs the deepctl 0.3.x cleanup on stderr only."""

import json
import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner
from deepctl_cmd_plugin import command as plugin_module
from deepctl_cmd_plugin.command import PluginCommand
from deepctl_cmd_plugin.models import PluginOperationResult
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
    for con in (output.console, output.stderr_console, plugin_module.console):
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


def seed():
    paths = []
    for n in NAMES:
        path = Path.home() / ".claude" / "commands" / "deepgram" / f"{n}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((FIX / f"{n}.md").read_bytes())
        paths.append(str(path))
    edited = Path.home() / ".cursor" / "rules" / "deepctl.mdc"
    edited.parent.mkdir(parents=True)
    edited.write_bytes((FIX / "deepctl.mdc").read_bytes() + b"mine\n")
    legacy = {"claude": {"paths": paths}, "cursor": {"paths": [str(edited)]}}
    sg._STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    sg._STATE_FILE.write_text(json.dumps({"installed_skills": legacy}), "utf-8")
    return [Path(p) for p in paths], edited


@pytest.mark.parametrize("agentic", [False, True])
def test_plugin_remove_json_keeps_cleanup_off_stdout(agentic):
    output._output_config.update(agentic=agentic, format="json", quiet=False)
    files, edited = seed()
    cmd = PluginCommand()
    ok = PluginOperationResult(
        success=True, action="remove", package="foo", message="Removed foo"
    )
    group = click.Group("plugin", commands=cmd.setup_commands())
    obj = {"config": MagicMock(), "auth_manager": MagicMock(), "client": MagicMock()}
    with patch.object(cmd, "remove_plugin", return_value=ok):
        result = CliRunner().invoke(group, ["remove", "foo", "--yes"], obj=obj)
    assert result.exit_code == 0, result.output
    assert not any(p.exists() for p in files)
    assert edited.exists()
    assert "0.3.x" not in result.stdout
    assert "deepctl can't prove it wrote" not in result.stdout
    err = " ".join(result.stderr.split())
    assert "Removed deepctl 0.3.x files for Claude Code" in err
    assert "deepctl can't prove it wrote" in err
    if agentic:
        assert result.stdout == ""
