"""``dg listen`` prerecorded uploads (W2.4): streamed file bodies, --timeout."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner
from deepctl_cmd_listen.command import ListenCommand
from deepctl_cmd_listen.models import ListenResult
from deepctl_core import Config, PluginManager, output, transport, upload
from deepctl_core.upload import UploadFileError


@pytest.fixture(autouse=True)
def _plain_output(monkeypatch):
    monkeypatch.setitem(output._output_config, "agentic", False)
    monkeypatch.setattr(transport, "prog", lambda: "dg")


def _prerecorded(client, source="audio.wav", **overrides):
    kwargs: dict[str, Any] = {
        "is_url": False,
        "model": "nova-3",
        "language": "en-US",
        "api_version": 1,
        "diarize": False,
        "smart_format": True,
        "punctuate": True,
        "summarize": False,
        "topics": False,
        "sentiment": False,
        "redact": (),
        "numerals": False,
        "save_to": None,
        "probe": False,
        "no_validate": True,
        "caption_format": None,
        "config": Mock(spec=Config),
    }
    kwargs.update(overrides)
    return ListenCommand()._prerecorded(client, source, **kwargs)


def test_file_changed_returns_coded_error(capsys):
    client = Mock()
    client.transcribe_file.side_effect = UploadFileError(
        upload.FILE_CHANGED, "audio.wav"
    )
    result = _prerecorded(client)
    payload = result.model_dump()
    assert payload["status"] == "error"
    assert payload["code"] == "invalid_argument"
    assert payload["error"] == "audio.wav changed while dg was uploading it."
    assert payload["next_step"] is None
    assert payload["details"] == {"path": "audio.wav"}
    assert ListenCommand().exit_code_for(result) == 1
    assert "changed while dg was uploading it" in capsys.readouterr().err


def test_validated_file_identity_reaches_the_upload(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF" + b"\0" * 64)
    st = audio.stat()
    client = Mock()
    client.transcribe_file.return_value = {"results": {"channels": []}}
    _prerecorded(client, str(audio), no_validate=False)
    assert client.transcribe_file.call_args.kwargs["file_id"] == (st.st_dev, st.st_ino)


def test_path_swapped_during_validation_uploads_nothing(tmp_path, monkeypatch):
    """The file validated is the file uploaded."""
    import deepctl_shared_utils
    from deepctl_core.client import DeepgramClient

    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF" + b"\0" * 64)
    other = tmp_path / "other.wav"
    other.write_bytes(b"RIFF" + b"\1" * 64)

    def validate_then_swap(path):
        os.replace(other, audio)  # someone swaps the path mid-validation
        return True

    monkeypatch.setattr(deepctl_shared_utils, "validate_audio_file", validate_then_swap)
    client = Mock(spec=DeepgramClient)
    client.transcribe_file.side_effect = lambda path, options, **kw: (
        DeepgramClient.transcribe_file(client, path, options, **kw)
    )
    client._secrets = ()
    result = _prerecorded(client, str(audio), no_validate=False)
    payload = result.model_dump()
    assert payload["code"] == "invalid_argument"
    assert payload["error"] == f"{audio} changed while dg was uploading it."
    client.client.listen.v1.media.transcribe_file.assert_not_called()


# ── --timeout ────────────────────────────────────────────────────────


def _invoke(args: list[str], prerecorded: Mock | None = None, **streams: Mock):
    config = Mock(spec=Config)
    config.get.side_effect = lambda key, default=None: (
        True if key == "output.quiet" else default
    )
    config.get_profile.return_value.base_url = "https://api.deepgram.com"
    command = ListenCommand()
    done = ListenResult(status="success", source="file", mode="prerecorded")
    with (
        patch.object(command, "_prerecorded", prerecorded or Mock(return_value=done)),
        patch.object(command, "_stream_mic", streams.get("mic", Mock())),
        patch.object(command, "_stream_stdin", streams.get("stdin", Mock())),
        patch("deepctl_core.base_command.AuthManager.guard"),
        patch(
            "deepctl_core.base_command.AuthManager.check_base_url",
            return_value="https://api.deepgram.com",
        ),
    ):
        click_command = PluginManager()._create_click_command(command)
        return CliRunner().invoke(click_command, args, obj={"config": config})


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["a.wav"], 300.0),
        (["a.wav", "--timeout", "120"], 120.0),
        (["https://example.com/a.wav", "--timeout", "2.5"], 2.5),
    ],
)
def test_timeout_reaches_the_request(args, expected):
    done = ListenResult(status="success", source="file", mode="prerecorded")
    prerecorded = Mock(return_value=done)
    result = _invoke(args, prerecorded)
    assert result.exit_code == 0, result.output
    assert prerecorded.call_args.kwargs["timeout_s"] == expected


@pytest.mark.parametrize("value", ["0.5", "0", "-1", "soon"])
def test_timeout_below_one_second_is_a_usage_error(value):
    result = _invoke(["a.wav", "--timeout", value])
    assert result.exit_code == 2
    assert "--timeout" in result.output


@pytest.mark.parametrize("args", [["--mic"], ["-"]])
def test_timeout_with_a_live_source_is_a_usage_error(args):
    mic, stdin = Mock(), Mock()
    result = _invoke([*args, "--timeout", "30"], mic=mic, stdin=stdin)
    assert result.exit_code == 2
    assert "--timeout applies only to a file or URL source" in result.output
    mic.assert_not_called()
    stdin.assert_not_called()


def test_prerecorded_passes_timeout_to_the_client():
    client = Mock()
    client.transcribe_file.return_value = {"results": {"channels": []}}
    _prerecorded(client, timeout_s=42.0)
    assert client.transcribe_file.call_args.kwargs == {
        "timeout_s": 42.0,
        "file_id": None,
    }


def test_help_shows_timeout():
    result = _invoke(["--help"])
    assert "--timeout" in result.output
    # The approved text, whatever the wrap width.
    assert (
        "Seconds to wait at each step (connecting, sending, the response) "
        "before giving up. Not a limit on the whole run. Default: 300. "
        "Files and URLs only."
    ) in " ".join(result.output.split())
