"""A REST call whose response body is returned as the server sent it.

For results that are printed as-is (``dg -o json listen``): no SDK model is
built, so the bytes on stdout are the server's JSON. The request goes
through the Transport's shared ``httpx.Client`` and retries as the SDK
does: up to two more attempts after a connect failure, a connection closed
before any response, or a 408, 409, 429 or 5xx status. Once a 2xx response
has started, nothing is retried: a body cut short raises the coded
``network_unavailable`` error and is never returned in part.
"""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING, Any

from . import transport

if TYPE_CHECKING:
    from collections.abc import Iterable

    import httpx

# The SDK's retry policy (deepgram.core.http_client), so moving a request
# off the SDK changes nothing about when it is resent.
MAX_RETRIES = 2
_INITIAL_DELAY_S = 1.0
_MAX_DELAY_S = 60.0
_JITTER = 0.2
_RETRY_STATUSES = frozenset({408, 409, 429})


def query_params(
    defaults: dict[str, Any], options: dict[str, Any]
) -> list[tuple[str, str]]:
    """Query parameters as the SDK encodes them: booleans as ``true`` or
    ``false``, a list as the parameter repeated, ``None`` left out."""
    merged = {**defaults, **options}
    params: list[tuple[str, str]] = []
    for key, value in merged.items():
        values = value if isinstance(value, (list, tuple)) else [value]
        for item in values:
            if item is None:
                continue
            if isinstance(item, bool):
                item = "true" if item else "false"
            params.append((key, str(item)))
    return params


def _zstd_decompress() -> Any:
    """The stdlib zstd decompressor (Python 3.14+), or ``None``."""
    import importlib

    try:
        module = importlib.import_module("compression.zstd")
    except ImportError:
        return None
    return module.decompress


def accept_encoding(client: httpx.Client) -> str:
    """What to accept: what httpx decodes, plus zstd when this Python can
    decode it and httpx can't."""
    from httpx._decoders import SUPPORTED_DECODERS

    codings = [c for c in SUPPORTED_DECODERS if c != "identity"]
    if "zstd" not in codings and _zstd_decompress() is not None:
        codings.append("zstd")
    return ", ".join(codings)


def _decoded(response: httpx.Response) -> bytes:
    """The whole body, decoded. httpx passes a coding it can't decode
    through unchanged, so those are decoded here or refused."""
    from httpx._decoders import SUPPORTED_DECODERS

    codings = [
        c.strip().lower()
        for c in response.headers.get_list("content-encoding", split_commas=True)
    ]
    codings = [c for c in codings if c and c != "identity"]
    unknown = [c for c in codings if c not in SUPPORTED_DECODERS]
    if not unknown:
        return response.read()
    decompress = _zstd_decompress()
    if codings != ["zstd"] or decompress is None:
        response.read()
        raise transport.unreadable_body()
    # httpx has no zstd decoder here, so read() returns the coded bytes.
    raw = response.read()
    try:
        return bytes(decompress(raw))
    except Exception as e:
        raise transport.unreadable_body() from e


def _delay(retries: int, response: httpx.Response | None) -> float:
    if response is not None:
        after = (response.headers.get("retry-after") or "").strip()
        if after.isdigit() and int(after) > 0:
            return min(float(after), _MAX_DELAY_S)
    backoff = min(_INITIAL_DELAY_S * 2.0**retries, _MAX_DELAY_S)
    return backoff * (1 + (random.random() - 0.5) * _JITTER)


def _error_body(response: httpx.Response) -> object:
    try:
        text = response.read().decode("utf-8", "replace")
    except Exception:
        return None
    import json

    try:
        return json.loads(text)
    except ValueError:
        return text


def post_for_body(
    client: httpx.Client,
    url: str,
    *,
    params: list[tuple[str, str]],
    headers: dict[str, str],
    content: Iterable[bytes] | None = None,
    json_body: Any = None,
    timeout_s: float | None = None,
    secrets: tuple[str, ...] = (),
) -> bytes:
    """POST and return the decoded 2xx body, read to the end.

    ``content`` must be re-iterable (``upload.FileBody``) so a retry sends
    it again. Raises ``TransportError`` for an error status, a body cut
    short, a body that can't be decoded, a timeout or a network failure.
    """
    import httpx

    from .upload import UploadFileError

    timeout = httpx.Timeout(timeout_s) if timeout_s is not None else None
    # ``headers`` come from Transport.auth_headers, so this request
    # identifies itself as dg's SDK requests do.
    request_headers = {"Accept-Encoding": accept_encoding(client), **headers}
    retries = 0
    while True:
        request = client.build_request(
            "POST",
            url,
            params=tuple(params),
            headers=request_headers,
            content=content,
            json=json_body,
            timeout=timeout if timeout is not None else client.timeout,
        )
        try:
            response = client.send(request, stream=True)
        except UploadFileError:
            raise
        except (httpx.ConnectError, httpx.RemoteProtocolError) as e:
            if retries < MAX_RETRIES:
                time.sleep(_delay(retries, None))
                retries += 1
                continue
            transport.raise_for(e, secrets, timeout_s=timeout_s)
            raise
        except httpx.DecodingError as e:
            # A transport that reads the body while sending (corrupt gzip).
            raise transport.unreadable_body() from e
        except Exception as e:
            transport.raise_for(e, secrets, timeout_s=timeout_s)
            raise
        try:
            status = response.status_code
            if 200 <= status < 300:
                try:
                    return _decoded(response)
                except httpx.DecodingError as e:
                    # A coded body that doesn't decode (corrupt gzip).
                    raise transport.unreadable_body() from e
                except Exception as e:
                    transport.raise_for(e, secrets, timeout_s=timeout_s)
                    raise
            if retries < MAX_RETRIES and (status >= 500 or status in _RETRY_STATUSES):
                wait = _delay(retries, response)
                response.close()
                time.sleep(wait)
                retries += 1
                continue
            body = _error_body(response)
            mapped = transport.for_status(status, dict(response.headers), body, secrets)
            if mapped is not None:
                raise mapped
            raise transport.unreadable_body()
        finally:
            response.close()
