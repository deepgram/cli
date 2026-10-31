"""One HTTP client per process, and the map from transport failures to codes.

Every REST call the SDK facade makes goes through :func:`shared_client`: one
``httpx.Client`` (one connection pool) per process, created on first use so
``--version`` and ``--help`` never import httpx. The client carries no
credentials and no base URL. The key and the host still come from
``DeepgramClient``, which takes the base URL from
:func:`deepctl_core.hosts.check_credential_host` before any request carries
the key; this module never decides where a credential may go.

:func:`raise_for` turns a failed request into a :class:`TransportError` with a
catalogue code (``transport_mapping`` in ``error_codes.json``, in
order): DNS, connect, TLS, reset or a truncated 2xx body ->
``network_unavailable``; timeout -> ``timeout``; 401 -> ``auth_invalid``;
403 -> ``auth_forbidden``; 404 -> ``not_found``; 429 -> ``rate_limited``;
400, 422 and any other 4xx -> ``invalid_argument``; 5xx -> ``server_error``.
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

if TYPE_CHECKING:
    import httpx

    from .auth import AuthManager

# The SDK's own default for a client it builds itself.
DEFAULT_TIMEOUT_S = 60.0

_lock = threading.Lock()
_client: httpx.Client | None = None
# Where the most recent request went, for the error text: method, host and
# path only (never the query string, which can carry values from argv).
_last_request: dict[str, str] = {}


def _record_request(request: httpx.Request) -> None:
    _last_request.clear()
    _last_request.update(
        method=request.method, host=request.url.host, path=request.url.path
    )


def _install(client: httpx.Client) -> httpx.Client:
    _no_cookies(client)
    hooks = client.event_hooks
    hooks.setdefault("request", [])
    if _record_request not in hooks["request"]:
        hooks["request"] = [*hooks["request"], _record_request]
        client.event_hooks = hooks
    return client


def shared_client() -> httpx.Client:
    """The process's one ``httpx.Client``, created on first use.

    It follows no redirects: a 3xx could take the key, or a request body,
    to another host (see ``DeepgramClient._failed``).
    """
    global _client
    with _lock:
        if _client is None:
            import httpx

            _client = _install(
                httpx.Client(timeout=DEFAULT_TIMEOUT_S, follow_redirects=False)
            )
        return _client


def _no_cookies(client: httpx.Client) -> None:
    """Keep no cookies: one process can serve several profiles and hosts."""
    from http.cookiejar import DefaultCookiePolicy

    client.cookies.jar.set_policy(DefaultCookiePolicy(allowed_domains=[]))


def _set_client(client: httpx.Client | None) -> None:
    """Replace the shared client (tests pass one with an ``httpx.MockTransport``)."""
    global _client
    with _lock:
        _client = _install(client) if client is not None else None
        _last_request.clear()


# ── Catalogue text (error_codes.json "strings") ──────────────────────

STRINGS: dict[str, str] = {
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

# network_unavailable's other messages: the response was cut off, or it
# arrived complete but isn't Deepgram's (a proxy or captive portal page).
NETWORK_CUT: Messages = ("E-NETWORK-CUT", "H-NETWORK-CUT", "A-NETWORK-CUT", None)
NETWORK_UNREADABLE: Messages = (
    "E-NETWORK-UNREADABLE",
    "H-NETWORK-UNREADABLE",
    "A-NETWORK-UNREADABLE",
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
REASON_OTHER = "connection failed"

KEY_SOURCE_ENV = "DEEPGRAM_API_KEY (env)"
KEY_SOURCE_FLAG = "--api-key"
KEY_SOURCE_KEYRING = "system keyring"
KEY_SOURCE_FILE = "config file"


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
            cut = reason == REASON_TRUNCATED
            return TransportError(
                "network_unavailable",
                {"host": host, "reason": reason},
                {"host": host, "reason": reason},
                NETWORK_CUT if cut else None,
            )

    status = getattr(exc, "status_code", None)
    if not isinstance(status, int) or "deepgram" not in sys.modules:
        return None
    from deepgram.core.api_error import ApiError

    if not isinstance(exc, ApiError):
        return None
    headers = exc.headers
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
    if status == 401:
        return TransportError("auth_invalid", {}, {"http_status": 401})
    if status == 403:
        return TransportError("auth_forbidden", {}, {"http_status": 403})
    if status == 404:
        path = _clean(_last_request.get("path") or "this request")
        return TransportError(
            "not_found", {"path": path}, {"http_status": 404, "path": path}
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
                "api_message": _api_message(exc.body, status, secrets),
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


def _seconds(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


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
    if auth_manager is None:
        return None
    if auth_manager.explicit_api_key:
        return KEY_SOURCE_FLAG
    try:
        key = auth_manager.get_api_key()
    except Exception:
        return None
    if not key:
        return None
    # The order `dg whoami` uses: keyring, then the config file (a profile key
    # equal to the env value came from the env), then the env.
    import keyring

    from .auth import KEYRING_SERVICE

    env_key = os.environ.get("DEEPGRAM_API_KEY")
    profile = auth_manager.config.profile or "default"
    try:
        stored = keyring.get_password(KEYRING_SERVICE, f"api-key.{profile}")
    except Exception:
        stored = None
    if stored and stored == key:
        return KEY_SOURCE_KEYRING
    # Config merges the env key into the profile, so a file key equal to the
    # env value came from the env.
    return KEY_SOURCE_ENV if env_key == key else KEY_SOURCE_FILE


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
    if err.code == "auth_invalid":
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
