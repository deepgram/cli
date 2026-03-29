"""Tests for the deprecated TranscribeCommand alias."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner
from deepctl_cmd_listen.command import ListenCommand
from deepctl_cmd_transcribe.command import TranscribeCommand
from deepctl_core import AuthManager, Config, DeepgramClient


class TestTranscribeIsAlias:
    def test_is_subclass_of_listen(self):
        assert issubclass(TranscribeCommand, ListenCommand)

    def test_name_is_transcribe(self):
        assert TranscribeCommand().name == "transcribe"

    def test_hidden_is_true(self):
        assert TranscribeCommand().hidden is True

    def test_listen_is_not_hidden(self):
        assert ListenCommand().hidden is False

    def test_inherits_same_arguments(self):
        """TranscribeCommand should expose the same flags as ListenCommand."""
        tc_args = TranscribeCommand().get_arguments()
        lc_args = ListenCommand().get_arguments()
        assert tc_args == lc_args

    def test_inherits_requires_auth(self):
        assert TranscribeCommand().requires_auth is True

    def test_inherits_ci_friendly(self):
        assert TranscribeCommand().ci_friendly is True


class TestHiddenFlagInPluginManager:
    """Verify the plugin manager passes hidden=True to Click for hidden commands."""

    def test_hidden_command_not_in_dg_help(self):
        """TranscribeCommand should not appear in `dg --help` output."""
        from src.deepctl.main import cli as deepctl_cli

        runner = CliRunner()
        result = runner.invoke(deepctl_cli, ["--help"])
        assert result.exit_code == 0
        assert "transcribe" not in result.output

    def test_hidden_command_help_still_works(self):
        """Calling `dg transcribe --help` should succeed even though it is hidden."""
        from src.deepctl.main import cli as deepctl_cli

        runner = CliRunner()
        result = runner.invoke(deepctl_cli, ["transcribe", "--help"])
        assert result.exit_code == 0
        # Should show the command help, including SOURCE argument
        assert "SOURCE" in result.output

    def test_listen_visible_in_help(self):
        """dg listen must remain visible in --help."""
        from src.deepctl.main import cli as deepctl_cli

        runner = CliRunner()
        result = runner.invoke(deepctl_cli, ["--help"])
        assert "listen" in result.output
