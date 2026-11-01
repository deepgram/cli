"""The server's body, as sent: ``DeepgramClient.transcribe_body`` and
``rawhttp.post_for_body`` on an ``httpx.MockTransport``."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import httpx
import pytest
from deepctl_core import hosts, output, rawhttp, transport
from deepctl_core.auth import AuthManager
from deepctl_core.client import DeepgramClient
from deepctl_core.config import Config
from deepctl_core.transport import TransportError

KEY = "sk-W25SENTINELKEY"
BODY = b'{"metadata":{"created":"2026-10-31T01:02:03.456Z"},"results":{}}'


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
    # facade() replaces the shared clients; monkeypatch puts the ones an
    # earlier test left back afterwards, so none is dropped unclosed.
    monkeypatch.setattr(transport, "_client", None)
    monkeypatch.setattr(transport, "_verified", None)
    monkeypatch.setattr(transport, "_client_insecure", False)
    monkeypatch.setitem(output._output_config, "agentic", False)
    monkeypatch.setattr(transport, "prog", lambda: "dg")
    from deepctl_core import auth as auth_module

    monkeypatch.setattr(auth_module.keyring, "get_password", lambda *a: None)
    monkeypatch.setattr(rawhttp.time, "sleep", lambda s: None)
    monkeypatch.setenv("DEEPGRAM_API_KEY", KEY)
    yield
    if transport._client is not None:
        transport._client.close()


def facade(handler) -> tuple[DeepgramClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        request.read()
        seen.append(request)
        return handler(request)

    transport._set_client(httpx.Client(transport=httpx.MockTransport(record)))
    config = Config(config_path=str(Path.cwd() / "none.yaml"))
    return DeepgramClient(config, AuthManager(config)), seen


def audio(tmp_path: Path, size: int = 3000) -> Path:
    path = tmp_path / "a.wav"
    path.write_bytes(b"\1" * size)
    return path


def test_query_params_match_the_sdk_encoding():
    params = rawhttp.query_params(
        {"model": "nova-3", "smart_format": True},
        {"smart_format": "false", "redact": ["pci", "numbers"], "x": None},
    )
    assert params == [
        ("model", "nova-3"),
        ("smart_format", "false"),
        ("redact", "pci"),
        ("redact", "numbers"),
    ]


def test_file_body_is_returned_byte_for_byte(tmp_path):
    client, seen = facade(lambda r: httpx.Response(200, content=BODY))
    body = client.transcribe_body(
        audio(tmp_path), {"model": "nova-3", "diarize": "true"}
    )
    assert body == BODY
    (request,) = seen
    assert request.method == "POST"
    assert request.url.path == "/v1/listen"
    assert request.url.params.get("diarize") == "true"
    assert request.url.params.get("language") == "en-US"
    assert request.headers["authorization"] == f"Token {KEY}"
    assert request.headers["content-length"] == "3000"
    assert request.headers["content-type"] == "application/octet-stream"
    assert request.content == b"\1" * 3000


def test_url_source_posts_json(tmp_path):
    client, seen = facade(lambda r: httpx.Response(200, content=BODY))
    assert client.transcribe_body("https://x.test/a.mp3", {}, is_url=True) == BODY
    assert json.loads(seen[0].content) == {"url": "https://x.test/a.mp3"}


def test_gzip_body_is_decoded(tmp_path):
    client, _ = facade(
        lambda r: httpx.Response(
            200, content=gzip.compress(BODY), headers={"Content-Encoding": "gzip"}
        )
    )
    assert client.transcribe_body(audio(tmp_path)) == BODY


def test_a_coding_nobody_can_decode_is_never_returned(tmp_path):
    client, _ = facade(
        lambda r: httpx.Response(
            200, content=b"\x28\xb5\x2f\xfd...", headers={"Content-Encoding": "br2"}
        )
    )
    with pytest.raises(TransportError) as err:
        client.transcribe_body(audio(tmp_path))
    assert err.value.code == "network_unavailable"
    assert err.value.messages == transport.NETWORK_UNREADABLE


def test_zstd_is_accepted_only_when_it_can_be_decoded(tmp_path):
    client, seen = facade(lambda r: httpx.Response(200, content=BODY))
    client.transcribe_body(audio(tmp_path))
    accepted = seen[0].headers["accept-encoding"]
    decodable = rawhttp._zstd_decompress() is not None or "zstd" in (
        __import__("httpx._decoders", fromlist=["x"]).SUPPORTED_DECODERS
    )
    assert ("zstd" in accepted) == decodable


@pytest.mark.skipif(
    rawhttp._zstd_decompress() is None, reason="no stdlib zstd on this Python"
)
def test_zstd_body_is_decoded_with_the_stdlib(tmp_path):
    from compression import zstd  # type: ignore[import-not-found,unused-ignore]

    client, _ = facade(
        lambda r: httpx.Response(
            200, content=zstd.compress(BODY), headers={"Content-Encoding": "zstd"}
        )
    )
    assert client.transcribe_body(audio(tmp_path)) == BODY


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "auth_invalid"), (403, "auth_forbidden"), (400, "invalid_argument")],
)
def test_error_status_maps_and_is_not_retried(tmp_path, status, code):
    client, seen = facade(
        lambda r: httpx.Response(status, json={"err_msg": "bad " + KEY})
    )
    with pytest.raises(TransportError) as err:
        client.transcribe_body(audio(tmp_path))
    assert err.value.code == code
    assert KEY not in err.value.error
    assert len(seen) == 1


def test_5xx_and_429_are_retried_and_the_body_resent(tmp_path):
    answers = iter(
        [
            httpx.Response(503, json={}),
            httpx.Response(429, json={}, headers={"Retry-After": "1"}),
            httpx.Response(200, content=BODY),
        ]
    )
    client, seen = facade(lambda r: next(answers))
    assert client.transcribe_body(audio(tmp_path, 5000)) == BODY
    assert len(seen) == 3
    assert all(r.content == b"\1" * 5000 for r in seen)


def test_retries_stop_after_two(tmp_path):
    client, seen = facade(lambda r: httpx.Response(502, json={}))
    with pytest.raises(TransportError) as err:
        client.transcribe_body(audio(tmp_path))
    assert err.value.code == "server_error"
    assert len(seen) == 3


def test_connect_error_is_retried(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, content=BODY)

    client, _ = facade(handler)
    assert client.transcribe_body(audio(tmp_path)) == BODY
    assert len(calls) == 2


def test_a_2xx_body_cut_short_is_never_returned(tmp_path):
    class Cut(httpx.SyncByteStream):
        def __iter__(self):
            yield BODY[:10]
            raise httpx.RemoteProtocolError(
                "peer closed connection without sending complete message body"
            )

    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, stream=Cut())

    client, _ = facade(handler)
    with pytest.raises(TransportError) as err:
        client.transcribe_body(audio(tmp_path))
    assert err.value.code == "network_unavailable"
    assert err.value.messages == transport.NETWORK_CUT
    # A response had started: nothing is resent (it would be billed again).
    assert len(calls) == 1


def test_timeout_maps_with_the_request_timeout(tmp_path):
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    client, _ = facade(handler)
    with pytest.raises(TransportError) as err:
        client.transcribe_body(audio(tmp_path), timeout_s=120)
    assert err.value.code == "timeout"
    assert err.value.details["timeout_s"] == 120


def test_a_redirect_is_not_followed(tmp_path):
    client, seen = facade(
        lambda r: httpx.Response(307, headers={"Location": "https://elsewhere.test/"})
    )
    with pytest.raises(TransportError) as err:
        client.transcribe_body(audio(tmp_path))
    assert err.value.code == "network_unavailable"
    assert len(seen) == 1


def test_missing_file_raises_file_not_found(tmp_path):
    client, seen = facade(lambda r: httpx.Response(200, content=BODY))
    with pytest.raises(FileNotFoundError):
        client.transcribe_body(tmp_path / "nope.wav")
    assert seen == []


def test_corrupt_gzip_is_unreadable(tmp_path):
    coded = bytearray(gzip.compress(b'{"a":"' + b"x" * 5000 + b'"}'))
    for i in range(12, 40):
        coded[i] ^= 0xFF  # damage the deflate stream: zlib refuses it
    client, _ = facade(
        lambda r: httpx.Response(
            200, content=bytes(coded), headers={"Content-Encoding": "gzip"}
        )
    )
    with pytest.raises(TransportError) as err:
        client.transcribe_body(audio(tmp_path))
    assert err.value.code == "network_unavailable"
    assert err.value.messages == transport.NETWORK_UNREADABLE


def test_identifies_itself_as_the_sdk_path_does(tmp_path):
    """The raw request carries the same User-Agent, X-Fern headers and
    session ID the SDK sends on dg's other requests."""
    client, seen = facade(lambda r: httpx.Response(200, content=BODY))
    client.transcribe_body(audio(tmp_path))
    client.client.listen.v1.media.transcribe_file(request=b"\1")
    raw, sdk = seen
    assert raw.headers["user-agent"].startswith("deepgram-sdk/")
    for name in (
        "user-agent",
        "x-fern-language",
        "x-fern-runtime",
        "x-fern-platform",
        "x-fern-sdk-name",
        "x-fern-sdk-version",
        "x-deepgram-session-id",
        "authorization",
    ):
        assert raw.headers[name] == sdk.headers[name], name
