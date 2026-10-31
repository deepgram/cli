"""Tests for the live streaming engine (deepctl_cmd_listen.live)."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
import types

import pytest
from deepctl_cmd_listen import live


class FakeWS:
    """A stand-in server socket. Replays ``frames`` at once and
    ``after_close`` once CloseStream arrives, then ends the stream if
    ``close_on_closestream`` (like the real API), or when ``finish()`` is
    called."""

    def __init__(
        self, frames=(), *, after_close=(), close_on_closestream=True, frame_gap=0.0
    ):
        self.sent: list = []
        self.frames = list(frames)
        self.after_close = list(after_close)
        self.close_on_closestream = close_on_closestream
        self.frame_gap = frame_gap
        self.got_close = False
        self.done = False
        self.wake = asyncio.Event()
        self.closed = False
        self.transport = None

    def finish(self):
        self.done = True
        self.wake.set()

    async def send(self, data):
        self.sent.append(data)
        if data == live.CLOSE_STREAM:
            self.got_close = True
            self.wake.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        while True:
            pending = self.frames or (self.after_close if self.got_close else [])
            if pending:
                if self.frame_gap:
                    await asyncio.sleep(self.frame_gap)
                return pending.pop(0)
            if self.done or (self.got_close and self.close_on_closestream):
                raise StopAsyncIteration
            self.wake.clear()
            await self.wake.wait()

    async def close(self):
        self.closed = True
        self.finish()


def install_fake_websockets(monkeypatch, ws):
    """Make ``websockets.connect(...)`` resolve to ``ws`` (awaitable form)."""

    calls = []

    class _Connect:
        def __await__(self):
            async def _open():
                return ws

            return _open().__await__()

    def connect(*args, **kwargs):
        calls.append((args, kwargs))
        return _Connect()

    mod = types.ModuleType("websockets")
    mod.connect = connect
    monkeypatch.setitem(sys.modules, "websockets", mod)
    monkeypatch.setattr(live, "_open", connect)
    return calls


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def session(ws_messages, *, audio=(), eof=True, stop_after=None, **kw):
    """Run run_session with an in-memory source; returns (outcome, messages)."""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    closing = threading.Event()
    got: list = []

    def start():
        async def feed():
            for chunk in audio:
                await q.put(chunk)
            if eof:
                await q.put(b"")

        tasks.append(loop.create_task(feed()))

    tasks: list = []
    stop = asyncio.Event()
    if stop_after is not None:
        loop.call_later(stop_after, stop.set)
    outcome = await live.run_session(
        "wss://api.deepgram.com/v1/listen",
        {"Authorization": "Token k"},
        on_message=got.append,
        queue=q,
        closing=closing,
        start_source=start,
        byte_rate=kw.pop("byte_rate", 32000),
        stop=stop,
    )
    assert closing.is_set(), "shutdown must always set closing"
    return outcome, got


def test_bytes_per_second_unknown_is_zero():
    assert live.bytes_per_second("opus", 48000, 1) == 0
    assert live.bytes_per_second("linear16", 0, 1) == 0


# ── Stdin reader ───────────────────────────────────────────────────────────


def test_reader_reads_capped_reads_then_eof():
    r, w = os.pipe()
    os.write(w, b"a" * 5000)
    os.close(w)

    async def main():
        q: asyncio.Queue = asyncio.Queue(maxsize=32)
        live.StdinReader(
            asyncio.get_running_loop(), q, threading.Event(), read_size=1600, fd=r
        ).start()
        items = []
        while True:
            item = await asyncio.wait_for(q.get(), 5)
            items.append(item)
            if not item:
                return items

    items = run(main())
    os.close(r)
    assert all(len(i) <= 1600 for i in items)
    assert b"".join(items) == b"a" * 5000
    assert items[-1] == b""


def test_reader_stops_without_put_when_closing():
    r, w = os.pipe()
    os.write(w, b"x" * 10)

    async def main():
        q: asyncio.Queue = asyncio.Queue(maxsize=4)
        closing = threading.Event()
        closing.set()
        live.StdinReader(
            asyncio.get_running_loop(), q, closing, read_size=100, fd=r
        ).start()
        await asyncio.sleep(0.1)
        return q.qsize()

    assert run(main()) == 0
    os.close(w)
    os.close(r)


def test_reader_put_after_loop_closed_is_quiet(recwarn):
    loop = asyncio.new_event_loop()
    q: asyncio.Queue = asyncio.Queue(maxsize=4)
    reader = live.StdinReader(loop, q, threading.Event(), read_size=10, fd=0)
    loop.close()
    assert reader._put(b"x") is False
    assert not [w for w in recwarn if "never awaited" in str(w.message)]


def test_reader_reports_read_errors():
    r, w = os.pipe()
    os.close(w)
    os.close(r)  # reading a closed descriptor: EBADF

    async def main():
        q: asyncio.Queue = asyncio.Queue(maxsize=4)
        live.StdinReader(
            asyncio.get_running_loop(), q, threading.Event(), read_size=10, fd=r
        ).start()
        return await asyncio.wait_for(q.get(), 5)

    assert isinstance(run(main()), OSError)


# Regression: a reader blocked
# handing audio to a slow consumer while Ctrl-C shuts the loop down used to
# abort the interpreter (exit 134, "Fatal Python error") on 3.11/3.12.
_SHUTDOWN_SCRIPT = textwrap.dedent(
    """
    import asyncio, sys, threading
    from deepctl_cmd_listen import live

    async def main():
        loop = asyncio.get_running_loop()
        q = asyncio.Queue(maxsize=32)
        closing = threading.Event()
        stop = asyncio.Event()
        guard = live._SignalGuard(loop, stop)
        guard.install()
        live.StdinReader(loop, q, closing, read_size=3200).start()

        async def consume():
            while True:
                b = await q.get()
                if not b:
                    stop.set()
                    return
                await asyncio.sleep(0.01)  # slow: the reader waits on the put

        t = asyncio.create_task(consume())
        print("ready", flush=True)
        await stop.wait()
        closing.set()  # before cancelling, as run_session does
        t.cancel()
        guard.restore()

    asyncio.run(main())
    print("exited main", file=sys.stderr)
    """
)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
@pytest.mark.parametrize("attempt", range(5))
def test_sigint_with_reader_blocked_on_put_exits_cleanly(attempt):
    feeder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys\nwhile True: sys.stdout.buffer.write(b'x'*65536)",
        ],
        stdout=subprocess.PIPE,
    )
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", _SHUTDOWN_SCRIPT],
            stdin=feeder.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert proc.stdout.readline().strip() == b"ready"
        time.sleep(0.3)
        proc.send_signal(signal.SIGINT)
        _, err = proc.communicate(timeout=10)
    finally:
        feeder.kill()
        feeder.wait()
    assert proc.returncode == 0, err.decode()
    assert b"Fatal Python error" not in err
    assert b"Traceback" not in err
    assert b"exited main" in err


# ── Session: ends and ordering ─────────────────────────────────────────────


def _result(text, start=0.0, duration=1.0):
    return json.dumps(
        {
            "type": "Results",
            "is_final": True,
            "start": start,
            "duration": duration,
            "channel": {"alternatives": [{"transcript": text}]},
        }
    )


def test_eof_sends_closestream_and_keeps_late_finals(monkeypatch):
    # Finals arrive only after CloseStream, like a slow finalize.
    ws = FakeWS(after_close=[_result("one"), _result("two")], frame_gap=0.2)
    install_fake_websockets(monkeypatch, ws)
    outcome, got = run(session([], audio=[b"a" * 100, b"b" * 100]))
    assert outcome.status == "success"
    assert got == [_result("one"), _result("two")]
    assert ws.sent[:2] == [b"a" * 100, b"b" * 100]
    assert ws.sent[-1] == live.CLOSE_STREAM


def test_server_close_ends_session_cleanly(monkeypatch):
    ws = FakeWS([_result("hi")])
    install_fake_websockets(monkeypatch, ws)

    async def main():
        asyncio.get_running_loop().call_later(0.05, ws.finish)
        return await session([], eof=False)

    outcome, got = run(main())
    assert outcome.status == "success"
    assert got == [_result("hi")]


def test_server_error_frame_is_an_error(monkeypatch):
    import click

    ws = FakeWS([json.dumps({"type": "Error"})])
    install_fake_websockets(monkeypatch, ws)

    async def main():
        q: asyncio.Queue = asyncio.Queue(maxsize=4)

        def on_message(raw):
            raise click.ClickException("Flux STT error (X): bad")

        return await live.run_session(
            "wss://x/v2/listen",
            {},
            on_message=on_message,
            queue=q,
            closing=threading.Event(),
            start_source=lambda: None,
            byte_rate=0,
        )

    outcome = run(main())
    assert outcome.status == "error"
    assert "Flux STT error (X): bad" in outcome.error


def test_stop_sends_closestream_and_waits_for_server(monkeypatch):
    # A 5 s drain cap, so a stop that waited it out is far from the bound.
    monkeypatch.setattr(live, "STOP_DRAIN_S", 5.0)
    ws = FakeWS([_result("kept")], frame_gap=0.0)
    install_fake_websockets(monkeypatch, ws)
    t0 = time.monotonic()
    outcome, got = run(session([], audio=[b"a" * 10], eof=False, stop_after=0.1))
    elapsed = time.monotonic() - t0
    assert outcome.status == "cancelled"
    assert not outcome.incomplete
    assert live.CLOSE_STREAM in ws.sent
    assert got == [_result("kept")]
    assert elapsed < 3.0


def test_stop_gives_up_after_one_second(monkeypatch):
    ws = FakeWS(close_on_closestream=False)  # never finishes
    install_fake_websockets(monkeypatch, ws)
    t0 = time.monotonic()
    outcome, _ = run(session([], eof=False, stop_after=0.1))
    elapsed = time.monotonic() - t0
    assert outcome.status == "cancelled"
    assert outcome.incomplete
    assert live.CLOSE_STREAM in ws.sent
    assert 1.0 <= elapsed < 1.9


def test_stop_during_eof_drain_waits_the_stop_cap_once(monkeypatch):
    # A 3 s stop cap: one wait ends near 3.3 s and two near 6.3 s, so the
    # bound is 1.5 s from each and a slow host can't blur them.
    monkeypatch.setattr(live, "STOP_DRAIN_S", 3.0)
    ws = FakeWS(close_on_closestream=False)
    install_fake_websockets(monkeypatch, ws)
    t0 = time.monotonic()
    outcome, _ = run(session([], audio=[b"a"], eof=True, stop_after=0.3))
    elapsed = time.monotonic() - t0
    assert outcome.status == "cancelled"
    assert outcome.incomplete
    assert ws.sent.count(live.CLOSE_STREAM) == 1  # not sent twice
    assert 3.2 <= elapsed < 4.8  # the stop cap waited once, not twice


def test_eof_idle_deadline(monkeypatch):
    monkeypatch.setattr(live, "EOF_IDLE_S", 0.3)
    ws = FakeWS(close_on_closestream=False)
    install_fake_websockets(monkeypatch, ws)
    t0 = time.monotonic()
    outcome, _ = run(session([], audio=[b"a"]))
    elapsed = time.monotonic() - t0
    assert outcome.status == "drain_timeout"
    assert outcome.drain_reason == "idle"
    assert 0.3 <= elapsed < 1.5


def test_eof_idle_deadline_refreshed_by_server_messages(monkeypatch):
    monkeypatch.setattr(live, "EOF_IDLE_S", 0.3)
    ws = FakeWS(
        after_close=[_result(str(i)) for i in range(6)],
        close_on_closestream=False,
        frame_gap=0.15,
    )
    install_fake_websockets(monkeypatch, ws)
    t0 = time.monotonic()
    outcome, got = run(session([], audio=[b"a"]))
    elapsed = time.monotonic() - t0
    assert len(got) == 6
    assert outcome.status == "drain_timeout"
    # 6 x 0.15 s of messages, then 0.3 s idle.
    assert 1.1 <= elapsed < 2.6


def test_eof_absolute_cap(monkeypatch):
    monkeypatch.setattr(live, "EOF_IDLE_S", 5.0)
    monkeypatch.setattr(live, "EOF_CAP_MIN_S", 0.4)
    ws = FakeWS(
        after_close=[_result(str(i)) for i in range(100)],
        close_on_closestream=False,
        frame_gap=0.05,
    )
    install_fake_websockets(monkeypatch, ws)
    t0 = time.monotonic()
    outcome, _ = run(session([], audio=[b"a"]))
    elapsed = time.monotonic() - t0
    assert outcome.status == "drain_timeout"
    assert outcome.drain_reason == "cap"
    assert 0.4 <= elapsed < 2.0


def test_eof_cap_uses_audio_behind_for_pcm(monkeypatch):
    # 128 000 B at 320 000 B/s = 0.4 s sent; finals cover 0.1 s; 0.3 s behind
    # -> cap = 2 x 0.3 = 0.6 s (with the 30 s floor lowered for the test).
    monkeypatch.setattr(live, "EOF_CAP_MIN_S", 0.0)
    monkeypatch.setattr(live, "EOF_IDLE_S", 5.0)
    ws = FakeWS(after_close=[_result("x", 0.0, 0.1)], close_on_closestream=False)
    install_fake_websockets(monkeypatch, ws)
    t0 = time.monotonic()
    outcome, _ = run(session([], audio=[b"a" * 32000] * 4, byte_rate=320000))
    elapsed = time.monotonic() - t0
    assert outcome.status == "drain_timeout"
    assert outcome.drain_reason == "cap"
    assert outcome.drain_limit_s == pytest.approx(0.6)
    assert 0.6 <= elapsed < 2.0


def test_eof_cap_uses_send_time_without_byte_rate(monkeypatch):
    monkeypatch.setattr(live, "EOF_CAP_MIN_S", 0.0)
    monkeypatch.setattr(live, "EOF_IDLE_S", 5.0)
    ws = FakeWS(close_on_closestream=False)
    install_fake_websockets(monkeypatch, ws)

    async def main():
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=4)

        def start():
            async def feed():
                for _ in range(3):
                    await q.put(b"o" * 10)
                    await asyncio.sleep(0.1)
                await q.put(b"")

            tasks.append(loop.create_task(feed()))

        tasks: list = []
        return await live.run_session(
            "wss://x",
            {},
            on_message=lambda m: None,
            queue=q,
            closing=threading.Event(),
            start_source=start,
            byte_rate=0,
        )

    outcome = run(main())
    assert outcome.drain_reason == "cap"
    # about 0.2 s between the first and last send -> cap about 0.4 s
    assert 0.4 <= outcome.drain_limit_s <= 2.0


def test_shutdown_sets_closing_before_cancelling(monkeypatch):
    """The producer must see ``closing`` before any task is cancelled: the
    sender, cancelled while it waits on the queue, records what it saw."""
    ws = FakeWS(close_on_closestream=False)
    install_fake_websockets(monkeypatch, ws)
    closing = threading.Event()
    seen: list = []

    class SpyQueue(asyncio.Queue):
        async def get(self):
            try:
                return await super().get()
            except asyncio.CancelledError:
                seen.append(closing.is_set())
                raise

    async def main():
        stop = asyncio.Event()
        asyncio.get_running_loop().call_later(0.05, stop.set)
        return await live.run_session(
            "wss://x",
            {},
            on_message=lambda m: None,
            queue=SpyQueue(maxsize=4),
            closing=closing,
            start_source=lambda: None,
            byte_rate=0,
            stop=stop,
        )

    run(main())
    assert seen == [True]


def test_signal_guard_first_stops_second_exits(monkeypatch):
    exits = []
    monkeypatch.setattr(live.os, "_exit", lambda code: exits.append(code))

    async def main():
        stop = asyncio.Event()
        guard = live._SignalGuard(asyncio.get_running_loop(), stop)
        guard._handler(signal.SIGINT, None)
        await asyncio.sleep(0)
        first = stop.is_set()
        guard._handler(signal.SIGINT, None)
        return first

    assert run(main()) is True
    assert exits == [2]


def test_signal_guard_restores_previous_handler():
    before = signal.getsignal(signal.SIGINT)

    async def main():
        guard = live._SignalGuard(asyncio.get_running_loop(), asyncio.Event())
        guard.install()
        installed = signal.getsignal(signal.SIGINT)
        guard.restore()
        return installed

    installed = run(main())
    assert installed != before
    assert signal.getsignal(signal.SIGINT) == before


def test_closed_outcomes():
    from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
    from websockets.frames import Close

    ok = ConnectionClosedOK(Close(1000, ""), Close(1000, ""), True)
    assert live._closed_outcome(ok, ()) is None
    bad = ConnectionClosedError(Close(1011, "fake\x1b[31m error"), None)
    out = live._closed_outcome(bad, ())
    assert out.kind == "server" and out.close_code == 1011
    assert "1011" in out.error and "\x1b" not in out.error
    lost = live._closed_outcome(ConnectionClosedError(None, None), ())
    assert lost.kind == "network"
    assert lost.error == "The connection to the server was lost."


# ── Secrets never echoed ────────────────────────────

SENTINEL = "fake_SENTINEL_0123456789abcdef"


@pytest.mark.parametrize(
    "text",
    [
        f"rejected Token {SENTINEL}",
        f"rejected bearer {SENTINEL}",
        f"key {SENTINEL} is bad",
        f"{'x' * 190}Token {SENTINEL}",  # the cut must not leave a prefix
    ],
)
def test_clean_scrubs_credentials_before_truncating(text):
    out = live._clean(text, 200, (SENTINEL,))
    assert SENTINEL not in out
    assert SENTINEL[:8] not in out
    assert len(out) <= 200


def test_server_close_reason_echoing_the_key_is_scrubbed(monkeypatch):
    from websockets.exceptions import ConnectionClosedError
    from websockets.frames import Close

    class Echo(FakeWS):
        async def __anext__(self):
            raise ConnectionClosedError(
                Close(1011, f"rejected Token {SENTINEL} and {SENTINEL}"), None
            )

    install_fake_websockets(monkeypatch, Echo())

    async def main():
        return await live.run_session(
            "wss://x",
            {"Authorization": f"Token {SENTINEL}"},
            on_message=lambda m: None,
            queue=asyncio.Queue(),
            closing=threading.Event(),
            start_source=lambda: None,
            byte_rate=0,
        )

    outcome = run(main())
    assert outcome.kind == "server"
    assert SENTINEL not in outcome.error


def test_flux_error_description_echoing_the_key_is_scrubbed(monkeypatch):
    import click

    install_fake_websockets(monkeypatch, FakeWS([json.dumps({"type": "Error"})]))

    def on_message(raw):
        raise click.ClickException(f"Flux STT error (AUTH): Token {SENTINEL}")

    async def main():
        return await live.run_session(
            "wss://x",
            {"Authorization": f"Token {SENTINEL}"},
            on_message=on_message,
            queue=asyncio.Queue(),
            closing=threading.Event(),
            start_source=lambda: None,
            byte_rate=0,
        )

    outcome = run(main())
    assert outcome.kind == "server"
    assert SENTINEL not in outcome.error
    assert "Flux STT error (AUTH)" in outcome.error


# ── stdout closed by its reader ──────────────────────────────────────


def test_broken_pipe_on_stdout_ends_quietly(monkeypatch):
    silenced = []
    monkeypatch.setattr(live, "silence_stdout", lambda: silenced.append(True))
    install_fake_websockets(monkeypatch, FakeWS([_result("a")]))

    def on_message(raw):
        raise BrokenPipeError(32, "Broken pipe")

    async def main():
        return await live.run_session(
            "wss://x",
            {},
            on_message=on_message,
            queue=asyncio.Queue(),
            closing=threading.Event(),
            start_source=lambda: None,
            byte_rate=0,
        )

    outcome = run(main())
    assert outcome.status == "success"
    assert outcome.stdout_closed
    assert silenced == [True]


# ── SIGTERM and non-blocking stdin ─────────────────────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_signal_guard_sigterm_drains_then_second_signal_dies_by_sigterm(monkeypatch):
    died = []
    monkeypatch.setattr(live, "die_by_signal", lambda s: died.append(s))
    monkeypatch.setattr(live.os, "_exit", lambda code: died.append(("exit", code)))

    async def main():
        stop = asyncio.Event()
        guard = live._SignalGuard(asyncio.get_running_loop(), stop)
        guard._handler(signal.SIGTERM, None)
        await asyncio.sleep(0)
        first = (stop.is_set(), guard.signum)
        guard._handler(signal.SIGINT, None)
        return first

    assert run(main()) == (True, signal.SIGTERM)
    assert died[0] == signal.SIGTERM


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_signal_guard_installs_sigterm_only_during_a_session():
    assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL

    async def main():
        guard = live._SignalGuard(asyncio.get_running_loop(), asyncio.Event())
        guard.install()
        during = signal.getsignal(signal.SIGTERM)
        guard.restore()
        return during

    assert run(main()) != signal.SIG_DFL
    assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL


def test_session_outcome_records_sigterm(monkeypatch):
    ws = FakeWS([_result("kept")])
    install_fake_websockets(monkeypatch, ws)

    async def main():
        loop = asyncio.get_running_loop()
        loop.call_later(0.1, signal.raise_signal, signal.SIGTERM)
        return await live.run_session(
            "wss://x",
            {},
            on_message=lambda m: None,
            queue=asyncio.Queue(),
            closing=threading.Event(),
            start_source=lambda: None,
            byte_rate=0,
        )

    if sys.platform == "win32":
        pytest.skip("POSIX signals")
    outcome = run(main())
    assert outcome.status == "cancelled"
    assert outcome.signal == signal.SIGTERM
    assert live.CLOSE_STREAM in ws.sent
    assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_signal_while_server_closes_still_cancels(monkeypatch):
    """A SIGTERM that lands while the server is closing isn't dropped: the
    clean close becomes a SIGTERM stop, so the command still re-raises it."""

    class _SignalOnClose(FakeWS):
        async def close(self):
            signal.raise_signal(signal.SIGTERM)
            await super().close()

    ws = _SignalOnClose([_result("kept")])
    install_fake_websockets(monkeypatch, ws)

    async def main():
        asyncio.get_running_loop().call_later(0.05, ws.finish)
        return await session([], eof=False)

    outcome, got = run(main())
    assert got == [_result("kept")]
    assert outcome.status == "cancelled"
    assert outcome.signal == signal.SIGTERM
    assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pipes")
def test_reader_waits_on_a_nonblocking_descriptor():
    """A parent may leave O_NONBLOCK on the shared stdin; the reader waits
    for data instead of failing with EAGAIN, and leaves the flag alone."""
    r, w = os.pipe()
    os.set_blocking(r, False)

    async def main():
        q: asyncio.Queue = asyncio.Queue()
        live.StdinReader(
            asyncio.get_running_loop(), q, threading.Event(), read_size=100, fd=r
        ).start()
        await asyncio.sleep(0.3)  # nothing to read yet: EAGAIN
        os.write(w, b"late")
        os.close(w)
        items = []
        while True:
            item = await asyncio.wait_for(q.get(), 5)
            items.append(item)
            if not isinstance(item, bytes) or not item:
                return items

    items = run(main())
    assert items == [b"late", b""]
    assert os.get_blocking(r) is False
    os.close(r)
