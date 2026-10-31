"""``dg listen`` prerecorded uploads (W2.4): streamed file bodies."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import Mock

import pytest
from deepctl_cmd_listen.command import ListenCommand
from deepctl_core import Config, output, transport, upload
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
