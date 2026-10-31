"""Ending the process by a signal, for commands that catch one to clean up.

Stdlib only and cheap to import: command modules use it at import time.
"""

from __future__ import annotations

import _imp
import _thread
import os
import signal
import sys
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import FrameType

    Handler = Callable[[int, FrameType | None], None]

__all__ = [
    "coalescing",
    "defer_in_importlib",
    "deliver_later",
    "die_by_signal",
    "in_importlib",
    "install_interrupt_handler",
    "send_to_main",
]


def die_by_signal(signum: int) -> None:
    """End the process by ``signum`` with its default action, so the wait
    status is "killed by signal N" (shells report 128+N).

    stdout and stderr are flushed first. PID 1 in a container ignores a
    default-action signal, so if the process is still here afterwards it
    exits with the shell's 128+N. POSIX only: on Windows ``os.kill`` of our
    own pid is TerminateProcess, so callers there exit instead.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    try:
        signal.signal(signum, signal.SIG_DFL)
        if hasattr(signal, "pthread_sigmask"):
            # A signal left blocked would stay pending and never kill.
            signal.pthread_sigmask(signal.SIG_UNBLOCK, {signum})
        os.kill(os.getpid(), signum)
        time.sleep(0.05)
    except Exception:
        pass
    os._exit(128 + signum)


#: Where importlib's own lock bookkeeping runs. A KeyboardInterrupt raised
#: in one of these frames can land between acquiring the global import lock
#: and the ``try`` that releases it (and in the module-lock weakref callback
#: it is swallowed outright), so every later import, and the run, hangs.
_IMPORTLIB_FILES = frozenset(
    {"<frozen importlib._bootstrap>", "<frozen importlib._bootstrap_external>"}
)

#: The longest a SIGINT is postponed, from its first deferral.
_MAX_DEFER_S = 0.2

#: A deferral older than this was never delivered (the handler changed, or
#: the re-send was lost): the next signal starts a fresh window.
_STALE_DEFER_S = 1.0

_deferred_since: list[float] = []
_in_handler: list[bool] = []


def _own_file() -> str:
    return _own_file.__code__.co_filename


def in_importlib(frame: FrameType | None) -> bool:
    """True when the interrupted ``frame`` is importlib's own code. Frames of
    this module are skipped: a second signal can arrive while a handler here
    runs, and what matters is where the first one interrupted."""
    while frame is not None and frame.f_code.co_filename == _own_file():
        frame = frame.f_back
    return frame is not None and frame.f_code.co_filename in _IMPORTLIB_FILES


def send_to_main(signum: int) -> None:
    """Deliver ``signum`` to the main thread (from any thread).

    POSIX: ``signal.pthread_kill`` to the main thread, a real signal, so it
    also interrupts a blocking wait there. ``_thread.interrupt_main`` would
    only set a flag that is checked once that wait returns.
    Windows: ``_thread.interrupt_main``; ``os.kill`` on our own pid there is
    TerminateProcess, which would skip the cleanup.
    """
    if os.name != "nt" and hasattr(signal, "pthread_kill"):
        main = threading.main_thread().ident
        if main is not None:
            signal.pthread_kill(main, signum)
            return
    try:
        _thread.interrupt_main(signal.Signals(signum))
    except (ValueError, TypeError):
        _thread.interrupt_main()  # a signal it can't simulate: Ctrl-C


def _resend(signum: int) -> None:
    # Under load, starting this timer can take longer than its delay. Wait
    # for the handler that started it to return, or the signal would arrive
    # inside that handler and be taken for a second Ctrl-C.
    deadline = time.monotonic() + 1.0
    while _in_handler and time.monotonic() < deadline:
        time.sleep(0.001)
    send_to_main(signum)


def deliver_later(signum: int, delay: float = 0.01) -> None:
    """Send ``signum`` to the main thread again after ``delay`` seconds."""
    timer = threading.Timer(delay, _resend, (signum,))
    timer.daemon = True
    timer.start()


def defer_in_importlib(signum: int, frame: FrameType | None) -> bool:
    """For a signal handler about to raise: if ``frame`` is importlib's own
    code, send the signal again a moment later and return True (the handler
    returns without raising). After ``_MAX_DEFER_S`` of deferrals (unless
    the import lock is held right now), or outside importlib, return False
    so the handler raises as usual."""
    now = time.monotonic()
    if _deferred_since and now - _deferred_since[0] > _STALE_DEFER_S:
        _deferred_since.clear()
    if in_importlib(frame) and (
        not _deferred_since
        or now - _deferred_since[0] < _MAX_DEFER_S
        or _import_lock_held()
    ):
        try:
            deliver_later(signum)
        except RuntimeError:
            # No new thread at interpreter shutdown: deliver it now instead.
            _deferred_since.clear()
            return False
        if not _deferred_since:
            _deferred_since.append(now)
        return True
    _deferred_since.clear()
    return False


def _import_lock_held() -> bool:
    """Past the cap, a signal is still held back while the import lock is
    held: in importlib's frames that means inside its locked bookkeeping, the
    one place a raise is unsafe. (A main thread waiting for another thread's
    lock is blocked in C, where no handler runs.)"""
    try:
        return bool(_imp.lock_held())
    except Exception:
        return False


def coalescing(handler: Handler) -> Handler:
    """Wrap a signal handler that uses :func:`defer_in_importlib`.

    A signal that arrives while the handler runs is coalesced into the one
    being handled (its frame would be the handler's, not where the first
    signal landed), and a re-send waits for the handler to return.
    """

    def wrapped(signum: int, frame: FrameType | None) -> None:
        if _in_handler:
            return  # a second signal while the first is handled
        _in_handler.append(True)
        try:
            handler(signum, frame)
        finally:
            _in_handler.clear()

    wrapped.__wrapped__ = handler  # type: ignore[attr-defined]
    return wrapped


def _interrupt(signum: int, frame: FrameType | None) -> None:
    if defer_in_importlib(signum, frame):
        return
    raise KeyboardInterrupt


_on_interrupt = coalescing(_interrupt)


def install_interrupt_handler() -> None:
    """Replace Python's default SIGINT handler with one that raises
    ``KeyboardInterrupt`` the same way, except inside importlib's own code
    (see :func:`defer_in_importlib`), where the signal comes again a moment
    later. A SIGINT that is ignored or handled elsewhere is left alone.
    """
    try:
        if signal.getsignal(signal.SIGINT) is signal.default_int_handler:
            signal.signal(signal.SIGINT, _on_interrupt)
    except (OSError, ValueError):
        pass  # not the main thread, or no SIGINT here
