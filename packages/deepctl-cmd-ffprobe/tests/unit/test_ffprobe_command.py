"""Tests for ffprobe command."""

from unittest.mock import Mock, patch

import pytest
from deepctl_cmd_ffprobe.command import FfprobeCommand
from deepctl_cmd_ffprobe.models import FfprobeResult


class TestFfprobeCommand:
    """Test cases for FfprobeCommand."""

    def setup_method(self):
        self.cmd = FfprobeCommand()
        self.config = Mock()
        self.config._config = Mock()
        self.config._config.tools = Mock()
        self.config._config.tools.ffprobe_path = None
        self.config.get.return_value = None
        self.auth = Mock()
        self.client = Mock()

    def test_command_metadata(self):
        assert self.cmd.name == "ffprobe"
        assert self.cmd.requires_auth is False
        assert self.cmd.ci_friendly is True

    def test_get_arguments(self):
        args = self.cmd.get_arguments()
        names = [a.get("names", [a.get("name")])[0] for a in args]
        assert "--path" in names
        assert "--reset" in names

    def test_path_and_reset_conflict(self):
        result = self.cmd.handle(
            self.config, self.auth, self.client,
            path="/usr/bin/ffprobe", reset=True,
        )
        assert result.status == "error"

    @patch("deepctl_cmd_ffprobe.command.os.access")
    @patch("deepctl_cmd_ffprobe.command.os.path.isfile")
    @patch("deepctl_cmd_ffprobe.command.subprocess.run")
    def test_set_path_success(self, mock_run, mock_isfile, mock_access):
        mock_isfile.return_value = True
        mock_access.return_value = True
        mock_run.return_value = Mock(
            returncode=0, stdout="ffprobe version 6.0\n"
        )

        result = self.cmd.handle(
            self.config, self.auth, self.client,
            path="/opt/homebrew/bin/ffprobe",
        )

        assert result.status == "success"
        assert isinstance(result, FfprobeResult)
        assert result.stored_path == "/opt/homebrew/bin/ffprobe"
        assert result.available is True
        self.config.save.assert_called_once()

    @patch("deepctl_cmd_ffprobe.command.os.path.isfile")
    def test_set_path_not_found(self, mock_isfile):
        mock_isfile.return_value = False

        result = self.cmd.handle(
            self.config, self.auth, self.client,
            path="/nonexistent/ffprobe",
        )

        assert result.status == "error"
        self.config.save.assert_not_called()

    @patch("deepctl_cmd_ffprobe.command.os.access")
    @patch("deepctl_cmd_ffprobe.command.os.path.isfile")
    def test_set_path_not_executable(self, mock_isfile, mock_access):
        mock_isfile.return_value = True
        mock_access.return_value = False

        result = self.cmd.handle(
            self.config, self.auth, self.client,
            path="/tmp/ffprobe.txt",
        )

        assert result.status == "error"

    @patch("deepctl_cmd_ffprobe.command.shutil.which")
    def test_reset(self, mock_which):
        mock_which.return_value = "/usr/bin/ffprobe"

        result = self.cmd.handle(
            self.config, self.auth, self.client,
            reset=True,
        )

        assert result.status == "success"
        assert isinstance(result, FfprobeResult)
        assert result.detected_path == "/usr/bin/ffprobe"
        self.config.save.assert_called_once()

    @patch("deepctl_cmd_ffprobe.command.get_ffprobe_path")
    @patch("deepctl_cmd_ffprobe.command.shutil.which")
    @patch("deepctl_cmd_ffprobe.command.subprocess.run")
    def test_status_available(self, mock_run, mock_which, mock_get_path):
        mock_which.return_value = "/usr/bin/ffprobe"
        mock_get_path.return_value = "/usr/bin/ffprobe"
        mock_run.return_value = Mock(
            returncode=0, stdout="ffprobe version 6.0\n"
        )

        result = self.cmd.handle(self.config, self.auth, self.client)

        assert result.status == "success"
        assert isinstance(result, FfprobeResult)
        assert result.available is True

    @patch("deepctl_cmd_ffprobe.command.print_ffprobe_install_instructions")
    @patch("deepctl_cmd_ffprobe.command.get_ffprobe_path")
    @patch("deepctl_cmd_ffprobe.command.shutil.which")
    def test_status_not_available(
        self, mock_which, mock_get_path, mock_print
    ):
        mock_which.return_value = None
        mock_get_path.return_value = None

        result = self.cmd.handle(self.config, self.auth, self.client)

        assert result.status == "error"
        assert isinstance(result, FfprobeResult)
        assert result.available is False
        mock_print.assert_called_once()
