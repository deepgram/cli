"""API base URLs: parsing and the regional Deepgram API hosts.

Every ``base_url`` carries its source (``flag``, ``parent_env``,
``user_config``, ``project_file`` or ``default``). A project
``deepgram.yaml``, or a ``--config`` file outside the user config folder,
may select only a regional Deepgram API URL (:func:`is_regional_base_url`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from collections.abc import Iterable

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
