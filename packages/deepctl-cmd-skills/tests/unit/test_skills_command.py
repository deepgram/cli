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
        generator.install_conflicts.return_value = []
        generator.install_skills.return_value = [root / "api"]
        generator.prune_retired.return_value = []

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
        generator.remove.return_value = []
        generator.prune_retired.return_value = []
        generator.installed_skill_paths.return_value = []
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
        ):
            with pytest.raises(click.ClickException) as excinfo:
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

        generator.remove.assert_called_once_with(recorded)
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

        generator.remove.assert_not_called()
        save.assert_not_called()
        captured = capsys.readouterr()
        assert "by hand" in " ".join((captured.out + captured.err).split())

    def test_a_failed_deletion_keeps_the_record_and_can_be_retried(
        self, tmp_path, capsys
    ):
        """Ownership has to outlive an rmtree that could not delete.

        `SkillGenerator.remove()` asks the filesystem before reporting a
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
        ):
            # A remove that could not remove is a failed command: the
            # README documents exit 1 for that, and exiting 0 is how the
            # user would never learn the folders are still there.
            with pytest.raises(click.ClickException) as excinfo:
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
        ):
            with pytest.raises(click.ClickException):
                cmd._handle_remove(remove_all=True)

        rendered = " ".join(capsys.readouterr().err.split())
        assert "could not be removed" in rendered
        assert "no longer deepctl's" not in rendered
        # Still recorded, so the retry the message asks for can find it.
        assert state["installed_skills"]["claude"]["paths"] == [
            str(root / "api")
        ]

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
        ):
            with pytest.raises(click.UsageError) as excinfo:
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
        ):
            cmd._handle_remove(remove_all=True)

        save.assert_not_called()
        assert "by hand" in " ".join(capsys.readouterr().err.split())

    def test_remove_does_not_claim_it_deleted_a_file_it_only_edited(
        self, tmp_path, capsys
    ):
        """clean_legacy cuts deepctl's section out of files users own.

        Those files are still on disk afterwards, so reporting them as
        removed points the user at something they can still see -- and
        counted them towards the "Removed N folder(s)" total.
        """
        cmd = SkillsCommand()
        generator, root = self._generator(tmp_path)
        shared = tmp_path / "GEMINI.md"
        shared.write_text("my own notes\n")
        generator.remove.return_value = [shared]
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

        combined = " ".join(
            (lambda c: c.out + c.err)(capsys.readouterr()).split()
        )
        assert "Cleaned deepctl's section out of" in combined
        assert "Removed 1 folder(s)" not in combined
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

        with patch(
            "deepctl_core.skill_generator.get_skills_state", return_value=state
        ):
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
        assert "No skills installed" in " ".join(
            (combined.out + combined.err).split()
        )

    def test_setup_without_a_tty_installs_for_everything_detected(self, tmp_path):
        """CI has no prompt to answer, so setup must not wait for one."""
        cmd = SkillsCommand()
        cmd._guided = False
        generator, root = self._generator(tmp_path)
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
        # And the table shows that count, not a directory listing.
        assert "1" in " ".join(capsys.readouterr().out.split())


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
