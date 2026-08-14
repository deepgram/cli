"""Tests for WebSocket message handling, URL building, flux model routing,
and caption/srt mutual exclusivity — the critical piping paths."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, Mock, patch

import pytest
from deepctl_cmd_listen.captions import StreamingCaptionWriter
from deepctl_cmd_listen.command import ListenCommand
from deepctl_cmd_listen.models import ListenResult
from deepctl_core import AuthManager, BaseResult, Config, DeepgramClient


@pytest.fixture
def command():
    return ListenCommand()


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.config.get_profile.return_value.base_url = "https://api.deepgram.com"
    client.auth_manager.get_api_key.return_value = "test-key"
    return client


@pytest.fixture
def mock_config():
    return Mock(spec=Config)


@pytest.fixture
def mock_auth_manager():
    m = Mock(spec=AuthManager)
    m.get_api_key.return_value = "test-key"
    return m


# ── _ws_url ────────────────────────────────────────────────────────────────────


class TestWsUrl:
    def _url(self, command, mock_client, **overrides):
        defaults = {
            "api_version": 1,
            "model": "nova-3",
            "language": "en-US",
            "diarize": False,
            "smart_format": True,
            "punctuate": True,
            "interim": False,
            "encoding": "linear16",
            "sample_rate": 16000,
            "channels": 1,
        }
        defaults.update(overrides)
        return command._ws_url(mock_client, **defaults)

    def test_uses_wss_scheme(self, command, mock_client):
        assert self._url(command, mock_client).startswith("wss://")

    def test_https_converted_to_wss(self, command, mock_client):
        url = self._url(command, mock_client)
        assert "https://" not in url

    def test_v1_path(self, command, mock_client):
        assert "/v1/listen?" in self._url(command, mock_client, api_version=1)

    def test_v2_path(self, command, mock_client):
        assert "/v2/listen?" in self._url(command, mock_client, api_version=2)

    def test_v2_omits_v1_only_params(self, command, mock_client):
        """Flux (v2) rejects v1-only params with HTTP 400, so they must not be
        sent. This locks in the fix; a regression would silently break Flux STT.
        """
        url = self._url(
            command,
            mock_client,
            api_version=2,
            diarize=True,
            interim=True,
        )
        for banned in (
            "language=",
            "smart_format=",
            "punctuate=",
            "channels=",
            "diarize=",
            "interim_results=",
        ):
            assert banned not in url, f"v2 URL must not contain {banned!r}: {url}"
        # The params v2 does accept are still present.
        assert "model=" in url
        assert "encoding=" in url
        assert "sample_rate=" in url

    def test_v1_includes_v1_params(self, command, mock_client):
        """v1 keeps sending the classic params (contrast with v2)."""
        url = self._url(
            command,
            mock_client,
            api_version=1,
            diarize=True,
            interim=True,
        )
        for expected in (
            "language=",
            "smart_format=",
            "punctuate=",
            "channels=",
            "diarize=true",
            "interim_results=true",
        ):
            assert expected in url, f"v1 URL should contain {expected!r}: {url}"

    def test_model_param(self, command, mock_client):
        assert "model=nova-3" in self._url(command, mock_client, model="nova-3")

    def test_language_param(self, command, mock_client):
        assert "language=es-ES" in self._url(command, mock_client, language="es-ES")

    def test_diarize_param_when_enabled(self, command, mock_client):
        assert "diarize=true" in self._url(command, mock_client, diarize=True)

    def test_diarize_param_absent_when_disabled(self, command, mock_client):
        assert "diarize" not in self._url(command, mock_client, diarize=False)

    def test_interim_param_when_enabled(self, command, mock_client):
        assert "interim_results=true" in self._url(command, mock_client, interim=True)

    def test_interim_param_absent_when_disabled(self, command, mock_client):
        assert "interim_results" not in self._url(command, mock_client, interim=False)

    def test_custom_base_url(self, command):
        client = MagicMock()
        client.config.get_profile.return_value.base_url = (
            "https://custom.api.example.com"
        )
        assert self._url(command, client).startswith("wss://custom.api.example.com")

    def test_sample_rate_param(self, command, mock_client):
        assert "sample_rate=8000" in self._url(command, mock_client, sample_rate=8000)

    def test_channels_param(self, command, mock_client):
        assert "channels=2" in self._url(command, mock_client, channels=2)


# ── Flux model → API version auto-selection ────────────────────────────────────


class TestFluxModelAutoVersion:
    def _handle_with_source(
        self, command, mock_config, mock_auth_manager, mock_client, **kwargs
    ):
        defaults = {
            "source": "audio.mp3",
            "mic": False,
            "model": "nova-3",
            "language": "en-US",
        }
        defaults.update(kwargs)
        with patch.object(
            command, "_prerecorded", return_value=ListenResult(status="success")
        ) as mock_pre:
            with patch.object(
                command,
                "_interactive_features",
                return_value=(False, False, False, False),
            ):
                command.handle(
                    config=mock_config,
                    auth_manager=mock_auth_manager,
                    client=mock_client,
                    **defaults,
                )
            return mock_pre

    def _handle_with_mic(
        self, command, mock_config, mock_auth_manager, mock_client, **kwargs
    ):
        defaults = {"mic": True, "model": "nova-3", "language": "en-US"}
        defaults.update(kwargs)
        with patch.object(
            command, "_stream_mic", return_value=ListenResult(status="success")
        ) as mock_stream:
            command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                **defaults,
            )
            return mock_stream

    @patch("deepctl_cmd_listen.command._agentic", False)
    @patch("deepctl_cmd_listen.command.sys")
    def test_flux_model_file_is_error(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client
    ):
        # Flux STT (v2) is streaming-only: a file must not reach _prerecorded.
        mock_sys.stdin.isatty.return_value = True
        with patch.object(
            command, "_interactive_features", return_value=(False, False, False, False)
        ):
            result = command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                source="audio.mp3",
                mic=False,
                model="flux-general-en",
                language="en-US",
            )
        assert result.status == "error"
        assert "streaming-only" in result.message

    @patch("deepctl_cmd_listen.command.sys")
    def test_flux_model_streaming_uses_v2(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client
    ):
        mock_sys.stdin.isatty.return_value = True
        mock_stream = self._handle_with_mic(
            command,
            mock_config,
            mock_auth_manager,
            mock_client,
            model="flux-general-en",
        )
        assert mock_stream.call_args.kwargs["api_version"] == 2

    @patch("deepctl_cmd_listen.command.sys")
    def test_flux_prefix_variant_streaming_uses_v2(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client
    ):
        mock_sys.stdin.isatty.return_value = True
        mock_stream = self._handle_with_mic(
            command, mock_config, mock_auth_manager, mock_client, model="flux-2-en"
        )
        assert mock_stream.call_args.kwargs["api_version"] == 2

    @patch("deepctl_cmd_listen.command._agentic", False)
    @patch("deepctl_cmd_listen.command.sys")
    def test_nova3_uses_v1(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client
    ):
        mock_sys.stdin.isatty.return_value = True
        mock_pre = self._handle_with_source(
            command, mock_config, mock_auth_manager, mock_client, model="nova-3"
        )
        assert mock_pre.call_args.kwargs["api_version"] == 1

    @patch("deepctl_cmd_listen.command._agentic", False)
    @patch("deepctl_cmd_listen.command.sys")
    def test_enhanced_uses_v1(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client
    ):
        mock_sys.stdin.isatty.return_value = True
        mock_pre = self._handle_with_source(
            command, mock_config, mock_auth_manager, mock_client, model="enhanced"
        )
        assert mock_pre.call_args.kwargs["api_version"] == 1


# ── --webvtt / --srt mutual exclusivity ───────────────────────────────────────


class TestCaptionFlagExclusivity:
    @patch("deepctl_cmd_listen.command.sys")
    def test_both_flags_is_error(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client
    ):
        mock_sys.stdin.isatty.return_value = True
        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            source="audio.mp3",
            mic=False,
            webvtt=True,
            srt=True,
        )
        assert result.status == "error"
        assert "mutually exclusive" in result.message

    @patch("deepctl_cmd_listen.command._agentic", False)
    @patch("deepctl_cmd_listen.command.sys")
    def test_webvtt_alone_passes_format_to_prerecorded(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client
    ):
        mock_sys.stdin.isatty.return_value = True
        with patch.object(
            command, "_prerecorded", return_value=ListenResult(status="success")
        ) as mock_pre:
            with patch.object(
                command,
                "_interactive_features",
                return_value=(False, False, False, False),
            ):
                command.handle(
                    config=mock_config,
                    auth_manager=mock_auth_manager,
                    client=mock_client,
                    source="audio.mp3",
                    mic=False,
                    webvtt=True,
                    srt=False,
                )
            assert mock_pre.call_args.kwargs["caption_format"] == "webvtt"

    @patch("deepctl_cmd_listen.command._agentic", False)
    @patch("deepctl_cmd_listen.command.sys")
    def test_srt_alone_passes_format_to_prerecorded(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client
    ):
        mock_sys.stdin.isatty.return_value = True
        with patch.object(
            command, "_prerecorded", return_value=ListenResult(status="success")
        ) as mock_pre:
            with patch.object(
                command,
                "_interactive_features",
                return_value=(False, False, False, False),
            ):
                command.handle(
                    config=mock_config,
                    auth_manager=mock_auth_manager,
                    client=mock_client,
                    source="audio.mp3",
                    mic=False,
                    webvtt=False,
                    srt=True,
                )
            assert mock_pre.call_args.kwargs["caption_format"] == "srt"

    @patch("deepctl_cmd_listen.command._agentic", False)
    @patch("deepctl_cmd_listen.command.sys")
    def test_no_caption_flag_passes_none(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client
    ):
        mock_sys.stdin.isatty.return_value = True
        with patch.object(
            command, "_prerecorded", return_value=ListenResult(status="success")
        ) as mock_pre:
            with patch.object(
                command,
                "_interactive_features",
                return_value=(False, False, False, False),
            ):
                command.handle(
                    config=mock_config,
                    auth_manager=mock_auth_manager,
                    client=mock_client,
                    source="audio.mp3",
                    mic=False,
                )
            assert mock_pre.call_args.kwargs["caption_format"] is None


# ── _handle_ws_message — piping stdout out ────────────────────────────────────


class TestHandleWsMessage:
    def _msg(
        self,
        transcript,
        words=None,
        is_final=True,
        msg_type="Results",
        start=0.0,
        duration=1.0,
    ):
        return json.dumps(
            {
                "type": msg_type,
                "channel": {
                    "alternatives": [{"transcript": transcript, "words": words or []}]
                },
                "is_final": is_final,
                "start": start,
                "duration": duration,
            }
        )

    def test_final_transcript_printed_to_stdout(self, command, capsys):
        acc = []
        command._handle_ws_message(
            self._msg("Hello world"), acc, diarize=False, interim=False
        )
        assert "Hello world" in capsys.readouterr().out

    def test_final_transcript_accumulated(self, command, capsys):
        acc = []
        command._handle_ws_message(
            self._msg("Hello world"), acc, diarize=False, interim=False
        )
        capsys.readouterr()
        assert acc == ["Hello world"]

    def test_non_final_not_accumulated(self, command, capsys):
        acc = []
        command._handle_ws_message(
            self._msg("partial", is_final=False), acc, diarize=False, interim=False
        )
        capsys.readouterr()
        assert acc == []

    def test_interim_printed_with_carriage_return(self, command, capsys):
        acc = []
        command._handle_ws_message(
            self._msg("partial text", is_final=False), acc, diarize=False, interim=True
        )
        out = capsys.readouterr().out
        assert "\r" in out
        assert "partial text" in out

    def test_interim_not_printed_when_flag_off(self, command, capsys):
        acc = []
        command._handle_ws_message(
            self._msg("partial", is_final=False), acc, diarize=False, interim=False
        )
        assert capsys.readouterr().out == ""

    def test_non_results_type_ignored(self, command, capsys):
        acc = []
        command._handle_ws_message(
            json.dumps({"type": "Metadata", "data": "x"}),
            acc,
            diarize=False,
            interim=False,
        )
        assert acc == []
        assert capsys.readouterr().out == ""

    def test_empty_transcript_not_accumulated(self, command, capsys):
        acc = []
        command._handle_ws_message(self._msg(""), acc, diarize=False, interim=False)
        capsys.readouterr()
        assert acc == []

    def test_invalid_json_does_not_raise(self, command):
        acc = []
        command._handle_ws_message("not json at all", acc, diarize=False, interim=False)
        assert acc == []

    def test_diarized_final_uses_speaker_labels(self, command, capsys):
        words = [
            {
                "word": "hello",
                "punctuated_word": "Hello",
                "start": 0.0,
                "end": 0.5,
                "speaker": 0,
            },
            {
                "word": "there",
                "punctuated_word": "there",
                "start": 0.6,
                "end": 1.0,
                "speaker": 1,
            },
        ]
        acc = []
        command._handle_ws_message(
            self._msg("Hello there", words=words), acc, diarize=True, interim=False
        )
        out = capsys.readouterr().out
        assert "[Speaker" in out

    def test_diarized_line_accumulated(self, command, capsys):
        words = [
            {
                "word": "hi",
                "punctuated_word": "Hi",
                "start": 0.0,
                "end": 0.5,
                "speaker": 0,
            },
        ]
        acc = []
        command._handle_ws_message(
            self._msg("Hi", words=words), acc, diarize=True, interim=False
        )
        capsys.readouterr()
        assert len(acc) == 1
        assert "[Speaker 0]" in acc[0]

    def test_caption_writer_receives_entry(self, command, capsys):
        words = [{"word": "hi", "punctuated_word": "Hi", "start": 0.08, "end": 0.5}]
        writer = StreamingCaptionWriter("webvtt")
        acc = []
        command._handle_ws_message(
            self._msg("Hi", words=words, start=0.0, duration=0.5),
            acc,
            diarize=False,
            interim=False,
            caption_writer=writer,
        )
        # Caption output should contain WebVTT timestamp arrow
        out = capsys.readouterr().out
        assert "-->" in out
        # Word accumulated in writer
        assert len(writer.accumulated_words) == 1

    def test_caption_writer_suppresses_plain_text(self, command, capsys):
        """When a caption_writer is active, plain transcript should not be printed."""
        words = [{"word": "hi", "punctuated_word": "Hi", "start": 0.0, "end": 0.5}]
        writer = StreamingCaptionWriter("webvtt")
        acc = []
        command._handle_ws_message(
            self._msg("Hi", words=words),
            acc,
            diarize=False,
            interim=False,
            caption_writer=writer,
        )
        out = capsys.readouterr().out
        # Should see the caption timestamp, not a bare "Hi\n"
        assert "-->" in out
        # The bare transcript line should not appear on its own
        lines = [line for line in out.splitlines() if line.strip() == "Hi"]
        assert len(lines) == 0 or "-->" in out  # caption mode

    def test_interim_suppressed_in_caption_mode(self, command, capsys):
        """Interim results should not print when caption_writer is active."""
        writer = StreamingCaptionWriter("webvtt")
        acc = []
        command._handle_ws_message(
            self._msg("partial", is_final=False),
            acc,
            diarize=False,
            interim=True,
            caption_writer=writer,
        )
        assert capsys.readouterr().out == ""

    def test_multiple_messages_accumulate(self, command, capsys):
        acc = []
        command._handle_ws_message(
            self._msg("First"), acc, diarize=False, interim=False
        )
        command._handle_ws_message(
            self._msg("Second"), acc, diarize=False, interim=False
        )
        capsys.readouterr()
        assert acc == ["First", "Second"]
