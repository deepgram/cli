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
                    redact="numbers",
                    numerals=True,
                )

            call_kwargs = mock_pre.call_args.kwargs
            assert call_kwargs["redact"] == "numbers"
            assert call_kwargs["numerals"] is True

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

    def _turn(self, event, transcript, *, turn_index=0, words=None):
        import json as _json

        return _json.dumps(
            {
                "type": "TurnInfo",
                "event": event,
                "turn_index": turn_index,
                "transcript": transcript,
                "words": words or [],
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
