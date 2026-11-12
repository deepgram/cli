"""Tests for API-key handling in support-toolkit commands."""

import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
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


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX directory modes")
def test_unreadable_cache_counts_as_no_cache(tmp_path: Path, monkeypatch) -> None:
    """Loading the debug group reads the toolkit cache; a HOME the user
    can't read must not fail it (every `dg debug <sub> --help` loads it)."""
    import os

    import pytest

    from deepctl_cmd_debug_toolkit import command, fetcher

    if os.geteuid() == 0:
        pytest.skip("root can read any directory")
    locked = tmp_path / "home"
    (locked / "toolkit").mkdir(parents=True)
    monkeypatch.setattr(
        fetcher, "_MANIFEST_CACHE", locked / "toolkit" / "manifest.json"
    )
    monkeypatch.setattr(fetcher, "_MANIFEST_AGE", locked / "toolkit" / "manifest.age")
    locked.chmod(0)
    try:
        assert fetcher.get_cached_manifest() is None
        names = [c.name for c in command.ToolkitCommand().setup_commands()]
    finally:
        locked.chmod(0o700)
    assert names == ["refresh"]
