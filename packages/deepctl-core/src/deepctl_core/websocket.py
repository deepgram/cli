"""Connections that never follow a redirect.

Every live socket the CLI opens (``dg listen`` streaming, ``dg speak`` with
Flux) carries the API key in its upgrade request and then sends the user's
audio or text. The host it goes to has passed the host check in
:mod:`deepctl_core.hosts`; a redirect would send the session somewhere
else. Depending on the websockets version, a 3xx on the upgrade is either
followed with the same headers (14 and 15, the key included), followed
without the key (16 and later, the data still goes), or raised. These
helpers turn every 3xx into :class:`RedirectRefused` before a second
connection is opened. REST calls through the SDK don't follow redirects
either (``follow_redirects=False`` in the client); :func:`redirect_refused`
words that error too, with no ``payload``.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import click

from .hosts import parse_origin
from .messages import display

E_WS_REDIRECT = (
    "{origin} answered with a redirect to {location_origin}. {command} "
    "doesn't follow redirects, so no {payload} was sent."
)
# REST: the request already reached the host that was checked; nothing
# goes to the new location.
E_HTTP_REDIRECT = (
    "{origin} answered with a redirect to {location_origin}. {command} "
    "doesn't follow redirects, so nothing was sent there."
)


class RedirectRefused(click.ClickException):
    """A request was answered with a redirect, which isn't followed."""


def redirect_refused(
    url: str, location: str | None, *, command: str, payload: str | None
) -> RedirectRefused:
    """The error for a redirect from ``url`` to ``location``.

    Only origins are shown, never paths or queries, and anything a server
    put in the Location header is made safe to print first.
    """
    here = parse_origin(url)
    there = None
    if location:
        # Header bytes outside ASCII arrive as surrogates; show them as "?".
        location = location.encode("ascii", "replace").decode("ascii")
        try:
            there = parse_origin(urljoin(url, location))
        except ValueError:  # e.g. an unbalanced "[" in the host
            there = None
    text = E_HTTP_REDIRECT if payload is None else E_WS_REDIRECT
    return RedirectRefused(
        text.format(
            origin=display(here.origin if here else "?"),
            location_origin=display(there.origin if there else "?"),
            command=command,
            payload=payload,
        )
    )


def _as_refusal(
    url: str, exc: BaseException, *, command: str, payload: str
) -> BaseException:
    """``RedirectRefused`` for a 3xx handshake error, else ``exc`` itself."""
    from websockets.exceptions import InvalidStatus

    if isinstance(exc, InvalidStatus) and 300 <= exc.response.status_code < 400:
        return redirect_refused(
            url,
            exc.response.headers.get("Location"),
            command=command,
            payload=payload,
        )
    return exc


async def open_async(
    url: str,
    headers: dict[str, str],
    *,
    command: str,
    payload: str,
    **options: Any,
) -> Any:
    """Open ``url`` and return the connection, refusing any redirect.

    Needs websockets 13.0 or later (its asyncio client); the sync variant
    works with anything the Deepgram SDK accepts. ``options`` go to
    websockets' ``connect`` (``close_timeout``, ``compression`` and so on).

    ``command`` and ``payload`` name the command and what it would have
    sent, for the error: "dg listen" and "audio", for example.
    """
    from websockets.asyncio.client import connect

    def refuse(exc: BaseException) -> BaseException:
        return _as_refusal(url, exc, command=command, payload=payload)

    class _NoRedirect(connect):
        # websockets 13.1 and later call this on a failed handshake and
        # follow whatever URI it returns; 13.0 never follows.
        def process_redirect(self, exc: Exception) -> Exception | str:
            result = refuse(exc)
            return result if isinstance(result, Exception) else exc

    try:
        return await _NoRedirect(url, additional_headers=headers, **options)
    except Exception as exc:
        refused = refuse(exc)
        if refused is exc:
            raise
        raise refused from exc


class _AsyncOpen:
    """``async with`` wrapper around :func:`open_async`."""

    def __init__(
        self,
        url: str,
        headers: dict[str, str],
        command: str,
        payload: str,
        options: dict[str, Any],
    ) -> None:
        self._args = (url, headers)
        self._kwargs = dict(options, command=command, payload=payload)
        self._ws: Any = None

    async def __aenter__(self) -> Any:
        self._ws = await open_async(*self._args, **self._kwargs)
        return self._ws

    async def __aexit__(self, *exc_info: Any) -> None:
        await self._ws.close()


def connect_async(
    url: str,
    headers: dict[str, str],
    *,
    command: str,
    payload: str,
    **options: Any,
) -> _AsyncOpen:
    """Open ``url`` for ``async with``, refusing any redirect (see open_async)."""
    return _AsyncOpen(url, headers, command, payload, options)


class _SyncOpen:
    """``with`` wrapper around websockets' sync ``connect``."""

    def __init__(
        self, url: str, headers: dict[str, str], command: str, payload: str
    ) -> None:
        self._url = url
        self._headers = headers
        self._command = command
        self._payload = payload
        self._ws: Any = None

    def _refuse(self, exc: BaseException) -> BaseException:
        return _as_refusal(self._url, exc, command=self._command, payload=self._payload)

    def __enter__(self) -> Any:
        from websockets.sync import client

        connecter: Any = getattr(client, "reconnect", None)
        try:
            if connecter is not None and hasattr(connecter, "process_redirect"):
                # From 17.1, sync connect() runs a ``reconnect`` object that
                # follows redirects; open through a subclass that doesn't.
                refuse = self._refuse

                class _NoRedirect(connecter):  # type: ignore[misc]
                    def process_redirect(self, exc: Exception) -> Exception | str:
                        result = refuse(exc)
                        return result if isinstance(result, Exception) else exc

                self._ws = _NoRedirect(
                    self._url, additional_headers=self._headers
                ).connect()
                self._ws.pending_legacy_warning = False
            else:
                # Before 17.1, sync connect() never follows a redirect.
                self._ws = client.connect(self._url, additional_headers=self._headers)
        except Exception as exc:
            refused = self._refuse(exc)
            if refused is exc:
                raise
            raise refused from exc
        return self._ws

    def __exit__(self, *exc_info: Any) -> None:
        self._ws.close()


def connect_sync(
    url: str, headers: dict[str, str], *, command: str, payload: str
) -> _SyncOpen:
    """Open ``url`` for ``with``, refusing any redirect (see connect_async)."""
    return _SyncOpen(url, headers, command, payload)
