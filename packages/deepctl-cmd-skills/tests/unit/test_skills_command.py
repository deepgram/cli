"""Unit tests for skills command."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest
from deepctl_cmd_skills.command import SkillsCommand
from deepctl_core.skill_bundle import DEFAULT_SKILLS_REF, RepoSkill, SkillFetchError


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
            patch.object(cmd, "confirm", return_value=False),
        ):
            with pytest.raises(click.Abort):
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
        generator.install_skills.return_value = [root / "api"]

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
            patch.object(cmd, "confirm", return_value=True),
        ):
            cmd._handle_install(cli_name="claude")

        generator.install_skills.assert_called_once()

    def test_unknown_cli_exits_one(self):
        """An unknown --cli must exit 1, not print an error and exit 0."""
        cmd = SkillsCommand()
        with patch(
            "deepctl_core.skill_generator.get_all_generators",
            return_value=[],
        ):
            with pytest.raises(click.ClickException) as exc:
                cmd._handle_install(cli_name="nonexistent-cli")

        assert "Unknown AI CLI: nonexistent-cli" in str(exc.value)

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
                return_value=[],
            ),
        ):
            with pytest.raises(click.ClickException) as exc:
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
        with patch(
            "deepctl_core.skill_generator.fetch_repo_skills",
            side_effect=SkillFetchError(message),
        ):
            with pytest.raises(click.ClickException) as excinfo:
                cmd._fetch_skills(None)
        rendered = str(excinfo.value)
        assert message in rendered
        assert "No skills were installed" in rendered

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
        ):
            with pytest.raises(click.ClickException):
                cmd._handle_install(install_all=True)

        generator.install_skills.assert_not_called()
        save.assert_not_called()


class TestInstallRecordsWhatItDid:
    def test_state_records_the_ref_and_every_skill(self, tmp_path):
        cmd = SkillsCommand()
        generator = MagicMock()
        generator.cli_name = "claude"
        generator.display_name = "Claude Code"
        generator.detect.return_value = True
        root = tmp_path / ".claude" / "skills"
        generator.skills_root.return_value = root
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
