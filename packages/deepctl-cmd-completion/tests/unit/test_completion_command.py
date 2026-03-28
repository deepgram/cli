"""Tests for the completion command."""

from __future__ import annotations

import pytest

from deepctl_cmd_completion.command import CompletionCommand


class TestCompletionCommand:
    """Tests for CompletionCommand."""

    @pytest.fixture
    def cmd(self) -> CompletionCommand:
        return CompletionCommand()

    @pytest.mark.unit
    def test_name(self, cmd: CompletionCommand) -> None:
        assert cmd.name == "completion"

    @pytest.mark.unit
    def test_detect_shell_zsh(self, cmd: CompletionCommand, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHELL", "/bin/zsh")
        assert cmd._detect_shell() == "zsh"

    @pytest.mark.unit
    def test_detect_shell_fish(self, cmd: CompletionCommand, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHELL", "/usr/bin/fish")
        assert cmd._detect_shell() == "fish"

    @pytest.mark.unit
    def test_detect_shell_bash_default(self, cmd: CompletionCommand, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SHELL", raising=False)
        assert cmd._detect_shell() == "bash"

    @pytest.mark.unit
    def test_eval_lines_contain_dg(self, cmd: CompletionCommand) -> None:
        from deepctl_cmd_completion.command import _SHELLS
        for shell, cfg in _SHELLS.items():
            assert "dg" in cfg["eval_line"], f"{shell} eval line should reference dg"
            assert "_DG_COMPLETE" in cfg["eval_line"]

    @pytest.mark.unit
    def test_install_idempotent(self, cmd: CompletionCommand, tmp_path: pytest.TempPathFactory) -> None:
        profile = tmp_path / ".bashrc"
        profile.write_text("# existing config\n_DG_COMPLETE=bash_source\n")

        result = cmd._install("bash", 'eval "$(_DG_COMPLETE=bash_source dg)"', "_DG_COMPLETE=bash_source", profile)

        assert result.installed is False
        assert result.status == "info"
        # File should be unchanged
        assert profile.read_text().count("_DG_COMPLETE") == 1

    @pytest.mark.unit
    def test_install_appends_to_profile(self, cmd: CompletionCommand, tmp_path: pytest.TempPathFactory) -> None:
        profile = tmp_path / ".zshrc"
        profile.write_text("# existing config\n")

        result = cmd._install("zsh", 'eval "$(_DG_COMPLETE=zsh_source dg)"', "_DG_COMPLETE=zsh_source", profile)

        assert result.installed is True
        assert result.status == "success"
        content = profile.read_text()
        assert "_DG_COMPLETE=zsh_source" in content

    @pytest.mark.unit
    def test_agent_help_set(self, cmd: CompletionCommand) -> None:
        assert cmd.agent_help
        assert "completion" in cmd.agent_help.lower() or "shell" in cmd.agent_help.lower()
