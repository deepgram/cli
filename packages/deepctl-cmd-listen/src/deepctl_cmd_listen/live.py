"""Live streaming engine for ``dg listen`` (stdin and microphone).

One WebSocket session: audio goes up from a queue, transcripts come
down to a callback, and the session ends one of four ways:

- the server closes the stream (exit 0, or 1 if it reported an error);
- stdin reaches end of input: ``CloseStream``, then wait for the server to
  close under an idle deadline and an absolute cap (``stream_drain_timeout``
  if either expires);
- Ctrl-C (or Ctrl-Break on Windows): ``CloseStream``, then wait at most
  ``STOP_DRAIN_S`` for the server's last results (exit 2). On ``--mic``,
  Ctrl-C is how the session ends, so it exits 0; Ctrl-Break stays a hard
  stop (exit 2). SIGTERM on POSIX drains the same way, and the command
  then re-raises it. A second signal ends the process at once;
- the connection fails (exit 1).

Stdin is read with ``os.read(0, cap)`` in a daemon thread that hands each
buffer to the event loop and waits until the queue accepts it. The thread is
never joined. Before every read and every hand-off it checks ``closing``,
which shutdown sets before it cancels any task; anything raised in the thread
(including a cancelled hand-off while the loop shuts down) ends the thread
quietly. ``loop.connect_read_pipe`` isn't used: it rejects a regular file on
stdin, misbehaves on ``/dev/null``, sets ``O_NONBLOCK`` on a descriptor shared
with the parent, and doesn't work with the Windows Proactor loop.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import click

from . import messages as msg

if TYPE_CHECKING:
    from collections.abc import Callable

# ── Tunables ────────────────────────────────────────────────────────────────

#: Largest single read from stdin.
DEFAULT_READ_BYTES = 4096
#: Ctrl-C: how long to wait for the server's last results after CloseStream.
STOP_DRAIN_S = 1.0
#: End of input: give up when the server has sent nothing for this long.
EOF_IDLE_S = 10.0
#: End of input: the absolute cap is max(EOF_CAP_MIN_S, 2 x the work left).
EOF_CAP_MIN_S = 30.0
#: Bound on the WebSocket closing handshake once the outcome is known.
CLOSE_TIMEOUT_S = 1.0

#: Bytes per sample for encodings whose byte rate is known from the rate.
_PCM_WIDTH = {"linear16": 2, "linear32": 4, "mulaw": 1, "alaw": 1}

CLOSE_STREAM = json.dumps({"type": "CloseStream"})


def bytes_per_second(encoding: str | None, sample_rate: int, channels: int) -> int:
    """Byte rate of raw PCM, mulaw or alaw audio; 0 when it isn't knowable."""
    width = _PCM_WIDTH.get((encoding or "").lower())
    if not width or sample_rate <= 0 or channels <= 0:
        return 0
    return width * sample_rate * channels


# ── Audio sources ───────────────────────────────────────────────────────────


class StdinReader:
    """Reads a file descriptor in a daemon thread into a queue.

    Puts ``bytes`` for audio, ``b""`` once at end of input, or the
    ``OSError`` a read raised. Never joined: a read blocked on an idle pipe
    can't be interrupted portably, and the process may exit while it waits.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue[Any],
        closing: threading.Event,
        *,
        read_size: int,
        fd: int = 0,
    ) -> None:
        self._loop = loop
        self._queue = queue
        self._closing = closing
        self._read_size = read_size
        self._fd = fd

    def start(self) -> None:
        threading.Thread(
            target=self._run, name="deepctl-stdin-reader", daemon=True
        ).start()

    def _put(self, item: Any) -> bool:
        """Hand ``item`` to the loop and wait for the queue to take it."""
        if self._closing.is_set() or self._loop.is_closed():
            return False
        coro = self._queue.put(item)
        try:
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        except BaseException:
            # The loop closed between the check and the call.
            coro.close()
            return False
        future.result()
        return True

    def _run(self) -> None:
        try:
            # Keep Ctrl-C on the main thread, where the handler runs; a
            # signal routed here would only interrupt the read.
            if hasattr(signal, "pthread_sigmask"):
                signal.pthread_sigmask(
                    signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM}
                )
            while not self._closing.is_set():
                try:
                    data = os.read(self._fd, self._read_size)
                except BlockingIOError:
                    # The parent left O_NONBLOCK on the shared descriptor.
                    # Wait for data rather than change its flags.
                    _wait_readable(self._fd)
                    continue
                except OSError as exc:
                    self._put(exc)
                    return
                if self._closing.is_set():
                    return
                if not self._put(data) or not data:
                    return
        except BaseException:
            # Shutdown cancelled the hand-off, or the loop is gone. Either
            # way the session no longer wants audio.
            return


def _wait_readable(fd: int) -> None:
    """Block (in short slices) until ``fd`` has data or is at end of input."""
    import select

    try:
        select.select([fd], [], [], 0.2)
    except (OSError, ValueError):
        time.sleep(0.2)


# ── Session ─────────────────────────────────────────────────────────────────


@dataclass
class LiveOutcome:
    """How a live session ended."""

    #: "success" (server closed), "cancelled" (Ctrl-C or SIGTERM), "error",
    #: or "drain_timeout" (end of input, the server never finished).
    status: str = "success"
    #: Error text for "error" (already scrubbed and safe to print).
    error: str | None = None
    #: For "error": "server" (error close or error frame), "network" (the
    #: connection dropped) or "read" (stdin failed).
    kind: str | None = None
    #: The server's close code, for a "server" error that closed the socket.
    close_code: int | None = None
    #: The request id the server reported, when it sent one.
    request_id: str | None = None
    #: The signal that stopped a "cancelled" session.
    signal: int | None = None
    #: The reader of stdout went away; the session ended quietly.
    stdout_closed: bool = False
    #: Ctrl-C: True when the server hadn't finished within STOP_DRAIN_S.
    incomplete: bool = False
    #: "idle" or "cap" for a drain timeout.
    drain_reason: str | None = None
    #: The bound that expired, in seconds, for a drain timeout.
    drain_limit_s: float = 0.0


@dataclass
class _Clock:
    last_server: float = 0.0
    last_audio: float = 0.0
    first_send: float | None = None
    eof_at: float | None = None
    bytes_sent: int = 0
    last_final_end: float = 0.0


def _final_end(data: dict[str, Any]) -> float | None:
    """Audio time covered by a final result, if this message is one."""
    if data.get("type") == "Results" and data.get("is_final"):
        try:
            return float(data.get("start", 0.0)) + float(data.get("duration", 0.0))
        except (TypeError, ValueError):
            return None
    if data.get("type") == "TurnInfo" and data.get("event") == "EndOfTurn":
        try:
            return float(data.get("audio_window_end", 0.0))
        except (TypeError, ValueError):
            return None
    return None


_AUTH_RE = re.compile(r"(?i)\b(token|bearer)\s+\S+")


def _clean(text: object, limit: int = 200, secrets: tuple[str, ...] = ()) -> str:
    """Server-supplied text for a message: one line, printable, no
    credentials, bounded. Secrets are scrubbed before the cut, so a
    truncation can't leave part of one."""
    out = "".join(ch if ch.isprintable() else " " for ch in str(text))
    for secret in secrets:
        if len(secret) >= 8:  # a real credential, not a stray letter
            out = out.replace(secret, "****" + secret[-4:])
    out = _AUTH_RE.sub(r"\1 ****", out)
    return out.strip()[:limit]


def _closed_outcome(exc: BaseException, secrets: tuple[str, ...]) -> LiveOutcome | None:
    """The outcome for a connection that ended, or None for a clean close."""
    try:
        from websockets.exceptions import ConnectionClosed, ConnectionClosedOK
    except Exception:  # pragma: no cover - websockets is a hard dependency
        return LiveOutcome(
            status="error", kind="network", error=_clean(exc, 200, secrets)
        )
    if isinstance(exc, ConnectionClosedOK):
        return None
    if isinstance(exc, ConnectionClosed):
        rcvd = getattr(exc, "rcvd", None)
        if rcvd is not None:
            reason = _clean(getattr(rcvd, "reason", ""), 200, secrets)
            code = getattr(rcvd, "code", None)
            text = (
                msg.E_LISTEN_SERVER_CLOSED.format(close_code=code, close_reason=reason)
                if reason
                else msg.E_LISTEN_SERVER_CLOSED_NO_REASON.format(close_code=code)
            )
            return LiveOutcome(
                status="error",
                kind="server",
                error=text,
                close_code=code if isinstance(code, int) else None,
            )
        return LiveOutcome(
            status="error", kind="network", error=msg.E_LISTEN_CONNECTION_LOST
        )
    return LiveOutcome(
        status="error",
        kind="network",
        error=_clean(exc, 200, secrets) or exc.__class__.__name__,
    )


def silence_stdout() -> None:
    """The reader of stdout went away (EPIPE): point fd 1 at the null
    device, so buffered output and later writes, including the flush at
    exit, succeed quietly."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)
        os.close(devnull)
    except OSError:
        pass


def _is_connection_closed(exc: BaseException) -> bool:
    try:
        from websockets.exceptions import ConnectionClosed
    except Exception:  # pragma: no cover - websockets is a hard dependency
        return False
    return isinstance(exc, ConnectionClosed)


class _SignalGuard:
    """Ctrl-C (and Ctrl-Break on Windows) and, on POSIX, SIGTERM → ``stop``.

    The first signal starts the drain; ``signum`` records which one it was.
    A second signal ends the process at once: by SIGTERM if either signal
    was SIGTERM, else with exit 2. Uses ``signal.signal`` rather than
    ``loop.add_signal_handler``, which is Unix-only. Outside the main thread
    (tests) it installs nothing.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
        self._loop = loop
        self._stop = stop
        self._count = 0
        self._previous: dict[int, Any] = {}
        self.signum: int | None = None

    def _handler(self, signum: int, _frame: Any) -> None:
        self._count += 1
        if self._count == 1:
            self.signum = signum
            try:
                self._loop.call_soon_threadsafe(self._stop.set)
                return
            except BaseException:
                pass
        # Second signal, or the loop is already gone: leave without
        # draining. stdout was flushed line by line.
        sigterm = getattr(signal, "SIGTERM", None)
        if sigterm is not None and sigterm in (signum, self.signum) and os.name != "nt":
            die_by_signal(sigterm)
        os._exit(2)

    def install(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return
        sigs = [signal.SIGINT]
        sigbreak = getattr(signal, "SIGBREAK", None)
        if sigbreak is not None:
            sigs.append(sigbreak)
        if os.name != "nt":
            sigs.append(signal.SIGTERM)
        for sig in sigs:
            try:
                self._previous[sig] = signal.signal(sig, self._handler)
            except (OSError, ValueError):
                pass

    def restore(self) -> None:
        for sig, previous in self._previous.items():
            try:
                signal.signal(sig, previous)
            except (OSError, ValueError, TypeError):
                pass
        self._previous.clear()


class EarlyStop:
    """Ctrl-C for a mic session before ``run_session`` installs its guard.

    Installed before the banner, so a Ctrl-C during the imports or while
    connecting is recorded instead of raising ``KeyboardInterrupt``; once
    ``bind`` hands over the session's ``stop`` event, it ends the session
    like the guard's first signal would. A second Ctrl-C exits at once.
    asyncio.run leaves a non-default SIGINT handler alone, so this one stays
    in place until ``restore``. Outside the main thread it does nothing.
    """

    def __init__(self) -> None:
        self.signum: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._previous: Any = None
        self._installed = False

    def _handler(self, signum: int, _frame: Any) -> None:
        if self.signum is not None:
            os._exit(2)
        self.signum = signum
        if self._loop is not None and self._stop is not None:
            try:
                self._loop.call_soon_threadsafe(self._stop.set)
            except RuntimeError:
                pass

    def install(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return
        try:
            self._previous = signal.signal(signal.SIGINT, self._handler)
            self._installed = True
        except (OSError, ValueError):
            pass

    def bind(self, loop: asyncio.AbstractEventLoop) -> asyncio.Event:
        """The ``stop`` event for ``run_session``; already set if Ctrl-C came."""
        self._stop = asyncio.Event()
        self._loop = loop
        if self.signum is not None:
            self._stop.set()
        return self._stop

    def restore(self) -> None:
        if self._installed:
            try:
                signal.signal(signal.SIGINT, self._previous)
            except (OSError, ValueError, TypeError):
                pass
            self._installed = False


def die_by_signal(signum: int) -> None:
    """End the process by ``signum`` with its default action, so the wait
    status is "killed by signal N" (shells report 128+N).

    PID 1 in a container ignores a default-action signal, so if the process
    is still here afterwards it exits with the shell's 128+N.
    """
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    try:
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)
        time.sleep(0.05)
    except Exception:
        pass
    os._exit(128 + signum)


async def _cancel_all(*tasks: asyncio.Task[Any] | None) -> None:
    live = [t for t in tasks if t is not None]
    for task in live:
        if not task.done():
            task.cancel()
    for task in live:
        try:
            await task
        except BaseException:
            pass


def _open(url: str, headers: dict[str, str], **options: Any) -> Any:
    """Open the live socket; a redirect on the upgrade, to the same origin
    or another, raises ``RedirectRefused`` before a second connection, so
    the key and the audio go only to the host that passed the host check."""
    from deepctl_core.websocket import open_async

    return open_async(url, headers, command="dg listen", payload="audio", **options)


async def run_session(
    url: str,
    headers: dict[str, str],
    *,
    on_message: Callable[[str | bytes], None],
    queue: asyncio.Queue[Any],
    closing: threading.Event,
    start_source: Callable[[], None],
    byte_rate: int,
    stop: asyncio.Event | None = None,
) -> LiveOutcome:
    """Run one live session; see the module docstring for how it ends.

    ``queue`` carries audio ``bytes``, ``b""`` at end of input, or an
    exception from the source. ``start_source`` starts the producer once the
    socket is open. ``byte_rate`` is the audio byte rate when it is known
    (raw PCM, mulaw, alaw), else 0. ``closing`` is set before any task is
    cancelled, so the producer stops handing over audio first.
    """
    loop = asyncio.get_running_loop()
    stop = stop or asyncio.Event()
    guard = _SignalGuard(loop, stop)
    guard.install()
    clock = _Clock()
    now = time.monotonic
    # Values that must never be echoed back (the credential in the header).
    secrets = tuple(v.split(" ", 1)[-1] for v in headers.values())
    request_id: str | None = None
    ws: Any = None
    tasks: dict[str, asyncio.Task[Any] | None] = {}
    last: list[LiveOutcome] = []

    def track(raw: str | bytes) -> None:
        nonlocal request_id
        clock.last_server = now()
        try:
            data = json.loads(raw)
        except Exception:
            return
        if isinstance(data, dict):
            rid = data.get("request_id") or (data.get("metadata") or {}).get(
                "request_id"
            )
            if isinstance(rid, str) and not request_id:
                request_id = _clean(rid, 100, secrets)
            end = _final_end(data)
            if end is not None:
                clock.last_final_end = max(clock.last_final_end, end)

    async def receive() -> LiveOutcome | None:
        """Read until the server closes. Returns the failure, or None."""
        try:
            async for raw in ws:
                track(raw)
                on_message(raw)
        except BrokenPipeError:
            # Whoever read stdout is gone (``| head -n 1``): end quietly.
            silence_stdout()
            return LiveOutcome(stdout_closed=True)
        except click.ClickException as exc:
            # A server-reported error frame (Flux "Error").
            return LiveOutcome(
                status="error", kind="server", error=_clean(exc.message, 200, secrets)
            )
        except Exception as exc:
            return _closed_outcome(exc, secrets)
        return None

    def ended(result: LiveOutcome | None) -> LiveOutcome:
        out = result or LiveOutcome()
        out.request_id = request_id
        last.append(out)
        return out

    async def send_audio() -> str:
        """Send audio until end of input, then CloseStream. Returns "eof"."""
        while True:
            item = await queue.get()
            if isinstance(item, BaseException):
                raise item
            if not item:
                break
            await ws.send(item)
            t = now()
            clock.last_audio = t
            if clock.first_send is None:
                clock.first_send = t
            clock.bytes_sent += len(item)
        clock.eof_at = now()
        await ws.send(CLOSE_STREAM)
        return "eof"

    def eof_cap() -> float:
        if byte_rate:
            behind = clock.bytes_sent / byte_rate - clock.last_final_end
        else:
            start = clock.first_send if clock.first_send is not None else clock.eof_at
            behind = (clock.eof_at or now()) - (start or now())
        return max(EOF_CAP_MIN_S, 2.0 * max(0.0, behind))

    async def finish_on_stop(deadline: float) -> LiveOutcome:
        """Ctrl-C: stop sending, CloseStream, wait for the server until deadline."""
        closing.set()
        await _cancel_all(tasks.get("send"))
        recv = tasks["recv"]
        assert recv is not None
        if clock.eof_at is None and not recv.done():
            try:
                await asyncio.wait_for(
                    ws.send(CLOSE_STREAM), max(0.0, deadline - now())
                )
            except BaseException:
                pass
        if not recv.done():
            await asyncio.wait({recv}, timeout=max(0.0, deadline - now()))
        done = recv.done() and not recv.cancelled()
        return LiveOutcome(
            status="cancelled",
            incomplete=not done,
            signal=guard.signum,
            request_id=request_id,
        )

    try:
        stop_wait = asyncio.ensure_future(stop.wait())
        tasks["stop"] = stop_wait
        connect = asyncio.ensure_future(
            _open(url, headers, close_timeout=CLOSE_TIMEOUT_S)
        )
        tasks["connect"] = connect
        await asyncio.wait({connect, stop_wait}, return_when=asyncio.FIRST_COMPLETED)
        if not connect.done():
            closing.set()
            await _cancel_all(connect)
            return LiveOutcome(status="cancelled", signal=guard.signum)
        ws = connect.result()
        clock.last_server = clock.last_audio = now()

        start_source()
        recv = asyncio.ensure_future(receive())
        send = asyncio.ensure_future(send_audio())
        tasks.update(recv=recv, send=send)

        # ── Streaming ─────────────────────────────────────────────────
        await asyncio.wait({recv, send, stop_wait}, return_when=asyncio.FIRST_COMPLETED)

        if stop_wait.done():
            return await finish_on_stop(now() + STOP_DRAIN_S)

        if recv.done():
            return ended(recv.result())

        # The sender finished first: end of input, or a failure.
        try:
            send.result()
        except Exception as exc:
            if _is_connection_closed(exc):
                # The socket went away while sending; the receiver has the
                # reason (or soon will).
                await asyncio.wait({recv}, timeout=CLOSE_TIMEOUT_S)
                return ended(
                    recv.result() if recv.done() else _closed_outcome(exc, secrets)
                )
            reason = getattr(exc, "strerror", None) or exc
            return ended(
                LiveOutcome(
                    status="error",
                    kind="read",
                    error=msg.E_LISTEN_STDIN_UNREADABLE.format(
                        os_error=_clean(reason, 200, secrets)
                    ),
                )
            )

        # ── End of input: drain ───────────────────────────────────────
        assert clock.eof_at is not None
        cap = eof_cap()
        cap_at = clock.eof_at + cap
        while True:
            idle_at = max(clock.eof_at, clock.last_server) + EOF_IDLE_S
            wake = min(idle_at, cap_at)
            remaining = wake - now()
            if remaining <= 0:
                closing.set()
                if cap_at <= idle_at:
                    return ended(
                        LiveOutcome(
                            status="drain_timeout",
                            drain_reason="cap",
                            drain_limit_s=cap,
                        )
                    )
                return ended(
                    LiveOutcome(
                        status="drain_timeout",
                        drain_reason="idle",
                        drain_limit_s=EOF_IDLE_S,
                    )
                )
            await asyncio.wait(
                {recv, stop_wait},
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if recv.done():
                return ended(recv.result())
            if stop_wait.done():
                return await finish_on_stop(now() + STOP_DRAIN_S)
    finally:
        # Order matters: stop the producer before cancelling anything, so a
        # hand-off cancelled below can't surface in the reader thread.
        closing.set()
        await _cancel_all(*(t for k, t in tasks.items() if k != "recv"))
        recv_task = tasks.get("recv")
        if ws is not None:
            if recv_task is None or not recv_task.done():
                # The server hasn't closed: don't wait for its handshake.
                transport = getattr(ws, "transport", None)
                if transport is not None:
                    transport.abort()
            close = getattr(ws, "close", None)
            if close is not None:
                try:
                    await asyncio.wait_for(close(), CLOSE_TIMEOUT_S)
                except BaseException:
                    pass
        await _cancel_all(recv_task)
        guard.restore()
        # A signal that landed while the server was closing still counts:
        # the clean close becomes a stop by that signal.
        if guard.signum is not None:
            for out in last:
                if out.status == "success" and not out.stdout_closed:
                    out.status = "cancelled"
                    out.signal = guard.signum
