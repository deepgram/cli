"""Unit tests for plugin_env module."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

unix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="Unix-specific path test"
)

from deepctl_core.plugin_env import (
    PLUGIN_DIR,
    PLUGIN_STATE_FILE,
    PLUGIN_VENV,
    find_system_python,
    get_plugin_state,
    get_venv_python,
    get_venv_python_version,
    get_venv_site_packages,
    is_frozen,
    save_plugin_state,
)


class TestConstants:
    """Test path constants."""

    @pytest.mark.unit
    def test_plugin_dir_is_under_home(self):
        assert PLUGIN_DIR == Path.home() / ".deepctl" / "plugins"

    @pytest.mark.unit
    def test_plugin_venv_is_under_plugin_dir(self):
        assert PLUGIN_VENV == PLUGIN_DIR / "venv"

    @pytest.mark.unit
    def test_plugin_state_file_is_under_plugin_dir(self):
        assert PLUGIN_STATE_FILE == PLUGIN_DIR / "plugins.json"


class TestIsFrozen:
    """Test is_frozen() detection."""

    @pytest.mark.unit
    def test_not_frozen_by_default(self):
        # In normal Python environments sys.frozen doesn't exist
        assert is_frozen() is False

    @pytest.mark.unit
    def test_frozen_when_attr_set(self):
        with patch.object(sys, "frozen", True, create=True):
            assert is_frozen() is True

    @pytest.mark.unit
    def test_not_frozen_when_attr_false(self):
        with patch.object(sys, "frozen", False, create=True):
            assert is_frozen() is False


class TestFindSystemPython:
    """Test find_system_python()."""

    @pytest.mark.unit
    def test_finds_python_on_path(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "3 11\n"

        with patch("deepctl_core.plugin_env.shutil.which") as mock_which:
            mock_which.side_effect = lambda name: (
                f"/usr/bin/{name}" if name == "python3" else None
            )
            with patch(
                "deepctl_core.plugin_env.subprocess.run",
                return_value=mock_result,
            ):
                result = find_system_python()
                assert result == "/usr/bin/python3"

    @pytest.mark.unit
    def test_returns_none_when_no_python_found(self):
        with patch(
            "deepctl_core.plugin_env.shutil.which", return_value=None
        ):
            # Also ensure no Homebrew paths exist
            with patch.object(Path, "exists", return_value=False):
                result = find_system_python()
                assert result is None

    @pytest.mark.unit
    def test_skips_python_below_min_version(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "3 8\n"  # Below 3.10

        with patch("deepctl_core.plugin_env.shutil.which") as mock_which:
            mock_which.side_effect = lambda name: (
                f"/usr/bin/{name}" if name == "python3" else None
            )
            with patch(
                "deepctl_core.plugin_env.subprocess.run",
                return_value=mock_result,
            ):
                # No Homebrew paths
                with patch.object(Path, "exists", return_value=False):
                    result = find_system_python()
                    assert result is None

    @pytest.mark.unit
    def test_handles_timeout(self):
        import subprocess as sp

        with patch("deepctl_core.plugin_env.shutil.which") as mock_which:
            mock_which.side_effect = lambda name: (
                f"/usr/bin/{name}" if name == "python3" else None
            )
            with patch(
                "deepctl_core.plugin_env.subprocess.run",
                side_effect=sp.TimeoutExpired("python3", 5),
            ):
                with patch.object(Path, "exists", return_value=False):
                    result = find_system_python()
                    assert result is None


class TestGetVenvPython:
    """Test get_venv_python()."""

    @pytest.mark.unit
    def test_returns_none_when_venv_missing(self):
        with patch.object(Path, "exists", return_value=False):
            assert get_venv_python() is None

    @unix_only
    @pytest.mark.unit
    def test_returns_python_path_on_unix(self):
        def mock_exists(self_path):
            return True

        with patch.object(Path, "exists", mock_exists):
            with patch("deepctl_core.plugin_env.sys") as mock_sys:
                mock_sys.platform = "linux"
                result = get_venv_python()
                assert result is not None
                assert "bin/python" in result

    @pytest.mark.unit
    def test_returns_python_path_on_windows(self):
        def mock_exists(self_path):
            return True

        with patch.object(Path, "exists", mock_exists):
            with patch("deepctl_core.plugin_env.sys") as mock_sys:
                mock_sys.platform = "win32"
                result = get_venv_python()
                assert result is not None
                assert "Scripts" in result


class TestGetVenvSitePackages:
    """Test get_venv_site_packages()."""

    @pytest.mark.unit
    def test_returns_none_when_venv_missing(self):
        with patch.object(Path, "exists", return_value=False):
            assert get_venv_site_packages() is None

    @unix_only
    @pytest.mark.unit
    def test_finds_site_packages_on_unix(self, tmp_path):
        """Test finding site-packages in a real directory structure."""
        venv = tmp_path / "venv"
        sp = venv / "lib" / "python3.11" / "site-packages"
        sp.mkdir(parents=True)

        with patch("deepctl_core.plugin_env.PLUGIN_VENV", venv):
            result = get_venv_site_packages()
            assert result == sp

    @pytest.mark.unit
    def test_finds_site_packages_on_windows(self, tmp_path):
        """Test finding site-packages on Windows."""
        venv = tmp_path / "venv"
        sp = venv / "Lib" / "site-packages"
        sp.mkdir(parents=True)

        with patch("deepctl_core.plugin_env.PLUGIN_VENV", venv):
            with patch("deepctl_core.plugin_env.sys") as mock_sys:
                mock_sys.platform = "win32"
                result = get_venv_site_packages()
                assert result == sp


class TestGetVenvPythonVersion:
    """Test get_venv_python_version()."""

    @pytest.mark.unit
    def test_returns_none_when_venv_missing(self, tmp_path):
        venv = tmp_path / "missing-venv"
        with patch("deepctl_core.plugin_env.PLUGIN_VENV", venv):
            assert get_venv_python_version() is None

    @pytest.mark.unit
    def test_returns_none_when_pyvenv_cfg_missing(self, tmp_path):
        venv = tmp_path / "venv"
        venv.mkdir()
        with patch("deepctl_core.plugin_env.PLUGIN_VENV", venv):
            assert get_venv_python_version() is None

    @pytest.mark.unit
    def test_parses_stdlib_version_key(self, tmp_path):
        venv = tmp_path / "venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text(
            "home = /usr/bin\n"
            "include-system-site-packages = false\n"
            "version = 3.13.7\n"
        )
        with patch("deepctl_core.plugin_env.PLUGIN_VENV", venv):
            assert get_venv_python_version() == (3, 13)

    @pytest.mark.unit
    def test_parses_uv_version_info_key(self, tmp_path):
        venv = tmp_path / "venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text(
            "home = /Users/lukeocodes/.local/share/uv/python/cpython-3.12-macos\n"
            "implementation = CPython\n"
            "uv = 0.11.7\n"
            "version_info = 3.12.4\n"
        )
        with patch("deepctl_core.plugin_env.PLUGIN_VENV", venv):
            assert get_venv_python_version() == (3, 12)

    @pytest.mark.unit
    def test_returns_none_on_malformed_version(self, tmp_path):
        venv = tmp_path / "venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text(
            "home = /usr/bin\n"
            "version = not-a-version\n"
        )
        with patch("deepctl_core.plugin_env.PLUGIN_VENV", venv):
            assert get_venv_python_version() is None

    @pytest.mark.unit
    def test_returns_none_when_no_version_key(self, tmp_path):
        venv = tmp_path / "venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text(
            "home = /usr/bin\n"
            "include-system-site-packages = false\n"
        )
        with patch("deepctl_core.plugin_env.PLUGIN_VENV", venv):
            assert get_venv_python_version() is None


class TestGetPluginState:
    """Test get_plugin_state()."""

    @pytest.mark.unit
    def test_returns_empty_when_no_file(self):
        with patch.object(Path, "exists", return_value=False):
            state = get_plugin_state()
            assert state == {"plugins": {}}

    @pytest.mark.unit
    def test_reads_existing_state(self):
        test_state = {
            "plugins": {"test-plugin": {"version": "1.0.0"}}
        }
        with patch.object(Path, "exists", return_value=True):
            with patch.object(
                Path, "read_text", return_value=json.dumps(test_state)
            ):
                state = get_plugin_state()
                assert state == test_state

    @pytest.mark.unit
    def test_returns_empty_on_parse_error(self):
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "read_text", return_value="not json"):
                state = get_plugin_state()
                assert state == {"plugins": {}}


class TestSavePluginState:
    """Test save_plugin_state()."""

    @pytest.mark.unit
    def test_saves_state(self):
        test_state = {
            "plugins": {"test-plugin": {"version": "1.0.0"}}
        }
        with patch.object(Path, "mkdir") as mock_mkdir:
            with patch.object(Path, "write_text") as mock_write:
                save_plugin_state(test_state)
                mock_mkdir.assert_called_once()
                written = json.loads(mock_write.call_args[0][0])
                assert written == test_state
