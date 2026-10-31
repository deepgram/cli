"""``dg listen`` prerecorded sources: streamed files, --timeout, URLs."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import Mock, patch

import click
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
        return None, []

    monkeypatch.setattr(
        deepctl_shared_utils.validation, "audio_file_problem", validate_then_swap
    )
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


# ── URL sources ──────────────────────────────────────────────────────

SIGNED = "https://bucket.example/call.wav?X-Amz-Signature=URLSENTINELTOKEN"


@pytest.mark.parametrize(("check_url", "probed"), [(False, False), (True, True)])
def test_url_is_checked_by_format_unless_check_url(check_url, probed):
    client = Mock()
    client.transcribe_url.return_value = {"results": {"channels": []}}
    with patch(
        "deepctl_shared_utils.validation.url_problem", return_value=(None, [])
    ) as validate:
        _prerecorded(
            client, SIGNED, is_url=True, no_validate=False, check_url=check_url
        )
    validate.assert_called_once_with(SIGNED, check_accessibility=probed)
    # The API still gets the whole URL, token included.
    assert client.transcribe_url.call_args.args[0] == SIGNED


def test_no_validate_skips_both_checks():
    client = Mock()
    client.transcribe_url.return_value = {"results": {"channels": []}}
    with patch("deepctl_shared_utils.validation.url_problem") as validate:
        _prerecorded(client, SIGNED, is_url=True, no_validate=True, check_url=True)
    validate.assert_not_called()


def test_url_query_never_reaches_stderr_or_the_result(capsys):
    client = Mock()
    client.transcribe_url.return_value = {"results": {"channels": []}}
    result = _prerecorded(client, SIGNED, is_url=True, no_validate=False)
    err = capsys.readouterr().err
    assert "Transcribing https://bucket.example/call.wav" in err
    assert "URLSENTINELTOKEN" not in err
    assert result.source_path == "https://bucket.example/call.wav"
    assert "URLSENTINELTOKEN" not in result.model_dump_json()


def _unreachable(monkeypatch):
    """--check-url's request fails to connect; nothing leaves the test."""
    import httpx

    def refuse(self, method, url, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx.Client, "request", refuse)
    monkeypatch.setattr(httpx.Client, "send", refuse)


@pytest.mark.parametrize(
    ("url", "check_url", "message"),
    [
        (
            SIGNED.replace("https:", "ftp:"),
            False,
            "URLs must start with http:// or https://: ftp://bucket.example/call.wav",
        ),
        (
            SIGNED,
            True,
            "--check-url couldn't reach https://bucket.example/call.wav "
            "(connection refused)",
        ),
    ],
)
def test_failed_url_check_message_drops_the_query(
    url, check_url, message, monkeypatch, capsys
):
    _unreachable(monkeypatch)
    result = _prerecorded(
        Mock(), url, is_url=True, no_validate=False, check_url=check_url
    )
    assert result.status == "error"
    assert result.message == message
    # One report of the failure: the result, nothing extra on stderr.
    out = capsys.readouterr()
    assert "reach" not in out.err and "http://" not in out.err
    assert "URLSENTINELTOKEN" not in out.err + out.out


@pytest.mark.parametrize(("args", "expected"), [([], False), (["--check-url"], True)])
def test_check_url_flag_reaches_prerecorded(args, expected):
    done = ListenResult(status="success", source="url", mode="prerecorded")
    prerecorded = Mock(return_value=done)
    result = _invoke([SIGNED, *args], prerecorded)
    assert result.exit_code == 0, result.output
    assert prerecorded.call_args.kwargs["check_url"] is expected


def test_help_shows_check_url():
    output = _invoke(["--help"]).output
    assert "--check-url" in output
    # The approved text, whatever the wrap width.
    assert (
        "Check that the URL responds before transcribing: a HEAD request, "
        "then a 1-byte GET if needed. Sends no Deepgram API key."
    ) in " ".join(output.split())


# ── Scheme case, URL-looking paths, and --check-url ──

UPPER = "HTTPS://u:URLPASS@bucket.example/call.wav?X-Amz-Signature=URLSENTINELTOKEN"


@pytest.mark.parametrize("url", [UPPER, "Http://h.example/a.wav", "hTtPs://h/a"])
def test_scheme_is_case_insensitive(url):
    from deepctl_cmd_listen.command import _is_url

    assert _is_url(url)


def test_uppercase_scheme_routes_to_the_url_path():
    done = ListenResult(status="success", source="url", mode="prerecorded")
    prerecorded = Mock(return_value=done)
    result = _invoke([UPPER], prerecorded)
    assert result.exit_code == 0, result.output
    assert prerecorded.call_args.kwargs["is_url"] is True


def test_uppercase_url_never_shows_the_token(capsys):
    client = Mock()
    client.transcribe_url.return_value = {"results": {"channels": []}}
    result = _prerecorded(client, UPPER, is_url=True, no_validate=False)
    err = capsys.readouterr().err
    assert "URLSENTINELTOKEN" not in err and "URLPASS" not in err
    assert result.source_path == "https://bucket.example/call.wav"


URL_AS_PATH = "ftp://u:URLPASS@h.example/a.wav?X-Amz-Signature=URLSENTINELTOKEN"


def test_url_looking_path_not_found_hides_the_token(capsys):
    result = _prerecorded(Mock(), URL_AS_PATH, no_validate=False)
    assert result.status == "error"
    assert result.message == "File not found: ftp://h.example/a.wav"
    assert "URLSENTINELTOKEN" not in capsys.readouterr().err


def test_url_looking_path_not_found_without_validation_hides_the_token(capsys):
    from deepctl_core.client import DeepgramClient

    client = Mock(spec=DeepgramClient)
    client.transcribe_file.side_effect = lambda path, options, **kw: (
        DeepgramClient.transcribe_file(client, path, options, **kw)
    )
    with pytest.raises(click.ClickException) as err:
        _prerecorded(client, URL_AS_PATH, no_validate=True)
    assert err.value.message == (
        "File not found: ftp://h.example/a.wav"
    )
    assert "URLSENTINELTOKEN" not in capsys.readouterr().err


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["a.wav", "--check-url"], "--check-url works only with a URL source."),
        (
            [SIGNED, "--check-url", "--no-validate"],
            "--check-url can't be used with --no-validate.",
        ),
        (
            ["a.wav", "--check-url", "--no-validate"],
            "--check-url works only with a URL source.",
        ),
        (["--mic", "--check-url"], "--check-url works only with a URL source."),
        (["-", "--check-url"], "--check-url works only with a URL source."),
    ],
)
def test_check_url_without_a_checked_url_is_a_usage_error(args, message):
    prerecorded, mic, stdin = Mock(), Mock(), Mock()
    result = _invoke(args, prerecorded, mic=mic, stdin=stdin)
    assert result.exit_code == 2
    assert message in result.output
    prerecorded.assert_not_called()
    mic.assert_not_called()
    stdin.assert_not_called()
    assert "URLSENTINELTOKEN" not in result.output


# ── A token in the URL path ──

DEEP = (
    "https://u:URLPASS@cdn.example/s/URLPATHTOKEN/calls/call.wav?sig=URLSENTINELTOKEN"
)


def test_deep_url_path_is_cut_in_status_lines_but_kept_in_the_result(capsys):
    client = Mock()
    client.transcribe_url.return_value = {"results": {"channels": []}}
    result = _prerecorded(client, DEEP, is_url=True, no_validate=False)
    err = capsys.readouterr().err
    assert "Transcribing https://cdn.example/…/call.wav" in err
    assert "URLPATHTOKEN" not in err and "URLSENTINELTOKEN" not in err
    assert "URLPASS" not in err
    # Machine output keeps the path, never the user info or query string.
    assert result.source_path == "https://cdn.example/s/URLPATHTOKEN/calls/call.wav"
    assert "URLSENTINELTOKEN" not in result.model_dump_json()
    # The request to Deepgram still gets the full URL.
    assert client.transcribe_url.call_args.args[0] == DEEP


@pytest.mark.parametrize("check_url", [False, True])
def test_deep_url_error_text_shows_only_the_last_segment(check_url, monkeypatch):
    _unreachable(monkeypatch)
    url = DEEP if check_url else DEEP.replace("https:", "ftp:")
    result = _prerecorded(
        Mock(), url, is_url=True, no_validate=False, check_url=check_url
    )
    assert "://cdn.example/…/call.wav" in result.message
    assert "URLPATHTOKEN" not in result.message


def test_deep_url_table_shows_only_the_last_segment(capsys):
    result = ListenResult(
        status="success",
        source="url",
        source_path="https://cdn.example/s/URLPATHTOKEN/calls/call.wav",
        mode="prerecorded",
        model="nova-3",
    )
    ListenCommand()._print_prerecorded_result(result)
    out = capsys.readouterr().out
    assert "https://cdn.example/…/call.wav" in out
    assert "URLPATHTOKEN" not in out


# ── User info that urlparse can't see ──

MALFORMED = [
    "https://u:pa#URLPW@cdn.example/a.wav",
    "https://u:pa?URLPW@cdn.example/a.wav",
    "https://u:pa/URLPW@cdn.example/a/b.wav",
    "https:///URLPW@cdn.example/y/a.wav",
    "https://u/URLPW@cdn.example/a.wav",
]


@pytest.mark.parametrize("url", MALFORMED)
@pytest.mark.parametrize("check_url", [False, True])
def test_malformed_user_info_never_reaches_messages(url, check_url, capsys):
    client = Mock()
    client.transcribe_url.return_value = {"results": {"channels": []}}
    # No request leaves the test: the HEAD check itself reports failure.
    with patch(
        "deepctl_shared_utils.validation._url_reach_problem",
        return_value=("--check-url couldn't reach https://<redacted>/… (HTTP 404)", []),
    ):
        result = _prerecorded(
            client, url, is_url=True, no_validate=False, check_url=check_url
        )
    err = capsys.readouterr().err
    assert "URLPW" not in err and "u:pa" not in err
    if result.status == "error":
        assert result.message in (
            "Not a valid URL: https://<redacted>/…",
            "--check-url couldn't reach https://<redacted>/… (HTTP 404)",
        )
        assert "Error" not in err  # reported once, by the result
    else:
        assert result.source_path == "https://<redacted>/…"
        assert "URLPW" not in result.model_dump_json()
        # The request to Deepgram still gets the full URL.
        assert client.transcribe_url.call_args.args[0] == url


@pytest.mark.parametrize("url", MALFORMED)
def test_malformed_user_info_without_validation(url, capsys):
    client = Mock()
    client.transcribe_url.return_value = {"results": {"channels": []}}
    result = _prerecorded(client, url, is_url=True, no_validate=True)
    assert "URLPW" not in capsys.readouterr().err
    assert result.source_path == "https://<redacted>/…"
    assert client.transcribe_url.call_args.args[0] == url


def test_transcription_error_text_drops_user_info(capsys):
    url = "https://u:URLPW@cdn.example/a.wav?sig=URLSENTINELTOKEN"
    client = Mock()
    client.transcribe_url.side_effect = RuntimeError(f"rejected {url}")
    with pytest.raises(click.ClickException) as err:
        _prerecorded(client, url, is_url=True, no_validate=True)
    assert "URLPW" not in err.value.message
    assert "URLSENTINELTOKEN" not in err.value.message
