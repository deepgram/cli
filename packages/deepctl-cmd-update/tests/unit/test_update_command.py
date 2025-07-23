"""Unit tests for update command."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from deepctl_cmd_update.command import UpdateCommand
from deepctl_core import VersionInfo, InstallationInfo, InstallMethod


class TestUpdateCommand:
    """Test update command functionality."""

    @pytest.fixture
    def command(self):
        """Create update command instance."""
        return UpdateCommand()

    @pytest.fixture
    def mock_args(self):
        """Create mock arguments."""
        args = MagicMock()
        args.check_only = False
        args.force = False
        args.yes = True  # Skip confirmation
        return args

    @pytest.mark.asyncio
    async def test_check_only(self, command, mock_args):
        """Test check-only mode."""
        mock_args.check_only = True

        # Mock version checker
        with patch("deepctl_cmd_update.command.VersionChecker") as mock_checker_class:
            mock_checker = mock_checker_class.return_value
            mock_checker.check_version = AsyncMock(return_value=VersionInfo(
                current_version="0.1.0",
                latest_version="0.2.0",
                update_available=True,
            ))

            # Mock config
            with patch("deepctl_cmd_update.command.Config"):
                result = await command.run(mock_args)

                assert result["success"] is True
                assert result["update_available"] is True
                assert result["current_version"] == "0.1.0"
                assert result["latest_version"] == "0.2.0"

    @pytest.mark.asyncio
    async def test_no_update_available(self, command, mock_args):
        """Test when no update is available."""
        # Mock version checker
        with patch("deepctl_cmd_update.command.VersionChecker") as mock_checker_class:
            mock_checker = mock_checker_class.return_value
            mock_checker.check_version = AsyncMock(return_value=VersionInfo(
                current_version="0.2.0",
                latest_version="0.2.0",
                update_available=False,
            ))

            # Mock config
            with patch("deepctl_cmd_update.command.Config"):
                result = await command.run(mock_args)

                assert result["success"] is True
                assert result["update_available"] is False

    @pytest.mark.asyncio
    async def test_pip_update(self, command, mock_args):
        """Test update via pip."""
        # Mock version checker
        with patch("deepctl_cmd_update.command.VersionChecker") as mock_checker_class:
            mock_checker = mock_checker_class.return_value
            mock_checker.check_version = AsyncMock(return_value=VersionInfo(
                current_version="0.1.0",
                latest_version="0.2.0",
                update_available=True,
            ))

            # Mock installation detector
            with patch("deepctl_cmd_update.command.InstallationDetector") as mock_detector_class:
                mock_detector = mock_detector_class.return_value
                mock_detector.detect.return_value = InstallationInfo(
                    method=InstallMethod.PIP,
                    path="/path/to/deepctl",
                    virtual_env=True,
                    editable=False,
                    python_executable="/usr/bin/python3",
                )
                mock_detector.get_update_command.return_value = "pip install --upgrade deepctl"

                # Mock subprocess
                with patch("deepctl_cmd_update.command.asyncio.create_subprocess_shell") as mock_subprocess:
                    mock_process = AsyncMock()
                    mock_process.communicate = AsyncMock(
                        return_value=(b"Success", b""))
                    mock_process.returncode = 0
                    mock_subprocess.return_value = mock_process

                    # Mock config
                    with patch("deepctl_cmd_update.command.Config") as mock_config_class:
                        mock_config = mock_config_class.return_value
                        mock_config._set_config_value = MagicMock()
                        mock_config.save = MagicMock()

                        result = await command.run(mock_args)

                        assert result["success"] is True
                        assert "Successfully updated" in result["message"]
                        assert result["installation_method"] == InstallMethod.PIP

                        # Verify update command was executed
                        mock_subprocess.assert_called_once_with(
                            "pip install --upgrade deepctl",
                            stdout=-1,  # asyncio.subprocess.PIPE
                            stderr=-1,
                        )

    @pytest.mark.asyncio
    async def test_development_installation(self, command, mock_args):
        """Test development installation handling."""
        # Mock version checker
        with patch("deepctl_cmd_update.command.VersionChecker") as mock_checker_class:
            mock_checker = mock_checker_class.return_value
            mock_checker.check_version = AsyncMock(return_value=VersionInfo(
                current_version="0.1.0",
                latest_version="0.2.0",
                update_available=True,
            ))

            # Mock installation detector
            with patch("deepctl_cmd_update.command.InstallationDetector") as mock_detector_class:
                mock_detector = mock_detector_class.return_value
                mock_detector.detect.return_value = InstallationInfo(
                    method=InstallMethod.DEVELOPMENT,
                    path="/home/user/projects/deepctl",
                    virtual_env=False,
                    editable=True,
                    python_executable="/usr/bin/python3",
                )
                mock_detector.get_update_command.return_value = None
                mock_detector.get_update_instructions.return_value = (
                    "Development installation detected. "
                    "Please pull the latest changes from the repository:\n"
                    "git pull origin main"
                )

                # Mock config
                with patch("deepctl_cmd_update.command.Config"):
                    result = await command.run(mock_args)

                    assert result["success"] is False
                    assert "Development installation" in result["message"]
                    assert result["installation_method"] == InstallMethod.DEVELOPMENT
