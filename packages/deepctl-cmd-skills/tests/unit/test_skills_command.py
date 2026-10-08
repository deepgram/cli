"""Unit tests for skills command."""

import errno
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest
from deepctl_cmd_skills import command
from deepctl_cmd_skills.command import SkillsCommand
from deepctl_core import output, skill_bundle
from deepctl_core import skill_generator as sg
from deepctl_core.skill_bundle import RepoSkill, SkillFetchError
from deepctl_core.skill_generator import _msg, get_all_generators


@pytest.fixture(autouse=True)
def _throwaway_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(sg, "_SKILLS_DIR", home / ".deepctl" / "skills")
    monkeypatch.setattr(sg, "_STATE_FILE", home / ".deepctl" / "skills" / "skills.json")
    monkeypatch.setenv("COLUMNS", "400")
    # Rich fixes a console's width at construction only if COLUMNS was set
    # then; pin _width on each so teardown restores the old value (often None).
    for con in (output.console, output.stderr_console, command.console):
        monkeypatch.setattr(con, "_width", 400)
    monkeypatch.delenv(skill_bundle.REF_ENV_VAR, raising=False)
    # Detection must not depend on what the test machine has on PATH.
    monkeypatch.setattr(shutil, "which", lambda name: None)
    return home


@pytest.fixture(autouse=True)
def _pinned_output():
    """S4: pin the agentic output mode so prefixes never depend on the env."""
    from deepctl_core import output

    saved = dict(output._output_config)
    output._output_config.update(agentic=True, format="default", quiet=False)
    yield
    output._output_config.clear()
    output._output_config.update(saved)


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    """Patch the one fetch point; return the list of refs fetched."""
    fetched = []

    def fetch(ref=None):
        fetched.append(ref)
        base = tmp_path / "bundle"
        skills = []
        for name in ("api", "docs"):
            folder = base / "skills" / name
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "SKILL.md").write_bytes(
                f"---\nname: {name}\n---\n{ref}\n".encode()
            )
            skills.append(RepoSkill(name, folder))
        return skills

    monkeypatch.setattr(skill_bundle, "fetch_skill_bundle", fetch)
    return fetched


@pytest.fixture(params=["set", "unset"])
def agent_env(request, monkeypatch):
    if request.param == "set":
        monkeypatch.setenv("CI", "1")
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setenv("TERM", "xterm")
        tty = True
    else:
        for name in ("CI", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
            monkeypatch.delenv(name, raising=False)
        tty = False
    monkeypatch.setattr(sys.stdin, "isatty", lambda: tty, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: tty, raising=False)
    return request.param


def err_text(capsys):
    return " ".join(click.unstyle(capsys.readouterr().err).split())


def gen(cli):
    return next(g for g in get_all_generators() if g.cli_name == cli)


def detect(*clis):
    for cli in clis:
        Path.home().joinpath(*gen(cli).homes[0]).mkdir(parents=True, exist_ok=True)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sha_tree(path):
    return {
        os.path.relpath(os.path.join(d, f), path): sha(os.path.join(d, f))
        for d, _, files in os.walk(path)
        for f in files
    }


def during_fetch(monkeypatch, action):
    """Run ``action`` (another command) once, while the next fetch "downloads"."""
    real, pending = skill_bundle.fetch_skill_bundle, [action]

    def fetch(ref=None):
        if pending:
            pending.pop()()
        return real(ref)

    monkeypatch.setattr(skill_bundle, "fetch_skill_bundle", fetch)


def write_state(state):
    sg._STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    sg._STATE_FILE.write_text(json.dumps(state), encoding="utf-8")


def disk_state():
    return json.loads(sg._STATE_FILE.read_bytes())


class TestSkillsCommand:
    """Test SkillsCommand class."""

    def test_init(self):
        cmd = SkillsCommand()
        assert cmd.name == "skills"
        assert "AI coding assistant" in cmd.help
        assert cmd.is_group is True

    def test_examples(self):
        cmd = SkillsCommand()
        assert len(cmd.examples) > 0
        assert any("install" in ex for ex in cmd.examples)
        assert any("status" in ex for ex in cmd.examples)

    def test_agent_help(self):
        cmd = SkillsCommand()
        assert cmd.agent_help
        assert "Claude Code" in cmd.agent_help or "AI coding" in cmd.agent_help

    def test_setup_commands_returns_subcommands(self):
        cmd = SkillsCommand()
        subcommands = {c.name: c for c in cmd.setup_commands()}
        assert {"install", "update", "remove", "list", "status", "setup"} <= set(
            subcommands
        )
        for name in ("install", "update", "setup"):
            assert "--ref" in [o for p in subcommands[name].params for o in p.opts]

    def test_declining_install_anyway_aborts_instead_of_exiting_zero(self, bundle):
        """Declining the prompt must exit 2, not 0.

        `_handle_install` is a plain click callback returning None, so there
        is no result for BaseCommand.EXIT_CODES to map to an exit code. A
        bare return made a declined install indistinguishable from a
        successful one, contradicting the exit-code table in the README.
        Abort is what main.py turns into 2.
        """
        cmd = SkillsCommand()
        with patch.object(cmd, "confirm", return_value=False):
            with pytest.raises(click.Abort):
                cmd._handle_install(cli_name="claude")
        assert bundle == []
        assert not gen("claude").skills_root().exists()

    def test_accepting_install_anyway_does_not_abort(self, bundle):
        """Positive control: confirming must proceed to the install."""
        cmd = SkillsCommand()
        with patch.object(cmd, "confirm", return_value=True):
            cmd._handle_install(cli_name="claude")
        assert (gen("claude").skills_root() / "api" / "SKILL.md").is_file()

    def test_unknown_cli_exits_one(self):
        """An unknown --cli must exit 1, not print an error and exit 0."""
        cmd = SkillsCommand()
        with pytest.raises(click.ClickException) as exc:
            cmd._handle_install(cli_name="nonexistent-cli")

        assert "Unknown AI CLI: nonexistent-cli" in str(exc.value)

    def test_removing_skills_that_are_not_installed_exits_one(self):
        """`skills remove --cli X` with nothing installed for X exits 1."""
        write_state({"installed_skills": {"claude": {"paths": []}}})
        cmd = SkillsCommand()
        with pytest.raises(click.ClickException) as exc:
            cmd._handle_remove(cli_name="cursor", remove_all=False)

        assert "No skills installed for 'cursor'" in str(exc.value)


class TestAgentPrefixes:
    """S4: the OK/INFO prefixes hold with CI, CLAUDECODE and a TTY set or unset."""

    def test_install_success_line_has_ok_prefix(self, agent_env, bundle, capsys):
        detect("claude")
        SkillsCommand()._handle_install(install_all=True)
        assert "OK: Claude Code: installed 2 skills in" in err_text(capsys)

    def test_list_hint_has_info_prefix(self, agent_env, capsys):
        SkillsCommand()._handle_list()
        assert (
            "INFO: No skill folders are installed; run 'dg skills install' to get started."
            in err_text(capsys)
        )


class TestSkillsFlows:
    def test_preflight_across_tools_writes_nothing_on_conflict(self, bundle):
        detect("claude", "cursor")
        mine = gen("cursor").skills_root() / "api"
        mine.mkdir(parents=True)
        (mine / "SKILL.md").write_bytes(b"mine")
        with pytest.raises(click.ClickException) as exc:
            SkillsCommand()._handle_install(install_all=True)
        assert exc.value.exit_code == 1
        assert exc.value.message == _msg("E1", paths=str(mine))
        assert not gen("claude").skills_root().exists()
        assert not sg._STATE_FILE.exists()

    def test_fetch_failure_writes_nothing(self, monkeypatch):
        detect("claude")

        def fail(ref=None):
            raise SkillFetchError("Could not download the bundle.")

        monkeypatch.setattr(skill_bundle, "fetch_skill_bundle", fail)
        with pytest.raises(click.ClickException) as exc:
            SkillsCommand()._handle_install(install_all=True)
        assert exc.value.message == "Could not download the bundle."
        assert not gen("claude").skills_root().exists()
        assert not sg._STATE_FILE.exists()

    def test_hint_only_tool_prints_hint_exit_zero(self, bundle, capsys):
        detect("amazonq")
        SkillsCommand()._handle_install(cli_name="amazonq")
        err = err_text(capsys)
        assert _msg("E15", gen("amazonq")) in err
        assert "No skills were installed." in err
        assert bundle == []

    def test_update_targets_records_and_03x_entries(self, bundle):
        old = Path.home() / ".codex" / "instructions.md"
        old.parent.mkdir()
        old.write_bytes(b"user text\n<!-- BEGIN deepctl -->\nold\n")
        before = sha(old)
        write_state(
            {"installed_skills": {"codex": {"paths": [str(old)], "version": "0.3.2"}}}
        )
        SkillsCommand()._handle_update()
        assert (gen("codex").skills_root() / "api" / sg._MARKER).is_file()
        assert sha(old) == before
        assert set(disk_state()["skill_folders"]["codex"]["folders"]) == {"api", "docs"}

    @pytest.mark.parametrize("how", ["recorded", "--ref", "env"])
    def test_update_follows_recorded_ref_unless_overridden(
        self, bundle, monkeypatch, how
    ):
        detect("claude")
        SkillsCommand()._handle_install(install_all=True, ref="my-branch")
        assert bundle == ["my-branch"]
        if how == "env":
            monkeypatch.setenv(skill_bundle.REF_ENV_VAR, "env-ref")
        SkillsCommand()._handle_update(ref="other" if how == "--ref" else None)
        assert (
            bundle[-1]
            == {"recorded": "my-branch", "--ref": "other", "env": "env-ref"}[how]
        )

    def test_update_keeps_a_ref_installed_during_its_download(
        self, bundle, monkeypatch, capsys
    ):
        detect("claude")
        SkillsCommand()._handle_install(install_all=True)
        during_fetch(
            monkeypatch,
            lambda: SkillsCommand()._handle_install(cli_name="claude", ref="newer"),
        )
        SkillsCommand()._handle_update()  # Returns: exit 0.
        assert disk_state()["skill_folders"]["claude"]["skills_ref"] == "newer"
        assert (
            b"newer" in (gen("claude").skills_root() / "api" / "SKILL.md").read_bytes()
        )
        assert err_text(capsys).count(_msg("E30", gen("claude"))) == 1

    def test_update_keeps_a_newer_copy_of_the_same_moving_ref(
        self, bundle, tmp_path, monkeypatch, capsys
    ):
        """B1 (review): one ref, two bodies; the newer lands first and stays."""
        detect("claude")
        SkillsCommand()._handle_install(install_all=True)
        folder = tmp_path / "newer" / "api"
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_bytes(b"---\nname: api\n---\nnewer body\n")
        ref = disk_state()["skill_folders"]["claude"]["skills_ref"]

        def other():  # Same ref spelling, but it moved: a different body.
            sg.install_tool(
                gen("claude"), [RepoSkill("api", folder)], ref=ref, version="9"
            )

        during_fetch(monkeypatch, other)
        capsys.readouterr()
        SkillsCommand()._handle_update()  # Returns: exit 0.
        api = gen("claude").skills_root() / "api" / "SKILL.md"
        assert api.read_bytes() == b"---\nname: api\n---\nnewer body\n"
        assert disk_state()["skill_folders"]["claude"]["version"] == "9"
        err = err_text(capsys)
        assert err.count(_msg("E32", gen("claude"))) == 1
        assert _msg("E30", gen("claude")) not in err
        assert "Installed" not in err

    def test_update_with_ref_overrides_but_skips_a_removed_tool(
        self, bundle, monkeypatch, capsys
    ):
        detect("claude", "cursor")
        SkillsCommand()._handle_install(install_all=True)

        def other():
            SkillsCommand()._handle_install(cli_name="claude", ref="newer")
            SkillsCommand()._handle_remove(cli_name="cursor")

        during_fetch(monkeypatch, other)
        SkillsCommand()._handle_update(ref="mine")
        state = disk_state()
        assert state["skill_folders"]["claude"]["skills_ref"] == "mine"
        assert "cursor" not in state["skill_folders"]
        assert not (gen("cursor").skills_root() / "api").exists()
        err = err_text(capsys)
        assert err.count(_msg("E31", gen("cursor"))) == 1
        assert _msg("E30", gen("claude")) not in err

    def test_update_summary_counts_only_tools_it_installed(
        self, bundle, monkeypatch, capsys
    ):
        detect("claude", "cursor")
        SkillsCommand()._handle_install(cli_name="claude", ref="claude-ref")
        SkillsCommand()._handle_install(cli_name="cursor", ref="cursor-ref")
        during_fetch(
            monkeypatch, lambda: SkillsCommand()._handle_remove(cli_name="claude")
        )
        capsys.readouterr()
        SkillsCommand()._handle_update()
        err = err_text(capsys)
        assert err.count(_msg("E31", gen("claude"))) == 1
        assert "for 1 tool from deepgram/skills cursor-ref." in err
        assert "claude-ref" not in err
        assert "/setup-mcp" not in err

    def test_only_this_runs_staging_gets_e12(self, bundle, monkeypatch, capsys):
        detect("claude")
        root = gen("claude").skills_root()
        seeded = [root / ".deepctl-staging-old1", root / ".deepctl-staging-mine"]
        for d in seeded:
            d.mkdir(parents=True)
            (d / "f").write_bytes(b"x")
        SkillsCommand()._handle_install(install_all=True)
        assert "could not finish cleaning up" not in err_text(capsys)
        SkillsCommand()._handle_status()
        err = err_text(capsys)
        for d in seeded:
            assert _msg("E16", path=d) in err
        real = shutil.rmtree

        def keep_this_runs_copies(path, *a, **k):
            if Path(path).parent.name == "old":
                return None
            return real(path, *a, **k)

        monkeypatch.setattr(shutil, "rmtree", keep_this_runs_copies)
        SkillsCommand()._handle_update()
        err = err_text(capsys)
        assert err.count("could not finish cleaning up") == 1
        new = [
            n
            for n in os.listdir(root)
            if n.startswith(".deepctl-staging-") and root / n not in seeded
        ]
        assert len(new) == 1
        assert _msg("E12", staging=root / new[0]) in err
        assert all(str(d) not in err for d in seeded)

    def test_remove_exit_one_when_folder_kept(self, bundle, monkeypatch, capsys):
        detect("claude")
        SkillsCommand()._handle_install(install_all=True)
        api = gen("claude").skills_root() / "api"
        real = os.rename

        def deny(src, dst, *a, **k):
            if Path(src) == api:
                raise PermissionError(errno.EACCES, "Permission denied")
            return real(src, dst, *a, **k)

        monkeypatch.setattr(os, "rename", deny)
        capsys.readouterr()
        with pytest.raises(click.ClickException) as exc:
            SkillsCommand()._handle_remove(remove_all=True)
        assert exc.value.exit_code == 1
        assert exc.value.message == (
            "Some skill folders were not fully removed; fix the problems listed above."
        )
        assert _msg("E13", dest=api, reason="Permission denied") in err_text(capsys)
        assert api.is_dir()

    def test_remove_leaves_an_empty_folder_at_a_recorded_name(self, bundle, capsys):
        detect("claude")
        SkillsCommand()._handle_install(install_all=True)
        docs = gen("claude").skills_root() / "docs"
        shutil.rmtree(docs)
        docs.mkdir()  # deepctl never makes one, so it is not deepctl's.
        capsys.readouterr()
        SkillsCommand()._handle_remove(remove_all=True)  # Exit 0.
        err = err_text(capsys)
        assert _msg("E26", dest=docs) in err
        assert "still recorded" not in err
        assert "Removed 1 skill folder from 1 tool." in err
        assert "claude" not in sg.get_skills_state().get("skill_folders", {})
        assert docs.is_dir() and os.listdir(docs) == []

    def test_install_while_another_command_holds_the_lock_exits_one(
        self, bundle, monkeypatch
    ):
        detect("claude")
        monkeypatch.setattr(sg, "_LOCK_TIMEOUT", 0.3)
        fd = sg._try_lock(sg._STATE_FILE.with_name("skills.json.lock"))
        assert fd >= 0  # As another deepctl process would.
        try:
            with pytest.raises(click.ClickException) as exc:
                SkillsCommand()._handle_install(install_all=True)
        finally:
            if sys.platform == "win32":
                sg.msvcrt.locking(fd, sg.msvcrt.LK_UNLCK, 1)
            os.close(fd)
        assert (exc.value.exit_code, exc.value.message) == (1, _msg("E27"))
        assert not sg._STATE_FILE.exists()
        assert not gen("claude").skills_root().exists()

    def test_remove_waits_for_the_lock_once_for_all_tools(
        self, bundle, monkeypatch, capsys
    ):
        detect("claude", "cursor")
        SkillsCommand()._handle_install(install_all=True)
        roots = [gen(c).skills_root() for c in ("claude", "cursor")]
        before = sg._STATE_FILE.read_bytes(), [sha_tree(r) for r in roots]
        capsys.readouterr()
        monkeypatch.setattr(sg, "_LOCK_TIMEOUT", 0.5)
        calls = []
        real_remove = sg.remove_tool
        monkeypatch.setattr(
            sg, "remove_tool", lambda g: calls.append(g) or real_remove(g)
        )
        fd = sg._try_lock(sg._STATE_FILE.with_name("skills.json.lock"))
        assert fd >= 0  # As another deepctl process would.
        try:
            start = time.monotonic()
            with pytest.raises(click.ClickException) as exc:
                SkillsCommand()._handle_remove(remove_all=True)
            took = time.monotonic() - start
        finally:
            if sys.platform == "win32":
                sg.msvcrt.locking(fd, sg.msvcrt.LK_UNLCK, 1)
            os.close(fd)
        assert (exc.value.exit_code, exc.value.message) == (1, _msg("E27"))
        assert 0.5 <= took < 1.0  # One wait, not one per tool.
        assert calls == [] and _msg("E27") not in err_text(capsys)
        assert (sg._STATE_FILE.read_bytes(), [sha_tree(r) for r in roots]) == before

    def test_fetch_runs_without_the_lock(self, bundle, monkeypatch):
        detect("claude")
        real = skill_bundle.fetch_skill_bundle

        def fetch(ref=None):
            assert getattr(sg._LOCAL, "fd", None) is None
            return real(ref)

        monkeypatch.setattr(skill_bundle, "fetch_skill_bundle", fetch)
        SkillsCommand()._handle_install(install_all=True)
        assert bundle and (gen("claude").skills_root() / "api").is_dir()

    @pytest.mark.parametrize("cli", ["claude", "cursor"])
    def test_setup_mcp_hint_only_after_claude_install(self, bundle, capsys, cli):
        detect(cli)
        SkillsCommand()._handle_install(install_all=True)
        err = err_text(capsys)
        assert "Installed 2 skill folders for 1 tool from" in err
        assert f"from deepgram/skills {skill_bundle.DEFAULT_SKILLS_RELEASE}." in err
        hint = "In Claude Code, run /setup-mcp to configure the Deepgram MCP server."
        assert (hint in err) == (cli == "claude")
        assert "/deepgram:setup-mcp" not in err

    def test_failed_install_reports_its_staging(self, bundle, monkeypatch, capsys):
        detect("claude")
        staging = gen("claude").skills_root() / ".deepctl-staging-x"

        def fail(*a, **k):
            exc = sg.SkillInstallError("boom")
            exc.leftover = staging
            raise exc

        monkeypatch.setattr(sg, "install_tool", fail)
        with pytest.raises(click.ClickException) as exc:
            SkillsCommand()._handle_install(install_all=True)
        assert exc.value.message == "boom"
        assert _msg("E12", staging=staging) in err_text(capsys)

    def test_remove_exit_one_when_staging_left(self, bundle, monkeypatch, capsys):
        detect("claude")
        SkillsCommand()._handle_install(install_all=True)
        monkeypatch.setattr(shutil, "rmtree", lambda *a, **k: None)
        capsys.readouterr()
        with pytest.raises(click.ClickException) as exc:
            SkillsCommand()._handle_remove(remove_all=True)
        assert exc.value.exit_code == 1
        assert "could not finish cleaning up" in err_text(capsys)

    @pytest.mark.parametrize("old", [True, False])
    def test_remove_notes_03x_files_left_behind(self, bundle, capsys, old):
        detect("claude")
        if old:
            write_state(
                {
                    "installed_skills": {
                        "claude": {
                            "paths": [
                                str(
                                    Path.home()
                                    / ".claude"
                                    / "commands"
                                    / "deepgram"
                                    / "api.md"
                                )
                            ]
                        }
                    }
                }
            )
        SkillsCommand()._handle_install(install_all=True)
        SkillsCommand()._handle_update()  # Its own folders are not 0.3.x files.
        capsys.readouterr()
        SkillsCommand()._handle_remove(remove_all=True)
        err = err_text(capsys)
        assert ("0.3.x" in err) == old
        assert (
            "For Claude Code, files from deepctl 0.3.x stay until a later release."
            in err
        ) == old
        assert "OK: Removed 2 skill folders from 1 tool." in err

    def test_remove_03x_only_tool_message_and_files_untouched(self, capsys):
        old = Path.home() / ".cursor" / "rules" / "deepctl.mdc"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"0.3.x rules")
        before = sha(old)
        write_state(
            {"installed_skills": {"cursor": {"paths": [str(old)]}}, "auto_update": True}
        )
        SkillsCommand()._handle_remove(remove_all=True)
        err = err_text(capsys)
        assert "Cursor has no skill folders recorded, so nothing was removed" in err
        assert "OK: Removed 0 skill folders from 0 tools." in err
        assert sha(old) == before
        assert "cursor" not in disk_state()["installed_skills"]

    def test_remove_tool_with_no_records_or_paths_says_so(self, capsys):
        write_state({"installed_skills": {"cursor": {"paths": []}}})
        SkillsCommand()._handle_remove(remove_all=True)
        assert err_text(capsys) == (
            "INFO: Cursor has no skill folders recorded, so nothing was removed. "
            "OK: Removed 0 skill folders from 0 tools."
        )

    def test_remove_tool_error_is_printed_and_exits_one(self, bundle, capsys):
        detect("claude", "cursor")
        SkillsCommand()._handle_install(install_all=True)
        shutil.rmtree(gen("claude").skills_root())
        gen("claude").skills_root().write_bytes(b"not a folder")
        capsys.readouterr()
        with pytest.raises(click.ClickException) as exc:
            SkillsCommand()._handle_remove(remove_all=True)
        assert exc.value.exit_code == 1
        assert exc.value.message == (
            "Some skill folders were not fully removed; fix the problems listed above."
        )
        assert f"ERROR: {_msg('E18', gen('claude'))}" in err_text(capsys)
        assert not (gen("cursor").skills_root() / "api").exists()  # It went on.
        assert "claude" in disk_state()["skill_folders"]

    def test_remove_e21_is_printed_and_exits_one(self, bundle, monkeypatch, capsys):
        detect("claude")
        SkillsCommand()._handle_install(install_all=True)
        saved = sg._STATE_FILE.read_bytes()

        def denied(*a, **k):
            raise PermissionError(errno.EACCES, "Permission denied")

        monkeypatch.setattr(sg.tempfile, "mkdtemp", denied)
        capsys.readouterr()
        with pytest.raises(click.ClickException) as exc:
            SkillsCommand()._handle_remove(remove_all=True)
        assert exc.value.exit_code == 1
        why = _msg("E21", root=gen("claude").skills_root(), reason="Permission denied")
        assert f"ERROR: {why}" in err_text(capsys)
        assert sg._STATE_FILE.read_bytes() == saved
        assert (gen("claude").skills_root() / "api").is_dir()

    def test_setup_passes_ref_through(self, bundle):
        detect("claude")
        SkillsCommand()._handle_setup(install_all=True, ref="my-branch")
        assert bundle == ["my-branch"]
        assert disk_state()["skill_folders"]["claude"]["skills_ref"] == "my-branch"

    def test_corrupt_state_fails_before_any_prompt(self, bundle):
        detect("claude")
        sg._STATE_FILE.parent.mkdir(parents=True)
        sg._STATE_FILE.write_bytes(b"{")
        cmd = SkillsCommand()
        confirm = MagicMock(return_value=True)
        with (
            patch.object(cmd, "confirm", confirm),
            pytest.raises(click.ClickException) as exc,
        ):
            cmd._handle_install()
        assert exc.value.message == _msg("E7")
        confirm.assert_not_called()
        assert bundle == []

    def test_update_with_nothing_installed_says_so(self, capsys):
        SkillsCommand()._handle_update()
        assert err_text(capsys) == (
            "INFO: No skills are installed, so there is nothing to update; "
            "run 'dg skills install' first."
        )

    def test_status_counts_only_proven_folders(self, bundle, capsys):
        detect("claude")
        SkillsCommand()._handle_install(install_all=True)
        with open(gen("claude").skills_root() / "docs" / "SKILL.md", "ab") as f:
            f.write(b"mine\n")
        capsys.readouterr()
        SkillsCommand()._handle_status()
        (row,) = [r for r in capsys.readouterr().out.splitlines() if "Claude Code" in r]
        assert row.split("│")[-2].strip() == "1"

    def test_setup_mcp_hint_when_claude_is_not_first(self, bundle, capsys):
        ref = skill_bundle.DEFAULT_SKILLS_COMMIT
        SkillsCommand()._install([(gen("cursor"), ref), (gen("claude"), ref)])
        hint = "In Claude Code, run /setup-mcp to configure the Deepgram MCP server."
        assert hint in err_text(capsys)

    def test_status_ignores_a_recorded_folder_the_user_deleted(self, bundle, capsys):
        detect("claude")
        SkillsCommand()._handle_install(install_all=True)
        api = gen("claude").skills_root() / "api"
        shutil.rmtree(api)
        capsys.readouterr()
        SkillsCommand()._handle_status()
        err = err_text(capsys)
        assert str(api) not in err
        assert "cannot prove" not in err

    @pytest.mark.parametrize(
        "sub", ["status", "install", "update", "remove", "list", "setup"]
    )
    def test_corrupt_state_exits_one_file_unchanged(self, bundle, sub):
        detect("claude")
        write_state({})
        sg._STATE_FILE.write_bytes(b"{")
        cmd = SkillsCommand()
        call = {
            "status": cmd._handle_status,
            "install": lambda: cmd._handle_install(install_all=True),
            "update": cmd._handle_update,
            "remove": lambda: cmd._handle_remove(remove_all=True),
            "list": cmd._handle_list,
            "setup": lambda: cmd._handle_setup(install_all=True),
        }[sub]
        with pytest.raises(click.ClickException) as exc:
            call()
        assert exc.value.exit_code == 1
        assert exc.value.message == _msg("E7")
        assert sg._STATE_FILE.read_bytes() == b"{"
        assert not gen("claude").skills_root().exists()

    def test_status_and_list_report_unproven_leftovers_and_03x_note(
        self, bundle, monkeypatch, capsys
    ):
        detect("claude", "amazonq")
        SkillsCommand()._handle_install(cli_name="claude")
        root = gen("claude").skills_root()
        (root / "api" / sg._MARKER).unlink()
        with open(root / "docs" / "SKILL.md", "ab") as f:
            f.write(b"mine\n")
        leftover = root / ".deepctl-staging-x"
        leftover.mkdir()
        state = disk_state()
        state["installed_skills"]["codex"] = {"paths": ["/old/instructions.md"]}
        state["installed_skills"]["amazonq"] = {"paths": ["/old/deepctl.md"]}
        write_state(state)
        capsys.readouterr()
        SkillsCommand()._handle_status()
        err = err_text(capsys)
        assert _msg("E14", dest=root / "api") in err
        assert _msg("E24", dest=root / "docs") in err
        assert _msg("E16", path=leftover) in err
        assert _msg("E15", gen("amazonq")) in err
        assert _msg("E15", gen("aider")) not in err  # Not detected.
        assert "Files from deepctl 0.3.x are recorded for OpenAI Codex;" in err
        SkillsCommand()._handle_list()
        out = capsys.readouterr()
        assert "0/2" in out.out
        assert skill_bundle.DEFAULT_SKILLS_RELEASE in out.out
        assert "0.3.x" not in " ".join(out.err.split())

        target = os.fspath(root / "docs" / "SKILL.md")
        real = os.open

        def deny(path, *a, **k):
            if os.fspath(path) == target:
                raise PermissionError(errno.EACCES, "Permission denied")
            return real(path, *a, **k)

        monkeypatch.setattr(os, "open", deny)
        SkillsCommand()._handle_status()
        assert _msg("E25", dest=root / "docs") in err_text(capsys)

    def test_update_halts_every_tool_when_one_folder_is_edited(self, bundle):
        detect("claude", "cursor")
        SkillsCommand()._handle_install(install_all=True)
        with open(gen("cursor").skills_root() / "api" / "SKILL.md", "ab") as f:
            f.write(b"mine\n")
        claude, saved = (
            sha_tree(gen("claude").skills_root()),
            sg._STATE_FILE.read_bytes(),
        )
        with pytest.raises(click.ClickException) as exc:
            SkillsCommand()._handle_update()
        assert exc.value.exit_code == 1
        assert sha_tree(gen("claude").skills_root()) == claude
        assert sg._STATE_FILE.read_bytes() == saved

    def test_update_warns_about_unknown_recorded_cli(self, capsys):
        write_state({"installed_skills": {"vim": {"paths": []}}})
        SkillsCommand()._handle_update()
        assert "Unknown CLI 'vim', skipping." in err_text(capsys)

    def test_remove_moved_folder_reports_e4_and_exits_one(self, monkeypatch, capsys):
        write_state({"installed_skills": {"claude": {"paths": []}}})
        api = gen("claude").skills_root() / "api"
        aside = api.parent / ".deepctl-staging-x" / "old" / "api"
        res = sg.RemoveResult(moved=[(api, aside)])
        monkeypatch.setattr(sg, "remove_tool", lambda g: res)
        with pytest.raises(click.ClickException) as exc:
            SkillsCommand()._handle_remove(remove_all=True)
        assert exc.value.exit_code == 1
        assert _msg("E4", dest=api, aside=aside) in err_text(capsys)

    def test_remove_notes_03x_files_after_a_plugin_refresh(self, bundle, capsys):
        detect("claude")
        old = Path.home() / ".claude" / "commands" / "deepgram" / "api.md"
        rule = Path.home() / ".amazonq" / "rules" / "deepctl.md"
        write_state(
            {
                "installed_skills": {
                    "claude": {"paths": [str(old)]},
                    "amazonq": {"paths": [str(rule)]},
                }
            }
        )
        from deepctl_cmd_plugin.command import PluginCommand

        capsys.readouterr()
        PluginCommand()._maybe_update_skills()  # The real plugin refresh.
        assert err_text(capsys) == ""
        assert "claude" in disk_state()["skill_folders"]
        assert disk_state()["installed_skills"]["amazonq"] == {"paths": [str(rule)]}
        assert disk_state()["installed_skills"]["claude"]["paths"] == [str(old)]
        SkillsCommand()._handle_update()  # A later write keeps the flag.
        capsys.readouterr()
        SkillsCommand()._handle_remove(remove_all=True)
        err, v03 = (
            err_text(capsys),
            "files from deepctl 0.3.x stay until a later release.",
        )
        assert f"For Claude Code, {v03}" in err
        assert (
            f"Amazon Q Developer has no skill folders recorded, so nothing was removed; {v03}"
            in err
        )

    def test_update_edited_folder_exits_one_with_rename_advice(self, bundle):
        detect("claude")
        SkillsCommand()._handle_install(install_all=True)
        api = gen("claude").skills_root() / "api"
        with open(api / "SKILL.md", "ab") as f:
            f.write(b"mine\n")
        with pytest.raises(click.ClickException) as exc:
            SkillsCommand()._handle_update()
        assert exc.value.exit_code == 1
        assert " ".join(click.unstyle(exc.value.format_message()).split()) == _msg(
            "E22", dest=api
        )

    def test_remove_edited_folder_exits_zero_and_reports(self, bundle, capsys):
        detect("claude")
        SkillsCommand()._handle_install(install_all=True)
        api = gen("claude").skills_root() / "api"
        with open(api / "SKILL.md", "ab") as f:
            f.write(b"mine\n")
        capsys.readouterr()
        SkillsCommand()._handle_remove(remove_all=True)
        err = err_text(capsys)
        assert _msg("E23", dest=api) in err
        assert "OK: Removed 1 skill folder from 1 tool." in err
        assert api.is_dir()

    def test_remove_reports_folder_without_marker(self, bundle, capsys):
        detect("claude")
        SkillsCommand()._handle_install(install_all=True)
        api = gen("claude").skills_root() / "api"
        (api / sg._MARKER).unlink()
        SkillsCommand()._handle_remove(remove_all=True)
        err = err_text(capsys)
        assert _msg("E26", dest=api) in err
        assert _msg("E14", dest=api) not in err
        assert api.is_dir()
        assert "claude" not in disk_state().get("skill_folders", {})

    def test_upgrade_home_from_main_has_no_data_loss(self, bundle, capsys):
        home = Path.home()
        files = {
            ".claude/commands/deepgram/api.md": b"api",
            ".claude/commands/deepgram/docs.md": b"docs",
            ".claude/commands/deepgram/setup-mcp.md": b"setup",
            ".claude/commands/deepgram/starters.md": b"starters",
            ".claude/commands/deepgram/mine.md": b"the user's own command",
            ".codex/instructions.md": b"user text\n<!-- BEGIN deepctl -->x<!-- END -->\n",
            ".gemini/GEMINI.md": b"<!-- BEGIN deepctl -->x<!-- END -->\n",
            ".opencode/agents.md": b"<!-- BEGIN deepctl -->x<!-- END -->\n",
            ".amazonq/rules/deepctl.md": b"q",
            ".cursor/rules/deepctl.mdc": b"cursor",
            ".cline/rules/deepctl.md": b"cline",
            ".deepctl/skills/deepctl-conventions.md": b"aider",
            ".aider.conf.yml": b"read: [x]\n",
            ".deepctl/skills/repo_cache/api.md": b"cache",
            ".deepctl/skills/repo_cache/.fetched": b"1",
        }
        for rel, data in files.items():
            path = home.joinpath(*rel.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        tools = (
            "claude",
            "codex",
            "gemini",
            "amazonq",
            "aider",
            "opencode",
            "cursor",
            "cline",
        )
        legacy = {
            t: {
                "paths": ["x"],
                "installed_at": "2026-01-02T03:04:05+00:00",
                "version": "0.3.2",
                "commands_hash": "sha256:ab12",
            }
            for t in tools
        }
        write_state({"installed_skills": legacy, "auto_update": True})
        before = {rel: sha(home.joinpath(*rel.split("/"))) for rel in files}

        def unchanged():
            return {rel: sha(home.joinpath(*rel.split("/"))) for rel in files} == before

        def fingerprinted():
            for tool in disk_state()["skill_folders"].values():
                for rec in tool["folders"].values():
                    assert rec["state"] == "installed"
                    assert sg._FP_PATTERN.fullmatch(rec["fingerprint"])
                    assert "pending" not in rec

        cmd = SkillsCommand()
        cmd._handle_status()
        assert "Run 'dg skills install'" not in err_text(capsys)  # 0.3.x recorded.
        cmd._handle_install(install_all=True)
        assert unchanged()
        fingerprinted()
        assert len(disk_state()["skill_folders"]) == 6
        assert disk_state()["installed_skills"] == legacy
        cmd._handle_update()
        assert unchanged()
        fingerprinted()
        cmd._handle_remove(remove_all=True)
        assert unchanged()
        assert disk_state().get("skill_folders", {}) == {}
        for g in get_all_generators():
            if g.skills_root() is not None:
                assert os.listdir(g.skills_root()) == []


class TestSkillsStartupCheck:
    """Test startup check module."""

    def setup_method(self):
        """Reset module-level state between tests."""
        from deepctl_cmd_skills import startup_check

        # Join any lingering thread from prior tests
        if startup_check._thread is not None:
            startup_check._thread.join(timeout=2.0)
        startup_check._thread = None
        startup_check._result = {}

    def test_import(self):
        from deepctl_cmd_skills.startup_check import (
            check_and_notify,
            print_pending_notification,
        )

        assert callable(check_and_notify)
        assert callable(print_pending_notification)

    @patch("deepctl_cmd_skills.startup_check._is_ci", return_value=True)
    def test_suppressed_in_ci(self, mock_ci):
        from deepctl_cmd_skills import startup_check

        startup_check.check_and_notify(quiet=False)
        assert startup_check._thread is None

    def test_suppressed_when_quiet(self):
        from deepctl_cmd_skills import startup_check

        startup_check.check_and_notify(quiet=True)
        assert startup_check._thread is None

    @pytest.mark.parametrize(
        "error",
        [
            BrokenPipeError(32, "Broken pipe"),
            ValueError("I/O operation on closed file"),
        ],
    )
    def test_broken_pipe_swallowed(self, error):
        """A closed/broken stderr (e.g. `dg mcp` host disconnect) is tolerated."""
        import threading

        from deepctl_cmd_skills import startup_check

        startup_check._result = {"should_prompt": True}
        startup_check._thread = threading.Thread(target=lambda: None)
        startup_check._thread.start()
        startup_check._thread.join()

        broken_stderr = MagicMock()
        broken_stderr.write.side_effect = error
        with patch.object(sys, "stderr", broken_stderr):
            # Must not raise.
            startup_check.print_pending_notification()
