"""Tests for API-key handling in support-toolkit commands."""

from pathlib import Path
from unittest.mock import Mock, patch

from click.testing import CliRunner
from deepctl_core import AuthenticationError

from deepctl_cmd_debug_toolkit.command import _make_script_command
from deepctl_cmd_debug_toolkit.models import ToolkitScript


def test_expired_credential_warning_is_visible_to_toolkit_script() -> None:
    command = _make_script_command(
        "diagnose",
        ToolkitScript(
            description="Run diagnostics",
            script="diagnose.py",
            pass_api_key=True,
        ),
    )
    with (
        patch(
            "deepctl_cmd_debug_toolkit.command.get_or_fetch_script",
            return_value=Path("/tmp/diagnose.py"),
        ),
        patch("deepctl_cmd_debug_toolkit.command.AuthManager") as auth_manager,
        patch(
            "deepctl_cmd_debug_toolkit.command.subprocess.run",
            return_value=Mock(returncode=0),
        ),
    ):
        auth_manager.return_value.get_api_key.side_effect = AuthenticationError(
            "Your Deepgram credential has expired. Run `dg login` again."
        )
        result = CliRunner().invoke(command)

    assert result.exit_code == 0
    assert "Run `dg login` again" in result.output
