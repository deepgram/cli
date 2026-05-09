"""Unit tests for skill generator module."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from deepctl_core.skill_generator import (
    AmazonQGenerator,
    ClaudeCodeGenerator,
    ClineGenerator,
    CodexGenerator,
    CommandMetadata,
    CursorGenerator,
    GeminiGenerator,
    _commands_hash,
    collect_command_metadata,
    detect_ai_clis,
    get_all_generators,
    get_skills_state,
    render_developer_guide,
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
        assert "aura-2-andromeda-en" in content

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
        assert len(paths) > 0
        assert all("deepgram" in str(p) for p in paths)
        assert all("commands" in str(p) for p in paths)
        assert all(p.suffix == ".md" for p in paths)

    def test_generate_includes_frontmatter(self):
        gen = ClaudeCodeGenerator()
        cmds = [_make_command()]
        result = gen.generate(cmds, "1.0.0")
        assert len(result) == 1
        content = list(result.values())[0]
        assert content.startswith("---\n")

    def test_install_writes_individual_skill_files(self, tmp_path):
        from unittest.mock import PropertyMock
        gen = ClaudeCodeGenerator()
        skill_dir = tmp_path / "commands" / "deepgram"
        with patch.object(type(gen), "_skill_dir", new_callable=PropertyMock, return_value=skill_dir):
            with patch("deepctl_core.skill_generator.fetch_repo_skills", return_value={"api": "# API\n", "docs": "# Docs\n"}):
                written = gen.install([_make_command()], "1.0.0")
                assert len(written) == 2
                assert skill_dir / "api.md" in written
                assert (skill_dir / "api.md").read_text() == "# API\n"
                assert (skill_dir / "docs.md").read_text() == "# Docs\n"

    def test_install_returns_empty_when_no_repo_skills(self, tmp_path):
        from unittest.mock import PropertyMock
        gen = ClaudeCodeGenerator()
        skill_dir = tmp_path / "commands" / "deepgram"
        with patch.object(type(gen), "_skill_dir", new_callable=PropertyMock, return_value=skill_dir):
            with patch("deepctl_core.skill_generator.fetch_repo_skills", return_value={}):
                written = gen.install([_make_command()], "1.0.0")
                assert written == []

    def test_remove_deletes_skill_dir(self, tmp_path):
        from unittest.mock import PropertyMock
        gen = ClaudeCodeGenerator()
        skill_dir = tmp_path / "deepgram"
        skill_dir.mkdir()
        (skill_dir / "api.md").write_text("hello")
        with patch.object(type(gen), "_skill_dir", new_callable=PropertyMock, return_value=skill_dir):
            removed = gen.remove()
            assert len(removed) == 1
            assert not (skill_dir / "api.md").exists()

    def test_is_installed(self, tmp_path):
        from unittest.mock import PropertyMock
        gen = ClaudeCodeGenerator()
        skill_dir = tmp_path / "deepgram"
        with patch.object(type(gen), "_skill_dir", new_callable=PropertyMock, return_value=skill_dir):
            assert gen.is_installed() is False
            skill_dir.mkdir()
            (skill_dir / "api.md").write_text("hello")
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
