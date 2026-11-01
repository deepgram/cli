"""DEEPGRAM_CLI_INSECURE turns off TLS verification only when set to 1 or
true, only for localhost, 127.0.0.1 and [::1], never in an AI agent session,
and without dropping HTTPS_PROXY/NO_PROXY for other hosts."""

from __future__ import annotations

import socket
import threading
from unittest.mock import patch

import httpx
import pytest
from deepctl_core import auth as auth_module
from deepctl_core import hosts, output
from deepctl_core.auth import AuthManager


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(hosts, "_reported", set())
    monkeypatch.setenv("DEEPGRAM_CLI_INSECURE", "1")
    monkeypatch.setattr(output.stderr_console, "_width", 400)
    monkeypatch.setattr(output, "is_ai_agent", lambda: False)


def client_kwargs(config) -> dict:
    with patch.object(auth_module.httpx, "Client") as client:
        AuthManager(config)
    return dict(client.call_args.kwargs)


def skips_verification(config) -> bool:
    return "mounts" in client_kwargs(config)


def test_ignored_with_one_warning_for_agents(monkeypatch, capsys):
    monkeypatch.setattr(output, "is_ai_agent", lambda: True)
    assert skips_verification(object()) is False
    assert skips_verification(object()) is False
    assert capsys.readouterr().err.count("Ignoring DEEPGRAM_CLI_INSECURE") == 1


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " True "])
def test_one_or_true_mounts_unverified_loopback_only(monkeypatch, capsys, value):
    monkeypatch.setenv("DEEPGRAM_CLI_INSECURE", value)
    kwargs = client_kwargs(object())
    assert kwargs["verify"] is True  # the default client still verifies
    assert "transport" not in kwargs  # so httpx keeps the env proxy mounts
    assert sorted(kwargs["mounts"]) == [
        "all://127.0.0.1",
        "all://[::1]",
        "all://localhost",
    ]
    assert "DEEPGRAM_CLI_INSECURE" not in capsys.readouterr().err


@pytest.mark.parametrize("value", ["0", "false", "no", "yes", "on", "", "2"])
def test_other_values_keep_verification_on(monkeypatch, value):
    # DEEPGRAM_CLI_INSECURE=0 (or any value but 1/true) keeps TLS verification on.
    monkeypatch.setenv("DEEPGRAM_CLI_INSECURE", value)
    kwargs = client_kwargs(object())
    assert kwargs.get("verify") is True
    assert "mounts" not in kwargs and "transport" not in kwargs


def test_unset_keeps_verification_on(monkeypatch):
    monkeypatch.delenv("DEEPGRAM_CLI_INSECURE")
    assert skips_verification(object()) is False


# -- real sockets: a recording "proxy" and a recording "server" on loopback --


class _Recorder:
    """Accepts TCP connections on 127.0.0.1 and records the first line.

    Stops (and joins its thread) on close(), so no thread outlives the test.
    """

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.sock.settimeout(0.05)
        self.port = self.sock.getsockname()[1]
        self.lines: list[str] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                return
            with conn:
                conn.settimeout(2)
                try:
                    data = conn.recv(4096)
                except OSError:
                    data = b""
                first = data.split(b"\r\n", 1)[0]
                self.lines.append(
                    first.decode("latin-1") if first else "<tls or empty>"
                )
                try:
                    conn.sendall(
                        b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n"
                    )
                except OSError:
                    pass

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self.sock.close()


@pytest.fixture
def proxy(monkeypatch):
    rec = _Recorder()
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{rec.port}")
    yield rec
    rec.close()


def _get(url: str) -> None:
    manager = AuthManager(object())
    try:
        manager.client.get(url, timeout=3)
    except httpx.HTTPError:
        pass
    finally:
        manager.client.close()


@pytest.mark.parametrize("value", ["1", "0"])
def test_remote_hosts_still_go_through_https_proxy(proxy, monkeypatch, value):
    monkeypatch.setenv("DEEPGRAM_CLI_INSECURE", value)
    _get("https://api.deepgram.example/v1/projects")
    assert proxy.lines == ["CONNECT api.deepgram.example:443 HTTP/1.1"]


def test_remote_host_warns_once(proxy, capsys):
    _get("https://api.deepgram.example/v1/projects")
    _get("https://api.deepgram.example/v1/projects")
    err = capsys.readouterr().err
    assert err.count("DEEPGRAM_CLI_INSECURE applies only to localhost") == 1
    assert "run it on localhost" in err


def test_loopback_goes_direct_not_through_the_proxy(proxy, capsys):
    server = _Recorder()
    try:
        _get(f"https://127.0.0.1:{server.port}/v1/projects")
        _get(f"https://localhost:{server.port}/v1/projects")
    finally:
        server.close()
    assert proxy.lines == []
    assert len(server.lines) == 2  # TLS ClientHello, straight to the server
    assert "applies only to localhost" not in capsys.readouterr().err


def test_other_loopback_addresses_keep_verification(capsys):
    from deepctl_core.auth import _warn_if_verified

    _warn_if_verified(httpx.Request("GET", "https://127.0.0.2/"))
    assert "applies only to localhost" in capsys.readouterr().err
