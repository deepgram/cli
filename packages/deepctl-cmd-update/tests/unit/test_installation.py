"""Unit tests for installation detection."""

from pathlib import Path
from unittest.mock import patch

import pytest
from deepctl_cmd_update.installation import (
    InstallationDetector,
    InstallMethod,
)


class TestInstallMethod:
    """Test InstallMethod enum values."""

    def test_all_methods_defined(self):
        """Verify all expected methods exist."""
        expected = {
            "pip", "pipx", "uv", "homebrew", "uv_tool",
            "uvx", "pipx_run", "system", "development", "unknown",
        }
        actual = {m.value for m in InstallMethod}
        assert actual == expected


class TestGetUpdateCommand:
    """Test get_update_command return type and values."""

    @pytest.fixture
    def detector(self):
        return InstallationDetector()

    def test_returns_list_for_pip(self, detector):
        cmd = detector.get_update_command(InstallMethod.PIP)
        assert isinstance(cmd, list)
        assert cmd == ["pip", "install", "--upgrade", "deepctl"]

    def test_returns_list_for_pipx(self, detector):
        cmd = detector.get_update_command(InstallMethod.PIPX)
        assert isinstance(cmd, list)
        assert cmd == ["pipx", "upgrade", "deepctl"]

    def test_returns_list_for_uv(self, detector):
        cmd = detector.get_update_command(InstallMethod.UV)
        assert isinstance(cmd, list)
        assert cmd == ["uv", "pip", "install", "--upgrade", "deepctl"]

    def test_returns_list_for_homebrew(self, detector):
        cmd = detector.get_update_command(InstallMethod.HOMEBREW)
        assert isinstance(cmd, list)
        assert cmd == ["brew", "upgrade", "deepctl"]

    def test_returns_list_for_uv_tool(self, detector):
        cmd = detector.get_update_command(InstallMethod.UV_TOOL)
        assert isinstance(cmd, list)
        assert cmd == ["uv", "tool", "upgrade", "deepctl"]

    def test_returns_none_for_uvx(self, detector):
        cmd = detector.get_update_command(InstallMethod.UVX)
        assert cmd is None

    def test_returns_none_for_pipx_run(self, detector):
        cmd = detector.get_update_command(InstallMethod.PIPX_RUN)
        assert cmd is None

    def test_returns_none_for_development(self, detector):
        cmd = detector.get_update_command(InstallMethod.DEVELOPMENT)
        assert cmd is None

    def test_returns_none_for_unknown(self, detector):
        cmd = detector.get_update_command(InstallMethod.UNKNOWN)
        assert cmd is None


class TestHomebrewDetection:
    """Test Homebrew installation detection."""

    @pytest.fixture
    def detector(self):
        return InstallationDetector()

    @patch("deepctl_cmd_update.installation.sys")
    def test_apple_silicon_path(self, mock_sys):
        """Detect Homebrew on Apple Silicon."""
        mock_sys.executable = "/opt/homebrew/bin/python3"
        detector = InstallationDetector()
        # Patch resolve to return the same path
        with patch.object(
            Path, "resolve", return_value=Path("/opt/homebrew/bin/python3")
        ):
            assert detector._is_homebrew_install() is True

    @patch("deepctl_cmd_update.installation.sys")
    def test_intel_mac_path(self, mock_sys):
        """Detect Homebrew on Intel Mac."""
        mock_sys.executable = "/usr/local/Cellar/python@3.12/bin/python3"
        detector = InstallationDetector()
        with patch.object(
            Path,
            "resolve",
            return_value=Path(
                "/usr/local/Cellar/python@3.12/bin/python3"
            ),
        ):
            assert detector._is_homebrew_install() is True

    @patch("deepctl_cmd_update.installation.sys")
    def test_linux_homebrew_path(self, mock_sys):
        """Detect Homebrew on Linux."""
        mock_sys.executable = (
            "/home/linuxbrew/.linuxbrew/bin/python3"
        )
        detector = InstallationDetector()
        with patch.object(
            Path,
            "resolve",
            return_value=Path(
                "/home/linuxbrew/.linuxbrew/bin/python3"
            ),
        ):
            assert detector._is_homebrew_install() is True

    @patch("deepctl_cmd_update.installation.sys")
    def test_non_homebrew_path(self, mock_sys):
        """Non-Homebrew path returns False."""
        mock_sys.executable = "/usr/bin/python3"
        detector = InstallationDetector()
        with patch.object(
            Path, "resolve", return_value=Path("/usr/bin/python3")
        ):
            assert detector._is_homebrew_install() is False


class TestUvToolDetection:
    """Test uv tool installation detection."""

    @pytest.fixture
    def detector(self):
        return InstallationDetector()

    @patch("deepctl_cmd_update.installation.sys")
    @patch.dict(
        "os.environ", {"UV_TOOL_DIR": "/custom/uv/tools"}, clear=False
    )
    def test_custom_uv_tool_dir(self, mock_sys):
        """Detect uv tool with custom UV_TOOL_DIR."""
        mock_sys.executable = "/custom/uv/tools/deepctl/bin/python3"
        detector = InstallationDetector()
        with patch.object(
            Path,
            "resolve",
            return_value=Path(
                "/custom/uv/tools/deepctl/bin/python3"
            ),
        ):
            assert detector._is_uv_tool_install() is True

    @patch("deepctl_cmd_update.installation.sys")
    @patch.dict("os.environ", {}, clear=False)
    def test_default_uv_tool_path(self, mock_sys):
        """Detect uv tool at default location."""
        home = str(Path.home())
        exe = f"{home}/.local/share/uv/tools/deepctl/bin/python3"
        mock_sys.executable = exe
        detector = InstallationDetector()
        with patch.object(Path, "resolve", return_value=Path(exe)):
            assert detector._is_uv_tool_install() is True


class TestOneshotDetection:
    """Test one-shot execution detection (uvx / pipx run)."""

    @pytest.fixture
    def detector(self):
        return InstallationDetector()

    @patch.dict(
        "os.environ",
        {"UV_INTERNAL__PARENT_INTERPRETER": "/usr/bin/python3"},
        clear=False,
    )
    def test_uvx_detected(self, detector):
        """uvx is detected via environment variable."""
        result = detector._detect_oneshot_execution()
        assert result == InstallMethod.UVX

    @patch("deepctl_cmd_update.installation.sys")
    @patch.dict("os.environ", {}, clear=False)
    def test_pipx_run_detected(self, mock_sys):
        """pipx run is detected via cache path."""
        home = str(Path.home())
        mock_sys.executable = f"{home}/.local/pipx/.cache/some/bin/python3"
        detector = InstallationDetector()
        with patch.object(
            Path,
            "resolve",
            return_value=Path(
                f"{home}/.local/pipx/.cache/some/bin/python3"
            ),
        ):
            result = detector._detect_oneshot_execution()
            assert result == InstallMethod.PIPX_RUN

    @patch.dict("os.environ", {}, clear=False)
    def test_no_oneshot(self, detector):
        """Regular execution returns None."""
        # Make sure UV_INTERNAL__PARENT_INTERPRETER is not set
        import os
        os.environ.pop("UV_INTERNAL__PARENT_INTERPRETER", None)
        result = detector._detect_oneshot_execution()
        assert result is None


class TestGetUpdateInstructions:
    """Test update instructions for various methods."""

    @pytest.fixture
    def detector(self):
        return InstallationDetector()

    def test_homebrew_instructions(self, detector):
        from deepctl_cmd_update.installation import InstallationInfo

        info = InstallationInfo(
            method=InstallMethod.HOMEBREW,
            path="/opt/homebrew/Cellar/deepctl",
            virtual_env=False,
            editable=False,
            python_executable="/opt/homebrew/bin/python3",
        )
        instructions = detector.get_update_instructions(info)
        assert "brew upgrade deepctl" in instructions

    def test_uvx_instructions(self, detector):
        from deepctl_cmd_update.installation import InstallationInfo

        info = InstallationInfo(
            method=InstallMethod.UVX,
            path="/tmp/ephemeral",
            virtual_env=False,
            editable=False,
            python_executable="/tmp/bin/python3",
        )
        instructions = detector.get_update_instructions(info)
        assert "uvx" in instructions
        assert "automatically" in instructions

    def test_pipx_run_instructions(self, detector):
        from deepctl_cmd_update.installation import InstallationInfo

        info = InstallationInfo(
            method=InstallMethod.PIPX_RUN,
            path="/tmp/ephemeral",
            virtual_env=False,
            editable=False,
            python_executable="/tmp/bin/python3",
        )
        instructions = detector.get_update_instructions(info)
        assert "pipx run" in instructions
        assert "automatically" in instructions
