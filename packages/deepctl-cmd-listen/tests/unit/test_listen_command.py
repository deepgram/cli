"""Tests for the unified listen command."""

from unittest.mock import Mock, patch

import pytest
from deepctl_cmd_listen.command import ListenCommand
from deepctl_cmd_listen.models import ListenResult
from deepctl_core import AuthManager, BaseResult, Config, DeepgramClient


class TestListenCommand:
    """Test ListenCommand routing and argument configuration."""

    @pytest.fixture
    def command(self):
        return ListenCommand()

    @pytest.fixture
    def mock_config(self):
        return Mock(spec=Config)

    @pytest.fixture
    def mock_auth_manager(self):
        manager = Mock(spec=AuthManager)
        manager.get_api_key.return_value = "test-api-key"
        return manager

    @pytest.fixture
    def mock_client(self):
        return Mock(spec=DeepgramClient)

    def test_command_properties(self, command):
        assert command.name == "listen"
        assert command.requires_auth is True
        assert command.requires_project is False
        assert command.ci_friendly is True

    def test_get_arguments(self, command):
        args = command.get_arguments()

        # One optional positional arg (source) plus many options
        positional = [a for a in args if not a.get("is_option", False)]
        assert len(positional) == 1
        assert positional[0]["name"] == "source"
        assert positional[0]["required"] is False

        option_names: list[str] = []
        for arg in args:
            if arg.get("is_option", False):
                option_names.extend(arg["names"])

        for expected in [
            "--mic",
            "--model",
            "-m",
            "--language",
            "-l",
            "--diarize",
            "--smart-format",
            "--punctuate",
            "--summarize",
            "--topics",
            "--sentiment",
            "--redact",
            "--numerals",
            "--interim",
            "--encoding",
            "--sample-rate",
            "--channels",
            "--save-to",
            "-s",
            "--probe",
            "--no-validate",
            "--webvtt",
            "--srt",
        ]:
            assert expected in option_names, f"Missing option: {expected}"

    @patch("deepctl_cmd_listen.command._agentic", True)
    @patch("deepctl_cmd_listen.command.sys")
    def test_handle_no_source_error_in_agent_mode(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client
    ):
        """In agent mode with no source, return an error instead of prompting."""
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
        """--mic without sounddevice installed returns an error."""
        mock_sys.stdin.isatty.return_value = True

        original_import = (
            __builtins__.__import__
            if hasattr(__builtins__, "__import__")
            else __import__
        )

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

        assert result.status == "error"
        assert "sounddevice" in result.message

    @patch("deepctl_cmd_listen.command.sys")
    def test_handle_stdin_routes_to_stream_stdin(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client
    ):
        """Piped stdin (isatty=False, no --mic) routes to _stream_stdin."""
        mock_sys.stdin.isatty.return_value = False
        expected = ListenResult(status="success", source="stdin", mode="live")

        with patch.object(
            command, "_stream_stdin", return_value=expected
        ) as mock_stream:
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

            mock_stream.assert_called_once()
            call_kwargs = mock_stream.call_args.kwargs
            assert call_kwargs["model"] == "nova-3"
            assert call_kwargs["encoding"] == "linear16"
            assert result.source == "stdin"

    @patch("deepctl_cmd_listen.command.status")
    @patch("deepctl_cmd_listen.command.sys")
    def test_handle_warns_diarize_ignored_on_flux(
        self,
        mock_sys,
        mock_status,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
    ):
        """--diarize on a Flux STT (v2) model warns instead of vanishing silently."""
        mock_sys.stdin.isatty.return_value = True
        expected = ListenResult(status="success", source="mic", mode="live")

        with patch.object(command, "_stream_mic", return_value=expected):
            command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                mic=True,
                model="flux-general-en",
                diarize=True,
            )

        printed = " ".join(
            str(c.args[0]) for c in mock_status.print.call_args_list if c.args
        )
        assert "not supported by Flux STT" in printed

    @patch("deepctl_cmd_listen.command.status")
    @patch("deepctl_cmd_listen.command.sys")
    def test_handle_no_diarize_warning_on_v1(
        self,
        mock_sys,
        mock_status,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
    ):
        """v1 models keep diarization — no spurious warning."""
        mock_sys.stdin.isatty.return_value = True
        expected = ListenResult(status="success", source="mic", mode="live")

        with patch.object(command, "_stream_mic", return_value=expected):
            command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                mic=True,
                model="nova-3",
                diarize=True,
            )

        printed = " ".join(
            str(c.args[0]) for c in mock_status.print.call_args_list if c.args
        )
        assert "not supported by Flux STT" not in printed

    def test_handle_flux_prerecorded_file_errors(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """Flux STT + a file errors up front (v2 is streaming-only)."""
        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            source="call.wav",
            model="flux-general-en",
        )
        assert result.status == "error"
        assert "streaming-only" in result.message

    def test_handle_flux_invalid_redact_errors(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """A v1-only --redact value on Flux STT errors instead of a raw 400."""
        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            mic=True,
            model="flux-general-en",
            redact="pci",
        )
        assert result.status == "error"
        assert "pci" in result.message
        assert "aggressive_numbers" in result.message

    @patch("deepctl_cmd_listen.command.sys")
    def test_handle_mic_routes_to_stream_mic(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client
    ):
        """--mic routes to _stream_mic."""
        mock_sys.stdin.isatty.return_value = True
        expected = ListenResult(status="success", source="mic", mode="live")

        with patch.object(command, "_stream_mic", return_value=expected) as mock_stream:
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

            mock_stream.assert_called_once()
            call_kwargs = mock_stream.call_args.kwargs
            assert call_kwargs["model"] == "nova-3"
            assert result.source == "mic"

    @patch("deepctl_cmd_listen.command._agentic", False)
    @patch("deepctl_cmd_listen.command.sys")
    def test_handle_file_source_routes_to_prerecorded(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client
    ):
        """A file path routes to _prerecorded with is_url=False."""
        mock_sys.stdin.isatty.return_value = True
        expected = ListenResult(
            status="success",
            source="file",
            mode="prerecorded",
            transcript="hello world",
        )

        with patch.object(command, "_prerecorded", return_value=expected) as mock_pre:
            # Skip interactive feature selection
            with patch.object(
                command,
                "_interactive_features",
                return_value=(False, False, False, False),
            ):
                result = command.handle(
                    config=mock_config,
                    auth_manager=mock_auth_manager,
                    client=mock_client,
                    source="audio.mp3",
                    mic=False,
                    model="nova-3",
                    language="en-US",
                )

            mock_pre.assert_called_once()
            call_kwargs = mock_pre.call_args.kwargs
            assert call_kwargs["is_url"] is False
            assert result.source == "file"

    @patch("deepctl_cmd_listen.command._agentic", False)
    @patch("deepctl_cmd_listen.command.sys")
    def test_handle_passes_redact_and_numerals_to_prerecorded(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client
    ):
        """--redact / --numerals reach _prerecorded."""
        mock_sys.stdin.isatty.return_value = True
        expected = ListenResult(status="success", source="file", mode="prerecorded")

        with patch.object(command, "_prerecorded", return_value=expected) as mock_pre:
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
                    model="nova-3",
                    language="en-US",
                    redact=("numbers",),
                    numerals=True,
                )

            call_kwargs = mock_pre.call_args.kwargs
            assert call_kwargs["redact"] == ("numbers",)
            assert call_kwargs["numerals"] is True

    @patch("deepctl_cmd_listen.command._agentic", False)
    @patch("deepctl_cmd_listen.command.sys")
    def test_handle_passes_multiple_redact_to_prerecorded(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client
    ):
        """A repeated --redact (v1) reaches _prerecorded as a tuple of values."""
        mock_sys.stdin.isatty.return_value = True
        expected = ListenResult(status="success", source="file", mode="prerecorded")

        with patch.object(command, "_prerecorded", return_value=expected) as mock_pre:
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
                    model="nova-3",
                    language="en-US",
                    redact=("pci", "numbers"),
                )

            assert mock_pre.call_args.kwargs["redact"] == ("pci", "numbers")

    def test_prerecorded_builds_multi_redact_and_numerals_options(
        self, command, mock_config
    ):
        """_prerecorded sends redact as a LIST (so Fern repeats the query param)
        and numerals as the string 'true'."""
        client = Mock()
        client.transcribe_file.return_value = {
            "results": {"channels": [{"alternatives": [{"transcript": "hi"}]}]}
        }
        result = command._prerecorded(
            client,
            "audio.wav",
            is_url=False,
            model="nova-3",
            language="en-US",
            api_version=1,
            diarize=False,
            smart_format=True,
            punctuate=True,
            summarize=False,
            topics=False,
            sentiment=False,
            redact=("pci", "numbers"),
            numerals=True,
            save_to=None,
            probe=False,
            no_validate=True,
            caption_format=None,
            config=mock_config,
        )

        assert result.status == "success"
        opts = client.transcribe_file.call_args.args[1]
        assert opts["redact"] == ["pci", "numbers"]  # list, not tuple
        assert opts["numerals"] == "true"

    def test_ws_url_expands_multiple_redact(self, command):
        """Repeated redact values expand to repeated query params (doseq)."""
        ws_client = Mock()
        ws_client.config.get_profile.return_value = Mock(
            base_url="https://api.deepgram.com"
        )
        url = command._ws_url(
            ws_client,
            api_version=1,
            model="nova-3",
            language="en-US",
            diarize=False,
            smart_format=True,
            punctuate=True,
            interim=False,
            encoding="linear16",
            sample_rate=16000,
            channels=1,
            redact=("pci", "numbers"),
        )
        assert "redact=pci" in url
        assert "redact=numbers" in url

    def test_ws_url_includes_redact_and_numerals(self, command):
        """redact / numerals become query params on the streaming URL."""
        ws_client = Mock()
        ws_client.config.get_profile.return_value = Mock(
            base_url="https://api.deepgram.com"
        )

        url = command._ws_url(
            ws_client,
            api_version=2,
            model="flux-general-en",
            language="en-US",
            diarize=False,
            smart_format=True,
            punctuate=True,
            interim=False,
            encoding="linear16",
            sample_rate=16000,
            channels=1,
            redact="aggressive_numbers",
            numerals=True,
        )

        assert url.startswith("wss://api.deepgram.com/v2/listen?")
        assert "redact=aggressive_numbers" in url
        assert "numerals=true" in url

    def test_ws_url_omits_redact_and_numerals_when_unset(self, command):
        """Unset redact / numerals are not sent (defaults)."""
        ws_client = Mock()
        ws_client.config.get_profile.return_value = Mock(
            base_url="https://api.deepgram.com"
        )

        url = command._ws_url(
            ws_client,
            api_version=1,
            model="nova-3",
            language="en-US",
            diarize=False,
            smart_format=True,
            punctuate=True,
            interim=False,
            encoding="linear16",
            sample_rate=16000,
            channels=1,
        )

        assert "redact=" not in url
        assert "numerals=" not in url

    @patch("deepctl_cmd_listen.command._agentic", False)
    @patch("deepctl_cmd_listen.command.sys")
    def test_handle_url_source_routes_to_prerecorded(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client
    ):
        """A URL routes to _prerecorded with is_url=True."""
        mock_sys.stdin.isatty.return_value = True
        expected = ListenResult(
            status="success",
            source="url",
            mode="prerecorded",
            transcript="hello",
        )

        with patch.object(command, "_prerecorded", return_value=expected) as mock_pre:
            with patch.object(
                command,
                "_interactive_features",
                return_value=(False, False, False, False),
            ):
                result = command.handle(
                    config=mock_config,
                    auth_manager=mock_auth_manager,
                    client=mock_client,
                    source="https://example.com/audio.mp3",
                    mic=False,
                    model="nova-3",
                    language="en-US",
                )

            mock_pre.assert_called_once()
            call_kwargs = mock_pre.call_args.kwargs
            assert call_kwargs["is_url"] is True
            assert result.source == "url"

    @patch("deepctl_cmd_listen.command.sys")
    def test_handle_explicit_stdin_dash(
        self, mock_sys, command, mock_config, mock_auth_manager, mock_client
    ):
        """source='-' forces stream_stdin mode regardless of isatty."""
        mock_sys.stdin.isatty.return_value = True  # would normally trigger interactive
        expected = ListenResult(status="success", source="stdin", mode="live")

        with patch.object(
            command, "_stream_stdin", return_value=expected
        ) as mock_stream:
            result = command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                source="-",
                mic=False,
            )

            mock_stream.assert_called_once()
            assert result.source == "stdin"


class TestGuidedFlow:
    """Verify the guided-flow gate: prompts only fire on bare `dg listen`."""

    @pytest.fixture
    def command(self):
        return ListenCommand()

    @pytest.fixture
    def common_kwargs(self):
        return {
            "client": Mock(spec=DeepgramClient),
            "config": Mock(spec=Config),
            "auth_manager": Mock(spec=AuthManager),
        }

    def test_url_arg_skips_both_prompts(self, command, common_kwargs):
        with (
            patch.object(command, "_interactive_features") as feat,
            patch.object(command, "_interactive_select_source") as src,
            patch.object(command, "_prerecorded", return_value=BaseResult(status="ok")),
        ):
            command.handle(**common_kwargs, source="https://example.com/audio.wav")
        assert feat.call_count == 0
        assert src.call_count == 0

    def test_file_arg_skips_both_prompts(self, command, common_kwargs):
        with (
            patch.object(command, "_interactive_features") as feat,
            patch.object(command, "_interactive_select_source") as src,
            patch.object(command, "_prerecorded", return_value=BaseResult(status="ok")),
        ):
            command.handle(**common_kwargs, source="/tmp/audio.wav")
        assert feat.call_count == 0
        assert src.call_count == 0

    def test_url_arg_with_diarize_skips_both_prompts(self, command, common_kwargs):
        with (
            patch.object(command, "_interactive_features") as feat,
            patch.object(command, "_interactive_select_source") as src,
            patch.object(command, "_prerecorded", return_value=BaseResult(status="ok")),
        ):
            command.handle(
                **common_kwargs,
                source="https://example.com/audio.wav",
                diarize=True,
            )
        assert feat.call_count == 0
        assert src.call_count == 0

    def test_bare_invocation_runs_full_guided_flow(self, command, common_kwargs):
        with (
            patch.object(
                command,
                "_interactive_features",
                return_value=(False, False, False, False),
            ) as feat,
            patch.object(
                command,
                "_interactive_select_source",
                return_value=("prerecorded_url", "https://x.com/a.wav"),
            ) as src,
            patch.object(command, "_prerecorded", return_value=BaseResult(status="ok")),
            patch("sys.stdin") as mock_stdin,
            patch("deepctl_cmd_listen.command._agentic", False),
        ):
            mock_stdin.isatty.return_value = True
            command.handle(**common_kwargs)
        assert src.call_count == 1
        assert feat.call_count == 1

    def test_cancelled_source_select_returns_cancelled(self, command, common_kwargs):
        with (
            patch.object(
                command, "_interactive_select_source", return_value=(None, None)
            ),
            patch.object(command, "_interactive_features") as feat,
            patch("sys.stdin") as mock_stdin,
            patch("deepctl_cmd_listen.command._agentic", False),
        ):
            mock_stdin.isatty.return_value = True
            result = command.handle(**common_kwargs)
        assert result.status == "cancelled"
        assert feat.call_count == 0


class TestListenResult:
    """Test ListenResult model fields and defaults."""

    def test_create_listen_result(self):
        result = ListenResult(
            status="success",
            message="done",
            transcript="Hello world",
            duration_seconds=5.2,
            source="mic",
            mode="live",
        )
        assert result.status == "success"
        assert result.transcript == "Hello world"
        assert result.duration_seconds == 5.2
        assert result.source == "mic"
        assert result.mode == "live"

    def test_listen_result_defaults(self):
        result = ListenResult()
        assert result.status == "success"
        assert result.transcript == ""
        assert result.duration_seconds == 0.0
        assert result.source == ""
        assert result.mode == ""
        assert result.diarized is False
        assert result.full_result is None


class TestFluxV2TurnHandling:
    """Flux (listen v2) TurnInfo parsing and finalization."""

    @pytest.fixture
    def command(self):
        return ListenCommand()

    def _turn(self, event, transcript, *, turn_index=0, words=None, window=(0.0, 0.0)):
        import json as _json

        return _json.dumps(
            {
                "type": "TurnInfo",
                "event": event,
                "turn_index": turn_index,
                "transcript": transcript,
                "words": words or [],
                "audio_window_start": window[0],
                "audio_window_end": window[1],
            }
        )

    def test_end_of_turn_finalizes_transcript(self, command, capsys):
        acc: list[str] = []
        state = command._new_v2_state()

        command._handle_ws_message(
            self._turn("Update", "hello"),
            acc,
            diarize=False,
            interim=False,
            v2_state=state,
        )
        # Update alone does not finalize.
        assert acc == []

        command._handle_ws_message(
            self._turn("EndOfTurn", "hello world"),
            acc,
            diarize=False,
            interim=False,
            v2_state=state,
        )
        assert acc == ["hello world"]
        assert "hello world" in capsys.readouterr().out

    def test_flush_emits_unfinalized_final_turn(self, command, capsys):
        """A stream that closes mid-turn still yields the latest transcript."""
        acc: list[str] = []
        state = command._new_v2_state()

        # Turn grows across Updates but never gets an EndOfTurn.
        for text in ("my", "my account", "my account number"):
            command._handle_ws_message(
                self._turn("Update", text),
                acc,
                diarize=False,
                interim=False,
                v2_state=state,
            )
        assert acc == []  # nothing finalized yet

        command._flush_v2(state, acc)
        assert acc == ["my account number"]

    def test_flush_does_not_double_emit_finalized_turn(self, command):
        acc: list[str] = []
        state = command._new_v2_state()

        command._handle_ws_message(
            self._turn("EndOfTurn", "done"),
            acc,
            diarize=False,
            interim=False,
            v2_state=state,
        )
        command._flush_v2(state, acc)
        assert acc == ["done"]  # not duplicated

    def test_multiple_turns_accumulate_in_order(self, command):
        acc: list[str] = []
        state = command._new_v2_state()

        command._handle_ws_message(
            self._turn("EndOfTurn", "first turn", turn_index=0),
            acc,
            diarize=False,
            interim=False,
            v2_state=state,
        )
        command._handle_ws_message(
            self._turn("Update", "second turn", turn_index=1),
            acc,
            diarize=False,
            interim=False,
            v2_state=state,
        )
        command._flush_v2(state, acc)
        assert acc == ["first turn", "second turn"]

    def test_turninfo_ignored_without_state(self, command):
        """A v2 message with no state (v1 caller) is a no-op, not a crash."""
        acc: list[str] = []
        command._handle_ws_message(
            self._turn("EndOfTurn", "ignored"),
            acc,
            diarize=False,
            interim=False,
            v2_state=None,
        )
        assert acc == []

    def test_captions_use_audio_window_and_survive_batch_save(self, command):
        """Flux words lack per-word timings: captions must key off the turn's
        audio window, and the end-of-stream batch save must not KeyError."""
        from deepctl_cmd_listen.captions import (
            StreamingCaptionWriter,
            captions_from_words,
        )

        writer = StreamingCaptionWriter("srt")
        acc: list[str] = []
        state = command._new_v2_state()

        # TurnInfo words carry only {word, confidence} — no start/end.
        words = [
            {"word": "my", "confidence": 0.9},
            {"word": "account", "confidence": 0.9},
        ]
        command._handle_ws_message(
            self._turn("EndOfTurn", "my account", words=words, window=(1.0, 2.5)),
            acc,
            diarize=False,
            interim=False,
            v2_state=state,
            caption_writer=writer,
        )

        assert acc == ["my account"]
        # Live cue span comes from the audio window, not 00:00:00.
        assert writer.accumulated_words  # words were captured for batch save
        # The batch save path used to raise KeyError: 'start' on Flux words.
        batch = captions_from_words(writer.accumulated_words, "srt")
        assert "my account" in batch
        assert "00:00:01,000 --> 00:00:02,500" in batch

    def test_ws_mic_flushes_final_turn_on_keyboard_interrupt(
        self, command, monkeypatch
    ):
        """Ctrl-C during a Flux mic stream must still flush the in-flight turn.

        The interrupt surfaces at the ``asyncio.gather`` await; ``_ws_mic`` has
        to catch it, run ``_flush_v2``, and return the accumulated transcript
        (rather than letting the final turn vanish)."""
        import asyncio as _asyncio
        import sys
        import types
        from unittest.mock import MagicMock

        # _ws_mic imports these at call time; stub them so no hardware/net is hit.
        for name in ("sounddevice", "numpy"):
            monkeypatch.setitem(sys.modules, name, types.ModuleType(name))

        class _FakeWS:
            async def send(self, *a):
                return None

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

        class _FakeConnect:
            async def __aenter__(self):
                return _FakeWS()

            async def __aexit__(self, *a):
                return False

        fake_ws = types.ModuleType("websockets")
        fake_ws.connect = lambda *a, **k: _FakeConnect()
        monkeypatch.setitem(sys.modules, "websockets", fake_ws)

        # A turn received but never finalized (no EndOfTurn before the interrupt).
        seeded = {
            "turns": {
                0: {
                    "transcript": "hello world",
                    "words": [],
                    "final": False,
                    "start": 0.0,
                    "end": 1.0,
                }
            },
            "order": [0],
        }
        monkeypatch.setattr(command, "_new_v2_state", lambda: seeded)

        async def _interrupt(*a, **k):
            raise KeyboardInterrupt

        monkeypatch.setattr("deepctl_cmd_listen.command.asyncio.gather", _interrupt)

        client = MagicMock()
        client.config.get_profile.return_value.base_url = "https://api.deepgram.com"
        client.auth_manager.get_api_key.return_value = "k"

        # Drive on a plain loop (not asyncio.run) so the patched gather can't
        # interfere with run()'s own shutdown machinery.
        loop = _asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                command._ws_mic(
                    client,
                    model="flux-general-en",
                    language="en-US",
                    api_version=2,
                    diarize=False,
                    smart_format=True,
                    punctuate=True,
                    interim=False,
                    redact=(),
                    numerals=False,
                    sample_rate=16000,
                    channels=1,
                )
            )
        finally:
            loop.close()

        assert result.transcript == "hello world"

    def test_end_of_turn_with_empty_transcript_is_noop(self, command, capsys):
        """An EndOfTurn that never carried text emits nothing (no blank line)."""
        acc: list[str] = []
        state = command._new_v2_state()
        command._handle_ws_message(
            self._turn("EndOfTurn", ""),
            acc,
            diarize=False,
            interim=False,
            v2_state=state,
        )
        assert acc == []
        assert capsys.readouterr().out == ""

    def test_v2_update_prints_interim(self, command, capsys):
        """An Update event with --interim shows a carriage-return partial."""
        acc: list[str] = []
        state = command._new_v2_state()
        command._handle_ws_message(
            self._turn("Update", "partial text"),
            acc,
            diarize=False,
            interim=True,
            v2_state=state,
        )
        out = capsys.readouterr().out
        assert "partial text" in out
        assert "\r" in out
        assert acc == []  # interim never accumulates

    def test_timed_v2_words_backfills_and_preserves(self, command):
        """_timed_v2_words: synthesize when empty, spread the window when words
        lack timings, and pass real per-word timings through untouched."""
        # No words → one synthetic word spanning the whole turn window.
        assert command._timed_v2_words([], "hello world", 1.0, 2.0) == [
            {"word": "hello world", "start": 1.0, "end": 2.0}
        ]
        # Words already carrying timings are returned unchanged.
        real = [{"word": "a", "start": 0.1, "end": 0.2}]
        assert command._timed_v2_words(real, "a", 0.0, 5.0) is real
        # Timing-less words get the window spread evenly across them.
        spread = command._timed_v2_words(
            [{"word": "a"}, {"word": "b"}], "a b", 0.0, 2.0
        )
        assert (spread[0]["start"], spread[0]["end"]) == (0.0, 1.0)
        assert (spread[1]["start"], spread[1]["end"]) == (1.0, 2.0)

    def test_fatal_error_frame_is_surfaced(self, command, capsys):
        """A Flux STT fatal error (type 'Error') is reported, not swallowed."""
        import json as _json

        acc: list[str] = []
        command._handle_ws_message(
            _json.dumps(
                {
                    "type": "Error",
                    "code": "INTERNAL_SERVER_ERROR",
                    "description": "something went wrong",
                }
            ),
            acc,
            diarize=False,
            interim=False,
            v2_state=command._new_v2_state(),
        )
        assert acc == []
        assert "something went wrong" in capsys.readouterr().err
