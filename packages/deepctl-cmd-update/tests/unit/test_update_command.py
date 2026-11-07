"""Unit tests for update command."""

import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from deepctl_cmd_update.command import UpdateCommand
from deepctl_cmd_update.installation import InstallationInfo, InstallMethod
from deepctl_cmd_update.version_check import VersionInfo


def _real_config(tmp_path, monkeypatch):
    """The default user config (so update.* keys are honored), in tmp_path."""
    from deepctl_core import Config

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    path = tmp_path / "xdg" / "deepctl" / "config.yaml"
    path.parent.mkdir(parents=True, mode=0o700)
    path.write_text(
        "profiles:\n  default:\n    project_id: P1\n"
        "update:\n  installation_method: pipx\n  check_frequency: daily\n"
    )
    os.chmod(path, 0o600)
    config = Config()
    assert config.config_path == path
    return config, path


class TestConfigUntouched:
    """dg update never writes the user config (no implicit save)."""

    @pytest.mark.parametrize("returncode", [0, 1])
    @patch("deepctl_cmd_update.command.subprocess.run")
    @patch("deepctl_cmd_update.command.InstallationDetector.detect")
    @patch("deepctl_cmd_update.command.asyncio.run")
    def test_full_update_leaves_the_file_byte_identical(
        self, run_check, detect, run, returncode, tmp_path, monkeypatch
    ):
        config, path = _real_config(tmp_path, monkeypatch)
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        run_check.return_value = VersionInfo(
            current_version="0.1.0", latest_version="0.2.0", update_available=True
        )
        detect.return_value = InstallationInfo(
            method=InstallMethod.PIP,
            path="/p",
            virtual_env=True,
            editable=False,
            python_executable="/p/bin/python3",
        )
        run.return_value = subprocess.CompletedProcess([], returncode, "", "")
        UpdateCommand().handle(
            config=config, auth_manager=MagicMock(), client=MagicMock(), yes=True
        )
        assert (path.read_bytes(), path.stat().st_mtime_ns) == before
        # The legacy key is still readable, and was not rewritten.
        assert config.get("update.installation_method") == "pipx"


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
            "brew", "upgrade", "deepgram",
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

    # ------------------------------------------------------------------
    # declining the confirmation prompt
    # ------------------------------------------------------------------

    @patch("deepctl_cmd_update.command.subprocess.run")
    @patch("deepctl_cmd_update.command.InstallationDetector")
    @patch("deepctl_cmd_update.command.asyncio.run")
    @patch("deepctl_cmd_update.command.VersionChecker")
    @patch("deepctl_cmd_update.command.get_console")
    @patch("deepctl_cmd_update.command.format_version_message")
    @patch("deepctl_cmd_update.command.print_info")
    @patch("deepctl_cmd_update.command.Confirm.ask")
    def test_declining_the_prompt_reports_cancelled_and_exits_two(
        self,
        mock_confirm,
        mock_print_info,
        mock_format_msg,
        mock_console,
        mock_checker_class,
        mock_asyncio_run,
        mock_detector_class,
        mock_subprocess,
        command,
    ):
        """Declining must exit 2, and say "cancelled" in the payload.

        The README documents exit 2 for declining a confirmation prompt.
        This path used to return `.model_dump()` -- a plain dict, which
        `BaseCommand.exit_code_for` cannot read a status off -- so the
        process exited 0 while the very same JSON payload carried
        `"status": "success"` next to "Update cancelled by user".
        """
        mock_asyncio_run.return_value = VersionInfo(
            current_version="0.1.0",
            latest_version="0.2.0",
            update_available=True,
        )
        mock_format_msg.return_value = "Update available!"

        mock_detector = mock_detector_class.return_value
        mock_detector.detect.return_value = InstallationInfo(
            method=InstallMethod.PIP,
            path="/path/to/deepctl",
            virtual_env=True,
            editable=False,
            python_executable="/usr/bin/python3",
        )
        mock_detector.get_update_command.return_value = [
            "pip",
            "install",
            "--upgrade",
            "deepctl",
        ]

        mock_confirm.return_value = False

        result = command.handle(
            config=MagicMock(),
            auth_manager=MagicMock(),
            client=MagicMock(),
            yes=False,
        )

        assert command.exit_code_for(result) == 2
        assert result.status == "cancelled"
        assert result.success is False
        assert result.message == "Update cancelled by user"
        mock_subprocess.assert_not_called()

    @patch("deepctl_cmd_update.command.subprocess.run")
    @patch("deepctl_cmd_update.command.InstallationDetector")
    @patch("deepctl_cmd_update.command.asyncio.run")
    @patch("deepctl_cmd_update.command.VersionChecker")
    @patch("deepctl_cmd_update.command.get_console")
    @patch("deepctl_cmd_update.command.format_version_message")
    @patch("deepctl_cmd_update.command.print_info")
    @patch("deepctl_cmd_update.command.print_success")
    @patch("deepctl_cmd_update.command.Confirm.ask")
    def test_successful_update_still_exits_zero(
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
        """Negative control: accepting the prompt must still exit 0.

        Only the decline path carries a status, so the success payload maps
        to 0 -- the point is that adding "cancelled" did not make an accepted
        update exit non-zero.
        """
        mock_asyncio_run.return_value = VersionInfo(
            current_version="0.1.0",
            latest_version="0.2.0",
            update_available=True,
        )
        mock_format_msg.return_value = "Update available!"

        mock_detector = mock_detector_class.return_value
        mock_detector.detect.return_value = InstallationInfo(
            method=InstallMethod.PIP,
            path="/path/to/deepctl",
            virtual_env=True,
            editable=False,
            python_executable="/usr/bin/python3",
        )
        mock_detector.get_update_command.return_value = [
            "pip",
            "install",
            "--upgrade",
            "deepctl",
        ]
        mock_subprocess.return_value = MagicMock(returncode=0, stderr="")
        mock_confirm.return_value = True

        result = command.handle(
            config=MagicMock(),
            auth_manager=MagicMock(),
            client=MagicMock(),
            yes=False,
        )

        assert command.exit_code_for(result) == 0
        assert result["success"] is True
        mock_subprocess.assert_called_once()

    # ------------------------------------------------------------------
    # failure paths
    # ------------------------------------------------------------------

    @patch("deepctl_cmd_update.command.subprocess.run")
    @patch("deepctl_cmd_update.command.InstallationDetector")
    @patch("deepctl_cmd_update.command.asyncio.run")
    @patch("deepctl_cmd_update.command.VersionChecker")
    @patch("deepctl_cmd_update.command.get_console")
    @patch("deepctl_cmd_update.command.format_version_message")
    @patch("deepctl_cmd_update.command.print_info")
    @patch("deepctl_cmd_update.command.print_error")
    @patch("deepctl_cmd_update.command.Confirm.ask")
    def test_failed_update_exits_one(
        self,
        mock_confirm,
        mock_print_error,
        mock_print_info,
        mock_format_msg,
        mock_console,
        mock_checker_class,
        mock_asyncio_run,
        mock_detector_class,
        mock_subprocess,
        command,
    ):
        """A pip that exits non-zero must make `dg update` exit 1."""
        mock_asyncio_run.return_value = VersionInfo(
            current_version="0.1.0",
            latest_version="0.2.0",
            update_available=True,
        )
        mock_format_msg.return_value = "Update available!"

        mock_detector = mock_detector_class.return_value
        mock_detector.detect.return_value = InstallationInfo(
            method=InstallMethod.PIP,
            path="/path/to/deepctl",
            virtual_env=True,
            editable=False,
            python_executable="/usr/bin/python3",
        )
        mock_detector.get_update_command.return_value = [
            "pip",
            "install",
            "--upgrade",
            "deepctl",
        ]
        mock_subprocess.return_value = MagicMock(
            returncode=1, stderr="No matching distribution"
        )
        mock_confirm.return_value = True

        result = command.handle(
            config=MagicMock(),
            auth_manager=MagicMock(),
            client=MagicMock(),
            yes=True,
        )

        assert command.exit_code_for(result) == 1
        assert result.status == "error"
        assert "No matching distribution" in result.message

    @patch("deepctl_cmd_update.command.asyncio.run")
    @patch("deepctl_cmd_update.command.VersionChecker")
    @patch("deepctl_cmd_update.command.get_console")
    @patch("deepctl_cmd_update.command.print_error")
    def test_failed_version_check_exits_one(
        self,
        mock_print_error,
        mock_console,
        mock_checker_class,
        mock_asyncio_run,
        command,
    ):
        """An unreachable PyPI must make `dg update` exit 1, not 0."""
        mock_asyncio_run.side_effect = RuntimeError("connection refused")

        result = command.handle(
            config=MagicMock(),
            auth_manager=MagicMock(),
            client=MagicMock(),
        )

        assert command.exit_code_for(result) == 1
        assert result.status == "error"
        assert "connection refused" in result.message


class TestDryRun:
    """`dg update --dry-run` reports the method and command, and nothing else."""

    @pytest.mark.parametrize(
        ("method", "expected"),
        [
            (InstallMethod.PIPX, "Run: pipx upgrade deepctl"),
            (InstallMethod.UV_TOOL, "Run: uv tool install deepctl@latest"),
            (InstallMethod.HOMEBREW, "Run: brew upgrade deepgram"),
        ],
    )
    @patch("deepctl_cmd_update.command.VersionChecker")
    @patch("deepctl_cmd_update.command.subprocess.run")
    @patch("deepctl_cmd_update.command.InstallationDetector.detect")
    def test_prints_the_command(
        self, detect, run, checker, method, expected, capsys, monkeypatch
    ):
        from deepctl_core import output

        monkeypatch.setitem(output._output_config, "format", "default")
        monkeypatch.setitem(output._output_config, "quiet", False)
        detect.return_value = InstallationInfo(
            method=method,
            path="/p",
            virtual_env=True,
            editable=False,
            python_executable="/p/bin/python3",
        )
        config = MagicMock()
        command = UpdateCommand()
        result = command.handle(
            config=config, auth_manager=MagicMock(), client=MagicMock(), dry_run=True
        )
        assert result["message"] == expected
        assert result["installation_method"] == method.value
        assert command.exit_code_for(result) == 0
        out = capsys.readouterr().out
        assert expected in out
        assert f"Installation method: {method.value}" in out
        checker.assert_not_called()
        run.assert_not_called()
        config.save.assert_not_called()
        config._set_config_value.assert_not_called()

    @patch("deepctl_cmd_update.command.InstallationDetector.detect")
    def test_dry_run_leaves_the_config_byte_identical(
        self, detect, tmp_path, monkeypatch
    ):
        config, path = _real_config(tmp_path, monkeypatch)
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        detect.return_value = InstallationInfo(
            method=InstallMethod.PIPX,
            path="/p",
            virtual_env=True,
            editable=False,
            python_executable="/p/bin/python3",
        )
        UpdateCommand().handle(
            config=config, auth_manager=MagicMock(), client=MagicMock(), dry_run=True
        )
        assert (path.read_bytes(), path.stat().st_mtime_ns) == before


    @patch("deepctl_cmd_update.command.InstallationDetector.detect")
    def test_json_keeps_stdout_for_the_payload(self, detect, capsys, monkeypatch):
        from deepctl_core import output

        monkeypatch.setitem(output._output_config, "format", "json")
        detect.return_value = InstallationInfo(
            method=InstallMethod.PIPX,
            path="/p",
            virtual_env=True,
            editable=False,
            python_executable="/p/bin/python3",
        )
        result = UpdateCommand().handle(
            config=MagicMock(), auth_manager=MagicMock(), client=MagicMock(), dry_run=True
        )
        assert result["message"] == "Run: pipx upgrade deepctl"
        assert capsys.readouterr().out == ""


class TestUnreachableIndex:
    @patch("deepctl_cmd_update.command.asyncio.run")
    @patch("deepctl_cmd_update.command.VersionChecker")
    def test_raises_update_check_failed(self, checker, run):
        from deepctl_cmd_update.version_check import UpdateCheckError
        from deepctl_core.transport import TransportError

        run.side_effect = UpdateCheckError("pypi.org")
        with pytest.raises(TransportError) as raised:
            UpdateCommand().handle(
                config=MagicMock(), auth_manager=MagicMock(), client=MagicMock()
            )
        err = raised.value
        assert err.code == "update_check_failed"
        assert err.details == {"host": "pypi.org"}
        assert err.error == "Couldn't check for updates: couldn't reach pypi.org."

    def test_renders_with_both_hints(self, monkeypatch):
        from deepctl_core import output, transport

        err = transport.TransportError(
            "update_check_failed", values={"host": "pypi.org"}, details={"host": "pypi.org"}
        )
        monkeypatch.setitem(output._output_config, "agentic", False)
        human = transport.render(err, command_path="update")
        assert human.code == "update_check_failed"
        assert human.hint.startswith("Check your network connection")
        assert human.next_step is None
        assert human.details == {"host": "pypi.org"}
        monkeypatch.setitem(output._output_config, "agentic", True)
        monkeypatch.setenv("AI_AGENT", "x")
        agent = transport.render(err, command_path="update")
        assert agent.hint.startswith("The update check needs network access")
