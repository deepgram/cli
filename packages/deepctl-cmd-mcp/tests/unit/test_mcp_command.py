"""Unit tests for the MCP command."""

import os
import signal
import sys
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from deepctl_cmd_mcp.command import McpCommand, create_mcp_server
from deepctl_cmd_mcp.models import MCPServerResult, TransportType


class TestMcpCommand:
    """Test the McpCommand class."""

    def test_command_metadata(self):
        """Test command metadata."""
        command = McpCommand()
        assert command.name == "mcp"
        assert command.requires_auth is False
        assert command.requires_project is False
        assert command.ci_friendly is True
        # Test new attributes from __init__
        assert hasattr(command, '_shutdown_requested')
        assert hasattr(command, '_original_sigint_handler')

    def test_get_arguments(self):
        """Test command arguments."""
        command = McpCommand()
        args = command.get_arguments()

        # Check we have all expected arguments
        arg_names = {arg["names"][0] for arg in args}
        expected_args = {
            "--transport",
            "--port",
            "--host",
            "--api-key",
            "--gnosis-url",
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

    @patch("deepctl_cmd_mcp.command.create_mcp_server")
    @patch("signal.signal")
    def test_handle_stdio_transport(self, mock_signal, mock_create_server):
        """Test handling stdio transport."""
        command = McpCommand()
        config = MagicMock()
        auth_manager = MagicMock()
        client = MagicMock()

        # Mock the server instance
        mock_server = MagicMock()
        mock_create_server.return_value = mock_server
        mock_server.run.return_value = None

        with patch.object(auth_manager, "get_api_key", return_value="stored"):
            result = command.handle(
                config,
                auth_manager,
                client,
                transport="stdio",
                api_key="test-key",
            )

        # Check environment was set
        assert os.environ.get("DEEPGRAM_API_KEY") == "test-key"

        # Check server was created and run
        mock_create_server.assert_called_once()
        mock_server.run.assert_called_once_with()

        assert isinstance(result, MCPServerResult)
        assert result.status == "success"
        assert result.transport == TransportType.STDIO

    @patch("deepctl_cmd_mcp.command.create_mcp_server")
    @patch("signal.signal")
    def test_handle_sse_transport(self, mock_signal, mock_create_server):
        """Test handling SSE transport."""
        command = McpCommand()
        config = MagicMock()
        auth_manager = MagicMock()
        client = MagicMock()

        # Mock the server instance
        mock_server = MagicMock()
        mock_create_server.return_value = mock_server
        mock_server.run.return_value = None

        result = command.handle(
            config,
            auth_manager,
            client,
            transport="sse",
            port=8080,
            host="localhost",
        )

        # Check server was created and run with correct params
        mock_create_server.assert_called_once()
        mock_server.run.assert_called_once_with(
            transport="sse", host="localhost", port=8080
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

        # Test signal handling
        command._handle_shutdown(None, None)

        # Check that shutdown was requested
        assert command._shutdown_requested is True

        # Check that os._exit was called
        mock_exit.assert_called_once_with(0)

    @patch("os._exit")
    def test_handle_shutdown_signal_once(self, mock_exit):
        """Test that shutdown signal handler only runs once."""
        command = McpCommand()

        # Call handler twice
        command._handle_shutdown(None, None)
        command._handle_shutdown(None, None)

        # Should only exit once
        mock_exit.assert_called_once_with(0)

    @patch("deepctl_cmd_mcp.command.create_mcp_server")
    @patch("signal.signal")
    def test_handle_keyboard_interrupt(self, mock_signal, mock_create_server):
        """Test handling KeyboardInterrupt."""
        command = McpCommand()
        config = MagicMock()
        auth_manager = MagicMock()
        client = MagicMock()

        # Mock the server to raise KeyboardInterrupt
        mock_server = MagicMock()
        mock_create_server.return_value = mock_server
        mock_server.run.side_effect = KeyboardInterrupt()

        result = command.handle(
            config,
            auth_manager,
            client,
            transport="stdio",
        )

        assert isinstance(result, MCPServerResult)
        assert result.status == "cancelled"
        assert result.message == "MCP server stopped by user"

    @patch("deepctl_cmd_mcp.command.create_mcp_server")
    @patch("signal.signal")
    def test_handle_error(self, mock_signal, mock_create_server):
        """Test handling server error."""
        command = McpCommand()
        config = MagicMock()
        auth_manager = MagicMock()
        client = MagicMock()

        # Mock the server to raise an exception
        mock_server = MagicMock()
        mock_create_server.return_value = mock_server
        mock_server.run.side_effect = Exception("Test error")

        result = command.handle(
            config,
            auth_manager,
            client,
            transport="stdio",
        )

        assert isinstance(result, MCPServerResult)
        assert result.status == "error"
        assert "Test error" in result.message


class TestCreateMCPServer:
    """Test the create_mcp_server function."""

    @patch.dict(os.environ, {"DEEPGRAM_API_KEY": "test-key"})
    def test_create_server(self):
        """Test creating MCP server."""
        server = create_mcp_server()

        # Check server name
        assert server.name == "Deepgram AI Assistant"

        # Check tools are registered
        tools = server._tool_manager.list_tools()
        tool_names = {tool.name for tool in tools}
        expected_tools = {
            "ask_question",
            "check_api_spec",
            "get_code_example",
            "search_docs",
        }
        assert tool_names == expected_tools

    @patch.dict(os.environ, {"DEEPGRAM_API_KEY": "test-key"})
    @patch("httpx.AsyncClient.post")
    @pytest.mark.asyncio
    async def test_ask_question_tool(self, mock_post):
        """Test ask_question tool functionality."""
        # Create server
        server = create_mcp_server()

        # Mock Gnosis response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "id": "test-id",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "gpt-4",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Deepgram is a speech recognition platform.",
                    }
                }
            ],
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        # Get the tool function
        tools = server._tool_manager.list_tools()
        ask_tool = next(t for t in tools if t.name == "ask_question")

        # Create a mock context
        mock_ctx = MagicMock()
        mock_ctx.info = AsyncMock()

        # Call the tool function directly
        result = await ask_tool.fn(question="What is Deepgram?", ctx=mock_ctx)

        assert result == "Deepgram is a speech recognition platform."
        mock_post.assert_called_once()
