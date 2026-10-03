"""Unit tests for skills command."""

import contextlib
import json
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner
from deepctl_cmd_skills import command as skills_command
from deepctl_cmd_skills.command import SkillsCommand
from deepctl_core import output, skill_generator
from deepctl_core.skill_bundle import (
    DEFAULT_SKILLS_REF,
    PINNED_REF_SOURCE,
    REF_ENV_VAR,
    RepoSkill,
    SkillFetchError,
    SkillRefNotFoundError,
)
from deepctl_core.skill_generator import AiderGenerator

#: Set by the autouse fixture below, so one test can prove it is in force.
_ACTIVE_THROWAWAY_HOME: Path | None = None


@pytest.fixture(autouse=True)
def _throwaway_home(tmp_path, monkeypatch):
    """Point every home-derived path in this module at a throwaway directory.

    Nothing here may read or write the home of whoever is running pytest.
    The handlers read ``skills.json`` before anything a test patches, the
    generators resolve ``Path.home()`` when called, and ``_SKILLS_DIR``,
    ``_STATE_FILE``, ``_REPO_CACHE_DIR`` and ``AiderGenerator._LEGACY_FILE``
    are evaluated at import time, so each is pointed at the throwaway home
    by hand. Same pattern as deepctl-core's test_skill_generator.py.
    """
    home = tmp_path / "throwaway-home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    # Rich sizes its output to the terminal; a narrow one folds a long
    # tmp_path in the middle, and assertions on that path then fail for
    # reasons that have nothing to do with what the command did.
    monkeypatch.setenv("COLUMNS", "400")
    # A developer's own pin must not decide which ref a test resolves.
    monkeypatch.delenv(REF_ENV_VAR, raising=False)

    skills_dir = home / ".deepctl" / "skills"
    monkeypatch.setattr(skill_generator, "_SKILLS_DIR", skills_dir)
    monkeypatch.setattr(skill_generator, "_STATE_FILE", skills_dir / "skills.json")
    monkeypatch.setattr(skill_generator, "_REPO_CACHE_DIR", skills_dir / "repo_cache")
    monkeypatch.setattr(
        AiderGenerator, "_LEGACY_FILE", skills_dir / "deepctl-conventions.md"
    )

    global _ACTIVE_THROWAWAY_HOME
    _ACTIVE_THROWAWAY_HOME = home
    yield home
    _ACTIVE_THROWAWAY_HOME = None


@pytest.fixture(autouse=True)
def _default_output_mode():
    """Start every test in default, non-agentic, non-quiet output mode.

    ``deepctl_core.output`` keeps the output format in a module-global
    ``_output_config``. Another package's tests can leave it set to json
    or agentic, and in the full suite that is what the handlers here saw
    when they asked ``get_output_format()`` and skipped the table. There
    is no public setter for the agentic flag, so the dict is snapshotted
    and restored directly.
    """
    saved = dict(output._output_config)
    saved_quiet = output.console.quiet
    output._output_config.update(format="default", agentic=False, quiet=False)
    output.console.quiet = False
    yield
    output._output_config.clear()
    output._output_config.update(saved)
    output.console.quiet = saved_quiet


def test_no_test_in_this_module_can_reach_the_real_home():
    """The guard above is the finding, so it gets its own assertion."""
    home = _ACTIVE_THROWAWAY_HOME
    assert home is not None, "the throwaway-home fixture is no longer autouse"
    assert Path.home() == home
    assert skill_generator._STATE_FILE.is_relative_to(home)
    assert skill_generator._REPO_CACHE_DIR.is_relative_to(home)


@pytest.fixture
def json_output():
    """Put the framework in `-o json` mode for one test, then put it back."""
    previous = output._output_config["format"]
    output.update_output(format_type="json")
    yield
    output._output_config["format"] = previous


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
        subcommands = cmd.setup_commands()
        names = {c.name for c in subcommands}
        assert "install" in names
        assert "update" in names
        assert "remove" in names
        assert "list" in names
        assert "status" in names

    def test_install_update_and_setup_accept_a_ref(self):
        """Pinning has to be overridable without editing the source."""
        cmd = SkillsCommand()
        by_name = {c.name: c for c in cmd.setup_commands()}
        for name in ("install", "update", "setup"):
            options = {p.name for p in by_name[name].params}
            assert "ref" in options, name

    def test_declining_install_anyway_aborts_instead_of_exiting_zero(self):
        """Declining the prompt must exit 2, not 0.

        `_handle_install` is a plain click callback returning None, so there
        is no result for BaseCommand.EXIT_CODES to map to an exit code. A
        bare return made a declined install indistinguishable from a
        successful one, contradicting the exit-code table in the README.
        Abort is what main.py turns into 2.
        """
        cmd = SkillsCommand()
        generator = MagicMock()
        generator.cli_name = "claude"
        generator.display_name = "Claude Code"
        generator.detect.return_value = False

        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[generator],
            ),
            patch.object(cmd, "_ask", return_value=False),
            pytest.raises(click.Abort),
        ):
            cmd._handle_install(cli_name="claude")

        generator.install_skills.assert_not_called()

    def test_accepting_install_anyway_does_not_abort(self, tmp_path):
        """Positive control: confirming must proceed to the install."""
        cmd = SkillsCommand()
        generator = MagicMock()
        generator.cli_name = "claude"
        generator.display_name = "Claude Code"
        generator.detect.return_value = False
        root = tmp_path / ".claude" / "skills"
        generator.skills_root.return_value = root
        generator.install_conflicts.return_value = []
        generator.install_skills.return_value = [root / "api"]
        generator.prune_retired_result.return_value = skill_generator.PruneResult()

        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[generator],
            ),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata",
                return_value=[],
            ),
            patch(
                "deepctl_core.skill_generator.get_skills_state",
                return_value={"installed_skills": {}},
            ),
            patch("deepctl_core.skill_generator.save_skills_state"),
            patch(
                "deepctl_core.skill_generator.fetch_repo_skills",
                return_value=[RepoSkill(name="api", path=tmp_path / "api")],
            ),
            patch.object(cmd, "_ask", return_value=True),
        ):
            cmd._handle_install(cli_name="claude")

        generator.install_skills.assert_called_once()

    def test_unknown_cli_exits_one(self):
        """An unknown --cli must exit 1, not print an error and exit 0."""
        cmd = SkillsCommand()
        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[],
            ),
            pytest.raises(click.ClickException) as exc,
        ):
            cmd._handle_install(cli_name="nonexistent-cli")

        assert "Unknown AI CLI: nonexistent-cli" in str(exc.value)

    @pytest.mark.parametrize("installed", [{}, {"claude": {"paths": []}}])
    def test_removing_an_unknown_cli_exits_one_either_way(self, installed):
        """An empty machine used to exit 0 here, and one with skills 1."""
        cmd = SkillsCommand()
        with (
            patch(
                "deepctl_core.skill_generator.get_skills_state",
                return_value={"installed_skills": installed},
            ) as read,
            pytest.raises(click.ClickException) as exc,
        ):
            cmd._handle_remove(cli_name="nosuch", remove_all=False)

        assert "Unknown AI CLI: nosuch" in str(exc.value)
        read.assert_not_called()

    def test_installing_an_unknown_cli_is_refused_before_the_records(self):
        """Same order as remove: a typo is not reported as a records problem."""
        cmd = SkillsCommand()
        with (
            patch(
                "deepctl_core.skill_generator.get_skills_state",
                return_value={"installed_skills": "not a map"},
            ) as read,
            pytest.raises(click.ClickException) as exc,
        ):
            cmd._handle_install(cli_name="nosuch")

        assert "Unknown AI CLI: nosuch" in str(exc.value)
        read.assert_not_called()

    def test_removing_skills_that_are_not_installed_exits_one(self):
        """`skills remove --cli X` with nothing installed for X exits 1."""
        cmd = SkillsCommand()
        with (
            patch(
                "deepctl_core.skill_generator.get_skills_state",
                return_value={"installed_skills": {"claude": {"paths": []}}},
            ),
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                # A tool deepctl knows, so this is "not installed", not
                # "unknown".
                return_value=[MagicMock(cli_name="cursor")],
            ),
            pytest.raises(click.ClickException) as exc,
        ):
            cmd._handle_remove(cli_name="cursor", remove_all=False)

        assert "No skills installed for 'cursor'" in str(exc.value)


class TestFetchFailuresAreFatal:
    """A partial install is worse than a failed one, so it must exit non-zero."""

    @pytest.mark.parametrize(
        "message",
        [
            "Could not download deepgram/skills@main: no network",
            "deepgram/skills has no ref 'nope' (HTTP 404)",
            "Skill manifest .claude-plugin/marketplace.json is not valid JSON",
        ],
    )
    def test_fetch_error_becomes_a_click_exception(self, message):
        cmd = SkillsCommand()
        with (
            patch(
                "deepctl_core.skill_generator.fetch_repo_skills",
                side_effect=SkillFetchError(message),
            ),
            pytest.raises(click.ClickException) as excinfo,
        ):
            cmd._fetch_skills(None)
        rendered = str(excinfo.value)
        assert message in rendered
        assert "No skills were installed" in rendered

    @pytest.mark.parametrize(
        ("ref_source", "last_line"),
        [
            # The user typed the ref: core's message already says so, so
            # there is nothing to add.
            ("--ref", "No skills were installed."),
            # The env var: how to get out of it, as login and plugin say.
            (
                REF_ENV_VAR,
                f"No skills were installed. Set {REF_ENV_VAR} to another ref, "
                "or unset it to use the pinned release.",
            ),
            # A ref the user did not type: --ref is the way to another.
            (
                PINNED_REF_SOURCE,
                "No skills were installed. Pass --ref to choose another "
                "deepgram/skills ref.",
            ),
            (
                "the last install's record in skills.json",
                "No skills were installed. Pass --ref to choose another "
                "deepgram/skills ref.",
            ),
        ],
    )
    def test_a_missing_ref_ends_without_network_advice(self, ref_source, last_line):
        """A 404 fails the same way on every retry; the network is not why."""
        exc = SkillRefNotFoundError("no-such-ref-zz9", "https://x", ref_source)
        with (
            patch("deepctl_core.skill_generator.fetch_repo_skills", side_effect=exc),
            pytest.raises(click.ClickException) as excinfo,
        ):
            SkillsCommand()._fetch_skills("no-such-ref-zz9", ref_source)
        rendered = str(excinfo.value)
        assert rendered.startswith(str(exc))
        assert rendered.splitlines()[-1] == last_line
        assert "network" not in rendered
        assert "Retry" not in rendered

    @pytest.mark.parametrize("subcommand", ["install", "update"])
    def test_a_missing_env_var_ref_says_how_to_change_it(
        self, monkeypatch, capsys, subcommand
    ):
        """It ended with a bare "No skills were installed." and no way out."""
        import urllib.error

        monkeypatch.setenv(REF_ENV_VAR, "no-such-ref-zz9")
        generator = MagicMock()
        generator.cli_name = "claude"
        generator.display_name = "Claude Code"
        generator.detect.return_value = True
        generator.install_conflicts.return_value = []
        not_found = urllib.error.HTTPError("https://x", 404, "Not Found", {}, None)
        state = {"installed_skills": {"claude": {"paths": [], "skills_ref": "main"}}}
        cmd = SkillsCommand()
        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[generator],
            ),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata",
                return_value=[],
            ),
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch("deepctl_core.skill_generator.save_skills_state") as save,
            patch(
                "deepctl_core.skill_bundle.urllib.request.urlopen",
                side_effect=not_found,
            ),
            pytest.raises(click.ClickException) as excinfo,
        ):
            if subcommand == "install":
                cmd._handle_install(cli_name="claude")
            else:
                cmd._handle_update()

        rendered = str(excinfo.value)
        assert f"which came from {REF_ENV_VAR}." in rendered
        assert rendered.splitlines()[-1] == (
            f"No skills were installed. Set {REF_ENV_VAR} to another ref, "
            "or unset it to use the pinned release."
        )
        assert "Pass --ref" not in rendered
        generator.install_skills.assert_not_called()
        save.assert_not_called()

    def test_a_network_error_keeps_the_network_advice(self):
        exc = SkillFetchError(
            "Could not download deepgram/skills@main from https://x: "
            "<urlopen error [Errno 111] Connection refused>"
        )
        with (
            patch("deepctl_core.skill_generator.fetch_repo_skills", side_effect=exc),
            pytest.raises(click.ClickException) as excinfo,
        ):
            SkillsCommand()._fetch_skills("main")
        assert str(excinfo.value).splitlines()[-1] == (
            "No skills were installed. Retry when the network is available, "
            "or pass --ref to pick another deepgram/skills revision."
        )

    def test_a_ref_passed_in_is_reported_as_from_the_flag(self):
        with patch(
            "deepctl_core.skill_generator.fetch_repo_skills", return_value=[]
        ) as fetch:
            SkillsCommand()._fetch_skills("v9")
            SkillsCommand()._fetch_skills(None)
        assert fetch.call_args_list[0].kwargs["ref_source"] == "--ref"
        # No ref: core works out the env var or the pinned default.
        assert fetch.call_args_list[1].kwargs["ref_source"] is None

    def test_install_does_not_swallow_the_failure(self, tmp_path):
        """`skills install` used to print a notice and exit 0 with no files."""
        cmd = SkillsCommand()
        generator = MagicMock()
        generator.cli_name = "claude"
        generator.display_name = "Claude Code"
        generator.detect.return_value = True
        generator.skills_root.return_value = tmp_path / ".claude" / "skills"

        with (
            patch(
                "deepctl_core.skill_generator.detect_ai_clis", return_value=[generator]
            ),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata", return_value=[]
            ),
            patch(
                "deepctl_core.skill_generator.get_skills_state",
                return_value={"installed_skills": {}},
            ),
            patch("deepctl_core.skill_generator.save_skills_state") as save,
            patch(
                "deepctl_core.skill_generator.fetch_repo_skills",
                side_effect=SkillFetchError("no network"),
            ),
            pytest.raises(click.ClickException),
        ):
            cmd._handle_install(install_all=True)

        generator.install_skills.assert_not_called()
        save.assert_not_called()

    @pytest.mark.parametrize("handler", ["_handle_install", "_handle_setup"])
    def test_an_invalid_ref_fails_before_any_prompt_or_announcement(
        self, handler, capsys
    ):
        """Not after the user has picked tools and seen "Installing..."."""
        cmd = SkillsCommand()
        with (
            patch(
                "deepctl_core.skill_generator.get_skills_state",
                return_value={"installed_skills": {}},
            ),
            patch("deepctl_core.skill_generator.detect_ai_clis") as detect,
            pytest.raises(SkillFetchError, match="Invalid skills ref"),
        ):
            getattr(cmd, handler)(ref="bad ref")

        detect.assert_not_called()
        captured = capsys.readouterr()
        assert "Installing" not in captured.out + captured.err


class TestInstallRecordsWhatItDid:
    def test_state_records_the_ref_and_every_skill(self, tmp_path):
        cmd = SkillsCommand()
        generator = MagicMock()
        generator.cli_name = "claude"
        generator.display_name = "Claude Code"
        generator.detect.return_value = True
        root = tmp_path / ".claude" / "skills"
        generator.skills_root.return_value = root
        generator.install_conflicts.return_value = []
        generator.install_skills.return_value = [root / "api", root / "docs"]

        skills = [
            RepoSkill(name="api", path=tmp_path / "api"),
            RepoSkill(name="docs", path=tmp_path / "docs"),
        ]
        state = {"installed_skills": {}}

        with (
            patch(
                "deepctl_core.skill_generator.detect_ai_clis", return_value=[generator]
            ),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata", return_value=[]
            ),
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch("deepctl_core.skill_generator.save_skills_state"),
            patch(
                "deepctl_core.skill_generator.fetch_repo_skills", return_value=skills
            ),
        ):
            cmd._handle_install(install_all=True)

        entry = state["installed_skills"]["claude"]
        assert entry["skills"] == ["api", "docs"]
        assert entry["skills_ref"] == DEFAULT_SKILLS_REF
        assert [Path(p).name for p in entry["paths"]] == ["api", "docs"]

    def test_a_tool_without_a_skills_directory_gets_the_one_liner(self, capsys):
        cmd = SkillsCommand()
        generator = MagicMock()
        generator.cli_name = "amazonq"
        generator.display_name = "Amazon Q Developer"
        generator.detect.return_value = True
        generator.skills_root.return_value = None
        generator.manual_hint.return_value = (
            "Amazon Q Developer has no documented skills directory. "
            "For the Deepgram skills, run: npx skills add deepgram/skills"
        )
        state = {"installed_skills": {}}

        with (
            patch(
                "deepctl_core.skill_generator.detect_ai_clis", return_value=[generator]
            ),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata", return_value=[]
            ),
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch("deepctl_core.skill_generator.save_skills_state"),
            patch("deepctl_core.skill_generator.fetch_repo_skills") as fetch,
        ):
            cmd._handle_install(install_all=True)

        # Nothing to install means nothing to download.
        fetch.assert_not_called()
        # Advisory output belongs on stderr, so stdout stays parseable.
        captured = capsys.readouterr()
        assert "npx skills add deepgram/skills" in " ".join(captured.err.split())
        assert captured.out == ""
        assert state["installed_skills"] == {}


class TestTheCommandTouchesOnlyWhatItInstalled:
    """`dg skills` shares these directories with the user and other publishers."""

    def _generator(self, tmp_path, cli_name="claude"):
        generator = MagicMock()
        generator.cli_name = cli_name
        generator.display_name = "Claude Code"
        generator.detect.return_value = True
        root = tmp_path / ".claude" / "skills"
        generator.skills_root.return_value = root
        generator.install_conflicts.return_value = []
        generator.install_skills.return_value = [root / "api"]
        generator.prune_retired_result.return_value = skill_generator.PruneResult()
        generator.installed_skill_paths.return_value = []
        generator.remove_report.return_value = skill_generator.RemoveReport()
        return generator, root

    def test_install_refuses_an_unowned_collision_and_writes_nothing(self, tmp_path):
        cmd = SkillsCommand()
        generator, root = self._generator(tmp_path)
        generator.install_conflicts.return_value = [root / "api"]
        skills = [RepoSkill(name="api", path=tmp_path / "api")]
        state = {"installed_skills": {}}

        with (
            patch(
                "deepctl_core.skill_generator.detect_ai_clis", return_value=[generator]
            ),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata", return_value=[]
            ),
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch("deepctl_core.skill_generator.save_skills_state") as save,
            patch(
                "deepctl_core.skill_generator.fetch_repo_skills", return_value=skills
            ),
            pytest.raises(click.ClickException) as excinfo,
        ):
            cmd._handle_install(install_all=True)

        rendered = str(excinfo.value)
        assert "Refusing to overwrite" in rendered
        assert str(root / "api") in rendered
        assert "Claude Code" in rendered
        generator.install_skills.assert_not_called()
        save.assert_not_called()
        assert state["installed_skills"] == {}

    def test_install_hands_the_recorded_paths_to_the_generator(self, tmp_path):
        cmd = SkillsCommand()
        generator, root = self._generator(tmp_path)
        recorded = [str(root / "api")]
        state = {"installed_skills": {"claude": {"paths": list(recorded)}}}
        skills = [RepoSkill(name="api", path=tmp_path / "api")]

        with (
            patch(
                "deepctl_core.skill_generator.detect_ai_clis", return_value=[generator]
            ),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata", return_value=[]
            ),
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch("deepctl_core.skill_generator.save_skills_state"),
            patch(
                "deepctl_core.skill_generator.fetch_repo_skills", return_value=skills
            ),
        ):
            cmd._handle_install(install_all=True)

        generator.install_conflicts.assert_called_once_with(skills, recorded)
        generator.install_skills.assert_called_once_with(skills, recorded)

    def test_remove_hands_the_recorded_paths_to_the_generator(self, tmp_path):
        cmd = SkillsCommand()
        generator, root = self._generator(tmp_path)
        recorded = [str(root / "api"), str(root / "docs")]
        # Everything it owned is gone, so the record has nothing left to
        # describe. Only then may the tool's entry go.
        generator.owned_skill_paths.return_value = [root / "api", root / "docs"]
        state = {"installed_skills": {"claude": {"paths": list(recorded)}}}

        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[generator],
            ),
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch("deepctl_core.skill_generator.save_skills_state"),
        ):
            cmd._handle_remove(remove_all=True)

        generator.remove_report.assert_called_once_with(recorded)
        assert state["installed_skills"] == {}

    def test_remove_with_no_record_deletes_nothing(self, capsys):
        """Deleting skills.json leaves deepctl nothing it can prove it owns."""
        cmd = SkillsCommand()
        generator = MagicMock()
        generator.cli_name = "claude"

        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[generator],
            ),
            patch(
                "deepctl_core.skill_generator.get_skills_state",
                return_value={"installed_skills": {}},
            ),
            patch("deepctl_core.skill_generator.save_skills_state") as save,
        ):
            cmd._handle_remove(remove_all=True)

        generator.remove_report.assert_not_called()
        save.assert_not_called()
        captured = capsys.readouterr()
        assert "by hand" in " ".join((captured.out + captured.err).split())

    def test_a_failed_deletion_keeps_the_record_and_can_be_retried(
        self, tmp_path, capsys
    ):
        """Ownership has to outlive an rmtree that could not delete.

        `SkillGenerator.remove_report()` asks the filesystem before reporting a
        folder gone, but the command used to drop the tool's whole
        skills.json entry regardless. One permission error or read-only
        mount then stranded Deepgram's own folders: the next update
        refuses to overwrite what deepctl cannot prove is its, and the
        next remove has no record left to act on.
        """
        from deepctl_core.skill_generator import ClaudeCodeGenerator

        cmd = SkillsCommand()
        gen = ClaudeCodeGenerator()
        root = tmp_path / ".claude" / "skills"
        (root / "api").mkdir(parents=True)
        (root / "api" / "SKILL.md").write_text("---\nname: api\n---\n")
        state = {
            "installed_skills": {
                "claude": {"paths": [str(root / "api")], "skills": ["api"]}
            }
        }

        def denied(path, ignore_errors=False, **kwargs):
            """What rmtree(ignore_errors=True) does on a read-only mount."""
            if not ignore_errors:
                raise PermissionError(13, "Permission denied", str(path))

        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
            patch(
                "deepctl_core.skill_generator.get_all_generators", return_value=[gen]
            ),
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch("deepctl_core.skill_generator.save_skills_state"),
            patch("deepctl_core.skill_generator.shutil.rmtree", denied),
            # A remove that could not remove is a failed command: the
            # README documents exit 1 for that, and exiting 0 is how the
            # user would never learn the folders are still there.
            pytest.raises(click.ClickException) as excinfo,
        ):
            cmd._handle_remove(remove_all=True)

        assert "could not be removed" in str(excinfo.value)
        assert (root / "api" / "SKILL.md").is_file()
        assert state["installed_skills"]["claude"]["paths"] == [str(root / "api")]
        assert "could not" in " ".join(capsys.readouterr().err.split())

        # Retried once the permission is fixed, and now the record goes.
        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
            patch(
                "deepctl_core.skill_generator.get_all_generators", return_value=[gen]
            ),
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch("deepctl_core.skill_generator.save_skills_state"),
        ):
            cmd._handle_remove(cli_name="claude")

        assert not (root / "api").exists()
        assert state["installed_skills"] == {}

    @staticmethod
    def _refuse_unlink(monkeypatch, targets):
        """What a 0555 folder does to deleting the files in it, on any OS."""
        real = Path.unlink

        def refuse(self, *args, **kwargs):
            if self in targets:
                raise PermissionError(13, "Permission denied", str(self))
            return real(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", refuse)
        return lambda: monkeypatch.setattr(Path, "unlink", real)

    def _remove(self, gen, state, **kwargs):
        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators", return_value=[gen]
            ),
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch("deepctl_core.skill_generator.save_skills_state"),
        ):
            SkillsCommand()._handle_remove(**kwargs)

    def test_a_locked_legacy_file_gets_one_warning_and_can_be_retried(
        self, monkeypatch, capsys
    ):
        """The user follows the advice, re-runs the command, and it works.

        A deepctl <= 0.3.0 file that could not be deleted was reported as
        left in place, then again as "no longer deepctl's to delete",
        then the tool as "nothing left to remove" -- and the record was
        dropped, so the retry the first warning asked for found nothing.
        """
        from deepctl_core.skill_generator import CursorGenerator

        gen = CursorGenerator()
        rules = Path.home() / ".cursor" / "rules" / "deepctl.mdc"
        rules.parent.mkdir(parents=True)
        rules.write_text("---\nname: api\n---\n\n# api\n")
        state = {"installed_skills": {"cursor": {"paths": [str(rules)]}}}
        allow = self._refuse_unlink(monkeypatch, {rules})

        with pytest.raises(click.ClickException) as excinfo:
            self._remove(gen, state, remove_all=True)

        rendered = " ".join(capsys.readouterr().err.split())
        assert rendered.count(str(rules)) == 1
        assert "could not be deleted: Permission denied" in rendered
        assert "no longer deepctl's" not in rendered
        assert "nothing left to remove" not in rendered
        assert "dg skills remove --cli cursor" in rendered
        assert "could not be removed" in str(excinfo.value)
        assert rules.is_file()
        assert state["installed_skills"]["cursor"]["paths"] == [str(rules)]

        allow()
        self._remove(gen, state, cli_name="cursor")

        assert not rules.exists()
        assert state["installed_skills"] == {}
        captured = capsys.readouterr()
        assert f"Removed {rules}" in " ".join((captured.out + captured.err).split())

    def test_locked_legacy_command_files_keep_the_record_too(self, monkeypatch, capsys):
        from deepctl_core.skill_generator import ClaudeCodeGenerator

        gen = ClaudeCodeGenerator()
        commands = Path.home() / ".claude" / "commands" / "deepgram"
        commands.mkdir(parents=True)
        files = []
        for name in ("api", "docs", "setup-mcp", "starters"):
            (commands / f"{name}.md").write_text(f"---\nname: {name}\n---\n")
            files.append(commands / f"{name}.md")
        state = {"installed_skills": {"claude": {"paths": [str(f) for f in files]}}}
        allow = self._refuse_unlink(monkeypatch, set(files))

        with pytest.raises(click.ClickException):
            self._remove(gen, state, remove_all=True)

        rendered = " ".join(capsys.readouterr().err.split())
        for f in files:
            assert rendered.count(str(f)) == 1
        assert "no longer deepctl's" not in rendered
        assert "nothing left to remove" not in rendered
        assert state["installed_skills"]["claude"]["paths"] == [str(f) for f in files]

        allow()
        self._remove(gen, state, remove_all=True)

        assert not commands.exists()
        assert state["installed_skills"] == {}

    def test_a_foreign_file_at_a_legacy_path_is_warned_once_and_dropped(self, capsys):
        """Not deepctl's, so there is nothing to retry: exit 0, record goes."""
        from deepctl_core.skill_generator import CursorGenerator

        rules = Path.home() / ".cursor" / "rules" / "deepctl.mdc"
        rules.parent.mkdir(parents=True)
        rules.write_text("my own rules\n")
        state = {"installed_skills": {"cursor": {"paths": [str(rules)]}}}

        self._remove(CursorGenerator(), state, remove_all=True)

        rendered = " ".join(capsys.readouterr().err.split())
        assert rendered.count(str(rules)) == 1
        assert "nothing left to remove" not in rendered
        assert rules.read_text() == "my own rules\n"
        assert state["installed_skills"] == {}

    def test_remove_drops_the_record_for_a_cli_it_no_longer_supports(
        self, tmp_path, capsys
    ):
        """No generator means no root to check a path against.

        deepctl cannot prove anything about those folders, so the record
        is the only thing it can honestly drop -- and it has to say so
        rather than let the user think something was deleted.
        """
        cmd = SkillsCommand()
        state = {"installed_skills": {"retired-tool": {"paths": ["/somewhere/api"]}}}

        with (
            patch("deepctl_core.skill_generator.get_all_generators", return_value=[]),
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch("deepctl_core.skill_generator.save_skills_state") as save,
        ):
            cmd._handle_remove(remove_all=True)

        assert state["installed_skills"] == {}
        save.assert_called_once()
        rendered = " ".join(capsys.readouterr().err.split())
        assert "retired-tool" in rendered
        assert "by hand" in rendered

    def test_remove_reports_a_recorded_path_it_may_no_longer_touch(
        self, tmp_path, capsys
    ):
        """A symlink where a skill folder was is nobody's to delete.

        Dropping the record without a word would leave it in a shared
        directory with deepctl no longer able to name it, and following
        it would delete whatever it points at.
        """
        from deepctl_core.skill_generator import ClaudeCodeGenerator

        cmd = SkillsCommand()
        gen = ClaudeCodeGenerator()
        root = tmp_path / ".claude" / "skills"
        root.mkdir(parents=True)
        target = tmp_path / "my-own-work"
        target.mkdir()
        (target / "SKILL.md").write_text("---\nname: api\n---\n\nmine\n")
        link = root / "api"
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):  # unprivileged Windows
            pytest.skip("this filesystem does not allow creating symlinks")
        state = {"installed_skills": {"claude": {"paths": [str(link)]}}}

        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
            patch(
                "deepctl_core.skill_generator.get_all_generators", return_value=[gen]
            ),
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch("deepctl_core.skill_generator.save_skills_state"),
        ):
            cmd._handle_remove(remove_all=True)

        assert link.is_symlink()
        assert (target / "SKILL.md").read_text().endswith("mine\n")
        # Rich wraps the path across lines, so compare without whitespace.
        rendered = "".join(capsys.readouterr().err.split())
        assert str(link) in rendered
        assert "byhand" in rendered
        # The record goes, because the path is not deepctl's any more and
        # nothing it can do would ever clear it. README documents this.
        assert state["installed_skills"] == {}

    def test_a_tilde_record_is_not_reported_twice_when_it_cannot_be_removed(
        self, tmp_path, capsys, monkeypatch
    ):
        """`~/x` and its expansion are one path, so they get one verdict.

        A stranded folder is already reported as "could not be removed".
        Matching the raw record against the expanded owned path missed
        it, so the same folder was also reported as one deepctl may no
        longer touch — two contradictory instructions for one path.
        """
        from deepctl_core.skill_generator import ClaudeCodeGenerator

        cmd = SkillsCommand()
        gen = ClaudeCodeGenerator()
        home = tmp_path / "home"
        root = home / ".claude" / "skills"
        (root / "api").mkdir(parents=True)
        (root / "api" / "SKILL.md").write_text("---\nname: api\n---\n")
        state = {"installed_skills": {"claude": {"paths": ["~/.claude/skills/api"]}}}

        def denied(path, ignore_errors=False, **kwargs):
            if not ignore_errors:
                raise PermissionError(13, "Permission denied", str(path))

        # expanduser() reads $HOME, not Path.home, so both are pointed at
        # the throwaway directory -- otherwise this resolves against the
        # developer's own home.
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))
        with (
            patch.object(Path, "home", staticmethod(lambda: home)),
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
            patch(
                "deepctl_core.skill_generator.get_all_generators", return_value=[gen]
            ),
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch("deepctl_core.skill_generator.save_skills_state"),
            patch("deepctl_core.skill_generator.shutil.rmtree", denied),
            pytest.raises(click.ClickException),
        ):
            cmd._handle_remove(remove_all=True)

        rendered = " ".join(capsys.readouterr().err.split())
        assert "could not be removed" in rendered
        assert "no longer deepctl's" not in rendered
        # Still recorded, so the retry the message asks for can find it.
        assert state["installed_skills"]["claude"]["paths"] == [str(root / "api")]

    def test_remove_without_all_or_cli_is_a_usage_error(self):
        """Forgetting a flag must not look like a successful removal."""
        cmd = SkillsCommand()
        with (
            patch(
                "deepctl_core.skill_generator.get_skills_state",
                return_value={"installed_skills": {"claude": {"paths": []}}},
            ),
            patch("deepctl_core.skill_generator.get_all_generators", return_value=[]),
            patch("deepctl_core.skill_generator.save_skills_state") as save,
            pytest.raises(click.UsageError) as excinfo,
        ):
            cmd._handle_remove()

        assert "--all" in str(excinfo.value)
        save.assert_not_called()

    def test_remove_survives_a_hand_edited_state_file(self, capsys):
        """A list where a dict belongs must not raise AttributeError."""
        cmd = SkillsCommand()
        with (
            patch(
                "deepctl_core.skill_generator.get_skills_state",
                return_value={"installed_skills": ["claude"]},
            ),
            patch("deepctl_core.skill_generator.get_all_generators", return_value=[]),
            patch("deepctl_core.skill_generator.save_skills_state") as save,
            pytest.raises(click.ClickException) as excinfo,
        ):
            cmd._handle_remove(remove_all=True)

        save.assert_not_called()
        assert "by hand" in " ".join(str(excinfo.value).split())

    def test_remove_does_not_claim_it_deleted_a_path_that_survived(
        self, tmp_path, capsys
    ):
        """clean_legacy leaves the user's half of a shared path behind.

        A context file keeps their own text, and the legacy command
        directory keeps a command they added. Both are still on disk
        afterwards, so a bare "Removed" points at something they can
        still see -- and counted towards "Removed N folder(s)".
        """
        cmd = SkillsCommand()
        generator, _root = self._generator(tmp_path)
        shared = tmp_path / "GEMINI.md"
        shared.write_text("my own notes\n")
        generator.remove_report.return_value = skill_generator.RemoveReport(
            removed=[shared]
        )
        generator.owned_skill_paths.return_value = []
        state = {"installed_skills": {"claude": {"paths": []}}}

        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[generator],
            ),
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch("deepctl_core.skill_generator.save_skills_state"),
        ):
            cmd._handle_remove(remove_all=True)

        combined = " ".join((lambda c: c.out + c.err)(capsys.readouterr()).split())
        assert "Removed deepctl's content from" in combined
        assert "Removed 1 " not in combined
        # ...and does not then deny it. The path is excluded from the
        # folder count because the folder is still there, which is not
        # the same as deepctl having done nothing to it.
        assert "Nothing was removed" not in combined
        assert shared.read_text() == "my own notes\n"

    def test_update_reports_the_tools_it_refreshed(self, tmp_path, capsys):
        cmd = SkillsCommand()
        generator, root = self._generator(tmp_path)
        skills = [RepoSkill(name="api", path=tmp_path / "api")]
        state = {"installed_skills": {"claude": {"paths": [str(root / "api")]}}}

        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[generator],
            ),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata", return_value=[]
            ),
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch("deepctl_core.skill_generator.save_skills_state"),
            patch(
                "deepctl_core.skill_generator.fetch_repo_skills", return_value=skills
            ),
        ):
            cmd._handle_update()

        assert state["installed_skills"]["claude"]["skills"] == ["api"]
        rendered = " ".join(capsys.readouterr().err.split())
        assert "Updated 1 tool(s)" in rendered

    def test_update_skips_a_cli_it_no_longer_supports(self, capsys):
        """An unknown key must not take the whole update down."""
        cmd = SkillsCommand()
        state = {"installed_skills": {"retired-tool": {"paths": []}}}

        with (
            patch("deepctl_core.skill_generator.get_all_generators", return_value=[]),
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch("deepctl_core.skill_generator.save_skills_state") as save,
            patch("deepctl_core.skill_generator.fetch_repo_skills") as fetch,
        ):
            cmd._handle_update()

        fetch.assert_not_called()
        save.assert_not_called()
        rendered = " ".join(capsys.readouterr().err.split())
        assert "retired-tool" in rendered
        assert "Nothing to update" in rendered

    def test_update_retires_a_tool_it_cannot_install_to(self, capsys):
        """The warning has to stop, and only dropping the record stops it.

        Filtering these out before the shared installer meant `update`
        never reached the code that cleans up their deepctl <= 0.3.0
        files and drops the record, so it reprinted the same hint on
        every run with nothing on that path that would ever resolve it.
        """
        cmd = SkillsCommand()
        generator = MagicMock()
        generator.cli_name = "amazonq"
        generator.display_name = "Amazon Q Developer"
        generator.skills_root.return_value = None
        generator.manual_hint.return_value = "Amazon Q Developer has no ..."
        state = {"installed_skills": {"amazonq": {"paths": []}}}

        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[generator],
            ),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata", return_value=[]
            ),
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch("deepctl_core.skill_generator.save_skills_state"),
            patch("deepctl_core.skill_generator.fetch_repo_skills") as fetch,
        ):
            cmd._handle_update()

        generator.clean_legacy_report.assert_called_once()
        assert state["installed_skills"] == {}
        # Nothing to install into means nothing to download.
        fetch.assert_not_called()
        rendered = " ".join(capsys.readouterr().err.split())
        assert "Amazon Q Developer has no" in rendered
        # ...and it does not then claim a tool was refreshed.
        assert "Updated" not in rendered

    def test_list_shows_the_ref_and_count_it_recorded(self, tmp_path, capsys):
        """`dg skills list` is how a user checks which revision they have."""
        cmd = SkillsCommand()
        root = tmp_path / ".claude" / "skills"
        state = {
            "installed_skills": {
                "claude": {
                    "paths": [str(root / "api"), str(root / "docs")],
                    "version": "0.4.0",
                    "skills_ref": "deepgram-skills-v1.6.0",
                    "skills": ["api", "docs"],
                }
            }
        }

        with patch("deepctl_core.skill_generator.get_skills_state", return_value=state):
            cmd._handle_list()

        rendered = "".join(capsys.readouterr().out.split())
        assert "deepgram-skills-v1.6.0" in rendered
        assert "0.4.0" in rendered
        assert "claude" in rendered

    def test_list_says_so_when_nothing_is_installed(self, capsys):
        cmd = SkillsCommand()
        with patch(
            "deepctl_core.skill_generator.get_skills_state",
            return_value={"installed_skills": {}},
        ):
            cmd._handle_list()

        combined = capsys.readouterr()
        text = " ".join((combined.out + combined.err).split())
        assert "No skills installed. Run 'dg skills install' to get started." in text
        assert "deepctl skills install" not in text

    def test_setup_without_a_tty_installs_for_everything_detected(self, tmp_path):
        """CI has no prompt to answer, so setup must not wait for one."""
        cmd = SkillsCommand()
        cmd._guided = False
        generator, _root = self._generator(tmp_path)
        skills = [RepoSkill(name="api", path=tmp_path / "api")]
        state = {"installed_skills": {}}

        with (
            patch("sys.stdout") as stdout,
            patch(
                "deepctl_core.skill_generator.detect_ai_clis", return_value=[generator]
            ),
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[generator],
            ),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata", return_value=[]
            ),
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch("deepctl_core.skill_generator.save_skills_state"),
            patch(
                "deepctl_core.skill_generator.fetch_repo_skills", return_value=skills
            ),
        ):
            stdout.isatty.return_value = False
            cmd._handle_setup()

        generator.install_skills.assert_called_once()
        assert state["installed_skills"]["claude"]["skills"] == ["api"]

    def test_status_asks_the_generator_only_for_recorded_folders(
        self, tmp_path, capsys
    ):
        cmd = SkillsCommand()
        generator, root = self._generator(tmp_path)
        recorded = [str(root / "api")]
        state = {"installed_skills": {"claude": {"paths": list(recorded)}}}
        generator.installed_skill_paths.return_value = [root / "api"]

        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[generator],
            ),
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
        ):
            cmd._handle_status()

        generator.installed_skill_paths.assert_called_once_with(recorded)
        # And the table shows that count, not a directory listing. Read
        # out of the row rather than searched for in the whole screen:
        # the skills directory is a tmp_path, and a "1" anywhere in it
        # would pass for a skill count even when the cell reads "No".
        row = next(
            line
            for line in capsys.readouterr().out.splitlines()
            if "Claude Code" in line
        )
        assert [c.strip() for c in row.split("│")][3] == "1"


class TestARecordsFileThatIsNotRecords:
    """`installed_skills` holding something other than a map of tools.

    The README tells users to drop an entry from skills.json by hand, so
    the file does get edited. Every handler here iterates that value, and
    main.py turns the resulting attribute error into "Error: 'list'
    object has no attribute 'keys'" -- a message that names a Python type
    instead of the file to fix. Each one has to name the file instead.
    """

    BROKEN: ClassVar[list[object]] = [["claude"], "claude", 7, None]

    def _generator(self):
        generator = MagicMock()
        generator.cli_name = "claude"
        generator.display_name = "Claude Code"
        generator.detect.return_value = True
        generator.skills_root.return_value = Path("/nowhere/.claude/skills")
        generator.installed_skill_paths.return_value = []
        return generator

    @staticmethod
    def _said(capsys):
        captured = capsys.readouterr()
        return " ".join((captured.out + captured.err).split())

    @pytest.mark.parametrize("broken", BROKEN)
    def test_status_still_prints_the_table_and_names_the_file(self, broken, capsys):
        """Status is how a user finds their tools, so it must still run."""
        cmd = SkillsCommand()
        generator = self._generator()

        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[generator],
            ),
            patch(
                "deepctl_core.skill_generator.get_skills_state",
                return_value={"installed_skills": broken},
            ),
        ):
            cmd._handle_status()

        said = self._said(capsys)
        assert "Claude Code" in said
        assert "cannot read its own records" in said

    @pytest.mark.parametrize("broken", BROKEN)
    def test_status_counts_read_as_unknown_and_the_command_exits_one(
        self, broken, json_output
    ):
        """What it can show, it shows; but it is still a failed command."""
        cmd = SkillsCommand()
        generator = self._generator()
        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[generator],
            ),
            patch(
                "deepctl_core.skill_generator.get_skills_state",
                return_value={"installed_skills": broken},
            ),
        ):
            result = CliRunner().invoke(cmd.get_click_group(), ["status"], obj={})

        assert result.exit_code == 1, result.output
        payload = json.loads(result.stdout)
        assert payload["status"] == "error"
        assert "cannot read its own records" in payload["message"]
        assert payload["tools"][0]["display_name"] == "Claude Code"
        assert payload["tools"][0]["installed"] is None
        generator.installed_skill_paths.assert_not_called()

    @pytest.mark.parametrize("broken", BROKEN)
    def test_status_table_shows_the_count_as_unknown(self, broken, capsys):
        cmd = SkillsCommand()
        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[self._generator()],
            ),
            patch(
                "deepctl_core.skill_generator.get_skills_state",
                return_value={"installed_skills": broken},
            ),
        ):
            cmd._handle_status()

        row = next(
            line
            for line in capsys.readouterr().out.splitlines()
            if "Claude Code" in line
        )
        assert [c.strip() for c in row.split("│")][3] == "?"

    @pytest.mark.parametrize("broken", BROKEN)
    def test_list_exits_one_with_an_error_payload(self, broken, json_output):
        cmd = SkillsCommand()
        with patch(
            "deepctl_core.skill_generator.get_skills_state",
            return_value={"installed_skills": broken},
        ):
            result = CliRunner().invoke(cmd.get_click_group(), ["list"], obj={})

        assert result.exit_code == 1, result.output
        payload = json.loads(result.stdout)
        assert payload["status"] == "error"
        assert payload["installed"] == []
        assert "cannot read its own records" in payload["message"]

    @pytest.mark.parametrize("broken", BROKEN)
    def test_list_names_the_file(self, broken, capsys):
        cmd = SkillsCommand()
        with patch(
            "deepctl_core.skill_generator.get_skills_state",
            return_value={"installed_skills": broken},
        ):
            cmd._handle_list()

        assert "cannot read its own records" in self._said(capsys)

    @pytest.mark.parametrize("broken", BROKEN)
    def test_update_names_the_file_and_downloads_nothing(self, broken, capsys):
        cmd = SkillsCommand()
        generator = self._generator()

        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[generator],
            ),
            patch(
                "deepctl_core.skill_generator.get_skills_state",
                return_value={"installed_skills": broken},
            ),
            patch("deepctl_core.skill_generator.fetch_repo_skills") as fetch,
            patch("deepctl_core.skill_generator.save_skills_state") as save,
            pytest.raises(click.ClickException) as excinfo,
        ):
            cmd._handle_update()

        assert "cannot read its own records" in str(excinfo.value)
        assert str(skill_generator._STATE_FILE) in str(excinfo.value)
        fetch.assert_not_called()
        save.assert_not_called()

    @pytest.mark.parametrize("broken", BROKEN)
    def test_remove_names_the_file_and_deletes_nothing(self, broken, capsys):
        cmd = SkillsCommand()
        generator = self._generator()

        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[generator],
            ),
            patch(
                "deepctl_core.skill_generator.get_skills_state",
                return_value={"installed_skills": broken},
            ),
            patch("deepctl_core.skill_generator.save_skills_state") as save,
            pytest.raises(click.ClickException) as excinfo,
        ):
            cmd._handle_remove(remove_all=True)

        assert "cannot read its own records" in str(excinfo.value)
        assert str(skill_generator._STATE_FILE) in str(excinfo.value)
        generator.remove_report.assert_not_called()
        save.assert_not_called()

    @pytest.mark.parametrize("broken", BROKEN)
    @pytest.mark.parametrize(
        "argv",
        [
            ["install", "--all"],
            ["update"],
            ["remove", "--all"],
            ["status"],
            ["list"],
        ],
        ids=["install", "update", "remove", "status", "list"],
    )
    def test_every_subcommand_exits_one_and_leaves_the_file_alone(self, broken, argv):
        """The real file on disk, read by core, through the click group.

        `update` and `remove --all` printed INFO and exited 0 for these,
        and `install` replaced the file with fresh records and exited 0.
        """
        state_file = Path(skill_generator._STATE_FILE)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        before = json.dumps({"installed_skills": broken, "auto_update": True})
        state_file.write_text(before)
        cmd = SkillsCommand()

        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[self._generator()],
            ),
            patch(
                "deepctl_core.skill_generator.detect_ai_clis",
                return_value=[self._generator()],
            ),
            patch("deepctl_core.skill_generator.fetch_repo_skills") as fetch,
        ):
            result = CliRunner().invoke(cmd.get_click_group(), argv, obj={})

        assert result.exit_code == 1, result.output
        assert state_file.read_text() == before
        fetch.assert_not_called()

    def test_a_file_with_no_records_key_is_nothing_installed(self, capsys):
        """`{}` is an empty record, not a damaged one."""
        cmd = SkillsCommand()
        with patch(
            "deepctl_core.skill_generator.get_skills_state",
            return_value={"auto_update": True},
        ):
            result = cmd._handle_list()

        assert result.status == "success"


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
        import sys
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


class TestStdoutCarriesOnlyTheResult:
    """`-o json` must yield one JSON document; default mode, the table.

    `status` and `list` printed a Rich table through a stdout console and
    returned nothing, so `dg -o json skills status | jq` got a table. Each
    now returns a result the framework serialises, and every human line
    goes to stderr.
    """

    def _generator(self, root):
        generator = MagicMock()
        generator.cli_name = "claude"
        generator.display_name = "Claude Code"
        generator.detect.return_value = True
        generator.skills_root.return_value = root
        generator.installed_skill_paths.return_value = [root / "api"]
        return generator

    def _status_state(self, root):
        return {
            "installed_skills": {
                "claude": {
                    "paths": [str(root / "api")],
                    "skills": ["api"],
                    "skills_ref": "main",
                    "version": "0.4.0",
                }
            }
        }

    def _invoke(self, args, generators, state):
        cmd = SkillsCommand()
        runner = CliRunner()
        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=generators,
            ),
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
        ):
            return runner.invoke(cmd.get_click_group(), args, obj={})

    def test_status_in_json_mode_is_one_json_document(self, tmp_path, json_output):
        root = tmp_path / ".claude" / "skills"
        result = self._invoke(
            ["status"], [self._generator(root)], self._status_state(root)
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["status"] == "success"
        assert payload["tools"] == [
            {
                "cli": "claude",
                "display_name": "Claude Code",
                "detected": True,
                "installed": 1,
                "skills_ref": "main",
                "skills_directory": str(root),
                "remove_pending": False,
            }
        ]
        # The table went nowhere, and the hints went to stderr.
        assert "AI Coding Assistant Status" not in result.output

    def test_list_in_json_mode_is_one_json_document(self, tmp_path, json_output):
        root = tmp_path / ".claude" / "skills"
        result = self._invoke(["list"], [], self._status_state(root))

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["installed"] == [
            {
                "cli": "claude",
                "deepctl_version": "0.4.0",
                "skills_ref": "main",
                "skills": ["api"],
                "count": 1,
                "location": str(root),
                "remove_pending": False,
            }
        ]
        # The follow-up hint is for a person, so it is not in the document.
        assert "Run 'dg skills update'" not in result.stdout
        assert "Run 'dg skills update'" in result.stderr

    def test_list_in_json_mode_with_nothing_installed_is_still_a_document(
        self, json_output
    ):
        result = self._invoke(["list"], [], {"installed_skills": {}})

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["installed"] == []
        assert payload["message"] == (
            "No skills installed. Run 'dg skills install' to get started."
        )

    def test_status_in_default_mode_prints_the_table_to_stdout(self, tmp_path):
        root = tmp_path / ".claude" / "skills"
        result = self._invoke(
            ["status"], [self._generator(root)], self._status_state(root)
        )

        assert result.exit_code == 0, result.output
        assert "AI Coding Assistant Status" in result.stdout
        assert "Claude Code" in result.stdout
        with pytest.raises(json.JSONDecodeError):
            json.loads(result.stdout)

    def test_status_shows_the_installed_ref(self, tmp_path):
        """A user checking which revision they have should not need `list`."""
        root = tmp_path / ".claude" / "skills"
        result = self._invoke(
            ["status"], [self._generator(root)], self._status_state(root)
        )

        row = next(line for line in result.stdout.splitlines() if "Claude Code" in line)
        cells = [c.strip() for c in row.split("│")]
        assert cells[3] == "1"
        assert cells[4] == "main"

    def test_list_in_default_mode_prints_the_table_to_stdout(self, tmp_path):
        root = tmp_path / ".claude" / "skills"
        result = self._invoke(["list"], [], self._status_state(root))

        assert result.exit_code == 0, result.output
        assert "Installed Skills" in result.stdout
        assert "main" in result.stdout

    def test_setup_keeps_stdout_empty(self, tmp_path, capsys):
        """The 'Installing...' banner is progress, so it belongs on stderr."""
        cmd = SkillsCommand()
        cmd._guided = False
        root = tmp_path / ".claude" / "skills"
        generator = self._generator(root)
        generator.install_conflicts.return_value = []
        generator.install_skills.return_value = [root / "api"]
        generator.prune_retired_result.return_value = skill_generator.PruneResult()
        skills = [RepoSkill(name="api", path=tmp_path / "api")]

        with (
            patch("sys.stdout") as stdout,
            patch(
                "deepctl_core.skill_generator.detect_ai_clis", return_value=[generator]
            ),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata", return_value=[]
            ),
            patch(
                "deepctl_core.skill_generator.get_skills_state",
                return_value={"installed_skills": {}},
            ),
            patch("deepctl_core.skill_generator.save_skills_state"),
            patch(
                "deepctl_core.skill_generator.fetch_repo_skills", return_value=skills
            ),
        ):
            stdout.isatty.return_value = False
            cmd._handle_setup()

        # `patch("sys.stdout")` swallowed anything written to the real
        # stdout, so prove the banner went to stderr instead.
        err = " ".join(capsys.readouterr().err.split())
        assert "Installing Deepgram skills" in err
        assert "Setup complete" in err
        stdout.write.assert_not_called()


class TestQuietSetup:
    """`dg -q skills setup --all` is silent, like every other subcommand."""

    def test_quiet_setup_all_prints_nothing(self, tmp_path, capsys):
        root = tmp_path / ".claude" / "skills"
        generator = MagicMock()
        generator.cli_name = "claude"
        generator.display_name = "Claude Code"
        generator.skills_root.return_value = root
        generator.install_conflicts.return_value = []
        generator.install_skills.return_value = [root / "api"]
        generator.prune_retired_result.return_value = skill_generator.PruneResult()
        skills = [RepoSkill(name="api", path=tmp_path / "api")]
        cmd = SkillsCommand()
        cmd._guided = False
        console = output.get_console()
        was_quiet = console.quiet
        console.quiet = True
        try:
            with (
                patch(
                    "deepctl_core.skill_generator.detect_ai_clis",
                    return_value=[generator],
                ),
                patch(
                    "deepctl_core.skill_generator.collect_command_metadata",
                    return_value=[],
                ),
                patch(
                    "deepctl_core.skill_generator.get_skills_state",
                    return_value={"installed_skills": {}},
                ),
                patch("deepctl_core.skill_generator.save_skills_state"),
                patch(
                    "deepctl_core.skill_generator.fetch_repo_skills",
                    return_value=skills,
                ),
            ):
                cmd._handle_setup(install_all=True)
        finally:
            console.quiet = was_quiet

        generator.install_skills.assert_called_once()
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""


class TestUpdateFollowsTheRecordedRef:
    """`install --ref main` then `update` must stay on main.

    `update` resolved its ref through the same chain as `install` (flag,
    env var, pinned default), so a bare `update` silently switched a user
    who had installed from a branch back to the pinned tag.
    """

    def _generator(self, tmp_path):
        root = tmp_path / ".claude" / "skills"
        generator = MagicMock()
        generator.cli_name = "claude"
        generator.display_name = "Claude Code"
        generator.detect.return_value = True
        generator.skills_root.return_value = root
        generator.install_conflicts.return_value = []
        generator.install_skills.return_value = [root / "api"]
        generator.prune_retired_result.return_value = skill_generator.PruneResult()
        return generator, root

    def _update(self, tmp_path, state, generators=None, ref=None):
        cmd = SkillsCommand()
        if generators is None:
            generator, _root = self._generator(tmp_path)
            generators = [generator]
        skills = [RepoSkill(name="api", path=tmp_path / "api")]
        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=generators,
            ),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata", return_value=[]
            ),
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch("deepctl_core.skill_generator.save_skills_state"),
            patch(
                "deepctl_core.skill_generator.fetch_repo_skills", return_value=skills
            ) as fetch,
        ):
            cmd._handle_update(ref=ref)
        return fetch

    def _state(self, root, ref="main"):
        return {
            "installed_skills": {
                "claude": {"paths": [str(root / "api")], "skills_ref": ref}
            }
        }

    def test_update_defaults_to_the_recorded_ref(self, tmp_path, capsys):
        _generator, root = self._generator(tmp_path)
        state = self._state(root, "main")

        fetch = self._update(tmp_path, state)

        fetch.assert_called_once_with(
            "main",
            force=True,
            ref_source=f"the last install's record in {skills_command._state_file()}",
        )
        assert state["installed_skills"]["claude"]["skills_ref"] == "main"
        err = " ".join(capsys.readouterr().err.split())
        assert "Updating to deepgram/skills@main" in err
        assert "recorded in" in err
        assert "skills.json" in err
        assert "Updated 1 tool(s) to deepgram/skills@main" in err

    def test_the_flag_beats_the_recorded_ref(self, tmp_path, capsys):
        _generator, root = self._generator(tmp_path)
        state = self._state(root, "main")

        fetch = self._update(tmp_path, state, ref="v9")

        fetch.assert_called_once_with("v9", force=True, ref_source="--ref")
        assert state["installed_skills"]["claude"]["skills_ref"] == "v9"
        err = " ".join(capsys.readouterr().err.split())
        assert "Updating to deepgram/skills@v9 (--ref)" in err

    def test_the_env_var_beats_the_recorded_ref(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv(REF_ENV_VAR, "from-env")
        _generator, root = self._generator(tmp_path)
        state = self._state(root, "main")

        fetch = self._update(tmp_path, state)

        fetch.assert_called_once_with("from-env", force=True, ref_source=REF_ENV_VAR)
        err = " ".join(capsys.readouterr().err.split())
        assert f"Updating to deepgram/skills@from-env ({REF_ENV_VAR})" in err

    def test_the_flag_beats_the_env_var(self, tmp_path, monkeypatch):
        monkeypatch.setenv(REF_ENV_VAR, "from-env")
        _generator, root = self._generator(tmp_path)

        fetch = self._update(tmp_path, self._state(root, "main"), ref="v9")

        fetch.assert_called_once_with("v9", force=True, ref_source="--ref")

    def test_nothing_recorded_falls_back_to_the_pinned_release(self, tmp_path, capsys):
        """A record from before refs were recorded has no `skills_ref`."""
        _generator, root = self._generator(tmp_path)
        state = {"installed_skills": {"claude": {"paths": [str(root / "api")]}}}

        fetch = self._update(tmp_path, state)

        fetch.assert_called_once_with(
            DEFAULT_SKILLS_REF, force=True, ref_source=PINNED_REF_SOURCE
        )
        err = " ".join(capsys.readouterr().err.split())
        assert (
            f"Updating to deepgram/skills@{DEFAULT_SKILLS_REF} (pinned release)" in err
        )

    @pytest.mark.parametrize("source", ["--ref", REF_ENV_VAR])
    def test_an_invalid_ref_is_refused_before_it_is_announced(
        self, tmp_path, monkeypatch, capsys, source
    ):
        """It printed "Updating to deepgram/skills@bad ref" and then failed."""
        _generator, root = self._generator(tmp_path)
        ref = None
        if source == REF_ENV_VAR:
            monkeypatch.setenv(REF_ENV_VAR, "bad ref")
        else:
            ref = "bad ref"

        with pytest.raises(SkillFetchError, match="Invalid skills ref"):
            self._update(tmp_path, self._state(root, "main"), ref=ref)

        err = capsys.readouterr().err
        assert "Updating to" not in err

    def test_disagreeing_records_warn_and_use_the_pinned_release(
        self, tmp_path, capsys
    ):
        """Two tools on two refs: guessing one would silently move the other."""
        claude, claude_root = self._generator(tmp_path)
        cursor = MagicMock()
        cursor.cli_name = "cursor"
        cursor.display_name = "Cursor"
        cursor_root = tmp_path / ".cursor" / "skills"
        cursor.skills_root.return_value = cursor_root
        cursor.install_conflicts.return_value = []
        cursor.install_skills.return_value = [cursor_root / "api"]
        cursor.prune_retired_result.return_value = skill_generator.PruneResult()
        state = {
            "installed_skills": {
                "claude": {"paths": [str(claude_root / "api")], "skills_ref": "main"},
                "cursor": {"paths": [str(cursor_root / "api")], "skills_ref": "v1"},
            }
        }

        fetch = self._update(tmp_path, state, generators=[claude, cursor])

        fetch.assert_called_once_with(
            DEFAULT_SKILLS_REF, force=True, ref_source=PINNED_REF_SOURCE
        )
        err = " ".join(capsys.readouterr().err.split())
        assert "more than one deepgram/skills ref (main, v1)" in err
        assert "Pass --ref" in err

    def test_help_names_the_ref_sources_and_their_order(self):
        cmd = SkillsCommand()
        by_name = {c.name: c for c in cmd.setup_commands()}
        ctx = click.Context(by_name["update"], info_name="update")
        update_help = " ".join(by_name["update"].get_help(ctx).split())
        assert "--ref" in update_help
        assert REF_ENV_VAR in update_help
        assert "recorded in skills.json" in update_help
        assert DEFAULT_SKILLS_REF in update_help
        # The order is the precedence, so it has to read in that order.
        assert (
            update_help.index("--ref, then")
            < update_help.index(REF_ENV_VAR + ", then")
            < update_help.index("recorded in skills.json")
            < update_help.index("pinned release")
        )

        ctx = click.Context(by_name["install"], info_name="install")
        install_help = " ".join(by_name["install"].get_help(ctx).split())
        assert REF_ENV_VAR in install_help
        assert "pinned release" in install_help
        assert "recorded" not in install_help


class TestASkillsFileCoreCannotRead:
    """A corrupt `skills.json` is a failed command that names the file.

    Core raises `SkillsStateError` for this instead of quietly resetting
    the records.
    """

    @pytest.fixture(autouse=True)
    def _core_raises(self):
        with patch(
            "deepctl_core.skill_generator.get_skills_state",
            side_effect=skill_generator.SkillsStateError(
                "skills.json: Expecting value: line 1"
            ),
        ):
            yield

    @pytest.mark.parametrize(
        ("handler", "kwargs"),
        [
            ("_handle_update", {}),
            ("_handle_remove", {"remove_all": True}),
            ("_handle_install", {"install_all": True}),
            ("_handle_setup", {"install_all": True}),
        ],
    )
    def test_each_subcommand_exits_one_naming_the_file(self, handler, kwargs):
        cmd = SkillsCommand()
        with (
            patch("deepctl_core.skill_generator.get_all_generators", return_value=[]),
            patch("deepctl_core.skill_generator.detect_ai_clis", return_value=[]),
            patch("deepctl_core.skill_generator.save_skills_state") as save,
            patch("deepctl_core.skill_generator.fetch_repo_skills") as fetch,
            pytest.raises(click.ClickException) as excinfo,
        ):
            getattr(cmd, handler)(**kwargs)

        message = str(excinfo.value)
        assert str(skill_generator._STATE_FILE) in message
        assert "Expecting value" in message
        assert "dg skills install" in message
        save.assert_not_called()
        fetch.assert_not_called()

    @pytest.mark.parametrize("handler", ["_handle_status", "_handle_list"])
    def test_status_and_list_report_it_in_their_result(self, handler, capsys):
        """They return an error result, so -o json still gets a document."""
        cmd = SkillsCommand()
        with patch("deepctl_core.skill_generator.get_all_generators", return_value=[]):
            result = getattr(cmd, handler)()

        assert result.status == "error"
        assert str(skill_generator._STATE_FILE) in result.message
        assert "Expecting value" in result.message
        assert "dg skills install" in result.message
        assert cmd.exit_code_for(result) == 1
        assert str(skill_generator._STATE_FILE) in capsys.readouterr().err


def _seed_a_record_to_update():
    """A skills.json with one tool recorded, so update gets as far as the lock."""
    state_file = Path(skill_generator._STATE_FILE)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"installed_skills": {"claude": {"paths": []}}}))


@contextlib.contextmanager
def _a_bundle_to_install():
    """A fetch that succeeds, and a Claude Code that writes nothing for real."""
    root = Path.home() / ".claude" / "skills"
    generator = MagicMock()
    generator.cli_name = "claude"
    generator.display_name = "Claude Code"
    generator.skills_root.return_value = root
    generator.install_conflicts.return_value = []
    generator.install_skills.return_value = []
    generator.prune_retired_result.return_value = skill_generator.PruneResult()
    with (
        patch(
            "deepctl_core.skill_generator.get_all_generators",
            return_value=[generator],
        ),
        patch("deepctl_core.skill_generator.collect_command_metadata", return_value=[]),
        patch(
            "deepctl_core.skill_generator.fetch_repo_skills",
            return_value=[RepoSkill(name="api", path=Path("/upstream/api"))],
        ),
    ):
        yield


class TestTheStateFileIsHeldWhileItIsRewritten:
    """`update` and `remove` read, change and save `skills.json`.

    Core's `skills_state_lock` is what keeps two deepctl processes from
    saving over each other's records, so the whole read-modify-write has
    to sit inside it, not just the install step core locks itself.
    """

    @pytest.fixture
    def lock_calls(self, monkeypatch):
        import contextlib

        calls = []

        @contextlib.contextmanager
        def recording_lock(timeout=None):
            calls.append("enter")
            yield
            calls.append("exit")

        # Core's, which an install takes after the fetch, and the name the
        # command imported from it, which remove takes itself.
        monkeypatch.setattr(skill_generator, "skills_state_lock", recording_lock)
        monkeypatch.setattr(skills_command, "skills_state_lock", recording_lock)
        return calls

    def test_remove_runs_under_the_lock(self, tmp_path, lock_calls):
        cmd = SkillsCommand()
        root = tmp_path / ".claude" / "skills"
        generator = MagicMock()
        generator.cli_name = "claude"
        generator.display_name = "Claude Code"
        generator.skills_root.return_value = root
        generator.owned_skill_paths.return_value = []
        generator.remove_report.return_value = skill_generator.RemoveReport()
        state = {"installed_skills": {"claude": {"paths": [str(root / "api")]}}}
        saved = []

        def save(st):
            # Saved while the lock is held, not after it was released.
            saved.append(list(lock_calls))

        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[generator],
            ),
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch("deepctl_core.skill_generator.save_skills_state", side_effect=save),
        ):
            cmd._handle_remove(remove_all=True)

        assert lock_calls == ["enter", "exit"]
        assert saved == [["enter"]]

    @staticmethod
    def _claude(root):
        generator = MagicMock()
        generator.cli_name = "claude"
        generator.display_name = "Claude Code"
        generator.detect.return_value = True
        generator.skills_root.return_value = root
        generator.install_conflicts.return_value = []
        generator.install_skills.return_value = [root / "api"]
        generator.prune_retired_result.return_value = skill_generator.PruneResult()
        return generator

    @pytest.mark.parametrize(
        ("handler", "kwargs", "recorded"),
        [
            ("_handle_update", {}, True),
            ("_handle_install", {"install_all": True}, False),
            ("_handle_setup", {"install_all": True}, False),
        ],
    )
    def test_the_download_runs_with_no_lock_and_the_save_under_it(
        self, tmp_path, lock_calls, handler, kwargs, recorded
    ):
        """Fetch before the lock: a slow download must not block another deepctl."""
        cmd = SkillsCommand()
        root = tmp_path / ".claude" / "skills"
        generator = self._claude(root)
        state = {
            "installed_skills": (
                {"claude": {"paths": [str(root / "api")]}} if recorded else {}
            )
        }
        at_fetch = []
        at_save = []

        def fetch(*_args, **_kwargs):
            at_fetch.append(list(lock_calls))
            return [RepoSkill(name="api", path=tmp_path / "api")]

        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[generator],
            ),
            patch(
                "deepctl_core.skill_generator.detect_ai_clis", return_value=[generator]
            ),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata", return_value=[]
            ),
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch(
                "deepctl_core.skill_generator.save_skills_state",
                side_effect=lambda _st: at_save.append(list(lock_calls)),
            ),
            patch("deepctl_core.skill_generator.fetch_repo_skills", side_effect=fetch),
        ):
            getattr(cmd, handler)(**kwargs)

        # The lock is free during the fetch; every save inside it. An
        # update may take and release it first, to settle a remove that
        # had not finished before deciding there is anything to fetch.
        assert len(at_fetch) == 1
        assert at_fetch[0].count("enter") == at_fetch[0].count("exit")
        assert at_save
        assert all(calls.count("enter") > calls.count("exit") for calls in at_save)
        assert lock_calls.count("enter") == lock_calls.count("exit")

    def test_the_real_lock_is_free_during_the_download(self, tmp_path):
        """Against core's own lock, not a stand-in for it."""
        cmd = SkillsCommand()
        root = tmp_path / ".claude" / "skills"
        generator = self._claude(root)
        depth = []

        def fetch(*_args, **_kwargs):
            depth.append(skill_generator._state_lock_depth)
            return [RepoSkill(name="api", path=tmp_path / "api")]

        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[generator],
            ),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata", return_value=[]
            ),
            patch("deepctl_core.skill_generator.fetch_repo_skills", side_effect=fetch),
        ):
            cmd._handle_install(cli_name="claude")
            cmd._handle_update()

        assert depth == [0, 0]
        recorded = skill_generator.get_skills_state()["installed_skills"]["claude"]
        assert [Path(p).name for p in recorded["paths"]] == ["api"]

    def test_a_tool_removed_during_the_download_is_not_reinstalled(
        self, tmp_path, capsys
    ):
        """`dg skills remove --cli cursor` while `update` is downloading.

        `update` picks its tools from a first read. Installing them all
        after the fetch brought cursor back: recorded again, with every
        folder reinstalled, moments after the user removed it.
        """
        claude = self._claude(tmp_path / ".claude" / "skills")
        cursor = self._claude(tmp_path / ".cursor" / "skills")
        cursor.cli_name = "cursor"
        cursor.display_name = "Cursor"
        cursor.owned_skill_paths.return_value = []
        cursor.remove_report.return_value = skill_generator.RemoveReport()
        state_file = Path(skill_generator._STATE_FILE)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps(
                {
                    "installed_skills": {
                        "claude": {"paths": [], "skills": []},
                        "cursor": {"paths": [], "skills": []},
                    }
                }
            )
        )

        def fetch(*_args, **_kwargs):
            # The other process: a real remove, lock and all.
            SkillsCommand()._handle_remove(cli_name="cursor")
            return [RepoSkill(name="api", path=tmp_path / "api")]

        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[claude, cursor],
            ),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata", return_value=[]
            ),
            patch("deepctl_core.skill_generator.fetch_repo_skills", side_effect=fetch),
        ):
            SkillsCommand()._handle_update()

        saved = json.loads(state_file.read_text())["installed_skills"]
        assert set(saved) == {"claude"}
        cursor.install_skills.assert_not_called()
        claude.install_skills.assert_called_once()
        captured = capsys.readouterr()
        assert "removed while the update ran" not in captured.out
        err = " ".join(captured.err.split())
        assert "cursor: removed while the update ran, so it was left alone." in err

    @pytest.mark.parametrize(
        ("handler", "kwargs", "expected"),
        [
            ("_handle_update", {}, True),
            ("_handle_install", {"install_all": True}, False),
            ("_handle_setup", {"install_all": True}, False),
        ],
    )
    def test_only_update_skips_tools_the_records_no_longer_hold(
        self, tmp_path, handler, kwargs, expected
    ):
        """Install and setup install what the user just picked, recorded or not."""
        from deepctl_core.skill_generator import SkillInstallReport

        generator = self._claude(tmp_path / ".claude" / "skills")
        state = {"installed_skills": {"claude": {"paths": []}}}
        report = SkillInstallReport(
            ref="v1",
            skills=[],
            written={},
            unsupported=[],
            conflicts=[],
            failures=[],
        )
        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[generator],
            ),
            patch(
                "deepctl_core.skill_generator.detect_ai_clis", return_value=[generator]
            ),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata", return_value=[]
            ),
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch(
                "deepctl_core.skill_generator.install_skills_for", return_value=report
            ) as install,
        ):
            getattr(SkillsCommand(), handler)(**kwargs)

        assert install.call_args.kwargs["only_recorded"] is expected

    @pytest.mark.parametrize(
        ("handler", "kwargs"),
        [("_handle_install", {"cli_name": "claude"}), ("_handle_update", {})],
    )
    def test_records_damaged_during_the_download_exit_one_unchanged(
        self, tmp_path, handler, kwargs
    ):
        """The re-read under the lock gets the same refusal as the first read."""
        root = tmp_path / ".claude" / "skills"
        generator = self._claude(root)
        state_file = Path(skill_generator._STATE_FILE)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps({"installed_skills": {"claude": {"paths": []}}})
        )

        def fetch(*_args, **_kwargs):
            state_file.write_text(json.dumps({"installed_skills": ["claude"]}))
            return [RepoSkill(name="api", path=tmp_path / "api")]

        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[generator],
            ),
            patch(
                "deepctl_core.skill_generator.collect_command_metadata", return_value=[]
            ),
            patch("deepctl_core.skill_generator.fetch_repo_skills", side_effect=fetch),
            pytest.raises(click.ClickException) as excinfo,
        ):
            getattr(SkillsCommand(), handler)(**kwargs)

        assert "cannot read its own records" in str(excinfo.value)
        assert json.loads(state_file.read_text()) == {"installed_skills": ["claude"]}
        generator.install_skills.assert_not_called()

    def test_a_lock_that_cannot_be_taken_is_exit_one_naming_the_file(self, monkeypatch):
        def refusing_lock(timeout=None):
            raise skill_generator.SkillsStateError(
                "Another deepctl is holding the lock."
            )

        monkeypatch.setattr(skill_generator, "skills_state_lock", refusing_lock)
        _seed_a_record_to_update()
        cmd = SkillsCommand()
        with _a_bundle_to_install(), pytest.raises(click.ClickException) as excinfo:
            cmd._handle_update()

        message = str(excinfo.value)
        assert "holding the lock" in message
        assert str(skill_generator._STATE_FILE) in message


class TestLegacyFilesLeftInPlaceAreNamed:
    """A deepctl <= 0.3.0 file the cleanup declined to touch gets a warning.

    Core reports these instead of editing a file whose content is not all
    deepctl's. Silence would leave a stale copy next to the fresh install
    with nothing saying why it is still there.
    """

    REASON = "it holds text deepctl did not write"

    @staticmethod
    def _said(capsys):
        captured = capsys.readouterr()
        return " ".join((captured.out + captured.err).split())

    def test_install_names_each_one(self, tmp_path, capsys):
        from deepctl_core.skill_generator import SkillInstallReport

        legacy = tmp_path / "CONVENTIONS.md"
        report = SkillInstallReport(
            ref="v1",
            skills=[],
            written={},
            unsupported=[],
            conflicts=[],
            failures=[],
        )
        report.legacy_skipped = [("Aider", legacy, self.REASON)]
        with (
            patch(
                "deepctl_core.skill_generator.collect_command_metadata", return_value=[]
            ),
            patch(
                "deepctl_core.skill_generator.install_skills_for", return_value=report
            ),
        ):
            SkillsCommand()._install_for([], {"installed_skills": {}}, None)

        assert f"Aider: left {legacy} in place: {self.REASON}" in self._said(capsys)

    def test_remove_names_each_one(self, tmp_path, capsys):
        from deepctl_core.skill_generator import RemoveReport

        root = tmp_path / ".aider" / "skills"
        legacy = tmp_path / "CONVENTIONS.md"
        generator = MagicMock()
        generator.cli_name = "aider"
        generator.display_name = "Aider"
        generator.skills_root.return_value = root
        generator.owned_skill_paths.return_value = []
        generator.remove_report.return_value = RemoveReport(
            removed=[], stranded=[], legacy_skipped=[(legacy, self.REASON)]
        )
        state = {"installed_skills": {"aider": {"paths": []}}}
        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[generator],
            ),
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch("deepctl_core.skill_generator.save_skills_state"),
        ):
            SkillsCommand()._handle_remove(remove_all=True)

        assert f"Aider: left {legacy} in place: {self.REASON}" in self._said(capsys)


class TestRecordsHeldByAnotherDeepctl:
    """A lock timeout is "retry", never "fix or delete the file"."""

    def test_update_exits_one_saying_to_wait_and_retry(self, monkeypatch):
        lock_file = getattr(skill_generator, "_lock_file", None)
        try_lock = getattr(skill_generator, "_try_lock", None)
        unlock = getattr(skill_generator, "_unlock", None)
        if not (callable(lock_file) and callable(try_lock) and callable(unlock)):
            pytest.skip("core has no cross-process skills.json lock yet")
        monkeypatch.setattr(skill_generator, "_STATE_LOCK_TIMEOUT", 0.2)
        _seed_a_record_to_update()
        path = lock_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a+b") as other:
            assert try_lock(other)
            try:
                with (
                    _a_bundle_to_install(),
                    pytest.raises(click.ClickException) as excinfo,
                ):
                    SkillsCommand()._handle_update()
            finally:
                unlock(other)

        message = str(excinfo.value)
        assert "holds the skill records lock" in message
        # Core's message says to wait and retry; the command adds no
        # second copy of that advice.
        assert message.count("ait for it to finish") == 1
        assert message.endswith("Nothing was changed.")
        assert "Fix or delete" not in message
        assert "delete that file" not in message

    def test_a_damaged_file_still_says_fix_or_delete(self, monkeypatch):
        error = getattr(skill_generator, "SkillsStateError", None)
        if error is None:
            pytest.skip("core has no SkillsStateError yet")

        def refusing_lock(timeout=None):
            raise error(f"Cannot open {skill_generator._STATE_FILE}.lock")

        monkeypatch.setattr(skill_generator, "skills_state_lock", refusing_lock)
        _seed_a_record_to_update()
        with _a_bundle_to_install(), pytest.raises(click.ClickException) as excinfo:
            SkillsCommand()._handle_update()
        assert "Another deepctl process" not in str(excinfo.value)


def test_messages_name_the_file_core_actually_reads(monkeypatch, tmp_path):
    """An overridden skills directory moves the file; the message follows it."""
    elsewhere = tmp_path / "elsewhere" / "skills.json"
    monkeypatch.setattr(skill_generator, "_STATE_FILE", elsewhere)
    assert skills_command._state_file() == str(elsewhere)


def test_update_short_help_matches_the_other_subcommands():
    """No trailing period, like every other subcommand's short help."""
    group = SkillsCommand().get_click_group()
    for name, sub in group.commands.items():
        assert not (sub.help or "").rstrip().endswith("."), name


class TestAPromptWithNoAnswer:
    """A declined prompt and an unanswered one both exit 2.

    With stdin at EOF, `install --cli X` exited 2 and a bare `install`
    exited 0 saying "No skills were installed". The rule now: a prompt
    declined, or left unanswered at EOF, is the user declining, which the
    README documents as exit 2. The prompts go to stderr so stdout stays
    the command's payload.
    """

    @pytest.fixture(autouse=True)
    def _a_person_could_answer(self, monkeypatch):
        # CI sets the agentic heuristic, which skips every prompt.
        monkeypatch.setattr(output, "_agentic", False)

    @staticmethod
    def _tool(detected=True):
        generator = MagicMock()
        generator.cli_name = "claude"
        generator.display_name = "Claude Code"
        generator.detect.return_value = detected
        return generator

    def _run(self, handler, stdin, **kwargs):
        """Run a handler with a real stdin; return (exit code, stdout, stderr)."""
        cmd = SkillsCommand()
        cmd._guided = True
        generator = self._tool(detected=kwargs.pop("detected", True))
        code = None
        with (
            CliRunner().isolation(input=stdin) as streams,
            # After the isolation, so it is that stdout which claims a tty.
            patch("sys.stdout.isatty", return_value=True, create=True),
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[generator],
            ),
            patch(
                "deepctl_core.skill_generator.detect_ai_clis", return_value=[generator]
            ),
            patch("deepctl_core.skill_generator.fetch_repo_skills") as fetch,
        ):
            try:
                getattr(cmd, handler)(**kwargs)
            except click.Abort:
                code = 2
            except SystemExit as exc:
                code = exc.code
            # Click yields (stdout, stderr, interleaved) since 8.2.
            stdout = streams[0].getvalue().decode()
            stderr = streams[1].getvalue().decode()
        fetch.assert_not_called()
        generator.install_skills.assert_not_called()
        return code, stdout, " ".join(stderr.split())

    def test_a_bare_install_at_eof_exits_two_and_names_all(self):
        code, stdout, stderr = self._run("_handle_install", "")
        assert code == 2
        assert "No answer was read" in stderr
        assert "--all" in stderr
        assert "No skills were installed" not in stdout + stderr

    def test_install_for_an_undetected_cli_at_eof_exits_two(self):
        code, _stdout, stderr = self._run(
            "_handle_install", "", cli_name="claude", detected=False
        )
        assert code == 2
        assert "No answer was read" in stderr

    def test_declining_every_tool_exits_two(self):
        code, _stdout, stderr = self._run("_handle_install", "n\n")
        assert code == 2
        assert "nothing was installed" in stderr

    def test_the_prompt_goes_to_stderr(self):
        """The prompt and the decline message, not whatever stdin echoes.

        On Windows the CliRunner echoes the typed answer onto stdout, so
        an empty stdout is not something this can assert there.
        """
        _code, stdout, stderr = self._run("_handle_install", "n\n")
        assert "Install Deepgram skills for Claude Code?" in stderr
        assert "nothing was installed" in stderr
        assert "Install Deepgram skills" not in stdout
        assert "nothing was installed" not in stdout

    def test_setup_at_eof_exits_two_and_names_all(self):
        code, stdout, stderr = self._run("_handle_setup", "")
        assert code == 2
        assert "No answer was read" in stderr
        assert "--all" in stderr
        assert "Install skills for" not in stdout

    def test_setup_answered_none_exits_two(self):
        code, _stdout, _stderr = self._run("_handle_setup", "none\n")
        assert code == 2

    def test_setup_answered_with_no_matching_tool_exits_two(self):
        """`9` with one tool detected installs nothing, so it is a decline."""
        code, stdout, stderr = self._run("_handle_setup", "9\n")
        assert code == 2
        assert "'9' matches no detected tool" in stderr
        assert "Choose from 1, all, or none" in stderr
        assert "No valid tools selected" not in stdout + stderr
        assert "matches no detected tool" not in stdout

    def test_ctrl_c_is_still_an_abort(self, monkeypatch):
        """Only EOF gets the "no answer" message; an interrupt keeps main.py's."""

        def interrupted(*_args, **_kwargs):
            try:
                raise KeyboardInterrupt
            except KeyboardInterrupt:
                raise click.Abort() from None

        monkeypatch.setattr(click, "confirm", interrupted)
        cmd = SkillsCommand()
        cmd._guided = True
        with pytest.raises(click.Abort):
            cmd._ask("Install?", default=True, skip_with="--all")


class TestStatusAdvice:
    """`status` suggests an install only when the records say nothing is there."""

    @staticmethod
    def _status(state, capsys):
        generator = MagicMock()
        generator.cli_name = "claude"
        generator.display_name = "Claude Code"
        generator.detect.return_value = True
        generator.skills_root.return_value = Path.home() / ".claude" / "skills"
        generator.installed_skill_paths.return_value = []
        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[generator],
            ),
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
        ):
            result = SkillsCommand()._handle_status()
        captured = capsys.readouterr()
        return result, " ".join((captured.out + captured.err).split())

    def test_unreadable_records_do_not_say_run_install(self, capsys):
        result, said = self._status({"installed_skills": ["claude"]}, capsys)
        assert result.status == "error"
        assert "cannot read its own records" in said
        assert "Run 'dg skills install'" not in said

    def test_empty_records_do(self, capsys):
        result, said = self._status({"installed_skills": {}}, capsys)
        assert result.status == "success"
        assert "Run 'dg skills install'" in said


class TestArgumentsAreCheckedBeforeTheRecords:
    """A bad argument fails the same way whether or not anything is installed.

    `update` returned "No skills installed" before it looked at the ref,
    and `remove` returned "No skills are installed" before it looked for
    --all or --cli, so both exited 0 on an empty machine and 1 elsewhere.
    """

    @staticmethod
    def _state_file() -> Path:
        return Path(skill_generator._STATE_FILE)

    def test_update_with_an_empty_ref_is_refused(self, capsys):
        """`install --ref ""` was refused; `update --ref ""` used the record."""
        state_file = self._state_file()
        state_file.parent.mkdir(parents=True, exist_ok=True)
        records = {"installed_skills": {"claude": {"paths": [], "skills_ref": "main"}}}
        state_file.write_text(json.dumps(records))

        with (
            patch("deepctl_core.skill_generator.fetch_repo_skills") as fetch,
            pytest.raises(SkillFetchError, match="Skills ref is empty"),
        ):
            SkillsCommand()._handle_update(ref="")

        fetch.assert_not_called()
        assert json.loads(state_file.read_text()) == records
        assert "Updating to" not in capsys.readouterr().err

    @pytest.mark.parametrize("source", ["--ref", REF_ENV_VAR])
    def test_update_with_an_invalid_ref_fails_with_nothing_installed(
        self, monkeypatch, capsys, source
    ):
        ref = None
        if source == REF_ENV_VAR:
            monkeypatch.setenv(REF_ENV_VAR, "a..b")
        else:
            ref = "a..b"

        with pytest.raises(SkillFetchError, match="Invalid skills ref"):
            SkillsCommand()._handle_update(ref=ref)

        assert "No skills installed" not in capsys.readouterr().err
        assert not self._state_file().exists()

    def test_update_with_an_empty_ref_fails_with_nothing_installed(self):
        with pytest.raises(SkillFetchError, match="Skills ref is empty"):
            SkillsCommand()._handle_update(ref="")
        assert not self._state_file().exists()

    def test_a_blank_environment_ref_is_still_ignored(self, monkeypatch, capsys):
        monkeypatch.setenv(REF_ENV_VAR, "   ")
        SkillsCommand()._handle_update()
        assert "No skills installed" in capsys.readouterr().err

    def test_bare_remove_is_a_usage_error_with_nothing_installed(self, capsys):
        with pytest.raises(click.UsageError, match="--all"):
            SkillsCommand()._handle_remove()

        assert "No skills are installed" not in capsys.readouterr().err
        assert not self._state_file().exists()


def _seed_state_file(content: str) -> Path:
    state_file = Path(skill_generator._STATE_FILE)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(content)
    return state_file


class TestADamagedSkillsFileUnderStructuredOutput:
    """`dg -o json skills list` with a skills.json that is not JSON exited 1
    with an empty stdout, while records of the wrong shape got an error
    document. Both kinds of damage now get the same document."""

    @pytest.mark.parametrize("fmt", ["json", "yaml"])
    @pytest.mark.parametrize("subcommand", ["status", "list"])
    @pytest.mark.parametrize(
        "content",
        ["{not json", json.dumps({"installed_skills": []})],
        ids=["invalid-json", "wrong-shape"],
    )
    def test_each_damage_is_one_error_document_and_exit_one(
        self, fmt, subcommand, content
    ):
        import yaml

        state_file = _seed_state_file(content)
        output.update_output(format_type=fmt)
        with patch("deepctl_core.skill_generator.get_all_generators", return_value=[]):
            result = CliRunner().invoke(
                SkillsCommand().get_click_group(), [subcommand], obj={}
            )

        assert result.exit_code == 1, result.output
        payload = (json.loads if fmt == "json" else yaml.safe_load)(result.stdout)
        assert payload["status"] == "error"
        assert str(state_file) in " ".join(payload["message"].split())


class TestTableAndCsvOutput:
    """`-o table` and `-o csv` printed the result document as key/value
    pairs, with the list of tools as a Python repr. `-o table` is now the
    skills table, and `-o csv` one row per tool under its columns."""

    STATUS_HEADER: ClassVar[list[str]] = [
        "CLI",
        "Detected",
        "Deepgram Skills",
        "Skills ref",
        "Skills Directory",
    ]
    LIST_HEADER: ClassVar[list[str]] = [
        "CLI",
        "deepctl",
        "Skills ref",
        "Skills",
        "Location",
    ]

    @staticmethod
    def _generators(root):
        claude = MagicMock()
        claude.cli_name = "claude"
        claude.display_name = "Claude Code"
        claude.detect.return_value = True
        claude.skills_root.return_value = root
        claude.installed_skill_paths.return_value = [root / "api"]
        aider = MagicMock()
        aider.cli_name = "aider"
        aider.display_name = "Aider"
        aider.detect.return_value = False
        aider.skills_root.return_value = None
        aider.installed_skill_paths.return_value = []
        return [claude, aider]

    @staticmethod
    def _state(root):
        return {
            "installed_skills": {
                "claude": {
                    "paths": [str(root / "api")],
                    "skills": ["api"],
                    "skills_ref": "main",
                    "version": "0.4.0",
                }
            }
        }

    def _invoke(self, fmt, subcommand, tmp_path):
        root = tmp_path / ".claude" / "skills"
        output.update_output(format_type=fmt)
        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=self._generators(root),
            ),
            patch(
                "deepctl_core.skill_generator.get_skills_state",
                return_value=self._state(root),
            ),
        ):
            result = CliRunner().invoke(
                SkillsCommand().get_click_group(), [subcommand], obj={}
            )
        assert result.exit_code == 0, result.output
        return result.stdout, root

    @staticmethod
    def _no_repr(out):
        assert "[{" not in out
        assert "'cli':" not in out
        assert "Key" not in out.split()

    @staticmethod
    def _cells(out, needle):
        row = next(line for line in out.splitlines() if needle in line)
        return [c.strip() for c in row.split("│")][1:-1]

    def test_status_table_is_the_skills_table(self, tmp_path):
        out, root = self._invoke("table", "status", tmp_path)

        self._no_repr(out)
        assert "AI Coding Assistant Status" in out
        assert self._cells(out, "Claude Code") == [
            "Claude Code",
            "Yes",
            "1",
            "main",
            str(root),
        ]
        assert self._cells(out, "Aider") == [
            "Aider",
            "No",
            "n/a",
            "-",
            "no skills directory",
        ]

    def test_status_table_matches_the_default_mode(self, tmp_path):
        as_table, _ = self._invoke("table", "status", tmp_path)
        by_default, _ = self._invoke("default", "status", tmp_path)
        assert as_table == by_default

    def test_status_csv_is_one_row_per_tool(self, tmp_path):
        import csv
        import io

        out, root = self._invoke("csv", "status", tmp_path)

        self._no_repr(out)
        rows = list(csv.reader(io.StringIO(out)))
        assert rows == [
            self.STATUS_HEADER,
            ["Claude Code", "Yes", "1", "main", str(root)],
            ["Aider", "No", "n/a", "-", "no skills directory"],
        ]

    def test_list_table_is_the_skills_table(self, tmp_path):
        out, root = self._invoke("table", "list", tmp_path)

        self._no_repr(out)
        assert "Installed Skills" in out
        assert self._cells(out, "claude") == [
            "claude",
            "0.4.0",
            "main",
            "1",
            str(root),
        ]

    def test_list_table_matches_the_default_mode(self, tmp_path):
        as_table, _ = self._invoke("table", "list", tmp_path)
        by_default, _ = self._invoke("default", "list", tmp_path)
        assert as_table == by_default

    def test_list_csv_is_one_row_per_tool(self, tmp_path):
        import csv
        import io

        out, root = self._invoke("csv", "list", tmp_path)

        self._no_repr(out)
        rows = list(csv.reader(io.StringIO(out)))
        assert rows == [self.LIST_HEADER, ["claude", "0.4.0", "main", "1", str(root)]]

    def test_list_csv_with_nothing_installed_is_the_header_alone(self):
        output.update_output(format_type="csv")
        with patch(
            "deepctl_core.skill_generator.get_skills_state",
            return_value={"installed_skills": {}},
        ):
            result = CliRunner().invoke(
                SkillsCommand().get_click_group(), ["list"], obj={}
            )
        assert result.exit_code == 0, result.output
        assert result.stdout.splitlines() == [",".join(self.LIST_HEADER)]

    @pytest.mark.parametrize("subcommand", ["status", "list"])
    def test_json_and_yaml_are_unchanged(self, tmp_path, subcommand):
        import yaml

        as_json, _ = self._invoke("json", subcommand, tmp_path)
        as_yaml, _ = self._invoke("yaml", subcommand, tmp_path)
        assert json.loads(as_json) == yaml.safe_load(as_yaml)
        key = "tools" if subcommand == "status" else "installed"
        assert isinstance(json.loads(as_json)[key], list)


class TestAnInvalidEnvironmentRef:
    """`DEEPCTL_SKILLS_REF='bad ref' dg skills install --all` said only
    "Invalid skills ref 'bad ref'", with no mention of the variable."""

    @pytest.mark.parametrize(
        ("args", "state"),
        [
            (["install", "--all"], {"installed_skills": {}}),
            (["setup", "--all"], {"installed_skills": {}}),
            (["update"], {"installed_skills": {"claude": {"paths": []}}}),
        ],
        ids=["install", "setup", "update"],
    )
    def test_the_error_names_the_variable_and_the_way_out(
        self, monkeypatch, args, state
    ):
        monkeypatch.setenv(REF_ENV_VAR, "bad ref")
        with (
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch("deepctl_core.skill_generator.detect_ai_clis") as detect,
            patch("deepctl_core.skill_generator.fetch_repo_skills") as fetch,
        ):
            result = CliRunner().invoke(SkillsCommand().get_click_group(), args, obj={})

        assert result.exit_code != 0
        message = " ".join(str(result.exception).split())
        assert message.startswith("Invalid skills ref 'bad ref'")
        assert f"That ref came from {REF_ENV_VAR}." in message
        assert message.endswith(
            "Set it to another ref, or unset it to use the pinned release."
        )
        detect.assert_not_called()
        fetch.assert_not_called()

    @pytest.mark.parametrize("args", [["install", "--all"], ["update"]])
    def test_an_invalid_flag_ref_keeps_its_message(self, monkeypatch, args):
        monkeypatch.setenv(REF_ENV_VAR, "main")
        state = {"installed_skills": {"claude": {"paths": []}}}
        with patch("deepctl_core.skill_generator.get_skills_state", return_value=state):
            result = CliRunner().invoke(
                SkillsCommand().get_click_group(),
                [*args, "--ref", "bad ref"],
                obj={},
            )

        message = str(result.exception)
        assert message.startswith("Invalid skills ref 'bad ref'")
        assert REF_ENV_VAR not in message
        assert message.endswith("or an empty path segment.")


class TestEightyColumnTables:
    """At 80 columns `dg skills status` cut the ref and the directory short:
    `│ Claude Code │ Yes │ 14 │ deepgram-ski… │ ~/.claude/s… │`. Both are
    now folded onto more lines instead, in `status` and in `list`."""

    REF = "deepgram-skills-release/v0.14.0-rc.1"

    @staticmethod
    def _render(table):
        import io

        from rich.console import Console

        buffer = io.StringIO()
        # Pin the box style: on a Windows console Rich would otherwise swap
        # the heavy header for a "safe" legacy box.
        Console(
            file=buffer,
            width=80,
            color_system=None,
            force_terminal=False,
            legacy_windows=False,
            safe_box=False,
        ).print(table)
        return buffer.getvalue()

    @staticmethod
    def _column(out, index):
        """Column ``index`` of the only data row, its folded lines joined."""
        lines = out.splitlines()
        # The body sits between the header rule and the closing rule. Find
        # them as the lines that hold no cell divider rather than by a box
        # glyph, which differs between box styles.
        rules = [
            i
            for i, line in enumerate(lines)
            if line.strip() and "│" not in line and "┃" not in line
        ]
        start, end = rules[-2], rules[-1]
        return "".join(
            line.split("│")[index + 1].strip() for line in lines[start + 1 : end]
        )

    def _long_dir(self, tmp_path):
        return tmp_path / "a-rather-long-directory-name" / ".claude" / "skills"

    def test_status_keeps_the_whole_ref_and_directory(self, tmp_path):
        root = self._long_dir(tmp_path)
        out = self._render(
            skills_command._status_table(
                [
                    skills_command.SkillsToolStatus(
                        cli="claude",
                        display_name="Claude Code",
                        detected=True,
                        installed=14,
                        skills_ref=self.REF,
                        skills_directory=str(root),
                    )
                ]
            )
        )

        assert "…" not in out
        assert max(len(line) for line in out.splitlines()) <= 80
        assert self._column(out, 0) == "Claude Code"
        assert self._column(out, 3) == self.REF
        assert self._column(out, 4) == str(root)

    def test_list_keeps_the_whole_ref_and_location(self, tmp_path):
        root = self._long_dir(tmp_path)
        out = self._render(
            skills_command._list_table(
                [
                    skills_command.SkillsInstalledTool(
                        cli="claude",
                        deepctl_version="0.4.0",
                        skills_ref=self.REF,
                        skills=["api"],
                        count=14,
                        location=str(root),
                    )
                ]
            )
        )

        assert "…" not in out
        assert max(len(line) for line in out.splitlines()) <= 80
        assert self._column(out, 1) == "0.4.0"
        assert self._column(out, 2) == self.REF
        assert self._column(out, 4) == str(root)

    @pytest.mark.parametrize("subcommand", ["status", "list"])
    def test_csv_is_unaffected_at_80_columns(self, tmp_path, monkeypatch, subcommand):
        import csv
        import io

        monkeypatch.setenv("COLUMNS", "80")
        root = self._long_dir(tmp_path)
        state = {
            "installed_skills": {
                "claude": {
                    "paths": [str(root / "api")],
                    "skills": ["api"],
                    "skills_ref": self.REF,
                    "version": "0.4.0",
                }
            }
        }
        claude = MagicMock()
        claude.cli_name = "claude"
        claude.display_name = "Claude Code"
        claude.detect.return_value = True
        claude.skills_root.return_value = root
        claude.installed_skill_paths.return_value = [root / "api"]
        output.update_output(format_type="csv")
        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[claude],
            ),
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
        ):
            result = CliRunner().invoke(
                SkillsCommand().get_click_group(), [subcommand], obj={}
            )

        assert result.exit_code == 0, result.output
        rows = list(csv.reader(io.StringIO(result.stdout)))
        assert len(rows) == 2
        assert self.REF in rows[1]
        assert str(root) in rows[1]


class TestTableModeLineOrder:
    """Under `-o table`, `dg skills status` printed its stderr INFO line
    above the table; default mode prints it below. Both now print the
    table first."""

    @staticmethod
    def _invoke(fmt, subcommand, tmp_path):
        root = tmp_path / ".claude" / "skills"
        claude = MagicMock()
        claude.cli_name = "claude"
        claude.display_name = "Claude Code"
        claude.detect.return_value = True
        claude.skills_root.return_value = root
        claude.installed_skill_paths.return_value = [root / "api"]
        # Detected with nowhere to put skills: the source of the INFO line.
        aider = MagicMock()
        aider.cli_name = "aider"
        aider.display_name = "Aider"
        aider.detect.return_value = True
        aider.skills_root.return_value = None
        aider.installed_skill_paths.return_value = []
        state = {
            "installed_skills": {
                "claude": {
                    "paths": [str(root / "api")],
                    "skills": ["api"],
                    "skills_ref": "main",
                    "version": "0.4.0",
                }
            }
        }
        output.update_output(format_type=fmt)
        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[claude, aider],
            ),
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
        ):
            result = CliRunner().invoke(
                SkillsCommand().get_click_group(), [subcommand], obj={}
            )
        assert result.exit_code == 0, result.output
        return result

    @pytest.mark.parametrize("fmt", ["default", "table"])
    @pytest.mark.parametrize(
        ("subcommand", "title", "info"),
        [
            ("status", "AI Coding Assistant Status", "Tools with no skills directory"),
            ("list", "Installed Skills", "Run 'dg skills update'"),
        ],
    )
    def test_info_lines_follow_the_table(self, tmp_path, fmt, subcommand, title, info):
        result = self._invoke(fmt, subcommand, tmp_path)

        # Click interleaves stdout and stderr in .output, in write order.
        combined = result.output
        table_end = combined.rindex("└")
        assert combined.index(title) < table_end < combined.index(info)
        # The INFO line is stderr in every mode, never part of the table.
        assert info not in result.stdout
        assert info in result.stderr

    @pytest.mark.parametrize("fmt", ["json", "yaml", "csv"])
    def test_machine_formats_keep_info_on_stderr(self, tmp_path, fmt):
        result = self._invoke(fmt, "status", tmp_path)

        assert "Tools with no skills directory" not in result.stdout
        assert "Tools with no skills directory" in result.stderr
        assert "AI Coding Assistant Status" not in result.output


class TestAnInvalidRecordedRef:
    """`dg skills update` with `a..b` recorded in skills.json said only
    "Invalid skills ref 'a..b': ... empty path segment.", naming neither
    the file nor the way past it."""

    def test_the_error_names_the_file_and_the_way_out(self):
        state = {"installed_skills": {"claude": {"paths": [], "skills_ref": "a..b"}}}
        with (
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch("deepctl_core.skill_generator.fetch_repo_skills") as fetch,
        ):
            result = CliRunner().invoke(
                SkillsCommand().get_click_group(), ["update"], obj={}
            )

        assert result.exit_code == 1, result.output
        message = " ".join(result.output.split())
        assert "Invalid skills ref 'a..b'" in message
        assert "That ref came from the last install's record in" in message
        assert "skills.json." in message
        assert message.endswith(
            "Nothing was updated. Pass --ref to choose another deepgram/skills ref."
        )
        assert "Updating to" not in message
        fetch.assert_not_called()

    def test_a_flag_ref_overrides_the_bad_record(self):
        state = {"installed_skills": {"claude": {"paths": [], "skills_ref": "a..b"}}}
        with (
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch(
                "deepctl_core.skill_generator.fetch_repo_skills",
                side_effect=SkillFetchError("offline"),
            ) as fetch,
        ):
            result = CliRunner().invoke(
                SkillsCommand().get_click_group(), ["update", "--ref", "main"], obj={}
            )

        assert "Invalid skills ref" not in result.output
        fetch.assert_called_once()


class TestRetiredSkillsAreNamed:
    """An update deleted ~/.claude/skills/self-hosted and printed nothing."""

    def _report(self, tmp_path):
        from deepctl_core.skill_generator import SkillInstallReport

        root = tmp_path / ".claude" / "skills"
        report = SkillInstallReport(
            ref="v1.8.0",
            skills=[],
            written={"claude": [root / "api"]},
            unsupported=[],
            conflicts=[],
            failures=[],
        )
        report.pruned = {"claude": [root / "self-hosted"]}
        report.pruned_tools = {"claude": "Claude Code"}
        return report

    def _update(self, tmp_path, args=("update",)):
        state = {"installed_skills": {"claude": {"paths": [], "skills_ref": "v1.7.0"}}}
        with (
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch(
                "deepctl_core.skill_generator.install_skills_for",
                return_value=self._report(tmp_path),
            ),
        ):
            return CliRunner().invoke(
                SkillsCommand().get_click_group(), list(args), obj={}
            )

    def test_update_names_each_retired_skill_it_deleted(self, tmp_path):
        result = self._update(tmp_path)

        assert result.exit_code == 0, result.output
        said = " ".join(result.stderr.split())
        assert (
            "Removed retired skill self-hosted from Claude Code "
            "(no longer in deepgram/skills@v1.8.0)" in said
        )
        assert "Removed retired skill" not in result.stdout

    def test_quiet_keeps_it_quiet(self, tmp_path, monkeypatch):
        monkeypatch.setattr(skills_command.get_console(), "quiet", True)
        result = self._update(tmp_path)

        assert result.exit_code == 0, result.output
        assert "Removed retired skill" not in result.output


class TestRemoveSummaryCountsWhatWentAway:
    """`remove --all` against locked files printed "✓ Removed 5 folder(s)"
    for five single files, then exited 1."""

    def _generator(self, tmp_path, report, owned):
        generator = MagicMock()
        generator.cli_name = "claude"
        generator.display_name = "Claude Code"
        generator.skills_root.return_value = tmp_path / ".claude" / "skills"
        generator.owned_skill_paths.return_value = owned
        generator.legacy_locations.return_value = []
        generator.remove_report.return_value = report
        return generator

    def _remove(self, generator, state):
        with (
            patch(
                "deepctl_core.skill_generator.get_all_generators",
                return_value=[generator],
            ),
            patch("deepctl_core.skill_generator.get_skills_state", return_value=state),
            patch("deepctl_core.skill_generator.save_skills_state"),
        ):
            return CliRunner().invoke(
                SkillsCommand().get_click_group(), ["remove", "--all"], obj={}
            )

    def test_skill_folders_and_older_files_are_counted_apart(self, tmp_path):
        skill = tmp_path / ".claude" / "skills" / "api"
        legacy = tmp_path / ".claude" / "commands" / "deepgram"
        report = skill_generator.RemoveReport(removed=[legacy, skill])
        generator = self._generator(tmp_path, report, [skill])
        state = {"installed_skills": {"claude": {"paths": [str(skill)]}}}

        result = self._remove(generator, state)

        assert result.exit_code == 0, result.output
        said = " ".join(result.stderr.split())
        assert (
            # CliRunner is not a terminal, so the agent prefix stands in
            # for the glyph: OK for success, INFO for information.
            "OK: Removed 1 skill folder(s) and 1 older deepctl file(s) from 1 tool(s)."
            in said
        )

    def test_a_zero_count_is_left_out(self, tmp_path):
        legacy = tmp_path / ".claude" / "commands" / "deepgram"
        report = skill_generator.RemoveReport(removed=[legacy])
        generator = self._generator(tmp_path, report, [])
        state = {"installed_skills": {"claude": {"paths": [str(legacy)]}}}

        result = self._remove(generator, state)

        said = " ".join(result.stderr.split())
        assert "Removed 1 older deepctl file(s) from 1 tool(s)." in said
        assert "skill folder(s)" not in said

    def test_a_remove_about_to_fail_does_not_claim_success(self, tmp_path):
        skill = tmp_path / ".claude" / "skills" / "api"
        locked = tmp_path / ".aider.conf.yml"
        locked.write_text("read: []\n")
        report = skill_generator.RemoveReport(
            removed=[skill],
            legacy_skipped=[(locked, "could not be edited")],
            legacy_retryable=[locked],
        )
        generator = self._generator(tmp_path, report, [skill])
        state = {"installed_skills": {"claude": {"paths": [str(skill)]}}}

        result = self._remove(generator, state)

        assert result.exit_code == 1, result.output
        said = " ".join(result.output.split())
        assert "INFO: Removed 1 skill folder(s) from 1 tool(s)." in said
        assert "OK:" not in said and "✓" not in said
        assert "1 older deepctl file(s) could not be removed" in said
        # Marked, so list, status and update read it as a remove.
        entry = state["installed_skills"]["claude"]
        assert entry["remove_pending"] is True
        assert entry["paths"] == [str(locked)]
        assert entry["skills"] == []


class TestAPendingRemovalIsNotAnInstall:
    """Between a failed remove and its retry, `list` showed the leftovers
    as installed skills and hinted 'dg skills update', which put fourteen
    skills back into each tool the user had asked to remove."""

    def _pending_state(self):
        home = Path.home()
        legacy = home / ".claude" / "commands" / "deepgram"
        legacy.mkdir(parents=True)
        files = [legacy / name for name in ("api.md", "docs.md")]
        for path in files:
            path.write_text(f"---\nname: {path.stem}\ndescription: x\n---\n")
        conf = home / ".aider.conf.yml"
        state = {
            "installed_skills": {
                "claude": {
                    "paths": [str(p) for p in files],
                    "skills": [],
                    "skills_ref": "v1.7.0",
                    "version": "0.4.0",
                    "remove_pending": True,
                },
                "aider": {
                    "paths": [str(conf)],
                    "skills": [],
                    "skills_ref": "v1.7.0",
                    "version": "0.4.0",
                    "remove_pending": True,
                },
            }
        }
        state_file = Path(skill_generator._STATE_FILE)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state))
        return legacy, conf

    def _invoke(self, *args):
        return CliRunner().invoke(SkillsCommand().get_click_group(), list(args), obj={})

    def test_list_shows_remove_pending_with_the_real_location(self):
        _legacy, _conf = self._pending_state()

        result = self._invoke("list")

        assert result.exit_code == 0, result.output
        table = result.stdout
        assert table.count("remove pending") == 2
        # _tilde keeps "~/" and renders the rest with the platform separator.
        assert "~/" + str(Path(".claude") / "commands" / "deepgram") in table
        assert "~/.aider.conf.yml" in table
        assert "~/." + " " not in table
        said = " ".join(result.stderr.split())
        assert "run 'dg skills remove --cli claude'" in said
        assert "run 'dg skills remove --cli aider'" in said
        assert "dg skills update" not in said

    def test_list_json_carries_the_flag(self, json_output):
        self._pending_state()

        result = self._invoke("list")

        payload = json.loads(result.stdout)
        rows = {row["cli"]: row for row in payload["installed"]}
        assert rows["claude"]["remove_pending"] is True
        assert rows["claude"]["count"] == 0
        assert rows["aider"]["remove_pending"] is True
        assert rows["aider"]["location"] == str(Path.home() / ".aider.conf.yml")

    def test_status_agrees_with_list(self, json_output):
        self._pending_state()

        result = self._invoke("status")

        payload = json.loads(result.stdout)
        rows = {row["cli"]: row for row in payload["tools"]}
        assert rows["claude"]["remove_pending"] is True
        assert rows["aider"]["remove_pending"] is True
        assert rows["codex"]["remove_pending"] is False

    def test_update_finishes_the_remove_and_reinstalls_nothing(self):
        legacy, _conf = self._pending_state()

        with patch("deepctl_core.skill_generator.fetch_repo_skills") as fetch:
            result = self._invoke("update")

        assert result.exit_code == 0, result.output
        fetch.assert_not_called()
        assert not legacy.exists()
        assert not (Path.home() / ".claude" / "skills").exists()
        said = " ".join(result.stderr.split())
        assert (
            "Claude Code: finished the earlier 'dg skills remove', so no skills "
            "were reinstalled." in said
        )
        # Folders were just deleted: "Nothing to update" said otherwise.
        assert "Nothing to update" not in said
        assert "Finished 2 earlier remove(s). No skills were reinstalled." in said
        records = json.loads(Path(skill_generator._STATE_FILE).read_text())
        assert records["installed_skills"] == {}

    def _pending_and_codex(self):
        """Claude Code and Aider with a remove pending, Codex recorded."""
        legacy, _conf = self._pending_state()
        state_file = Path(skill_generator._STATE_FILE)
        state = json.loads(state_file.read_text())
        codex = Path.home() / ".codex" / "skills" / "api"
        state["installed_skills"]["codex"] = {
            "paths": [str(codex)],
            "skills": ["api"],
            "skills_ref": "v1.7.0",
        }
        state_file.write_text(json.dumps(state))
        return legacy, state_file

    #: What the error says once the pending removes it ran after are done.
    _FINISHED_IN_ERROR = (
        "The earlier 'dg skills remove' for Claude Code and Aider did finish first."
    )

    def test_update_says_the_remove_finished_before_a_fetch_error(self):
        """The fetch error replaced the line, so the user never heard the
        remove they asked for had finished."""
        legacy, state_file = self._pending_and_codex()

        with patch(
            "deepctl_core.skill_generator.fetch_repo_skills",
            side_effect=SkillFetchError("offline"),
        ):
            result = self._invoke("update")

        assert result.exit_code == 1, result.output
        assert not legacy.exists()
        said = " ".join(result.stderr.split())
        assert "offline" in said
        # In the error itself, the one line -q prints, and only there.
        assert said.index("offline") < said.index(self._FINISHED_IN_ERROR)
        assert "finished the earlier 'dg skills remove'" not in said
        records = json.loads(state_file.read_text())
        assert "claude" not in records["installed_skills"]
        assert "codex" in records["installed_skills"]

    def _when_the_remove_is_done(self, monkeypatch, then):
        """Run ``then`` at the first lock taken after Claude Code's record
        is gone, as a second deepctl taking skills.json there would."""
        real_lock = skill_generator.skills_state_lock

        @contextlib.contextmanager
        def lock(*args, **kwargs):
            records = json.loads(Path(skill_generator._STATE_FILE).read_text())
            if "claude" not in records.get("installed_skills", {}):
                then()
            with real_lock(*args, **kwargs):
                yield

        monkeypatch.setattr(skill_generator, "skills_state_lock", lock)

    @pytest.mark.parametrize("quiet", [False, True])
    def test_a_lock_timeout_after_the_remove_does_not_say_nothing_changed(
        self, monkeypatch, quiet
    ):
        """It said "Nothing was changed." straight after finishing the
        remove, and under -q that line was all the user saw."""
        legacy, state_file = self._pending_and_codex()

        def held():
            raise skill_generator.SkillsStateLockTimeout(Path("skills.json.lock"))

        self._when_the_remove_is_done(monkeypatch, held)
        if quiet:
            monkeypatch.setattr(skills_command.get_console(), "quiet", True)

        with patch(
            "deepctl_core.skill_generator.fetch_repo_skills",
            return_value=[RepoSkill(name="api", path=Path("/upstream/api"))],
        ):
            result = self._invoke("update")

        assert result.exit_code == 1, result.output
        assert not legacy.exists()
        said = " ".join(result.stderr.split())
        assert "holds the skill records lock" in said
        assert "Nothing was changed" not in said
        assert "No skills were installed." in said
        assert self._FINISHED_IN_ERROR in said
        records = json.loads(state_file.read_text())
        assert set(records["installed_skills"]) == {"codex"}

    @pytest.mark.parametrize("quiet", [False, True])
    def test_records_damaged_after_the_remove_do_not_say_nothing_changed(
        self, monkeypatch, quiet
    ):
        """The same claim from the re-read that finds the records broken."""
        legacy, state_file = self._pending_and_codex()

        def damage():
            state_file.write_text(json.dumps({"installed_skills": []}))

        self._when_the_remove_is_done(monkeypatch, damage)
        if quiet:
            monkeypatch.setattr(skills_command.get_console(), "quiet", True)

        with patch(
            "deepctl_core.skill_generator.fetch_repo_skills",
            return_value=[RepoSkill(name="api", path=Path("/upstream/api"))],
        ):
            result = self._invoke("update")

        assert result.exit_code == 1, result.output
        assert not legacy.exists()
        said = " ".join(result.stderr.split())
        assert "There is no list of tools to update." in said
        assert "Nothing was changed" not in said
        assert self._FINISHED_IN_ERROR in said

    @pytest.mark.parametrize("quiet", [False, True])
    def test_a_write_failure_after_the_remove_names_it_once(self, quiet):
        """keep_going reported Codex's failure in the exit-1 summary, and
        the finished remove only on an info line, so -q never mentioned
        Claude Code and Aider at all."""
        legacy, state_file = self._pending_and_codex()
        codex_root = skill_generator.CodexGenerator().skills_root()
        if quiet:
            output.update_output(quiet=True)

        with (
            patch(
                "deepctl_core.skill_generator.fetch_repo_skills",
                return_value=[_bundle_skill(Path.home(), "api")],
            ),
            _copy_failing([codex_root]),
        ):
            result = self._invoke("update")

        assert result.exit_code == 1, result.output
        assert not legacy.exists()
        said = " ".join(result.stderr.split())
        assert (
            f"OpenAI Codex: could not write to {codex_root}: Permission denied. "
            "Fix its permissions and run 'dg skills update' again." in said
        )
        summary = (
            "No tool was updated: 1 failed (OpenAI Codex). OpenAI Codex stays "
            "recorded at its previous release. Fix the problem above, then run "
            "'dg skills update' again."
        )
        assert summary in said
        assert said.index(summary) < said.index(self._FINISHED_IN_ERROR)
        assert said.count(self._FINISHED_IN_ERROR) == 1
        assert "finished the earlier 'dg skills remove'" not in said
        assert said.count("Claude Code") == 1
        records = json.loads(state_file.read_text())
        assert set(records["installed_skills"]) == {"codex"}

    def test_update_with_only_a_pending_remove_announces_no_update(self):
        """It printed "Updating to deepgram/skills@..." and then
        "No skills were reinstalled."."""
        self._pending_state()

        with patch("deepctl_core.skill_generator.fetch_repo_skills"):
            result = self._invoke("update")

        assert result.exit_code == 0, result.output
        said = " ".join(result.stderr.split())
        assert "Updating to" not in said
        assert "No skills were reinstalled." in said

    def test_update_leaves_a_still_blocked_remove_recorded(self):
        legacy, _conf = self._pending_state()
        blocked = skill_generator.RemoveReport(
            legacy_skipped=[(legacy / "api.md", "could not be deleted")],
            legacy_retryable=[legacy / "api.md"],
        )

        with (
            patch("deepctl_core.skill_generator.fetch_repo_skills") as fetch,
            patch.object(
                skill_generator.ClaudeCodeGenerator,
                "remove_report",
                return_value=blocked,
            ),
        ):
            result = self._invoke("update")

        assert result.exit_code == 0, result.output
        fetch.assert_not_called()
        said = " ".join(result.stderr.split())
        assert "run 'dg skills remove --cli claude' again." in said
        assert "Nothing to update" not in said
        assert (
            "Finished 1 earlier remove(s); 1 earlier remove(s) still could not "
            "finish. No skills were reinstalled." in said
        )
        entry = json.loads(Path(skill_generator._STATE_FILE).read_text())[
            "installed_skills"
        ]["claude"]
        assert entry["remove_pending"] is True
        assert entry["paths"] == [str(legacy / "api.md")]

    def test_a_failed_install_leaves_list_and_status_agreeing(self, json_output):
        """A failed install over a pending remove kept the old stamp: `list`
        said v1.7.0 and 14 skills, `status` said No."""
        root = Path.home() / ".claude" / "skills"
        stranded = [root / name for name in ("api", "docs")]
        for folder in stranded:
            # What a remove under a read-only root leaves: the folder, emptied.
            folder.mkdir(parents=True)
        state = {
            "installed_skills": {
                "claude": {
                    "paths": [str(p) for p in stranded],
                    "skills": [p.name for p in stranded],
                    "skills_ref": "v1.7.0",
                    "version": "0.4.0",
                    "remove_pending": True,
                }
            }
        }
        state_file = Path(skill_generator._STATE_FILE)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state))
        denied = PermissionError(13, "Permission denied", str(root / ".api.tmp-9"))
        bundle = [_bundle_skill(Path.home(), "api"), _bundle_skill(Path.home(), "docs")]

        with (
            patch(
                "deepctl_core.skill_generator.fetch_repo_skills", return_value=bundle
            ),
            patch.object(
                skill_generator.ClaudeCodeGenerator,
                "install_skills",
                side_effect=denied,
            ),
            patch.object(
                skill_generator.ClaudeCodeGenerator, "detect", return_value=True
            ),
        ):
            failed = self._invoke("install", "--cli", "claude")

        assert failed.exit_code == 1
        listed = {
            row["cli"]: row
            for row in json.loads(self._invoke("list").stdout)["installed"]
        }
        status = {
            row["cli"]: row
            for row in json.loads(self._invoke("status").stdout)["tools"]
        }
        assert listed["claude"]["remove_pending"] is True
        assert status["claude"]["remove_pending"] is True


def _bundle_skill(base, name):
    """One upstream skill folder, the shape the bundle ships."""
    from deepctl_core.skill_bundle import RepoSkill

    folder = base / "bundle" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\n---\n\n# {name}\n")
    return RepoSkill(name=name, path=folder)


#: Fourteen skill names, as many as the pinned bundle ships.
_FOURTEEN = [f"skill-{i:02d}" for i in range(14)]


class TestADeepctl03InstallIsAnInstall:
    """deepctl 0.3.0 and 0.3.1 recorded every install as its paths, a
    timestamp, the version and the commands hash, with no ``skills`` key.
    Read as a pending remove, `update` deleted it and installed nothing."""

    def _v03_home(self):
        legacy = Path.home() / ".claude" / "commands" / "deepgram"
        legacy.mkdir(parents=True)
        files = []
        for name in ("api", "docs", "setup-mcp", "starters"):
            path = legacy / f"{name}.md"
            path.write_text(f"---\nname: {name}\ndescription: x\n---\n")
            files.append(path)
        # Exactly what `dg skills install` and `dg login` wrote in 0.3.x.
        state = {
            "installed_skills": {
                "claude": {
                    "paths": [str(p) for p in files],
                    "installed_at": "2026-05-01T12:00:00.000000+00:00",
                    "version": "0.3.1",
                    "commands_hash": "0123456789abcdef",
                }
            }
        }
        state_file = Path(skill_generator._STATE_FILE)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state))
        return legacy

    def _invoke(self, *args):
        return CliRunner().invoke(SkillsCommand().get_click_group(), list(args), obj={})

    def test_list_shows_it_installed_not_pending(self):
        self._v03_home()

        result = self._invoke("list")

        assert result.exit_code == 0, result.output
        assert "remove pending" not in result.stdout
        assert "0.3.1" in result.stdout
        said = " ".join(result.stderr.split())
        assert "Run 'dg skills update' to reinstall from upstream." in said
        assert "dg skills remove --cli claude" not in said

    def test_list_and_status_json_are_not_pending(self, json_output):
        self._v03_home()

        listed = json.loads(self._invoke("list").stdout)["installed"]
        status = json.loads(self._invoke("status").stdout)["tools"]

        assert [row["remove_pending"] for row in listed] == [False]
        assert listed[0]["deepctl_version"] == "0.3.1"
        assert all(row["remove_pending"] is False for row in status)

    def test_update_upgrades_it_to_the_skill_folders(self):
        legacy = self._v03_home()
        bundle = [_bundle_skill(Path.home(), name) for name in _FOURTEEN]

        with patch(
            "deepctl_core.skill_generator.fetch_repo_skills", return_value=bundle
        ):
            result = self._invoke("update")

        assert result.exit_code == 0, result.output
        root = Path.home() / ".claude" / "skills"
        assert sorted(p.name for p in root.iterdir()) == _FOURTEEN
        assert not legacy.exists()
        said = " ".join(result.stderr.split())
        assert "Updated 1 tool(s)" in said
        assert "finished the earlier" not in said
        entry = json.loads(Path(skill_generator._STATE_FILE).read_text())[
            "installed_skills"
        ]["claude"]
        assert entry["skills"] == _FOURTEEN
        assert "remove_pending" not in entry


class TestAnUnwritableSkillsDirectory:
    """`chmod 555 ~/.claude/skills` printed the raw OSError naming a
    staging folder inside it: `.speech-to-text.tmp-1447`."""

    def _invoke(self, *args):
        root = Path.home() / ".claude" / "skills"
        root.mkdir(parents=True)
        denied = PermissionError(
            13, "Permission denied", str(root / ".speech-to-text.tmp-1447")
        )
        bundle = [_bundle_skill(Path.home(), "speech-to-text")]
        with (
            patch(
                "deepctl_core.skill_generator.fetch_repo_skills", return_value=bundle
            ),
            patch.object(
                skill_generator.ClaudeCodeGenerator,
                "install_skills",
                side_effect=denied,
            ),
            patch.object(
                skill_generator.ClaudeCodeGenerator, "detect", return_value=True
            ),
        ):
            result = CliRunner().invoke(
                SkillsCommand().get_click_group(), list(args), obj={}
            )
        return root, result

    def _assert_names_the_root(self, root, result):
        assert result.exit_code == 1, result.output
        said = " ".join(result.output.split())
        assert (
            f"could not write to {root}: Permission denied. Fix its permissions "
            in said
        )
        assert ".tmp-" not in said
        assert "Errno" not in said

    def test_install_cli_names_the_root(self):
        self._assert_names_the_root(*self._invoke("install", "--cli", "claude"))

    def test_install_all_names_the_root(self):
        self._assert_names_the_root(*self._invoke("install", "--all"))

    def test_update_names_the_root(self):
        state_file = Path(skill_generator._STATE_FILE)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps({"installed_skills": {"claude": {"paths": [], "skills": []}}})
        )
        self._assert_names_the_root(*self._invoke("update"))


def _v03_state(*tools):
    """skills.json as deepctl 0.3.x wrote it, with each tool's legacy file."""
    records = {}
    for cli, path in tools:
        path.parent.mkdir(parents=True, exist_ok=True)
        # A copy of the 0.3.0 `api` skill, which the cleanup recognises.
        path.write_text("---\nname: api\ndescription: x\n---\n")
        records[cli] = {
            "paths": [str(path)],
            "installed_at": "2026-05-01T12:00:00.000000+00:00",
            "version": "0.3.1",
            "commands_hash": "0123456789abcdef",
        }
    state_file = Path(skill_generator._STATE_FILE)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"installed_skills": records}))


def _records():
    return json.loads(Path(skill_generator._STATE_FILE).read_text())["installed_skills"]


def _copy_failing(roots, *, after=0, errno_=13, strerror="Permission denied"):
    """A copytree that fails once ``after`` skills have landed under ``roots``."""
    import shutil

    real = shutil.copytree
    seen = []

    def copytree(src, dst, *args, **kwargs):
        if any(root in Path(dst).parents for root in roots):
            seen.append(dst)
            if len(seen) > after:
                raise OSError(errno_, strerror, str(dst))
        return real(src, dst, *args, **kwargs)

    return patch.object(skill_generator.shutil, "copytree", copytree)


class TestAPartialRecordWithLegacyFiles:
    """A partial record keeps the 0.3.x files, but `list` counted
    deepctl.mdc as a skill and gave ~/.cursor/rules as the location."""

    def _partial_cursor(self):
        legacy = Path.home() / ".cursor" / "rules" / "deepctl.mdc"
        _v03_state(("cursor", legacy))
        root = Path.home() / ".cursor" / "skills"
        bundle = [_bundle_skill(Path.home(), name) for name in _FOURTEEN]
        with (
            patch(
                "deepctl_core.skill_generator.fetch_repo_skills", return_value=bundle
            ),
            _copy_failing(
                [root], after=5, errno_=28, strerror="No space left on device"
            ),
        ):
            result = CliRunner().invoke(
                SkillsCommand().get_click_group(), ["update"], obj={}
            )
        assert result.exit_code == 1, result.output
        return legacy, root

    def test_list_counts_only_skill_folders_at_the_skills_root(self, json_output):
        legacy, root = self._partial_cursor()

        result = CliRunner().invoke(SkillsCommand().get_click_group(), ["list"], obj={})

        assert result.exit_code == 0, result.output
        (row,) = json.loads(result.stdout)["installed"]
        assert row["skills"] == _FOURTEEN[:5]
        assert row["count"] == 5
        assert Path(row["location"]) == root
        assert legacy.exists()
        assert str(legacy) in _records()["cursor"]["paths"]

    def test_list_table_and_status_agree(self):
        _legacy, _root = self._partial_cursor()
        group = SkillsCommand().get_click_group()

        listed = CliRunner().invoke(group, ["list"], obj={})
        output.update_output(format_type="json")
        try:
            status = CliRunner().invoke(group, ["status"], obj={})
        finally:
            output._output_config["format"] = "default"

        assert listed.exit_code == 0, listed.output
        assert "rules" not in listed.stdout
        assert "~/" + str(Path(".cursor") / "skills") in listed.stdout
        cursor = next(
            t for t in json.loads(status.stdout)["tools"] if t["cli"] == "cursor"
        )
        assert cursor["installed"] == 5


class TestAnUnwritableToolDoesNotStopTheOthers:
    """`chmod 555 ~/.claude/skills; dg skills update` failed on Claude
    Code and never tried Cursor, or said that it had not."""

    def _run(self, *args, detected=False):
        claude_legacy = Path.home() / ".claude" / "commands" / "deepgram" / "api.md"
        cursor_legacy = Path.home() / ".cursor" / "rules" / "deepctl.mdc"
        if not detected:
            _v03_state(("claude", claude_legacy), ("cursor", cursor_legacy))
        bundle = [_bundle_skill(Path.home(), name) for name in _FOURTEEN]
        claude_root = Path.home() / ".claude" / "skills"
        with (
            patch(
                "deepctl_core.skill_generator.fetch_repo_skills", return_value=bundle
            ),
            patch(
                "deepctl_core.skill_generator.detect_ai_clis",
                return_value=[
                    skill_generator.ClaudeCodeGenerator(),
                    skill_generator.CursorGenerator(),
                ],
            ),
            _copy_failing([claude_root]),
        ):
            result = CliRunner().invoke(
                SkillsCommand().get_click_group(), list(args), obj={}
            )
        return result, claude_root, claude_legacy

    def _assert_cursor_installed_and_claude_reported(self, result, claude_root, retry):
        assert result.exit_code == 1, result.output
        said = " ".join(result.output.split())
        cursor_root = Path.home() / ".cursor" / "skills"
        assert f"Cursor: 14 skills -> {cursor_root}" in said
        assert (
            f"Claude Code: could not write to {claude_root}: Permission denied. "
            f"Fix its permissions and {retry} again." in said
        )
        assert "1 failed (Claude Code)" in said
        assert sorted(p.name for p in cursor_root.iterdir()) == _FOURTEEN
        records = _records()
        assert records["cursor"]["skills"] == _FOURTEEN
        assert not (Path.home() / ".cursor" / "rules" / "deepctl.mdc").exists()
        return records

    def test_update_attempts_every_tool_and_exits_one(self):
        result, claude_root, claude_legacy = self._run("update")

        records = self._assert_cursor_installed_and_claude_reported(
            result, claude_root, "run 'dg skills update'"
        )
        assert "Updated 1 tool(s)" in " ".join(result.output.split())
        assert claude_legacy.exists()
        assert records["claude"]["paths"] == [str(claude_legacy)]
        assert records["claude"]["version"] == "0.3.1"

    def test_install_all_attempts_every_tool_and_exits_one(self):
        result, claude_root, _legacy = self._run("install", "--all", detected=True)

        records = self._assert_cursor_installed_and_claude_reported(
            result, claude_root, "run the command"
        )
        assert "claude" not in records

    def test_setup_all_attempts_every_tool_and_exits_one(self):
        result, claude_root, _legacy = self._run("setup", "--all", detected=True)

        records = self._assert_cursor_installed_and_claude_reported(
            result, claude_root, "run the command"
        )
        assert "claude" not in records

    def test_install_cli_still_fails_with_the_one_message(self):
        claude_root = Path.home() / ".claude" / "skills"
        bundle = [_bundle_skill(Path.home(), name) for name in _FOURTEEN]
        with (
            patch(
                "deepctl_core.skill_generator.fetch_repo_skills", return_value=bundle
            ),
            patch.object(
                skill_generator.ClaudeCodeGenerator, "detect", return_value=True
            ),
            _copy_failing([claude_root]),
        ):
            result = CliRunner().invoke(
                SkillsCommand().get_click_group(),
                ["install", "--cli", "claude"],
                obj={},
            )

        assert result.exit_code == 1, result.output
        said = " ".join(result.output.split())
        assert (
            f"Error: could not write to {claude_root}: Permission denied. Fix its "
            "permissions and run the command again." in said
        )
        assert "failed (" not in said


class TestEveryFailedToolIsNamedAndSummedUpTruthfully:
    """Under -q a multi-tool failure printed only "Fix the problem above"
    with nothing above it, and the summary said a tool "stays recorded"
    when it never had a record, or "Updated 0 tool(s)"."""

    def _run(self, args, locked, recorded=(), *, quiet=False):
        claude_root = Path.home() / ".claude" / "skills"
        cursor_root = Path.home() / ".cursor" / "skills"
        roots = {"claude": claude_root, "cursor": cursor_root}
        legacy = {
            "claude": Path.home() / ".claude" / "commands" / "deepgram" / "api.md",
            "cursor": Path.home() / ".cursor" / "rules" / "deepctl.mdc",
        }
        if recorded:
            _v03_state(*((cli, legacy[cli]) for cli in recorded))
        bundle = [_bundle_skill(Path.home(), name) for name in _FOURTEEN]
        if quiet:
            output.update_output(quiet=True)
        with (
            patch(
                "deepctl_core.skill_generator.fetch_repo_skills", return_value=bundle
            ),
            patch(
                "deepctl_core.skill_generator.detect_ai_clis",
                return_value=[
                    skill_generator.ClaudeCodeGenerator(),
                    skill_generator.CursorGenerator(),
                ],
            ),
            _copy_failing([roots[cli] for cli in locked]),
        ):
            result = CliRunner().invoke(
                SkillsCommand().get_click_group(), list(args), obj={}
            )
        assert result.exit_code == 1, result.output
        return result, roots

    @staticmethod
    def _flat(text):
        return " ".join(text.split())

    def test_quiet_update_names_each_failed_tool_on_stderr(self):
        result, roots = self._run(
            ["update"], ["claude", "cursor"], ["claude", "cursor"], quiet=True
        )

        said = self._flat(result.stderr)
        for name, cli in (("Claude Code", "claude"), ("Cursor", "cursor")):
            assert (
                f"{name}: could not write to {roots[cli]}: Permission denied. "
                "Fix its permissions and run 'dg skills update' again." in said
            )
        assert (
            "No tool was updated: 2 failed (Claude Code, Cursor). Claude Code "
            "and Cursor stay recorded at their previous release. Fix the "
            "problem above, then run 'dg skills update' again." in said
        )
        assert "Updating to" not in said

    def test_quiet_json_install_all_keeps_stdout_empty(self, json_output):
        result, roots = self._run(["install", "--all"], ["claude"], quiet=True)

        assert result.stdout == ""
        said = self._flat(result.stderr)
        assert (
            f"Claude Code: could not write to {roots['claude']}: Permission "
            "denied." in said
        )
        assert "Installed skills for 1 tool(s), 1 failed (Claude Code). Fix" in said
        assert "stays recorded" not in said
        # -q still silences the chrome around the error.
        assert "Cursor: 14 skills" not in said

    def test_each_failure_line_prints_once_without_quiet(self):
        result, roots = self._run(["update"], ["claude"], ["claude", "cursor"])

        said = self._flat(result.output)
        assert said.count(f"could not write to {roots['claude']}") == 1
        assert said.count("1 failed (Claude Code)") == 1
        assert f"Cursor: 14 skills -> {roots['cursor']}" in said
        assert "Updated 1 tool(s) to deepgram/skills@" in said
        assert "Claude Code stays recorded at its previous release." in said

    def test_install_all_with_every_root_locked_says_none_was_installed(self):
        result, _roots = self._run(["install", "--all"], ["claude", "cursor"])

        said = self._flat(result.output)
        assert (
            "Error: No tool was installed: 2 failed (Claude Code, Cursor). Fix "
            "the problem above, then run the same command again." in said
        )
        assert "0 tool(s)" not in said
        assert "recorded" not in said

    def test_setup_all_with_every_root_locked_says_none_was_set_up(self):
        result, _roots = self._run(["setup", "--all"], ["claude", "cursor"])

        said = self._flat(result.output)
        assert (
            "Error: No tool was set up: 2 failed (Claude Code, Cursor). Fix "
            "the problem above, then run the same command again." in said
        )
        assert "recorded" not in said

    def test_only_the_previously_recorded_tool_stays_recorded(self):
        result, _roots = self._run(
            ["install", "--all"], ["claude", "cursor"], ["claude"]
        )

        said = self._flat(result.output)
        assert (
            "No tool was installed: 2 failed (Claude Code, Cursor). Claude "
            "Code stays recorded at its previous release. Fix" in said
        )
        assert "Cursor stays" not in said
