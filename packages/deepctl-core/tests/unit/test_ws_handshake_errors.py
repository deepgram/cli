"""A WebSocket upgrade answered with something that isn't valid HTTP, and
errors the client wraps for the SDK: plain words, no field dumps."""

from __future__ import annotations

import pytest
from deepctl_core import transport

websockets_exceptions = pytest.importorskip("websockets.exceptions")


def _wrapped(cause: BaseException) -> BaseException:
    from deepgram.core.api_error import ApiError

    try:
        try:
            raise cause
        except Exception as e:
            raise ApiError(body=f"Text-to-speech streaming failed: {e}") from None
    except ApiError as wrapped:
        wrapped.__context__ = cause
        return wrapped


def test_invalid_http_answer_maps_to_unreadable():
    exc = websockets_exceptions.InvalidMessage(
        "did not receive a valid HTTP response"
    )
    exc.__cause__ = EOFError("connection closed while reading HTTP status line")
    mapped = transport.map_exception(exc)
    assert isinstance(mapped, transport.TransportError)
    assert mapped.code == "network_unavailable"
    assert mapped.details["reason"] == transport.REASON_UNREADABLE


def test_invalid_header_maps_too():
    mapped = transport.map_ws_open(
        websockets_exceptions.InvalidHeader("Upgrade"), host="api.deepgram.com"
    )
    assert mapped is not None
    assert mapped.code == "network_unavailable"
    assert mapped.details == {
        "host": "api.deepgram.com",
        "reason": transport.REASON_UNREADABLE,
    }


def test_refused_status_still_maps_by_status():
    from websockets.datastructures import Headers
    from websockets.http11 import Response

    response = Response(401, "Unauthorized", Headers(), b"")
    mapped = transport.map_exception(websockets_exceptions.InvalidStatus(response))
    assert mapped is not None
    assert mapped.code == "auth_invalid"


def test_plain_detail_skips_the_sdk_field_dump():
    wrapped = _wrapped(RuntimeError("socket closed"))
    assert "status_code" in str(wrapped)  # the dump this avoids
    assert transport.plain_detail(wrapped) == "socket closed"


def test_plain_detail_keeps_an_ordinary_message():
    assert transport.plain_detail(ValueError("bad value")) == "bad value"
    assert transport.plain_detail(ValueError()) == "ValueError"


def test_plain_detail_keeps_an_api_error_with_a_status():
    from deepgram.core.api_error import ApiError

    err = ApiError(status_code=500, body="oops")
    assert "500" in transport.plain_detail(err)


def test_rest_redirect_names_the_running_command():
    import click

    @click.group(name="dg")
    def root() -> None:
        pass

    @root.command(name="models")
    def models() -> None:
        err = transport.for_status(
            307, {"location": "https://elsewhere.example/x"}, host="api.deepgram.com"
        )
        assert err is not None
        click.echo(err.message)

    from click.testing import CliRunner

    result = CliRunner().invoke(root, ["models"])
    assert "dg models doesn't follow redirects, so nothing was sent there." in (
        " ".join(result.output.split())
    ), result.output


def test_rest_redirect_outside_a_command_says_dg():
    err = transport.for_status(
        307, {"location": "https://elsewhere.example/x"}, host="api.deepgram.com"
    )
    assert err is not None
    assert "dg doesn't follow redirects" in err.message
