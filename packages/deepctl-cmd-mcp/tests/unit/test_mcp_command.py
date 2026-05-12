"""Unit tests for the MCP proxy command."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from deepctl_cmd_mcp.command import McpCommand
from deepctl_cmd_mcp.models import MCPServerResult, TransportType


class TestMcpCommand:
    """Test the McpCommand class."""

    def test_command_metadata(self):
        """Test command metadata."""
        command = McpCommand()
        assert command.name == "mcp"
        assert command.requires_auth is True
        assert command.requires_project is False
        assert command.ci_friendly is True
        assert hasattr(command, "_shutdown_requested")
        assert hasattr(command, "_original_sigint_handler")

    def test_get_arguments(self):
        """Test command arguments."""
        command = McpCommand()
        args = command.get_arguments()

        arg_names = {arg["names"][0] for arg in args}
        expected_args = {
            "--transport",
            "--port",
            "--host",
            "--base-url",
            "--debug",
        }
        assert expected_args.issubset(arg_names)

    def test_handle_invalid_transport(self):
        """Test handling invalid transport type."""
        command = McpCommand()
        config = MagicMock()
        auth_manager = MagicMock()
        client = MagicMock()

        result = command.handle(
            config,
            auth_manager,
            client,
            transport="invalid",
        )

        assert isinstance(result, MCPServerResult)
        assert result.status == "error"
        assert "Invalid transport type" in result.message

    def test_handle_no_api_key(self):
        """Test handling missing API key (guard runs before handle, but assert catches it)."""
        command = McpCommand()
        config = MagicMock()
        auth_manager = MagicMock()
        auth_manager.get_api_key.return_value = None
        client = MagicMock()

        import pytest

        with pytest.raises(AssertionError):
            command.handle(
                config,
                auth_manager,
                client,
                transport="stdio",
            )

    @patch("deepctl_cmd_mcp.command.asyncio.run")
    @patch("signal.signal")
    def test_handle_stdio_transport_returns_none(self, mock_signal, mock_asyncio_run):
        """stdio mode returns None so the host's owned stdout is never written to.

        Returning a result would invite ``output_result`` to print to stdout,
        which the MCP host has typically closed. See DX-CLI-4/DX-CLI-5 in
        Sentry for the regression we're guarding against.
        """
        command = McpCommand()
        config = MagicMock()
        auth_manager = MagicMock()
        client = MagicMock()

        mock_asyncio_run.return_value = None

        result = command.handle(
            config,
            auth_manager,
            client,
            transport="stdio",
            api_key="test-key",
            base_url="http://localhost:8080",
        )

        mock_asyncio_run.assert_called_once()
        assert result is None

    @patch("deepctl_cmd_mcp.command.asyncio.run")
    @patch("signal.signal")
    def test_handle_sse_transport(self, mock_signal, mock_asyncio_run):
        """Test handling SSE transport."""
        command = McpCommand()
        config = MagicMock()
        auth_manager = MagicMock()
        client = MagicMock()

        mock_asyncio_run.return_value = None

        result = command.handle(
            config,
            auth_manager,
            client,
            transport="sse",
            port=8080,
            host="localhost",
            api_key="test-key",
        )

        assert isinstance(result, MCPServerResult)
        assert result.status == "success"
        assert result.transport == TransportType.SSE
        assert result.port == 8080
        assert result.host == "localhost"

    @patch("os._exit")
    def test_handle_shutdown_signal(self, mock_exit):
        """Test that shutdown signal handler works correctly."""
        command = McpCommand()
        command._handle_shutdown(2, None)
        assert command._shutdown_requested is True
        mock_exit.assert_called_once_with(0)

    @patch("os._exit")
    def test_handle_shutdown_signal_once(self, mock_exit):
        """Test that shutdown signal handler only runs once."""
        command = McpCommand()
        command._handle_shutdown(2, None)
        command._handle_shutdown(2, None)
        mock_exit.assert_called_once_with(0)

    @patch("deepctl_cmd_mcp.command.asyncio.run")
    @patch("signal.signal")
    def test_handle_keyboard_interrupt(self, mock_signal, mock_asyncio_run):
        """Test handling KeyboardInterrupt."""
        command = McpCommand()
        config = MagicMock()
        auth_manager = MagicMock()
        client = MagicMock()

        mock_asyncio_run.side_effect = KeyboardInterrupt()

        result = command.handle(
            config,
            auth_manager,
            client,
            transport="stdio",
            api_key="test-key",
        )

        assert isinstance(result, MCPServerResult)
        assert result.status == "cancelled"

    @patch("deepctl_cmd_mcp.command.asyncio.run")
    @patch("signal.signal")
    def test_handle_error(self, mock_signal, mock_asyncio_run):
        """Test handling proxy error."""
        command = McpCommand()
        config = MagicMock()
        auth_manager = MagicMock()
        client = MagicMock()

        mock_asyncio_run.side_effect = Exception("Connection refused")

        result = command.handle(
            config,
            auth_manager,
            client,
            transport="stdio",
            api_key="test-key",
        )

        assert isinstance(result, MCPServerResult)
        assert result.status == "error"
        assert "Connection refused" in result.message

    def test_handle_api_key_from_auth_manager(self):
        """Test API key fallback to auth manager."""
        command = McpCommand()
        config = MagicMock()
        auth_manager = MagicMock()
        auth_manager.get_api_key.return_value = "stored-key"
        client = MagicMock()

        with patch("deepctl_cmd_mcp.command.asyncio.run") as mock_run, \
             patch("signal.signal"):
            mock_run.return_value = None

            result = command.handle(
                config,
                auth_manager,
                client,
                transport="stdio",
            )

        assert result is None
        mock_run.assert_called_once()
