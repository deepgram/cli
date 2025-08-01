"""Tests for audio debug command."""

import pytest
from unittest.mock import Mock, patch, MagicMock
import json

from deepctl_cmd_debug_audio.command import AudioCommand
from deepctl_cmd_debug_audio.models import (
    AudioDebugResult,
    AudioInfo,
    AudioFormat,
    AudioStream,
)
from deepctl_core import Config, AuthManager, DeepgramClient


class TestAudioCommand:
    """Test cases for AudioCommand."""

    @pytest.fixture
    def command(self):
        """Create an AudioCommand instance."""
        return AudioCommand()

    @pytest.fixture
    def mock_config(self):
        """Create a mock config."""
        return Mock(spec=Config)

    @pytest.fixture
    def mock_auth_manager(self):
        """Create a mock auth manager."""
        return Mock(spec=AuthManager)

    @pytest.fixture
    def mock_client(self):
        """Create a mock Deepgram client."""
        return Mock(spec=DeepgramClient)

    @pytest.fixture
    def sample_probe_data(self):
        """Sample ffprobe output data."""
        return {
            "format": {
                "filename": "test.mp3",
                "format_name": "mp3",
                "format_long_name": "MP2/3 (MPEG audio layer 2/3)",
                "duration": "120.456",
                "size": "2890752",
                "bit_rate": "192000",
                "nb_streams": 1,
            },
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "mp3",
                    "codec_long_name": "MP3 (MPEG audio layer 3)",
                    "sample_rate": "44100",
                    "channels": 2,
                    "channel_layout": "stereo",
                    "bit_rate": "192000",
                }
            ],
        }

    def test_command_properties(self, command):
        """Test command basic properties."""
        assert command.name == "audio"
        assert command.requires_auth is False
        assert command.requires_project is False
        assert command.ci_friendly is True

    def test_get_arguments(self, command):
        """Test command arguments configuration."""
        args = command.get_arguments()

        # Check that all required arguments are present
        arg_names = [arg["names"][0] for arg in args]
        assert "--file" in arg_names
        assert "--verbose" in arg_names
        assert "--extra-verbose" in arg_names
        assert "--ffprobe-args" in arg_names

        # Check file argument is required
        file_arg = next(arg for arg in args if "--file" in arg["names"])
        assert file_arg["required"] is True

    @patch("shutil.which")
    def test_check_ffmpeg_installed(self, mock_which, command):
        """Test ffmpeg installation check."""
        # Test when ffmpeg is installed
        mock_which.return_value = "/usr/bin/ffprobe"
        assert command.check_ffmpeg_installed() is True

        # Test when ffmpeg is not installed
        mock_which.return_value = None
        assert command.check_ffmpeg_installed() is False

    @patch("ffmpeg.probe")
    def test_run_ffprobe_standard(
        self, mock_probe, command, sample_probe_data
    ):
        """Test running ffprobe without custom arguments."""
        mock_probe.return_value = sample_probe_data

        result = command.run_ffprobe("test.mp3")

        assert result == sample_probe_data
        mock_probe.assert_called_once_with("test.mp3")

    @patch("subprocess.run")
    def test_run_ffprobe_custom_args(
        self, mock_run, command, sample_probe_data
    ):
        """Test running ffprobe with custom arguments."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(sample_probe_data)
        mock_run.return_value = mock_result

        result = command.run_ffprobe("test.mp3", "-show_streams -show_format")

        assert result == sample_probe_data
        expected_cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            "test.mp3",
        ]
        mock_run.assert_called_once()
        assert mock_run.call_args[0][0] == expected_cmd

    def test_parse_audio_info(self, command, sample_probe_data):
        """Test parsing ffprobe output into AudioInfo."""
        audio_info = command.parse_audio_info(sample_probe_data)

        # Check format parsing
        assert audio_info.format is not None
        assert audio_info.format.filename == "test.mp3"
        assert audio_info.format.format_name == "mp3"
        assert audio_info.format.duration == 120.456
        assert audio_info.format.size == 2890752
        assert audio_info.format.bit_rate == "192000"

        # Check stream parsing
        assert len(audio_info.streams) == 1
        stream = audio_info.streams[0]
        assert stream.codec_name == "mp3"
        assert stream.sample_rate == "44100"
        assert stream.channels == 2
        assert stream.channel_layout == "stereo"

    @patch("shutil.which")
    def test_handle_ffmpeg_not_installed(
        self, mock_which, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test handling when ffmpeg is not installed."""
        mock_which.return_value = None

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            file="test.mp3",
        )

        assert isinstance(result, AudioDebugResult)
        assert result.status == "error"
        assert result.message == "FFmpeg is not installed"
        assert result.ffmpeg_installed is False

    @patch("shutil.which")
    @patch("ffmpeg.probe")
    def test_handle_success(
        self,
        mock_probe,
        mock_which,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        sample_probe_data,
    ):
        """Test successful audio file analysis."""
        mock_which.return_value = "/usr/bin/ffprobe"
        mock_probe.return_value = sample_probe_data

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            file="test.mp3",
        )

        assert isinstance(result, AudioDebugResult)
        assert result.status == "success"
        assert result.message == "Audio file analyzed successfully"
        assert result.audio_info is not None
        assert result.audio_info.format.filename == "test.mp3"

    @patch("shutil.which")
    @patch("ffmpeg.probe")
    def test_handle_probe_error(
        self,
        mock_probe,
        mock_which,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
    ):
        """Test handling ffprobe errors."""
        mock_which.return_value = "/usr/bin/ffprobe"
        mock_probe.side_effect = Exception("Failed to probe file")

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            file="test.mp3",
        )

        assert isinstance(result, AudioDebugResult)
        assert result.status == "error"
        assert result.message == "Failed to analyze audio file"
        assert "Failed to probe file" in result.error_details

    def test_deepgram_compatibility_checks(self, command, sample_probe_data):
        """Test Deepgram compatibility checking logic."""
        # Test low sample rate detection
        low_sample_rate_data = sample_probe_data.copy()
        low_sample_rate_data["streams"][0]["sample_rate"] = "4000"

        audio_info = command.parse_audio_info(low_sample_rate_data)
        assert int(audio_info.streams[0].sample_rate) < 8000

        # Test multi-channel detection
        multi_channel_data = sample_probe_data.copy()
        multi_channel_data["streams"][0]["channels"] = 5

        audio_info = command.parse_audio_info(multi_channel_data)
        assert audio_info.streams[0].channels > 2
