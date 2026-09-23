"""Unit tests for skill generator module."""

import json
import shutil
from pathlib import Path
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
    SkillOwnershipError,
    _commands_hash,
    collect_command_metadata,
    detect_ai_clis,
    get_all_generators,
    get_skills_state,
    recorded_skill_paths,
    render_developer_guide,
    render_skill_content,
    save_skills_state,
    skills_need_update,
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
    assert AiderGenerator._LEGACY_FILE.is_relative_to(home)
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
    """Test render_developer_guide and render_skill_content delegation."""

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
        assert "developers.deepgram.com" in content
        assert "console.deepgram.com" in content
        assert "discord.gg/deepgram" in content
        assert "github.com/deepgram" in content

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

    def test_render_skill_content_delegates(self):
        """render_skill_content should delegate to render_developer_guide."""
        cmds = [_make_command()]
        content = render_skill_content(cmds, "1.0.0")
        assert "# Deepgram Developer Guide" in content
        assert "Speech-to-Text" in content

    def test_render_skill_content_frontmatter(self):
        cmds = [_make_command()]
        content = render_skill_content(cmds, "1.0.0", include_frontmatter=True)
        assert content.startswith("---\n")
        assert "description:" in content


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

    EXPECTED = {
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
    NO_SKILLS_DIRECTORY = {"amazonq", "aider"}

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
            # clean_legacy is patched out: install() calls it, and for
            # these two it edits the developer's real ~/.amazonq and
            # ~/.aider.conf.yml while the unit suite runs.
            with patch.object(gen, "clean_legacy", return_value=[]):
                assert gen.install([_make_command()], "1.0.0") == []
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
        _, root, written = self._install(tmp_path, skills)
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
            assert gen.installed_skill_paths(written) != []

    def test_remove_deletes_every_installed_skill(self, tmp_path):
        skills = [_fake_skill(tmp_path, n) for n in ("api", "docs")]
        gen, root, written = self._install(tmp_path, skills)
        with patch.object(gen, "skills_root", return_value=root):
            with patch.object(gen, "legacy_paths", return_value=[]):
                removed = gen.remove(written)
                assert len(removed) == 2
                assert gen.installed_skill_paths(written) == []
        assert not root.exists()

    def test_install_propagates_a_fetch_failure(self, tmp_path):
        """A partial install must never look like a complete one."""
        gen = ClaudeCodeGenerator()
        with patch.object(gen, "skills_root", return_value=tmp_path / "skills"):
            with patch(
                "deepctl_core.skill_generator.fetch_repo_skills",
                side_effect=SkillFetchError("no network"),
            ):
                with pytest.raises(SkillFetchError):
                    gen.install([_make_command()], "1.0.0")


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
        with patch.object(gen, "skills_root", return_value=root):
            with patch.object(gen, "legacy_paths", return_value=[]):
                written = gen.install_skills(skills)
                removed = gen.remove(written)

        assert sorted(p.name for p in removed) == ["api", "docs"]
        assert mine.is_dir()
        assert (mine / "SKILL.md").read_text().endswith("mine\n")

    def test_remove_without_a_record_deletes_nothing(self, tmp_path):
        """A hand-deleted skills.json leaves deepctl unable to prove ownership."""
        gen, root = self._gen(tmp_path)
        mine = self._unrelated(root)
        skills = [_fake_skill(tmp_path, "api")]
        with patch.object(gen, "skills_root", return_value=root):
            with patch.object(gen, "legacy_paths", return_value=[]):
                gen.install_skills(skills)
                assert gen.remove([]) == []

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

        with patch.object(gen, "skills_root", return_value=root):
            with patch.object(gen, "legacy_paths", return_value=[]):
                removed = gen.remove(
                    [
                        str(elsewhere),
                        str(root / ".." / ".." / "Documents"),
                        str(nested),  # a grandchild, not a direct child
                        "relative/path",
                    ]
                )

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

        with patch.object(gen, "skills_root", return_value=root):
            with patch.object(gen, "legacy_paths", return_value=[]):
                assert gen.remove([str(link)]) == []

        assert target.is_dir()
        assert (target / "SKILL.md").is_file()

    def test_remove_clears_a_recorded_path_someone_replaced_with_a_file(
        self, tmp_path
    ):
        """Skipping it left a record no retry could ever clear.

        install already unlinks a plain file standing at a recorded
        destination, so remove has to be able to finish the same job --
        otherwise ownership outliving a failed delete means the warning
        repeats forever with no action that resolves it.
        """
        gen, root = self._gen(tmp_path)
        stray = root / "api"
        stray.write_text("not a skill folder\n")

        with patch.object(gen, "skills_root", return_value=root):
            with patch.object(gen, "legacy_paths", return_value=[]):
                removed = gen.remove([str(stray)])

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

        with patch.object(gen, "skills_root", return_value=root):
            with patch.object(gen, "legacy_paths", return_value=[]):
                written = gen.install_skills(skills)
                with patch.object(skill_generator.shutil, "rmtree", denied):
                    assert gen.remove(written) == []

        assert (root / "api" / "SKILL.md").is_file()

    # -- install -----------------------------------------------------

    def test_install_refuses_a_same_name_collision(self, tmp_path):
        """An unrecorded folder named `api` is somebody else's `api`."""
        gen, root = self._gen(tmp_path)
        mine = self._unrelated(root, "api")
        skills = [_fake_skill(tmp_path, n) for n in ("api", "docs")]

        with patch.object(gen, "skills_root", return_value=root):
            with patch.object(gen, "legacy_paths", return_value=[]):
                with pytest.raises(SkillOwnershipError) as excinfo:
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
        skills = [_fake_skill(tmp_path, "api", references=("old.md",))]
        with patch.object(gen, "skills_root", return_value=root):
            with patch.object(gen, "legacy_paths", return_value=[]):
                written = gen.install_skills(skills)
                # Recorded, so a reinstall may replace it.
                again = gen.install_skills(skills, written)
        assert again == written
        assert (root / "api" / "references" / "old.md").is_file()

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
        with patch.object(gen, "skills_root", return_value=root):
            with patch.object(gen, "legacy_paths", return_value=[]):
                gen.install_skills(skills)
                # Recorded under the other spelling of the same directory.
                assert gen.install_conflicts(skills, [str(link_root / "api")]) == []

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
        with patch.object(gen, "skills_root", return_value=root):
            with patch.object(gen, "legacy_paths", return_value=[]):
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
        with patch.object(gen, "skills_root", return_value=root):
            with patch.object(gen, "legacy_paths", return_value=[]):
                # Patched: install() would otherwise download the real
                # bundle into the developer's own ~/.deepctl cache.
                with patch(
                    "deepctl_core.skill_generator.fetch_repo_skills",
                    return_value=[_fake_skill(tmp_path, "api")],
                ):
                    with pytest.raises(SkillOwnershipError):
                        gen.install([_make_command()], "1.0.0", recorded=[])
        assert (mine / "SKILL.md").read_text().endswith("mine\n")

    def test_prune_removes_a_skill_that_disappeared_upstream(self, tmp_path):
        """Otherwise a retired skill is left behind and becomes unownable."""
        gen, root = self._gen(tmp_path)
        mine = self._unrelated(root)
        first = [_fake_skill(tmp_path, n) for n in ("api", "retired")]
        with patch.object(gen, "skills_root", return_value=root):
            with patch.object(gen, "legacy_paths", return_value=[]):
                written = gen.install_skills(first)
                pruned = gen.prune_retired(written, [_fake_skill(tmp_path, "api")])

        assert pruned == [root / "retired"]
        assert not (root / "retired").exists()
        assert (root / "api").is_dir()
        # And it still leaves everything it does not own alone.
        assert mine.is_dir()

    # -- status ------------------------------------------------------

    def test_status_does_not_count_unowned_folders(self, tmp_path):
        gen, root = self._gen(tmp_path)
        self._unrelated(root)
        self._unrelated(root, "someone-elses-api")
        skills = [_fake_skill(tmp_path, "api")]
        with patch.object(gen, "skills_root", return_value=root):
            with patch.object(gen, "legacy_paths", return_value=[]):
                written = gen.install_skills(skills)
                assert gen.installed_skill_paths(written) == [root / "api"]

    def test_status_drops_a_recorded_folder_the_user_deleted(self, tmp_path):
        gen, root = self._gen(tmp_path)
        skills = [_fake_skill(tmp_path, n) for n in ("api", "docs")]
        with patch.object(gen, "skills_root", return_value=root):
            with patch.object(gen, "legacy_paths", return_value=[]):
                written = gen.install_skills(skills)
                shutil.rmtree(root / "docs")
                assert gen.installed_skill_paths(written) == [root / "api"]

    # -- the record itself -------------------------------------------

    def test_recorded_skill_paths_tolerates_a_mangled_state_file(self):
        assert recorded_skill_paths({}, "claude") == []
        assert recorded_skill_paths({"installed_skills": None}, "claude") == []
        assert recorded_skill_paths({"installed_skills": {}}, "claude") == []
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
        with patch.object(gen, "legacy_paths", return_value=[LegacyArtifact(legacy)]):
            removed = gen.clean_legacy()
        assert removed == [legacy]
        assert not legacy.exists()

    def test_claude_cleanup_only_takes_the_markdown_it_wrote(self, tmp_path):
        """0.3.0 wrote `*.md` here and removed `*.md`; so does the cleanup."""
        legacy = tmp_path / ".claude" / "commands" / "deepgram"
        legacy.mkdir(parents=True)
        names = ClaudeCodeGenerator().legacy_paths()[0].contents
        for name in names:
            (legacy / name).write_text(f"---\nname: {name}\n---\n")
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

    def test_claude_cleanup_removes_the_directory_once_it_is_empty(self, tmp_path):
        legacy = tmp_path / ".claude" / "commands" / "deepgram"
        legacy.mkdir(parents=True)
        (legacy / "api.md").write_text("stale")

        gen = ClaudeCodeGenerator()
        with patch.object(
            gen,
            "legacy_paths",
            return_value=[LegacyArtifact(legacy, contents=("api.md",))],
        ):
            gen.clean_legacy()
        assert not legacy.exists()

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
        (legacy / "api.md").write_text("stale")

        gen = ClaudeCodeGenerator()
        root = tmp_path / "skills"
        with patch.object(gen, "skills_root", return_value=root):
            with patch.object(
                gen, "legacy_paths", return_value=[LegacyArtifact(legacy)]
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
            with patch.object(gen, "skills_root", return_value=root):
                with patch.object(gen, "legacy_paths", return_value=[]):
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
    looped over `gen.install()` instead and saved once at the end, so a
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

        with patch.object(ClaudeCodeGenerator, "install_skills", install_skills):
            with pytest.raises(OSError):
                self._run([first, second], roots, skills, state)

        entry = state["installed_skills"]["claude"]
        assert [Path(p).name for p in entry["paths"]] == ["api", "docs"]
        assert entry["skills_ref"] == DEFAULT_SKILLS_REF
        assert (first_root / "api" / "SKILL.md").is_file()
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
            if Path(dst).name == "cli":
                raise OSError(28, "No space left on device")
            return real_copytree(src, dst, *args, **kwargs)

        with patch.object(skill_generator.shutil, "copytree", copytree):
            with pytest.raises(OSError):
                self._run([gen], {"claude": root}, skills, state)

        entry = state["installed_skills"]["claude"]
        assert [Path(p).name for p in entry["paths"]] == ["api", "docs"]

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

    def test_best_effort_skips_a_conflicting_tool_and_installs_the_rest(
        self, tmp_path
    ):
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

    def test_each_tool_is_reported_as_it_lands_not_after_the_last_one(
        self, tmp_path
    ):
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

        with patch.object(ClaudeCodeGenerator, "install_skills", install_skills):
            with pytest.raises(OSError):
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

        with patch.object(ClaudeCodeGenerator, "install_skills", install_skills):
            with pytest.raises(OSError):
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

    def test_a_null_record_for_an_unsupported_tool_is_dropped_and_saved(
        self, tmp_path
    ):
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

    def test_legacy_cleanup_failing_does_not_abort_the_whole_install(
        self, tmp_path
    ):
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
                "clean_legacy",
                side_effect=PermissionError(13, "Permission denied"),
            ),
            patch.object(skill_generator, "save_skills_state"),
            patch.object(
                skill_generator, "fetch_repo_skills", return_value=skills
            ),
        ):
            report = skill_generator.install_skills_for(
                [supported, unsupported],
                state,
                commands=[_make_command()],
                version="9.9.9",
            )

        assert list(report.written) == ["claude"]
        assert (root / "api" / "SKILL.md").is_file()

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
            patch.object(unsupported, "clean_legacy") as clean,
            patch.object(
                skill_generator,
                "fetch_repo_skills",
                side_effect=SkillFetchError("no network"),
            ),
        ):
            with pytest.raises(SkillFetchError):
                skill_generator.install_skills_for(
                    [supported, unsupported],
                    state,
                    commands=[_make_command()],
                    version="9.9.9",
                )

        clean.assert_not_called()
