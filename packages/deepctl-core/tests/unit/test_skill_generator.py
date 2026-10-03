"""Unit tests for skill generator module."""

import copy
import json
import multiprocessing
import os
import pickle
import re
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest
from deepctl_core import skill_generator
from deepctl_core.skill_bundle import DEFAULT_SKILLS_REF, SkillFetchError
from deepctl_core.skill_generator import (
    AiderGenerator,
    AmazonQGenerator,
    ClaudeCodeGenerator,
    ClineGenerator,
    CodexGenerator,
    CommandMetadata,
    CursorGenerator,
    GeminiGenerator,
    LegacyArtifact,
    RemoveReport,
    SkillOwnershipError,
    SkillsStateError,
    SkillsStateLockTimeout,
    _commands_hash,
    collect_command_metadata,
    detect_ai_clis,
    get_all_generators,
    get_skills_state,
    recorded_skill_paths,
    render_developer_guide,
    save_skills_state,
    skills_need_update,
    skills_state_lock,
)

#: Set by the autouse fixture below for the duration of each test. The guard
#: test reads it from here rather than requesting the fixture, so it fails if
#: the fixture ever stops being autouse.
_ACTIVE_THROWAWAY_HOME: Path | None = None


@pytest.fixture(autouse=True)
def _throwaway_home(tmp_path, monkeypatch):
    """Point every home-derived path in this module at a throwaway directory.

    Nothing here may read or write the home of whoever is running pytest.
    Two routes reach it and neither is obvious at the call site:

    * ``install_skills()`` runs ``clean_legacy()``, which resolves
      ``Path.home()`` when it is called, so patching only ``skills_root``
      still let six tests delete the real
      ``~/.claude/commands/deepgram/*.md``.
    * ``_SKILLS_DIR``, ``_STATE_FILE``, ``_REPO_CACHE_DIR`` and
      ``AiderGenerator._LEGACY_FILE`` are evaluated at import time, so they
      keep pointing at the real home however ``Path.home`` is patched.
    """
    home = tmp_path / "throwaway-home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

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


def test_no_test_in_this_module_can_reach_the_real_home():
    """The guard above is the finding, so it gets its own assertion."""
    home = _ACTIVE_THROWAWAY_HOME
    assert home is not None, "the throwaway-home fixture is no longer autouse"
    assert Path.home() == home
    assert skill_generator._STATE_FILE.is_relative_to(home)
    assert skill_generator._REPO_CACHE_DIR.is_relative_to(home)
    # The accessors the module actually reads through, lock file included.
    assert skill_generator.skills_state_file().is_relative_to(home)
    assert skill_generator._repo_cache_dir().is_relative_to(home)
    assert skill_generator._lock_file().is_relative_to(home)
    assert AiderGenerator._LEGACY_FILE.is_relative_to(home)
    assert AiderGenerator._config_path().is_relative_to(home)
    for gen in get_all_generators():
        for artifact in gen.legacy_paths():
            assert artifact.path.is_relative_to(home), gen.cli_name
        root = gen.skills_root()
        assert root is None or root.is_relative_to(home), gen.cli_name


def _make_command(**overrides):
    """Create a CommandMetadata with sensible defaults."""
    defaults = {
        "name": "test",
        "full_command": "deepctl test",
        "help": "A test command",
        "agent_help": "Test agent help",
        "requires_auth": False,
        "ci_friendly": True,
        "examples": ["dg test foo"],
        "arguments": [],
        "is_group": False,
        "parent_group": None,
        "source": "builtin",
    }
    defaults.update(overrides)
    return CommandMetadata(**defaults)


class TestCommandMetadata:
    """Test CommandMetadata dataclass."""

    def test_create(self):
        cmd = _make_command()
        assert cmd.name == "test"
        assert cmd.full_command == "deepctl test"
        assert cmd.examples == ["dg test foo"]

    def test_create_with_parent_group(self):
        cmd = _make_command(
            name="audio", parent_group="debug", full_command="deepctl debug audio"
        )
        assert cmd.parent_group == "debug"
        assert cmd.full_command == "deepctl debug audio"


class TestCommandsHash:
    """Test _commands_hash."""

    def test_deterministic(self):
        cmds = [
            _make_command(),
            _make_command(name="other", full_command="deepctl other"),
        ]
        h1 = _commands_hash(cmds)
        h2 = _commands_hash(cmds)
        assert h1 == h2
        assert h1.startswith("sha256:")

    def test_changes_when_commands_differ(self):
        cmds1 = [_make_command()]
        cmds2 = [_make_command(help="Different help")]
        assert _commands_hash(cmds1) != _commands_hash(cmds2)

    def test_order_independent(self):
        a = _make_command(name="a", full_command="deepctl a")
        b = _make_command(name="b", full_command="deepctl b")
        assert _commands_hash([a, b]) == _commands_hash([b, a])


class TestSkillsState:
    """Test state management functions."""

    def test_get_skills_state_missing_file(self, tmp_path):
        with patch("deepctl_core.skill_generator._STATE_FILE", tmp_path / "nope.json"):
            state = get_skills_state()
            assert state == {"installed_skills": {}, "auto_update": True}

    def test_save_and_get_skills_state(self, tmp_path):
        state_file = tmp_path / "skills.json"
        with (
            patch("deepctl_core.skill_generator._STATE_FILE", state_file),
            patch("deepctl_core.skill_generator._SKILLS_DIR", tmp_path),
        ):
            save_skills_state(
                {"installed_skills": {"claude": {}}, "auto_update": False}
            )
            result = get_skills_state()
            assert result["installed_skills"] == {"claude": {}}
            assert result["auto_update"] is False

    def test_state_file_follows_path_home_when_the_constants_are_untouched(
        self, monkeypatch
    ):
        """Evaluated at call time, not import time, unless a test patched it."""
        base = skill_generator._IMPORT_TIME_SKILLS_DIR
        monkeypatch.setattr(skill_generator, "_SKILLS_DIR", base)
        monkeypatch.setattr(skill_generator, "_STATE_FILE", base / "skills.json")
        monkeypatch.setattr(skill_generator, "_REPO_CACHE_DIR", base / "repo_cache")
        # Path.home() is the throwaway home here, courtesy of the fixture.
        home = Path.home()
        assert skill_generator.skills_state_file() == (
            home / ".deepctl" / "skills" / "skills.json"
        )
        assert skill_generator._repo_cache_dir() == (
            home / ".deepctl" / "skills" / "repo_cache"
        )

    def test_a_crash_before_the_rename_leaves_the_old_file(self, tmp_path):
        """Written to a sibling and renamed over, never truncated in place."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        state_file = state_dir / "skills.json"
        state_file.write_text('{"installed_skills": {"claude": {}}}')
        with (
            patch("deepctl_core.skill_generator._STATE_FILE", state_file),
            patch.object(
                skill_generator.os,
                "replace",
                side_effect=OSError(28, "No space left on device"),
            ),
            pytest.raises(OSError),
        ):
            save_skills_state({"installed_skills": {}})

        assert json.loads(state_file.read_text()) == {
            "installed_skills": {"claude": {}}
        }
        # And the temporary file did not stay behind.
        assert [p.name for p in state_dir.iterdir()] == ["skills.json"]

    def test_a_corrupt_state_file_raises_instead_of_resetting(self, tmp_path):
        """Returning the empty default here is how a record gets lost."""
        state_file = tmp_path / "skills.json"
        state_file.write_text('{"installed_skills": {"claude": ')
        with (
            patch("deepctl_core.skill_generator._STATE_FILE", state_file),
            pytest.raises(SkillsStateError) as excinfo,
        ):
            get_skills_state()
        message = str(excinfo.value)
        assert str(state_file) in message
        assert "move it aside" in message

    def test_a_corrupt_state_file_is_not_overwritten_by_a_save(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        state_file = state_dir / "skills.json"
        state_file.write_text("not json at all")
        with (
            patch("deepctl_core.skill_generator._STATE_FILE", state_file),
            pytest.raises(SkillsStateError),
        ):
            save_skills_state({"installed_skills": {}, "auto_update": True})
        assert state_file.read_text() == "not json at all"
        assert [p.name for p in state_dir.iterdir()] == ["skills.json"]

    @pytest.mark.parametrize("payload", ["[]", '"skills"', "42", "null"])
    def test_json_that_is_not_an_object_raises(self, tmp_path, payload):
        state_file = tmp_path / "skills.json"
        state_file.write_text(payload)
        with patch("deepctl_core.skill_generator._STATE_FILE", state_file):
            with pytest.raises(SkillsStateError, match="not an object"):
                get_skills_state()
            with pytest.raises(SkillsStateError):
                save_skills_state({"installed_skills": {}})
        assert state_file.read_text() == payload

    def test_an_unreadable_state_file_raises(self, tmp_path):
        """Other OSErrors are not 'missing' and must not look like it."""
        state_file = tmp_path / "skills.json"
        state_file.mkdir()  # reading a directory is an OSError, not absence
        with (
            patch("deepctl_core.skill_generator._STATE_FILE", state_file),
            pytest.raises(SkillsStateError, match="Cannot read"),
        ):
            get_skills_state()

    def test_skills_need_update_no_installed(self):
        with patch(
            "deepctl_core.skill_generator.get_skills_state",
            return_value={"installed_skills": {}},
        ):
            assert skills_need_update([_make_command()]) is False

    def test_skills_need_update_stale_hash(self):
        state = {"installed_skills": {"claude": {"commands_hash": "sha256:old"}}}
        with patch("deepctl_core.skill_generator.get_skills_state", return_value=state):
            assert skills_need_update([_make_command()]) is True


class TestRenderDeveloperGuide:
    """Test render_developer_guide."""

    def test_basic_render(self):
        content = render_developer_guide("1.0.0")
        assert "# Deepgram Developer Guide" in content
        assert "v1.0.0" in content
        assert "Authentication" in content

    def test_contains_stt_content(self):
        content = render_developer_guide("1.0.0")
        assert "Speech-to-Text" in content
        assert "Nova-3" in content
        assert "diarize" in content
        assert "smart_format" in content

    def test_contains_tts_content(self):
        content = render_developer_guide("1.0.0")
        assert "Text-to-Speech" in content
        assert "Aura-2" in content
        assert "Aura and Flux voices" in content
        assert "aura-2-andromeda-en" in content
        assert "`expressivity` is beta" in content
        assert "defaults to `0`" in content

    def test_contains_audio_intelligence(self):
        content = render_developer_guide("1.0.0")
        assert "Audio Intelligence" in content
        assert "summarize" in content
        assert "sentiment" in content

    def test_contains_voice_agent(self):
        content = render_developer_guide("1.0.0")
        assert "Voice Agent" in content
        assert "barge-in" in content.lower() or "Barge-in" in content

    def test_contains_sdks(self):
        content = render_developer_guide("1.0.0")
        assert "deepgram-sdk" in content
        assert "@deepgram/sdk" in content
        assert "pip install" in content
        assert "npm install" in content

    def test_contains_resources(self):
        content = render_developer_guide("1.0.0")
        # Matched as whole URLs, not substrings: a hostname anywhere in
        # the text (``evil-developers.deepgram.com.example``) is not a link.
        assert re.search(r"<https://developers\.deepgram\.com[/>]", content)
        assert re.search(r"<https://console\.deepgram\.com[/>]", content)
        assert re.search(r"https://discord\.gg/deepgram\b", content)
        assert re.search(r"\(https://github\.com/deepgram/[\w-]+\)", content)

    def test_contains_mcp_server(self):
        content = render_developer_guide("1.0.0")
        assert "MCP" in content
        assert '"dg"' in content or "'dg'" in content
        assert "mcpServers" in content

    def test_contains_cli_section(self):
        content = render_developer_guide("1.0.0")
        assert "deepctl CLI" in content
        assert "dg listen" in content
        assert "dg login" in content

    def test_frontmatter(self):
        content = render_developer_guide("1.0.0", include_frontmatter=True)
        assert content.startswith("---\n")
        assert "description:" in content

    def test_no_frontmatter_by_default(self):
        content = render_developer_guide("1.0.0")
        assert not content.startswith("---")


def _fake_skill(tmp_path, name, references=()):
    """Build a skill folder the way the upstream bundle ships one."""
    from deepctl_core.skill_bundle import RepoSkill

    skill_dir = tmp_path / "bundle" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"---\nname: {name}\n---\n\n# {name}\n")
    for ref in references:
        refs = skill_dir / "references"
        refs.mkdir(exist_ok=True)
        (refs / ref).write_text(f"# {ref}\n")
    return RepoSkill(name=name, path=skill_dir)


class TestSkillsRoots:
    """Every destination is the directory the tool's own docs name."""

    EXPECTED: ClassVar[dict[str, Path]] = {
        "claude": Path(".claude") / "skills",
        # Codex documents only the cross-tool location; ~/.codex/skills is
        # marked deprecated in Codex's own source.
        "codex": Path(".agents") / "skills",
        "gemini": Path(".gemini") / "skills",
        "cursor": Path(".cursor") / "skills",
        "opencode": Path(".config") / "opencode" / "skills",
        "cline": Path(".cline") / "skills",
    }

    # Neither tool has a skills mechanism to install into.
    NO_SKILLS_DIRECTORY: ClassVar[set[str]] = {"amazonq", "aider"}

    def test_each_generator_targets_its_documented_directory(self):
        by_name = {g.cli_name: g for g in get_all_generators()}
        for cli_name, expected in self.EXPECTED.items():
            root = by_name[cli_name].skills_root()
            assert root == Path.home() / expected, cli_name

    def test_tools_without_a_skills_directory_install_nothing(self):
        by_name = {g.cli_name: g for g in get_all_generators()}
        for cli_name in self.NO_SKILLS_DIRECTORY:
            gen = by_name[cli_name]
            assert gen.skills_root() is None
            # legacy_paths is patched out: the install cleans them, and
            # for these two that edits ~/.amazonq and ~/.aider.conf.yml.
            # The fetch must never run for a tool with nowhere to write.
            with patch.object(gen, "legacy_paths", return_value=[]):
                report = skill_generator.install_skills_for(
                    [gen],
                    {"installed_skills": {}},
                    commands=[_make_command()],
                    version="1.0.0",
                    fetch=lambda: pytest.fail("fetched for an unsupported tool"),
                )
            assert report.unsupported == [gen]
            assert report.written == {}
            hint = gen.manual_hint()
            assert hint and "npx skills add deepgram/skills" in hint

    def test_no_generator_writes_a_slash_command_or_rules_file(self):
        """The old destinations were commands/ and rules/ files, not skills."""
        for gen in get_all_generators():
            root = gen.skills_root()
            if root is None:
                continue
            assert root.name == "skills", gen.cli_name
            assert "commands" not in root.parts, gen.cli_name
            assert "rules" not in root.parts, gen.cli_name


class TestInstallSkills:
    """Installing copies whole skill folders, references and all."""

    def _install(self, tmp_path, skills):
        gen = ClaudeCodeGenerator()
        root = tmp_path / "home" / ".claude" / "skills"
        with patch.object(gen, "skills_root", return_value=root):
            return gen, root, gen.install_skills(skills)

    def test_writes_one_directory_per_skill(self, tmp_path):
        skills = [_fake_skill(tmp_path, n) for n in ("api", "docs", "cli")]
        _, _root, written = self._install(tmp_path, skills)
        assert len(written) == 3
        assert {p.name for p in written} == {"api", "docs", "cli"}
        for path in written:
            assert (path / "SKILL.md").is_file()

    def test_preserves_reference_subdirectories(self, tmp_path):
        """skills/api and skills/self-hosted ship a references/ folder."""
        skills = [
            _fake_skill(tmp_path, "api", references=("listen.md", "speak.md")),
            _fake_skill(tmp_path, "docs"),
        ]
        _, root, _ = self._install(tmp_path, skills)
        refs = root / "api" / "references"
        assert refs.is_dir()
        assert sorted(p.name for p in refs.iterdir()) == ["listen.md", "speak.md"]

    def test_reinstall_drops_files_that_disappeared_upstream(self, tmp_path):
        skills = [_fake_skill(tmp_path, "api", references=("old.md",))]
        gen, root, written = self._install(tmp_path, skills)
        assert (root / "api" / "references" / "old.md").is_file()

        fresh = tmp_path / "bundle2"
        (fresh / "api").mkdir(parents=True)
        (fresh / "api" / "SKILL.md").write_text("---\nname: api\n---\n")
        from deepctl_core.skill_bundle import RepoSkill

        with patch.object(gen, "skills_root", return_value=root):
            gen.install_skills([RepoSkill(name="api", path=fresh / "api")], written)
        assert not (root / "api" / "references").exists()

    def test_frontmatter_survives_verbatim(self, tmp_path):
        skills = [_fake_skill(tmp_path, "api")]
        _, root, _ = self._install(tmp_path, skills)
        assert (root / "api" / "SKILL.md").read_text().startswith("---\nname: api")

    def test_installed_skill_paths_reports_what_deepctl_installed(self, tmp_path):
        skills = [_fake_skill(tmp_path, n) for n in ("api", "docs")]
        gen, root, written = self._install(tmp_path, skills)
        with patch.object(gen, "skills_root", return_value=root):
            assert [p.name for p in gen.installed_skill_paths(written)] == [
                "api",
                "docs",
            ]
            # A folder nobody recorded is not reported, even though it
            # sits in the same directory and has a SKILL.md of its own.
            (root / "mine").mkdir()
            (root / "mine" / "SKILL.md").write_text("---\nname: mine\n---\n")
            assert [p.name for p in gen.installed_skill_paths(written)] == [
                "api",
                "docs",
            ]

    def test_remove_deletes_every_installed_skill(self, tmp_path):
        skills = [_fake_skill(tmp_path, n) for n in ("api", "docs")]
        gen, root, written = self._install(tmp_path, skills)
        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
        ):
            removed = gen.remove_report(written).removed
            assert len(removed) == 2
            assert gen.installed_skill_paths(written) == []
        # Every skill folder is gone; the shared directory they sat in
        # stays, because deepctl does not own that either.
        assert sorted(p.name for p in root.iterdir()) == []

    def test_install_propagates_a_fetch_failure(self, tmp_path):
        """A partial install must never look like a complete one."""
        gen = ClaudeCodeGenerator()
        state: dict = {"installed_skills": {}}
        with (
            patch.object(gen, "skills_root", return_value=tmp_path / "skills"),
            patch(
                "deepctl_core.skill_generator.fetch_repo_skills",
                side_effect=SkillFetchError("no network"),
            ),
            pytest.raises(SkillFetchError),
        ):
            skill_generator.install_skills_for(
                [gen], state, commands=[_make_command()], version="1.0.0"
            )
        assert not (tmp_path / "skills").exists()
        assert state == {"installed_skills": {}}


class TestOwnership:
    """These skills directories are shared. deepctl touches only its own.

    ``~/.claude/skills`` and every other destination here hold skills from
    the user and from other publishers. Treating each child folder with a
    ``SKILL.md`` as deepctl's made ``dg skills remove`` delete a
    developer's unrelated work and let an install overwrite it.
    """

    def _gen(self, tmp_path):
        gen = ClaudeCodeGenerator()
        root = tmp_path / "home" / ".claude" / "skills"
        root.mkdir(parents=True)
        return gen, root

    def _unrelated(self, root, name="my-private-skill"):
        """A skill folder somebody other than deepctl put there."""
        folder = root / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(f"---\nname: {name}\n---\n\nmine\n")
        return folder

    # -- remove ------------------------------------------------------

    def test_remove_preserves_an_unrelated_skill(self, tmp_path):
        """The reported bug: `dg skills remove` erased user-owned folders."""
        gen, root = self._gen(tmp_path)
        mine = self._unrelated(root)
        skills = [_fake_skill(tmp_path, n) for n in ("api", "docs")]
        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
        ):
            written = gen.install_skills(skills)
            removed = gen.remove_report(written).removed

        assert sorted(p.name for p in removed) == ["api", "docs"]
        assert mine.is_dir()
        assert (mine / "SKILL.md").read_text().endswith("mine\n")

    def test_remove_without_a_record_deletes_nothing(self, tmp_path):
        """A hand-deleted skills.json leaves deepctl unable to prove ownership."""
        gen, root = self._gen(tmp_path)
        mine = self._unrelated(root)
        skills = [_fake_skill(tmp_path, "api")]
        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
        ):
            gen.install_skills(skills)
            assert gen.remove_report([]).removed == []

        assert (root / "api" / "SKILL.md").is_file()
        assert mine.is_dir()

    def test_remove_ignores_a_recorded_path_outside_the_skills_root(self, tmp_path):
        """A tampered or stale skills.json cannot aim a delete elsewhere."""
        gen, root = self._gen(tmp_path)
        elsewhere = tmp_path / "home" / "Documents" / "thesis"
        elsewhere.mkdir(parents=True)
        (elsewhere / "chapter-1.md").write_text("years of work")
        nested = root / "api" / "references"
        nested.mkdir(parents=True)

        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
        ):
            removed = gen.remove_report(
                [
                    str(elsewhere),
                    str(root / ".." / ".." / "Documents"),
                    str(nested),  # a grandchild, not a direct child
                    "relative/path",
                ]
            ).removed

        assert removed == []
        assert (elsewhere / "chapter-1.md").is_file()
        assert nested.is_dir()

    def test_remove_ignores_a_recorded_path_that_became_a_symlink(self, tmp_path):
        """Resolve first: a symlinked name must not delete its target."""
        gen, root = self._gen(tmp_path)
        target = tmp_path / "home" / "real-work"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("---\nname: api\n---\n")
        link = root / "api"
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):  # unprivileged Windows
            pytest.skip("this filesystem does not allow creating symlinks")

        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
        ):
            assert gen.remove_report([str(link)]).removed == []

        assert target.is_dir()
        assert (target / "SKILL.md").is_file()

    def test_remove_clears_a_recorded_path_someone_replaced_with_a_file(self, tmp_path):
        """Skipping it left a record no retry could ever clear.

        install already unlinks a plain file standing at a recorded
        destination, so remove has to be able to finish the same job --
        otherwise ownership outliving a failed delete means the warning
        repeats forever with no action that resolves it.
        """
        gen, root = self._gen(tmp_path)
        stray = root / "api"
        stray.write_text("not a skill folder\n")

        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
        ):
            removed = gen.remove_report([str(stray)]).removed

        assert removed == [stray]
        assert not stray.exists()

    def test_remove_reports_nothing_for_a_folder_it_could_not_delete(self, tmp_path):
        """The caller drops the record on the strength of this list."""
        gen, root = self._gen(tmp_path)
        skills = [_fake_skill(tmp_path, "api")]

        def denied(path, ignore_errors=False, **kwargs):
            """What rmtree(ignore_errors=True) does on a read-only mount."""
            if not ignore_errors:
                raise PermissionError(13, "Permission denied", str(path))

        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
        ):
            written = gen.install_skills(skills)
            with patch.object(skill_generator.shutil, "rmtree", denied):
                assert gen.remove_report(written).removed == []

        assert (root / "api" / "SKILL.md").is_file()

    def test_remove_report_names_a_folder_it_could_not_delete(self, tmp_path):
        """removed leaves it out; stranded says it is still there."""
        gen, root = self._gen(tmp_path)
        skills = [_fake_skill(tmp_path, n) for n in ("api", "docs")]
        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
        ):
            written = gen.install_skills(skills)
            real_rmtree = shutil.rmtree

            def rmtree(path, *args, **kwargs):
                if Path(path).name == "docs":
                    return  # survives, as a read-only mount would leave it
                return real_rmtree(path, *args, **kwargs)

            with patch.object(skill_generator.shutil, "rmtree", rmtree):
                report = gen.remove_report(written)

        assert isinstance(report, RemoveReport)
        assert report.removed == [root / "api"]
        assert report.stranded == [root / "docs"]
        assert report.legacy_skipped == []
        assert (root / "docs" / "SKILL.md").is_file()

    def test_remove_report_names_the_legacy_file_it_left_alone(self, tmp_path):
        """A skipped legacy path reaches the caller as (path, reason)."""
        gen, root = self._gen(tmp_path)
        rules = tmp_path / "deepctl.mdc"
        rules.write_text("mine\n")
        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
        ):
            written = gen.install_skills([_fake_skill(tmp_path, "api")])
        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[LegacyArtifact(rules)]),
        ):
            report = gen.remove_report(written)

        assert report.removed == [root / "api"]
        assert report.stranded == []
        assert report.legacy_skipped == [(rules, skill_generator._NOT_DEEPCTLS)]
        assert rules.read_text() == "mine\n"

    def test_remove_report_strands_a_recorded_file_it_could_not_unlink(self, tmp_path):
        """A plain file at a recorded path that will not go is stranded, not removed."""
        gen, root = self._gen(tmp_path)
        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
        ):
            written = gen.install_skills(
                [_fake_skill(tmp_path, n) for n in ("api", "docs")]
            )
            shutil.rmtree(root / "docs")
            (root / "docs").write_text("someone replaced the folder\n")
            real_unlink = Path.unlink

            def unlink(self, *args, **kwargs):
                if self == root / "docs":
                    raise PermissionError(13, "Permission denied", str(self))
                return real_unlink(self, *args, **kwargs)

            with patch.object(Path, "unlink", unlink):
                report = gen.remove_report(written)

        assert report.removed == [root / "api"]
        assert report.stranded == [root / "docs"]
        assert report.legacy_skipped == []
        assert (root / "docs").is_file()

    def test_remove_report_lists_the_legacy_path_then_the_skill_folders(self, tmp_path):
        """Legacy cleanup runs first, and both land in ``removed``."""
        gen, root = self._gen(tmp_path)
        legacy = tmp_path / "legacy.md"
        legacy.write_text(render_developer_guide("0.2.3"))
        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
        ):
            written = gen.install_skills([_fake_skill(tmp_path, "api")])
        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[LegacyArtifact(legacy)]),
        ):
            assert gen.remove_report(written).removed == [legacy, root / "api"]
        assert not legacy.exists()

    def test_remove_leaves_the_shared_skills_root_standing(self, tmp_path):
        """The directory is not deepctl's either, only the folders in it.

        ~/.agents/skills is Codex's and `npx skills add`'s as much as
        deepctl's, and none of the six roots is created by deepctl alone.
        Emptying one is not a licence to delete it.
        """
        gen, root = self._gen(tmp_path)
        skills = [_fake_skill(tmp_path, "api")]

        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
        ):
            written = gen.install_skills(skills)
            assert gen.remove_report(written).removed == written

        assert root.is_dir()
        assert list(root.iterdir()) == []

    # -- install -----------------------------------------------------

    def test_install_refuses_a_same_name_collision(self, tmp_path):
        """An unrecorded folder named `api` is somebody else's `api`."""
        gen, root = self._gen(tmp_path)
        mine = self._unrelated(root, "api")
        skills = [_fake_skill(tmp_path, n) for n in ("api", "docs")]

        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
            pytest.raises(SkillOwnershipError) as excinfo,
        ):
            gen.install_skills(skills)

        assert [p for _, p in excinfo.value.conflicts] == [root / "api"]
        assert "Refusing to overwrite" in str(excinfo.value)
        assert str(root / "api") in str(excinfo.value)
        # The user's file is untouched...
        assert (mine / "SKILL.md").read_text().endswith("mine\n")
        # ...and nothing else was installed either: a collision on one
        # skill must not leave a half-written bundle behind.
        assert not (root / "docs").exists()

    def test_install_replaces_only_what_deepctl_recorded(self, tmp_path):
        gen, root = self._gen(tmp_path)
        mine = self._unrelated(root, "mine")
        skills = [_fake_skill(tmp_path, "api", references=("old.md",))]
        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
        ):
            written = gen.install_skills(skills)
            # Recorded, so a reinstall may replace it.
            again = gen.install_skills(skills, written)
        assert again == written
        assert (root / "api" / "references" / "old.md").is_file()
        # "only": the neighbour in the same directory that deepctl never
        # recorded came through both installs untouched.
        assert (mine / "SKILL.md").read_text().endswith("mine\n")

    def test_a_record_written_through_a_symlinked_home_still_counts(self, tmp_path):
        """/tmp vs /private/tmp is the same folder, so it is still ours."""
        gen, root = self._gen(tmp_path)
        link_root = tmp_path / "link-home" / ".claude" / "skills"
        link_root.parent.mkdir(parents=True)
        try:
            link_root.symlink_to(root, target_is_directory=True)
        except (OSError, NotImplementedError):  # unprivileged Windows
            pytest.skip("this filesystem does not allow creating symlinks")

        skills = [_fake_skill(tmp_path, "api")]
        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
        ):
            gen.install_skills(skills)
            # Recorded under the other spelling of the same directory.
            assert gen.install_conflicts(skills, [str(link_root / "api")]) == []

    def test_unique_paths_folds_two_spellings_of_one_folder(self, tmp_path):
        """/tmp/x and /private/tmp/x are one folder, so one record."""
        real = tmp_path / "real" / "skills"
        real.mkdir(parents=True)
        link = tmp_path / "link"
        _symlink_or_skip(link, tmp_path / "real")
        via_link = link / "skills" / "api"
        (real / "api").mkdir()

        unique = skill_generator._unique_paths([via_link, real / "api"])
        assert len(unique) == 1
        # And the order of arrival decides which spelling survives.
        assert unique == [via_link]
        # A path that does not exist yet still folds (resolve is non-strict).
        assert (
            len(
                skill_generator._unique_paths([link / "skills" / "docs", real / "docs"])
            )
            == 1
        )

    def test_ownership_after_failure_does_not_double_record_through_a_symlink(
        self, tmp_path
    ):
        """A record spelled one way and a root reached another way: one entry."""
        gen = ClaudeCodeGenerator()
        real = tmp_path / "real" / "skills"
        real.mkdir(parents=True)
        link = tmp_path / "link"
        _symlink_or_skip(link, tmp_path / "real")
        root = link / "skills"
        skills = [_fake_skill(tmp_path, n) for n in ("api", "docs")]
        # Landed via the symlinked root; recorded under the real path.
        shutil.copytree(skills[0].path, root / "api")

        with patch.object(gen, "skills_root", return_value=root):
            kept = skill_generator._ownership_after_failure(
                gen, skills, [str(real / "api")]
            )

        assert len(kept) == 1
        assert kept[0].name == "api"

    def test_a_mid_install_record_has_one_entry_per_folder_through_a_symlink(
        self, tmp_path
    ):
        """The same fold, end to end: every save during the install."""
        gen = ClaudeCodeGenerator()
        gen.cli_name = "claude"
        real = tmp_path / "real" / "skills"
        real.mkdir(parents=True)
        link = tmp_path / "link"
        _symlink_or_skip(link, tmp_path / "real")
        root = link / "skills"
        skills = [_fake_skill(tmp_path, n) for n in ("api", "docs")]
        shutil.copytree(skills[0].path, real / "api")
        state = {"installed_skills": {"claude": {"paths": [str(real / "api")]}}}
        snapshots = []

        with (
            patch.object(ClaudeCodeGenerator, "skills_root", lambda self: root),
            patch.object(ClaudeCodeGenerator, "legacy_paths", lambda self: []),
            patch.object(
                skill_generator,
                "save_skills_state",
                side_effect=lambda s: snapshots.append(
                    sorted(
                        Path(p).name for p in s["installed_skills"]["claude"]["paths"]
                    )
                ),
            ),
            patch.object(skill_generator, "fetch_repo_skills", return_value=skills),
        ):
            skill_generator.install_skills_for(
                [gen], state, commands=[_make_command()], version="9.9.9"
            )

        assert snapshots == [["api"], ["api", "docs"], ["api", "docs"]]

    def test_a_symlink_to_another_skill_in_the_same_root_is_not_owned(self, tmp_path):
        """A recorded name replaced by a symlink is dropped wherever it points.

        Resolving alone is not enough: `skills/api -> skills/mine` has the
        same parent once resolved, so the resolved-parent check called it
        deepctl's, and an update would have unlinked the name and buried
        the user's folder under the Deepgram skill.
        """
        gen, root = self._gen(tmp_path)
        mine = self._unrelated(root, "my-private-skill")
        link = root / "api"
        try:
            link.symlink_to(mine, target_is_directory=True)
        except (OSError, NotImplementedError):  # unprivileged Windows
            pytest.skip("this filesystem does not allow creating symlinks")

        skills = [_fake_skill(tmp_path, "api")]
        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
        ):
            assert gen.owned_skill_paths([str(link)]) == []
            assert gen.installed_skill_paths([str(link)]) == []
            # Recorded or not, it is a destination deepctl must refuse.
            assert gen.install_conflicts(skills, [str(link)]) == [link]
            with pytest.raises(SkillOwnershipError):
                gen.install_skills(skills, [str(link)])

        assert link.is_symlink()
        assert (mine / "SKILL.md").read_text().endswith("mine\n")

    def test_install_conflicts_lists_every_unowned_destination(self, tmp_path):
        gen, root = self._gen(tmp_path)
        self._unrelated(root, "api")
        self._unrelated(root, "docs")
        skills = [_fake_skill(tmp_path, n) for n in ("api", "docs", "cli")]
        with patch.object(gen, "skills_root", return_value=root):
            conflicts = gen.install_conflicts(skills)
        assert conflicts == [root / "api", root / "docs"]

    def test_a_fresh_install_over_an_existing_folder_fails_rather_than_overwrites(
        self, tmp_path
    ):
        """No skills.json yet is exactly the case with no proof of ownership."""
        gen, root = self._gen(tmp_path)
        mine = self._unrelated(root, "api")
        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
            # Patched: the install would otherwise download the real
            # bundle into the developer's own ~/.deepctl cache.
            patch(
                "deepctl_core.skill_generator.fetch_repo_skills",
                return_value=[_fake_skill(tmp_path, "api")],
            ),
            patch.object(skill_generator, "save_skills_state") as save,
            pytest.raises(SkillOwnershipError),
        ):
            skill_generator.install_skills_for(
                [gen],
                {"installed_skills": {}},
                commands=[_make_command()],
                version="1.0.0",
            )
        save.assert_not_called()
        assert (mine / "SKILL.md").read_text().endswith("mine\n")

    def test_prune_removes_a_skill_that_disappeared_upstream(self, tmp_path):
        """Otherwise a retired skill is left behind and becomes unownable."""
        gen, root = self._gen(tmp_path)
        mine = self._unrelated(root)
        first = [_fake_skill(tmp_path, n) for n in ("api", "retired")]
        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
        ):
            written = gen.install_skills(first)
            pruned = gen.prune_retired(written, [_fake_skill(tmp_path, "api")])

        assert pruned == [root / "retired"]
        assert not (root / "retired").exists()
        assert (root / "api").is_dir()
        # And it still leaves everything it does not own alone.
        assert mine.is_dir()

    def test_prune_keeps_a_retired_folder_it_could_not_delete(self, tmp_path):
        """The same rule remove_report() follows: ownership outlives a failed rmtree."""
        gen, root = self._gen(tmp_path)
        first = [_fake_skill(tmp_path, n) for n in ("api", "retired")]
        current = [_fake_skill(tmp_path, "api")]

        def denied(path, ignore_errors=False, **kwargs):
            if not ignore_errors:
                raise PermissionError(13, "Permission denied", str(path))

        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
        ):
            written = gen.install_skills(first)
            with patch.object(skill_generator.shutil, "rmtree", denied):
                result = gen.prune_retired_result(written, current)
                assert gen.prune_retired(written, current) == []

        assert result.pruned == []
        assert result.stranded == [root / "retired"]
        assert (root / "retired" / "SKILL.md").is_file()

    def test_a_case_only_rename_is_not_retired(self, tmp_path, monkeypatch):
        """`Foo` renamed `foo` upstream: on a case-folding volume they are one folder."""
        gen, root = self._gen(tmp_path)
        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
        ):
            written = gen.install_skills(
                [_fake_skill(tmp_path, n) for n in ("Foo", "old")]
            )
            # Stand in for a case-insensitive volume: Foo and foo are one
            # location. normcase stays the identity, as it is on macOS.
            real_same = skill_generator._same_location

            def same_location(a, b):
                if str(a).lower() == str(b).lower():
                    return True
                return real_same(a, b)

            monkeypatch.setattr(skill_generator, "_same_location", same_location)
            current = [_fake_skill(tmp_path, "foo")]
            assert gen.retired_paths(written, current) == [root / "old"]
            assert gen.prune_retired(written, current) == [root / "old"]

        assert (root / "Foo" / "SKILL.md").is_file()
        assert not (root / "old").exists()

    def test_a_case_only_rename_survives_on_a_real_case_insensitive_volume(
        self, tmp_path
    ):
        """The same, end to end, where the filesystem under tmp_path folds case."""
        probe = tmp_path / "CaseProbe"
        probe.mkdir()
        if not (tmp_path / "caseprobe").exists():
            pytest.skip("tmp_path is on a case-sensitive filesystem")
        gen, root = self._gen(tmp_path)
        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
        ):
            written = gen.install_skills([_fake_skill(tmp_path / "v1", "Foo")])
            current = [_fake_skill(tmp_path / "v2", "foo")]
            gen.install_skills(current, written)
            assert gen.retired_paths(written, current) == []
            assert gen.prune_retired(written, current) == []

        assert (root / "foo" / "SKILL.md").is_file()

    def test_ownership_compare_folds_case_where_the_platform_does(self, tmp_path):
        """`API` recorded, `api` on disk: one folder on a case-insensitive volume."""
        gen, root = self._gen(tmp_path)
        skills = [_fake_skill(tmp_path, "api")]
        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
        ):
            gen.install_skills(skills)
            recorded = [str(root / "API")]
            # A case-sensitive filesystem calls these two different
            # folders, so without the fold this is a conflict.
            with patch.object(skill_generator, "_normcase", str.lower):
                assert gen.owned_skill_paths(recorded) == [root / "API"]
                assert gen.install_conflicts(skills, recorded) == []
                # Both spellings recorded is still one folder.
                both = [str(root / "API"), str(root / "api")]
                assert gen.owned_skill_paths(both) == [root / "API"]
                assert skill_generator._unique_paths([root / "api", root / "API"]) == [
                    root / "api"
                ]

    # -- status ------------------------------------------------------

    def test_status_does_not_count_unowned_folders(self, tmp_path):
        gen, root = self._gen(tmp_path)
        self._unrelated(root)
        self._unrelated(root, "someone-elses-api")
        skills = [_fake_skill(tmp_path, "api")]
        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
        ):
            written = gen.install_skills(skills)
            assert gen.installed_skill_paths(written) == [root / "api"]

    def test_status_drops_a_recorded_folder_the_user_deleted(self, tmp_path):
        gen, root = self._gen(tmp_path)
        skills = [_fake_skill(tmp_path, n) for n in ("api", "docs")]
        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
        ):
            written = gen.install_skills(skills)
            shutil.rmtree(root / "docs")
            assert gen.installed_skill_paths(written) == [root / "api"]

    # -- the record itself -------------------------------------------

    def test_recorded_skill_paths_tolerates_a_mangled_state_file(self):
        assert recorded_skill_paths({}, "claude") == []
        assert recorded_skill_paths({"installed_skills": None}, "claude") == []
        assert recorded_skill_paths({"installed_skills": {}}, "claude") == []
        # A truthy non-map got as far as calling .get() on it, which is
        # an attribute error, not an empty result. The status table calls
        # this once per tool, so it took `dg skills status` down with it.
        assert recorded_skill_paths({"installed_skills": ["claude"]}, "claude") == []
        assert recorded_skill_paths({"installed_skills": "claude"}, "claude") == []
        assert recorded_skill_paths({"installed_skills": 7}, "claude") == []
        assert (
            recorded_skill_paths({"installed_skills": {"claude": "nope"}}, "claude")
            == []
        )
        assert (
            recorded_skill_paths(
                {"installed_skills": {"claude": {"paths": "not-a-list"}}}, "claude"
            )
            == []
        )
        assert recorded_skill_paths(
            {"installed_skills": {"claude": {"paths": ["/a", 7, None, "/b"]}}},
            "claude",
        ) == ["/a", "/b"]

    def test_tools_without_a_skills_directory_own_nothing(self):
        gen = AmazonQGenerator()
        assert gen.owned_skill_paths(["/anywhere"]) == []
        assert gen.install_conflicts([], ["/anywhere"]) == []


class TestLegacyCleanup:
    """deepctl <= 0.3.0 wrote files these tools do not read as skills."""

    def test_claude_slash_command_directory_is_removed(self, tmp_path):
        legacy = tmp_path / ".claude" / "commands" / "deepgram"
        legacy.mkdir(parents=True)
        (legacy / "api.md").write_text("---\nname: api\n---\n")

        gen = ClaudeCodeGenerator()
        artifact = LegacyArtifact(legacy, contents=("api.md",))
        with patch.object(gen, "legacy_paths", return_value=[artifact]):
            removed = gen.clean_legacy()
        assert removed == [legacy]
        assert not legacy.exists()

    def test_claude_cleanup_only_takes_the_markdown_it_wrote(self, tmp_path):
        """0.3.0 wrote `*.md` here and removed `*.md`; so does the cleanup."""
        legacy = tmp_path / ".claude" / "commands" / "deepgram"
        legacy.mkdir(parents=True)
        names = ClaudeCodeGenerator().legacy_paths()[0].contents
        for name in names:
            # What each release actually put there: 0.3.0 the upstream
            # SKILL.md verbatim, the dead generate() path the rendered
            # guide behind Claude's frontmatter.
            if name == "deepgram.md":
                content = render_developer_guide("0.2.3", include_frontmatter=True)
            else:
                content = f"---\nname: {Path(name).stem}\ndescription: x\n---\n"
            (legacy / name).write_text(content)
        # A slash command the user wrote. It is a .md file in the same
        # directory, which is exactly why a *.md glob is not safe here.
        (legacy / "deploy.md").write_text("my own slash command")
        (legacy / "mine").mkdir()

        gen = ClaudeCodeGenerator()
        with patch.object(
            gen, "legacy_paths", return_value=[LegacyArtifact(legacy, contents=names)]
        ):
            removed = gen.clean_legacy()

        assert removed == [legacy]
        assert sorted(p.name for p in legacy.iterdir()) == ["deploy.md", "mine"]
        assert (legacy / "deploy.md").read_text() == "my own slash command"

    def test_claude_cleanup_does_not_reach_through_a_symlinked_directory(
        self, tmp_path
    ):
        """Keeping dotfiles in a repo is how this path becomes a link.

        `api.md` and `docs.md` are plausible names for slash commands
        someone wrote, and the cleanup deletes exactly those names. It
        must not follow a link to find them.
        """
        mine = tmp_path / "dotfiles" / "claude-commands"
        mine.mkdir(parents=True)
        (mine / "api.md").write_text("my own /api command")
        legacy = tmp_path / ".claude" / "commands" / "deepgram"
        legacy.parent.mkdir(parents=True)
        try:
            legacy.symlink_to(mine, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("this filesystem does not allow creating symlinks")

        gen = ClaudeCodeGenerator()
        names = ClaudeCodeGenerator().legacy_paths()[0].contents
        with patch.object(
            gen, "legacy_paths", return_value=[LegacyArtifact(legacy, contents=names)]
        ):
            removed = gen.clean_legacy()

        assert removed == []
        assert (mine / "api.md").read_text() == "my own /api command"
        assert legacy.is_symlink()

    def test_a_symlinked_whole_directory_artifact_is_left_alone(self, tmp_path):
        """rmtree already refused this one, but reported it as cleaned."""
        mine = tmp_path / "dotfiles" / "rules"
        mine.mkdir(parents=True)
        (mine / "notes.md").write_text("mine")
        legacy = tmp_path / ".cursor" / "rules"
        legacy.parent.mkdir(parents=True)
        try:
            legacy.symlink_to(mine, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("this filesystem does not allow creating symlinks")

        gen = ClaudeCodeGenerator()
        with patch.object(gen, "legacy_paths", return_value=[LegacyArtifact(legacy)]):
            assert gen.clean_legacy() == []
        assert (mine / "notes.md").read_text() == "mine"

    def test_claude_cleanup_removes_the_directory_once_it_is_empty(self, tmp_path):
        legacy = tmp_path / ".claude" / "commands" / "deepgram"
        legacy.mkdir(parents=True)
        (legacy / "api.md").write_text("---\nname: api\n---\n\nstale\n")

        gen = ClaudeCodeGenerator()
        with patch.object(
            gen,
            "legacy_paths",
            return_value=[LegacyArtifact(legacy, contents=("api.md",))],
        ):
            gen.clean_legacy()
        assert not legacy.exists()

    def test_claude_cleanup_skips_a_command_file_that_is_not_a_skill_copy(
        self, tmp_path
    ):
        """A file deepctl named but did not write stays, and is reported."""
        legacy = tmp_path / ".claude" / "commands" / "deepgram"
        legacy.mkdir(parents=True)
        (legacy / "api.md").write_text("---\nname: api\n---\n")
        # The user reused deepctl's filename for a slash command of their
        # own. Same name, not deepctl's content.
        (legacy / "docs.md").write_text("Summarise the docs I point you at.\n")

        gen = ClaudeCodeGenerator()
        with patch.object(
            gen,
            "legacy_paths",
            return_value=[LegacyArtifact(legacy, contents=("api.md", "docs.md"))],
        ):
            report = gen.clean_legacy_report()

        assert report.removed == [legacy]
        assert [p for p, _ in report.skipped] == [legacy / "docs.md"]
        assert "header deepctl wrote" in report.skipped[0][1]
        assert not (legacy / "api.md").exists()
        assert (legacy / "docs.md").read_text().startswith("Summarise")

    def test_a_legacy_file_without_deepctls_header_is_left_alone(self, tmp_path):
        """~/.cursor/rules/deepctl.mdc is deleted by name; the name is not proof."""
        rules = tmp_path / ".cursor" / "rules" / "deepctl.mdc"
        rules.parent.mkdir(parents=True)
        rules.write_text("---\ndescription: my rules for the deepctl repo\n---\n")

        gen = CursorGenerator()
        with patch.object(gen, "legacy_paths", return_value=[LegacyArtifact(rules)]):
            report = gen.clean_legacy_report()
            assert gen.clean_legacy() == []

        assert report.removed == []
        assert report.skipped == [(rules, skill_generator._NOT_DEEPCTLS)]
        assert rules.is_file()

    @pytest.mark.parametrize("frontmatter", [False, True])
    def test_a_legacy_file_with_the_generated_header_is_removed(
        self, tmp_path, frontmatter
    ):
        """deepctl 0.2.0-0.2.15 wrote the rendered guide; that header counts."""
        rules = tmp_path / ".cline" / "rules" / "deepctl.md"
        rules.parent.mkdir(parents=True)
        # The real renderer, so the test fails if the header line changes
        # out from under the marker the cleanup looks for.
        rules.write_text(
            render_developer_guide("0.2.3", include_frontmatter=frontmatter)
        )

        gen = ClineGenerator()
        with patch.object(gen, "legacy_paths", return_value=[LegacyArtifact(rules)]):
            report = gen.clean_legacy_report()

        assert report.removed == [rules]
        assert report.skipped == []
        assert not rules.exists()

    def test_a_verbatim_upstream_skill_copy_is_removed(self, tmp_path):
        """deepctl 0.2.16-0.3.1 joined the four upstream SKILL.md files."""
        rules = tmp_path / ".amazonq" / "rules" / "deepctl.md"
        rules.parent.mkdir(parents=True)
        rules.write_text(
            "---\nname: api\ndescription: >\n  Deepgram API reference\n---\n\n"
            "# api\n\n---\n\n---\nname: docs\n---\n"
        )

        gen = AmazonQGenerator()
        with patch.object(gen, "legacy_paths", return_value=[LegacyArtifact(rules)]):
            assert gen.clean_legacy() == [rules]
        assert not rules.exists()

    def test_a_skill_copy_with_description_before_name_is_still_deepctls(
        self, tmp_path
    ):
        """YAML key order is upstream's to choose; `name:` need not be line 2."""
        rules = tmp_path / ".amazonq" / "rules" / "deepctl.md"
        rules.parent.mkdir(parents=True)
        rules.write_text(
            "---\ndescription: >\n  Deepgram API reference\nname: api\n---\n\n# api\n"
        )

        gen = AmazonQGenerator()
        with patch.object(gen, "legacy_paths", return_value=[LegacyArtifact(rules)]):
            assert gen.clean_legacy() == [rules]
        assert not rules.exists()

    def test_a_name_line_after_the_frontmatter_closes_does_not_count(self, tmp_path):
        """Only the block between the first two `---` lines is frontmatter."""
        rules = tmp_path / ".amazonq" / "rules" / "deepctl.md"
        rules.parent.mkdir(parents=True)
        rules.write_text("---\ndescription: mine\n---\nname: api\n")

        gen = AmazonQGenerator()
        with patch.object(gen, "legacy_paths", return_value=[LegacyArtifact(rules)]):
            report = gen.clean_legacy_report()

        assert report.removed == []
        assert report.skipped == [(rules, skill_generator._NOT_DEEPCTLS)]
        assert rules.is_file()

    def test_a_skill_copy_under_the_wrong_name_is_not_deepctls(self, tmp_path):
        """starters.md holding the api skill was not written by any release."""
        legacy = tmp_path / ".claude" / "commands" / "deepgram"
        legacy.mkdir(parents=True)
        (legacy / "starters.md").write_text("---\nname: api\n---\n")

        gen = ClaudeCodeGenerator()
        with patch.object(
            gen,
            "legacy_paths",
            return_value=[LegacyArtifact(legacy, contents=("starters.md",))],
        ):
            report = gen.clean_legacy_report()

        assert report.removed == []
        assert [p for p, _ in report.skipped] == [legacy / "starters.md"]

    def test_claude_generator_scopes_its_real_legacy_artifact(self):
        """Not just the test's fixture — the shipped artifact is scoped too."""
        (artifact,) = ClaudeCodeGenerator().legacy_paths()
        assert artifact.path == Path.home() / ".claude" / "commands" / "deepgram"
        # Exact filenames, never a glob: a slash command the user added to
        # this directory is also a .md file.
        assert artifact.contents == (
            "api.md",
            "docs.md",
            "setup-mcp.md",
            "starters.md",
            "deepgram.md",
        )

    def test_shared_context_file_keeps_the_user_content(self, tmp_path):
        target = tmp_path / "instructions.md"
        target.write_text(
            "my own notes\n"
            "<!-- BEGIN deepctl CLI Reference (auto-generated by deepctl) -->\n"
            "four concatenated skills\n"
            "<!-- END deepctl CLI Reference -->\n"
            "more of my notes\n"
        )
        gen = CodexGenerator()
        with patch.object(
            gen,
            "legacy_paths",
            return_value=[LegacyArtifact(target, shared=True)],
        ):
            removed = gen.clean_legacy()
        assert removed == [target]
        text = target.read_text()
        assert "BEGIN deepctl" not in text
        assert "four concatenated skills" not in text
        assert "my own notes" in text
        assert "more of my notes" in text

    def test_shared_file_is_deleted_when_only_deepctl_wrote_it(self, tmp_path):
        target = tmp_path / "instructions.md"
        target.write_text(
            "<!-- BEGIN deepctl CLI Reference (auto-generated by deepctl) -->\n"
            "blob\n"
            "<!-- END deepctl CLI Reference -->\n"
        )
        gen = CodexGenerator()
        with patch.object(
            gen,
            "legacy_paths",
            return_value=[LegacyArtifact(target, shared=True)],
        ):
            gen.clean_legacy()
        assert not target.exists()

    def test_shared_file_without_markers_is_untouched(self, tmp_path):
        target = tmp_path / "instructions.md"
        target.write_text("purely the user's own file\n")
        gen = CodexGenerator()
        with patch.object(
            gen,
            "legacy_paths",
            return_value=[LegacyArtifact(target, shared=True)],
        ):
            assert gen.clean_legacy() == []
        assert target.read_text() == "purely the user's own file\n"

    def test_unterminated_marker_does_not_leave_half_a_blob(self, tmp_path):
        target = tmp_path / "instructions.md"
        target.write_text(
            "keep me\n"
            "<!-- BEGIN deepctl CLI Reference (auto-generated by deepctl) -->\n"
            "truncated blob with no end marker\n"
        )
        gen = CodexGenerator()
        with patch.object(
            gen,
            "legacy_paths",
            return_value=[LegacyArtifact(target, shared=True)],
        ):
            gen.clean_legacy()
        assert target.read_text() == "keep me\n"

    def test_installing_cleans_up_the_old_location(self, tmp_path):
        legacy = tmp_path / "commands" / "deepgram"
        legacy.mkdir(parents=True)
        (legacy / "api.md").write_text("---\nname: api\n---\n\nstale\n")

        gen = ClaudeCodeGenerator()
        root = tmp_path / "skills"
        artifact = LegacyArtifact(legacy, contents=("api.md",))
        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[artifact]),
        ):
            gen.install_skills([_fake_skill(tmp_path, "api")])
        assert not legacy.exists()
        assert (root / "api" / "SKILL.md").is_file()

    def test_no_generator_still_writes_the_cli_reference_markers(self, tmp_path):
        """The markers exist only to be cleaned up, never to be written."""
        skills = [_fake_skill(tmp_path, "api")]
        for gen in get_all_generators():
            if gen.skills_root() is None:
                continue
            root = tmp_path / gen.cli_name
            with (
                patch.object(gen, "skills_root", return_value=root),
                patch.object(gen, "legacy_paths", return_value=[]),
            ):
                gen.install_skills(skills)
            for path in root.rglob("*"):
                if path.is_file():
                    assert "BEGIN deepctl CLI Reference" not in path.read_text()


class TestGetAllGenerators:
    """Test get_all_generators."""

    def test_returns_all_generators(self):
        generators = get_all_generators()
        names = {g.cli_name for g in generators}
        assert "claude" in names
        assert "codex" in names
        assert "gemini" in names
        assert "amazonq" in names
        assert "aider" in names
        assert "opencode" in names
        assert "cursor" in names
        assert "cline" in names

    def test_generators_have_display_names(self):
        for gen in get_all_generators():
            assert gen.display_name, f"{gen.cli_name} missing display_name"


class TestDetectAiClis:
    """Test detect_ai_clis."""

    def test_returns_only_detected(self):
        with (
            patch.object(ClaudeCodeGenerator, "detect", return_value=True),
            patch.object(CodexGenerator, "detect", return_value=False),
            patch.object(GeminiGenerator, "detect", return_value=False),
            patch.object(AmazonQGenerator, "detect", return_value=False),
            patch.object(CursorGenerator, "detect", return_value=False),
            patch.object(ClineGenerator, "detect", return_value=False),
        ):
            detected = detect_ai_clis()
            claude = [g for g in detected if g.cli_name == "claude"]
            assert len(claude) >= 1


class TestInstallSkillsForKeepsOwnership:
    """Every route that writes skill folders shares this one contract.

    `dg skills install` already fetched once, preflighted every
    destination and saved after each tool. Login and the plugin refresh
    looped over a per-tool install instead and saved once at the end, so a
    failure part-way through left folders on disk with no ownership
    record -- exactly the unowned litter the primary flow was redesigned
    to prevent. They all call this helper now.
    """

    def _gen(self, tmp_path, cli_name):
        gen = ClaudeCodeGenerator()
        gen.cli_name = cli_name
        gen.display_name = cli_name
        root = tmp_path / cli_name / "skills"
        root.mkdir(parents=True)
        return gen, root

    def _run(self, generators, roots, skills, state, **kwargs):
        def skills_root(self, _roots=roots):
            return _roots[self.cli_name]

        with (
            patch.object(ClaudeCodeGenerator, "skills_root", skills_root),
            patch.object(ClaudeCodeGenerator, "legacy_paths", lambda self: []),
            patch.object(skill_generator, "save_skills_state") as save,
            patch.object(skill_generator, "fetch_repo_skills", return_value=skills),
        ):
            # Also hung off the instance, because the tests that matter
            # most here run inside pytest.raises and never see a return
            # value. Mutating `state` is not the contract -- reaching
            # save_skills_state before the exception does is.
            self.save = save
            report = skill_generator.install_skills_for(
                generators,
                state,
                commands=[_make_command()],
                version="9.9.9",
                **kwargs,
            )
        return report, save

    def test_a_second_tool_failing_leaves_the_first_recorded(self, tmp_path):
        """The bug: tool one's folders became litter when tool two raised."""
        first, first_root = self._gen(tmp_path, "claude")
        second, second_root = self._gen(tmp_path, "cursor")
        roots = {"claude": first_root, "cursor": second_root}
        skills = [_fake_skill(tmp_path, n) for n in ("api", "docs")]
        state = {"installed_skills": {}}

        real_install = ClaudeCodeGenerator.install_skills

        def install_skills(self, bundle, recorded=()):
            if self.cli_name == "cursor":
                raise OSError(30, "Read-only file system")
            return real_install(self, bundle, recorded)

        with (
            patch.object(ClaudeCodeGenerator, "install_skills", install_skills),
            pytest.raises(skill_generator.SkillWriteError),
        ):
            self._run([first, second], roots, skills, state)

        entry = state["installed_skills"]["claude"]
        assert [Path(p).name for p in entry["paths"]] == ["api", "docs"]
        assert entry["skills_ref"] == DEFAULT_SKILLS_REF
        assert (first_root / "api" / "SKILL.md").is_file()
        # And it was written out before cursor raised. Asserting only the
        # in-memory dict would still pass with the per-tool save moved
        # back after the loop, which is the bug itself. More than one
        # save now: one per skill as it lands, then one for the tool.
        assert self.save.call_count >= 1
        self.save.assert_called_with(state)
        # Nothing was written for the tool that failed, so nothing claims
        # it was -- but the tool that succeeded stays deepctl's.
        assert "cursor" not in state["installed_skills"]

    def test_best_effort_reports_the_failure_instead_of_raising(self, tmp_path):
        """Login and the plugin refresh must not fail their own command."""
        first, first_root = self._gen(tmp_path, "claude")
        second, second_root = self._gen(tmp_path, "cursor")
        roots = {"claude": first_root, "cursor": second_root}
        skills = [_fake_skill(tmp_path, "api")]
        state = {"installed_skills": {}}

        real_install = ClaudeCodeGenerator.install_skills

        def install_skills(self, bundle, recorded=()):
            if self.cli_name == "cursor":
                raise OSError(30, "Read-only file system")
            return real_install(self, bundle, recorded)

        with patch.object(ClaudeCodeGenerator, "install_skills", install_skills):
            report, _ = self._run(
                [first, second], roots, skills, state, best_effort=True
            )

        assert [name for name, _ in report.failures] == ["cursor"]
        assert list(report.written) == ["claude"]
        assert state["installed_skills"]["claude"]["skills"] == ["api"]
        assert "cursor" not in state["installed_skills"]

    def test_a_half_written_bundle_is_still_recorded_as_owned(self, tmp_path):
        """Whatever landed before the error must stay deepctl's to fix."""
        gen, root = self._gen(tmp_path, "claude")
        skills = [_fake_skill(tmp_path, n) for n in ("api", "docs", "cli")]
        state = {"installed_skills": {}}
        real_copytree = shutil.copytree

        def copytree(src, dst, *args, **kwargs):
            # Copies go to a `.cli.tmp-<pid>` staging sibling first.
            if ".cli." in Path(dst).name:
                raise OSError(28, "No space left on device")
            return real_copytree(src, dst, *args, **kwargs)

        with (
            patch.object(skill_generator.shutil, "copytree", copytree),
            pytest.raises(skill_generator.SkillWriteError),
        ):
            self._run([gen], {"claude": root}, skills, state)

        entry = state["installed_skills"]["claude"]
        assert [Path(p).name for p in entry["paths"]] == ["api", "docs"]
        # On disk, not just in the dict: the folders outlive the process
        # that wrote them, so the record has to as well.
        self.save.assert_called_with(state)

    def test_a_collision_in_the_last_tool_writes_nothing_at_all(self, tmp_path):
        """Preflight covers every destination before the first byte lands."""
        first, first_root = self._gen(tmp_path, "claude")
        second, second_root = self._gen(tmp_path, "cursor")
        theirs = second_root / "api"
        theirs.mkdir()
        (theirs / "SKILL.md").write_text("---\nname: api\n---\n\nmine\n")
        skills = [_fake_skill(tmp_path, "api")]
        state = {"installed_skills": {}}

        with pytest.raises(SkillOwnershipError):
            self._run(
                [first, second],
                {"claude": first_root, "cursor": second_root},
                skills,
                state,
            )

        assert not (first_root / "api").exists()
        assert state["installed_skills"] == {}
        assert (theirs / "SKILL.md").read_text().endswith("mine\n")

    def test_nothing_installable_means_nothing_downloaded(self, tmp_path):
        """A tool with no skills directory must not trigger a download."""
        gen, _ = self._gen(tmp_path, "amazonq")
        state = {"installed_skills": {"amazonq": {"paths": []}}}

        with (
            patch.object(ClaudeCodeGenerator, "skills_root", lambda self: None),
            patch.object(ClaudeCodeGenerator, "legacy_paths", lambda self: []),
            patch.object(skill_generator, "save_skills_state"),
            patch.object(skill_generator, "fetch_repo_skills") as fetch,
        ):
            report = skill_generator.install_skills_for(
                [gen], state, commands=[_make_command()], version="9.9.9"
            )

        fetch.assert_not_called()
        assert report.unsupported == [gen]
        # Nothing was written for it, so nothing may claim it was.
        assert state["installed_skills"] == {}

    def test_best_effort_skips_a_conflicting_tool_and_installs_the_rest(self, tmp_path):
        """Login and the plugin refresh must not lose every tool to one."""
        first, first_root = self._gen(tmp_path, "claude")
        second, second_root = self._gen(tmp_path, "cursor")
        theirs = second_root / "api"
        theirs.mkdir()
        (theirs / "SKILL.md").write_text("---\nname: api\n---\n\nmine\n")
        skills = [_fake_skill(tmp_path, "api")]
        state = {"installed_skills": {}}

        report, _ = self._run(
            [first, second],
            {"claude": first_root, "cursor": second_root},
            skills,
            state,
            best_effort=True,
        )

        assert [name for name, _ in report.conflicts] == ["cursor"]
        assert list(report.written) == ["claude"]
        assert (first_root / "api" / "SKILL.md").is_file()
        assert (theirs / "SKILL.md").read_text().endswith("mine\n")
        assert "cursor" not in state["installed_skills"]

    def test_a_late_collision_never_becomes_a_claim_of_ownership(self, tmp_path):
        """The refusal must not be recorded as "these folders are ours".

        `install_skills` re-checks its own destinations, so a folder that
        appears between the all-tool preflight and the write raises after
        the loop has started. Recording what is on disk at that moment
        would hand deepctl a claim over the very folder it just refused
        to touch, and the next install would delete it.
        """
        gen, root = self._gen(tmp_path, "claude")
        skills = [_fake_skill(tmp_path, "api")]
        state = {"installed_skills": {}}
        theirs = root / "api"

        real_conflicts = ClaudeCodeGenerator.install_conflicts
        calls = {"n": 0}

        def install_conflicts(self, bundle, recorded=()):
            calls["n"] += 1
            if calls["n"] > 1:
                # Someone else got there between preflight and write.
                theirs.mkdir(exist_ok=True)
                (theirs / "SKILL.md").write_text("---\nname: api\n---\n\nmine\n")
            return real_conflicts(self, bundle, recorded)

        with patch.object(ClaudeCodeGenerator, "install_conflicts", install_conflicts):
            report, _ = self._run(
                [gen], {"claude": root}, skills, state, best_effort=True
            )

        assert [name for name, _ in report.failures] == ["claude"]
        assert state["installed_skills"] == {}
        assert (theirs / "SKILL.md").read_text().endswith("mine\n")

    def test_each_tool_is_reported_as_it_lands_not_after_the_last_one(self, tmp_path):
        """A later tool failing must not hide the ones already recorded."""
        first, first_root = self._gen(tmp_path, "claude")
        second, second_root = self._gen(tmp_path, "cursor")
        skills = [_fake_skill(tmp_path, "api")]
        state = {"installed_skills": {}}
        announced = []

        real_install = ClaudeCodeGenerator.install_skills

        def install_skills(self, bundle, recorded=()):
            if self.cli_name == "cursor":
                raise OSError(30, "Read-only file system")
            return real_install(self, bundle, recorded)

        with (
            patch.object(ClaudeCodeGenerator, "install_skills", install_skills),
            pytest.raises(skill_generator.SkillWriteError),
        ):
            self._run(
                [first, second],
                {"claude": first_root, "cursor": second_root},
                skills,
                state,
                on_installed=lambda gen, paths: announced.append(gen.cli_name),
            )

        assert announced == ["claude"]

    def test_a_null_installed_skills_does_not_crash_the_install(self, tmp_path):
        """A hand-edited skills.json must not take the command down."""
        gen, root = self._gen(tmp_path, "claude")
        skills = [_fake_skill(tmp_path, "api")]
        state = {"installed_skills": None}

        self._run([gen], {"claude": root}, skills, state)

        assert state["installed_skills"]["claude"]["skills"] == ["api"]

    def test_a_tool_failing_still_retires_the_unsupported_ones(self, tmp_path):
        """Cleanup ran after the loop, so a raise part-way skipped it.

        The unsupported tool's stale record then survived an install that
        had already written and recorded another tool's folders.
        """
        first, first_root = self._gen(tmp_path, "claude")
        second, second_root = self._gen(tmp_path, "cursor")
        unsupported, _ = self._gen(tmp_path, "amazonq")
        roots = {"claude": first_root, "cursor": second_root, "amazonq": None}
        skills = [_fake_skill(tmp_path, "api")]
        state = {"installed_skills": {"amazonq": {"paths": []}}}

        real_install = ClaudeCodeGenerator.install_skills

        def install_skills(self, bundle, recorded=()):
            if self.cli_name == "cursor":
                raise OSError(30, "Read-only file system")
            return real_install(self, bundle, recorded)

        with (
            patch.object(ClaudeCodeGenerator, "install_skills", install_skills),
            pytest.raises(skill_generator.SkillWriteError),
        ):
            self._run([first, second, unsupported], roots, skills, state)

        assert state["installed_skills"]["claude"]["skills"] == ["api"]
        assert "amazonq" not in state["installed_skills"]

    def test_retiring_an_unsupported_tool_is_saved_by_the_core(self, tmp_path):
        """The pop is the whole point, so it cannot wait for the caller."""
        unsupported, _ = self._gen(tmp_path, "amazonq")
        state = {"installed_skills": {"amazonq": {"paths": []}}}

        with (
            patch.object(ClaudeCodeGenerator, "skills_root", lambda self: None),
            patch.object(ClaudeCodeGenerator, "legacy_paths", lambda self: []),
            patch.object(skill_generator, "save_skills_state") as save,
            patch.object(skill_generator, "fetch_repo_skills") as fetch,
        ):
            skill_generator.install_skills_for(
                [unsupported], state, commands=[_make_command()], version="9.9.9"
            )

        fetch.assert_not_called()
        save.assert_called_once()
        assert state["installed_skills"] == {}

    def test_a_null_record_for_an_unsupported_tool_is_dropped_and_saved(self, tmp_path):
        """pop()'s return cannot tell "absent" from "present but null".

        A hand-edited skills.json holding a null for a tool left the key
        gone in memory but the save skipped, so the bad record came back
        on the next run and 'dg skills update' kept chasing it.
        """
        unsupported, _ = self._gen(tmp_path, "amazonq")
        state = {"installed_skills": {"amazonq": None}}

        with (
            patch.object(ClaudeCodeGenerator, "skills_root", lambda self: None),
            patch.object(ClaudeCodeGenerator, "legacy_paths", lambda self: []),
            patch.object(skill_generator, "save_skills_state") as save,
            patch.object(skill_generator, "fetch_repo_skills"),
        ):
            skill_generator.install_skills_for(
                [unsupported], state, commands=[_make_command()], version="9.9.9"
            )

        assert state["installed_skills"] == {}
        save.assert_called_once()

    def test_legacy_cleanup_failing_does_not_abort_the_whole_install(self, tmp_path):
        """Those files belong to a tool nothing is being installed to.

        Cleanup moved ahead of the writes so no later failure could skip
        it; unguarded, an unreadable ~/.gemini/GEMINI.md then took down
        an install that was about to write folders for every other tool.
        """
        supported, root = self._gen(tmp_path, "claude")
        unsupported, _ = self._gen(tmp_path, "amazonq")
        skills = [_fake_skill(tmp_path, "api")]
        state = {"installed_skills": {}}

        def skills_root(self):
            return root if self.cli_name == "claude" else None

        with (
            patch.object(ClaudeCodeGenerator, "skills_root", skills_root),
            patch.object(ClaudeCodeGenerator, "legacy_paths", lambda self: []),
            patch.object(
                unsupported,
                "clean_legacy_report",
                side_effect=PermissionError(13, "Permission denied"),
            ),
            patch.object(skill_generator, "save_skills_state"),
            patch.object(skill_generator, "fetch_repo_skills", return_value=skills),
        ):
            report = skill_generator.install_skills_for(
                [supported, unsupported],
                state,
                commands=[_make_command()],
                version="9.9.9",
            )

        assert list(report.written) == ["claude"]
        assert (root / "api" / "SKILL.md").is_file()

    @pytest.mark.parametrize("with_supported", [False, True])
    def test_an_unsupported_tools_skipped_legacy_file_reaches_the_report(
        self, tmp_path, with_supported
    ):
        """Before, _retire_unsupported called clean_legacy() and lost the skip.

        The user then saw the manual hint on every run with no word about
        the file deepctl had found and declined to touch.
        """
        supported, root = self._gen(tmp_path, "claude")
        unsupported, _ = self._gen(tmp_path, "amazonq")
        rules = tmp_path / ".amazonq" / "rules" / "deepctl.md"
        rules.parent.mkdir(parents=True)
        rules.write_text("my own rules\n")
        state = {"installed_skills": {"amazonq": {"paths": []}}}
        generators = [supported, unsupported] if with_supported else [unsupported]

        def skills_root(self):
            return root if self.cli_name == "claude" else None

        def legacy_paths(self):
            return [LegacyArtifact(rules)] if self.cli_name == "amazonq" else []

        with (
            patch.object(ClaudeCodeGenerator, "skills_root", skills_root),
            patch.object(ClaudeCodeGenerator, "legacy_paths", legacy_paths),
            patch.object(skill_generator, "save_skills_state"),
            patch.object(
                skill_generator,
                "fetch_repo_skills",
                return_value=[_fake_skill(tmp_path, "api")],
            ),
        ):
            report = skill_generator.install_skills_for(
                generators, state, commands=[_make_command()], version="9.9.9"
            )

        assert report.legacy_skipped == [
            ("amazonq", rules, skill_generator._NOT_DEEPCTLS)
        ]
        assert rules.read_text() == "my own rules\n"
        assert "amazonq" not in state["installed_skills"]

    def test_a_fetch_failure_leaves_an_unsupported_tool_alone(self, tmp_path):
        """Nothing was installed, so nothing of theirs may be cleaned up."""
        supported, root = self._gen(tmp_path, "claude")
        unsupported, _ = self._gen(tmp_path, "amazonq")
        state = {"installed_skills": {}}

        def skills_root(self):
            return root if self.cli_name == "claude" else None

        with (
            patch.object(ClaudeCodeGenerator, "skills_root", skills_root),
            patch.object(skill_generator, "save_skills_state"),
            patch.object(unsupported, "clean_legacy_report") as clean,
            patch.object(
                skill_generator,
                "fetch_repo_skills",
                side_effect=SkillFetchError("no network"),
            ),
            pytest.raises(SkillFetchError),
        ):
            skill_generator.install_skills_for(
                [supported, unsupported],
                state,
                commands=[_make_command()],
                version="9.9.9",
            )

        clean.assert_not_called()


#: The skip reasons a refused edit and a refused delete produce.
_EDIT_DENIED = (
    "could not be edited: Permission denied. Fix its permissions, or edit it by hand."
)
_DELETE_DENIED = (
    "could not be deleted: Permission denied. "
    "Fix the permissions on its folder, or delete it by hand."
)


class TestLegacyCleanupThatCannotWrite:
    """A legacy file deepctl may not change is skipped, never raised.

    Before, an OSError from editing or deleting one deepctl <= 0.3.0
    leftover escaped install_skills_for even with best_effort, so every
    tool after it got nothing, and remove stopped before deleting a
    single skill folder. Monkeypatched so it runs as root and on Windows
    too; the chmod variant below covers the real filesystem.
    """

    _BLOCK = (
        f"{GeminiGenerator._LEGACY_BEGIN}\nold reference\n"
        f"{GeminiGenerator._LEGACY_END}\n"
    )

    @staticmethod
    def _refuse(monkeypatch, method, target):
        real = getattr(Path, method)

        def refuse(self, *args, **kwargs):
            if self == target:
                raise PermissionError(13, "Permission denied", str(self))
            return real(self, *args, **kwargs)

        monkeypatch.setattr(Path, method, refuse)

    def _gemini_md(self):
        path = Path.home() / ".gemini" / "GEMINI.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("my own notes\n\n" + self._BLOCK)
        return path

    def test_a_read_only_file_does_not_stop_the_next_tool(self, tmp_path, monkeypatch):
        gemini_md = self._gemini_md()
        before = gemini_md.read_text()
        self._refuse(monkeypatch, "write_text", gemini_md)
        gemini, cursor = GeminiGenerator(), CursorGenerator()
        state = {"installed_skills": {}}

        with (
            patch.object(skill_generator, "save_skills_state"),
            patch.object(
                skill_generator,
                "fetch_repo_skills",
                return_value=[_fake_skill(tmp_path, "api")],
            ),
        ):
            report = skill_generator.install_skills_for(
                [gemini, cursor],
                state,
                commands=[_make_command()],
                version="9.9.9",
                best_effort=True,
            )

        assert report.failures == []
        assert sorted(report.written) == ["cursor", "gemini"]
        assert (Path.home() / ".cursor" / "skills" / "api" / "SKILL.md").is_file()
        assert report.legacy_skipped == [(gemini.display_name, gemini_md, _EDIT_DENIED)]
        assert gemini_md.read_text() == before

    def test_remove_still_deletes_skill_folders(self, monkeypatch):
        gemini_md = self._gemini_md()
        self._refuse(monkeypatch, "write_text", gemini_md)
        folder = Path.home() / ".gemini" / "skills" / "api"
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text("---\nname: api\n---\n")

        report = GeminiGenerator().remove_report([str(folder)])

        assert not folder.exists()
        assert folder in report.removed
        assert report.stranded == []
        assert report.legacy_skipped == [(gemini_md, _EDIT_DENIED)]

    def test_a_file_that_cannot_be_unlinked_is_skipped(self, monkeypatch):
        rules = Path.home() / ".cursor" / "rules" / "deepctl.mdc"
        rules.parent.mkdir(parents=True)
        rules.write_text("---\nname: api\n---\n\n# api\n")
        self._refuse(monkeypatch, "unlink", rules)

        report = CursorGenerator().clean_legacy_report()

        assert report.removed == []
        assert report.skipped == [(rules, _DELETE_DENIED)]
        assert rules.is_file()

    def test_a_command_file_that_cannot_be_unlinked_is_skipped(self, monkeypatch):
        commands = Path.home() / ".claude" / "commands" / "deepgram"
        commands.mkdir(parents=True)
        api, docs = commands / "api.md", commands / "docs.md"
        api.write_text("---\nname: api\n---\n")
        docs.write_text("---\nname: docs\n---\n")
        self._refuse(monkeypatch, "unlink", api)

        report = ClaudeCodeGenerator().clean_legacy_report()

        assert not docs.exists()
        assert api.is_file()
        assert report.removed == [commands]
        assert report.skipped == [(api, _DELETE_DENIED)]

    @pytest.mark.skipif(
        sys.platform == "win32" or os.geteuid() == 0,
        reason="needs POSIX directory permissions and a non-root user",
    )
    def test_an_unwritable_parent_directory_is_skipped(self):
        rules = Path.home() / ".cursor" / "rules" / "deepctl.mdc"
        rules.parent.mkdir(parents=True)
        rules.write_text("---\nname: api\n---\n\n# api\n")
        rules.parent.chmod(0o555)
        try:
            report = CursorGenerator().clean_legacy_report()
        finally:
            rules.parent.chmod(0o755)

        assert report.removed == []
        assert report.skipped == [(rules, _DELETE_DENIED)]
        assert rules.is_file()

    def test_a_file_that_cannot_be_read_says_so_and_is_retryable(self, monkeypatch):
        """Unreadable is not "yours": deepctl may well have written it.

        Before, the read error was swallowed and the file was called the
        user's, which is wrong advice for a file deepctl wrote and lost
        its read permission.
        """
        rules = Path.home() / ".cursor" / "rules" / "deepctl.mdc"
        rules.parent.mkdir(parents=True)
        rules.write_text("---\nname: api\n---\n\n# api\n")
        self._refuse(monkeypatch, "open", rules)

        report = CursorGenerator().clean_legacy_report()

        assert report.removed == []
        assert report.skipped == [
            (
                rules,
                "could not be read: Permission denied. Fix its permissions, "
                "or delete it by hand if it is deepctl's.",
            )
        ]
        assert report.retryable == [rules]
        assert rules.is_file()

    def test_a_refused_delete_is_retryable_and_a_foreign_file_is_not(self, monkeypatch):
        rules = Path.home() / ".cursor" / "rules" / "deepctl.mdc"
        rules.parent.mkdir(parents=True)
        rules.write_text("---\nname: api\n---\n\n# api\n")
        self._refuse(monkeypatch, "unlink", rules)
        assert CursorGenerator().clean_legacy_report().retryable == [rules]

        rules.write_text("my own rules\n")
        report = CursorGenerator().clean_legacy_report()
        assert report.skipped == [(rules, skill_generator._NOT_DEEPCTLS)]
        assert report.retryable == []

    def test_the_not_deepctls_reason_is_a_full_sentence(self):
        assert skill_generator._NOT_DEEPCTLS.endswith(".")

    def test_a_folder_where_a_legacy_file_was_is_left_alone(self):
        """A file-type legacy path holding a folder is not deepctl's.

        It used to be rmtree'd with no header check, and reported as
        removed even when the deletion failed.
        """
        rules = Path.home() / ".cursor" / "rules" / "deepctl.mdc"
        rules.mkdir(parents=True)
        (rules / "mine.md").write_text("my notes\n")

        report = CursorGenerator().clean_legacy_report()

        assert report.removed == []
        assert report.skipped == [
            (rules, "is a folder, not the file deepctl wrote; left in place.")
        ]
        assert report.retryable == []
        assert (rules / "mine.md").read_text() == "my notes\n"


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):
        pytest.skip("this filesystem does not allow creating symlinks")


class TestAiderConfigCleanup:
    """~/.aider.conf.yml is the user's file; only deepctl's line comes out."""

    def _conf(self, text):
        conf = AiderGenerator._config_path()
        conf.write_text(text)
        return conf

    def test_the_edit_keeps_comments_order_and_other_keys(self):
        ref = str(AiderGenerator._LEGACY_FILE)
        conf = self._conf(
            "# my aider config\n"
            "model: gpt-4o   # keep this\n"
            "read:\n"
            "  # conventions I always load\n"
            "  - ~/CONVENTIONS.md\n"
            f"  - {ref}\n"
            "dark-mode: true\n"
        )

        report = AiderGenerator().clean_legacy_report()

        assert conf in report.removed
        assert report.skipped == []
        assert conf.read_text() == (
            "# my aider config\n"
            "model: gpt-4o   # keep this\n"
            "read:\n"
            "  # conventions I always load\n"
            "  - ~/CONVENTIONS.md\n"
            "dark-mode: true\n"
        )

    def test_a_quoted_or_tilde_entry_is_recognised(self):
        home = Path.home()
        tilde = "~/" + str(AiderGenerator._LEGACY_FILE.relative_to(home))
        conf = self._conf(f'read:\n  - "{tilde}"\n  - other.md\n')

        AiderGenerator().clean_legacy()

        assert conf.read_text() == "read:\n  - other.md\n"

    def test_the_last_entry_takes_the_key_with_it(self):
        ref = str(AiderGenerator._LEGACY_FILE)
        conf = self._conf(f"model: gpt-4o\nread:\n  - {ref}\nauto-commits: false\n")

        AiderGenerator().clean_legacy()

        assert conf.read_text() == "model: gpt-4o\nauto-commits: false\n"

    def test_a_scalar_read_is_dropped(self):
        ref = str(AiderGenerator._LEGACY_FILE)
        conf = self._conf(f"model: gpt-4o\nread: {ref}\n")

        AiderGenerator().clean_legacy()

        assert conf.read_text() == "model: gpt-4o\n"

    def test_a_flow_list_keeps_its_other_entries(self):
        ref = str(AiderGenerator._LEGACY_FILE)
        conf = self._conf(f"read: [a.md, {ref}, b.md]\n")

        AiderGenerator().clean_legacy()

        assert conf.read_text() == "read: [a.md, b.md]\n"

    def test_a_config_without_the_entry_is_untouched(self):
        text = "model: gpt-4o\nread:\n  - ~/CONVENTIONS.md\n# trailing comment\n"
        conf = self._conf(text)
        before = conf.stat().st_mtime_ns

        report = AiderGenerator().clean_legacy_report()

        assert report.removed == []
        assert report.skipped == []
        assert conf.read_text() == text
        assert conf.stat().st_mtime_ns == before

    def test_no_config_at_all_is_not_an_error(self):
        report = AiderGenerator().clean_legacy_report()
        assert report.removed == []
        assert report.skipped == []

    def test_a_config_reached_through_a_symlink_is_refused(self, tmp_path):
        """A rename would replace the link with a file; say so instead."""
        ref = str(AiderGenerator._LEGACY_FILE)
        real = tmp_path / "dotfiles" / "aider.conf.yml"
        real.parent.mkdir()
        real.write_text(f"read:\n  - {ref}\n")
        conf = AiderGenerator._config_path()
        _symlink_or_skip(conf, real)

        report = AiderGenerator().clean_legacy_report()

        assert report.removed == []
        assert report.skipped == [
            (
                conf,
                "is a symlink, which deepctl never writes through; "
                f"remove the {ref} entry by hand.",
            )
        ]
        assert real.read_text() == f"read:\n  - {ref}\n"
        assert conf.is_symlink()

    def test_a_symlinked_config_without_the_entry_is_not_reported(self, tmp_path):
        """Nothing to remove by hand, so no warning on every run."""
        real = tmp_path / "dotfiles" / "aider.conf.yml"
        real.parent.mkdir()
        real.write_text("model: gpt-4o\n")
        conf = AiderGenerator._config_path()
        _symlink_or_skip(conf, real)

        report = AiderGenerator().clean_legacy_report()

        assert report.removed == []
        assert report.skipped == []
        assert conf.is_symlink()

    def test_a_non_utf8_config_without_the_entry_is_not_reported(self):
        """A Latin-1 config that never named the entry is not a warning."""
        data = "model: gpt-4o\n# caf\xe9\n".encode("latin-1")
        conf = self._write_bytes(data)

        report = AiderGenerator().clean_legacy_report()

        assert report.removed == []
        assert report.skipped == []
        assert conf.read_bytes() == data

    def test_a_non_utf8_config_naming_the_entry_is_reported(self):
        ref = str(AiderGenerator._LEGACY_FILE)
        data = f"# caf\xe9\nread:\n  - {ref}\n".encode("latin-1")
        conf = self._write_bytes(data)

        report = AiderGenerator().clean_legacy_report()

        assert report.removed == []
        assert report.skipped == [
            (
                conf,
                "is not UTF-8 text, so deepctl will not edit it; "
                f"remove the {ref} entry by hand.",
            )
        ]
        assert conf.read_bytes() == data

    def test_a_legacy_file_reached_through_a_symlink_is_refused(self, tmp_path):
        real = tmp_path / "elsewhere.md"
        real.write_text(render_developer_guide("0.2.3"))
        link = tmp_path / "deepctl.mdc"
        _symlink_or_skip(link, real)

        gen = CursorGenerator()
        with patch.object(gen, "legacy_paths", return_value=[LegacyArtifact(link)]):
            report = gen.clean_legacy_report()

        assert report.removed == []
        assert [p for p, _ in report.skipped] == [link]
        assert real.exists() and link.is_symlink()

    @staticmethod
    def _write_bytes(data: bytes) -> Path:
        conf = AiderGenerator._config_path()
        conf.write_bytes(data)
        return conf

    def test_crlf_line_endings_are_kept(self):
        ref = str(AiderGenerator._LEGACY_FILE)
        conf = self._write_bytes(
            f"model: gpt-4o\r\nread:\r\n  - a.md\r\n  - {ref}\r\nx: 1\r\n".encode()
        )

        AiderGenerator().clean_legacy()

        assert conf.read_bytes() == b"model: gpt-4o\r\nread:\r\n  - a.md\r\nx: 1\r\n"

    def test_lf_line_endings_are_kept(self):
        """No translation to the platform's newline, on Windows either."""
        ref = str(AiderGenerator._LEGACY_FILE)
        conf = self._write_bytes(f"read:\n  - a.md\n  - {ref}\n".encode())

        AiderGenerator().clean_legacy()

        assert conf.read_bytes() == b"read:\n  - a.md\n"

    def test_a_comment_on_the_key_line_is_kept(self):
        ref = str(AiderGenerator._LEGACY_FILE)
        conf = self._conf(f"read:  # my read files\n  - a.md\n  - {ref}\n")

        AiderGenerator().clean_legacy()

        assert conf.read_text() == "read:  # my read files\n  - a.md\n"

    def test_a_comment_on_a_key_that_goes_is_kept_on_its_own_line(self):
        ref = str(AiderGenerator._LEGACY_FILE)
        conf = self._conf(f"model: x\nread:  # my read files\n  - {ref}\ny: 1\n")

        AiderGenerator().clean_legacy()

        assert conf.read_text() == "model: x\n# my read files\ny: 1\n"

    def test_a_flow_list_keeps_the_users_spacing(self):
        ref = str(AiderGenerator._LEGACY_FILE)
        conf = self._conf(f"read: [a.md,b.md,  {ref}]\n")

        AiderGenerator().clean_legacy()

        assert conf.read_text() == "read: [a.md,b.md]\n"

    def test_a_flow_list_first_entry_hands_its_spacing_on(self):
        ref = str(AiderGenerator._LEGACY_FILE)
        conf = self._conf(f"read: [ {ref},  a.md ]\n")

        AiderGenerator().clean_legacy()

        assert conf.read_text() == "read: [ a.md ]\n"

    def test_a_flow_list_with_a_trailing_comment(self):
        ref = str(AiderGenerator._LEGACY_FILE)
        conf = self._conf(f"read: [a.md, {ref}]  # c\n")

        report = AiderGenerator().clean_legacy_report()

        assert report.removed == [conf]
        assert conf.read_text() == "read: [a.md]  # c\n"

    def test_a_flow_list_with_only_the_entry_keeps_its_comment(self):
        ref = str(AiderGenerator._LEGACY_FILE)
        conf = self._conf(f"read: [{ref}]  # c\nmodel: x\n")

        AiderGenerator().clean_legacy()

        assert conf.read_text() == "# c\nmodel: x\n"

    def test_an_irregular_flow_list_is_left_alone_with_a_reason(self):
        ref = str(AiderGenerator._LEGACY_FILE)
        text = f"read: [a.md,\n  {ref}]\n"
        conf = self._conf(text)

        report = AiderGenerator().clean_legacy_report()

        assert report.removed == []
        assert report.skipped == [
            (
                conf,
                "has a read: list deepctl could not edit safely; "
                f"remove the {ref} entry by hand.",
            )
        ]
        assert report.retryable == []
        assert conf.read_text() == text

    def test_an_irregular_flow_list_without_the_entry_is_not_reported(self):
        text = "read: [a.md, [b.md]]\n"
        conf = self._conf(text)

        report = AiderGenerator().clean_legacy_report()

        assert report.skipped == []
        assert conf.read_text() == text

    def test_a_read_only_config_is_left_alone(self, monkeypatch):
        """The same as a read-only GEMINI.md, not replaced by a rename."""
        ref = str(AiderGenerator._LEGACY_FILE)
        text = f"read:\n  - a.md\n  - {ref}\n"
        conf = self._conf(text)
        real_access = os.access

        def access(path, mode, *args, **kwargs):
            if Path(path) == conf and mode == os.W_OK:
                return False
            return real_access(path, mode, *args, **kwargs)

        monkeypatch.setattr(skill_generator.os, "access", access)

        report = AiderGenerator().clean_legacy_report()

        assert report.removed == []
        assert report.skipped == [
            (
                conf,
                "could not be edited: Permission denied. Fix its permissions, "
                f"or remove the {ref} entry by hand.",
            )
        ]
        assert report.retryable == [conf]
        assert conf.read_text() == text

    def test_an_unwritable_home_names_the_config_not_the_temp_file(self, monkeypatch):
        ref = str(AiderGenerator._LEGACY_FILE)
        text = f"read:\n  - a.md\n  - {ref}\n"
        conf = self._conf(text)

        def denied(*args, **kwargs):
            raise PermissionError(
                13, "Permission denied", str(conf.parent / ".aider.conf.yml.x.tmp")
            )

        monkeypatch.setattr(skill_generator.tempfile, "NamedTemporaryFile", denied)

        report = AiderGenerator().clean_legacy_report()

        assert report.skipped == [
            (
                conf,
                "could not be rewritten: Permission denied. deepctl replaces "
                "the file in one step, which needs write permission on its "
                f"folder. Fix that, or remove the {ref} entry by hand.",
            )
        ]
        assert ".tmp" not in report.skipped[0][1]
        assert report.retryable == [conf]
        assert conf.read_text() == text

    def test_the_temp_file_has_one_leading_dot(self, monkeypatch):
        seen = {}
        real = skill_generator.tempfile.NamedTemporaryFile

        def spy(*args, **kwargs):
            seen["prefix"] = kwargs["prefix"]
            return real(*args, **kwargs)

        monkeypatch.setattr(skill_generator.tempfile, "NamedTemporaryFile", spy)
        conf = AiderGenerator._config_path()
        skill_generator._atomic_write_text(conf, "x: 1\n")

        assert seen["prefix"] == ".aider.conf.yml."


class TestAtomicSkillInstall:
    """A skill folder on disk is always the previous content or recorded.

    The old sequence was rmtree(dest) then copytree(src, dest), with the
    ownership record written only in an `except Exception` handler. A
    Ctrl-C mid-copy -- a KeyboardInterrupt, not an Exception -- left a
    half-written folder nothing recorded, which the next install then
    refused to overwrite as somebody else's.
    """

    def _gen(self, tmp_path, cli_name="claude"):
        gen = ClaudeCodeGenerator()
        gen.cli_name = cli_name
        gen.display_name = cli_name
        root = tmp_path / cli_name / "skills"
        root.mkdir(parents=True)
        return gen, root

    def _run(self, generators, roots, skills, state, **kwargs):
        def skills_root(self, _roots=roots):
            return _roots[self.cli_name]

        with (
            patch.object(ClaudeCodeGenerator, "skills_root", skills_root),
            patch.object(ClaudeCodeGenerator, "legacy_paths", lambda self: []),
            patch.object(skill_generator, "save_skills_state") as save,
            patch.object(skill_generator, "fetch_repo_skills", return_value=skills),
        ):
            self.save = save
            return skill_generator.install_skills_for(
                generators,
                state,
                commands=[_make_command()],
                version="9.9.9",
                **kwargs,
            )

    @staticmethod
    def _interrupt_while_copying(name):
        """A copytree that gets partway into `name`'s staging copy and dies."""

        real_copytree = shutil.copytree

        def copytree(src, dst, *args, **kwargs):
            if f".{name}." in Path(dst).name:
                Path(dst).mkdir()
                (Path(dst) / "SKILL.md").write_text("half of it")
                raise KeyboardInterrupt
            return real_copytree(src, dst, *args, **kwargs)

        return copytree

    def test_an_interrupted_copy_leaves_no_unrecorded_folder(self, tmp_path):
        gen, root = self._gen(tmp_path)
        skills = [_fake_skill(tmp_path, n) for n in ("api", "docs")]
        state = {"installed_skills": {}}

        with (
            patch.object(
                skill_generator.shutil,
                "copytree",
                self._interrupt_while_copying("docs"),
            ),
            pytest.raises(KeyboardInterrupt),
        ):
            self._run([gen], {"claude": root}, skills, state)

        # Nothing of docs -- not the folder, not the staging copy.
        assert sorted(p.name for p in root.iterdir()) == ["api"]
        # And what did land is recorded, on disk, before the interrupt
        # reached the caller.
        entry = state["installed_skills"]["claude"]
        assert [Path(p).name for p in entry["paths"]] == ["api"]
        assert entry["skills"] == ["api"]
        self.save.assert_called_with(state)

    def test_an_interrupted_copy_keeps_the_previous_folder_intact(self, tmp_path):
        gen, root = self._gen(tmp_path)
        skills = [_fake_skill(tmp_path, "api")]
        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
        ):
            written = gen.install_skills(skills)
            (root / "api" / "SKILL.md").write_text("the previous release")
            with (
                patch.object(
                    skill_generator.shutil,
                    "copytree",
                    self._interrupt_while_copying("api"),
                ),
                pytest.raises(KeyboardInterrupt),
            ):
                gen.install_skills(skills, written)

        assert (root / "api" / "SKILL.md").read_text() == "the previous release"
        assert sorted(p.name for p in root.iterdir()) == ["api"]

    def test_a_failed_move_aside_leaves_the_previous_folder_in_place(self, tmp_path):
        """Rename one of two: the old folder could not be moved aside."""
        gen, root = self._gen(tmp_path)
        skills = [_fake_skill(tmp_path, "api")]
        real_replace = os.replace

        def replace(src, dst, *args, **kwargs):
            if ".old-" in Path(dst).name:
                raise PermissionError(13, "Permission denied", str(dst))
            return real_replace(src, dst, *args, **kwargs)

        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
        ):
            written = gen.install_skills(skills)
            (root / "api" / "SKILL.md").write_text("the previous release")
            with (
                patch.object(skill_generator.os, "replace", replace),
                pytest.raises(PermissionError),
            ):
                gen.install_skills(skills, written)

        assert (root / "api" / "SKILL.md").read_text() == "the previous release"
        assert sorted(p.name for p in root.iterdir()) == ["api"]

    def test_a_failed_final_rename_puts_the_previous_folder_back(self, tmp_path):
        """Rename two of two: moved aside, but the new copy would not land."""
        gen, root = self._gen(tmp_path)
        skills = [_fake_skill(tmp_path, "api")]
        real_replace = os.replace

        def replace(src, dst, *args, **kwargs):
            if ".tmp-" in Path(src).name:
                raise OSError(5, "Input/output error", str(dst))
            return real_replace(src, dst, *args, **kwargs)

        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
        ):
            written = gen.install_skills(skills)
            (root / "api" / "SKILL.md").write_text("the previous release")
            with (
                patch.object(skill_generator.os, "replace", replace),
                pytest.raises(OSError),
            ):
                gen.install_skills(skills, written)

        assert (root / "api" / "SKILL.md").read_text() == "the previous release"
        assert sorted(p.name for p in root.iterdir()) == ["api"]

    def test_each_skill_is_recorded_the_moment_it_lands(self, tmp_path):
        """Not once per tool: the record grows skill by skill."""
        gen, root = self._gen(tmp_path)
        skills = [_fake_skill(tmp_path, n) for n in ("api", "docs", "cli")]
        state = {"installed_skills": {}}
        snapshots = []

        def skills_root(self, _root=root):
            return _root

        with (
            patch.object(ClaudeCodeGenerator, "skills_root", skills_root),
            patch.object(ClaudeCodeGenerator, "legacy_paths", lambda self: []),
            patch.object(
                skill_generator,
                "save_skills_state",
                side_effect=lambda s: snapshots.append(
                    [Path(p).name for p in s["installed_skills"]["claude"]["paths"]]
                ),
            ),
            patch.object(skill_generator, "fetch_repo_skills", return_value=skills),
        ):
            skill_generator.install_skills_for(
                [gen], state, commands=[_make_command()], version="9.9.9"
            )

        assert snapshots[:3] == [["api"], ["api", "docs"], ["api", "cli", "docs"]]
        # Then the tool's own record, in install order.
        assert snapshots[-1] == ["api", "docs", "cli"]

    def test_a_partial_record_keeps_the_previous_ref_and_version(self, tmp_path):
        """Half-updated is not current: the new ref lands with the last skill.

        A bare `dg skills update` follows the recorded ref, so a record
        stamped with the new ref while `docs` still holds the old release
        would make it skip exactly the tool that needs finishing.
        """
        gen, root = self._gen(tmp_path)
        skills = [_fake_skill(tmp_path, n) for n in ("api", "docs")]
        state = {"installed_skills": {}}
        self._run([gen], {"claude": root}, skills, state)
        entry = state["installed_skills"]["claude"]
        entry["skills_ref"] = "deepgram-skills-v0.9.0"
        entry["version"] = "0.3.0"
        entry["commands_hash"] = "old-hash"
        entry["installed_at"] = "2025-01-01T00:00:00+00:00"

        with (
            patch.object(
                skill_generator.shutil,
                "copytree",
                self._interrupt_while_copying("docs"),
            ),
            pytest.raises(KeyboardInterrupt),
        ):
            self._run([gen], {"claude": root}, skills, state)

        entry = state["installed_skills"]["claude"]
        assert sorted(Path(p).name for p in entry["paths"]) == ["api", "docs"]
        assert entry["skills_ref"] == "deepgram-skills-v0.9.0"
        assert entry["version"] == "0.3.0"
        assert entry["commands_hash"] == "old-hash"
        assert entry["installed_at"] == "2025-01-01T00:00:00+00:00"
        # Saved that way, not only held in memory.
        self.save.assert_called_with(state)

        # Finishing the update is what stamps the new ref.
        self._run([gen], {"claude": root}, skills, state)
        entry = state["installed_skills"]["claude"]
        assert entry["skills_ref"] == DEFAULT_SKILLS_REF
        assert entry["version"] == "9.9.9"

    def test_a_partial_first_install_carries_no_ref_at_all(self, tmp_path):
        """Nothing to keep, so nothing is claimed: no ref, no version."""
        gen, root = self._gen(tmp_path)
        skills = [_fake_skill(tmp_path, n) for n in ("api", "docs", "cli")]
        state = {"installed_skills": {}}
        snapshots = []

        def skills_root(self, _root=root):
            return _root

        with (
            patch.object(ClaudeCodeGenerator, "skills_root", skills_root),
            patch.object(ClaudeCodeGenerator, "legacy_paths", lambda self: []),
            patch.object(
                skill_generator,
                "save_skills_state",
                side_effect=lambda s: snapshots.append(
                    dict(s["installed_skills"]["claude"])
                ),
            ),
            patch.object(skill_generator, "fetch_repo_skills", return_value=skills),
        ):
            skill_generator.install_skills_for(
                [gen], state, commands=[_make_command()], version="9.9.9"
            )

        partial, complete = snapshots[:-1], snapshots[-1]
        assert len(partial) == 3
        for snap in partial:
            assert "skills_ref" not in snap
            assert "version" not in snap
            assert sorted(snap) == ["paths", "skills"]
        assert complete["skills_ref"] == DEFAULT_SKILLS_REF
        assert complete["version"] == "9.9.9"
        assert complete["skills"] == ["api", "docs", "cli"]

    def test_a_reinstall_keeps_the_not_yet_replaced_folders_recorded(self, tmp_path):
        """Mid-update, the folders still to be replaced are the old release's."""
        gen, root = self._gen(tmp_path)
        skills = [_fake_skill(tmp_path, n) for n in ("api", "docs")]
        state = {"installed_skills": {}}
        self._run([gen], {"claude": root}, skills, state)
        recorded_before = list(state["installed_skills"]["claude"]["paths"])

        with (
            patch.object(
                skill_generator.shutil,
                "copytree",
                self._interrupt_while_copying("docs"),
            ),
            pytest.raises(KeyboardInterrupt),
        ):
            self._run([gen], {"claude": root}, skills, state)

        entry = state["installed_skills"]["claude"]
        assert sorted(entry["paths"]) == sorted(recorded_before)
        assert (root / "docs" / "SKILL.md").is_file()

    def test_stale_staging_and_old_siblings_are_swept(self, tmp_path):
        """Leftovers of an interrupted run from another process go too."""
        gen, root = self._gen(tmp_path)
        for name in (".api.tmp-4242", ".api.old-4242-0"):
            (root / name).mkdir()
            (root / name / "SKILL.md").write_text("leftover")
        skills = [_fake_skill(tmp_path, "api")]

        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
        ):
            written = gen.install_skills(skills)
            assert sorted(p.name for p in root.iterdir()) == ["api"]
            (root / ".api.old-4242-1").mkdir()
            gen.remove_report(written)

        assert list(root.iterdir()) == []

    def test_the_sweep_takes_only_the_shapes_this_module_writes(self, tmp_path):
        """A user's `.api.old-backup` is not deepctl's leftover."""
        _gen, root = self._gen(tmp_path)
        for name in (
            ".api.old-backup",
            ".api.tmp-notes",
            ".api.tmp-123",
            ".api.old-123-1",
        ):
            (root / name).mkdir()
            (root / name / "SKILL.md").write_text("content")
        (root / ".api.tmp-123-extra").mkdir()
        (root / ".api.old-123").mkdir()

        skill_generator._sweep_stale_siblings(root / "api")

        assert sorted(p.name for p in root.iterdir()) == [
            ".api.old-123",
            ".api.old-backup",
            ".api.tmp-123-extra",
            ".api.tmp-notes",
        ]
        assert (root / ".api.old-backup" / "SKILL.md").read_text() == "content"

        # And the real generators still produce names the sweep matches.
        for sibling in (
            skill_generator._staging_sibling(root / "api"),
            skill_generator._unique_sibling(root / "api", "old"),
        ):
            sibling.mkdir()
        skill_generator._sweep_stale_siblings(root / "api")
        assert sorted(p.name for p in root.iterdir()) == [
            ".api.old-123",
            ".api.old-backup",
            ".api.tmp-123-extra",
            ".api.tmp-notes",
        ]

    def test_a_stranded_retired_folder_stays_recorded_after_install(self, tmp_path):
        """prune_retired dropping it from the record would strand it for good."""
        gen, root = self._gen(tmp_path)
        state = {"installed_skills": {}}
        self._run(
            [gen],
            {"claude": root},
            [_fake_skill(tmp_path, n) for n in ("api", "retired")],
            state,
        )

        def denied(path, ignore_errors=False, **kwargs):
            if not ignore_errors:
                raise PermissionError(13, "Permission denied", str(path))

        with patch.object(skill_generator.shutil, "rmtree", denied):
            report = self._run(
                [gen], {"claude": root}, [_fake_skill(tmp_path, "api")], state
            )

        assert report.stranded == {"claude": [root / "retired"]}
        entry = state["installed_skills"]["claude"]
        assert sorted(Path(p).name for p in entry["paths"]) == ["api", "retired"]
        assert (root / "retired" / "SKILL.md").is_file()

    def test_a_pruned_retired_folder_is_in_the_report(self, tmp_path):
        """Deleted on deepctl's own initiative, so the caller can say so."""
        gen, root = self._gen(tmp_path)
        gen.display_name = "Claude Code"
        state = {"installed_skills": {}}
        self._run(
            [gen],
            {"claude": root},
            [_fake_skill(tmp_path, n) for n in ("api", "self-hosted")],
            state,
        )

        report = self._run(
            [gen], {"claude": root}, [_fake_skill(tmp_path, "api")], state, ref="v9"
        )

        assert report.pruned == {"claude": [root / "self-hosted"]}
        assert report.stranded == {}
        assert report.pruned_notices == [
            "Removed retired skill self-hosted from Claude Code "
            "(no longer in deepgram/skills@v9)"
        ]
        assert not (root / "self-hosted").exists()

    def test_nothing_pruned_means_no_notices(self, tmp_path):
        gen, root = self._gen(tmp_path)
        state = {"installed_skills": {}}
        skills = [_fake_skill(tmp_path, "api")]
        self._run([gen], {"claude": root}, skills, state)

        report = self._run([gen], {"claude": root}, skills, state)

        assert report.pruned == {}
        assert report.pruned_notices == []

    def test_legacy_files_left_alone_are_in_the_report(self, tmp_path):
        gen, root = self._gen(tmp_path)
        rules = tmp_path / "deepctl.mdc"
        rules.write_text("mine\n")
        state = {"installed_skills": {}}

        def skills_root(self, _root=root):
            return _root

        with (
            patch.object(ClaudeCodeGenerator, "skills_root", skills_root),
            patch.object(
                ClaudeCodeGenerator,
                "legacy_paths",
                lambda self: [LegacyArtifact(rules)],
            ),
            patch.object(skill_generator, "save_skills_state"),
            patch.object(
                skill_generator,
                "fetch_repo_skills",
                return_value=[_fake_skill(tmp_path, "api")],
            ),
        ):
            report = skill_generator.install_skills_for(
                [gen], state, commands=[_make_command()], version="9.9.9"
            )

        assert report.legacy_skipped == [
            ("claude", rules, skill_generator._NOT_DEEPCTLS)
        ]
        assert rules.read_text() == "mine\n"


def _hold_records_lock(state_file: str, held, release) -> None:
    """Child process: hold the records lock on ``state_file`` until released.

    Module level so a spawned interpreter can import it by name. The
    parent's monkeypatches do not cross the process boundary, so the
    records location is passed in and set here.
    """
    from deepctl_core import skill_generator as child_generator

    child_generator._STATE_FILE = Path(state_file)
    with child_generator.skills_state_lock(timeout=5.0):
        held.set()
        release.wait(10.0)


class TestSkillsStateLock:
    """Two deepctl processes must not read-modify-write skills.json at once."""

    def test_a_lock_held_by_another_process_times_out(self, monkeypatch):
        """A real second interpreter, not a second handle in this one."""
        # pytest's importlib mode imports this file as a package-qualified
        # name without putting its root on sys.path; a spawned child gets
        # the parent's sys.path, so add the root for it to unpickle the
        # target by name.
        depth = len(__name__.split("."))
        monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[depth - 1]))
        ctx = multiprocessing.get_context("spawn")
        held = ctx.Event()
        release = ctx.Event()
        child = ctx.Process(
            target=_hold_records_lock,
            args=(str(skill_generator.skills_state_file()), held, release),
            daemon=True,
        )
        child.start()
        try:
            # Poll so a child that dies on startup fails fast, not at 30s.
            deadline = time.monotonic() + 30.0
            while not held.wait(0.05):
                assert child.is_alive(), f"the child exited ({child.exitcode})"
                assert time.monotonic() < deadline, "the child never took the lock"
            with (
                pytest.raises(SkillsStateLockTimeout) as excinfo,
                skills_state_lock(timeout=0.3),
            ):
                pass  # pragma: no cover - never entered
        finally:
            release.set()
            child.join(30.0)

        assert child.exitcode == 0
        assert excinfo.value.lock_path == skill_generator._lock_file()
        # The child has exited and let go, so this process can take it.
        with skills_state_lock(timeout=2.0):
            pass

    def test_the_lock_file_sits_next_to_the_state_file(self):
        state = skill_generator.skills_state_file()
        assert skill_generator._lock_file() == state.parent / "skills.json.lock"

    def test_a_lock_held_elsewhere_times_out(self):
        """A second open file description stands in for another process."""
        lock_path = skill_generator._lock_file()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+b") as other:
            assert skill_generator._try_lock(other)
            try:
                with (
                    pytest.raises(SkillsStateLockTimeout) as excinfo,
                    skills_state_lock(timeout=0.2),
                ):
                    pass  # pragma: no cover - never entered
            finally:
                skill_generator._unlock(other)

        # Released: the lock is free again.
        with skills_state_lock(timeout=0.2):
            pass

        # A subclass, so a caller catching SkillsStateError still does.
        assert isinstance(excinfo.value, SkillsStateError)
        assert excinfo.value.lock_path == lock_path
        message = str(excinfo.value)
        assert "Another deepctl process holds" in message
        assert str(lock_path) in message
        assert "retry" in message
        # flock drops when its holder dies, so a stale lock file cannot
        # exist and the user must never be told to delete it.
        assert "delete" not in message.lower()

    def test_a_lock_file_that_cannot_be_opened_is_not_a_timeout(self, monkeypatch):
        lock_path = skill_generator._lock_file()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        # A directory where the lock file goes: open(..., "a+b") fails.
        lock_path.mkdir()
        with pytest.raises(SkillsStateError) as excinfo, skills_state_lock(timeout=0.2):
            pass  # pragma: no cover - never entered
        assert not isinstance(excinfo.value, SkillsStateLockTimeout)
        assert "Cannot open" in str(excinfo.value)

    def test_a_lock_held_by_another_thread_is_the_same_timeout(self):
        import threading

        taken = threading.Event()
        release = threading.Event()

        def hold():
            with skills_state_lock(timeout=1.0):
                taken.set()
                release.wait(5.0)

        holder = threading.Thread(target=hold)
        holder.start()
        try:
            assert taken.wait(5.0)
            with (
                pytest.raises(SkillsStateLockTimeout, match="deepctl thread holds"),
                skills_state_lock(timeout=0.2),
            ):
                pass  # pragma: no cover - never entered
        finally:
            release.set()
            holder.join(5.0)

    def test_the_lock_is_reentrant_within_a_process(self):
        with skills_state_lock(timeout=0.2):
            with skills_state_lock(timeout=0.2):
                assert skill_generator._state_lock_depth == 2
            assert skill_generator._state_lock_depth == 1
        assert skill_generator._state_lock_depth == 0
        assert skill_generator._state_lock_handle is None

    def test_install_skills_for_gives_up_when_the_lock_is_held(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(skill_generator, "_STATE_LOCK_TIMEOUT", 0.2)
        gen = ClaudeCodeGenerator()
        root = tmp_path / "skills"
        root.mkdir()
        state = {"installed_skills": {}}
        lock_path = skill_generator._lock_file()
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with open(lock_path, "a+b") as other:
            assert skill_generator._try_lock(other)
            try:
                with (
                    patch.object(gen, "skills_root", return_value=root),
                    patch.object(gen, "legacy_paths", return_value=[]),
                    patch.object(
                        skill_generator,
                        "fetch_repo_skills",
                        return_value=[_fake_skill(tmp_path, "api")],
                    ) as fetch,
                    patch.object(skill_generator, "save_skills_state") as save,
                    pytest.raises(SkillsStateLockTimeout),
                ):
                    skill_generator.install_skills_for(
                        [gen], state, commands=[_make_command()], version="1"
                    )
            finally:
                skill_generator._unlock(other)

        # The bundle is fetched before the lock is taken (a slow download
        # must not make the other process's wait time out), but nothing
        # is written or recorded once the lock cannot be had.
        fetch.assert_called_once()
        save.assert_not_called()
        assert list(root.iterdir()) == []
        assert state == {"installed_skills": {}}

    def test_the_fetch_runs_before_the_lock_is_taken(self, tmp_path):
        """The lock covers state and writes, not the network."""
        gen = ClaudeCodeGenerator()
        root = tmp_path / "skills"
        root.mkdir()
        state = {"installed_skills": {}}
        depth_at_fetch = []

        def fetch():
            depth_at_fetch.append(skill_generator._state_lock_depth)
            return [_fake_skill(tmp_path, "api")]

        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
            patch.object(skill_generator, "save_skills_state"),
        ):
            report = skill_generator.install_skills_for(
                [gen], state, commands=[_make_command()], version="1", fetch=fetch
            )

        assert depth_at_fetch == [0]
        assert report.written == {"claude": [root / "api"]}
        assert skill_generator._state_lock_depth == 0

    def test_a_failed_fetch_takes_no_lock_and_writes_nothing(self, tmp_path):
        gen = ClaudeCodeGenerator()
        root = tmp_path / "skills"
        root.mkdir()
        state = {"installed_skills": {}}
        lock_calls = []
        real_lock = skill_generator.skills_state_lock

        def lock(timeout=None):
            lock_calls.append(timeout)
            return real_lock(timeout)

        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(skill_generator, "skills_state_lock", lock),
            patch.object(skill_generator, "save_skills_state") as save,
            pytest.raises(SkillFetchError),
        ):
            skill_generator.install_skills_for(
                [gen],
                state,
                commands=[_make_command()],
                version="1",
                fetch=lambda: (_ for _ in ()).throw(SkillFetchError("offline")),
            )

        assert lock_calls == []
        save.assert_not_called()
        assert list(root.iterdir()) == []

    def test_remove_takes_the_lock_and_releases_it(self, tmp_path):
        gen = ClaudeCodeGenerator()
        root = tmp_path / "skills"
        root.mkdir()
        with (
            patch.object(gen, "skills_root", return_value=root),
            patch.object(gen, "legacy_paths", return_value=[]),
        ):
            written = gen.install_skills([_fake_skill(tmp_path, "api")])
            seen = []
            real_rmtree = shutil.rmtree

            def rmtree(path, *args, **kwargs):
                seen.append(skill_generator._state_lock_depth)
                return real_rmtree(path, *args, **kwargs)

            with patch.object(skill_generator.shutil, "rmtree", rmtree):
                assert gen.remove_report(written).removed == written

        assert seen == [1]
        assert skill_generator._state_lock_depth == 0


class TestInstallSkillsForLoadState:
    """``load_state`` re-reads skills.json under the lock, after the fetch.

    Without it a caller had to hold the lock across its own read to keep
    records current, which held the lock through the download too.
    """

    def _gen(self, tmp_path):
        gen = ClaudeCodeGenerator()
        root = tmp_path / "claude" / "skills"
        root.mkdir(parents=True)
        return gen, root

    def _patches(self, root, fetch=None, skills=()):
        return (
            patch.object(ClaudeCodeGenerator, "skills_root", lambda self: root),
            patch.object(ClaudeCodeGenerator, "legacy_paths", lambda self: []),
            patch.object(
                skill_generator,
                "fetch_repo_skills",
                side_effect=fetch if fetch is not None else lambda *a, **k: skills,
            ),
        )

    def _install(self, gen, state, **kwargs):
        return skill_generator.install_skills_for(
            [gen], state, commands=[_make_command()], version="9.9.9", **kwargs
        )

    def test_the_lock_is_free_while_the_fetch_runs(self, tmp_path):
        gen, root = self._gen(tmp_path)
        skills = [_fake_skill(tmp_path, "api")]
        fetching = threading.Event()
        release = threading.Event()

        def fetch(*args, **kwargs):
            fetching.set()
            assert release.wait(5.0)
            return skills

        state: dict = {}
        errors: list[BaseException] = []
        p1, p2, p3 = self._patches(root, fetch=fetch)
        with p1, p2, p3:

            def run():
                try:
                    self._install(gen, state, load_state=get_skills_state)
                except BaseException as exc:  # pragma: no cover - surfaced below
                    errors.append(exc)

            worker = threading.Thread(target=run)
            worker.start()
            try:
                assert fetching.wait(5.0)
                # Another thread (or process) can take the lock meanwhile.
                with skills_state_lock(timeout=0.5):
                    pass
            finally:
                release.set()
                worker.join(10.0)

        assert not worker.is_alive()
        assert errors == []
        assert (root / "api" / "SKILL.md").is_file()
        assert "claude" in get_skills_state()["installed_skills"]

    def test_a_record_saved_during_the_fetch_is_kept(self, tmp_path):
        gen, root = self._gen(tmp_path)
        skills = [_fake_skill(tmp_path, "api")]
        state = get_skills_state()  # the caller's first, soon stale, read
        other = {"paths": [], "skills": [], "version": "1.0.0"}

        def fetch(*args, **kwargs):
            # Another deepctl saves between the caller's read and the lock.
            with skills_state_lock():
                fresh = get_skills_state()
                fresh.setdefault("installed_skills", {})["cursor"] = other
                save_skills_state(fresh)
            return skills

        p1, p2, p3 = self._patches(root, fetch=fetch)
        with p1, p2, p3:
            self._install(gen, state, load_state=get_skills_state)

        saved = get_skills_state()["installed_skills"]
        assert saved["cursor"] == other
        assert sorted(Path(p).name for p in saved["claude"]["paths"]) == ["api"]
        # And the caller's dict is the fresh view, updated in place.
        assert state["installed_skills"]["cursor"] == other
        assert "claude" in state["installed_skills"]

    def test_load_state_raising_writes_nothing_and_propagates(self, tmp_path):
        gen, root = self._gen(tmp_path)
        before = {"installed_skills": {"cursor": {"paths": [], "skills": []}}}
        save_skills_state(before)
        state_file = skill_generator.skills_state_file()
        on_disk = state_file.read_bytes()
        state = json.loads(on_disk)
        refused = SkillsStateError("skills.json records are unreadable")

        def load_state():
            raise refused

        p1, p2, p3 = self._patches(root, skills=[_fake_skill(tmp_path, "api")])
        with p1, p2, p3, pytest.raises(SkillsStateError) as excinfo:
            self._install(gen, state, load_state=load_state)

        assert excinfo.value is refused
        assert state_file.read_bytes() == on_disk
        assert list(root.iterdir()) == []
        assert state == before

    # -- only_recorded: a remove during the download stays removed ---------

    def _two_tools(self, tmp_path):
        gens, roots = {}, {}
        for name in ("claude", "cursor"):
            gen = ClaudeCodeGenerator()
            gen.cli_name = name
            gen.display_name = name
            roots[name] = tmp_path / name / "skills"
            roots[name].mkdir(parents=True)
            gens[name] = gen
        return gens, roots

    def _update_removing(self, tmp_path, removed, **kwargs):
        """Install both tools, then update while ``removed`` are removed.

        The removal runs inside the fetch, the way a ``dg skills remove``
        in another terminal lands while this update is downloading.
        """
        gens, roots = self._two_tools(tmp_path)
        skills = [_fake_skill(tmp_path, n) for n in ("api", "docs")]
        on_disk: dict[str, bytes] = {}

        def remove_during_fetch():
            with skills_state_lock():
                fresh = get_skills_state()
                for name in removed:
                    report = gens[name].remove_report(recorded_skill_paths(fresh, name))
                    assert report.stranded == []
                    del fresh["installed_skills"][name]
                save_skills_state(fresh)
            on_disk["after_remove"] = skill_generator.skills_state_file().read_bytes()
            return skills

        def skills_root(self):
            return roots[self.cli_name]

        with (
            patch.object(ClaudeCodeGenerator, "skills_root", skills_root),
            patch.object(ClaudeCodeGenerator, "legacy_paths", lambda self: []),
        ):
            first = skill_generator.install_skills_for(
                list(gens.values()),
                get_skills_state(),
                commands=[_make_command()],
                version="9.9.9",
                fetch=lambda: skills,
            )
            assert set(first.written) == {"claude", "cursor"}
            state = get_skills_state()  # the update's first, soon stale, read
            report = skill_generator.install_skills_for(
                list(gens.values()),
                state,
                commands=[_make_command()],
                version="9.9.9",
                fetch=remove_during_fetch,
                load_state=get_skills_state,
                **kwargs,
            )
        return report, roots, state, on_disk["after_remove"]

    def test_a_tool_removed_during_the_fetch_is_not_reinstalled(self, tmp_path):
        """The bug: the update put back what `remove --cli claude` took."""
        report, roots, state, _ = self._update_removing(
            tmp_path, ["claude"], only_recorded=True
        )

        assert report.skipped_unrecorded == ["claude"]
        assert set(report.written) == {"cursor"}
        assert report.failures == []
        assert report.conflicts == []
        assert list(roots["claude"].iterdir()) == []
        assert (roots["cursor"] / "api" / "SKILL.md").is_file()
        saved = get_skills_state()["installed_skills"]
        assert set(saved) == {"cursor"}
        assert set(state["installed_skills"]) == {"cursor"}

    def test_removing_every_tool_during_the_fetch_writes_nothing(self, tmp_path):
        report, roots, _, after_remove = self._update_removing(
            tmp_path, ["claude", "cursor"], only_recorded=True
        )

        assert sorted(report.skipped_unrecorded) == ["claude", "cursor"]
        assert report.written == {}
        assert report.total_written == 0
        assert report.skills == []
        assert report.unsupported == []
        assert report.failures == []
        assert report.conflicts == []
        assert report.stranded == {}
        for root in roots.values():
            assert list(root.iterdir()) == []
        # Not even a re-save of the same records.
        assert skill_generator.skills_state_file().read_bytes() == after_remove
        assert get_skills_state()["installed_skills"] == {}

    def test_the_drop_runs_before_the_conflict_preflight(self, tmp_path):
        """A removed tool's folder is the user's again, so it is not a conflict.

        After `remove --cli claude`, the user may put their own skill at
        the path deepctl just freed. Preflighting claude before dropping
        it would refuse the whole update over a tool it is not updating.
        """
        gens, roots = self._two_tools(tmp_path)
        unsupported = ClaudeCodeGenerator()
        unsupported.cli_name = "amazonq"
        unsupported.display_name = "amazonq"
        skills = [_fake_skill(tmp_path, n) for n in ("api", "docs")]
        user_file = roots["claude"] / "api" / "SKILL.md"

        def remove_during_fetch():
            with skills_state_lock():
                fresh = get_skills_state()
                removed = gens["claude"].remove_report(
                    recorded_skill_paths(fresh, "claude")
                )
                assert removed.stranded == []
                del fresh["installed_skills"]["claude"]
                del fresh["installed_skills"]["amazonq"]
                save_skills_state(fresh)
            (roots["claude"] / "api").mkdir()
            user_file.write_text("mine\n")
            return skills

        def skills_root(self):
            return roots.get(self.cli_name)

        with (
            patch.object(ClaudeCodeGenerator, "skills_root", skills_root),
            patch.object(ClaudeCodeGenerator, "legacy_paths", lambda self: []),
        ):
            skill_generator.install_skills_for(
                list(gens.values()),
                get_skills_state(),
                commands=[_make_command()],
                version="9.9.9",
                fetch=lambda: skills,
            )
            seeded = get_skills_state()
            seeded["installed_skills"]["amazonq"] = {"paths": []}
            save_skills_state(seeded)
            report = skill_generator.install_skills_for(
                [*gens.values(), unsupported],
                get_skills_state(),
                commands=[_make_command()],
                version="9.9.9",
                fetch=remove_during_fetch,
                load_state=get_skills_state,
                only_recorded=True,
            )

        assert report.conflicts == []
        assert sorted(report.skipped_unrecorded) == ["amazonq", "claude"]
        assert report.unsupported == []
        assert set(report.written) == {"cursor"}
        assert report.failures == []
        assert user_file.read_text() == "mine\n"
        assert [p.name for p in roots["claude"].iterdir()] == ["api"]
        assert [p.name for p in (roots["claude"] / "api").iterdir()] == ["SKILL.md"]
        assert set(get_skills_state()["installed_skills"]) == {"cursor"}

    def test_without_only_recorded_every_requested_tool_is_installed(self, tmp_path):
        """An explicit install of a tool is not second-guessed by the record."""
        report, roots, _, _ = self._update_removing(tmp_path, ["claude"])

        assert report.skipped_unrecorded == []
        assert set(report.written) == {"claude", "cursor"}
        assert (roots["claude"] / "api" / "SKILL.md").is_file()
        assert set(get_skills_state()["installed_skills"]) == {"claude", "cursor"}

    def test_without_load_state_the_callers_state_is_used(self, tmp_path):
        gen, root = self._gen(tmp_path)
        state = {"installed_skills": {"cursor": {"paths": [], "skills": []}}}
        loads: list[int] = []
        p1, p2, p3 = self._patches(root, skills=[_fake_skill(tmp_path, "api")])
        with (
            p1,
            p2,
            p3,
            patch.object(
                skill_generator,
                "get_skills_state",
                side_effect=lambda: loads.append(1) or {},
            ),
        ):
            report = self._install(gen, state)

        assert loads == []
        assert report.written == {"claude": [root / "api"]}
        assert set(state["installed_skills"]) == {"claude", "cursor"}
        saved = json.loads(skill_generator.skills_state_file().read_text())
        assert set(saved["installed_skills"]) == {"claude", "cursor"}


class TestWithAdvice:
    """One sentence break between what went wrong and what to do."""

    @pytest.mark.parametrize(
        ("detail", "joined"),
        [
            ("It failed.", "It failed. Retry."),
            ("It failed", "It failed. Retry."),
            ("Is a directory: '/x/skills.json.lock'", "'/x/skills.json.lock'. Retry."),
            ("It failed.  ", "It failed. Retry."),
            ("Really?", "Really? Retry."),
            ("", "Retry."),
        ],
    )
    def test_joins_with_exactly_one_full_stop(self, detail, joined):
        assert skill_generator.with_advice(detail, "Retry.").endswith(joined)


_CLONES = pytest.mark.parametrize(
    "clone",
    [lambda e: pickle.loads(pickle.dumps(e)), copy.copy, copy.deepcopy],
    ids=["pickle", "copy", "deepcopy"],
)


class TestSkillErrorsRoundTrip:
    """BaseException rebuilds a copy or an unpickled error from ``args``,
    which for these held only the formatted message, not the arguments."""

    @_CLONES
    def test_lock_timeout_survives_pickle_and_copy(self, clone, tmp_path):
        exc = SkillsStateLockTimeout(tmp_path / "skills.lock", holder="thread")

        again = clone(exc)

        assert type(again) is SkillsStateLockTimeout
        assert again.lock_path == tmp_path / "skills.lock"
        assert again.holder == "thread"
        assert str(again) == str(exc)

    @_CLONES
    def test_ownership_error_survives_pickle_and_copy(self, clone, tmp_path):
        conflicts = [("api", tmp_path / "api"), ("docs", tmp_path / "docs")]
        exc = SkillOwnershipError(conflicts)

        again = clone(exc)

        assert type(again) is SkillOwnershipError
        assert again.conflicts == conflicts
        assert str(again) == str(exc)

    @staticmethod
    def _both(tmp_path):
        return [
            SkillsStateLockTimeout(tmp_path / "skills.lock", holder="thread"),
            SkillOwnershipError([("api", tmp_path / "api")]),
            skill_generator.SkillWriteError(tmp_path, "Permission denied", 13),
        ]

    @_CLONES
    def test_an_attribute_set_later_survives(self, clone, tmp_path):
        for exc in self._both(tmp_path):
            exc.retried = 2

            again = clone(exc)

            assert type(again) is type(exc)
            assert again.retried == 2
            assert str(again) == str(exc)

    @pytest.mark.skipif(sys.version_info < (3, 11), reason="add_note is 3.11+")
    @_CLONES
    def test_a_note_survives(self, clone, tmp_path):
        for exc in self._both(tmp_path):
            exc.add_note("while installing for Claude Code")

            again = clone(exc)

            assert again.__notes__ == ["while installing for Claude Code"]


class TestPendingRemoval:
    """A failed remove's record is not an install, and update must not
    reinstall the skills the user asked to remove."""

    @staticmethod
    def _leftover():
        legacy = Path.home() / ".claude" / "commands" / "deepgram"
        legacy.mkdir(parents=True)
        leftover = legacy / "api.md"
        leftover.write_text("---\nname: api\ndescription: x\n---\n")
        return legacy, leftover

    def test_only_the_remove_pending_marker_makes_a_pending_removal(self):
        """A record's shape says nothing; deepctl 0.3.x wrote this one."""
        _legacy, leftover = self._leftover()
        skill = str(Path.home() / ".claude" / "skills" / "api")

        assert not skill_generator.is_pending_removal(_v03_record([leftover]))
        assert not skill_generator.is_pending_removal(
            {"paths": [str(leftover)], "skills": []}
        )
        assert not skill_generator.is_pending_removal(
            {"paths": [skill], "skills": ["api"]}
        )
        assert not skill_generator.is_pending_removal({"paths": [], "skills": []})
        assert not skill_generator.is_pending_removal(None)
        assert not skill_generator.is_pending_removal(
            {"paths": [str(leftover)], "remove_pending": "yes"}
        )
        # Marked by remove, stranded skill folders included.
        assert skill_generator.is_pending_removal(
            {"paths": [skill], "skills": ["api"], "remove_pending": True}
        )

    def _run(self, disk, **kwargs):
        def load():
            return json.loads(json.dumps(disk))

        def save(state):
            disk.clear()
            disk.update(json.loads(json.dumps(state)))

        fetch = MagicMock(return_value=[])
        with patch.object(skill_generator, "save_skills_state", side_effect=save):
            report = skill_generator.install_skills_for(
                [ClaudeCodeGenerator()],
                {},
                commands=[_make_command()],
                version="9.9.9",
                fetch=fetch,
                load_state=load,
                only_recorded=True,
                **kwargs,
            )
        return report, fetch

    def test_an_update_finishes_the_remove_instead_of_reinstalling(self):
        legacy, leftover = self._leftover()
        disk = {
            "installed_skills": {
                "claude": {
                    "paths": [str(leftover)],
                    "skills": [],
                    "skills_ref": "v1",
                    "remove_pending": True,
                }
            }
        }

        report, fetch = self._run(disk)

        fetch.assert_not_called()
        assert report.removals_finished == [("claude", "Claude Code")]
        assert report.removals_pending == []
        assert report.written == {}
        assert disk["installed_skills"] == {}
        assert not legacy.exists()
        assert not (Path.home() / ".claude" / "skills").exists()

    def test_a_remove_still_blocked_stays_recorded_and_marked(self):
        _legacy, leftover = self._leftover()
        disk = {
            "installed_skills": {
                "claude": {
                    "paths": [str(leftover)],
                    "skills": [],
                    "remove_pending": True,
                }
            }
        }
        blocked = RemoveReport(
            legacy_skipped=[(leftover, "could not be deleted")],
            legacy_retryable=[leftover],
        )

        with patch.object(ClaudeCodeGenerator, "remove_report", return_value=blocked):
            report, fetch = self._run(disk)

        fetch.assert_not_called()
        assert report.removals_pending == [("claude", "Claude Code")]
        assert report.legacy_skipped == [
            ("Claude Code", leftover, "could not be deleted")
        ]
        entry = disk["installed_skills"]["claude"]
        assert entry["remove_pending"] is True
        assert entry["paths"] == [str(leftover)]
        assert leftover.exists()

    @staticmethod
    def _pending_and_current():
        """A pending remove for Claude Code next to a current Codex install."""
        api = Path.home() / ".claude" / "skills" / "api"
        api.mkdir(parents=True)
        (api / "SKILL.md").write_text("---\nname: api\ndescription: x\n---\n")
        codex = Path.home() / ".codex" / "skills" / "api"
        disk = {
            "installed_skills": {
                "claude": {
                    "paths": [str(api)],
                    "skills": ["api"],
                    "skills_ref": "v1",
                    "remove_pending": True,
                },
                "codex": {"paths": [str(codex)], "skills": ["api"]},
            }
        }
        return api, disk

    def _run_both(self, disk, *, fetch, load=None):
        def load_disk():
            return json.loads(json.dumps(disk))

        def save(state):
            disk.clear()
            disk.update(json.loads(json.dumps(state)))

        with patch.object(skill_generator, "save_skills_state", side_effect=save):
            return skill_generator.install_skills_for(
                [ClaudeCodeGenerator(), CodexGenerator()],
                {},
                commands=[_make_command()],
                version="9.9.9",
                fetch=fetch,
                load_state=load or load_disk,
                only_recorded=True,
            )

    def test_a_fetch_failure_after_a_finished_remove_carries_it(self):
        """The remove is done and saved; the error must not hide that."""
        api, disk = self._pending_and_current()

        with pytest.raises(SkillFetchError) as raised:
            self._run_both(disk, fetch=MagicMock(side_effect=SkillFetchError("down")))

        assert not api.exists()
        assert list(disk["installed_skills"]) == ["codex"]
        finished = [("claude", "Claude Code")]
        assert skill_generator.removals_finished_by(raised.value) == finished
        again = pickle.loads(pickle.dumps(raised.value))
        assert skill_generator.removals_finished_by(again) == finished

    def test_a_records_error_after_a_finished_remove_carries_it(self):
        """Any exit after the remove, not only the download, says so."""
        _api, disk = self._pending_and_current()
        reads = []

        def load():
            reads.append(1)
            if len(reads) > 1:
                raise skill_generator.SkillsStateError("skills.json is damaged")
            return json.loads(json.dumps(disk))

        with pytest.raises(skill_generator.SkillsStateError) as raised:
            self._run_both(disk, fetch=MagicMock(return_value=[]), load=load)

        assert skill_generator.removals_finished_by(raised.value) == [
            ("claude", "Claude Code")
        ]

    def test_a_failure_with_no_remove_finished_carries_none(self):
        disk = {
            "installed_skills": {
                "codex": {
                    "paths": [str(Path.home() / ".codex" / "skills" / "api")],
                    "skills": ["api"],
                }
            }
        }

        with pytest.raises(SkillFetchError) as raised:
            self._run_both(disk, fetch=MagicMock(side_effect=SkillFetchError("down")))

        assert skill_generator.removals_finished_by(raised.value) == []
        assert not hasattr(raised.value, "removals_finished")

    def test_an_explicit_install_replaces_a_pending_record(self, tmp_path):
        """The user asking to install again is not undone by the flag."""
        _legacy, leftover = self._leftover()
        state = {
            "installed_skills": {
                "claude": {
                    "paths": [str(leftover)],
                    "skills": [],
                    "remove_pending": True,
                }
            }
        }
        with (
            patch.object(skill_generator, "save_skills_state"),
            patch.object(
                skill_generator,
                "fetch_repo_skills",
                return_value=[_fake_skill(tmp_path, "api")],
            ),
        ):
            report = skill_generator.install_skills_for(
                [ClaudeCodeGenerator()],
                state,
                commands=[_make_command()],
                version="9.9.9",
            )

        assert report.written["claude"] == [Path.home() / ".claude" / "skills" / "api"]
        assert "remove_pending" not in state["installed_skills"]["claude"]

    def test_a_failed_install_over_a_pending_remove_stays_pending(self, tmp_path):
        """The old stamp on a partial record read as current in `list`."""
        root = Path.home() / ".claude" / "skills"
        stranded = root / "api"
        stranded.mkdir(parents=True)
        state = {
            "installed_skills": {
                "claude": {
                    "paths": [str(stranded)],
                    "skills": ["api"],
                    "skills_ref": "v1.7.0",
                    "version": "0.4.0",
                    "remove_pending": True,
                }
            }
        }
        denied = PermissionError(13, "Permission denied", str(root / ".api.tmp-1"))
        with (
            patch.object(skill_generator, "save_skills_state"),
            patch.object(
                skill_generator,
                "fetch_repo_skills",
                return_value=[_fake_skill(tmp_path, "api")],
            ),
            patch.object(ClaudeCodeGenerator, "install_skills", side_effect=denied),
        ):
            report = skill_generator.install_skills_for(
                [ClaudeCodeGenerator()],
                state,
                commands=[_make_command()],
                version="9.9.9",
                best_effort=True,
            )

        assert report.failures
        entry = state["installed_skills"]["claude"]
        assert entry["paths"] == [str(stranded)]
        assert entry["remove_pending"] is True
        assert skill_generator.is_pending_removal(entry)


#: The fourteen skill names the pinned bundle ships, for the 0.3.x upgrade.
_FOURTEEN = [f"skill-{i:02d}" for i in range(14)]


def _v03_record(paths):
    """A tool's record exactly as deepctl 0.3.0 and 0.3.1 wrote it.

    Copied from those versions' `dg skills install` and `dg login`: the
    paths written, a timestamp, the version and the commands hash. No
    ``skills`` key and no ``skills_ref``.
    """
    return {
        "paths": [str(p) for p in paths],
        "installed_at": "2026-05-01T12:00:00.000000+00:00",
        "version": "0.3.1",
        "commands_hash": "0123456789abcdef",
    }


class TestADeepctl03InstallIsUpgraded:
    """A HOME `dg skills install --all` from deepctl 0.3.1 wrote: every
    record has only legacy paths and no ``skills`` key. That is an install,
    and an update must upgrade it, not delete it as a pending remove."""

    @staticmethod
    def _legacy_claude():
        legacy = Path.home() / ".claude" / "commands" / "deepgram"
        legacy.mkdir(parents=True)
        files = []
        for name in ("api", "docs", "setup-mcp", "starters"):
            path = legacy / f"{name}.md"
            path.write_text(f"---\nname: {name}\ndescription: x\n---\n")
            files.append(path)
        return legacy, files

    def test_an_update_installs_the_fourteen_skills_and_clears_the_old_files(
        self, tmp_path
    ):
        legacy, files = self._legacy_claude()
        disk = {"installed_skills": {"claude": _v03_record(files)}}

        def load():
            return json.loads(json.dumps(disk))

        def save(state):
            disk.clear()
            disk.update(json.loads(json.dumps(state)))

        bundle = [_fake_skill(tmp_path, name) for name in _FOURTEEN]
        with patch.object(skill_generator, "save_skills_state", side_effect=save):
            report = skill_generator.install_skills_for(
                [ClaudeCodeGenerator()],
                {},
                commands=[_make_command()],
                version="9.9.9",
                fetch=lambda: bundle,
                load_state=load,
                only_recorded=True,
            )

        root = Path.home() / ".claude" / "skills"
        assert report.removals_finished == []
        assert report.removals_pending == []
        assert report.written["claude"] == [root / name for name in _FOURTEEN]
        assert sorted(p.name for p in root.iterdir()) == _FOURTEEN
        assert not legacy.exists()
        entry = disk["installed_skills"]["claude"]
        assert entry["skills"] == _FOURTEEN
        assert entry["version"] == "9.9.9"
        assert "remove_pending" not in entry


class TestAFailedUpgradeKeepsTheOldFiles:
    """`chmod 555 ~/.claude/skills; dg skills update` on a 0.3.x install
    deleted ~/.claude/commands/deepgram while the record still listed it."""

    @staticmethod
    def _run(tmp_path, disk, fail_after=None):
        """``update`` on ``disk``; the copy fails after ``fail_after`` skills."""

        def load():
            return json.loads(json.dumps(disk))

        def save(state):
            disk.clear()
            disk.update(json.loads(json.dumps(state)))

        real = shutil.copytree
        calls = []

        def copytree(src, dst, *args, **kwargs):
            calls.append(src)
            if fail_after is not None and len(calls) > fail_after:
                raise PermissionError(13, "Permission denied", str(dst))
            return real(src, dst, *args, **kwargs)

        bundle = [_fake_skill(tmp_path, name) for name in _FOURTEEN]
        with (
            patch.object(skill_generator, "save_skills_state", side_effect=save),
            patch.object(skill_generator.shutil, "copytree", copytree),
        ):
            return skill_generator.install_skills_for(
                [ClaudeCodeGenerator()],
                {},
                commands=[_make_command()],
                version="9.9.9",
                fetch=lambda: bundle,
                load_state=load,
                only_recorded=True,
            )

    def test_a_failed_write_leaves_the_legacy_files_and_their_record(self, tmp_path):
        legacy, files = TestADeepctl03InstallIsUpgraded._legacy_claude()
        disk = {"installed_skills": {"claude": _v03_record(files)}}

        with pytest.raises(skill_generator.SkillWriteError):
            self._run(tmp_path, disk, fail_after=0)

        assert all(f.exists() for f in files)
        assert disk["installed_skills"]["claude"] == _v03_record(files)

        report = self._run(tmp_path, disk)

        root = Path.home() / ".claude" / "skills"
        assert report.written["claude"] == [root / name for name in _FOURTEEN]
        assert not legacy.exists()
        entry = disk["installed_skills"]["claude"]
        assert entry["paths"] == [str(root / name) for name in _FOURTEEN]
        assert entry["version"] == "9.9.9"

    def test_a_write_failing_part_way_keeps_the_legacy_files_recorded(self, tmp_path):
        _legacy, files = TestADeepctl03InstallIsUpgraded._legacy_claude()
        disk = {"installed_skills": {"claude": _v03_record(files)}}

        with pytest.raises(skill_generator.SkillWriteError):
            self._run(tmp_path, disk, fail_after=2)

        root = Path.home() / ".claude" / "skills"
        assert all(f.exists() for f in files)
        entry = disk["installed_skills"]["claude"]
        assert sorted(entry["paths"]) == sorted(
            [str(root / name) for name in _FOURTEEN[:2]] + [str(f) for f in files]
        )
        # The legacy files are kept in ``paths`` but are not skills.
        assert entry["skills"] == _FOURTEEN[:2]
        assert entry["version"] == "0.3.1"


class TestKeepGoingPastAnUnwritableTool:
    """`dg skills update` stopped at the first tool it could not write."""

    @staticmethod
    def _run(tmp_path, error):
        claude_root = Path.home() / ".claude" / "skills"
        real = shutil.copytree

        def copytree(src, dst, *args, **kwargs):
            if claude_root in Path(dst).parents:
                raise error(dst)
            return real(src, dst, *args, **kwargs)

        disk: dict = {}

        def save(state):
            disk.clear()
            disk.update(json.loads(json.dumps(state)))

        bundle = [_fake_skill(tmp_path, name) for name in _FOURTEEN]
        with (
            patch.object(skill_generator, "save_skills_state", side_effect=save),
            patch.object(skill_generator.shutil, "copytree", copytree),
        ):
            report = skill_generator.install_skills_for(
                [ClaudeCodeGenerator(), CursorGenerator()],
                {},
                commands=[_make_command()],
                version="9.9.9",
                fetch=lambda: bundle,
                keep_going=True,
            )
        return report, disk, claude_root

    def test_the_next_tool_is_still_installed(self, tmp_path):
        report, disk, claude_root = self._run(
            tmp_path, lambda dst: PermissionError(13, "Permission denied", str(dst))
        )

        assert [name for name, _ in report.failures] == ["Claude Code"]
        ((_name, failure),) = report.failures
        assert isinstance(failure, skill_generator.SkillWriteError)
        assert failure.root == claude_root
        cursor_root = Path.home() / ".cursor" / "skills"
        assert report.written == {"cursor": [cursor_root / n for n in _FOURTEEN]}
        assert set(disk["installed_skills"]) == {"cursor"}

    def test_an_error_that_is_not_the_tools_still_stops_the_run(self, tmp_path):
        with pytest.raises(RuntimeError):
            self._run(tmp_path, lambda dst: RuntimeError("bug"))

        assert not (Path.home() / ".cursor" / "skills").exists()


class TestAnUnwritableSkillsRoot:
    """The raw OSError named a staging folder the user never chose."""

    def _install(self, tmp_path, **kwargs):
        root = Path.home() / ".claude" / "skills"
        denied = PermissionError(
            13, "Permission denied", str(root / ".speech-to-text.tmp-1447")
        )
        with (
            patch.object(skill_generator, "save_skills_state"),
            patch.object(
                skill_generator,
                "fetch_repo_skills",
                return_value=[_fake_skill(tmp_path, "speech-to-text")],
            ),
            patch.object(ClaudeCodeGenerator, "install_skills", side_effect=denied),
        ):
            return root, skill_generator.install_skills_for(
                [ClaudeCodeGenerator()],
                {},
                commands=[_make_command()],
                version="9.9.9",
                **kwargs,
            )

    def test_it_raises_an_error_naming_the_root(self, tmp_path):
        with pytest.raises(skill_generator.SkillWriteError) as caught:
            self._install(tmp_path)

        root = Path.home() / ".claude" / "skills"
        assert caught.value.root == root
        assert str(caught.value) == f"could not write to {root}: Permission denied."
        assert ".tmp-" not in str(caught.value)
        assert caught.value.advice() == (
            "Fix its permissions and run the command again."
        )
        assert caught.value.advice("dg skills update") == (
            "Fix its permissions and run 'dg skills update' again."
        )
        assert isinstance(caught.value.__cause__, PermissionError)

    def test_best_effort_reports_it_naming_the_root(self, tmp_path):
        root, report = self._install(tmp_path, best_effort=True)

        [(name, failure)] = report.failures
        assert name == "Claude Code"
        assert isinstance(failure, skill_generator.SkillWriteError)
        assert failure.root == root

    @staticmethod
    def _copy_failing(tmp_path, raise_for, monkeypatch):
        """Install for Claude with copytree's per-file copy raising.

        The real ``shutil.copytree`` runs, so its per-file failure arrives
        folded into one ``shutil.Error`` exactly as it does on a full disk.
        """
        real = shutil.copytree

        def copytree(src, dst, *args, **kwargs):
            def copy(s, d, *a, **k):
                raise raise_for(s, d)

            return real(src, dst, *args, copy_function=copy, **kwargs)

        monkeypatch.setattr(skill_generator.shutil, "copytree", copytree)
        with patch.object(skill_generator, "save_skills_state"):
            return skill_generator.install_skills_for(
                [ClaudeCodeGenerator()],
                {},
                commands=[_make_command()],
                version="9.9.9",
                fetch=lambda: [_fake_skill(tmp_path, "speech-to-text")],
            )

    def test_a_full_disk_is_not_called_a_permissions_problem(
        self, tmp_path, monkeypatch
    ):
        root = Path.home() / ".claude" / "skills"

        with pytest.raises(skill_generator.SkillWriteError) as caught:
            self._copy_failing(
                tmp_path,
                lambda s, d: OSError(28, "No space left on device", d),
                monkeypatch,
            )

        assert str(caught.value) == (
            f"could not write to {root}: No space left on device."
        )
        assert caught.value.errno == 28
        assert caught.value.advice() == "Fix that and run the command again."
        assert isinstance(caught.value.__cause__, shutil.Error)

    def test_a_denied_copy_is_called_a_permissions_problem(self, tmp_path, monkeypatch):
        root = Path.home() / ".claude" / "skills"

        with pytest.raises(skill_generator.SkillWriteError) as caught:
            self._copy_failing(
                tmp_path,
                lambda s, d: PermissionError(13, "Permission denied", d),
                monkeypatch,
            )

        assert str(caught.value) == f"could not write to {root}: Permission denied."
        assert caught.value.advice() == (
            "Fix its permissions and run the command again."
        )

    def test_an_unreadable_bundle_cache_file_is_not_blamed_on_the_root(
        self, tmp_path, monkeypatch
    ):
        # The bundle the copy reads from is the cache under ~/.deepctl.
        monkeypatch.setattr(skill_generator, "_repo_cache_dir", lambda: tmp_path)

        with pytest.raises(shutil.Error) as caught:
            self._copy_failing(
                tmp_path,
                lambda s, d: PermissionError(13, "Permission denied", s),
                monkeypatch,
            )

        assert not isinstance(caught.value, skill_generator.SkillWriteError)

    def test_an_unknown_errno_is_not_called_a_permissions_problem(self):
        root = Path.home() / ".claude" / "skills"
        exc = skill_generator.SkillWriteError(root, "something broke", None)

        assert exc.advice() == "Fix that and run the command again."

    def test_a_copy_error_text_is_read_like_an_oserror(self):
        root = Path.home() / ".claude" / "skills"
        dest = root / "api" / "SKILL.md"
        folded = shutil.Error(
            [("src", str(dest), str(OSError(28, "No space left on device", str(dest))))]
        )

        failure = skill_generator._as_write_error(ClaudeCodeGenerator(), folded)

        assert isinstance(failure, skill_generator.SkillWriteError)
        assert failure.errno == 28
        assert str(failure) == f"could not write to {root}: No space left on device."

    def test_an_error_about_another_path_is_left_alone(self, tmp_path):
        elsewhere = OSError(5, "Input/output error", str(tmp_path / "bundle"))

        assert (
            skill_generator._as_write_error(ClaudeCodeGenerator(), elsewhere)
            is elsewhere
        )
