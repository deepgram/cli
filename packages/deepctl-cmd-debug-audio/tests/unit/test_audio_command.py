"""Tests for audio debug command."""

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from unittest.mock import MagicMock, Mock, patch

import pytest
from deepctl_cmd_debug_audio import command as command_module
from deepctl_cmd_debug_audio.command import AudioCommand
from deepctl_cmd_debug_audio.models import (
    AudioDebugResult,
    AudioFormat,
    AudioInfo,
    AudioStream,
)
from deepctl_core import AuthManager, Config, DeepgramClient


@pytest.fixture(autouse=True)
def _ffprobe_lookup_via_which(monkeypatch):
    """Drive the ffprobe lookup through shutil.which, which tests patch."""
    import shutil

    monkeypatch.setattr(
        command_module, "resolve_tool", lambda config, name, key: shutil.which(name)
    )


def _completed(returncode: int, stdout: str, stderr: bytes = b"") -> Mock:
    """A subprocess.run result carrying ffprobe's bytes output."""
    result = Mock()
    result.returncode = returncode
    result.stdout = stdout.encode("utf-8")
    result.stderr = stderr
    return result


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

    @patch("shutil.which", return_value="/usr/bin/ffprobe")
    @patch("subprocess.run")
    def test_run_ffprobe_standard(
        self, mock_run, mock_which, command, sample_probe_data
    ):
        """The standard probe runs ffprobe's JSON argv as a list, no shell."""
        mock_run.return_value = _completed(0, json.dumps(sample_probe_data))

        result = command.run_ffprobe("test.mp3")

        assert result == sample_probe_data
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert args[0] == [
            "/usr/bin/ffprobe",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            "-protocol_whitelist",
            "file",
            "file:" + os.path.abspath("test.mp3"),
        ]
        assert kwargs.get("shell", False) is False
        assert kwargs["timeout"] == 30
        assert kwargs["capture_output"] is True
        assert kwargs["stdin"] is subprocess.DEVNULL

    @pytest.mark.parametrize(
        ("given", "probed"),
        [
            ("-v.wav", "file:" + os.path.abspath("-v.wav")),
            ("-show_entries.wav", "file:" + os.path.abspath("-show_entries.wav")),
            ("/abs/-v.wav", "file:" + os.path.abspath("/abs/-v.wav")),
            ("dir/-v.wav", "file:" + os.path.abspath("dir/-v.wav")),
        ],
    )
    @patch("shutil.which", return_value="/usr/bin/ffprobe")
    @patch("subprocess.run")
    def test_run_ffprobe_never_passes_a_path_as_an_option(
        self, mock_run, mock_which, command, sample_probe_data, given, probed
    ):
        """A file name that starts with "-" reaches ffprobe as a file."""
        mock_run.return_value = _completed(0, json.dumps(sample_probe_data))

        command.run_ffprobe(given)
        assert mock_run.call_args[0][0][-3:] == ["-protocol_whitelist", "file", probed]

        command.run_ffprobe(given, "-show_streams")
        assert mock_run.call_args[0][0][-3:] == ["-protocol_whitelist", "file", probed]

    @patch("shutil.which", return_value="/usr/bin/ffprobe")
    @patch("subprocess.run")
    def test_run_ffprobe_failure_says_what_ffprobe_said(
        self, mock_run, mock_which, command
    ):
        """A non-zero ffprobe exit shows the last line ffprobe wrote."""
        mock_run.return_value = _completed(
            1, "", b"ffprobe version 7\n\nbroken.mp4: moov atom not found\n"
        )

        with pytest.raises(Exception) as exc:
            command.run_ffprobe("broken.mp4")

        assert str(exc.value) == (
            "Failed to probe file: ffprobe couldn't read the file: "
            "broken.mp4: moov atom not found"
        )

        mock_run.return_value = _completed(1, "", b"")
        with pytest.raises(Exception) as exc:
            command.run_ffprobe("broken.mp4")
        assert str(exc.value).endswith("ffprobe couldn't read the file: exit 1")

    @patch("shutil.which", return_value="/usr/bin/ffprobe")
    @patch("subprocess.run")
    def test_run_ffprobe_timeout_is_a_probe_failure(
        self, mock_run, mock_which, command
    ):
        """An ffprobe that hangs past the timeout is reported, not waited on."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(["ffprobe"], 30)

        with pytest.raises(Exception) as exc:
            command.run_ffprobe("slow.mp4")

        assert str(exc.value).startswith("Failed to probe file: ")
        assert "timed out after 30 seconds" in str(exc.value)

    @patch("shutil.which", return_value="/usr/bin/ffprobe")
    @patch("subprocess.run")
    def test_run_ffprobe_custom_args(
        self, mock_run, mock_which, command, sample_probe_data
    ):
        """Custom arguments go before the input, which keeps the file-only
        whitelist, the timeout and the closed stdin."""
        mock_run.return_value = _completed(0, json.dumps(sample_probe_data))

        result = command.run_ffprobe("test.mp3", "-show_streams -show_format")

        assert result == sample_probe_data
        expected_cmd = [
            "/usr/bin/ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            "-protocol_whitelist",
            "file",
            "file:" + os.path.abspath("test.mp3"),
        ]
        mock_run.assert_called_once()
        assert mock_run.call_args[0][0] == expected_cmd
        assert mock_run.call_args[1]["timeout"] == 30
        assert mock_run.call_args[1]["stdin"] is subprocess.DEVNULL

    @patch("shutil.which", return_value="/usr/bin/ffprobe")
    @patch("subprocess.run")
    def test_run_ffprobe_custom_args_cannot_widen_the_whitelist(
        self, mock_run, mock_which, command, sample_probe_data
    ):
        """A user-supplied -protocol_whitelist comes before ours, so the
        file-only one is the one that applies to the input."""
        mock_run.return_value = _completed(0, json.dumps(sample_probe_data))

        command.run_ffprobe("test.mp3", "-protocol_whitelist file,tcp")

        assert mock_run.call_args[0][0][-3:] == [
            "-protocol_whitelist",
            "file",
            "file:" + os.path.abspath("test.mp3"),
        ]

    @patch("shutil.which", return_value="/usr/bin/ffprobe")
    @patch("subprocess.run")
    def test_option_like_file_name_is_probed_as_a_file(
        self, mock_run, mock_which, command, sample_probe_data, tmp_path, monkeypatch
    ):
        """A file named "-f lavfi" is an input, not options."""
        mock_run.return_value = _completed(0, json.dumps(sample_probe_data))
        monkeypatch.chdir(tmp_path)

        command.run_ffprobe("-f lavfi")

        target = mock_run.call_args[0][0][-1]
        assert target == f"file:{tmp_path}{os.sep}-f lavfi"
        assert not target.startswith("-")

    @patch("shutil.which", return_value="/usr/bin/ffprobe")
    @patch("subprocess.run")
    def test_custom_args_are_split_like_a_shell(
        self, mock_run, mock_which, command, sample_probe_data
    ):
        """--ffprobe-args keeps a quoted value as one argument (shlex)."""
        mock_run.return_value = _completed(0, json.dumps(sample_probe_data))

        command.run_ffprobe(
            "-f lavfi", "-show_entries 'format=duration,size' -select_streams a"
        )

        cmd = mock_run.call_args[0][0]
        assert cmd[5:] == [
            "-show_entries",
            "format=duration,size",
            "-select_streams",
            "a",
            "-protocol_whitelist",
            "file",
            "file:" + os.path.abspath("-f lavfi"),
        ]

    @patch("shutil.which", return_value="/usr/bin/ffprobe")
    @patch("subprocess.run")
    def test_unbalanced_quotes_in_custom_args_fail_before_running(
        self, mock_run, mock_which, command
    ):
        with pytest.raises(Exception, match="Failed to probe file"):
            command.run_ffprobe("test.mp3", "-show_entries 'format")
        mock_run.assert_not_called()

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
    @patch("subprocess.run")
    def test_handle_success(
        self,
        mock_run,
        mock_which,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        sample_probe_data,
    ):
        """Test successful audio file analysis."""
        mock_which.return_value = "/usr/bin/ffprobe"
        mock_run.return_value = _completed(0, json.dumps(sample_probe_data))

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
    @patch("subprocess.run")
    def test_handle_probe_error(
        self,
        mock_run,
        mock_which,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
    ):
        """Test handling ffprobe errors."""
        mock_which.return_value = "/usr/bin/ffprobe"
        mock_run.return_value = _completed(1, "", b"Invalid data found")

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


class TestOutputChannels:
    """Which stream each line lands on (#104).

    `dg -o json debug audio -f missing.wav` used to put a rich failure panel
    on stdout ahead of the serialized AudioDebugResult, so `json.loads(stdout)`
    raised. Progress lines and failure panels belong on stderr always; the
    human rendering of the analysis belongs on stdout only in default mode.
    """

    @pytest.fixture
    def command(self):
        return AudioCommand()

    @pytest.fixture
    def mocks(self):
        return Mock(spec=Config), Mock(spec=AuthManager), Mock(spec=DeepgramClient)

    @pytest.fixture
    def probe_data(self):
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
                }
            ],
        }

    @pytest.mark.parametrize("fmt", ["json", "yaml", "csv", "table"])
    @patch("deepctl_cmd_debug_audio.command.get_output_format")
    @patch.object(AudioCommand, "check_ffmpeg_installed", return_value=False)
    def test_ffmpeg_missing_panel_goes_to_stderr(
        self, _ffmpeg, mock_format, fmt, command, mocks, capsys
    ):
        mock_format.return_value = fmt

        result = command.handle(*mocks, file="recording.wav")
        captured = capsys.readouterr()

        assert result.status == "error"
        assert captured.out == ""
        assert "FFmpeg" in captured.err

    @patch("deepctl_cmd_debug_audio.command.get_output_format", return_value="json")
    @patch.object(AudioCommand, "check_ffmpeg_installed", return_value=True)
    @patch.object(AudioCommand, "run_ffprobe", side_effect=RuntimeError("boom"))
    def test_analysis_failure_panel_goes_to_stderr(
        self, _probe, _ffmpeg, _format, command, mocks, capsys
    ):
        result = command.handle(*mocks, file="missing.wav")
        captured = capsys.readouterr()

        assert result.status == "error"
        assert captured.out == ""
        assert "Analyzing audio file" in captured.err
        assert "boom" in captured.err

    @patch("deepctl_cmd_debug_audio.command.get_output_format", return_value="json")
    @patch.object(AudioCommand, "check_ffmpeg_installed", return_value=True)
    def test_human_rendering_is_suppressed_for_machines(
        self, _ffmpeg, _format, command, mocks, probe_data, capsys
    ):
        with patch.object(AudioCommand, "run_ffprobe", return_value=probe_data):
            result = command.handle(*mocks, file="test.mp3")
        captured = capsys.readouterr()

        assert result.status == "success"
        assert captured.out == ""
        assert "Analyzing audio file" in captured.err

    @patch("deepctl_cmd_debug_audio.command.get_output_format", return_value="default")
    @patch.object(AudioCommand, "check_ffmpeg_installed", return_value=True)
    def test_human_rendering_still_prints_for_humans(
        self, _ffmpeg, _format, command, mocks, probe_data, capsys
    ):
        with patch.object(AudioCommand, "run_ffprobe", return_value=probe_data):
            command.handle(*mocks, file="test.mp3")
        captured = capsys.readouterr()

        assert "Audio File Analysis Complete" in captured.out
        assert "Deepgram Compatibility Check" in captured.out


def test_ffprobe_never_from_current_directory(tmp_path, monkeypatch):
    """A repository's ./ffprobe is not run, even with "." on PATH."""
    from deepctl_core import tools

    tool = tmp_path / "ffprobe"
    tool.write_text("#!/bin/sh\nexit 0\n")
    tool.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", os.pathsep.join([".", "", str(tmp_path)]))
    monkeypatch.setattr(command_module, "resolve_tool", tools.resolve_tool)
    command = AudioCommand()
    assert command.check_ffmpeg_installed() is False
    with pytest.raises(Exception, match="ffprobe not found"):
        command.run_ffprobe("a.wav")


# -- real ffprobe: the argv hardening holds against the binary itself -------

_FFPROBE = shutil.which("ffprobe")
needs_ffprobe = pytest.mark.skipif(_FFPROBE is None, reason="ffprobe not installed")


def _write_wav(path) -> None:
    import wave

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 1600)


@pytest.fixture
def real_command(monkeypatch):
    """An AudioCommand that uses the real ffprobe on PATH."""
    monkeypatch.setattr(command_module, "resolve_tool", lambda *a: _FFPROBE)
    return AudioCommand()


@needs_ffprobe
class TestRealFfprobe:
    def test_dash_named_file_is_probed_as_a_file(
        self, real_command, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        _write_wav(tmp_path / "-v.wav")

        for custom in (None, "-show_streams"):
            data = real_command.run_ffprobe("-v.wav", custom)
            assert data["streams"][0]["codec_name"] == "pcm_s16le"

    def test_tcp_input_is_refused_without_connecting(self, real_command):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(0.2)
        port = listener.getsockname()[1]
        accepted: list[object] = []
        stop = threading.Event()

        def accept() -> None:
            while not stop.is_set():
                try:
                    accepted.append(listener.accept()[0])
                except OSError:
                    continue

        thread = threading.Thread(target=accept, daemon=True)
        thread.start()
        try:
            for custom in (None, "-show_streams", "-protocol_whitelist file,tcp"):
                with pytest.raises(Exception, match="^Failed to probe file: "):
                    real_command.run_ffprobe(f"tcp://127.0.0.1:{port}", custom)
            time.sleep(0.3)
        finally:
            stop.set()
            thread.join(2)
            listener.close()
        assert accepted == []

    def test_concat_input_is_refused(self, real_command, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_wav(tmp_path / "a.wav")

        with pytest.raises(Exception, match="^Failed to probe file: "):
            real_command.run_ffprobe("concat:a.wav|a.wav")

    def test_pipe_input_returns_promptly(self, real_command):
        start = time.monotonic()
        with pytest.raises(Exception, match="^Failed to probe file: "):
            real_command.run_ffprobe("pipe:0")
        assert time.monotonic() - start < 5
