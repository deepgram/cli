"""Unit tests for installation detection."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from deepctl_cmd_update.installation import (
    InstallationDetector,
    InstallMethod,
)

unix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="Unix-specific path test"
)


class TestInstallMethod:
    """Test InstallMethod enum values."""

    def test_all_methods_defined(self):
        """Verify all expected methods exist."""
        expected = {
            "pip", "pipx", "uv", "homebrew", "uv_tool",
            "uvx", "pipx_run", "npm", "system", "development", "unknown",
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
        assert cmd == ["brew", "upgrade", "deepgram"]

    def test_returns_list_for_uv_tool(self, detector):
        cmd = detector.get_update_command(InstallMethod.UV_TOOL)
        assert isinstance(cmd, list)
        assert cmd == ["uv", "tool", "install", "deepctl@latest"]

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


@pytest.fixture
def layout(tmp_path, monkeypatch):
    """A home directory with no tool settings, and a way to set sys.prefix."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in (
        "UV_CACHE_DIR",
        "UV_TOOL_DIR",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "PIPX_HOME",
        "PIPX_BIN_DIR",
        "DEEPCTL_LAUNCHER",
        "UV_INTERNAL__PARENT_INTERPRETER",
        "UV_PROJECT",
    ):
        monkeypatch.delenv(name, raising=False)
    main = sys.modules.get("deepctl.main")
    if main is not None:
        monkeypatch.setattr(main, "LAUNCHER_CAPTURED", False, raising=False)

    def set_prefix(path):
        monkeypatch.setattr(sys, "prefix", str(path))
        return InstallationDetector()

    set_prefix.home = home
    return set_prefix


@unix_only
class TestHomebrewDetection:
    """Homebrew means the deepgram formula's own Cellar prefix."""

    @pytest.mark.parametrize(
        "prefix",
        [
            "/opt/homebrew/Cellar/deepgram/0.5.0/libexec",
            "/usr/local/Cellar/deepgram/0.5.0/libexec",
            "/home/linuxbrew/.linuxbrew/Cellar/deepgram/0.5.0/libexec",
        ],
    )
    def test_formula_prefix(self, layout, prefix):
        assert layout(prefix)._is_homebrew_install() is True

    @pytest.mark.parametrize(
        "prefix",
        [
            "/opt/homebrew/Cellar/python@3.12/3.12.7/Frameworks/Python.framework",
            "/home/linuxbrew/.linuxbrew/Cellar/python@3.12/3.12.7",
            "/usr",
        ],
    )
    def test_homebrew_python_alone_is_not_brew(self, layout, prefix):
        assert layout(prefix)._is_homebrew_install() is False

    def test_pipx_on_homebrew_python(self, layout, monkeypatch):
        """A pipx venv whose interpreter resolves into the Cellar is pipx."""
        prefix = layout.home / ".local" / "share" / "pipx" / "venvs" / "deepctl"
        monkeypatch.setattr(
            sys, "executable", "/home/linuxbrew/.linuxbrew/Cellar/python@3.12/bin/python3"
        )
        detector = layout(prefix)
        assert detector._is_homebrew_install() is False
        assert detector._detect_method(prefix, True, False) == InstallMethod.PIPX

    def test_uv_tool_on_homebrew_python(self, layout, monkeypatch):
        prefix = layout.home / ".local" / "share" / "uv" / "tools" / "deepctl"
        monkeypatch.setattr(
            sys, "executable", "/home/linuxbrew/.linuxbrew/Cellar/python@3.12/bin/python3"
        )
        detector = layout(prefix)
        assert detector._detect_method(prefix, True, False) == InstallMethod.UV_TOOL

    def test_formula_through_opt_link(self, layout, tmp_path):
        cellar = tmp_path / "brew" / "Cellar" / "deepgram" / "0.5.0" / "libexec"
        cellar.mkdir(parents=True)
        opt = tmp_path / "brew" / "opt"
        opt.mkdir()
        (opt / "deepgram").symlink_to(cellar.parent)
        detector = layout(opt / "deepgram" / "libexec")
        assert detector._detect_method(None, True, False) == InstallMethod.HOMEBREW


@unix_only
class TestUvToolDetection:
    """Test uv tool installation detection."""

    def test_custom_uv_tool_dir(self, layout, monkeypatch):
        monkeypatch.setenv("UV_TOOL_DIR", "/custom/uv/tools")
        assert layout("/custom/uv/tools/deepctl")._is_uv_tool_install() is True

    def test_default_uv_tool_path(self, layout, monkeypatch):
        """Detect uv tool at default location via sys.prefix (symlinked exe)."""
        monkeypatch.setattr(
            sys, "executable", "/x/.local/share/uv/python/cpython-3.11/bin/python3"
        )
        prefix = layout.home / ".local" / "share" / "uv" / "tools" / "deepctl"
        assert layout(prefix)._is_uv_tool_install() is True

    def test_xdg_data_home(self, layout, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        prefix = tmp_path / "data" / "uv" / "tools" / "deepctl"
        assert layout(prefix)._is_uv_tool_install() is True

    def test_tools_dir_itself_is_not_a_tool(self, layout):
        prefix = layout.home / ".local" / "share" / "uv" / "tools"
        assert layout(prefix)._is_uv_tool_install() is False


@unix_only
class TestPipxDetection:
    def test_pipx_home_env(self, layout, monkeypatch, tmp_path):
        monkeypatch.setenv("PIPX_HOME", str(tmp_path / "px"))
        assert layout(tmp_path / "px" / "venvs" / "deepctl")._is_pipx_install()

    @pytest.mark.parametrize(
        "parts", [(".local", "pipx"), (".local", "share", "pipx")]
    )
    def test_default_homes(self, layout, parts):
        prefix = layout.home.joinpath(*parts, "venvs", "deepctl")
        assert layout(prefix)._is_pipx_install() is True

    def test_pipx_home_set_for_another_install(self, layout, monkeypatch, tmp_path):
        """PIPX_HOME in the environment doesn't make a venv a pipx install."""
        monkeypatch.setenv("PIPX_HOME", str(tmp_path / "px"))
        monkeypatch.setenv("PIPX_BIN_DIR", str(tmp_path / "bin"))
        detector = layout(tmp_path / "project" / ".venv")
        assert detector._is_pipx_install() is False


@unix_only
class TestOneshotDetection:
    """Test one-shot execution detection (uvx / pipx run)."""

    def test_uvx_default_cache(self, layout):
        prefix = layout.home / ".cache" / "uv" / "archive-v0" / "AbC123"
        assert layout(prefix)._detect_oneshot_execution() == InstallMethod.UVX

    def test_uvx_with_uv_cache_dir(self, layout, monkeypatch, tmp_path):
        monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uvc"))
        prefix = tmp_path / "uvc" / "archive-v0" / "AbC123"
        assert layout(prefix)._detect_oneshot_execution() == InstallMethod.UVX

    def test_uv_cache_outside_archive_is_not_uvx(self, layout):
        prefix = layout.home / ".cache" / "uv" / "builds-v0" / "x"
        assert layout(prefix)._detect_oneshot_execution() is None

    def test_parent_interpreter_env_alone_is_not_uvx(self, layout, monkeypatch, tmp_path):
        """uv run sets this too, for an ordinary project venv."""
        monkeypatch.setenv("UV_INTERNAL__PARENT_INTERPRETER", "/usr/bin/python3")
        assert layout(tmp_path / "project" / ".venv")._detect_oneshot_execution() is None

    def test_pipx_run_detected(self, layout, monkeypatch, tmp_path):
        monkeypatch.setenv("PIPX_HOME", str(tmp_path / "px"))
        prefix = tmp_path / "px" / ".cache" / "0123abcd"
        assert layout(prefix)._detect_oneshot_execution() == InstallMethod.PIPX_RUN

    def test_no_oneshot(self, layout, tmp_path):
        assert layout(tmp_path / "venv")._detect_oneshot_execution() is None


@unix_only
class TestNpmLauncher:
    @pytest.mark.parametrize("value", ["npm", "npx"])
    def test_env(self, layout, monkeypatch, tmp_path, value):
        monkeypatch.setenv("DEEPCTL_LAUNCHER", value)
        detector = layout(tmp_path / "cache" / "venv")
        assert detector._detect_method(None, True, False) == InstallMethod.NPM

    def test_captured_by_main(self, layout, monkeypatch, tmp_path):
        """deepctl.main removes the variable at startup and keeps its value."""
        import deepctl.main as main

        monkeypatch.setattr(main, "LAUNCHER_CAPTURED", True)
        monkeypatch.setattr(main, "LAUNCHER", "npm")
        detector = layout(tmp_path / "cache" / "venv")
        assert detector._detect_method(None, True, False) == InstallMethod.NPM

    def test_unknown_value_ignored(self, layout, monkeypatch, tmp_path):
        monkeypatch.setenv("DEEPCTL_LAUNCHER", "/usr/local/bin/dg")
        detector = layout(tmp_path / "venv")
        assert detector._detect_method(None, True, False) != InstallMethod.NPM

    def test_npm_prints_the_command(self, layout, monkeypatch, tmp_path):
        from deepctl_cmd_update.installation import InstallationInfo

        monkeypatch.setenv("DEEPCTL_LAUNCHER", "npm")
        detector = layout(tmp_path / "venv")
        assert detector.get_update_command(InstallMethod.NPM) is None
        info = InstallationInfo(
            method=InstallMethod.NPM,
            path="/x",
            virtual_env=True,
            editable=False,
            python_executable="/x/bin/python3",
        )
        assert detector.get_update_instructions(info) == (
            "Run: npm i -g @deepgram/cli@latest"
        )
        monkeypatch.setenv("DEEPCTL_LAUNCHER", "npx")
        assert "npx" in detector.get_update_instructions(info)


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
        assert instructions == (
            "Please update using Homebrew: brew update && brew upgrade deepgram"
        )

    @patch("deepctl_cmd_update.installation.sys")
    def test_system_install_on_macos_names_brew_formula(self, mock_sys, detector):
        from deepctl_cmd_update.installation import InstallationInfo

        mock_sys.platform = "darwin"
        info = InstallationInfo(
            method=InstallMethod.SYSTEM,
            path="/usr/local/lib/python3.12/site-packages",
            virtual_env=False,
            editable=False,
            python_executable="/usr/local/bin/python3",
        )
        instructions = detector.get_update_instructions(info)
        # The Homebrew formula is `deepgram`; `deepctl` is not a formula.
        assert instructions == (
            "Please update using Homebrew: brew update && brew upgrade deepgram"
        )

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
        assert "uv tool install deepctl@latest" in instructions

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
