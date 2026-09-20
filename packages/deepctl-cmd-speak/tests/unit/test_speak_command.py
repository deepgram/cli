"""Tests for speak command."""

import struct
import sys
import wave
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

import click
import pytest
from deepctl_cmd_speak.command import (
    _FILE_PLAYER_ARGV,
    _STDIN_PLAYER_ARGV,
    SpeakCommand,
    _check_playable,
    _find_audio_player,
    _fmt_bytes,
    _play_audio,
    _play_suffix,
    _player_stdin,
    _streaming_wav_header,
    _StreamProgress,
    _voice_type_badge,
)
from deepctl_cmd_speak.models import SpeakResult, SpeakVoicesResult
from deepctl_core import AuthManager, BaseResult, Config, DeepgramClient


def _only(*players):
    """A shutil.which side effect where only these players are installed."""
    return lambda p: f"/usr/bin/{p}" if p in players else None


def _wav_header_fields(blob: bytes) -> tuple[int, int, int]:
    """(sample_rate, channels, bits) parsed from a 44-byte PCM WAV header."""
    assert blob[:4] == b"RIFF"
    assert blob[8:12] == b"WAVE"
    assert blob[12:16] == b"fmt "
    channels, sample_rate = struct.unpack("<HI", blob[22:28])
    (bits,) = struct.unpack("<H", blob[34:36])
    assert blob[36:40] == b"data"
    return sample_rate, channels, bits


@pytest.fixture
def stub_stdin_player(tmp_path, monkeypatch):
    """Stand a real stub process in for the stdin-reading player.

    Actual playback needs a sound card, but the plumbing does not: the stub
    copies everything piped to it into a file, so a test can assert on the
    exact byte stream a player would have been handed.
    """
    script = tmp_path / "stdin_player.py"
    script.write_text(
        "import sys\n"
        "data = sys.stdin.buffer.read()\n"
        "with open(sys.argv[1], 'wb') as fh:\n"
        "    fh.write(data)\n"
    )
    piped = tmp_path / "piped.bin"
    piped.write_bytes(b"")
    monkeypatch.setitem(
        _STDIN_PLAYER_ARGV, "ffplay", [sys.executable, str(script), str(piped)]
    )
    return piped


@pytest.fixture
def failing_stdin_player(tmp_path, monkeypatch):
    """A stdin player that consumes the audio and then exits non-zero."""
    script = tmp_path / "failing_stdin_player.py"
    script.write_text("import sys\nsys.stdin.buffer.read()\nsys.exit(3)\n")
    monkeypatch.setitem(_STDIN_PLAYER_ARGV, "ffplay", [sys.executable, str(script)])
    return script


class _FilePlayerStub:
    """Records the temp file a file-based player (afplay) was handed."""

    def __init__(self, copy_path: Path, record_path: Path) -> None:
        self.copy_path = copy_path
        self._record_path = record_path

    @property
    def arg_path(self) -> str:
        return self._record_path.read_text().strip()


@pytest.fixture
def stub_file_player(tmp_path, monkeypatch):
    """Stand a real stub process in for afplay, which plays a file by path."""
    script = tmp_path / "file_player.py"
    script.write_text(
        "import sys\n"
        "src = sys.argv[3]\n"
        "with open(src, 'rb') as fh:\n"
        "    data = fh.read()\n"
        "with open(sys.argv[1], 'wb') as fh:\n"
        "    fh.write(data)\n"
        "with open(sys.argv[2], 'w') as fh:\n"
        "    fh.write(src)\n"
    )
    copy_path = tmp_path / "played.bin"
    record_path = tmp_path / "played_path.txt"
    monkeypatch.setitem(
        _FILE_PLAYER_ARGV,
        "afplay",
        [sys.executable, str(script), str(copy_path), str(record_path)],
    )
    return _FilePlayerStub(copy_path, record_path)


@pytest.fixture
def failing_file_player(tmp_path, monkeypatch):
    """A file-based player that exits non-zero."""
    script = tmp_path / "failing_file_player.py"
    script.write_text("import sys\nsys.exit(4)\n")
    monkeypatch.setitem(_FILE_PLAYER_ARGV, "afplay", [sys.executable, str(script)])
    return script


class TestStreamProgress:
    """The live-progress helper used by the Flux streaming path."""

    def test_fmt_bytes(self):
        assert _fmt_bytes(512) == "512 B"
        assert _fmt_bytes(2048) == "2 KB"
        assert _fmt_bytes(3 * 1024 * 1024) == "3.0 MB"

    def test_track_passes_chunks_through_and_counts(self):
        prog = _StreamProgress("flux-alexis-en")
        out = list(prog.track(iter([b"ab", b"", b"cde"])))

        # Chunks are yielded unchanged (including the empty one).
        assert out == [b"ab", b"", b"cde"]
        # Total counts every byte; the empty chunk contributes nothing.
        assert prog.total == 5
        # Time-to-first-audio is recorded from the first non-empty chunk.
        assert prog.first_audio is not None
        assert "first audio" in prog.timing()

    def test_track_no_audio_leaves_first_audio_unset(self):
        prog = _StreamProgress("flux-alexis-en")
        assert list(prog.track(iter([]))) == []
        assert prog.total == 0
        assert prog.first_audio is None
        assert "n/a" in prog.timing()


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
        assert "beta" in command.agent_help
        assert "default 0" in command.agent_help
        assert any(
            "--expressivity" in example and "beta" in example
            for example in command.examples
        )

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
        assert "--speed" in option_names
        assert "--expressivity" in option_names
        assert "--file" in option_names
        assert "-f" in option_names
        assert "--play" in option_names
        assert "--list-voices" in option_names

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

        # Aura stdout path returns None (audio already written to the pipe).
        assert result is None
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
        # The message names every way out, including --play.
        assert "--play" in result.message

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
    def test_handle_default_model_routes_to_flux(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client, tmp_path
    ):
        """With no -m, the default is Flux (v2 streaming), not Aura (v1 REST)."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = True

        pcm = b"\x01\x00\x02\x00"  # raw 16-bit PCM
        mock_client.speak_text_stream.return_value = iter([pcm])

        output_file = tmp_path / "hello.wav"
        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            text="Hello world",
            output=str(output_file),
            model=None,
            encoding=None,
            container=None,
            sample_rate=None,
            file=None,
        )

        assert isinstance(result, SpeakResult)
        assert result.model == "flux-alexis-en"
        # Routed to streaming (v2), not batch REST (v1).
        mock_client.speak_text_stream.assert_called_once()
        mock_client.speak_text.assert_not_called()
        assert output_file.read_bytes()[:4] == b"RIFF"

    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_flux_streams_and_wraps_wav(
        self,
        mock_sys,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        tmp_path,
    ):
        """flux-* models route to WebSocket streaming (v2) and wrap PCM in WAV."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = True

        pcm = b"\x01\x00\x02\x00\x03\x00\x04\x00"  # raw 16-bit PCM
        mock_client.speak_text_stream.return_value = iter([pcm[:4], pcm[4:]])

        output_file = tmp_path / "hello.wav"
        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            text="Hello from Flux",
            output=str(output_file),
            model="flux-alexis-en",
            encoding=None,
            container=None,
            sample_rate=None,
            file=None,
        )

        assert isinstance(result, SpeakResult)
        assert result.status == "success"
        assert result.model == "flux-alexis-en"
        # Routed to streaming (v2), not batch REST (v1).
        mock_client.speak_text_stream.assert_called_once()
        mock_client.speak_text.assert_not_called()
        # Output is a valid WAV container wrapping the streamed PCM.
        data = output_file.read_bytes()
        assert data[:4] == b"RIFF"
        assert data[8:12] == b"WAVE"
        assert pcm in data

    @pytest.mark.parametrize("model", ["flux", "fluxfoo"])
    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_non_flux_prefix_uses_v1_pass_through(
        self,
        mock_sys,
        model,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        tmp_path,
    ):
        """Bare and typo flux names do not enter the flux-* Speak v2 route."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = True
        mock_client.speak_text.return_value = iter([b"audio"])

        command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            text="Hello",
            output=str(tmp_path / "output.mp3"),
            model=model,
            encoding=None,
            container=None,
            sample_rate=None,
            file=None,
        )

        mock_client.speak_text.assert_called_once_with(
            text="Hello",
            model=model,
            encoding=None,
            container=None,
            sample_rate=None,
        )
        mock_client.speak_text_stream.assert_not_called()

    @pytest.mark.parametrize("model", ["flux", "fluxfoo", "aura-2-asteria-en"])
    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_rest_api_error_exits_nonzero(
        self,
        mock_sys,
        model,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        tmp_path,
    ):
        """A Speak v1 (REST) API failure raises ClickException so the command
        exits non-zero. A returned error result would only print and still exit
        0 — the sink that bare/typo `flux` names (now routed to REST, not the
        raising v2 path) would otherwise fall into."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = True
        mock_client.speak_text.side_effect = Exception("model not found")

        with pytest.raises(click.ClickException) as exc_info:
            command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                text="Hello",
                output=str(tmp_path / "output.mp3"),
                model=model,
                encoding=None,
                container=None,
                sample_rate=None,
                file=None,
            )

        assert "model not found" in str(exc_info.value)

    @pytest.mark.parametrize("model", ["flux", "fluxfoo"])
    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_non_flux_prefix_control_error_is_model_neutral(
        self,
        mock_sys,
        model,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        tmp_path,
    ):
        """Unknown models are not mislabeled as Aura in control validation."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = True

        with pytest.raises(click.ClickException) as exc_info:
            command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                text="Hello",
                output=str(tmp_path / "output.wav"),
                model=model,
                encoding=None,
                container=None,
                sample_rate=None,
                speed=1.0,
                file=None,
            )

        assert model in str(exc_info.value)
        assert "Aura" not in str(exc_info.value)
        mock_client.speak_text.assert_not_called()
        mock_client.speak_text_stream.assert_not_called()

    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_flux_rejects_non_raw_encoding(
        self,
        mock_sys,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        tmp_path,
    ):
        """flux-* with a containerized encoding fails loudly (streaming is raw).

        The guard raises so the process exits non-zero rather than printing
        nothing and exiting 0 in the default output format.
        """
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = True

        with pytest.raises(click.ClickException, match="not supported for Flux"):
            command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                text="Hello",
                output=str(tmp_path / "x.mp3"),
                model="flux-alexis-en",
                encoding="mp3",
                container=None,
                sample_rate=None,
                file=None,
            )

        mock_client.speak_text_stream.assert_not_called()

    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_flux_forwards_speed_and_expressivity(
        self,
        mock_sys,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        tmp_path,
    ):
        """--speed / --expressivity reach speak_text_stream for flux-* models."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = True

        pcm = b"\x01\x00\x02\x00"
        mock_client.speak_text_stream.return_value = iter([pcm])

        command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            text="Hello",
            output=str(tmp_path / "hello.wav"),
            model="flux-alexis-en",
            encoding=None,
            container=None,
            sample_rate=None,
            speed=0.9,
            expressivity=2,
            file=None,
        )

        _, kwargs = mock_client.speak_text_stream.call_args
        assert kwargs["speed"] == 0.9
        assert kwargs["expressivity"] == 2

    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_speed_rejected_for_aura(
        self,
        mock_sys,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        tmp_path,
    ):
        """speed / expressivity are Flux-only; using them with Aura fails loudly."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = True

        with pytest.raises(click.ClickException, match="only supported for Flux"):
            command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                text="Hello",
                output=str(tmp_path / "x.mp3"),
                model="aura-2-asteria-en",
                encoding=None,
                container=None,
                sample_rate=None,
                speed=1.0,
                file=None,
            )

        mock_client.speak_text_stream.assert_not_called()
        mock_client.speak_text.assert_not_called()

    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_invalid_speed_rejected(
        self,
        mock_sys,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        tmp_path,
    ):
        """An off-grid --speed value fails before opening a stream."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = True

        with pytest.raises(click.ClickException, match="--speed must be one of"):
            command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                text="Hello",
                output=str(tmp_path / "x.wav"),
                model="flux-alexis-en",
                encoding=None,
                container=None,
                sample_rate=None,
                speed=1.3,
                file=None,
            )

        mock_client.speak_text_stream.assert_not_called()

    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_invalid_expressivity_rejected(
        self,
        mock_sys,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        tmp_path,
    ):
        """An out-of-range --expressivity value fails before opening a stream."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = True

        with pytest.raises(click.ClickException, match="--expressivity must be one of"):
            command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                text="Hello",
                output=str(tmp_path / "x.wav"),
                model="flux-alexis-en",
                encoding=None,
                container=None,
                sample_rate=None,
                expressivity=5,
                file=None,
            )

        mock_client.speak_text_stream.assert_not_called()

    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_flux_empty_audio_fails(
        self,
        mock_sys,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        tmp_path,
    ):
        """A Flux stream that yields no audio fails loudly and writes nothing."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = True

        mock_client.speak_text_stream.return_value = iter([])

        output_file = tmp_path / "empty.wav"
        with pytest.raises(click.ClickException, match="returned no audio"):
            command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                text="Hello from Flux",
                output=str(output_file),
                model="flux-alexis-en",
                encoding=None,
                container=None,
                sample_rate=None,
                file=None,
            )

        # No header-only WAV is left behind on failure.
        assert not output_file.exists()

    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_flux_streaming_failure_raises(
        self,
        mock_sys,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        tmp_path,
    ):
        """A streaming error surfaces as a non-zero exit, not a success/exit-0."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = True

        def _boom():
            raise RuntimeError("socket closed")
            yield  # pragma: no cover — make this a generator

        mock_client.speak_text_stream.return_value = _boom()

        with pytest.raises(click.ClickException, match="Flux streaming failed"):
            command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                text="Hello from Flux",
                output=str(tmp_path / "x.wav"),
                model="flux-alexis-en",
                encoding=None,
                container=None,
                sample_rate=None,
                file=None,
            )

    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_flux_streams_to_stdout_incrementally(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client
    ):
        """flux-* piped to stdout emits a WAV header then each chunk, flushing
        per chunk so a downstream player starts on the first frame."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = False
        mock_buffer = Mock()
        mock_sys.stdout.buffer = mock_buffer

        mock_client.speak_text_stream.return_value = iter(
            [b"\x01\x00\x02\x00", b"\x03\x00\x04\x00"]
        )

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            text="Hello from Flux",
            output=None,
            model="flux-alexis-en",
            encoding=None,
            container=None,
            sample_rate=None,
            file=None,
        )

        # Returns None so the framework never serializes a result to stdout —
        # in agentic/json mode that JSON would corrupt the piped audio.
        assert result is None

        writes = [c.args[0] for c in mock_buffer.write.call_args_list]
        # First write is a streaming WAV header; then the raw PCM chunks.
        assert writes[0][:4] == b"RIFF"
        assert writes[0][8:12] == b"WAVE"
        assert b"\x01\x00\x02\x00" in writes
        assert b"\x03\x00\x04\x00" in writes
        # Flushed per chunk (low latency), not a single flush at the end.
        assert mock_buffer.flush.call_count >= 2

    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_flux_stdout_empty_audio_fails(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client
    ):
        """An empty Flux stream to stdout fails loudly and writes nothing —
        no lone header, so a player never sees a valid-but-silent WAV."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = False
        mock_buffer = Mock()
        mock_sys.stdout.buffer = mock_buffer

        mock_client.speak_text_stream.return_value = iter([])

        with pytest.raises(click.ClickException, match="returned no audio"):
            command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                text="Hello from Flux",
                output=None,
                model="flux-alexis-en",
                encoding=None,
                container=None,
                sample_rate=None,
                file=None,
            )

        mock_buffer.write.assert_not_called()

    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_write_to_stdout(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client
    ):
        """Aura piped to stdout writes audio and returns None.

        Returning None keeps the framework from serializing a result to the
        stdout console — in agentic/json mode that JSON would be appended to
        the audio, corrupting a `> out` redirect.
        """
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

        assert result is None

        mock_stdout_buffer.write.assert_any_call(b"chunk1")
        mock_stdout_buffer.write.assert_any_call(b"chunk2")
        mock_stdout_buffer.flush.assert_called_once()

    # ── --list-voices ────────────────────────────────────────────────

    @patch("deepctl_cmd_speak.command.get_output_format", return_value="default")
    def test_handle_list_voices(
        self, _fmt, command, mock_config, mock_auth_manager, mock_client, capsys
    ):
        """--list-voices lists TTS voices with aura/flux badges, no generation."""
        mock_client.list_models.return_value = {
            "stt": [{"name": "nova-3", "language": "en"}],
            "tts": [
                {"name": "aura-2-asteria-en", "language": "en"},
                {"name": "flux-alexis-en", "language": "en"},
            ],
        }

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
            play=False,
            list_voices=True,
        )

        assert isinstance(result, SpeakVoicesResult)
        assert result.status == "success"
        assert result.count == 2
        badges = {v.name: v.voice_type for v in result.voices}
        assert badges["aura-2-asteria-en"] == "aura"
        assert badges["flux-alexis-en"] == "flux"
        # STT models are excluded; no speech is generated.
        assert all("nova" not in v.name for v in result.voices)
        mock_client.speak_text.assert_not_called()
        mock_client.speak_text_stream.assert_not_called()

        # The table renders like `dg models`: on stdout, which carries no
        # audio on this path, naming both voices and the default model.
        captured = capsys.readouterr()
        assert "Deepgram TTS Voices" in captured.out
        assert "flux-alexis-en" in captured.out
        assert "default: flux-alexis-en" in captured.out.replace("\n", "")

    @patch("deepctl_cmd_speak.command.get_output_format", return_value="json")
    def test_handle_list_voices_json_prints_no_table(
        self, _fmt, command, mock_config, mock_auth_manager, mock_client, capsys
    ):
        """In json mode the framework serializes the result; no table on stdout."""
        mock_client.list_models.return_value = {
            "tts": [{"name": "flux-alexis-en", "language": "en"}]
        }

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            text=None,
            list_voices=True,
        )

        assert isinstance(result, SpeakVoicesResult)
        assert result.count == 1
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_handle_list_voices_empty(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """--list-voices with no TTS voices returns an info result."""
        mock_client.list_models.return_value = {"tts": []}

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
            play=False,
            list_voices=True,
        )

        assert isinstance(result, SpeakVoicesResult)
        assert result.status == "info"
        assert result.count == 0

    def test_handle_list_voices_api_error(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """A failing list_models surfaces as an error result, not a traceback."""
        mock_client.list_models.side_effect = Exception("boom")

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            text=None,
            list_voices=True,
        )

        assert result is not None
        assert result.status == "error"
        assert "boom" in result.message

    # ── --play ───────────────────────────────────────────────────────

    @patch("deepctl_cmd_speak.command.shutil")
    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_flux_play_streams_wav_to_player(
        self,
        mock_sys,
        mock_shutil,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        stub_stdin_player,
    ):
        """flux --play streams a playable WAV into the player as audio arrives."""
        mock_sys.stdin.isatty.return_value = True
        # stdout is a TTY and there is no -o: --play is the destination, so the
        # "no output specified" guard must not fire.
        mock_sys.stdout.isatty.return_value = True
        mock_shutil.which.side_effect = _only("ffplay")

        pcm = [b"\x01\x00\x02\x00", b"\x03\x00\x04\x00"]
        # The empty keep-alive chunk must not reach the player.
        mock_client.speak_text_stream.return_value = iter([pcm[0], b"", pcm[1]])

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            text="Hello from Flux",
            output=None,
            model="flux-alexis-en",
            play=True,
        )

        assert isinstance(result, SpeakResult)
        assert result.status == "success"
        assert result.played is True
        assert result.output_path == ""
        assert result.bytes_written == 8

        # What the player actually received: the streaming WAV header this
        # command already uses for `| ffplay -`, then the PCM frames.
        piped = stub_stdin_player.read_bytes()
        assert piped == _streaming_wav_header(sample_rate=24000) + b"".join(pcm)
        assert _wav_header_fields(piped) == (24000, 1, 16)

    @patch("deepctl_cmd_speak.command.shutil")
    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_flux_play_and_save_together(
        self,
        mock_sys,
        mock_shutil,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        stub_stdin_player,
        tmp_path,
    ):
        """--play with -o plays the stream and saves an exact-length WAV."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = True
        mock_shutil.which.side_effect = _only("ffplay")

        pcm = [b"\x01\x00\x02\x00", b"\x03\x00\x04\x00"]
        mock_client.speak_text_stream.return_value = iter(pcm)
        output_file = tmp_path / "out.wav"

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            text="Hello from Flux",
            output=str(output_file),
            model="flux-alexis-en",
            play=True,
        )

        assert isinstance(result, SpeakResult)
        assert result.played is True
        assert result.output_path == str(output_file)

        # The player got the streaming framing...
        assert stub_stdin_player.read_bytes() == (
            _streaming_wav_header(sample_rate=24000) + b"".join(pcm)
        )
        # ...and the saved file got a real header a WAV reader can trust.
        with wave.open(str(output_file), "rb") as wav:
            assert wav.getframerate() == 24000
            assert wav.getnchannels() == 1
            assert wav.getsampwidth() == 2
            assert wav.readframes(wav.getnframes()) == b"".join(pcm)

    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_flux_mulaw_to_file_is_not_wrapped(
        self,
        mock_sys,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        tmp_path,
    ):
        """Only linear16 gets a WAV wrapper; mulaw is saved as raw audio."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = True
        mock_client.speak_text_stream.return_value = iter([b"\xff\xfe", b"\xfd"])
        output_file = tmp_path / "out.raw"

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            text="Hello",
            output=str(output_file),
            model="flux-alexis-en",
            encoding="mulaw",
            play=False,
        )

        assert isinstance(result, SpeakResult)
        assert output_file.read_bytes() == b"\xff\xfe\xfd"
        assert result.bytes_written == 3

    @patch("deepctl_cmd_speak.command.shutil")
    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_flux_play_no_audio_fails(
        self,
        mock_sys,
        mock_shutil,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        stub_stdin_player,
    ):
        """An empty Flux stream fails loudly instead of reporting playback."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = True
        mock_shutil.which.side_effect = _only("ffplay")
        mock_client.speak_text_stream.return_value = iter([])

        with pytest.raises(click.ClickException, match="returned no audio"):
            command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                text="Hello",
                output=None,
                model="flux-alexis-en",
                play=True,
            )

    @patch("deepctl_cmd_speak.command.shutil")
    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_flux_play_stream_error_is_reported(
        self,
        mock_sys,
        mock_shutil,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        stub_stdin_player,
    ):
        """A mid-stream Flux failure exits non-zero rather than half-playing."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = True
        mock_shutil.which.side_effect = _only("ffplay")

        def exploding():
            yield b"\x01\x00"
            raise RuntimeError("socket died")

        mock_client.speak_text_stream.return_value = exploding()

        with pytest.raises(click.ClickException, match="Flux streaming failed"):
            command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                text="Hello",
                output=None,
                model="flux-alexis-en",
                play=True,
            )

    @patch("deepctl_cmd_speak.command.shutil")
    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_flux_play_player_exit_code_fails(
        self,
        mock_sys,
        mock_shutil,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        failing_stdin_player,
    ):
        """A player that exits non-zero fails the command."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = True
        mock_shutil.which.side_effect = _only("ffplay")
        mock_client.speak_text_stream.return_value = iter([b"\x01\x00\x02\x00"])

        with pytest.raises(click.ClickException, match="exited with status 3"):
            command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                text="Hello",
                output=None,
                model="flux-alexis-en",
                play=True,
            )

    @patch("deepctl_cmd_speak.command.shutil")
    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_flux_play_player_quit_is_not_an_error(
        self,
        mock_sys,
        mock_shutil,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
    ):
        """Quitting the player mid-playback (broken pipe) is not a failure."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = True
        mock_shutil.which.side_effect = _only("ffplay")
        mock_client.speak_text_stream.return_value = iter([b"\x01\x00", b"\x02\x00"])

        @contextmanager
        def quit_immediately(player):
            sink = Mock()
            sink.write.side_effect = BrokenPipeError
            yield sink

        with patch("deepctl_cmd_speak.command._player_stdin", quit_immediately):
            result = command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                text="Hello",
                output=None,
                model="flux-alexis-en",
                play=True,
            )

        assert isinstance(result, SpeakResult)
        assert result.status == "success"
        assert result.played is True

    @patch("deepctl_cmd_speak.command.shutil")
    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_flux_play_afplay_gets_a_complete_wav_file(
        self,
        mock_sys,
        mock_shutil,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        stub_file_player,
    ):
        """afplay has no stdin mode, so it is handed a finished WAV file."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = True
        mock_shutil.which.side_effect = _only("afplay")
        pcm = [b"\x01\x00\x02\x00", b"\x03\x00\x04\x00"]
        mock_client.speak_text_stream.return_value = iter(pcm)

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            text="Hello from Flux",
            output=None,
            model="flux-alexis-en",
            play=True,
        )

        assert isinstance(result, SpeakResult)
        assert result.played is True
        # The file afplay was pointed at is a complete, exact-length WAV.
        handed_to_player = stub_file_player.copy_path
        with wave.open(str(handed_to_player), "rb") as wav:
            assert wav.getframerate() == 24000
            assert wav.readframes(wav.getnframes()) == b"".join(pcm)
        assert str(stub_file_player.arg_path).endswith(".wav")

    @patch("deepctl_cmd_speak.command.shutil")
    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_aura_play_pipes_container_bytes(
        self,
        mock_sys,
        mock_shutil,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        stub_stdin_player,
    ):
        """Aura --play pipes the container bytes (mp3) straight to the player."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = True
        mock_shutil.which.side_effect = _only("ffplay")
        mock_client.speak_text.return_value = iter([b"ID3", b"mp3-data"])

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            text="Hello world",
            output=None,
            model="aura-2-asteria-en",
            play=True,
        )

        assert isinstance(result, SpeakResult)
        assert result.status == "success"
        assert result.played is True
        assert result.bytes_written == 11
        assert stub_stdin_player.read_bytes() == b"ID3mp3-data"

    @patch("deepctl_cmd_speak.command.shutil")
    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_aura_play_and_save_together(
        self,
        mock_sys,
        mock_shutil,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        stub_stdin_player,
        tmp_path,
    ):
        """--play together with -o both saves the file and plays the audio."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = True
        mock_shutil.which.side_effect = _only("ffplay")
        mock_client.speak_text.return_value = iter([b"chunk1", b"chunk2"])
        output_file = tmp_path / "out.mp3"

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            text="Hello world",
            output=str(output_file),
            model="aura-2-asteria-en",
            play=True,
        )

        assert isinstance(result, SpeakResult)
        assert result.played is True
        assert result.output_path == str(output_file)
        assert output_file.read_bytes() == b"chunk1chunk2"
        assert stub_stdin_player.read_bytes() == b"chunk1chunk2"

    @patch("deepctl_cmd_speak.command.shutil")
    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_aura_play_no_audio_errors(
        self,
        mock_sys,
        mock_shutil,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        stub_stdin_player,
    ):
        """An empty Aura response is an error, not a silent success."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = True
        mock_shutil.which.side_effect = _only("ffplay")
        mock_client.speak_text.return_value = iter([])

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            text="Hello world",
            output=None,
            model="aura-2-asteria-en",
            play=True,
        )

        assert result is not None
        assert result.status == "error"
        assert "no audio" in result.message

    @patch("deepctl_cmd_speak.command.shutil")
    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_play_no_player_found(
        self,
        mock_sys,
        mock_shutil,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
    ):
        """--play with no available player fails clearly before any API call."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = True
        mock_shutil.which.return_value = None

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            text="Hello world",
            output=None,
            model="aura-2-asteria-en",
            play=True,
        )

        assert isinstance(result, BaseResult)
        assert result.status == "error"
        assert "No audio player found" in result.message
        mock_client.speak_text.assert_not_called()
        mock_client.speak_text_stream.assert_not_called()

    @patch("deepctl_cmd_speak.command.shutil")
    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_play_rejects_raw_flux_encoding(
        self,
        mock_sys,
        mock_shutil,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
    ):
        """Raw mulaw has no container to detect, so --play refuses it up front."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = True
        mock_shutil.which.side_effect = _only("ffplay")

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            text="Hello",
            output=None,
            model="flux-alexis-en",
            encoding="mulaw",
            play=True,
        )

        assert result is not None
        assert result.status == "error"
        assert "raw mulaw" in result.message
        mock_client.speak_text_stream.assert_not_called()

    @patch("deepctl_cmd_speak.command.shutil")
    @patch("deepctl_cmd_speak.command.sys")
    def test_handle_play_rejects_mp3_on_pcm_only_player(
        self,
        mock_sys,
        mock_shutil,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
    ):
        """aplay cannot decode Aura's mp3, so say so instead of playing noise."""
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdout.isatty.return_value = True
        mock_shutil.which.side_effect = _only("aplay")

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            text="Hello",
            output=None,
            model="aura-2-asteria-en",
            play=True,
        )

        assert result is not None
        assert result.status == "error"
        assert "can only play PCM/WAV" in result.message
        mock_client.speak_text.assert_not_called()


class TestAudioPlayerHelpers:
    """The player-detection, format, and playback helpers behind --play."""

    @patch("deepctl_cmd_speak.command.shutil")
    def test_find_audio_player_prefers_ffplay(self, mock_shutil):
        mock_shutil.which.side_effect = lambda p: f"/usr/bin/{p}"
        assert _find_audio_player() == "ffplay"

    @patch("deepctl_cmd_speak.command.shutil")
    def test_find_audio_player_falls_back_in_order(self, mock_shutil):
        # ffplay missing, afplay present → afplay wins over paplay/aplay.
        mock_shutil.which.side_effect = lambda p: (
            f"/usr/bin/{p}" if p in ("afplay", "paplay", "aplay") else None
        )
        assert _find_audio_player() == "afplay"

    @patch("deepctl_cmd_speak.command.shutil")
    def test_find_audio_player_prefers_paplay_over_aplay(self, mock_shutil):
        mock_shutil.which.side_effect = lambda p: (
            f"/usr/bin/{p}" if p in ("paplay", "aplay") else None
        )
        assert _find_audio_player() == "paplay"

    @patch("deepctl_cmd_speak.command.shutil")
    def test_find_audio_player_last_resort_aplay(self, mock_shutil):
        mock_shutil.which.side_effect = _only("aplay")
        assert _find_audio_player() == "aplay"

    @patch("deepctl_cmd_speak.command.shutil")
    def test_find_audio_player_none(self, mock_shutil):
        mock_shutil.which.return_value = None
        assert _find_audio_player() is None

    def test_voice_type_badge(self):
        assert _voice_type_badge("aura-2-asteria-en") == "aura"
        assert _voice_type_badge("Aura-Asteria-EN") == "aura"
        assert _voice_type_badge("flux-alexis-en") == "flux"
        assert _voice_type_badge("something-else") == "tts"

    def test_play_suffix(self):
        assert _play_suffix(is_flux=True, encoding="linear16", container=None) == ".wav"
        assert _play_suffix(is_flux=True, encoding=None, container=None) == ".wav"
        assert _play_suffix(is_flux=True, encoding="mulaw", container=None) == ".raw"
        assert _play_suffix(is_flux=False, encoding="mp3", container=None) == ".mp3"
        assert _play_suffix(is_flux=False, encoding=None, container=None) == ".mp3"
        assert _play_suffix(is_flux=False, encoding=None, container="wav") == ".wav"
        assert _play_suffix(is_flux=False, encoding="opus", container="ogg") == ".ogg"
        assert _play_suffix(is_flux=False, encoding="flac", container=None) == ".flac"
        # Speak v1 wraps PCM encodings in WAV unless --container none.
        assert _play_suffix(is_flux=False, encoding="mulaw", container=None) == ".wav"
        assert _play_suffix(is_flux=False, encoding="mulaw", container="none") == ".raw"

    def test_check_playable_accepts_what_players_can_decode(self):
        assert (
            _check_playable("ffplay", is_flux=True, encoding=None, container=None)
            is None
        )
        assert (
            _check_playable("aplay", is_flux=True, encoding="linear16", container=None)
            is None
        )
        assert (
            _check_playable("ffplay", is_flux=False, encoding=None, container=None)
            is None
        )
        assert (
            _check_playable("afplay", is_flux=False, encoding="mp3", container=None)
            is None
        )

    def test_check_playable_rejects_raw_audio(self):
        flux_raw = _check_playable(
            "ffplay", is_flux=True, encoding="alaw", container=None
        )
        assert flux_raw is not None
        assert "raw alaw" in flux_raw

        aura_raw = _check_playable(
            "ffplay", is_flux=False, encoding="linear16", container="none"
        )
        assert aura_raw is not None
        assert "no container" in aura_raw

    def test_check_playable_rejects_compressed_audio_on_pcm_only_players(self):
        for player in ("paplay", "aplay"):
            problem = _check_playable(
                player, is_flux=False, encoding="mp3", container=None
            )
            assert problem is not None
            assert "can only play PCM/WAV" in problem

    def test_play_audio_pipes_to_stdin_player(self, stub_stdin_player):
        """The bytes handed to a stdin player are exactly the audio."""
        _play_audio("ffplay", b"audio-bytes", suffix=".wav")

        assert stub_stdin_player.read_bytes() == b"audio-bytes"

    def test_play_audio_default_ffplay_argv_reads_stdin(self):
        """The real ffplay invocation is the documented no-extra-flags one."""
        assert _STDIN_PLAYER_ARGV["ffplay"] == [
            "ffplay",
            "-loglevel",
            "error",
            "-nodisp",
            "-autoexit",
            "-",
        ]
        assert _STDIN_PLAYER_ARGV["aplay"][-1] == "-"

    def test_play_audio_raises_on_player_failure(self, failing_stdin_player):
        with pytest.raises(click.ClickException, match="exited with status 3"):
            _play_audio("ffplay", b"audio", suffix=".wav")

    def test_play_audio_file_player_gets_the_bytes_and_cleans_up(
        self, stub_file_player
    ):
        """afplay is handed a temp file with the audio, removed afterwards."""
        _play_audio("afplay", b"wav-bytes", suffix=".wav")

        assert stub_file_player.copy_path.read_bytes() == b"wav-bytes"
        assert str(stub_file_player.arg_path).endswith(".wav")
        # The temp file itself is gone once playback finishes.
        assert not Path(stub_file_player.arg_path).exists()

    def test_play_audio_file_player_raises_on_failure(self, failing_file_player):
        with pytest.raises(click.ClickException, match="exited with status 4"):
            _play_audio("afplay", b"wav-bytes", suffix=".wav")

    def test_player_stdin_kills_the_player_when_the_body_raises(self, tmp_path):
        """A failure mid-stream must not leave the player running."""
        script = tmp_path / "sleeper.py"
        script.write_text("import sys\nsys.stdin.buffer.read()\n")
        with (
            patch.dict(_STDIN_PLAYER_ARGV, {"ffplay": [sys.executable, str(script)]}),
            pytest.raises(RuntimeError),
            _player_stdin("ffplay"),
        ):
            raise RuntimeError("stream died")


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
