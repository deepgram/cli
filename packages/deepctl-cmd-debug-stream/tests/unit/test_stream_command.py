"""Tests for stream debug command."""

from unittest.mock import Mock, patch

import pytest
from deepctl_cmd_debug_stream.command import StreamCommand, _find_available_port
from deepctl_cmd_debug_stream.models import StreamDebugResult
from deepctl_core import AuthManager, Config, DeepgramClient


class TestStreamCommand:
    """Test cases for StreamCommand."""

    @pytest.fixture
    def command(self):
        """Create a StreamCommand instance."""
        return StreamCommand()

    @pytest.fixture
    def mock_config(self):
        """Create a mock config."""
        return Mock(spec=Config)

    @pytest.fixture
    def mock_auth_manager(self):
        """Create a mock auth manager."""
        manager = Mock(spec=AuthManager)
        manager.get_api_key.return_value = "test-api-key"
        return manager

    @pytest.fixture
    def mock_client(self):
        """Create a mock Deepgram client."""
        return Mock(spec=DeepgramClient)

    def test_command_properties(self, command):
        """Test command basic properties."""
        assert command.name == "stream"
        assert command.requires_auth is True
        assert command.requires_project is False
        assert command.ci_friendly is False

    def test_get_arguments(self, command):
        """Test command arguments configuration."""
        args = command.get_arguments()
        arg_names = []
        for arg in args:
            arg_names.extend(arg["names"])

        assert "--port" in arg_names
        assert "-p" in arg_names
        assert "--upstream" in arg_names
        assert "--timeout" in arg_names
        assert "--sample-size" in arg_names
        assert "--no-analysis" in arg_names
        assert "--verbose" in arg_names

    def test_handle_no_api_key(
        self, command, mock_config, mock_client
    ):
        """Test handling when no API key is available."""
        mock_auth = Mock(spec=AuthManager)
        mock_auth.get_api_key.return_value = None

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth,
            client=mock_client,
        )

        assert isinstance(result, StreamDebugResult)
        assert result.status == "error"
        assert "No API key found" in result.message

    @patch("deepctl_cmd_debug_stream.command._find_available_port")
    def test_handle_no_available_port(
        self, mock_find_port, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test handling when no ports are available."""
        mock_find_port.return_value = None

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
        )

        assert isinstance(result, StreamDebugResult)
        assert result.status == "error"
        assert "No available ports" in result.message


class TestFindAvailablePort:
    """Test cases for port finding."""

    def test_find_available_port(self):
        """Test finding an available port."""
        port = _find_available_port(start=49152, end=49160)
        assert port is not None
        assert 49152 <= port <= 49160

    @patch("socket.socket")
    def test_find_available_port_all_busy(self, mock_socket_class):
        """Test when all ports are busy."""
        mock_socket = Mock()
        mock_socket.__enter__ = Mock(return_value=mock_socket)
        mock_socket.__exit__ = Mock(return_value=False)
        mock_socket.bind.side_effect = OSError("Address already in use")
        mock_socket_class.return_value = mock_socket

        port = _find_available_port(start=3000, end=3002)
        assert port is None
