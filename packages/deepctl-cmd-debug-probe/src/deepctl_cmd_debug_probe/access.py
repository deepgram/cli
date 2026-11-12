"""Which local clients may use the probe proxy, and how request-derived text
is printed.

Host names this listener, and an Origin, if sent, is a page on this machine.
"""

from __future__ import annotations

import re

from rich.text import Text

# Browsers send Origin on every WebSocket handshake; native clients usually
# send none. Only pages served from this machine may use the proxy.
LOOPBACK_ORIGIN = re.compile(
    r"https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?"
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_C0_C1_DEL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def allowed_hosts(port: int) -> set[str]:
    """Host values that name a listener on 127.0.0.1:`port`."""
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    if port == 80:
        hosts |= {"127.0.0.1", "localhost"}
    return hosts


def refusal_reason(hosts: list[str], origins: list[str], port: int) -> str | None:
    """ "host" or "origin" if the request is refused, None to accept it.

    Host must be 127.0.0.1:PORT or localhost:PORT in any case (a bare host
    on port 80). Origin may be absent (native clients) or a loopback page
    origin; anything else, "null" included, is refused.
    """
    if len(hosts) != 1 or hosts[0].lower() not in allowed_hosts(port):
        return "host"
    if origins and (
        len(origins) != 1 or not LOOPBACK_ORIGIN.fullmatch(origins[0].lower())
    ):
        return "origin"
    return None


def printable(text: str) -> str:
    """`text` from a request as plain ASCII: control characters and anything
    non-ASCII become backslash escapes."""
    text = text.encode("ascii", "backslashreplace").decode("ascii")
    return _CONTROL.sub(lambda m: f"\\x{ord(m.group()):02x}", text)


def untrusted(text: str, style: str = "") -> Text:
    """`text` from a request for rich to print as it is: a Text object, so
    neither markup nor :emoji: codes in it are interpreted."""
    return Text(printable(text), style=style)


def readable(text: str, style: str = "") -> Text:
    """Text from upstream (transcripts) for rich to print as it is: a Text
    object, so markup and :emoji: codes aren't interpreted, with C0 and C1
    control characters and DEL removed and all other Unicode kept."""
    return Text(_C0_C1_DEL.sub("", text), style=style)
