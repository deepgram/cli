"""Tests for listen command."""

from unittest.mock import Mock, patch

import pytest
from deepctl_cmd_listen.command import ListenCommand
from deepctl_cmd_listen.models import ListenResult
from deepctl_core import AuthManager, BaseResult, Config, DeepgramClient


class TestListenCommand:
    """Test cases for ListenCommand."""

    @pytest.fixture
    def command(self):
        """Create a ListenCommand instance."""
        return ListenCommand()

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
        assert command.name == "listen"
        assert command.requires_auth is True
        assert command.requires_project is False
        assert command.ci_friendly is True

    def test_get_arguments(self, command):
        """Test command arguments configuration."""
        args = command.get_arguments()

        # All arguments should be options (no positional args)
        positional = [a for a in args if not a.get("is_option", False)]
        assert len(positional) == 0

        # Collect all option names
        option_names = []
        for arg in args:
            if arg.get("is_option", False):
                option_names.extend(arg["names"])

        assert "--mic" in option_names
        assert "--model" in option_names
        assert "-m" in option_names
        assert "--language" in option_names
        assert "-l" in option_names
        assert "--encoding" in option_names
        assert "--sample-rate" in option_names
        assert "--channels" in option_names
        assert "--interim" in option_names
        assert "--punctuate" in option_names
        assert "--smart-format" in option_names

    @patch("deepctl_cmd_listen.command.sys")
    def test_handle_no_source_error(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test error when no mic flag and stdin is a TTY."""
        mock_sys.stdin.isatty.return_value = True

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            mic=False,
        )

        assert isinstance(result, BaseResult)
        assert result.status == "error"
        assert "No audio source" in result.message

    @patch("deepctl_cmd_listen.command.sys")
    def test_handle_mic_no_sounddevice(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test error when mic=True but sounddevice is not installed."""
        mock_sys.stdin.isatty.return_value = True

        original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def mock_import(name, *args, **kwargs):
            if name == "sounddevice":
                raise ImportError("No module named 'sounddevice'")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            result = command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                mic=True,
            )

        assert isinstance(result, BaseResult)
        assert result.status == "error"
        assert "sounddevice" in result.message

    @patch("deepctl_cmd_listen.command.sys")
    def test_handle_stdin_calls_listen_stdin(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test that piped stdin routes to _listen_stdin."""
        mock_sys.stdin.isatty.return_value = False

        with patch.object(
            command, "_listen_stdin", return_value=ListenResult(status="success", source="stdin")
        ) as mock_listen_stdin:
            result = command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                mic=False,
                model="nova-3",
                language="en-US",
                encoding="linear16",
                sample_rate=16000,
                channels=1,
                interim=False,
                punctuate=True,
                smart_format=True,
            )

            mock_listen_stdin.assert_called_once_with(
                mock_client,
                "nova-3",
                "en-US",
                "linear16",
                16000,
                1,
                False,
                True,
                True,
            )
            assert isinstance(result, ListenResult)
            assert result.status == "success"
            assert result.source == "stdin"

    @patch("deepctl_cmd_listen.command.sys")
    def test_handle_mic_calls_listen_mic(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test that --mic flag routes to _listen_mic."""
        mock_sys.stdin.isatty.return_value = True

        with patch.object(
            command, "_listen_mic", return_value=ListenResult(status="success", source="mic")
        ) as mock_listen_mic:
            result = command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                mic=True,
                model="nova-3",
                language="en-US",
                sample_rate=16000,
                channels=1,
                interim=False,
                punctuate=True,
                smart_format=True,
            )

            mock_listen_mic.assert_called_once_with(
                mock_client,
                "nova-3",
                "en-US",
                16000,
                1,
                False,
                True,
                True,
            )
            assert isinstance(result, ListenResult)
            assert result.status == "success"
            assert result.source == "mic"


class TestListenResult:
    """Test cases for ListenResult model."""

    def test_create_listen_result(self):
        """Test creating a ListenResult with all fields."""
        result = ListenResult(
            status="success",
            message="Transcription complete",
            transcript="Hello world",
            duration_seconds=5.2,
            source="mic",
        )

        assert result.status == "success"
        assert result.message == "Transcription complete"
        assert result.transcript == "Hello world"
        assert result.duration_seconds == 5.2
        assert result.source == "mic"

    def test_listen_result_defaults(self):
        """Test ListenResult with default values."""
        result = ListenResult()

        assert result.status == "success"
        assert result.transcript == ""
        assert result.duration_seconds == 0.0
        assert result.source == ""
