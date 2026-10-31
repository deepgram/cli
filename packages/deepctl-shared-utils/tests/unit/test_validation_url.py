"""URL source checks: format only by default, an opt-in HEAD request."""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from typing import ClassVar

import httpx
import pytest
from deepctl_shared_utils import validation
from deepctl_shared_utils.validation import (
    display_url,
    output_url,
    scrub_url_text,
    validate_url,
)
from rich.console import Console

TOKEN = "X-Amz-Signature=URLSENTINELTOKEN"


@pytest.fixture
def printed(monkeypatch) -> StringIO:
    buffer = StringIO()
    monkeypatch.setattr(
        validation, "console", Console(file=buffer, width=400, no_color=True)
    )
    return buffer


@pytest.mark.parametrize(
    ("url", "shown"),
    [
        ("https://h.example/a.wav", "https://h.example/a.wav"),
        (f"https://h.example/a.wav?{TOKEN}&b=1", "https://h.example/a.wav"),
        ("https://u:p@h.example:8443/x/a.wav#t", "https://h.example:8443/x/a.wav"),
        (f"http://[::1]:8080/a.wav?{TOKEN}", "http://[::1]:8080/a.wav"),
        (f"not a url?{TOKEN}", "not a url"),
    ],
)
def test_output_url(url, shown):
    assert output_url(url) == shown
    # One path segment or none: status lines show the same thing.
    if shown.count("/") <= 3:
        assert display_url(url) == shown


@pytest.mark.parametrize(
    ("url", "full", "shown"),
    [
        (
            f"https://u:p@cdn.example/s/URLPATHTOKEN/a.wav?{TOKEN}",
            "https://cdn.example/s/URLPATHTOKEN/a.wav",
            "https://cdn.example/…/a.wav",
        ),
        (
            "https://h.example:8443/x/y/",
            "https://h.example:8443/x/y/",
            "https://h.example:8443/…/y/",
        ),
        (
            "https://u:p@h.example:8443/x/a.wav#t",
            "https://h.example:8443/x/a.wav",
            "https://h.example:8443/…/a.wav",
        ),
        ("https://h.example/a/", "https://h.example/a/", "https://h.example/a/"),
        ("https://h.example", "https://h.example", "https://h.example"),
        ("https://h.example/", "https://h.example/", "https://h.example/"),
    ],
)
def test_display_url_keeps_only_the_last_path_segment(url, full, shown):
    assert output_url(url) == full
    assert display_url(url) == shown


def test_format_check_makes_no_request(monkeypatch, printed):
    def no_client(*args, **kwargs):
        raise AssertionError("validate_url opened an HTTP client")

    monkeypatch.setattr(httpx, "Client", no_client)
    assert validate_url(f"https://h.example/a.wav?{TOKEN}") is True
    assert printed.getvalue() == ""


@pytest.mark.parametrize(
    "url",
    [
        f"https:///a.wav?{TOKEN}",
        f"https://user@/a.wav?{TOKEN}",
        f"ftp://h.example/a.wav?{TOKEN}",
        f"h.example/a.wav?{TOKEN}",
    ],
)
def test_bad_format_is_rejected_without_the_query(url, printed):
    assert validate_url(url) is False
    assert "Error:" in printed.getvalue()
    assert "URLSENTINELTOKEN" not in printed.getvalue()


def test_non_audio_warning_drops_the_query(printed):
    assert validate_url(f"https://h.example/page.html?{TOKEN}") is True
    assert "https://h.example/page.html" in printed.getvalue()
    assert "URLSENTINELTOKEN" not in printed.getvalue()


class _Origin(BaseHTTPRequestHandler):
    """Sets a cookie and redirects once, then answers 200 or ``status``."""

    seen: ClassVar[list[dict[str, str | None]]] = []
    status = 200

    def do_HEAD(self):
        _Origin.seen.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "cookie": self.headers.get("Cookie"),
            }
        )
        if self.path.startswith("/start"):
            self.send_response(302)
            self.send_header("Set-Cookie", "session=URLCOOKIE; Path=/")
            self.send_header("Location", "/audio.wav?" + TOKEN)
        else:
            self.send_response(_Origin.status)
            self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_HEAD

    def log_message(self, *args):
        pass


@pytest.fixture
def origin():
    _Origin.seen = []
    _Origin.status = 200
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Origin)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_check_url_sends_head_without_credentials_or_cookies(origin, printed):
    assert validate_url(f"{origin}/start/a.wav?{TOKEN}", check_accessibility=True)
    assert [r["path"].split("?")[0] for r in _Origin.seen] == [
        "/start/a.wav",
        "/audio.wav",
    ]
    assert all(r["authorization"] is None for r in _Origin.seen)
    assert all(r["cookie"] is None for r in _Origin.seen)
    assert "URLSENTINELTOKEN" not in printed.getvalue()


def test_check_url_failure_drops_the_query(origin, printed):
    _Origin.status = 403
    assert not validate_url(f"{origin}/a.wav?{TOKEN}", check_accessibility=True)
    assert "HTTP 403" in printed.getvalue()
    assert f"{origin}/a.wav" in printed.getvalue()
    assert "URLSENTINELTOKEN" not in printed.getvalue()


def test_check_url_connection_error_drops_the_query(printed):
    url = f"http://127.0.0.1:9/a.wav?{TOKEN}"
    assert not validate_url(url, check_accessibility=True)
    assert "--check-url couldn't reach http://127.0.0.1:9/a.wav (" in printed.getvalue()
    assert "URLSENTINELTOKEN" not in printed.getvalue()


def test_httpx_loads_only_for_the_check():
    import subprocess
    import sys

    code = (
        "import sys; from deepctl_shared_utils import validation; "
        "validation.validate_url('https://h.example/a.wav'); "
        "print('httpx' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "False"


# ── The GET fallback never reads the body ────────────────────────────

BIG = 100 * 1024 * 1024


class _IgnoresRange(BaseHTTPRequestHandler):
    """HEAD -> 405; GET ignores Range and sends 100 MB."""

    protocol_version = "HTTP/1.1"
    sent: ClassVar[list[int]] = []

    def do_HEAD(self):
        self.send_response(405)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(BIG))
        self.end_headers()
        chunk = b"\0" * (1 << 16)
        sent = 0
        try:
            while sent < BIG:
                self.wfile.write(chunk)
                sent += len(chunk)
        except OSError:
            pass
        _IgnoresRange.sent.append(sent)

    def log_message(self, *args):
        pass


def test_get_fallback_reads_headers_only(printed):
    _IgnoresRange.sent = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _IgnoresRange)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/big.wav?{TOKEN}"
        assert validate_url(url, check_accessibility=True) is True
        deadline = time.monotonic() + 10
        while not _IgnoresRange.sent and time.monotonic() < deadline:
            time.sleep(0.05)
        server.shutdown()
    finally:
        server.server_close()
    # The client closed the connection after the headers: the server could
    # write only what fits in the socket buffers, far short of 100 MB.
    assert _IgnoresRange.sent and _IgnoresRange.sent[0] < BIG // 4
    assert "URLSENTINELTOKEN" not in printed.getvalue()


# ── User info that urlparse can't see ──

REDACTED = "https://<redacted>/…"


@pytest.mark.parametrize(
    ("url", "valid_format"),
    [
        # A password with an unescaped #, ? or / ends the authority early.
        ("https://u:pa#URLPW@cdn.example/a.wav", False),
        ("https://u:pa?URLPW@cdn.example/a.wav", False),
        ("https://u:pa/URLPW@cdn.example/a/b.wav", False),
        (f"https://u:pa/URLPW@cdn.example/a/b.wav?{TOKEN}", False),
        # An empty authority: a WHATWG parser reads this as user info.
        ("https:///URLPW@cdn.example/y/a.wav", False),
        ("https://user@/a.wav", False),
        # Parses, but an @ sits outside the authority: never shown.
        ("https://u/URLPW@cdn.example/a.wav", True),
        ("https://user?URLPW@cdn.example/a.wav", True),
        ("HTTPS://u/URLPW@cdn.example/x/a.wav", True),
    ],
)
def test_malformed_user_info_is_redacted(url, valid_format, printed):
    assert output_url(url) == REDACTED
    assert display_url(url) == REDACTED
    assert validate_url(url) is valid_format
    shown = printed.getvalue()
    assert "URLPW" not in shown and "u:pa" not in shown
    if not valid_format:
        assert f"Not a valid URL: {REDACTED}" in shown


@pytest.mark.parametrize(
    ("url", "full", "shown"),
    [
        (
            "https://u%40x:URLPW%40y@h.example/a/b.wav",
            "https://h.example/a/b.wav",
            "https://h.example/…/b.wav",
        ),
        (
            f"https://u:URLPW@[::1]:8443/x/a.wav?{TOKEN}",
            "https://[::1]:8443/x/a.wav",
            "https://[::1]:8443/…/a.wav",
        ),
        (
            "https://u:URLPW@h.example:8443/a.wav",
            "https://h.example:8443/a.wav",
            "https://h.example:8443/a.wav",
        ),
        (
            "https://u:URLPW@[2001:db8::1]/s/a.wav",
            "https://[2001:db8::1]/s/a.wav",
            "https://[2001:db8::1]/…/a.wav",
        ),
    ],
)
def test_well_formed_user_info_is_dropped(url, full, shown, printed):
    assert output_url(url) == full
    assert display_url(url) == shown
    assert validate_url(url) is True
    assert "URLPW" not in printed.getvalue()


@pytest.mark.parametrize(
    "url", ["https://h.example:99999/a.wav", "https://[::1/a.wav", "https://h:x/a"]
)
def test_unparseable_url_is_redacted(url, printed):
    assert display_url(url) == REDACTED
    assert validate_url(url) is False
    assert f"Not a valid URL: {REDACTED}" in printed.getvalue()


def test_scrub_url_text_hides_quoted_password_pieces():
    url = f"https://u:pa#URLPW@cdn.example/a.wav?{TOKEN}"
    text = scrub_url_text(f"Invalid port: 'pa' in {url}", url)
    assert "URLPW" not in text and "'pa'" not in text
    assert "URLSENTINELTOKEN" not in text


def test_check_url_errors_never_quote_user_info(monkeypatch, printed):
    url = "https://u/URLPW@cdn.example/a.wav"

    class _Boom:
        def __init__(self, *args, **kwargs):
            raise httpx.ConnectError(f"cannot connect to {url}")

    monkeypatch.setattr(httpx, "Client", _Boom)
    assert validate_url(url, check_accessibility=True) is False
    assert "URLPW" not in printed.getvalue()


def test_url_problem_prints_nothing(printed):
    from deepctl_shared_utils.validation import url_problem

    assert url_problem("https://h.example:99999/a.wav") == (
        f"Not a valid URL: {REDACTED}",
        [],
    )
    assert url_problem("ftp://h.example/a.wav")[0] == (
        "URLs must start with http:// or https://: ftp://h.example/a.wav"
    )
    problem, warnings = url_problem("https://h.example/a.txt")
    assert problem is None
    assert warnings and "doesn't look like an audio file (.txt)" in warnings[0]
    assert printed.getvalue() == ""


def test_audio_file_problem_names_the_way_out(tmp_path, printed):
    from deepctl_shared_utils.validation import audio_file_problem

    doc = tmp_path / "notes.xyz"
    doc.write_text("x")
    problem, _ = audio_file_problem(doc)
    assert problem is not None
    assert problem.startswith(f"{doc} doesn't look like an audio file (.xyz).")
    assert problem.endswith("Pass --no-validate to send it anyway.")
    assert audio_file_problem(tmp_path / "gone.wav")[0] == (
        f"File not found: {tmp_path / 'gone.wav'}"
    )
    assert printed.getvalue() == ""
