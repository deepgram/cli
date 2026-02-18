"""Unit tests for plugin installation strategies."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest
from deepctl_cmd_plugin.models import PluginInstallOptions
from deepctl_cmd_plugin.strategies import (
    DevelopmentStrategy,
    EphemeralStrategy,
    IsolatedVenvStrategy,
    PipStrategy,
    PipxStrategy,
    PluginInstallStrategy,
    UvToolStrategy,
    get_strategy,
)
from deepctl_cmd_update.installation import InstallMethod


class TestPipStrategy:
    """Test PipStrategy install/uninstall."""

    @pytest.mark.unit
    @patch("deepctl_cmd_plugin.strategies.subprocess.run")
    def test_install_basic(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Successfully installed"
        )
        strategy = PipStrategy()
        options = PluginInstallOptions(package="test-plugin")

        ok, out = strategy.install(options)
        assert ok is True
        cmd = mock_run.call_args[0][0]
        assert "pip" in " ".join(cmd)
        assert "test-plugin" in cmd

    @pytest.mark.unit
    @patch("deepctl_cmd_plugin.strategies.subprocess.run")
    def test_install_with_version(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="OK")
        strategy = PipStrategy()
        options = PluginInstallOptions(package="test-plugin", version="2.0.0")

        ok, _ = strategy.install(options)
        assert ok is True
        cmd = mock_run.call_args[0][0]
        assert "test-plugin==2.0.0" in cmd

    @pytest.mark.unit
    @patch("deepctl_cmd_plugin.strategies.subprocess.run")
    def test_install_with_options(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="OK")
        strategy = PipStrategy()
        options = PluginInstallOptions(
            package="test-plugin",
            upgrade=True,
            pre=True,
            force_reinstall=True,
            index_url="https://custom.pypi.org/simple",
        )

        ok, _ = strategy.install(options)
        assert ok is True
        cmd = mock_run.call_args[0][0]
        assert "--upgrade" in cmd
        assert "--pre" in cmd
        assert "--force-reinstall" in cmd
        assert "--index-url" in cmd
        assert "https://custom.pypi.org/simple" in cmd

    @pytest.mark.unit
    @patch("deepctl_cmd_plugin.strategies.subprocess.run")
    def test_install_git_url(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="OK")
        strategy = PipStrategy()
        options = PluginInstallOptions(
            package="test-plugin",
            git_url="git+https://github.com/user/repo.git",
        )

        ok, _ = strategy.install(options)
        assert ok is True
        cmd = mock_run.call_args[0][0]
        assert "git+https://github.com/user/repo.git" in cmd

    @pytest.mark.unit
    @patch("deepctl_cmd_plugin.strategies.subprocess.run")
    def test_uninstall(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="Removed")
        strategy = PipStrategy()

        ok, _ = strategy.uninstall("test-plugin")
        assert ok is True
        cmd = mock_run.call_args[0][0]
        assert "uninstall" in cmd
        assert "test-plugin" in cmd

    @pytest.mark.unit
    @patch("deepctl_cmd_plugin.strategies.subprocess.run")
    def test_install_failure(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "pip", stderr="not found"
        )
        strategy = PipStrategy()
        options = PluginInstallOptions(package="nonexistent")

        ok, err = strategy.install(options)
        assert ok is False
        assert "not found" in err


class TestPipxStrategy:
    """Test PipxStrategy install/uninstall."""

    @pytest.mark.unit
    @patch("deepctl_cmd_plugin.strategies.subprocess.run")
    def test_install_inject(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="injected")
        strategy = PipxStrategy()
        options = PluginInstallOptions(package="test-plugin")

        ok, _ = strategy.install(options)
        assert ok is True
        cmd = mock_run.call_args[0][0]
        assert "pipx" in cmd
        assert "inject" in cmd
        assert "deepctl" in cmd

    @pytest.mark.unit
    @patch("deepctl_cmd_plugin.strategies.subprocess.run")
    def test_install_fallback_runpip(self, mock_run: MagicMock) -> None:
        # First call (inject) fails, second (runpip) succeeds
        mock_run.side_effect = [
            subprocess.CalledProcessError(1, "pipx", stderr="unknown"),
            MagicMock(returncode=0, stdout="installed"),
        ]
        strategy = PipxStrategy()
        options = PluginInstallOptions(package="test-plugin")

        ok, _ = strategy.install(options)
        assert ok is True
        # Second call should use runpip
        cmd = mock_run.call_args_list[1][0][0]
        assert "runpip" in cmd

    @pytest.mark.unit
    @patch("deepctl_cmd_plugin.strategies.subprocess.run")
    def test_uninstall(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="removed")
        strategy = PipxStrategy()

        ok, _ = strategy.uninstall("test-plugin")
        assert ok is True


class TestUvToolStrategy:
    """Test UvToolStrategy install/uninstall."""

    @pytest.mark.unit
    @patch("deepctl_cmd_plugin.strategies.subprocess.run")
    def test_install(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="OK")
        strategy = UvToolStrategy()
        options = PluginInstallOptions(package="test-plugin")

        ok, _ = strategy.install(options)
        assert ok is True
        cmd = mock_run.call_args[0][0]
        assert "uv" in cmd
        assert "tool" in cmd
        assert "--with" in cmd
        assert "test-plugin" in cmd


class TestIsolatedVenvStrategy:
    """Test IsolatedVenvStrategy install/uninstall."""

    @pytest.mark.unit
    @patch("deepctl_cmd_plugin.strategies.subprocess.run")
    def test_install(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="OK")
        strategy = IsolatedVenvStrategy("/path/to/venv/python")
        options = PluginInstallOptions(package="test-plugin")

        ok, _ = strategy.install(options)
        assert ok is True
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/path/to/venv/python"
        assert "pip" in " ".join(cmd)

    @pytest.mark.unit
    @patch("deepctl_cmd_plugin.strategies.subprocess.run")
    def test_uninstall(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="OK")
        strategy = IsolatedVenvStrategy("/path/to/venv/python")

        ok, _ = strategy.uninstall("test-plugin")
        assert ok is True
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/path/to/venv/python"


class TestDevelopmentStrategy:
    """Test DevelopmentStrategy install/uninstall."""

    @pytest.mark.unit
    @patch("deepctl_cmd_plugin.strategies.subprocess.run")
    def test_install(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="OK")
        strategy = DevelopmentStrategy()
        options = PluginInstallOptions(package="test-plugin", editable=True)

        ok, _ = strategy.install(options)
        assert ok is True
        cmd = mock_run.call_args[0][0]
        assert "--editable" in cmd

    @pytest.mark.unit
    @patch("deepctl_cmd_plugin.strategies.subprocess.run")
    def test_uninstall(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="OK")
        strategy = DevelopmentStrategy()

        ok, _ = strategy.uninstall("test-plugin")
        assert ok is True


class TestEphemeralStrategy:
    """Test EphemeralStrategy warn-and-bail behavior."""

    @pytest.mark.unit
    def test_install_warns_uvx(self) -> None:
        strategy = EphemeralStrategy(InstallMethod.UVX)
        options = PluginInstallOptions(package="test-plugin")

        ok, msg = strategy.install(options)
        assert ok is False
        assert "ephemeral" in msg.lower()
        assert "uv tool install" in msg

    @pytest.mark.unit
    def test_install_warns_pipx_run(self) -> None:
        strategy = EphemeralStrategy(InstallMethod.PIPX_RUN)
        options = PluginInstallOptions(package="test-plugin")

        ok, msg = strategy.install(options)
        assert ok is False
        assert "pipx install" in msg

    @pytest.mark.unit
    def test_uninstall_warns(self) -> None:
        strategy = EphemeralStrategy(InstallMethod.UVX)

        ok, msg = strategy.uninstall("test-plugin")
        assert ok is False
        assert "ephemeral" in msg.lower()


class TestGetStrategy:
    """Test the get_strategy factory function."""

    @pytest.mark.unit
    def test_pip_returns_pip_strategy(self) -> None:
        assert isinstance(get_strategy(InstallMethod.PIP), PipStrategy)

    @pytest.mark.unit
    def test_uv_returns_pip_strategy(self) -> None:
        assert isinstance(get_strategy(InstallMethod.UV), PipStrategy)

    @pytest.mark.unit
    def test_pipx_returns_pipx_strategy(self) -> None:
        assert isinstance(get_strategy(InstallMethod.PIPX), PipxStrategy)

    @pytest.mark.unit
    def test_uv_tool_returns_uv_tool_strategy(self) -> None:
        assert isinstance(
            get_strategy(InstallMethod.UV_TOOL), UvToolStrategy
        )

    @pytest.mark.unit
    def test_development_returns_development_strategy(self) -> None:
        assert isinstance(
            get_strategy(InstallMethod.DEVELOPMENT), DevelopmentStrategy
        )

    @pytest.mark.unit
    def test_uvx_returns_ephemeral_strategy(self) -> None:
        assert isinstance(
            get_strategy(InstallMethod.UVX), EphemeralStrategy
        )

    @pytest.mark.unit
    def test_pipx_run_returns_ephemeral_strategy(self) -> None:
        assert isinstance(
            get_strategy(InstallMethod.PIPX_RUN), EphemeralStrategy
        )

    @pytest.mark.unit
    def test_system_returns_isolated_venv(self) -> None:
        strategy = get_strategy(
            InstallMethod.SYSTEM, venv_python="/path/to/python"
        )
        assert isinstance(strategy, IsolatedVenvStrategy)

    @pytest.mark.unit
    def test_homebrew_returns_isolated_venv(self) -> None:
        strategy = get_strategy(
            InstallMethod.HOMEBREW, venv_python="/path/to/python"
        )
        assert isinstance(strategy, IsolatedVenvStrategy)

    @pytest.mark.unit
    def test_unknown_returns_isolated_venv(self) -> None:
        strategy = get_strategy(
            InstallMethod.UNKNOWN, venv_python="/path/to/python"
        )
        assert isinstance(strategy, IsolatedVenvStrategy)

    @pytest.mark.unit
    def test_system_without_venv_python_raises(self) -> None:
        with pytest.raises(ValueError, match="venv_python is required"):
            get_strategy(InstallMethod.SYSTEM)
