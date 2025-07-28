"""Unit tests for update command."""

import pytest
from unittest.mock import MagicMock, patch
import subprocess
from deepctl_cmd_update.command import UpdateCommand
from deepctl_cmd_update.version_check import VersionInfo
from deepctl_cmd_update.installation import InstallationInfo, InstallMethod


class TestUpdateCommand:
    """Test update command functionality."""

    @pytest.fixture
    def command(self):
        """Create update command instance."""
        return UpdateCommand()

    @patch("deepctl_cmd_update.command.asyncio.run")
    @patch("deepctl_cmd_update.command.VersionChecker")
    @patch("deepctl_cmd_update.command.get_console")
    @patch("deepctl_cmd_update.command.format_version_message")
    def test_check_only(self, mock_format_msg, mock_console, mock_checker_class, mock_asyncio_run, command):
        """Test check-only mode."""
        # Mock version info
        mock_version_info = VersionInfo(
            current_version="0.1.0",
            latest_version="0.2.0",
            update_available=True,
        )

        # Configure asyncio.run to return the version info
        mock_asyncio_run.return_value = mock_version_info
        mock_format_msg.return_value = "Update available!"

        # Mock config
        mock_config = MagicMock()
        mock_auth = MagicMock()
        mock_client = MagicMock()

        # Run command
        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth,
            client=mock_client,
            check_only=True
        )

        assert result["success"] is True
        assert result["update_available"] is True
        assert result["current_version"] == "0.1.0"
        assert result["latest_version"] == "0.2.0"

    @patch("deepctl_cmd_update.command.asyncio.run")
    @patch("deepctl_cmd_update.command.VersionChecker")
    @patch("deepctl_cmd_update.command.get_console")
    @patch("deepctl_cmd_update.command.format_version_message")
    @patch("deepctl_cmd_update.command.print_success")
    def test_no_update_available(self, mock_print_success, mock_format_msg, mock_console,
                                 mock_checker_class, mock_asyncio_run, command):
        """Test when no update is available."""
        # Mock version info
        mock_version_info = VersionInfo(
            current_version="0.2.0",
            latest_version="0.2.0",
            update_available=False,
        )

        # Configure asyncio.run to return the version info
        mock_asyncio_run.return_value = mock_version_info
        mock_format_msg.return_value = "Already up to date!"

        # Mock config
        mock_config = MagicMock()
        mock_auth = MagicMock()
        mock_client = MagicMock()

        # Run command
        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth,
            client=mock_client,
            check_only=False,
            force=False
        )

        assert result["success"] is True
        assert result["update_available"] is False
        mock_print_success.assert_called_once()

    @patch("deepctl_cmd_update.command.subprocess.run")
    @patch("deepctl_cmd_update.command.InstallationDetector")
    @patch("deepctl_cmd_update.command.asyncio.run")
    @patch("deepctl_cmd_update.command.VersionChecker")
    @patch("deepctl_cmd_update.command.get_console")
    @patch("deepctl_cmd_update.command.format_version_message")
    @patch("deepctl_cmd_update.command.print_info")
    @patch("deepctl_cmd_update.command.print_success")
    @patch("deepctl_cmd_update.command.Confirm.ask")
    def test_pip_update(self, mock_confirm, mock_print_success, mock_print_info, mock_format_msg,
                        mock_console, mock_checker_class, mock_asyncio_run,
                        mock_detector_class, mock_subprocess, command):
        """Test update via pip."""
        # Mock version info
        mock_version_info = VersionInfo(
            current_version="0.1.0",
            latest_version="0.2.0",
            update_available=True,
        )

        # Configure asyncio.run to return the version info
        mock_asyncio_run.return_value = mock_version_info
        mock_format_msg.return_value = "Update available!"

        # Mock installation detection
        mock_detector = mock_detector_class.return_value
        mock_install_info = InstallationInfo(
            method=InstallMethod.PIP,
            path="/path/to/deepctl",
            virtual_env=True,
            editable=False,
            python_executable="/usr/bin/python3",
        )
        mock_detector.detect.return_value = mock_install_info
        mock_detector.get_update_command.return_value = "pip install --upgrade deepctl"

        # Mock subprocess
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stderr = ""
        mock_subprocess.return_value = mock_process

        # Mock user confirmation
        mock_confirm.return_value = True

        # Mock config
        mock_config = MagicMock()
        mock_auth = MagicMock()
        mock_client = MagicMock()

        # Run command
        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth,
            client=mock_client,
            yes=False
        )

        assert result["success"] is True
        assert result["installation_method"] == "pip"
        mock_subprocess.assert_called_once()

    @patch("deepctl_cmd_update.command.InstallationDetector")
    @patch("deepctl_cmd_update.command.asyncio.run")
    @patch("deepctl_cmd_update.command.VersionChecker")
    @patch("deepctl_cmd_update.command.get_console")
    @patch("deepctl_cmd_update.command.format_version_message")
    @patch("deepctl_cmd_update.command.print_warning")
    @patch("deepctl_cmd_update.command.print_info")
    def test_development_installation(self, mock_print_info, mock_print_warning, mock_format_msg,
                                      mock_console, mock_checker_class, mock_asyncio_run,
                                      mock_detector_class, command):
        """Test development installation handling."""
        # Mock version info
        mock_version_info = VersionInfo(
            current_version="0.1.0",
            latest_version="0.2.0",
            update_available=True,
        )

        # Configure asyncio.run to return the version info
        mock_asyncio_run.return_value = mock_version_info
        mock_format_msg.return_value = "Update available!"

        # Mock installation detection
        mock_detector = mock_detector_class.return_value
        mock_install_info = InstallationInfo(
            method=InstallMethod.DEVELOPMENT,
            path="/home/user/projects/deepctl",
            virtual_env=False,
            editable=True,
            python_executable="/usr/bin/python3",
        )
        mock_detector.detect.return_value = mock_install_info
        mock_detector.get_update_command.return_value = None
        mock_detector.get_update_instructions.return_value = (
            "Development installation detected. "
            "Please pull the latest changes from the repository:\n"
            "git pull origin main"
        )

        # Mock config
        mock_config = MagicMock()
        mock_auth = MagicMock()
        mock_client = MagicMock()

        # Run command
        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth,
            client=mock_client
        )

        # Can't auto-update development installations
        assert result["success"] is False
        assert result["installation_method"] == "development"
        mock_print_warning.assert_called_once()
