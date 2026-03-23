"""Tests for read command."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from deepctl_cmd_read.command import ReadCommand
from deepctl_cmd_read.models import ReadResult
from deepctl_core import AuthManager, BaseResult, Config, DeepgramClient


class TestReadCommand:
    """Test cases for ReadCommand."""

    @pytest.fixture
    def command(self):
        """Create a ReadCommand instance."""
        return ReadCommand()

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

    @pytest.fixture
    def analyze_response(self):
        """Create a standard analyze_text response."""
        return {
            "results": {
                "summary": {"text": "A summary of the content"},
                "sentiments": {
                    "average": {
                        "sentiment": "positive",
                        "sentiment_score": 0.85,
                    }
                },
                "topics": {
                    "segments": [
                        {
                            "topics": [
                                {
                                    "topic": "billing",
                                    "confidence_score": 0.9,
                                }
                            ]
                        }
                    ]
                },
                "intents": {
                    "segments": [
                        {
                            "intents": [
                                {
                                    "intent": "complaint",
                                    "confidence_score": 0.8,
                                }
                            ]
                        }
                    ]
                },
            }
        }

    def test_command_properties(self, command):
        """Test command basic properties."""
        assert command.name == "read"
        assert command.requires_auth is True
        assert command.ci_friendly is True

    def test_get_arguments(self, command):
        """Test command arguments configuration."""
        args = command.get_arguments()

        # Check positional argument
        positional = [a for a in args if not a.get("is_option", False)]
        assert len(positional) == 1
        assert positional[0]["name"] == "text"
        assert positional[0]["required"] is False
        assert positional[0]["default"] is None

        # Check options
        option_names = []
        for arg in args:
            if arg.get("is_option", False):
                option_names.extend(arg["names"])

        assert "--file" in option_names
        assert "-f" in option_names
        assert "--sentiment" in option_names
        assert "--summarize" in option_names
        assert "--topics" in option_names
        assert "--intents" in option_names
        assert "--language" in option_names
        assert "-l" in option_names

        # Check flags
        flags = [a for a in args if a.get("is_flag", False)]
        flag_names = []
        for f in flags:
            flag_names.extend(f["names"])
        assert "--sentiment" in flag_names
        assert "--summarize" in flag_names
        assert "--topics" in flag_names
        assert "--intents" in flag_names

    def test_handle_text_from_arg(
        self, command, mock_config, mock_auth_manager, mock_client, analyze_response
    ):
        """Test handling text provided as argument with all features enabled by default."""
        mock_client.analyze_text.return_value = analyze_response

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            text="Hello world",
        )

        assert isinstance(result, ReadResult)
        assert result.status == "success"

        # When no features specified, all should be enabled
        mock_client.analyze_text.assert_called_once_with(
            text="Hello world",
            sentiment=True,
            summarize=True,
            topics=True,
            intents=True,
            language=None,
        )

    @patch("deepctl_cmd_read.command.Path")
    def test_handle_text_from_file(
        self,
        mock_path_cls,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        analyze_response,
    ):
        """Test handling text read from a file."""
        mock_path_instance = Mock()
        mock_path_instance.exists.return_value = True
        mock_path_instance.read_text.return_value = "Text from file"
        mock_path_cls.return_value = mock_path_instance

        mock_client.analyze_text.return_value = analyze_response

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            file="test.txt",
        )

        assert isinstance(result, ReadResult)
        assert result.status == "success"
        mock_path_cls.assert_called_once_with("test.txt")
        mock_path_instance.exists.assert_called_once()
        mock_path_instance.read_text.assert_called_once()
        mock_client.analyze_text.assert_called_once()

    @patch("deepctl_cmd_read.command.sys")
    def test_handle_no_text_error(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test error when no text provided and stdin is a tty."""
        mock_sys.stdin.isatty.return_value = True

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
        )

        assert isinstance(result, BaseResult)
        assert result.status == "error"
        assert "No text provided" in result.message

    def test_handle_sentiment_only(
        self, command, mock_config, mock_auth_manager, mock_client, analyze_response
    ):
        """Test handling with only sentiment flag enabled."""
        mock_client.analyze_text.return_value = analyze_response

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            text="Hello world",
            sentiment=True,
            summarize=False,
            topics=False,
            intents=False,
        )

        assert result.status == "success"
        mock_client.analyze_text.assert_called_once_with(
            text="Hello world",
            sentiment=True,
            summarize=False,
            topics=False,
            intents=False,
            language=None,
        )

    @patch("deepctl_cmd_read.command.Path")
    def test_handle_file_not_found(
        self, mock_path_cls, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test error when file path doesn't exist."""
        mock_path_instance = Mock()
        mock_path_instance.exists.return_value = False
        mock_path_cls.return_value = mock_path_instance

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            file="nonexistent.txt",
        )

        assert isinstance(result, BaseResult)
        assert result.status == "error"
        assert "File not found" in result.message

    def test_handle_error(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test handling when client raises an exception."""
        mock_client.analyze_text.side_effect = Exception("API connection failed")

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            text="Hello world",
        )

        assert isinstance(result, BaseResult)
        assert result.status == "error"
        assert "API connection failed" in result.message


class TestReadResult:
    """Test cases for ReadResult model."""

    def test_create_read_result(self):
        """Test creating a ReadResult with all fields."""
        result = ReadResult(
            status="success",
            summary="A summary",
            sentiment="positive",
            sentiment_score=0.85,
            topics=[{"topics": [{"topic": "billing"}]}],
            intents=[{"intents": [{"intent": "complaint"}]}],
        )

        assert result.status == "success"
        assert result.summary == "A summary"
        assert result.sentiment == "positive"
        assert result.sentiment_score == 0.85
        assert len(result.topics) == 1
        assert len(result.intents) == 1

    def test_read_result_defaults(self):
        """Test ReadResult with default values."""
        result = ReadResult()

        assert result.status == "success"
        assert result.summary == ""
        assert result.sentiment == ""
        assert result.sentiment_score == 0.0
        assert result.topics == []
        assert result.intents == []

    def test_read_result_serialization(self):
        """Test ReadResult can be serialized."""
        result = ReadResult(
            status="success",
            summary="Test summary",
            sentiment="negative",
            sentiment_score=0.3,
        )

        data = result.model_dump()
        assert data["summary"] == "Test summary"
        assert data["sentiment"] == "negative"
        assert data["sentiment_score"] == 0.3
