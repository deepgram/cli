"""Unit tests for the MCP proxy command."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from deepctl_cmd_mcp.command import McpCommand
from deepctl_cmd_mcp.models import MCPServerResult


class TestMcpCommand:
    """Test the McpCommand class."""

    def test_command_metadata(self):
        """Test command metadata."""
        command = McpCommand()
        assert command.name == "mcp"
        assert command.requires_auth is False
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

    @patch("deepctl_cmd_mcp.command.asyncio.run")
    @patch("signal.signal")
    def test_handle_invalid_transport(self, mock_signal, mock_asyncio_run, capsys):
        """An unknown transport exits 1 on stderr; nothing runs."""
        auth_manager = MagicMock()
        with pytest.raises(SystemExit) as exc:
            McpCommand().handle(
                MagicMock(), auth_manager, MagicMock(), transport="invalid"
            )
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "supports only the stdio transport, not invalid" in captured.err
        mock_asyncio_run.assert_not_called()
        auth_manager.get_api_key.assert_not_called()

    def test_handle_no_api_key_still_serves(self):
        """No key: the server starts anyway; the key is read per tool call."""
        auth_manager = MagicMock()
        auth_manager.get_api_key.return_value = None
        config = MagicMock()
        config.api_key_untrusted = False
        seen = {}
        with (
            patch("deepctl_cmd_mcp.command.run_proxy", lambda **kw: seen.update(kw)),
            patch("deepctl_cmd_mcp.command.asyncio.run"),
            patch("signal.signal"),
        ):
            result = McpCommand().handle(
                config, auth_manager, MagicMock(), transport="stdio"
            )
        assert result is None
        auth_manager.get_api_key.assert_not_called()
        with pytest.raises(RuntimeError, match="No API key is set"):
            seen["api_key"]()

    def test_key_is_read_on_each_call(self):
        auth_manager = MagicMock()
        auth_manager.get_api_key.side_effect = ["k1", "k2"]
        seen = {}
        with (
            patch("deepctl_cmd_mcp.command.run_proxy", lambda **kw: seen.update(kw)),
            patch("deepctl_cmd_mcp.command.asyncio.run"),
            patch("signal.signal"),
        ):
            McpCommand().handle(MagicMock(), auth_manager, MagicMock())
        assert [seen["api_key"](), seen["api_key"]()] == ["k1", "k2"]

    def test_untrusted_config_key_sends_an_agent_to_a_person(self):
        """The W-PERM-AGENT rule: never tell an agent to run dg login."""
        from deepctl_core import messages as msg

        from deepctl_cmd_mcp.command import key_or_error

        config = MagicMock()
        config.api_key_untrusted = True
        config.config_path = "/x/config.yaml"
        auth_manager = MagicMock()
        auth_manager.get_api_key.return_value = None
        with pytest.raises(RuntimeError) as exc:
            key_or_error(config, auth_manager)
        text = str(exc.value)
        assert text.endswith(msg.W_CFG_UNTRUSTED_FILE_HINT)
        assert "dg login" not in text

    def test_key_lookup_failure_is_a_start_error(self):
        from deepctl_cmd_mcp.command import key_or_error

        auth_manager = MagicMock()
        auth_manager.get_api_key.side_effect = OSError("keyring locked\nmore")
        with pytest.raises(RuntimeError) as exc:
            key_or_error(MagicMock(), auth_manager)
        assert str(exc.value) == (
            "dg mcp can't start the documentation search: keyring locked"
        )

    def test_result_never_reaches_stdout(self, capsys):
        """stdout belongs to the MCP client, even for a failure result."""
        McpCommand().output_result(
            MCPServerResult(status="error", message="x"), MagicMock()
        )
        assert capsys.readouterr().out == ""

    def test_proxy_gets_the_isolated_protocol_stream(self):
        stream = object()
        seen = {}
        with (
            patch("deepctl_cmd_mcp.command.isolate_stdout", return_value=stream),
            patch("deepctl_cmd_mcp.command.run_proxy", lambda **kw: seen.update(kw)),
            patch("deepctl_cmd_mcp.command.asyncio.run"),
            patch("signal.signal"),
        ):
            McpCommand().handle(MagicMock(), MagicMock(), MagicMock())
        assert seen["stdout"] is stream

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
        # A non-default --base-url must be approved in the user config.
        config.allowed_hosts.return_value = ["localhost"]
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

    @pytest.mark.parametrize("transport", ["sse", "SSE"])
    @patch("deepctl_cmd_mcp.command.asyncio.run")
    @patch("signal.signal")
    def test_handle_sse_transport_exits_1(
        self, mock_signal, mock_asyncio_run, capsys, transport
    ):
        """--transport sse exits 1 with a pointer to stdio, before reading
        the key and without starting a listener."""
        auth_manager = MagicMock()
        with pytest.raises(SystemExit) as exc:
            McpCommand().handle(
                MagicMock(),
                auth_manager,
                MagicMock(),
                transport=transport,
                port=8080,
                host="localhost",
            )
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "supports only the stdio transport, not sse" in captured.err
        assert "Use stdio" in captured.err
        mock_asyncio_run.assert_not_called()
        mock_signal.assert_not_called()
        auth_manager.get_api_key.assert_not_called()

    def test_sse_refused_before_the_credential_check(self, capsys):
        """Through execute(): no auth guard (which would send the key to
        verify it) runs for --transport sse."""
        import click
        from deepctl_core import AuthManager

        ctx = click.Context(click.Command("mcp"), obj={"config": MagicMock()})
        with (
            patch.object(AuthManager, "guard", side_effect=AssertionError("no")),
            patch("deepctl_cmd_mcp.command.asyncio.run") as run,
            pytest.raises(SystemExit) as exc,
        ):
            McpCommand().execute(ctx, transport="sse", port=8000, host="127.0.0.1")
        assert exc.value.code == 1
        run.assert_not_called()
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "supports only the stdio transport, not sse" in captured.err

    def test_pre_auth_check_allows_stdio(self):
        assert McpCommand().pre_auth_check(MagicMock(), transport="stdio") is None
        assert McpCommand().pre_auth_check(MagicMock()) is None

    @patch("deepctl_cmd_mcp.command.asyncio.run")
    @patch("signal.signal")
    def test_stdio_ignores_port_and_host(self, mock_signal, mock_asyncio_run):
        """--port/--host still parse (old configs) but only stdio runs."""
        with patch("deepctl_cmd_mcp.command.run_proxy") as run_proxy:
            McpCommand().handle(
                MagicMock(),
                MagicMock(),
                MagicMock(),
                transport="stdio",
                port=8080,
                host="0.0.0.0",
            )
        # The proxy only knows stdio: no transport, host or port reach it.
        assert set(run_proxy.call_args.kwargs) == {
            "base_url",
            "api_key",
            "debug",
            "stdout",
        }

    def test_sse_not_in_examples_or_help(self):
        command = McpCommand()
        assert not any("sse" in e.lower() for e in command.examples)
        assert "sse" not in command.agent_help.lower()

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

    @patch("deepctl_cmd_mcp.command.asyncio.run")
    @patch("signal.signal")
    def test_handle_error_is_reported_without_stdout(
        self, mock_signal, mock_asyncio_run, capsys
    ):
        """A proxy failure reaches telemetry, which writes nothing to stdout."""
        error = RuntimeError("proxy crashed")
        mock_asyncio_run.side_effect = error

        with patch("deepctl_telemetry.report_exception") as mock_report:
            result = McpCommand().handle(
                MagicMock(),
                MagicMock(),
                MagicMock(),
                transport="stdio",
                api_key="test-key",
            )

        mock_report.assert_called_once_with(error)
        assert result.status == "error"
        assert capsys.readouterr().out == ""

    @patch("deepctl_cmd_mcp.command.asyncio.run")
    @patch("signal.signal")
    def test_handle_error_survives_telemetry_failure(
        self, mock_signal, mock_asyncio_run
    ):
        mock_asyncio_run.side_effect = RuntimeError("proxy crashed")
        with patch("deepctl_telemetry.report_exception", side_effect=RuntimeError("x")):
            result = McpCommand().handle(
                MagicMock(),
                MagicMock(),
                MagicMock(),
                transport="stdio",
                api_key="test-key",
            )
        assert result.status == "error"

    def test_handle_api_key_from_auth_manager(self):
        """Test API key fallback to auth manager."""
        command = McpCommand()
        config = MagicMock()
        auth_manager = MagicMock()
        auth_manager.get_api_key.return_value = "stored-key"
        client = MagicMock()

        with (
            patch("deepctl_cmd_mcp.command.asyncio.run") as mock_run,
            patch("signal.signal"),
        ):
            mock_run.return_value = None

            result = command.handle(
                config,
                auth_manager,
                client,
                transport="stdio",
            )

        assert result is None
        mock_run.assert_called_once()
        auth_manager.get_api_key.assert_not_called()  # read per tool call
