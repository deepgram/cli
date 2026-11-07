"""Prerecorded uploads streamed from disk.

``FileBody`` on its own, then through the real SDK facade on an
``httpx.MockTransport``: the request carries ``Content-Length`` and no
chunked encoding, the SDK's retries resend the whole file, and a file that
changes or can't be read mid-upload maps to the ``E-UPLOAD-*`` messages.
"""

from __future__ import annotations

import errno
import json
import os
import sys
import threading
from pathlib import Path

import httpx
import pytest
from deepctl_core import hosts, output, transport, upload
from deepctl_core.auth import AuthManager
from deepctl_core.client import DeepgramClient
from deepctl_core.config import Config
from deepctl_core.upload import FileBody, UploadFileError

KEY = "sk-URLSENTINELKEY"
MIB = 1 << 20


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "deepctl_core.config._default_config_file",
        lambda: tmp_path / "user" / "config.yaml",
    )
    monkeypatch.setattr(
        "deepctl_core.config._trust_root_location",
        lambda: (tmp_path, tmp_path / "user" / "config.yaml"),
    )
    monkeypatch.setattr(hosts, "_reported", set())
    monkeypatch.setattr(transport, "_client", None)
    monkeypatch.setattr(transport, "prog", lambda: "dg")
    monkeypatch.setitem(output._output_config, "agentic", False)
    monkeypatch.setattr(output.stderr_console, "_width", 400)
    from deepctl_core import auth as auth_module

    monkeypatch.setattr(auth_module.keyring, "get_password", lambda *a: None)
    import deepgram.core.http_client as sdk_http

    monkeypatch.setattr(sdk_http.time, "sleep", lambda s: None)
    monkeypatch.setenv("DEEPGRAM_API_KEY", KEY)


def _audio(tmp_path: Path, size: int, name: str = "a.wav") -> Path:
    path = tmp_path / name
    path.write_bytes(bytes(i % 251 for i in range(size)))
    return path


def facade(handler) -> tuple[DeepgramClient, list[tuple[httpx.Request, bytes]]]:
    seen: list[tuple[httpx.Request, bytes]] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append((request, request.read()))
        return handler(request)

    # The same timeout and redirect settings as transport.shared_client().
    transport._set_client(
        httpx.Client(
            transport=httpx.MockTransport(record),
            timeout=transport.DEFAULT_TIMEOUT_S,
            follow_redirects=True,
        )
    )
    config = Config(config_path=str(Path.cwd() / "none.yaml"))
    return DeepgramClient(config, AuthManager(config)), seen


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"metadata": {}, "results": {}})


# ── FileBody ─────────────────────────────────────────────────────────


def test_reads_in_1_mib_chunks_and_reiterates(tmp_path):
    path = _audio(tmp_path, 2 * MIB + 5)
    with FileBody(path) as body:
        assert len(body) == 2 * MIB + 5
        first = list(body)
        assert [len(c) for c in first] == [MIB, MIB, 5]
        assert b"".join(body) == path.read_bytes()


def test_a_retry_sends_the_file_opened_not_the_one_at_the_path(tmp_path):
    path = _audio(tmp_path, 3000)
    original = path.read_bytes()
    with FileBody(path) as body:
        assert b"".join(body) == original
        swap = tmp_path / "swap.wav"
        swap.write_bytes(b"x" * 3000)
        os.replace(swap, path)  # a new file at the same path
        assert b"".join(body) == original


def test_file_changed_mid_read(tmp_path):
    path = _audio(tmp_path, 2 * MIB)
    with FileBody(path) as body:
        chunks = iter(body)
        next(chunks)
        os.truncate(path, MIB // 2)
        with pytest.raises(UploadFileError) as err:
            next(chunks)
    assert err.value.messages == upload.FILE_CHANGED
    assert err.value.values == {"path": str(path)}


def test_file_grown_after_the_last_chunk(tmp_path):
    path = _audio(tmp_path, 10)
    with FileBody(path) as body:
        chunks = iter(body)
        next(chunks)
        with path.open("ab") as f:
            f.write(b"more")
        with pytest.raises(UploadFileError) as err:
            next(chunks)
    assert err.value.messages == upload.FILE_CHANGED


def test_read_error_is_unreadable(tmp_path, monkeypatch):
    path = _audio(tmp_path, 10)

    def eio(*args):
        raise OSError(errno.EIO, os.strerror(errno.EIO))

    with FileBody(path) as body:
        monkeypatch.setattr(os, "pread", eio, raising=False)
        with pytest.raises(UploadFileError) as err:
            list(body)
    assert err.value.messages == upload.FILE_UNREADABLE
    assert err.value.values["os_error"] == os.strerror(errno.EIO)


def test_missing_file_is_unreadable(tmp_path):
    with pytest.raises(UploadFileError) as err:
        FileBody(tmp_path / "gone.wav")
    assert err.value.messages == upload.FILE_UNREADABLE
    assert err.value.values["os_error"] == os.strerror(errno.ENOENT)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs a FIFO")
def test_a_pipe_is_read_once_and_kept(tmp_path):
    fifo = tmp_path / "audio.fifo"
    os.mkfifo(fifo)
    writer = threading.Thread(target=lambda: fifo.write_bytes(b"piped audio"))
    writer.start()
    with FileBody(fifo) as body:
        writer.join()
        assert len(body) == 11
        assert b"".join(body) == b"piped audio"
        assert b"".join(body) == b"piped audio"


def _file_id(path: Path) -> tuple[int, int]:
    st = os.stat(path)
    return (st.st_dev, st.st_ino)


def test_same_file_as_validated_is_uploaded(tmp_path):
    path = _audio(tmp_path, 10)
    with FileBody(path, file_id=_file_id(path)) as body:
        assert b"".join(body) == path.read_bytes()


def test_path_swapped_after_validation_is_changed(tmp_path):
    validated = _audio(tmp_path, 10, "validated.wav")
    other = _audio(tmp_path, 10, "other.wav")
    link = tmp_path / "x.wav"
    try:
        link.symlink_to(validated)
    except OSError:
        pytest.skip("symlinks not available")
    file_id = _file_id(link)
    link.unlink()
    link.symlink_to(other)  # swapped between validation and upload
    with pytest.raises(UploadFileError) as err:
        FileBody(link, file_id=file_id)
    assert err.value.messages == upload.FILE_CHANGED


def test_close_releases_the_descriptor(tmp_path):
    body = FileBody(_audio(tmp_path, 10))
    fd = body._fd
    body.close()
    body.close()
    with pytest.raises(OSError):
        os.fstat(fd)


def test_path_in_the_message_is_cleaned(tmp_path):
    err = UploadFileError(upload.FILE_CHANGED, "/tmp/a\x1b[31m.wav")
    assert "\x1b" not in err.values["path"]


# ── Through the SDK ──────────────────────────────────────────────────


def test_upload_has_content_length_and_no_chunked_encoding(tmp_path):
    path = _audio(tmp_path, MIB + 7)
    client, seen = facade(ok)
    client.transcribe_file(path)
    ((request, body),) = seen
    assert request.headers["Content-Length"] == str(MIB + 7)
    assert "Transfer-Encoding" not in request.headers
    assert body == path.read_bytes()


def refused(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


def busy(request: httpx.Request) -> httpx.Response:
    return httpx.Response(503, json={"err_msg": "busy"})


@pytest.mark.parametrize("first", [refused, busy], ids=["connect", "503"])
def test_sdk_retry_resends_the_whole_file(tmp_path, first):
    path = _audio(tmp_path, 2 * MIB + 3)
    calls = []

    def handler(request):
        calls.append(1)
        return first(request) if len(calls) == 1 else ok(request)

    client, seen = facade(handler)
    client.transcribe_file(path)
    assert len(seen) == 2
    assert [len(body) for _, body in seen] == [2 * MIB + 3] * 2
    assert seen[1][1] == path.read_bytes()


def test_swapped_path_sends_nothing(tmp_path):
    validated = _audio(tmp_path, 10, "validated.wav")
    file_id = _file_id(validated)
    os.replace(_audio(tmp_path, 10, "other.wav"), validated)
    client, seen = facade(ok)
    with pytest.raises(UploadFileError) as err:
        client.transcribe_file(validated, file_id=file_id)
    assert err.value.messages == upload.FILE_CHANGED
    assert seen == []


def _changing_pread(path: Path, monkeypatch, action):
    real = os.pread if hasattr(os, "pread") else None
    calls = []

    def pread(fd, size, offset):
        calls.append(offset)
        if len(calls) == 2:
            action(path)
        if real is not None:
            return real(fd, size, offset)
        os.lseek(fd, offset, os.SEEK_SET)
        return os.read(fd, size)

    monkeypatch.setattr(os, "pread", pread, raising=False)


def test_file_changed_during_upload_maps_to_upload_changed(tmp_path, monkeypatch):
    path = _audio(tmp_path, 3 * MIB)
    client, seen = facade(ok)
    _changing_pread(path, monkeypatch, lambda p: os.truncate(p, MIB))
    with pytest.raises(UploadFileError) as err:
        client.transcribe_file(path)
    assert err.value.messages == upload.FILE_CHANGED
    assert str(err.value) == f"{path} changed while dg was uploading it."
    assert seen == []  # the request never completed


def test_read_error_during_upload_maps_to_upload_unreadable(tmp_path, monkeypatch):
    path = _audio(tmp_path, 3 * MIB)
    client, _ = facade(ok)
    real = getattr(os, "pread", None)
    calls = []

    def pread(fd, size, offset):
        calls.append(offset)
        if len(calls) == 2:
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        return real(fd, size, offset) if real else os.read(fd, size)

    monkeypatch.setattr(os, "pread", pread, raising=False)
    with pytest.raises(UploadFileError) as err:
        client.transcribe_file(path)
    assert err.value.messages == upload.FILE_UNREADABLE
    assert str(err.value) == f"Couldn't read {path}: {os.strerror(errno.EIO)}."


# ── The coded result ─────────────────────────────────────────────────


@pytest.mark.parametrize("agentic", [False, True])
def test_coded_result(agentic, monkeypatch, capsys):
    monkeypatch.setitem(output._output_config, "agentic", agentic)
    err = UploadFileError(upload.FILE_CHANGED, "/data/call.wav")
    payload = upload.coded_result(err).model_dump()
    hint = upload.STRINGS[
        "A-UPLOAD-FILE-CHANGED" if agentic else "H-UPLOAD-FILE-CHANGED"
    ]
    assert payload == {
        "error": "/data/call.wav changed while dg was uploading it.",
        "status": "error",
        "code": "invalid_argument",
        "hint": hint,
        "next_step": None,
        "schema_version": 1,
        "details": {"path": "/data/call.wav"},
    }
    err_lines = capsys.readouterr().err
    assert "/data/call.wav changed while dg was uploading it." in err_lines
    assert hint in err_lines


def test_unreadable_uses_the_human_hint_for_agents(monkeypatch, capsys):
    monkeypatch.setitem(output._output_config, "agentic", True)
    err = UploadFileError(
        upload.FILE_UNREADABLE, "/x.wav", OSError(errno.EACCES, "Permission denied")
    )
    payload = upload.coded_result(err).model_dump()
    assert payload["error"] == "Couldn't read /x.wav: Permission denied."
    assert payload["hint"] == upload.STRINGS["H-UPLOAD-FILE-UNREADABLE"]
    assert payload["next_step"] is None


@pytest.mark.skipif(
    not os.environ.get("DG_ERROR_CODES_JSON"),
    reason="DG_ERROR_CODES_JSON names no error catalogue file",
)
def test_strings_match_catalogue():
    catalogue = json.loads(Path(os.environ["DG_ERROR_CODES_JSON"]).read_text())
    for message_id, text in upload.STRINGS.items():
        assert catalogue["strings"][message_id] == text, message_id
    entry = catalogue["codes"]["invalid_argument"]
    assert "path" in entry["details"]
    for error_id, human, agent in (upload.FILE_CHANGED, upload.FILE_UNREADABLE):
        message = next(m for m in entry["messages"] if m["id"] == error_id)
        assert (message["hint_human"], message["hint_agent"]) == (human, agent)
        assert entry["next_step_overrides"][error_id] is None


def test_no_module_level_import_cost():
    """``deepctl_core.upload`` loads nothing heavier than ``os``."""
    code = (
        "import sys, deepctl_core.upload; "
        "print(sorted(m for m in ('httpx', 'pydantic', 'rich', 'deepgram') "
        "if m in sys.modules))"
    )
    import subprocess

    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]"


# ── --timeout ────────────────────────────────────────────────────────


def _timed_out(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


@pytest.mark.parametrize(
    ("timeout_s", "reported"), [(None, 60), (2.0, 2), (2.5, 2.5), (300.0, 300)]
)
def test_timeout_reports_the_effective_value(tmp_path, timeout_s, reported):
    path = _audio(tmp_path, 10)
    client, seen = facade(_timed_out)
    with pytest.raises(transport.TransportError) as err:
        client.transcribe_file(path, timeout_s=timeout_s)
    assert err.value.code == "timeout"
    assert err.value.details == {"host": "api.deepgram.com", "timeout_s": reported}
    assert err.value.error == f"api.deepgram.com didn't respond within {reported} s."
    ((request, _),) = seen
    assert request.extensions["timeout"]["read"] == (timeout_s or 60)


def test_url_timeout_reports_the_effective_value():
    client, seen = facade(_timed_out)
    with pytest.raises(transport.TransportError) as err:
        client.transcribe_url("https://example.com/a.wav", timeout_s=120)
    assert err.value.details["timeout_s"] == 120
    ((request, _),) = seen
    assert request.extensions["timeout"] == {
        "connect": 120,
        "read": 120,
        "write": 120,
        "pool": 120,
    }


def test_other_requests_keep_the_60_s_timeout():
    client, seen = facade(_timed_out)
    with pytest.raises(transport.TransportError) as err:
        client.list_models()
    assert err.value.details["timeout_s"] == 60
    assert seen[0][0].extensions["timeout"]["read"] == 60
