"""Real exit codes for Ctrl-C on live ``dg listen``, in a subprocess.

A loopback WebSocket server in this process stands in for the API (fake key,
no network), and a fake ``sounddevice`` on ``PYTHONPATH`` stands in for the
microphone. ``dg listen -`` stops with exit 2 on Ctrl-C; ``dg listen --mic``
ends with exit 0, including a Ctrl-C that lands before the session's own
handler is in place.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")

ENTRY = "import sys; from deepctl.main import main; sys.exit(main())"
FAKE_KEY = "fa1ce0000000000000000000000000000000000d"
# Generous bound for a cold interpreter start on a loaded runner; a normal
# run of each test takes about a second.
STARTUP_S = 20.0

FAKE_SOUNDDEVICE = '''
import threading, time

class RawInputStream:
    """Calls the callback with 50 ms of silence until stopped."""

    def __init__(self, samplerate, channels, dtype, callback, blocksize, **kw):
        self._cb = callback
        self._n = blocksize * channels
        self._stop = threading.Event()

    def start(self):
        def run():
            while not self._stop.is_set():
                self._cb(b"\\x10\\x00" * self._n, self._n, None, None)
                time.sleep(0.05)

        threading.Thread(target=run, daemon=True).start()

    def stop(self):
        self._stop.set()

    def close(self):
        pass
'''


def _final(text: str) -> str:
    return json.dumps(
        {
            "type": "Results",
            "is_final": True,
            "start": 0.0,
            "duration": 1.0,
            "channel": {"alternatives": [{"transcript": text, "words": []}]},
        }
    )


class _FakeServer:
    """Sends one final after the first audio frame, and one more on
    CloseStream, then closes. ``audio`` is set once audio has arrived."""

    def __init__(self) -> None:
        from websockets.sync.server import serve

        self.audio = threading.Event()
        self.closestream = threading.Event()
        self._server = serve(self._handle, "127.0.0.1", 0, process_request=self._http)
        self.port = self._server.socket.getsockname()[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @staticmethod
    def _http(connection, request):
        """Plain HTTP (the auth pre-flight): one fake project, as JSON."""
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return None
        from websockets.datastructures import Headers
        from websockets.http11 import Response

        body = json.dumps(
            {"projects": [{"project_id": "00000000-0000-0000-0000-000000000000"}]}
        ).encode()
        headers = Headers(
            [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(body))),
                ("Connection", "close"),
            ]
        )
        return Response(200, "OK", headers, body)

    def _handle(self, ws) -> None:
        sent = False
        try:
            for message in ws:
                if isinstance(message, bytes):
                    if not sent:
                        ws.send(_final("hello"))
                        sent = True
                        self.audio.set()
                elif "CloseStream" in message:
                    self.closestream.set()
                    ws.send(_final("bye"))
                    ws.close()
                    return
        except Exception:
            pass

    def shutdown(self) -> None:
        self._server.shutdown()


@pytest.fixture
def server():
    srv = _FakeServer()
    yield srv
    srv.shutdown()


@pytest.fixture
def env(tmp_path, server):
    fakes = tmp_path / "fakes"
    fakes.mkdir()
    (fakes / "sounddevice.py").write_text(FAKE_SOUNDDEVICE)
    home = tmp_path / "home"
    home.mkdir()
    e = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("DEEPGRAM_", "DEEPCTL_", "DG_"))
        and k
        not in (
            "AI_AGENT",
            "AGENT",
            "CI",
            "CLAUDECODE",
            "CLAUDE_CODE_ENTRYPOINT",
            "CODEX_SANDBOX",
            "CODEX_SANDBOX_NETWORK_DISABLED",
            "CURSOR_AGENT",
            "GEMINI_CLI",
            "GOOSE_TERMINAL",
            "OR_APP_NAME",
            "OR_SITE_URL",
        )
    }
    e.update(
        HOME=str(home),
        XDG_CONFIG_HOME=str(home / ".config"),
        TERM="xterm",
        DEEPGRAM_API_KEY=FAKE_KEY,
        DEEPGRAM_BASE_URL=f"http://127.0.0.1:{server.port}",
        DEEPCTL_TELEMETRY_DISABLED="1",
        DEEPCTL_TELEMETRY_DSN="http://k@127.0.0.1:9/1",
        CI="true",  # no background update check (it can hold exit for 2 s)
        PYTHONPATH=os.pathsep.join(
            [str(fakes), *filter(None, [os.environ.get("PYTHONPATH")])]
        ),
    )
    return e


def _start(args, env, *, stdin=subprocess.DEVNULL):
    return subprocess.Popen(
        [sys.executable, "-c", ENTRY, "-o", "json", "listen", *args],
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )


def _wait_for_stderr(proc, text: str, timeout: float = STARTUP_S) -> None:
    """Read stderr until ``text`` appears (the banner), byte by byte."""
    assert proc.stderr is not None
    seen = b""
    deadline = time.monotonic() + timeout
    while text.encode() not in seen:
        if time.monotonic() > deadline or proc.poll() is not None:
            raise AssertionError(f"never saw {text!r}: {seen.decode(errors='replace')}")
        b = proc.stderr.read(1)
        if not b:
            continue
        seen += b


def _why(proc) -> str:
    proc.kill()
    _out, err = proc.communicate(timeout=STARTUP_S)
    return f"no audio reached the server; stderr: {err.decode(errors='replace')}"


def _finish(proc):
    # A stop drains for at most 1 s; this only bounds a hang.
    out, err = proc.communicate(timeout=STARTUP_S)
    return proc.returncode, out.decode(), err.decode()


def test_stdin_ctrl_c_exits_2(env, server):
    proc = _start(
        ["-", "--encoding", "linear16", "--sample-rate", "16000"],
        env,
        stdin=subprocess.PIPE,
    )
    assert proc.stdin is not None
    try:
        proc.stdin.write(b"\x10\x00" * 3200)
        proc.stdin.flush()
        assert server.audio.wait(STARTUP_S), _why(proc)
        proc.send_signal(signal.SIGINT)
        rc, out, err = _finish(proc)
    finally:
        proc.kill()
    assert rc == 2, err
    assert server.closestream.is_set()
    payload = json.loads(out)
    assert payload["status"] == "cancelled"
    assert "hello" in payload["transcript"]
    assert "Traceback" not in err


def test_mic_ctrl_c_exits_0(env, server):
    proc = _start(["--mic"], env)
    try:
        assert server.audio.wait(STARTUP_S), _why(proc)
        proc.send_signal(signal.SIGINT)
        rc, out, err = _finish(proc)
    finally:
        proc.kill()
    assert rc == 0, err
    assert server.closestream.is_set()
    payload = json.loads(out)
    assert payload["status"] == "success"
    assert "hello" in payload["transcript"]
    assert "Traceback" not in err


def test_mic_ctrl_c_right_after_start_exits_0(env, server):
    """Ctrl-C as soon as the banner shows, before the session's handler is
    installed (imports and connect still running), is still a mic stop.
    --verbose keeps the status line that -o json drops in this mode."""
    proc = subprocess.Popen(
        [sys.executable, "-c", ENTRY, "--verbose", "-o", "json", "listen", "--mic"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        _wait_for_stderr(proc, "Listening on microphone")
        proc.send_signal(signal.SIGINT)
        rc, out, err = _finish(proc)
    finally:
        proc.kill()
    assert rc == 0, err
    payload = json.loads(out)
    assert payload["status"] == "success"
    assert "Traceback" not in err


def test_fake_sounddevice_is_on_path(env):
    """Guard against the real PortAudio module sneaking in."""
    out = subprocess.run(
        [sys.executable, "-c", "import sounddevice; print(sounddevice.__file__)"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert Path(out.stdout.strip()).parent.name == "fakes"
