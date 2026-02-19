"""Unit tests for skill generator module."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from deepctl_core.skill_generator import (
    ClaudeCodeGenerator,
    CodexGenerator,
    CommandMetadata,
    GeminiGenerator,
    AmazonQGenerator,
    CursorGenerator,
    ClineGenerator,
    _commands_hash,
    collect_command_metadata,
    detect_ai_clis,
    get_all_generators,
    get_skills_state,
    render_skill_content,
    save_skills_state,
    skills_need_update,
)


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
        cmd = _make_command(name="audio", parent_group="debug", full_command="deepctl debug audio")
        assert cmd.parent_group == "debug"
        assert cmd.full_command == "deepctl debug audio"


class TestCommandsHash:
    """Test _commands_hash."""

    def test_deterministic(self):
        cmds = [_make_command(), _make_command(name="other", full_command="deepctl other")]
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
        with patch("deepctl_core.skill_generator._STATE_FILE", state_file), \
             patch("deepctl_core.skill_generator._SKILLS_DIR", tmp_path):
            save_skills_state({"installed_skills": {"claude": {}}, "auto_update": False})
            result = get_skills_state()
            assert result["installed_skills"] == {"claude": {}}
            assert result["auto_update"] is False

    def test_skills_need_update_no_installed(self):
        with patch("deepctl_core.skill_generator.get_skills_state", return_value={"installed_skills": {}}):
            assert skills_need_update([_make_command()]) is False

    def test_skills_need_update_stale_hash(self):
        state = {
            "installed_skills": {
                "claude": {"commands_hash": "sha256:old"}
            }
        }
        with patch("deepctl_core.skill_generator.get_skills_state", return_value=state):
            assert skills_need_update([_make_command()]) is True


class TestRenderSkillContent:
    """Test render_skill_content."""

    def test_basic_render(self):
        cmds = [_make_command()]
        content = render_skill_content(cmds, "1.0.0")
        assert "# deepctl CLI" in content
        assert "v1.0.0" in content
        assert "dg test foo" in content
        assert "Authentication" in content

    def test_frontmatter(self):
        cmds = [_make_command()]
        content = render_skill_content(cmds, "1.0.0", include_frontmatter=True)
        assert content.startswith("---\n")
        assert "description:" in content

    def test_no_frontmatter_by_default(self):
        cmds = [_make_command()]
        content = render_skill_content(cmds, "1.0.0")
        assert not content.startswith("---")

    def test_group_with_subcommands(self):
        group = _make_command(name="debug", is_group=True, full_command="deepctl debug")
        sub = _make_command(name="audio", parent_group="debug", full_command="deepctl debug audio")
        content = render_skill_content([group, sub], "1.0.0")
        assert "deepctl debug" in content
        assert "deepctl debug audio" in content

    def test_auth_indicator(self):
        cmd = _make_command(requires_auth=True)
        content = render_skill_content([cmd], "1.0.0")
        assert "Requires authentication" in content

    def test_options_rendered(self):
        cmd = _make_command(
            arguments=[
                {"name": "source", "is_option": False},
                {"names": ["--model", "-m"], "help": "Model to use", "default": "nova-2", "is_option": True},
            ]
        )
        content = render_skill_content([cmd], "1.0.0")
        assert "--model, -m" in content
        assert "nova-2" in content


class TestClaudeCodeGenerator:
    """Test ClaudeCodeGenerator."""

    def test_detect_with_dir(self, tmp_path):
        gen = ClaudeCodeGenerator()
        with patch.object(Path, "joinpath", return_value=tmp_path):
            with patch.object(tmp_path.__class__, "is_dir", return_value=True):
                assert gen.detect() is True

    def test_skill_path(self):
        gen = ClaudeCodeGenerator()
        paths = gen.get_skill_paths()
        assert len(paths) == 1
        assert paths[0].name == "deepctl.md"
        assert "commands" in str(paths[0])

    def test_generate_includes_frontmatter(self):
        gen = ClaudeCodeGenerator()
        cmds = [_make_command()]
        result = gen.generate(cmds, "1.0.0")
        path = gen.get_skill_paths()[0]
        assert path in result
        assert result[path].startswith("---\n")

    def test_install_writes_file(self, tmp_path):
        gen = ClaudeCodeGenerator()
        target = tmp_path / "commands" / "deepctl.md"
        with patch.object(gen, "get_skill_paths", return_value=[target]):
            with patch.object(gen, "generate", return_value={target: "# test content\n"}):
                written = gen.install([_make_command()], "1.0.0")
                assert target in written
                assert target.read_text() == "# test content\n"

    def test_remove_deletes_file(self, tmp_path):
        gen = ClaudeCodeGenerator()
        target = tmp_path / "deepctl.md"
        target.write_text("hello")
        with patch.object(gen, "get_skill_paths", return_value=[target]):
            removed = gen.remove()
            assert target in removed
            assert not target.exists()

    def test_is_installed(self, tmp_path):
        gen = ClaudeCodeGenerator()
        target = tmp_path / "deepctl.md"
        with patch.object(gen, "get_skill_paths", return_value=[target]):
            assert gen.is_installed() is False
            target.write_text("hello")
            assert gen.is_installed() is True


class TestCodexGenerator:
    """Test CodexGenerator (append-mode)."""

    def test_generate_wraps_in_delimiters(self):
        gen = CodexGenerator()
        cmds = [_make_command()]
        result = gen.generate(cmds, "1.0.0")
        path = gen.get_skill_paths()[0]
        content = result[path]
        assert "<!-- BEGIN deepctl CLI Reference" in content
        assert "<!-- END deepctl CLI Reference -->" in content

    def test_merge_into_existing(self, tmp_path):
        gen = CodexGenerator()
        target = tmp_path / "instructions.md"
        target.write_text("# My instructions\n\nSome content\n")

        with patch.object(gen, "get_skill_paths", return_value=[target]):
            result = gen.generate([_make_command()], "1.0.0")
            content = result[target]
            assert content.startswith("# My instructions\n")
            assert "<!-- BEGIN deepctl" in content

    def test_replace_existing_section(self, tmp_path):
        gen = CodexGenerator()
        target = tmp_path / "instructions.md"
        target.write_text(
            "before\n"
            "<!-- BEGIN deepctl CLI Reference (auto-generated by deepctl) -->\n"
            "old content\n"
            "<!-- END deepctl CLI Reference -->\n"
            "after\n"
        )

        with patch.object(gen, "get_skill_paths", return_value=[target]):
            result = gen.generate([_make_command()], "1.0.0")
            content = result[target]
            assert "old content" not in content
            assert "before\n" in content
            assert "after\n" in content

    def test_remove_section(self, tmp_path):
        gen = CodexGenerator()
        target = tmp_path / "instructions.md"
        target.write_text(
            "before\n"
            "<!-- BEGIN deepctl CLI Reference (auto-generated by deepctl) -->\n"
            "content\n"
            "<!-- END deepctl CLI Reference -->\n"
            "after\n"
        )

        with patch.object(gen, "get_skill_paths", return_value=[target]):
            removed = gen.remove()
            assert target in removed
            text = target.read_text()
            assert "BEGIN deepctl" not in text
            assert "before" in text
            assert "after" in text


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
        with patch.object(ClaudeCodeGenerator, "detect", return_value=True), \
             patch.object(CodexGenerator, "detect", return_value=False), \
             patch.object(GeminiGenerator, "detect", return_value=False), \
             patch.object(AmazonQGenerator, "detect", return_value=False), \
             patch.object(CursorGenerator, "detect", return_value=False), \
             patch.object(ClineGenerator, "detect", return_value=False):
            detected = detect_ai_clis()
            claude = [g for g in detected if g.cli_name == "claude"]
            assert len(claude) >= 1
