"""Tests for speak command."""

from unittest.mock import Mock, patch

import pytest
from deepctl_cmd_speak.command import SpeakCommand
from deepctl_cmd_speak.models import SpeakResult
from deepctl_core import AuthManager, BaseResult, Config, DeepgramClient


class TestSpeakCommand:
    """Test cases for SpeakCommand."""

    @pytest.fixture
    def command(self):
        """Create a SpeakCommand instance."""
        return SpeakCommand()

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
        assert command.name == "speak"
        assert command.requires_auth is True
        assert command.ci_friendly is True

    def test_get_arguments(self, command):
        """Test command arguments configuration."""
        args = command.get_arguments()

        # Check positional argument
        positional = [a for a in args if not a.get("is_option", False)]
        assert len(positional) == 1
        assert positional[0]["name"] == "text"

        # Check options
        option_names = []
        for arg in args:
            if arg.get("is_option", False):
                option_names.extend(arg["names"])

        assert "--output" in option_names
        assert "-o" in option_names
        assert "--model" in option_names
        assert "-m" in option_names
        assert "--encoding" in option_names
        assert "--container" in option_names
        assert "--sample-rate" in option_names
        assert "--file" in option_names
        assert "-f" in option_names

    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_no_text_error(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test error when no text provided and stdin is a TTY."""
        mock_sys.stdin.isatty.return_value = True

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            text=None,
            output=None,
            model=None,
            encoding=None,
            container=None,
            sample_rate=None,
            file=None,
        )

        assert isinstance(result, BaseResult)
        assert result.status == "error"
        assert "No text provided" in result.message

    @patch("deepctl_cmd_speak.command.Path")
    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_text_from_file(
        self,
        mock_sys,
        mock_path_cls,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
    ):
        """Test reading text from a file."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = False

        mock_stdout_buffer = Mock()
        mock_sys.stdout.buffer = mock_stdout_buffer

        mock_path_instance = Mock()
        mock_path_instance.exists.return_value = True
        mock_path_instance.read_text.return_value = "Hello from file"
        mock_path_cls.return_value = mock_path_instance

        mock_client.speak_text.return_value = iter([b"chunk1", b"chunk2"])

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            text=None,
            output=None,
            model="aura-2-asteria-en",
            encoding=None,
            container=None,
            sample_rate=None,
            file="test.txt",
        )

        assert isinstance(result, SpeakResult)
        assert result.status == "success"
        mock_path_cls.assert_any_call("test.txt")
        mock_path_instance.read_text.assert_called_once()
        mock_client.speak_text.assert_called_once_with(
            text="Hello from file",
            model="aura-2-asteria-en",
            encoding=None,
            container=None,
            sample_rate=None,
        )

    @patch("deepctl_cmd_speak.command.Path")
    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_file_not_found(
        self,
        mock_sys,
        mock_path_cls,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
    ):
        """Test error when specified file does not exist."""
        mock_sys.stdin.isatty.return_value = True

        mock_path_instance = Mock()
        mock_path_instance.exists.return_value = False
        mock_path_cls.return_value = mock_path_instance

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            text=None,
            output=None,
            model=None,
            encoding=None,
            container=None,
            sample_rate=None,
            file="nonexistent.txt",
        )

        assert isinstance(result, BaseResult)
        assert result.status == "error"
        assert "File not found" in result.message

    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_no_output_tty_error(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test error when stdout is a TTY and no output file specified."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = True

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            text="Hello world",
            output=None,
            model="aura-2-asteria-en",
            encoding=None,
            container=None,
            sample_rate=None,
            file=None,
        )

        assert isinstance(result, BaseResult)
        assert result.status == "error"
        assert "No output specified" in result.message

    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_write_to_file(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client, tmp_path
    ):
        """Test writing audio output to a file."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = True

        mock_client.speak_text.return_value = iter([b"chunk1", b"chunk2"])

        output_file = tmp_path / "output.mp3"

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            text="Hello world",
            output=str(output_file),
            model="aura-2-asteria-en",
            encoding=None,
            container=None,
            sample_rate=None,
            file=None,
        )

        assert isinstance(result, SpeakResult)
        assert result.status == "success"
        assert result.output_path == str(output_file)
        assert result.model == "aura-2-asteria-en"
        assert result.bytes_written == len(b"chunk1") + len(b"chunk2")

        assert output_file.read_bytes() == b"chunk1chunk2"

    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_write_to_stdout(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test writing audio output to stdout when not a TTY."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = False

        mock_stdout_buffer = Mock()
        mock_sys.stdout.buffer = mock_stdout_buffer

        mock_client.speak_text.return_value = iter([b"chunk1", b"chunk2"])

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            text="Hello world",
            output=None,
            model="aura-2-asteria-en",
            encoding=None,
            container=None,
            sample_rate=None,
            file=None,
        )

        assert isinstance(result, SpeakResult)
        assert result.status == "success"
        assert result.bytes_written == len(b"chunk1") + len(b"chunk2")

        mock_stdout_buffer.write.assert_any_call(b"chunk1")
        mock_stdout_buffer.write.assert_any_call(b"chunk2")
        mock_stdout_buffer.flush.assert_called_once()


class TestSpeakResult:
    """Test cases for SpeakResult model."""

    def test_create_speak_result(self):
        """Test creating a SpeakResult."""
        result = SpeakResult(
            status="success",
            message="Audio saved to output.mp3",
            output_path="output.mp3",
            model="aura-2-asteria-en",
            bytes_written=1024,
        )

        assert result.status == "success"
        assert result.output_path == "output.mp3"
        assert result.model == "aura-2-asteria-en"
        assert result.bytes_written == 1024

    def test_speak_result_defaults(self):
        """Test SpeakResult with default values."""
        result = SpeakResult()

        assert result.status == "success"
        assert result.output_path == ""
        assert result.model == ""
        assert result.bytes_written == 0
