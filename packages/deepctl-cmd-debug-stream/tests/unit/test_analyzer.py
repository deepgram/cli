"""Tests for audio analyzer."""

import json
from unittest.mock import Mock, patch

import pytest
from deepctl_cmd_debug_stream.analyzer import AudioAnalyzer
from deepctl_cmd_debug_stream.models import AudioFormatReport


class TestAudioAnalyzer:
    """Test cases for AudioAnalyzer."""

    @patch("shutil.which")
    def test_is_available_installed(self, mock_which):
        """Test availability check when ffprobe is installed."""
        mock_which.return_value = "/usr/bin/ffprobe"
        assert AudioAnalyzer.is_available() is True

    @patch("shutil.which")
    def test_is_available_not_installed(self, mock_which):
        """Test availability check when ffprobe is not installed."""
        mock_which.return_value = None
        assert AudioAnalyzer.is_available() is False

    def test_analyze_buffer_empty(self):
        """Test analysis of empty buffer."""
        result = AudioAnalyzer.analyze_buffer(b"")
        assert result is None

    @patch("shutil.which")
    def test_analyze_buffer_ffprobe_not_available(self, mock_which):
        """Test analysis when ffprobe is not available."""
        mock_which.return_value = None
        result = AudioAnalyzer.analyze_buffer(b"\x00" * 100)
        assert result is None

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_analyze_buffer_success(self, mock_run, mock_which):
        """Test successful audio analysis."""
        mock_which.return_value = "/usr/bin/ffprobe"

        ffprobe_output = {
            "format": {
                "format_name": "wav",
                "bit_rate": "256000",
                "duration": "1.5",
            },
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": "16000",
                    "channels": 1,
                    "bit_rate": "256000",
                }
            ],
        }

        mock_run.return_value = Mock(
            returncode=0,
            stdout=json.dumps(ffprobe_output),
            stderr="",
        )

        result = AudioAnalyzer.analyze_buffer(b"\x00" * 100, "test")

        assert isinstance(result, AudioFormatReport)
        assert result.codec == "pcm_s16le"
        assert result.sample_rate == "16000"
        assert result.channels == 1
        assert result.format_name == "wav"
        assert result.bit_rate == "256000"
        assert result.duration == 1.5

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_analyze_buffer_ffprobe_failure(self, mock_run, mock_which):
        """Test analysis when ffprobe fails."""
        mock_which.return_value = "/usr/bin/ffprobe"
        mock_run.return_value = Mock(
            returncode=1,
            stdout="",
            stderr="error",
        )

        result = AudioAnalyzer.analyze_buffer(b"\x00" * 100)
        assert result is None

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_analyze_buffer_timeout(self, mock_run, mock_which):
        """Test analysis when ffprobe times out."""
        import subprocess

        mock_which.return_value = "/usr/bin/ffprobe"
        mock_run.side_effect = subprocess.TimeoutExpired("ffprobe", 10)

        result = AudioAnalyzer.analyze_buffer(b"\x00" * 100)
        assert result is None

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_analyze_buffer_no_audio_stream(self, mock_run, mock_which):
        """Test analysis when no audio stream is found."""
        mock_which.return_value = "/usr/bin/ffprobe"

        ffprobe_output = {
            "format": {"format_name": "raw"},
            "streams": [],
        }

        mock_run.return_value = Mock(
            returncode=0,
            stdout=json.dumps(ffprobe_output),
            stderr="",
        )

        result = AudioAnalyzer.analyze_buffer(b"\x00" * 100)

        assert isinstance(result, AudioFormatReport)
        assert result.format_name == "raw"
        assert result.codec is None
        assert result.sample_rate is None
