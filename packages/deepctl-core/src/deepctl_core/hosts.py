"""Which hosts may receive the user's API key.

Every ``base_url`` carries its source (``flag``, ``parent_env``,
``user_config``, ``project_file`` or ``default``). Credentials are attached
only when the effective host is a Deepgram regional API host, a host the
user listed in ``api.allowed_hosts`` in their own config file, or, outside
agent mode, a ``DEEPGRAM_BASE_URL`` from the environment. Anything else is
refused with ``foreign_host`` before a request is made. Plain http is
accepted only for loopback hosts (``insecure_base_url`` otherwise).

The decision lives here, once, and every path that attaches a credential
(the auth pre-flight check, the SDK client environment, WebSocket URLs,
``dg api`` and ``dg mcp``) goes through :func:`check_credential_host`.
"""

from __future__ import annotations

import ipaddress
import os
import shlex
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from rich.markup import escape

from . import messages as msg
from . import output

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .config import Config

DEFAULT_BASE_URL = "https://api.deepgram.com"

# Deepgram API hosts a project file may select and that always receive
# credentials over https: the regional endpoints the CLI documents.
REGIONAL_API_HOSTS = frozenset(
    {
        "api.deepgram.com",
        "api.eu.deepgram.com",
        "api.au.deepgram.com",
        "api.in.deepgram.com",
    }
)

# The developer-tools API that `dg mcp` proxies to by default.
DEFAULT_DX_URL = "https://api.dx.deepgram.com"
DX_API_HOSTS = frozenset({"api.dx.deepgram.com"})

# The identity provider for device login (`dg login` without --api-key).
# Its token exchange mints the API key, so it gets the same host rule.
IDENTITY_ENV = "DEEPGRAM_CLI_BASE_URL"
DEFAULT_IDENTITY_URL = "https://id.dx.deepgram.com"
IDENTITY_HOSTS = frozenset({"id.dx.deepgram.com"})

SOURCE_FLAG = "flag"
SOURCE_ENV = "parent_env"
SOURCE_USER = "user_config"
SOURCE_PROJECT = "project_file"
SOURCE_DEFAULT = "default"

_SECURE_SCHEMES = ("https", "wss")
_KNOWN_SCHEMES = ("http", "https", "ws", "wss")


@dataclass(frozen=True)
class UrlOrigin:
    """The parts of a URL that decide where a credential goes."""

    scheme: str
    host: str
    port: int | None

    @property
    def secure(self) -> bool:
        return self.scheme in _SECURE_SCHEMES

    @property
    def origin(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        port = f":{self.port}" if self.port is not None else ""
        return f"{self.scheme}://{host}{port}"

    @property
    def effective_port(self) -> int:
        if self.port is not None:
            return self.port
        return 443 if self.secure else 80


def parse_origin(url: str) -> UrlOrigin | None:
    """Parse ``url`` into scheme, host and port; ``None`` if it has no host.

    A bare host (no scheme) is treated as https, matching how base URLs
    have always been interpreted.
    """
    text = (url or "").strip()
    if "://" not in text:
        text = f"https://{text}"
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").rstrip(".")
    if scheme not in _KNOWN_SCHEMES or not host:
        return None
    return UrlOrigin(scheme=scheme, host=host, port=port)


def is_loopback(host: str) -> bool:
    """True for ``localhost``, ``127.0.0.0/8`` and ``::1``."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_regional(origin: UrlOrigin, hosts: Iterable[str] = REGIONAL_API_HOSTS) -> bool:
    """True for a listed Deepgram host over https on the default port."""
    return origin.secure and origin.host in hosts and origin.port in (None, 443)


def is_regional_base_url(url: object) -> bool:
    """True if ``url`` is exactly a regional Deepgram API URL.

    Used for project files: no userinfo, path, query, wildcard or other
    port is accepted.
    """
    if not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    if parts.username or parts.password or parts.query or parts.fragment:
        return False
    if parts.path not in ("", "/"):
        return False
    origin = parse_origin(url) if "://" in url else None
    return origin is not None and origin.scheme == "https" and is_regional(origin)


def host_entry(origin: UrlOrigin) -> str:
    """The ``api.allowed_hosts`` entry that approves exactly this host."""
    if origin.port is None:
        return origin.host
    host = f"[{origin.host}]" if ":" in origin.host else origin.host
    return f"{host}:{origin.port}"


def is_allowlisted(origin: UrlOrigin, allowed_hosts: Iterable[object]) -> bool:
    """True if ``origin`` matches an ``api.allowed_hosts`` entry.

    An entry without a port approves the host on any port; an entry with a
    port approves only that port. Matching is exact: no wildcards.
    """
    for entry in allowed_hosts:
        if not isinstance(entry, str) or not entry.strip():
            continue
        text = entry.strip()
        parsed = parse_origin(text if "://" in text else f"https://{text}")
        if parsed is None or parsed.host != origin.host:
            continue
        if parsed.port is None or parsed.port == origin.effective_port:
            return True
    return False


def same_origin(a: UrlOrigin, b: UrlOrigin) -> bool:
    """Scheme family, host and port all match."""
    return (
        a.secure == b.secure
        and a.host == b.host
        and a.effective_port == b.effective_port
    )


class CredentialHostError(Exception):
    """A credential would go to a host that isn't trusted. Nothing was sent."""

    def __init__(self, message_id: str, code: str, error: str, hint: str) -> None:
        super().__init__(error)
        self.message_id = message_id
        self.code = code
        self.error = error
        self.hint = hint


def _source_label(source: str, profile: str, user_config: str, env_label: str) -> str:
    if source == SOURCE_ENV:
        return env_label
    label = msg.SOURCE_LABELS.get(source, msg.SOURCE_LABELS["flag"])
    return label.format(profile=profile, user_config=user_config)


def canonical_url(url: str) -> str:
    """``url`` normalized: lowercase scheme and host, no trailing dot,
    userinfo, query or fragment, and the path kept without a trailing slash
    (a bare host means https).

    The trust decision is made on the origin; callers build request URLs
    from this value, never from the raw one, so the host that is checked is
    the host that is used.
    """
    origin = parse_origin(url)
    if origin is None:
        return url
    text = (url or "").strip()
    if "://" not in text:
        text = f"https://{text}"
    try:
        path = urlsplit(text).path
    except ValueError:
        path = ""
    return origin.origin + path.rstrip("/")


def _agent_hints(agentic: bool) -> bool:
    """Agent-mode hints whenever trust or rendering sees an agent."""
    return agentic or output.is_agentic() or output.is_ai_agent()


def user_config_display() -> str:
    """The default user config path for hints, with ``$HOME`` as ``~``."""
    from .config import default_config_file

    return msg.display_path(default_config_file())


def evaluate(
    url: str,
    source: str,
    allowed_hosts: Iterable[object] = (),
    *,
    profile: str = "default",
    trusted_hosts: Iterable[str] = REGIONAL_API_HOSTS,
    agentic: bool | None = None,
    env_label: str = "DEEPGRAM_BASE_URL",
    identity: bool = False,
) -> UrlOrigin:
    """Decide whether credentials may be sent to ``url``.

    Returns the parsed origin when they may; raises
    :class:`CredentialHostError` otherwise. ``agentic`` defaults to
    :func:`output.is_ai_agent` (hard AI-agent signals only, never CI or TTY
    state). Hints name the default user config, the only file that can
    approve hosts.
    """
    if agentic is None:
        agentic = output.is_ai_agent()
    config_display = user_config_display()
    profile_display = msg.display(profile)
    origin = parse_origin(url)
    host = msg.display(origin.host if origin else url)

    def foreign() -> CredentialHostError:
        if identity:
            message_id, text = "E-FH-IDENTITY", msg.E_FH_IDENTITY
            hint = msg.E_FH_IDENTITY_HINT
        elif source == SOURCE_USER:
            message_id, text = "E-FH-PROFILE", msg.E_FH_PROFILE
            hint = msg.E_FH_PROFILE_HINT
        else:
            message_id, text, hint = "E-FH-FLAG", msg.E_FH_FLAG, msg.E_FH_FLAG_HINT
        if _agent_hints(agentic):
            hint = msg.E_FH_AGENT_HINT
        return CredentialHostError(
            message_id,
            msg.CODE_FOREIGN_HOST,
            text.format(host=host, profile=profile_display),
            hint.format(
                host=host,
                user_config=config_display,
                # The hint is a command to copy: quote the profile name.
                profile=shlex.quote(profile_display),
            ),
        )

    if origin is None:
        raise foreign()

    if not origin.secure and not is_loopback(origin.host):
        label = _source_label(source, profile_display, config_display, env_label)
        hint = msg.E_INSECURE_HINT.format(source_label=label)
        if _agent_hints(agentic):
            hint = msg.E_INSECURE_AGENT_HINT
        raise CredentialHostError(
            "E-INSECURE",
            msg.CODE_INSECURE_BASE_URL,
            msg.E_INSECURE.format(origin=msg.display(origin.origin)),
            hint,
        )

    if is_regional(origin, trusted_hosts):
        return origin
    if is_allowlisted(origin, allowed_hosts):
        return origin
    if source == SOURCE_ENV and not agentic:
        # Unless an AI agent marked the environment, it is trusted like shell
        # config (CI jobs, cron, containers, local fake servers).
        return origin
    raise foreign()


def check_credential_host(config: Config, profile_name: str | None = None) -> str:
    """Return the canonical base URL if credentials may be sent to it.

    Raises :class:`CredentialHostError` (after reporting it once) when the
    host is not trusted.
    """
    name = profile_name or config.profile or "default"
    url = config.get_profile(name).base_url or DEFAULT_BASE_URL
    source = config.base_url_source(name)
    allowed = config.allowed_hosts()
    try:
        origin = evaluate(url, source, allowed, profile=name)
    except CredentialHostError as err:
        report(err)
        raise
    if (
        source == SOURCE_ENV
        and not is_regional(origin)
        and not is_allowlisted(origin, allowed)
    ):
        warn_env_host(origin)
    return canonical_url(url)


def check_identity_host(config: Config) -> str:
    """Return the device-login identity URL if it may be used.

    ``DEEPGRAM_CLI_BASE_URL`` from the environment follows the same rule as
    ``DEEPGRAM_BASE_URL``: refused under a hard AI-agent signal unless the
    host is listed or allowlisted, refused over plain http to a non-loopback
    host, and otherwise used with one warning. It is never saved.
    """
    env_url = os.environ.get(IDENTITY_ENV)
    url = env_url or DEFAULT_IDENTITY_URL
    source = SOURCE_ENV if env_url else SOURCE_DEFAULT
    allowed = config.allowed_hosts()
    try:
        origin = evaluate(
            url,
            source,
            allowed,
            trusted_hosts=IDENTITY_HOSTS,
            env_label=IDENTITY_ENV,
            identity=True,
        )
    except CredentialHostError as err:
        report(err)
        raise
    if (
        source == SOURCE_ENV
        and not is_regional(origin, IDENTITY_HOSTS)
        and not is_allowlisted(origin, allowed)
    ):
        warn_env_host(origin, identity=True)
    return canonical_url(url)


def warn_env_host(
    origin: UrlOrigin,
    variable: str = "DEEPGRAM_BASE_URL",
    *,
    identity: bool = False,
) -> None:
    """Say, once, that the key goes to (or comes from) a host the env chose."""
    host = msg.display(origin.host)
    if not _first_time(("W-ENV-BASEURL", f"{identity}:{variable}:{host}")):
        return
    if identity:
        text = msg.W_ENV_IDENTITY.format(host=host)
    else:
        text = msg.W_ENV_BASEURL.format(host=host, variable=variable)
    if _agent_hints(False):
        hint = msg.W_ENV_BASEURL_AGENT_HINT.format(host=host)
    else:
        hint = msg.W_ENV_BASEURL_HINT.format(
            host=host, user_config=user_config_display()
        )
    output.print_security_warning(escape(text))
    output.print_hint(escape(hint))


_reported: set[tuple[str, str]] = set()
_reported_lock = threading.Lock()


def _first_time(key: tuple[str, str]) -> bool:
    with _reported_lock:
        if key in _reported:
            return False
        _reported.add(key)
        return True


def report(err: CredentialHostError) -> None:
    """Print a refusal and its hint to stderr, once per process."""
    if not _first_time((err.message_id, err.error)):
        return
    output.print_error(escape(err.error))
    output.print_hint(escape(err.hint))
