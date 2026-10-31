"""Where API requests go, what they carry, and what their failures mean.

:class:`Transport` owns the REST and WebSocket base URLs, which come only
from :func:`deepctl_core.hosts.check_credential_host` (``http`` -> ``ws``,
``https`` -> ``wss``), and attaches ``Authorization`` only after that check
has passed for the exact origin. Requests go through :func:`shared_client`:
one ``httpx.Client`` (one connection pool) per process, created on first use
so ``--version`` and ``--help`` never import httpx. It follows no redirects
(a 3xx with credentials is an error, never a second request), keeps no
cookies, and verifies TLS. A person's ``DEEPGRAM_CLI_INSECURE=1`` (or
``true``) skips verification for localhost, 127.0.0.1 and [::1] only, and
never under an AI agent (``auth._insecure_allowed`` and
``auth._insecure_mounts``, as in 0.4.2). :func:`verified_client` is the
always-verified client for ``dg api``; WebSocket streams always verify.
The SDK facade builds its client through :attr:`Transport.backend`, a seam
for other backends (#142's SageMaker), with the REST backend as the default.

:func:`raise_for` turns a failed request into a :class:`TransportError` with a
catalogue code (``transport_mapping`` in ``error_codes.json``, in
order): DNS, connect, TLS, reset, a truncated 2xx body or a 3xx ->
``network_unavailable``; timeout -> ``timeout``; 401 -> ``auth_invalid``;
403 -> ``auth_forbidden``; 404 -> ``not_found``; 429 -> ``rate_limited``;
400, 422 and any other 4xx -> ``invalid_argument``; 5xx -> ``server_error``.
A WebSocket upgrade refused with an HTTP status maps through the same table.
``BaseCommand.execute`` renders it: a coded result on stdout under a machine
format, and two stderr lines.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import sys
import threading
from typing import TYPE_CHECKING, Any

from . import credentials

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx

    from .auth import AuthManager
    from .config import Config

# The SDK's own default for a client it builds itself.
DEFAULT_TIMEOUT_S = 60.0

_lock = threading.Lock()
_client: httpx.Client | None = None
# True when _client has DEEPGRAM_CLI_INSECURE's unverified loopback mounts.
_client_insecure = False
# The always-verified client for dg api, when _client isn't one.
_verified: httpx.Client | None = None
# Where the most recent request went, for the error text: method, host and
# path only (never the query string, which can carry values from argv).
_last_request: dict[str, str] = {}


def _record_request(request: httpx.Request) -> None:
    _last_request.clear()
    _last_request.update(
        method=request.method,
        host=request.url.host,
        path=request.url.path,
        origin=f"{request.url.scheme}://{request.url.netloc.decode('ascii')}",
    )


def _install(client: httpx.Client) -> httpx.Client:
    _no_cookies(client)
    # A redirect could take the key, or a request body, to another origin
    # a 3xx is an error (see map_exception), never followed.
    client.follow_redirects = False
    hooks = client.event_hooks
    hooks.setdefault("request", [])
    if _record_request not in hooks["request"]:
        hooks["request"] = [*hooks["request"], _record_request]
        client.event_hooks = hooks
    return client


def _new_client(*, insecure_loopback: bool) -> httpx.Client:
    import httpx

    from .auth import _insecure_mounts, _warn_if_verified

    if insecure_loopback:
        # As AuthManager.client (0.4.2): verification stays on, and only the
        # loopback hosts get unverified transports.
        return httpx.Client(
            timeout=DEFAULT_TIMEOUT_S,
            follow_redirects=False,
            verify=True,
            mounts=_insecure_mounts(),
            event_hooks={"request": [_warn_if_verified]},
        )
    return httpx.Client(timeout=DEFAULT_TIMEOUT_S, follow_redirects=False)


def shared_client() -> httpx.Client:
    """The process's one ``httpx.Client``, created on first use.

    It follows no redirects, keeps no cookies and verifies TLS. A person's
    ``DEEPGRAM_CLI_INSECURE=1`` (or ``true``) skips verification for
    localhost, 127.0.0.1 and [::1] only; never under an AI agent
    (``auth._insecure_allowed``, ``auth._insecure_mounts``).
    """
    global _client, _client_insecure
    with _lock:
        if _client is None:
            from .auth import _insecure_allowed

            _client_insecure = _insecure_allowed()
            _client = _install(_new_client(insecure_loopback=_client_insecure))
        return _client


def verified_client() -> httpx.Client:
    """A client that verifies TLS for every host (``dg api``, as in 0.4.1).

    The shared client itself, unless ``DEEPGRAM_CLI_INSECURE`` gave it
    unverified loopback transports; then a second client that has none.
    """
    global _verified
    client = shared_client()
    with _lock:
        if not _client_insecure:
            return client
        if _verified is None:
            _verified = _install(_new_client(insecure_loopback=False))
        return _verified


def _no_cookies(client: httpx.Client) -> None:
    """Keep no cookies: one process can serve several profiles and hosts."""
    from http.cookiejar import DefaultCookiePolicy

    client.cookies.jar.set_policy(DefaultCookiePolicy(allowed_domains=[]))


def _set_client(client: httpx.Client | None) -> None:
    """Replace the shared client (tests pass one with an ``httpx.MockTransport``)."""
    global _client, _client_insecure, _verified
    with _lock:
        _client = _install(client) if client is not None else None
        _client_insecure = False
        _verified = None
        _last_request.clear()


# ── Base URLs and credentials ────────────────────────────────────────


def ws_url(base_url: str) -> str:
    """The WebSocket form of a REST base URL: ``http`` -> ``ws``,
    ``https`` (or none) -> ``wss``; host, port and path unchanged."""
    scheme, sep, rest = base_url.partition("://")
    if not sep:
        return f"wss://{base_url}"
    scheme = scheme.lower()
    if scheme in ("http", "ws"):
        return f"ws://{rest}"
    return f"wss://{rest}"


def rest_url(base_url: str) -> str:
    """The REST form of a base URL: ``ws`` -> ``http``, ``wss`` (or none) ->
    ``https``."""
    scheme, sep, rest = base_url.partition("://")
    if not sep:
        return f"https://{base_url}"
    scheme = scheme.lower()
    if scheme in ("http", "ws"):
        return f"http://{rest}"
    return f"https://{rest}"


def default_host() -> str:
    """The Deepgram API host requests go to by default (``hosts.DEFAULT_BASE_URL``)."""
    from urllib.parse import urlsplit

    from .hosts import DEFAULT_BASE_URL

    return urlsplit(DEFAULT_BASE_URL).hostname or DEFAULT_BASE_URL


# One random ID per run, sent as x-deepgram-session-id with every request
# that carries the key, so Deepgram can group a run's requests. It says
# nothing about the user or the machine.
_session_id: str | None = None

SESSION_HEADER = "x-deepgram-session-id"


def session_id() -> str:
    """This run's ``x-deepgram-session-id``, the same on every request."""
    import uuid

    global _session_id
    if _session_id is None:
        _session_id = str(uuid.uuid4())
    return _session_id


def request_headers(api_key: str) -> dict[str, str]:
    """The headers every Deepgram API request and WebSocket open carries.

    The SDK's identification (``User-Agent: deepgram-sdk/<version>`` and its
    ``X-Fern-*`` headers, built as its ``get_headers`` builds them), this
    run's session ID, and ``Authorization``. The SDK client gets the same
    session ID (:class:`RestBackend`), so requests dg sends itself and
    requests the SDK sends identify the run alike. Callers check the host
    first; :meth:`Transport.auth_headers` does.
    """
    import platform
    from importlib import metadata

    headers: dict[str, str] = {}
    try:
        version = metadata.version("deepgram-sdk")
    except metadata.PackageNotFoundError:
        pass
    else:
        headers = {
            "User-Agent": "deepgram-sdk/" + version,
            "X-Fern-Language": "Python",
            "X-Fern-Runtime": f"python/{platform.python_version()}",
            "X-Fern-Platform": f"{platform.system().lower()}/{platform.release()}",
            "X-Fern-SDK-Name": "deepgram-sdk",
            "X-Fern-SDK-Version": version,
        }
    headers[SESSION_HEADER] = session_id()
    headers["Authorization"] = f"Token {api_key}"
    return headers


class RestBackend:
    """The default backend: the Deepgram SDK over the shared ``httpx.Client``.

    A backend turns a checked base URL and a key into the SDK client the
    facade uses. #142's SageMaker backend plugs in here (R-16).
    """

    name = "rest"

    def sdk_client(self, api_key: str, rest_base: str, ws_base: str) -> Any:
        # Through the facade module's lazily bound names (tests patch them).
        from . import client as facade
        from .hosts import DEFAULT_BASE_URL

        facade._sdk()
        sdk: dict[str, Any] = vars(facade)

        kwargs: dict[str, Any] = {
            "api_key": api_key,
            # One connection pool per process; requests reuse its connection.
            "httpx_client": shared_client(),
            # The run's ID, so SDK requests match request_headers().
            "session_id": session_id(),
        }
        if rest_base != DEFAULT_BASE_URL:
            # Both the REST and the WebSocket endpoints come from the checked
            # base URL, so a custom base works for batch REST and streaming.
            # agent_rest is required by the SDK environment.
            kwargs["environment"] = sdk["DeepgramClientEnvironment"](
                base=rest_base,
                production=ws_base,
                agent=ws_base,
                agent_rest=rest_base,
            )
        return sdk["DGClient"](**kwargs)


class Transport:
    """Where this run's API requests go and what they carry.

    Base URLs come from :func:`deepctl_core.hosts.check_credential_host`
    only; it raises ``CredentialHostError`` (already reported) when the host
    isn't trusted with credentials, before anything carries the key.
    """

    def __init__(
        self,
        config: Config,
        backend: Any = None,
        *,
        check_host: Callable[[], str] | None = None,
    ) -> None:
        """``check_host`` is ``AuthManager.check_base_url`` when there is one
        (the same ``check_credential_host`` call on the same config)."""
        self.config = config
        self.backend = backend if backend is not None else RestBackend()
        self._check_host = check_host

    def rest_base(self) -> str:
        """The checked REST base URL (scheme, host, port and path)."""
        if self._check_host is not None:
            return rest_url(self._check_host())
        from .hosts import check_credential_host

        return rest_url(check_credential_host(self.config))

    def ws_base(self) -> str:
        """The checked WebSocket base URL (``http`` -> ``ws``, ``https`` -> ``wss``)."""
        return ws_url(self.rest_base())

    def auth_headers(self, api_key: str) -> dict[str, str]:
        """:func:`request_headers` for ``api_key`` (identification, session
        ID and ``Authorization``), once the host check has passed."""
        self.rest_base()
        return request_headers(api_key)

    @property
    def client(self) -> httpx.Client:
        """The process's shared ``httpx.Client``."""
        return shared_client()

    def sdk_client(self, api_key: str) -> Any:
        """The SDK client for ``api_key``, built by the backend once the host
        check has passed."""
        rest = self.rest_base()
        return self.backend.sdk_client(api_key, rest, ws_url(rest))


# ── Catalogue text (error_codes.json "strings") ──────────────────────

STRINGS: dict[str, str] = {
    # The 0.4.x error text, frozen: scripts match on it (legacy_error_text_frozen).
    "E-AUTH-REQUIRED": "DEEPGRAM_API_KEY is not set",
    "H-AUTH-REQUIRED": "Run {prog} login, or set DEEPGRAM_API_KEY.",
    "A-AUTH-REQUIRED": (
        "Run {prog} login and give the user the verification link and code it "
        "prints. Never ask the user for an API key in a chat, and don't search "
        "files for one."
    ),
    "C-AUTH-REQUIRED": (
        "Set DEEPGRAM_API_KEY in this job's environment, or run {prog} login."
    ),
    # 0.4.1's messages.E_KEY_UNTRUSTED and W_CFG_UNTRUSTED_FILE_HINT, verbatim.
    "E-KEY-UNTRUSTED": (
        "dg didn't use the API key in {user_config} (see the warning above), and no "
        "other API key is set."
    ),
    "W_CFG_UNTRUSTED_FILE_HINT": (
        "A person must fix this file. If you are an AI agent, ask the user."
    ),
    "E-AUTH-INVALID": "Deepgram rejected the API key (HTTP 401).",
    "H-AUTH-INVALID": (
        "The API key may have expired or been revoked. Run {prog} login to log "
        "in again."
    ),
    "A-AUTH-INVALID": (
        "The API key was rejected. Run {prog} login and give the user the "
        "verification link and code it prints."
    ),
    "A-AUTH-INVALID-ENV": (
        "The DEEPGRAM_API_KEY in this environment was rejected. next_step starts "
        "a login whose saved key will take precedence over it; give the user the "
        "link and code it prints."
    ),
    "A-AUTH-INVALID-ENV-NULL": (
        "The DEEPGRAM_API_KEY in this environment was rejected. Ask the user to "
        "fix the key, or to run {prog} login --yes in their terminal."
    ),
    "C-AUTH-INVALID": (
        "The API key was rejected. Set a valid DEEPGRAM_API_KEY in this job's "
        "environment."
    ),
    "E-AUTH-FORBIDDEN": ("This API key isn't allowed to make this request (HTTP 403)."),
    "H-AUTH-FORBIDDEN": (
        "Use an API key with the needed permissions, or run {prog} login with "
        "an account that has access."
    ),
    "A-AUTH-FORBIDDEN": (
        "The API key lacks a permission this request needs. Tell the user; "
        "they can run {prog} login with an account that has access."
    ),
    "E-NETWORK": "Couldn't reach {host}: {reason}.",
    "H-NETWORK": "Check your network connection and proxy settings, then try again.",
    "A-NETWORK": (
        "{prog} can't reach Deepgram from this environment. If you run in a "
        "sandbox, ask the user to allow network access for {prog}."
    ),
    "E-NETWORK-CUT": "The connection to {host} dropped before the response finished.",
    "H-NETWORK-CUT": (
        "Try again. If it keeps happening, a proxy or firewall may be cutting off "
        "responses."
    ),
    "A-NETWORK-CUT": (
        "Retry once. If it fails again, tell the user the connection to Deepgram "
        "keeps dropping."
    ),
    "E-NETWORK-DROPPED": (
        "The connection to {host} dropped before the response was complete ({reason})."
    ),
    "E-NETWORK-UNREADABLE": "Got a response from {host} that {prog} couldn't read.",
    "H-NETWORK-UNREADABLE": (
        "A proxy, captive portal or firewall may be answering in Deepgram's "
        "place. Check that this network lets {prog} reach {host}, then try again."
    ),
    "A-NETWORK-UNREADABLE": (
        "Retry once. If it fails again, tell the user that something on this "
        "network (a proxy, captive portal or firewall) is answering in "
        "Deepgram's place."
    ),
    # 0.4.1's redirect refusal (origins only); {sent} is "nothing was sent
    # there" for REST and "no audio was sent" (or text) for a WebSocket.
    "E-NETWORK-REDIRECT": (
        "{origin} answered with a redirect to {location_origin}. {command} "
        "doesn't follow redirects, so {sent}."
    ),
    "H-NETWORK-REDIRECT": (
        "A network sign-in page or proxy may be redirecting requests. If {host} "
        "is your own server, use the address it redirects to."
    ),
    "A-NETWORK-REDIRECT": (
        "Tell the user {host} redirected the request. Don't retry until they "
        "have checked the network or the configured URL."
    ),
    "E-TIMEOUT": "{host} didn't respond within {timeout_s} s.",
    "H-TIMEOUT": "Try again. Large files take longer to upload on slow connections.",
    "A-TIMEOUT": (
        "Try again once. If it times out again, tell the user the network is "
        "slow or blocked."
    ),
    "E-RATE-LIMITED": "Deepgram is rate limiting requests (HTTP 429).",
    "H-RATE-LIMITED": "Wait a moment and try again.",
    "A-RATE-LIMITED": (
        "Wait the number of seconds in details.retry_after (or 10 s if absent) "
        "before retrying."
    ),
    "E-NOT-FOUND": "Deepgram returned 404 Not Found for {path}.",
    "H-NOT-FOUND": "Check the ID, and that it belongs to the selected project.",
    "A-NOT-FOUND": (
        "Check the ID. List the available items first instead of guessing IDs."
    ),
    "E-INVALID-REQUEST": (
        "Deepgram rejected the request (HTTP {http_status}): {api_message}"
    ),
    "H-INVALID-REQUEST": (
        "Check the options you passed. Run {prog} {command_path} --help to see "
        "valid values."
    ),
    "A-INVALID-REQUEST": (
        "Fix the option named in the message and retry. Run {prog} "
        "{command_path} --help for valid values."
    ),
    "E-SERVER": "Deepgram returned an error (HTTP {http_status}).",
    "H-SERVER": (
        "Try again in a moment. If it keeps failing, contact Deepgram support "
        "with the request ID in details."
    ),
    "A-SERVER": (
        "Retry once after a few seconds. If it fails again, tell the user and "
        "include details.request_id."
    ),
}

# code -> (error message ID, human hint, agent hint, automation hint or None)
Messages = tuple[str, str, str, "str | None"]
CODES: dict[str, Messages] = {
    "auth_required": (
        "E-AUTH-REQUIRED",
        "H-AUTH-REQUIRED",
        "A-AUTH-REQUIRED",
        "C-AUTH-REQUIRED",
    ),
    "auth_invalid": (
        "E-AUTH-INVALID",
        "H-AUTH-INVALID",
        "A-AUTH-INVALID",
        "C-AUTH-INVALID",
    ),
    "auth_forbidden": (
        "E-AUTH-FORBIDDEN",
        "H-AUTH-FORBIDDEN",
        "A-AUTH-FORBIDDEN",
        None,
    ),
    "network_unavailable": ("E-NETWORK", "H-NETWORK", "A-NETWORK", None),
    "timeout": ("E-TIMEOUT", "H-TIMEOUT", "A-TIMEOUT", None),
    "rate_limited": ("E-RATE-LIMITED", "H-RATE-LIMITED", "A-RATE-LIMITED", None),
    "not_found": ("E-NOT-FOUND", "H-NOT-FOUND", "A-NOT-FOUND", None),
    "invalid_argument": (
        "E-INVALID-REQUEST",
        "H-INVALID-REQUEST",
        "A-INVALID-REQUEST",
        None,
    ),
    "server_error": ("E-SERVER", "H-SERVER", "A-SERVER", None),
}

# network_unavailable's other messages: the connection dropped after the
# request started, the response was cut off, it arrived complete but isn't
# Deepgram's (a proxy or captive portal page), or it was a redirect.
NETWORK_DROPPED: Messages = (
    "E-NETWORK-DROPPED",
    "H-NETWORK-CUT",
    "A-NETWORK-CUT",
    None,
)
NETWORK_CUT: Messages = ("E-NETWORK-CUT", "H-NETWORK-CUT", "A-NETWORK-CUT", None)
NETWORK_UNREADABLE: Messages = (
    "E-NETWORK-UNREADABLE",
    "H-NETWORK-UNREADABLE",
    "A-NETWORK-UNREADABLE",
    None,
)
NETWORK_REDIRECT: Messages = (
    "E-NETWORK-REDIRECT",
    "H-NETWORK-REDIRECT",
    "A-NETWORK-REDIRECT",
    None,
)

# auth_required's other message: the only key was in a user config file this
# AI agent session doesn't trust. next_step is null: a person
# has to fix the file.
KEY_UNTRUSTED: Messages = (
    "E-KEY-UNTRUSTED",
    "W_CFG_UNTRUSTED_FILE_HINT",
    "W_CFG_UNTRUSTED_FILE_HINT",
    None,
)

# Network failure reasons for E-NETWORK's {reason}.
REASON_DNS = "name resolution failed"
REASON_REFUSED = "connection refused"
REASON_TLS = "TLS handshake failed"
REASON_RESET = "connection reset"
REASON_CLOSED = "connection closed"
REASON_TRUNCATED = "response cut off"
REASON_UNREADABLE = "unreadable response"
REASON_PROXY = "the proxy refused the connection"
REASON_REDIRECT = "unexpected redirect"
REASON_OTHER = "connection failed"

KEY_SOURCE_ENV = credentials.KEY_SOURCE_ENV
KEY_SOURCE_FLAG = credentials.KEY_SOURCE_FLAG
KEY_SOURCE_KEYRING = credentials.KEY_SOURCE_KEYRING
KEY_SOURCE_FILE = credentials.KEY_SOURCE_FILE


class TransportError(BaseException):
    """A request failed at the transport or with an HTTP error status.

    A ``BaseException`` on purpose: command handlers wrap their work in
    ``except Exception`` and turn anything into a legacy error result. This
    one has to reach ``BaseCommand.execute``, which prints the coded error.
    Anything else that catches it should re-raise it.
    """

    def __init__(
        self,
        code: str,
        values: dict[str, Any],
        details: dict[str, Any],
        messages: Messages | None = None,
    ) -> None:
        self.code = code
        self.values = values
        self.details = details
        self.messages = messages or CODES[code]
        super().__init__(self.error)

    @property
    def error(self) -> str:
        return STRINGS[self.messages[0]].format(**self.values)


class RedirectRefused(TransportError):
    """A request was answered with a redirect, which is never followed.

    REST and WebSocket alike: the key and the request's audio or text go
    only to the host that passed the host check. ``message`` and
    ``exit_code`` read as on the ``click.ClickException`` it was in 0.4.1.
    """

    exit_code = 1

    @property
    def message(self) -> str:
        return self.error


def redirect_refused(
    url: str,
    location: str | None,
    *,
    command: str = "dg",
    payload: str | None = None,
) -> RedirectRefused:
    """The error for a redirect from ``url`` to ``location``.

    Only origins are shown, never paths or queries, and anything a server
    put in the Location header is made safe to print first. ``payload``
    names what a WebSocket would have sent ("audio", "text"); ``None`` for
    REST, where the request reached ``url`` and nothing went further.
    """
    from urllib.parse import urljoin

    from .hosts import parse_origin
    from .messages import display

    here = parse_origin(url)
    there = None
    if location:
        # Header bytes outside ASCII arrive as surrogates; show them as "?".
        location = location.encode("ascii", "replace").decode("ascii")
        try:
            there = parse_origin(urljoin(url, location))
        except ValueError:  # e.g. an unbalanced "[" in the host
            there = None
    host = _clean(here.host if here else url)
    sent = "nothing was sent there" if payload is None else f"no {payload} was sent"
    return RedirectRefused(
        "network_unavailable",
        {
            "origin": display(here.origin if here else "?"),
            "location_origin": display(there.origin if there else "?"),
            "command": command,
            "sent": sent,
            "host": host,
            "prog": prog() or "dg",
        },
        {"host": host, "reason": REASON_REDIRECT},
        NETWORK_REDIRECT,
    )


# ── Mapping ──────────────────────────────────────────────────────────

_SECRET_URL = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@]*@")
_QUERY = re.compile(r"\?\S*")


def _clean(text: object, secrets: tuple[str, ...] = ()) -> str:
    """Untrusted text for ``error``."""
    from . import messages

    value = str(text)
    for secret in secrets:
        if secret:
            value = value.replace(secret, "****")
    value = _SECRET_URL.sub(r"\1", value)
    value = _QUERY.sub("", value)
    return messages.display(" ".join(value.split()))


def _chain(exc: BaseException) -> list[BaseException]:
    seen: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in seen:
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


def _network_reason(exc: BaseException) -> str:
    import socket
    import ssl

    import httpx

    chain = _chain(exc)
    for item in chain:
        if isinstance(item, socket.gaierror):
            return REASON_DNS
        if isinstance(item, ssl.SSLError):
            return REASON_TLS
        if isinstance(item, ConnectionRefusedError):
            return REASON_REFUSED
        if isinstance(item, ConnectionResetError):
            return REASON_RESET
    text = " ".join(str(item) for item in chain).lower()
    if "name or service not known" in text or "nodename nor servname" in text:
        return REASON_DNS
    if "temporary failure in name resolution" in text or "getaddrinfo" in text:
        return REASON_DNS
    if "ssl" in text or "certificate" in text or "tls" in text:
        return REASON_TLS
    if "connection refused" in text:
        return REASON_REFUSED
    if isinstance(exc, httpx.RemoteProtocolError):
        if "incomplete" in text or "message body" in text:
            return REASON_TRUNCATED
        return REASON_CLOSED
    if isinstance(exc, (httpx.ReadError, httpx.WriteError)) or "reset" in text:
        return REASON_RESET
    return REASON_OTHER


def _host(exc: BaseException | None = None) -> str:
    host = None
    try:
        host = exc.request.url.host  # type: ignore[union-attr]
    except (AttributeError, RuntimeError):
        host = _last_request.get("host")
    return _clean(host or "the Deepgram API")


def _header(headers: object, name: str) -> str | None:
    if not isinstance(headers, dict):
        return None
    for key, value in headers.items():
        if str(key).lower() == name:
            return str(value)
    return None


def _api_message(body: object, status: int, secrets: tuple[str, ...]) -> str:
    text: object = None
    if isinstance(body, dict):
        for key in ("err_msg", "message", "error", "detail"):
            if body.get(key):
                text = body[key]
                break
    elif isinstance(body, str) and body.strip():
        text = body
    if text is None:
        from http import HTTPStatus

        try:
            text = HTTPStatus(status).phrase
        except ValueError:
            text = "no details"
    return _clean(text, secrets)


def map_exception(
    exc: BaseException,
    secrets: tuple[str, ...] = (),
    *,
    timeout_s: float | None = None,
) -> TransportError | None:
    """The coded error for a failed request, or ``None`` when ``exc`` isn't one.

    ``timeout_s`` is the request's timeout when it isn't the default.
    """
    if isinstance(exc, TransportError):
        return exc
    if "httpx" in sys.modules:
        import httpx

        if isinstance(exc, httpx.TimeoutException):
            host = _host(exc)
            seconds = _seconds(timeout_s or DEFAULT_TIMEOUT_S)
            return TransportError(
                "timeout",
                {"host": host, "timeout_s": seconds},
                {"host": host, "timeout_s": seconds},
            )
        if isinstance(exc, httpx.ProxyError):
            host = _host(exc)
            return TransportError(
                "network_unavailable",
                {"host": host, "reason": REASON_PROXY},
                {"host": host, "reason": REASON_PROXY},
            )
        if isinstance(exc, (httpx.NetworkError, httpx.RemoteProtocolError)):
            host = _host(exc)
            reason = _network_reason(exc)
            messages: Messages | None = None
            if reason == REASON_TRUNCATED:
                messages = NETWORK_CUT
            elif reason in (REASON_RESET, REASON_CLOSED) and isinstance(
                exc, (httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError)
            ):
                # The connection was open and the request had started, so
                # "couldn't reach" would be wrong. A reset while connecting
                # (ConnectError) stays E-NETWORK.
                messages = NETWORK_DROPPED
            return TransportError(
                "network_unavailable",
                {"host": host, "reason": reason},
                {"host": host, "reason": reason},
                messages,
            )

    ws = _map_websocket(exc, secrets)
    if ws is not None:
        return ws

    status = getattr(exc, "status_code", None)
    if not isinstance(status, int) or "deepgram" not in sys.modules:
        return None
    from deepgram.core.api_error import ApiError

    if not isinstance(exc, ApiError):
        return None
    if 200 <= status < 300:
        # The SDK got a complete success response it couldn't parse: most
        # often not Deepgram's at all (a proxy's or captive portal's page).
        # A body cut short raises RemoteProtocolError above instead.
        host = _host()
        return TransportError(
            "network_unavailable",
            {"host": host, "reason": REASON_UNREADABLE, "prog": prog() or "dg"},
            {"host": host, "reason": REASON_UNREADABLE},
            NETWORK_UNREADABLE,
        )
    return for_status(status, exc.headers, exc.body, secrets)


def for_status(
    status: int,
    headers: object = None,
    body: object = None,
    secrets: tuple[str, ...] = (),
    *,
    host: str | None = None,
    path: str | None = None,
) -> TransportError | None:
    """The coded error for an HTTP error status, REST or WebSocket upgrade.

    ``host`` and ``path`` name the request when it didn't go through the
    shared client (a WebSocket upgrade); else the last request's are used.
    """
    if 300 <= status < 400:
        # Credentialed requests don't follow redirects.
        origin = host or _last_request.get("origin") or _host()
        return redirect_refused(
            origin, _header(headers, "location"), command=_running_command()
        )
    if status == 401:
        return TransportError("auth_invalid", {}, {"http_status": 401})
    if status == 403:
        return TransportError("auth_forbidden", {}, {"http_status": 403})
    if status == 404:
        where = _clean(path or _last_request.get("path") or "this request")
        return TransportError(
            "not_found", {"path": where}, {"http_status": 404, "path": where}
        )
    if status == 429:
        details: dict[str, Any] = {"http_status": 429}
        retry_after = _header(headers, "retry-after")
        if retry_after and retry_after.strip().isdigit():
            details["retry_after"] = int(retry_after.strip())
        return TransportError("rate_limited", {}, details)
    if 400 <= status < 500:
        return TransportError(
            "invalid_argument",
            {
                "http_status": status,
                "api_message": _api_message(body, status, secrets),
            },
            {"http_status": status},
        )
    if 500 <= status < 600:
        details = {"http_status": status}
        request_id = _header(headers, "dg-request-id")
        if request_id:
            details["request_id"] = _clean(request_id)
        return TransportError("server_error", {"http_status": status}, details)
    return None


# Seconds a WebSocket handshake may take before it maps to ``timeout``.
WS_OPEN_TIMEOUT_S = 10.0


def _ws_status(exc: BaseException) -> tuple[int, object, object] | None:
    """(status, headers, body) of a refused WebSocket upgrade, if ``exc`` is one."""
    if "websockets" in sys.modules:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        if isinstance(status, int) and type(exc).__module__.startswith("websockets"):
            headers = getattr(response, "headers", None)
            body = getattr(response, "body", None)
            if isinstance(body, bytes):
                body = body.decode("utf-8", "replace")
            return status, dict(headers or {}), body
        status = getattr(exc, "status_code", None)  # legacy InvalidStatusCode
        if isinstance(status, int) and type(exc).__module__.startswith("websockets"):
            return status, dict(getattr(exc, "headers", None) or {}), None
    return None


def _timeout_types() -> tuple[type[BaseException], ...]:
    types: list[type[BaseException]] = [TimeoutError]
    if "asyncio" in sys.modules:
        import asyncio

        types.append(asyncio.TimeoutError)  # a separate class before 3.11
    return tuple(types)


def _map_websocket(
    exc: BaseException,
    secrets: tuple[str, ...],
    *,
    host: str | None = None,
    path: str | None = None,
    opening: bool = False,
) -> TransportError | None:
    """A WebSocket open that failed: refused upgrade, network or timeout."""
    refused = _ws_status(exc)
    if refused is not None:
        status, headers, body = refused
        return for_status(status, headers, body, secrets, host=host, path=path)
    if not opening and "websockets" in sys.modules:
        opening = type(exc).__module__.startswith("websockets")
    if not opening:
        return None
    where = _clean(host) if host else _host(exc)
    if any(isinstance(item, _timeout_types()) for item in _chain(exc)):
        return TransportError(
            "timeout",
            {"host": where, "timeout_s": _seconds(WS_OPEN_TIMEOUT_S)},
            {"host": where, "timeout_s": _seconds(WS_OPEN_TIMEOUT_S)},
        )
    if any(isinstance(item, OSError) for item in _chain(exc)):
        reason = _network_reason(exc)
        return TransportError(
            "network_unavailable",
            {"host": where, "reason": reason},
            {"host": where, "reason": reason},
        )
    if "websockets" in sys.modules:
        from websockets.exceptions import InvalidHandshake

        # The server answered the upgrade with something that isn't a valid
        # HTTP response (a proxy, HTTP/1.0, a cut-off reply).
        if any(isinstance(item, InvalidHandshake) for item in _chain(exc)):
            return TransportError(
                "network_unavailable",
                {"host": where, "reason": REASON_UNREADABLE, "prog": prog() or "dg"},
                {"host": where, "reason": REASON_UNREADABLE},
                NETWORK_UNREADABLE,
            )
    return None


def map_ws_open(
    exc: BaseException,
    secrets: tuple[str, ...] = (),
    *,
    host: str | None = None,
    path: str | None = None,
) -> TransportError | None:
    """The coded error for a WebSocket open to ``host`` that raised ``exc``.

    A refused upgrade maps by its HTTP status through the REST table; DNS,
    connect and TLS failures map to ``network_unavailable``; an open that
    timed out maps to ``timeout``.
    """
    if isinstance(exc, TransportError):
        return exc
    return _map_websocket(exc, secrets, host=host, path=path, opening=True)


def _seconds(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


def plain_detail(exc: BaseException) -> str:
    """What went wrong, in the words of the error that caused it.

    The client wraps failures it can't map in the SDK's ``ApiError``,
    whose text is a dump of its fields (``headers: None, status_code:
    None, body: ...``). For one with no status, this is the message of
    the error it was raised from; anything else is its own message.
    """
    for item in _chain(exc):
        if (
            type(item).__name__ == "ApiError"
            and getattr(item, "status_code", 1) is None
        ):
            continue
        text = str(item).strip()
        if text:
            return text
        return type(item).__name__
    body = getattr(exc, "body", None)
    return str(body) if body else type(exc).__name__


def _running_command() -> str:
    """The command being run, as typed (``dg listen``), for messages that
    name it; ``dg`` outside a command."""
    if "click" in sys.modules:
        import click

        ctx = click.get_current_context(silent=True)
        if ctx is not None and ctx.command_path:
            return ctx.command_path
    return "dg"


def raise_for(
    exc: BaseException,
    secrets: tuple[str, ...] = (),
    *,
    timeout_s: float | None = None,
) -> None:
    """Raise the coded :class:`TransportError` for ``exc``, if it maps to one."""
    mapped = map_exception(exc, secrets, timeout_s=timeout_s)
    if mapped is not None and mapped is not exc:
        raise mapped from exc
    if mapped is not None:
        raise mapped


# ── Rendering ────────────────────────────────────────────────────────

_PROG_NAMES = ("dg", "deepgram", "deepctl")


def prog() -> str | None:
    """``{prog}``: the running executable, as a command.

    The bare name when ``which(name)`` is this same file; else the console
    script's absolute path, quoted. ``None`` when neither applies (``python
    -m``, an unknown launcher), which makes ``next_step`` null.
    A stable command name replaces the fallback in a later release.
    """
    argv0 = sys.argv[0] if sys.argv else ""
    name = os.path.basename(argv0)
    if os.name == "nt" and name.lower().endswith(".exe"):
        name = name[:-4]
    if name not in _PROG_NAMES:
        return None
    found = shutil.which(name)
    running = shutil.which(argv0) or argv0
    try:
        if found and os.path.samefile(found, running):
            return name
    except OSError:
        pass
    if not os.path.isfile(running):
        return None
    path = os.path.abspath(running)
    if os.name == "nt":
        return None if shlex.quote(path) != path else path
    return shlex.quote(path)


def _key_source(auth_manager: AuthManager | None) -> str | None:
    """Where the key that was sent came from: the resolver's answer, so the
    error path reads the keyring no second time."""
    if auth_manager is None:
        return None
    if auth_manager.explicit_api_key:
        return KEY_SOURCE_FLAG
    try:
        source = auth_manager.resolve().key_source
    except Exception:
        return None
    return None if source == credentials.KEY_SOURCE_NONE else source


def render(
    err: TransportError,
    *,
    command_path: str,
    auth_manager: AuthManager | None = None,
) -> Any:
    """The coded result for ``err``."""
    from . import output

    agentic = bool(output._output_config["agentic"])
    hard = output.is_ai_agent()
    soft_only = agentic and not hard
    _, human, agent, automation = err.messages
    program = prog()
    details = dict(err.details)

    next_step: str | None = None
    hint_id = human if not agentic else (automation or agent) if soft_only else agent
    if err.code == "auth_required":
        # Nobody can approve a device code under soft-only signals (CI, cron),
        # and a person has to fix an untrusted file.
        if not soft_only and program and err.messages[0] == "E-AUTH-REQUIRED":
            next_step = f"{program} login"
    elif err.code == "auth_invalid":
        source = _key_source(auth_manager)
        if source:
            details["key_source"] = source
        if not soft_only and program:
            next_step = f"{program} login"
        if hard and source == KEY_SOURCE_ENV:
            if program:
                next_step = f"{program} login --yes"
                hint_id = "A-AUTH-INVALID-ENV"
            else:
                hint_id = "A-AUTH-INVALID-ENV-NULL"
    elif err.code == "invalid_argument" and program and command_path:
        candidate = f"{program} {command_path} --help"
        if shlex.join(shlex.split(candidate)) == candidate:
            next_step = candidate

    values = {
        **err.values,
        "prog": program or _PROG_NAMES[0],
        "command_path": command_path,
    }
    hint = STRINGS[hint_id].format(**values)
    return result_class()(
        error=err.error,
        code=err.code,
        hint=hint,
        next_step=next_step,
        details=details or None,
    )


def _result_class() -> Any:
    from pydantic import model_serializer

    from .models import CodedErrorResult

    class _TransportErrorResult(CodedErrorResult):
        """A 0.5.0 coded error: ``error``, ``status``, ``code``, ``hint``,
        ``next_step``, ``schema_version`` and, when set, ``details``.

        Only transport errors use it: the 0.4.1 coded errors keep their keys.
        """

        next_step: str | None = None
        schema_version: int = 1
        details: dict[str, Any] | None = None

        @model_serializer(mode="wrap")
        def _drop_empty_details(self, handler: Any) -> dict[str, Any]:
            data: dict[str, Any] = handler(self)
            if not data.get("details"):
                data.pop("details", None)
            return data

    _TransportErrorResult.__name__ = "TransportErrorResult"
    _TransportErrorResult.__qualname__ = "TransportErrorResult"
    return _TransportErrorResult


_RESULT_CLASS: Any = None


def result_class() -> Any:
    """``TransportErrorResult``, built on first use so pydantic loads only
    when a transport error is rendered."""
    global _RESULT_CLASS
    if _RESULT_CLASS is None:
        _RESULT_CLASS = _result_class()
    return _RESULT_CLASS
