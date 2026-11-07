"""Tests for the unified listen command."""

from unittest.mock import Mock, patch

import click
import sys

import pytest
from click.testing import CliRunner
from deepctl_cmd_listen.command import ListenCommand
from deepctl_cmd_listen.models import ListenResult
from deepctl_core import (
    AuthManager,
    BaseResult,
    Config,
    DeepgramClient,
    PluginManager,
)


def _install_flux_error_websockets(monkeypatch, ready=None):
    """A server that sends one Flux Error frame (after ``ready()``), then ends."""
    import asyncio
    import json
    import sys
    import types

    error_frame = json.dumps(
        {
            "type": "Error",
            "code": "INVALID_AUDIO",
            "description": "audio stream is invalid",
        }
    )

    class _FakeWS:
        transport = None

        def __init__(self):
            self.messages = iter([error_frame])

        async def send(self, _data):
            return None

        async def close(self):
            return None

        def __aiter__(self):
            return self

        async def __anext__(self):
            while ready is not None and not ready():
                await asyncio.sleep(0)
            try:
                return next(self.messages)
            except StopIteration:
                raise StopAsyncIteration from None

    _install_websockets(monkeypatch, _FakeWS())


def _install_websockets(monkeypatch, ws):
    """Make ``await websockets.connect(...)`` return ``ws``."""
    import sys
    import types

    class _Connect:
        def __await__(self):
            async def _open():
                return ws

            return _open().__await__()

    fake = types.ModuleType("websockets")
    fake.connect = lambda *_args, **_kwargs: _Connect()
    monkeypatch.setitem(sys.modules, "websockets", fake)
    monkeypatch.setattr("deepctl_cmd_listen.live._open", fake.connect)


def _stdin_pipe(monkeypatch, data=b"", *, close=True):
    """Point the live stdin reader at a pipe holding ``data``.

    Returns the write end when ``close`` is False (the pipe stays open, so
    the reader blocks), else None.
    """
    import os

    r, w = os.pipe()
    if data:
        os.write(w, data)
    if close:
        os.close(w)
        w = None
    monkeypatch.setattr("deepctl_cmd_listen.command._stdin_fd", lambda: r)
    return w


class _EndOnCloseStream:
    """A server that ends the stream when CloseStream arrives."""

    transport = None

    def __init__(self):
        import asyncio

        self.sent = []
        self.ended = asyncio.Event()

    async def send(self, data):
        self.sent.append(data)
        if "CloseStream" in str(data):
            self.ended.set()

    async def close(self):
        self.ended.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        await self.ended.wait()
        raise StopAsyncIteration


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

    def invoke_click(self, command, mock_config, args, *, input=None):
        mock_config.get.side_effect = lambda key, default=None: (
            True if key == "output.quiet" else default
        )
        mock_config.get_profile.return_value.base_url = "https://api.deepgram.com"
        click_command = PluginManager()._create_click_command(command)
        with (
            patch("deepctl_core.base_command.AuthManager.guard"),
            patch(
                "deepctl_core.base_command.AuthManager.check_base_url",
                return_value="https://api.deepgram.com",
            ),
        ):
            return CliRunner().invoke(
                click_command,
                args,
                input=input,
                obj={"config": mock_config},
            )

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

    @patch("deepctl_cmd_listen.command.print_warning")
    @patch("deepctl_cmd_listen.command.status")
    @patch("deepctl_cmd_listen.command.sys")
    def test_handle_warns_diarize_ignored_on_flux(
        self,
        mock_sys,
        mock_status,
        mock_warning,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
    ):
        """--diarize on a Flux STT (v2) model warns instead of vanishing silently."""
        mock_sys.stdin.isatty.return_value = True

        async def fake_ws_mic(_client, **kwargs):
            return ListenResult(
                status="success",
                source="mic",
                mode="live",
                diarized=kwargs["diarize"],
            )

        with patch.object(command, "_ws_mic", side_effect=fake_ws_mic) as mock_ws:
            result = command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                mic=True,
                model="flux-general-en",
                diarize=True,
            )

        # The note is a warning (WARN: in agent mode), not status chrome.
        calls = mock_status.print.call_args_list + mock_warning.call_args_list
        printed = " ".join(str(c.args[0]) for c in calls if c.args)
        assert "Flux models don't support --diarize" in printed
        assert "Speaker labels enabled" not in printed
        assert mock_ws.call_args.kwargs["diarize"] is False
        assert result.diarized is False

    @patch("deepctl_cmd_listen.command.status")
    @patch("deepctl_cmd_listen.command.sys")
    def test_diarize_warning_prints_under_quiet(
        self,
        mock_sys,
        mock_status,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        monkeypatch,
        capsys,
    ):
        """`dg -q listen ... --diarize`: --quiet hides INFO/OK lines, not this
        WARN."""
        from deepctl_core import output

        monkeypatch.setitem(output._output_config, "quiet", True)
        monkeypatch.setitem(output._output_config, "agentic", True)
        mock_sys.stdin.isatty.return_value = True

        async def fake_ws_mic(_client, **kwargs):
            return ListenResult(status="success", source="mic", mode="live")

        with patch.object(command, "_ws_mic", side_effect=fake_ws_mic):
            command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                mic=True,
                model="flux-general-en",
                diarize=True,
            )
        assert "WARN: Flux models don't support --diarize, so it was ignored." in capsys.readouterr().err

    @patch("deepctl_cmd_listen.command.print_warning")
    @patch("deepctl_cmd_listen.command.status")
    @patch("deepctl_cmd_listen.command.sys")
    def test_handle_no_diarize_warning_on_v1(
        self,
        mock_sys,
        mock_status,
        mock_warning,
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

        # The note is a warning (WARN: in agent mode), not status chrome.
        calls = mock_status.print.call_args_list + mock_warning.call_args_list
        printed = " ".join(str(c.args[0]) for c in calls if c.args)
        assert "don't support --diarize" not in printed

    @pytest.mark.parametrize("source", ["call.wav", "https://example.com/call.wav"])
    def test_click_flux_prerecorded_source_errors(self, source, command, mock_config):
        """Flux STT file/URL guards are visible and exit non-zero."""
        result = self.invoke_click(
            command,
            mock_config,
            [source, "--model", "flux-general-en"],
        )
        assert result.exit_code == 1
        assert "streaming-only" in result.stderr

    def test_click_flux_invalid_redact_errors(self, command, mock_config):
        """An invalid Flux redact guard is visible and exits non-zero."""
        result = self.invoke_click(
            command,
            mock_config,
            ["--mic", "--model", "flux-general-en", "--redact", "pci"],
        )
        assert result.exit_code == 1
        assert "pci" in result.stderr
        assert "aggressive_numbers" in result.stderr

    def test_click_flux_error_frame_exits_nonzero(
        self, command, mock_config, monkeypatch
    ):
        """A stdin Error frame propagates through the stream and Click boundary."""
        _install_flux_error_websockets(monkeypatch)
        _stdin_pipe(monkeypatch)

        result = self.invoke_click(
            command,
            mock_config,
            ["-", "--model", "flux-general-en", "--encoding", "linear16"],
            input=b"",
        )

        assert result.exit_code == 1
        assert "INVALID_AUDIO" in result.stderr
        assert "audio stream is invalid" in result.stderr

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

    def test_prerecorded_api_error_exits_nonzero(self, command, mock_config):
        """A v1 REST rejection (e.g. a --redact value the endpoint refuses)
        raises ClickException so the command exits non-zero and is visible —
        not a returned error result that prints nothing and still exits 0."""
        client = Mock()
        client.transcribe_file.side_effect = Exception("Bad Request: invalid redact")
        with pytest.raises(click.ClickException) as exc_info:
            command._prerecorded(
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
                redact=("bogus",),
                numerals=False,
                save_to=None,
                probe=False,
                no_validate=True,
                caption_format=None,
                config=mock_config,
            )
        assert "Transcription failed" in str(exc_info.value)

    def test_ws_url_expands_multiple_redact(self, command):
        """Repeated redact values expand to repeated query params (doseq)."""
        ws_client = Mock()
        ws_client.config.get_profile.return_value = Mock(
            base_url="https://api.deepgram.com"
        )
        ws_client.auth_manager.check_base_url.return_value = "https://api.deepgram.com"
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
        ws_client.auth_manager.check_base_url.return_value = "https://api.deepgram.com"

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
        ws_client.auth_manager.check_base_url.return_value = "https://api.deepgram.com"

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

    def test_ws_mic_flushes_final_turn_on_ctrl_c(self, command, monkeypatch):
        """Ctrl-C during a Flux mic stream must still flush the in-flight turn,
        with CloseStream sent. Ctrl-C is how a mic session ends, so the
        result is a success (exit 0), as in 0.4.0."""
        import asyncio as _asyncio
        import signal
        import sys
        import types
        from unittest.mock import MagicMock

        class _FakeInputStream:
            def __init__(self, **_kwargs):
                pass

            def start(self):
                pass

            def stop(self):
                pass

            def close(self):
                pass

        fake_sd = types.ModuleType("sounddevice")
        fake_sd.RawInputStream = _FakeInputStream
        fake_np = types.ModuleType("numpy")
        fake_np.int16 = "int16"
        monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
        monkeypatch.setitem(sys.modules, "numpy", fake_np)
        ws = _EndOnCloseStream()
        _install_websockets(monkeypatch, ws)

        # A turn received but never finalized (no EndOfTurn before Ctrl-C).
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

        client = MagicMock()
        client.config.get_profile.return_value.base_url = "https://api.deepgram.com"
        client.auth_manager.get_api_key.return_value = "k"

        loop = _asyncio.new_event_loop()
        loop.call_later(0.1, signal.raise_signal, signal.SIGINT)
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
        assert result.status == "success"
        assert command.exit_code_for(result) == 0
        assert '{"type": "CloseStream"}' in ws.sent

    def test_ws_mic_streams_raw_int16_frames_without_numpy(self, command, monkeypatch):
        """The mic path opens a raw int16 stream and forwards each callback
        buffer to the socket byte for byte, with numpy unimportable."""
        import asyncio as _asyncio
        import json as _json
        import signal
        import sys
        import types
        from array import array
        from unittest.mock import MagicMock

        from deepctl_cmd_listen import live

        # Any `import numpy` now raises ImportError.
        monkeypatch.setitem(sys.modules, "numpy", None)

        # Fixed fixture: three 4-sample blocks of native-order int16, as
        # PortAudio delivers them to the callback.
        samples = [0, 1, -1, 32767, -32768, 1234, -1234, 7, 8, 9, -9, 0]
        blocks = [array("h", samples[i : i + 4]).tobytes() for i in range(0, 12, 4)]

        opened: dict = {}

        class _FakeRawInputStream:
            def __init__(self, **kwargs):
                opened.update(kwargs)
                self.events: list[str] = []
                opened["stream"] = self

            def start(self):
                self.events.append("start")
                for block in blocks:
                    # sounddevice hands the callback a CFFI buffer; a
                    # memoryview is the same buffer-protocol object.
                    opened["callback"](memoryview(block), 4, None, None)

            def stop(self):
                self.events.append("stop")

            def close(self):
                self.events.append("close")

        fake_sd = types.ModuleType("sounddevice")
        fake_sd.RawInputStream = _FakeRawInputStream
        monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

        ws = _EndOnCloseStream()
        _install_websockets(monkeypatch, ws)

        client = MagicMock()
        client.config.get_profile.return_value.base_url = "https://api.deepgram.com"
        client.auth_manager.get_api_key.return_value = "k"

        loop = _asyncio.new_event_loop()
        # The user's Ctrl-C, once the three blocks are on the socket.
        loop.call_later(0.2, signal.raise_signal, signal.SIGINT)
        try:
            loop.run_until_complete(
                command._ws_mic(
                    client,
                    model="nova-3",
                    language="en-US",
                    api_version=1,
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

        audio = [frame for frame in ws.sent if isinstance(frame, bytes)]
        assert opened["dtype"] == "int16"
        assert opened["samplerate"] == 16000
        assert opened["channels"] == 1
        assert opened["blocksize"] == live.frame_bytes("linear16", 16000, 1) // 2
        assert audio == blocks
        assert _json.loads(ws.sent[-1]) == {"type": "CloseStream"}
        assert opened["stream"].events == ["start", "stop", "close"]

    @pytest.mark.skipif(sys.platform == "win32", reason="os.kill of SIGINT ends the whole process on Windows")
    def test_sigint_during_mic_capture_stops_cleanly(self, command, monkeypatch):
        """A real SIGINT while frames are flowing stops the capture: the
        stream is stopped and closed, CloseStream is sent, and the transcript
        so far is kept on every Python version. Ctrl-C is how a mic session
        ends, so the result is success, exit 0."""
        import json as _json
        import os
        import signal
        import sys
        import threading
        import time
        import types
        from unittest.mock import MagicMock

        if signal.getsignal(signal.SIGINT) is not signal.default_int_handler:
            pytest.skip("SIGINT is not Python's default handler here")
        monkeypatch.setitem(sys.modules, "numpy", None)

        events: list[str] = []

        class _PacedStream:
            def __init__(self, *, callback, blocksize, samplerate, **kwargs):
                self._callback = callback
                self._period = blocksize / samplerate
                self._stop = threading.Event()
                self._thread = threading.Thread(target=self._run, daemon=True)

            def _run(self):
                while not self._stop.wait(self._period):
                    self._callback(memoryview(b"\x01\x00" * 800), 800, None, None)

            def start(self):
                events.append("start")
                self._thread.start()

            def stop(self):
                events.append("stop")
                self._stop.set()

            def close(self):
                events.append("close")

        fake_sd = types.ModuleType("sounddevice")
        fake_sd.RawInputStream = _PacedStream
        monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

        class _OneResultThenEndOnClose(_EndOnCloseStream):
            def __init__(self):
                super().__init__()
                self._said = False
                self._pressed = False

            async def send(self, data):
                await super().send(data)
                audio = [f for f in self.sent if isinstance(f, bytes)]
                if len(audio) == 3 and not self._pressed:
                    # Frames are flowing: the user presses Ctrl-C, once (a
                    # later CloseStream send must not schedule another).
                    self._pressed = True
                    threading.Timer(0.05, os.kill, (os.getpid(), signal.SIGINT)).start()

            async def __anext__(self):
                if not self._said:
                    self._said = True
                    return _json.dumps(
                        {
                            "type": "Results",
                            "is_final": True,
                            "start": 0.0,
                            "duration": 1.0,
                            "channel": {
                                "alternatives": [{"transcript": "hi", "words": []}]
                            },
                        }
                    )
                return await super().__anext__()

        ws = _OneResultThenEndOnClose()
        _install_websockets(monkeypatch, ws)

        client = MagicMock()
        client.config.get_profile.return_value.base_url = "https://api.deepgram.com"
        client.auth_manager.get_api_key.return_value = "k"

        start = time.monotonic()
        result = command._stream_mic(
            client,
            model="nova-3",
            language="en-US",
            api_version=1,
            diarize=False,
            smart_format=True,
            punctuate=True,
            interim=False,
            redact=(),
            numerals=False,
            sample_rate=16000,
            channels=1,
            save_to=None,
            caption_format=None,
        )

        assert time.monotonic() - start < 5
        assert events == ["start", "stop", "close"]
        assert all(isinstance(frame, bytes) for frame in ws.sent[:3])
        assert _json.loads(ws.sent[-1]) == {"type": "CloseStream"}
        assert result.status == "success"
        assert result.transcript == "hi"
        assert command.exit_code_for(result) == 0

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

    def test_timed_v2_words_backfills_partial_and_null_timings(self, command):
        partial = command._timed_v2_words(
            [{"word": "a", "start": 0.1}, {"word": "b", "end": 1.8}],
            "a b",
            0.0,
            2.0,
        )
        assert partial == [
            {"word": "a", "start": 0.1, "end": 1.0},
            {"word": "b", "start": 1.0, "end": 1.8},
        ]

        explicit_null = command._timed_v2_words(
            [{"word": "a", "start": None, "end": None}],
            "a",
            3.0,
            4.0,
        )
        assert explicit_null == [{"word": "a", "start": 3.0, "end": 4.0}]

    def test_fatal_error_frame_raises_code_and_description(self, command):
        """A Flux STT fatal error carries both server fields to the caller."""
        import json as _json

        acc: list[str] = []
        with pytest.raises(click.ClickException) as exc_info:
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
        assert "INTERNAL_SERVER_ERROR" in exc_info.value.message
        assert "something went wrong" in exc_info.value.message

    def test_stream_mic_flux_error_frame_does_not_return_success(
        self, command, monkeypatch
    ):
        import sys
        import types
        from unittest.mock import MagicMock

        events = {"started": False, "stopped": False, "closed": False}
        _install_flux_error_websockets(monkeypatch, ready=lambda: events["started"])

        class _FakeInputStream:
            def __init__(self, **_kwargs):
                pass

            def start(self):
                events["started"] = True

            def stop(self):
                events["stopped"] = True

            def close(self):
                events["closed"] = True

        fake_sounddevice = types.ModuleType("sounddevice")
        fake_sounddevice.RawInputStream = _FakeInputStream
        monkeypatch.setitem(sys.modules, "sounddevice", fake_sounddevice)

        client = MagicMock()
        client.config.get_profile.return_value.base_url = "https://api.deepgram.com"
        client.auth_manager.get_api_key.return_value = "test-key"

        result = command._stream_mic(
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
            save_to=None,
            caption_format=None,
        )

        assert result.status == "error"
        assert result.code == "server_error"
        assert command.exit_code_for(result) == 1
        assert "INVALID_AUDIO" in result.error
        assert "audio stream is invalid" in result.error
        assert events == {"started": True, "stopped": True, "closed": True}

    def test_stream_stdin_flux_error_does_not_wait_for_blocked_input(
        self, command, monkeypatch
    ):
        """A server error ends the stream while stdin is still open and idle."""
        import os
        import threading
        from unittest.mock import MagicMock

        writer = _stdin_pipe(monkeypatch, close=False)
        _install_flux_error_websockets(monkeypatch)

        client = MagicMock()
        client.config.get_profile.return_value.base_url = "https://api.deepgram.com"
        client.auth_manager.get_api_key.return_value = "test-key"
        results = []

        def run_stream():
            results.append(
                command._stream_stdin(
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
                    encoding="linear16",
                    sample_rate=16000,
                    channels=1,
                    save_to=None,
                    caption_format=None,
                )
            )

        stream_thread = threading.Thread(target=run_stream, daemon=True)
        stream_thread.start()
        stream_thread.join(timeout=2)
        exited_while_stdin_open = not stream_thread.is_alive()
        os.close(writer)

        assert exited_while_stdin_open
        (result,) = results
        assert result.status == "error"
        assert result.code == "server_error"
        assert "INVALID_AUDIO" in result.error

    @pytest.mark.skipif(sys.platform == "win32", reason="os.kill of SIGINT ends the whole process on Windows")
    def test_stream_stdin_ctrl_c_preserves_transcript_and_save_to(
        self, command, monkeypatch, tmp_path
    ):
        """Ctrl-C on `dg listen -` (stdin) keeps the partial transcript and
        writes --save-to, and ends as cancelled (exit 2)."""
        import os
        import signal
        import threading
        from unittest.mock import MagicMock

        writer = _stdin_pipe(monkeypatch, close=False)
        ws = _EndOnCloseStream()
        _install_websockets(monkeypatch, ws)
        threading.Timer(0.2, os.kill, (os.getpid(), signal.SIGINT)).start()

        # A turn received but never finalized before the interrupt.
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

        client = MagicMock()
        client.config.get_profile.return_value.base_url = "https://api.deepgram.com"
        client.auth_manager.get_api_key.return_value = "k"

        save_to = tmp_path / "out.txt"
        result = command._stream_stdin(
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
            encoding="linear16",
            sample_rate=16000,
            channels=1,
            save_to=str(save_to),
            caption_format=None,
        )

        os.close(writer)
        assert result.transcript == "hello world"
        assert result.status == "cancelled"
        assert command.exit_code_for(result) == 2
        assert save_to.read_text().strip() == "hello world"
