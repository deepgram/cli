"""dg skills and the deepctl 0.3.x cleanup: what the commands print around it."""

import json
import shutil
from pathlib import Path

import click
import pytest
from deepctl_cmd_skills import command
from deepctl_cmd_skills.command import SkillsCommand
from deepctl_core import output, skill_bundle
from deepctl_core import skill_generator as sg
from deepctl_core.skill_bundle import RepoSkill

FIX = Path(__file__).parents[3] / "deepctl-core" / "tests" / "unit" / "fixtures"
FIX = FIX / "legacy_v03"
NAMES = ("api", "docs", "setup-mcp", "starters")
BLOB = {n: (FIX / f"{n}.md").read_bytes() for n in NAMES}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(sg, "_SKILLS_DIR", home / ".deepctl" / "skills")
    monkeypatch.setattr(sg, "_STATE_FILE", home / ".deepctl" / "skills" / "skills.json")
    for con in (output.console, output.stderr_console, command.console):
        monkeypatch.setattr(con, "_width", 400)
    monkeypatch.delenv(skill_bundle.REF_ENV_VAR, raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    saved = dict(output._output_config)
    output._output_config.update(agentic=True, format="default", quiet=False)
    yield home
    output._output_config.clear()
    output._output_config.update(saved)


def use_bundle(tmp_path, monkeypatch, names=NAMES):
    def fetch(ref=None):
        skills = []
        for name in names:
            folder = tmp_path / "bundle" / "skills" / name
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "SKILL.md").write_bytes(f"---\nname: {name}\n---\n".encode())
            skills.append(RepoSkill(name, folder))
        return skills

    monkeypatch.setattr(skill_bundle, "fetch_skill_bundle", fetch)


def claude(name):
    return Path.home() / ".claude" / "commands" / "deepgram" / f"{name}.md"


def seed(names=NAMES, extra=None):
    Path.home().joinpath(".claude").mkdir(exist_ok=True)
    for n in names:
        claude(n).parent.mkdir(parents=True, exist_ok=True)
        claude(n).write_bytes(BLOB[n])
    legacy = {"claude": {"paths": [str(claude(n)) for n in names]}, **(extra or {})}
    sg._STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    sg._STATE_FILE.write_text(json.dumps({"installed_skills": legacy}), "utf-8")


def err_text(capsys):
    return " ".join(click.unstyle(capsys.readouterr().err).split())


def test_install_removes_proven_files_and_remove_has_no_03x_note(
    tmp_path, monkeypatch, capsys
):
    use_bundle(tmp_path, monkeypatch)
    seed()
    SkillsCommand()._handle_install(install_all=True)
    err = err_text(capsys)
    assert "INFO: Removed deepctl 0.3.x files for Claude Code:" in err
    assert not claude("api").parent.exists()
    SkillsCommand()._handle_status()
    assert "0.3.x" not in err_text(capsys)
    SkillsCommand()._handle_remove(remove_all=True)
    assert "0.3.x" not in err_text(capsys)


@pytest.mark.parametrize("gone", [False, True])
def test_kept_file_status_and_remove_name_install_and_update(
    tmp_path, monkeypatch, capsys, gone
):
    use_bundle(tmp_path, monkeypatch, ("api", "docs", "starters"))
    seed()  # No setup-mcp folder lands, so setup-mcp.md stays tracked.
    SkillsCommand()._handle_install(install_all=True)
    capsys.readouterr()
    assert claude("setup-mcp").read_bytes() == BLOB["setup-mcp"]
    assert not claude("api").exists()
    SkillsCommand()._handle_status()
    note = "Files from deepctl 0.3.x are recorded for Claude Code; 'dg skills install' or 'dg skills update' removes the ones deepctl can prove it wrote once the skill folders are installed."
    assert note in err_text(capsys)
    if gone:  # Deleted by hand since: nothing to say about 0.3.x files.
        claude("setup-mcp").unlink()
    SkillsCommand()._handle_remove(remove_all=True)
    err = err_text(capsys)
    kept = f"For Claude Code, its deepctl 0.3.x files were kept: {claude('setup-mcp')}; delete any you don't need, or 'dg skills install' removes the ones deepctl can prove it wrote."
    assert (kept in err) != gone
    assert ("0.3.x" in err) != gone


def test_hint_only_remove_says_the_file_is_kept(tmp_path, monkeypatch, capsys):
    use_bundle(tmp_path, monkeypatch)
    rule = Path.home() / ".amazonq" / "rules" / "deepctl.md"
    rule.parent.mkdir(parents=True)
    rule.write_bytes(b"0.3.x rules")
    conv = Path.home() / ".deepctl" / "skills" / "deepctl-conventions.md"
    conv.parent.mkdir(parents=True)
    conv.write_bytes(b"0.3.x conventions")  # On disk, so remove names it.
    seed(extra={"amazonq": {"paths": [str(rule)]}, "aider": {"paths": [str(conv)]}})
    SkillsCommand()._handle_install(install_all=True)
    capsys.readouterr()
    SkillsCommand()._handle_remove(remove_all=True)
    text = err_text(capsys)
    assert (
        f"INFO: Amazon Q Developer has no skill folders, so nothing was removed and its deepctl 0.3.x file is kept; delete it yourself if you don't need it."
        in text
    )
    aider = "Aider has no skill folders, so nothing was removed and its deepctl 0.3.x file is kept; delete it and its entry under 'read:' in ~/.aider.conf.yml yourself if you don't need it."
    assert aider in text
    assert rule.read_bytes() == b"0.3.x rules"
