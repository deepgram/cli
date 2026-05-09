"""Unit tests for browser debug command."""

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from aiohttp import WSMsgType
from deepctl_cmd_debug_browser import BrowserCommand, BrowserDebugResult


class TestBrowserCommand:
    """Test cases for BrowserCommand."""

    def test_command_properties(self):
        """Test basic command properties."""
        cmd = BrowserCommand()
        assert cmd.name == "browser"
        assert cmd.requires_auth is False
        assert cmd.requires_project is False
        assert cmd.ci_friendly is False  # Opens browser

    def test_get_arguments(self):
        """Test command arguments configuration."""
        cmd = BrowserCommand()
        args = cmd.get_arguments()

        # Check we have the expected arguments
        arg_names = []
        for arg in args:
            arg_names.extend(arg["names"])

        assert "--port" in arg_names
        assert "-p" in arg_names
        assert "--no-browser" in arg_names
        assert "--timeout" in arg_names

    def test_find_available_port(self):
        """Test port finding functionality."""
        cmd = BrowserCommand()

        # Should find a port starting from 3000
        port = cmd.find_available_port(3000)
        assert isinstance(port, int)
        assert port >= 3000
        assert port < 3100  # Should find within 100 ports

    @patch("webbrowser.open")
    @patch("builtins.input", return_value="")
    def test_handle_with_browser(self, mock_input, mock_browser_open):
        """Test command execution with browser opening."""
        cmd = BrowserCommand()

        # Mock the async run_servers method
        with patch.object(
            cmd, "run_servers", new_callable=AsyncMock
        ) as mock_run_servers:
            mock_run_servers.return_value = {
                "completed": True,
                "timed_out": False,
                "duration": 5.0,
            }

            # Set up capabilities data with proper BrowserCapability instances
            from deepctl_cmd_debug_browser.models import BrowserCapability

            cmd.capabilities_data = {
                "web_audio_api": BrowserCapability(
                    name="Web Audio API", supported=True, details="Supported"
                ),
                "audio_context": BrowserCapability(
                    name="AudioContext", supported=True, details="Supported"
                ),
                "audio_worklet": BrowserCapability(
                    name="AudioWorklet", supported=True, details="Supported"
                ),
                "websocket_api": BrowserCapability(
                    name="WebSocket API", supported=True, details="Supported"
                ),
                "fetch_api": BrowserCapability(
                    name="Fetch API", supported=True, details="Supported"
                ),
                "es6_features": BrowserCapability(
                    name="ES6+ Features", supported=True, details="Supported"
                ),
                "dom_apis": BrowserCapability(
                    name="DOM APIs", supported=True, details="Supported"
                ),
                "console_api": BrowserCapability(
                    name="Console API", supported=True, details="Supported"
                ),
                "timer_apis": BrowserCapability(
                    name="Timer APIs", supported=True, details="Supported"
                ),
                "secure_context": BrowserCapability(
                    name="Secure Context", supported=True, details="Supported"
                ),
                "user_agent": "Test Browser",
                "overall_compatible": True,
            }

            result = cmd.handle(
                config=Mock(),
                auth_manager=Mock(),
                client=Mock(),
                port=None,
                no_browser=False,
                timeout=60,
            )

        # Verify browser was opened
        mock_browser_open.assert_called_once()

        # Verify result
        assert isinstance(result, BrowserDebugResult)
        assert result.status == "success"
        assert result.browser_opened is True

    def test_handle_no_browser(self):
        """Test command execution without opening browser."""
        cmd = BrowserCommand()

        # Mock the async run_servers method
        with patch.object(
            cmd, "run_servers", new_callable=AsyncMock
        ) as mock_run_servers:
            mock_run_servers.return_value = {
                "completed": False,
                "timed_out": True,
                "duration": 60.0,
            }

            result = cmd.handle(
                config=Mock(),
                auth_manager=Mock(),
                client=Mock(),
                port=3005,
                no_browser=True,
                timeout=60,
            )

        # Verify result
        assert isinstance(result, BrowserDebugResult)
        assert result.status == "timeout"
        assert result.browser_opened is False
        assert result.port == 3005

    @pytest.mark.asyncio
    async def test_websocket_handler(self):
        """Test WebSocket message handling."""
        cmd = BrowserCommand()

        # Create mock messages
        mock_msg1 = Mock()
        mock_msg1.type = WSMsgType.TEXT
        mock_msg1.data = (
            '{"type": "info", "message": "Test message", "data": {}}'
        )

        mock_msg2 = Mock()
        mock_msg2.type = WSMsgType.TEXT
        mock_msg2.data = '{"type": "capability_check", "data": {"capability": "web_audio_api", "result": {"name": "Web Audio API", "supported": true, "details": "Supported", "required": true}}}'

        # Mock WebSocketResponse
        mock_ws = AsyncMock()
        mock_ws.prepare = AsyncMock()
        mock_ws.close = AsyncMock()
        mock_ws.__aiter__.return_value = [mock_msg1, mock_msg2]

        # Mock request
        mock_request = Mock()

        # Patch WebSocketResponse creation
        with patch("aiohttp.web.WebSocketResponse", return_value=mock_ws):
            result = await cmd.websocket_handler(mock_request)

        # Check messages were processed
        assert len(cmd.messages) == 2
        assert cmd.messages[0].type.value == "info"
        assert cmd.messages[0].message == "Test message"
        assert "web_audio_api" in cmd.capabilities_data
        assert result == mock_ws


class TestBrowserGuidedGate:
    """Verify the press-Enter prompt only fires in guided (bare) invocations."""

    def _run_handle(self, guided: bool):
        """Run BrowserCommand.handle and capture whether input() was called."""
        cmd = BrowserCommand()
        cmd._guided = guided
        called = {"input": 0, "open": 0}

        with patch("builtins.input", side_effect=lambda: called.__setitem__("input", called["input"] + 1)), patch(
            "deepctl_cmd_debug_browser.command.webbrowser.open",
            side_effect=lambda _url: called.__setitem__("open", called["open"] + 1),
        ), patch.object(
            cmd, "find_available_port", return_value=3100
        ), patch(
            "deepctl_cmd_debug_browser.command.web.AppRunner"
        ) as mock_runner_cls, patch(
            "deepctl_cmd_debug_browser.command.web.TCPSite"
        ) as mock_site_cls, patch(
            "deepctl_cmd_debug_browser.command.asyncio.sleep",
            new=AsyncMock(),
        ):
            mock_runner = AsyncMock()
            mock_runner_cls.return_value = mock_runner
            mock_runner.setup = AsyncMock()
            mock_runner.cleanup = AsyncMock()
            mock_site = AsyncMock()
            mock_site_cls.return_value = mock_site
            mock_site.start = AsyncMock()
            cmd.handle(
                config=Mock(),
                auth_manager=Mock(),
                client=Mock(),
                port=None,
                no_browser=False,
                timeout=0,
                save_report=None,
            )
        return called

    def test_guided_invocation_waits_for_press_enter(self):
        called = self._run_handle(guided=True)
        assert called["input"] == 1
        assert called["open"] == 1

    def test_non_guided_invocation_opens_browser_immediately(self):
        called = self._run_handle(guided=False)
        assert called["input"] == 0
        assert called["open"] == 1
