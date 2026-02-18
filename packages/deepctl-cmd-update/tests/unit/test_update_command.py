"""Unit tests for update command."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest
from deepctl_cmd_update.command import UpdateCommand
from deepctl_cmd_update.installation import InstallationInfo, InstallMethod
from deepctl_cmd_update.version_check import VersionInfo


class TestUpdateCommand:
    """Test update command functionality."""

    @pytest.fixture
    def command(self):
        """Create update command instance."""
        return UpdateCommand()

    # ------------------------------------------------------------------
    # check-only mode
    # ------------------------------------------------------------------

    @patch("deepctl_cmd_update.command.asyncio.run")
    @patch("deepctl_cmd_update.command.VersionChecker")
    @patch("deepctl_cmd_update.command.get_console")
    @patch("deepctl_cmd_update.command.format_version_message")
    def test_check_only(
        self,
        mock_format_msg,
        mock_console,
        mock_checker_class,
        mock_asyncio_run,
        command,
    ):
        """Test check-only mode."""
        mock_version_info = VersionInfo(
            current_version="0.1.0",
            latest_version="0.2.0",
            update_available=True,
        )
        mock_asyncio_run.return_value = mock_version_info
        mock_format_msg.return_value = "Update available!"

        mock_config = MagicMock()
        mock_auth = MagicMock()
        mock_client = MagicMock()

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth,
            client=mock_client,
            check_only=True,
        )

        assert result["success"] is True
        assert result["update_available"] is True
        assert result["current_version"] == "0.1.0"
        assert result["latest_version"] == "0.2.0"

    # ------------------------------------------------------------------
    # --check alias
    # ------------------------------------------------------------------

    def test_check_alias_defined(self, command):
        """Test that --check is an alias for --check-only."""
        args = command.get_arguments()
        check_arg = next(
            a for a in args if "--check-only" in a["names"]
        )
        assert "--check" in check_arg["names"]

    # ------------------------------------------------------------------
    # no update available
    # ------------------------------------------------------------------

    @patch("deepctl_cmd_update.command.asyncio.run")
    @patch("deepctl_cmd_update.command.VersionChecker")
    @patch("deepctl_cmd_update.command.get_console")
    @patch("deepctl_cmd_update.command.format_version_message")
    @patch("deepctl_cmd_update.command.print_success")
    def test_no_update_available(
        self,
        mock_print_success,
        mock_format_msg,
        mock_console,
        mock_checker_class,
        mock_asyncio_run,
        command,
    ):
        """Test when no update is available."""
        mock_version_info = VersionInfo(
            current_version="0.2.0",
            latest_version="0.2.0",
            update_available=False,
        )
        mock_asyncio_run.return_value = mock_version_info
        mock_format_msg.return_value = "Already up to date!"

        mock_config = MagicMock()
        mock_auth = MagicMock()
        mock_client = MagicMock()

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth,
            client=mock_client,
            check_only=False,
            force=False,
        )

        assert result["success"] is True
        assert result["update_available"] is False
        mock_print_success.assert_called_once()

    # ------------------------------------------------------------------
    # pip update (verifies list[str] command and no shell=True)
    # ------------------------------------------------------------------

    @patch("deepctl_cmd_update.command.subprocess.run")
    @patch("deepctl_cmd_update.command.InstallationDetector")
    @patch("deepctl_cmd_update.command.asyncio.run")
    @patch("deepctl_cmd_update.command.VersionChecker")
    @patch("deepctl_cmd_update.command.get_console")
    @patch("deepctl_cmd_update.command.format_version_message")
    @patch("deepctl_cmd_update.command.print_info")
    @patch("deepctl_cmd_update.command.print_success")
    @patch("deepctl_cmd_update.command.Confirm.ask")
    def test_pip_update(
        self,
        mock_confirm,
        mock_print_success,
        mock_print_info,
        mock_format_msg,
        mock_console,
        mock_checker_class,
        mock_asyncio_run,
        mock_detector_class,
        mock_subprocess,
        command,
    ):
        """Test update via pip — command is a list, shell=True absent."""
        mock_version_info = VersionInfo(
            current_version="0.1.0",
            latest_version="0.2.0",
            update_available=True,
        )
        mock_asyncio_run.return_value = mock_version_info
        mock_format_msg.return_value = "Update available!"

        mock_detector = mock_detector_class.return_value
        mock_install_info = InstallationInfo(
            method=InstallMethod.PIP,
            path="/path/to/deepctl",
            virtual_env=True,
            editable=False,
            python_executable="/usr/bin/python3",
        )
        mock_detector.detect.return_value = mock_install_info
        mock_detector.get_update_command.return_value = [
            "pip", "install", "--upgrade", "deepctl",
        ]

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stderr = ""
        mock_subprocess.return_value = mock_process

        mock_confirm.return_value = True

        mock_config = MagicMock()
        mock_auth = MagicMock()
        mock_client = MagicMock()

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth,
            client=mock_client,
            yes=False,
        )

        assert result["success"] is True
        assert result["installation_method"] == "pip"
        mock_subprocess.assert_called_once()

        # Verify shell=True is NOT passed
        call_kwargs = mock_subprocess.call_args
        assert call_kwargs.kwargs.get("shell") is not True
        # Verify command is a list
        cmd_arg = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("args")
        assert isinstance(cmd_arg, list)

    # ------------------------------------------------------------------
    # Homebrew update
    # ------------------------------------------------------------------

    @patch("deepctl_cmd_update.command.subprocess.run")
    @patch("deepctl_cmd_update.command.InstallationDetector")
    @patch("deepctl_cmd_update.command.asyncio.run")
    @patch("deepctl_cmd_update.command.VersionChecker")
    @patch("deepctl_cmd_update.command.get_console")
    @patch("deepctl_cmd_update.command.format_version_message")
    @patch("deepctl_cmd_update.command.print_info")
    @patch("deepctl_cmd_update.command.print_success")
    @patch("deepctl_cmd_update.command.Confirm.ask")
    def test_homebrew_update(
        self,
        mock_confirm,
        mock_print_success,
        mock_print_info,
        mock_format_msg,
        mock_console,
        mock_checker_class,
        mock_asyncio_run,
        mock_detector_class,
        mock_subprocess,
        command,
    ):
        """Test update via Homebrew."""
        mock_version_info = VersionInfo(
            current_version="0.1.0",
            latest_version="0.2.0",
            update_available=True,
        )
        mock_asyncio_run.return_value = mock_version_info
        mock_format_msg.return_value = "Update available!"

        mock_detector = mock_detector_class.return_value
        mock_install_info = InstallationInfo(
            method=InstallMethod.HOMEBREW,
            path="/opt/homebrew/Cellar/deepctl/0.1.0",
            virtual_env=False,
            editable=False,
            python_executable="/opt/homebrew/bin/python3",
        )
        mock_detector.detect.return_value = mock_install_info
        mock_detector.get_update_command.return_value = [
            "brew", "upgrade", "deepctl",
        ]

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stderr = ""
        mock_subprocess.return_value = mock_process
        mock_confirm.return_value = True

        mock_config = MagicMock()
        mock_auth = MagicMock()
        mock_client = MagicMock()

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth,
            client=mock_client,
            yes=True,
        )

        assert result["success"] is True
        assert result["installation_method"] == "homebrew"

    # ------------------------------------------------------------------
    # development installation
    # ------------------------------------------------------------------

    @patch("deepctl_cmd_update.command.InstallationDetector")
    @patch("deepctl_cmd_update.command.asyncio.run")
    @patch("deepctl_cmd_update.command.VersionChecker")
    @patch("deepctl_cmd_update.command.get_console")
    @patch("deepctl_cmd_update.command.format_version_message")
    @patch("deepctl_cmd_update.command.print_warning")
    @patch("deepctl_cmd_update.command.print_info")
    def test_development_installation(
        self,
        mock_print_info,
        mock_print_warning,
        mock_format_msg,
        mock_console,
        mock_checker_class,
        mock_asyncio_run,
        mock_detector_class,
        command,
    ):
        """Test development installation handling."""
        mock_version_info = VersionInfo(
            current_version="0.1.0",
            latest_version="0.2.0",
            update_available=True,
        )
        mock_asyncio_run.return_value = mock_version_info
        mock_format_msg.return_value = "Update available!"

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

        mock_config = MagicMock()
        mock_auth = MagicMock()
        mock_client = MagicMock()

        result = command.handle(
            config=mock_config, auth_manager=mock_auth, client=mock_client
        )

        assert result["success"] is False
        assert result["installation_method"] == "development"
        mock_print_warning.assert_called_once()
