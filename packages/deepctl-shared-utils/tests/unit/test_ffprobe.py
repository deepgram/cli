"""Tests for shared ffprobe wrapper."""

import json
import os
from unittest.mock import Mock, patch

import pytest
from deepctl_shared_utils.ffprobe import (
    check_ffprobe,
    ffprobe_input,
    get_ffprobe_path,
    probe_buffer,
    probe_file,
    require_ffprobe,
)
from deepctl_shared_utils.ffprobe_models import (
    AudioFormatInfo,
    AudioProbeResult,
    AudioStreamInfo,
)

SAMPLE_FFPROBE_OUTPUT = {
    "format": {
        "format_name": "wav",
        "format_long_name": "WAV / WAVE (Waveform Audio)",
        "duration": "2.5",
        "size": "80044",
        "bit_rate": "256000",
    },
    "streams": [
        {
            "codec_type": "audio",
            "codec_name": "pcm_s16le",
            "codec_long_name": "PCM signed 16-bit little-endian",
            "sample_rate": "16000",
            "channels": 1,
            "channel_layout": "mono",
            "bit_rate": "256000",
            "bits_per_sample": 16,
            "duration": "2.5",
        }
    ],
}


class TestModels:
    """Test ffprobe Pydantic models."""

    def test_audio_stream_info_defaults(self):
        stream = AudioStreamInfo()
        assert stream.codec_name is None
        assert stream.channels is None

    def test_audio_stream_info_populated(self):
        stream = AudioStreamInfo(
            codec_name="pcm_s16le",
            sample_rate="16000",
            channels=1,
            channel_layout="mono",
            bit_rate="256000",
            bits_per_sample=16,
            duration=2.5,
        )
        assert stream.codec_name == "pcm_s16le"
        assert stream.channels == 1
        assert stream.duration == 2.5

    def test_audio_format_info_defaults(self):
        fmt = AudioFormatInfo()
        assert fmt.format_name is None
        assert fmt.duration is None

    def test_audio_probe_result_defaults(self):
        result = AudioProbeResult()
        assert result.format is None
        assert result.streams == []
        assert result.raw_data is None

    def test_audio_probe_result_serialization(self):
        result = AudioProbeResult(
            format=AudioFormatInfo(format_name="wav", duration=2.5),
            streams=[AudioStreamInfo(codec_name="pcm_s16le", channels=1)],
        )
        data = result.model_dump()
        assert data["format"]["format_name"] == "wav"
        assert len(data["streams"]) == 1


class TestGetFfprobePath:
    """Test get_ffprobe_path function."""

    @patch("deepctl_shared_utils.ffprobe._find_on_path")
    def test_auto_detect(self, mock_which):
        mock_which.return_value = "/usr/bin/ffprobe"
        assert get_ffprobe_path() == "/usr/bin/ffprobe"

    @patch("deepctl_shared_utils.ffprobe._find_on_path")
    def test_not_found(self, mock_which):
        mock_which.return_value = None
        assert get_ffprobe_path() is None

    @patch("deepctl_shared_utils.ffprobe.os.access")
    @patch("deepctl_shared_utils.ffprobe.os.path.isfile")
    @patch("deepctl_shared_utils.ffprobe._find_on_path")
    def test_config_path_used(self, mock_which, mock_isfile, mock_access):
        mock_isfile.return_value = True
        mock_access.return_value = True
        mock_which.return_value = "/usr/bin/ffprobe"

        stored = os.path.abspath("/opt/custom/ffprobe")
        config = Mock()
        config.get.return_value = stored

        assert get_ffprobe_path(config) == stored
        # shutil.which should not be called when config path is valid
        mock_which.assert_not_called()

    @patch("deepctl_shared_utils.ffprobe.os.access")
    @patch("deepctl_shared_utils.ffprobe.os.path.isfile")
    @patch("deepctl_shared_utils.ffprobe._find_on_path")
    def test_config_path_invalid_falls_back(
        self, mock_which, mock_isfile, mock_access
    ):
        mock_isfile.return_value = False
        mock_access.return_value = False
        mock_which.return_value = "/usr/bin/ffprobe"

        config = Mock()
        config.get.return_value = "/nonexistent/ffprobe"

        assert get_ffprobe_path(config) == "/usr/bin/ffprobe"


    def test_relative_config_path_never_used(self, tmp_path, monkeypatch):
        """A relative stored path would resolve inside the current directory."""
        tool = tmp_path / "tools" / "ffprobe"
        tool.parent.mkdir()
        tool.write_text("#!/bin/sh\nexit 0\n")
        tool.chmod(0o755)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PATH", "")

        config = Mock()
        config.get.return_value = "./tools/ffprobe"

        assert get_ffprobe_path(config) is None

    def test_absolute_config_path_used(self, tmp_path):
        tool = tmp_path / "ffprobe"
        tool.write_text("#!/bin/sh\nexit 0\n")
        tool.chmod(0o755)

        config = Mock()
        config.get.return_value = str(tool)

        assert get_ffprobe_path(config) == str(tool)

    def test_path_lookup_skips_relative_entries(self, tmp_path, monkeypatch):
        _fake_ffprobe(tmp_path, monkeypatch)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PATH", os.pathsep.join([".", "", "tools"]))

        assert get_ffprobe_path() is None

    def test_path_lookup_skips_current_directory(self, tmp_path, monkeypatch):
        _fake_ffprobe(tmp_path, monkeypatch)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PATH", str(tmp_path))

        assert get_ffprobe_path() is None

    def test_path_lookup_finds_absolute_entries(self, tmp_path, monkeypatch):
        tool = _fake_ffprobe(tmp_path / "bin", monkeypatch)
        monkeypatch.setenv("PATH", str(tool.parent))

        assert get_ffprobe_path() == str(tool)


def _fake_ffprobe(directory, monkeypatch):
    """An executable ffprobe that PATH lookup would find on this platform."""
    tool = directory / "ffprobe"
    if os.name == "nt":
        tool = tool.with_suffix(".exe")
        monkeypatch.setenv("PATHEXT", ".exe")
    directory.mkdir(exist_ok=True)
    tool.write_text("#!/bin/sh\nexit 0\n")
    tool.chmod(0o755)
    return tool


class TestCheckFfprobe:
    """Test check_ffprobe function."""

    @patch("deepctl_shared_utils.ffprobe.get_ffprobe_path")
    def test_available(self, mock_get_path):
        mock_get_path.return_value = "/usr/bin/ffprobe"
        assert check_ffprobe() is True

    @patch("deepctl_shared_utils.ffprobe.get_ffprobe_path")
    def test_not_available(self, mock_get_path):
        mock_get_path.return_value = None
        assert check_ffprobe() is False


class TestRequireFfprobe:
    """Test require_ffprobe function."""

    @patch("deepctl_shared_utils.ffprobe.check_ffprobe")
    def test_available(self, mock_check):
        mock_check.return_value = True
        assert require_ffprobe() is True

    @patch("deepctl_shared_utils.ffprobe.print_ffprobe_install_instructions")
    @patch("deepctl_shared_utils.ffprobe.check_ffprobe")
    def test_not_available_prints_instructions(
        self, mock_check, mock_print
    ):
        mock_check.return_value = False
        assert require_ffprobe() is False
        mock_print.assert_called_once()


class TestProbeFile:
    """Test probe_file function."""

    @patch("deepctl_shared_utils.ffprobe.get_ffprobe_path")
    def test_no_ffprobe(self, mock_get_path):
        mock_get_path.return_value = None
        assert probe_file("/tmp/test.wav") is None

    @patch("subprocess.run")
    @patch("deepctl_shared_utils.ffprobe.get_ffprobe_path")
    def test_success(self, mock_get_path, mock_run):
        mock_get_path.return_value = "/usr/bin/ffprobe"
        mock_run.return_value = Mock(
            returncode=0,
            stdout=json.dumps(SAMPLE_FFPROBE_OUTPUT),
        )

        result = probe_file("/tmp/test.wav")

        assert isinstance(result, AudioProbeResult)
        assert result.format is not None
        assert result.format.format_name == "wav"
        assert result.format.duration == 2.5
        assert result.format.size == 80044
        assert len(result.streams) == 1
        assert result.streams[0].codec_name == "pcm_s16le"
        assert result.streams[0].sample_rate == "16000"
        assert result.streams[0].channels == 1
        assert result.streams[0].bits_per_sample == 16

    @patch("subprocess.run")
    @patch("deepctl_shared_utils.ffprobe.get_ffprobe_path")
    def test_ffprobe_failure(self, mock_get_path, mock_run):
        mock_get_path.return_value = "/usr/bin/ffprobe"
        mock_run.return_value = Mock(returncode=1, stdout="", stderr="error")

        assert probe_file("/tmp/test.wav") is None

    @patch("subprocess.run")
    @patch("deepctl_shared_utils.ffprobe.get_ffprobe_path")
    def test_timeout(self, mock_get_path, mock_run):
        import subprocess

        mock_get_path.return_value = "/usr/bin/ffprobe"
        mock_run.side_effect = subprocess.TimeoutExpired("ffprobe", 30)

        assert probe_file("/tmp/test.wav") is None

    @patch("subprocess.run")
    @patch("deepctl_shared_utils.ffprobe.get_ffprobe_path")
    def test_no_audio_streams(self, mock_get_path, mock_run):
        mock_get_path.return_value = "/usr/bin/ffprobe"
        mock_run.return_value = Mock(
            returncode=0,
            stdout=json.dumps({"format": {"format_name": "raw"}, "streams": []}),
        )

        result = probe_file("/tmp/test.raw")
        assert isinstance(result, AudioProbeResult)
        assert result.format is not None
        assert result.format.format_name == "raw"
        assert result.streams == []


class TestFfprobeInput:
    """File names are never read as ffprobe options."""

    def test_absolute_path_gets_file_protocol(self):
        path = os.path.abspath("/tmp/test.wav")
        assert ffprobe_input(path) == f"file:{path}"

    def test_option_like_name_becomes_a_file_input(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert ffprobe_input("-f lavfi") == f"file:{tmp_path}{os.sep}-f lavfi"

    @patch("subprocess.run")
    @patch("deepctl_shared_utils.ffprobe.get_ffprobe_path")
    def test_probe_file_passes_file_protocol(
        self, mock_get_path, mock_run, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        mock_get_path.return_value = "/usr/bin/ffprobe"
        mock_run.return_value = Mock(
            returncode=0, stdout=json.dumps(SAMPLE_FFPROBE_OUTPUT)
        )

        probe_file("-f lavfi")

        cmd = mock_run.call_args[0][0]
        assert cmd[-1] == f"file:{tmp_path}{os.sep}-f lavfi"
        assert "-f" not in cmd and "lavfi" not in cmd

    @patch("subprocess.run")
    @patch("deepctl_shared_utils.ffprobe.get_ffprobe_path")
    def test_probe_buffer_passes_file_protocol(self, mock_get_path, mock_run):
        mock_get_path.return_value = "/usr/bin/ffprobe"
        mock_run.return_value = Mock(
            returncode=0, stdout=json.dumps(SAMPLE_FFPROBE_OUTPUT)
        )

        probe_buffer(b"\x00" * 100, "test")

        arg = mock_run.call_args[0][0][-1]
        assert arg.startswith("file:")
        assert os.path.isabs(arg[len("file:") :])


class TestProbeBuffer:
    """Test probe_buffer function."""

    def test_empty_buffer(self):
        assert probe_buffer(b"") is None

    @patch("deepctl_shared_utils.ffprobe.get_ffprobe_path")
    def test_no_ffprobe(self, mock_get_path):
        mock_get_path.return_value = None
        assert probe_buffer(b"\x00" * 100) is None

    @patch("subprocess.run")
    @patch("deepctl_shared_utils.ffprobe.get_ffprobe_path")
    def test_success(self, mock_get_path, mock_run):
        mock_get_path.return_value = "/usr/bin/ffprobe"
        mock_run.return_value = Mock(
            returncode=0,
            stdout=json.dumps(SAMPLE_FFPROBE_OUTPUT),
        )

        result = probe_buffer(b"\x00" * 100, "test")

        assert isinstance(result, AudioProbeResult)
        assert result.streams[0].codec_name == "pcm_s16le"

    @patch("subprocess.run")
    @patch("deepctl_shared_utils.ffprobe.get_ffprobe_path")
    def test_cleanup_on_failure(self, mock_get_path, mock_run):
        mock_get_path.return_value = "/usr/bin/ffprobe"
        mock_run.return_value = Mock(returncode=1, stdout="", stderr="error")

        result = probe_buffer(b"\x00" * 100, "test")
        assert result is None
