"""Unit tests for skills command."""

from unittest.mock import MagicMock, patch

import pytest
from deepctl_cmd_skills.command import SkillsCommand


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
