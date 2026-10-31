"""deepctl_core.signals: the SIGINT handler that waits out importlib's own
lock bookkeeping, and the helpers behind it."""

from __future__ import annotations

import _imp
import importlib
import os
import signal
import sys
import threading
import time

import pytest
from deepctl_core import signals


@pytest.fixture(autouse=True)
def _restore_sigint():
    before = signal.getsignal(signal.SIGINT)
    for state in (signals._deferred_since, signals._in_handler):
        state.clear()
    yield
    signal.signal(signal.SIGINT, before)
    for state in (signals._deferred_since, signals._in_handler):
        state.clear()


def _importlib_frame():
    """A real frame of importlib's own code: the caller of a finder."""
    seen = []

    class Finder:
        def find_spec(self, name, path=None, target=None):
            if name == "deepctl_signals_probe_xyz":
                seen.append(sys._getframe(1))
            return None

    finder = Finder()
    sys.meta_path.insert(0, finder)
    try:
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("deepctl_signals_probe_xyz")
    finally:
        sys.meta_path.remove(finder)
    return seen[0]


def test_replaces_only_the_default_handler():
    signal.signal(signal.SIGINT, signal.default_int_handler)
    signals.install_interrupt_handler()
    assert signal.getsignal(signal.SIGINT) is signals._on_interrupt

    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signals.install_interrupt_handler()
    assert signal.getsignal(signal.SIGINT) is signal.SIG_IGN


def test_in_importlib_knows_importlibs_frames():
    assert signals.in_importlib(_importlib_frame()) is True
    assert signals.in_importlib(sys._getframe()) is False
    assert signals.in_importlib(None) is False


def test_a_signal_inside_this_modules_frames_is_judged_by_their_caller():
    """A second signal that lands in a handler's frames here counts as
    landing where the first one interrupted: importlib, in this case."""
    ns: dict = {}
    src = (
        "import sys\n"
        "def find_spec(name, path=None, target=None):\n"
        "    if name == 'deepctl_signals_probe_own':\n"
        "        seen.append(sys._getframe(0))\n"
    )
    ns["seen"] = seen = []
    exec(compile(src, signals._own_file(), "exec"), ns)

    class Finder:
        find_spec = staticmethod(ns["find_spec"])

    finder = Finder()
    sys.meta_path.insert(0, finder)
    try:
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("deepctl_signals_probe_own")
    finally:
        sys.meta_path.remove(finder)
    own = seen[0]
    assert own.f_code.co_filename == signals._own_file()
    assert signals.in_importlib(own) is True
    assert signals.in_importlib(sys._getframe()) is False


def test_a_second_ctrl_c_while_the_handler_runs_is_coalesced(monkeypatch):
    later: list[int] = []
    monkeypatch.setattr(signals, "deliver_later", later.append)

    def nested_then_raise(signum, frame):
        signals._on_interrupt(signum, frame)  # nested: no raise, nothing sent
        return False

    monkeypatch.setattr(signals, "defer_in_importlib", nested_then_raise)
    with pytest.raises(KeyboardInterrupt):
        signals._on_interrupt(signal.SIGINT, sys._getframe())
    assert later == []


def test_the_resend_waits_for_the_handler_to_return(monkeypatch):
    """Under load the re-send timer can fire before the handler that
    started it returns; it waits, so the signal isn't taken as nested."""
    sent: list[int] = []
    monkeypatch.setattr(signals, "send_to_main", sent.append)
    signals._in_handler.append(True)
    t = threading.Thread(target=signals._resend, args=(signal.SIGINT,))
    t.start()
    time.sleep(0.1)
    assert sent == []
    signals._in_handler.clear()
    t.join(2)
    assert sent == [signal.SIGINT]


def test_raises_outside_importlib():
    with pytest.raises(KeyboardInterrupt):
        signals._on_interrupt(signal.SIGINT, sys._getframe())


def test_defers_inside_importlib(monkeypatch):
    later: list[int] = []
    monkeypatch.setattr(signals, "deliver_later", later.append)
    signals._on_interrupt(signal.SIGINT, _importlib_frame())  # no raise
    assert later == [signal.SIGINT]


def test_deferral_is_capped(monkeypatch):
    """After _MAX_DEFER_S of deferrals the handler raises anyway."""
    later: list[int] = []
    clock = [100.0]
    monkeypatch.setattr(signals, "deliver_later", later.append)
    monkeypatch.setattr(signals.time, "monotonic", lambda: clock[0])
    frame = _importlib_frame()
    signals._on_interrupt(signal.SIGINT, frame)
    clock[0] += signals._MAX_DEFER_S / 2
    signals._on_interrupt(signal.SIGINT, frame)
    clock[0] += signals._MAX_DEFER_S
    with pytest.raises(KeyboardInterrupt):
        signals._on_interrupt(signal.SIGINT, frame)
    assert len(later) == 2
    # The next Ctrl-C starts a fresh window.
    signals._on_interrupt(signal.SIGINT, frame)
    assert len(later) == 3


def test_past_the_cap_a_held_import_lock_still_defers(monkeypatch):
    later: list[int] = []
    clock = [100.0]
    monkeypatch.setattr(signals, "deliver_later", later.append)
    monkeypatch.setattr(signals.time, "monotonic", lambda: clock[0])
    frame = _importlib_frame()
    signals._on_interrupt(signal.SIGINT, frame)
    clock[0] += signals._MAX_DEFER_S + 0.05  # past the cap, not stale
    _imp.acquire_lock()
    try:
        signals._on_interrupt(signal.SIGINT, frame)  # lock held: no raise
    finally:
        _imp.release_lock()
    assert len(later) == 2
    with pytest.raises(KeyboardInterrupt):
        signals._on_interrupt(signal.SIGINT, frame)  # lock free: raise


def test_a_stale_deferral_starts_a_fresh_window(monkeypatch):
    """A deferral whose re-send never came (the handler changed meanwhile)
    must not make a later Ctrl-C in importlib skip the deferral."""
    later: list[int] = []
    clock = [100.0]
    monkeypatch.setattr(signals, "deliver_later", later.append)
    monkeypatch.setattr(signals.time, "monotonic", lambda: clock[0])
    frame = _importlib_frame()
    signals._on_interrupt(signal.SIGINT, frame)  # deferred; re-send "lost"
    clock[0] += signals._STALE_DEFER_S + 1
    signals._on_interrupt(signal.SIGINT, frame)  # deferred again, no raise
    assert later == [signal.SIGINT, signal.SIGINT]


def test_no_thread_for_the_resend_delivers_now(monkeypatch):
    """At interpreter shutdown no thread can start: raise KeyboardInterrupt
    rather than that RuntimeError."""

    def no_thread(signum):
        raise RuntimeError("can't create new thread at interpreter shutdown")

    monkeypatch.setattr(signals, "deliver_later", no_thread)
    with pytest.raises(KeyboardInterrupt):
        signals._on_interrupt(signal.SIGINT, _importlib_frame())
    assert signals._deferred_since == []


def test_coalescing_releases_after_the_handler_raises():
    calls: list[int] = []

    @signals.coalescing
    def handler(signum, frame):
        calls.append(signum)
        handler(signum, frame)  # nested: coalesced, inner body not run
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        handler(signal.SIGINT, None)
    assert calls == [signal.SIGINT]
    assert signals._in_handler == []


@pytest.mark.skipif(not hasattr(signal, "pthread_kill"), reason="POSIX only")
def test_deliver_later_reaches_the_main_thread():
    got: list[int] = []
    before = signal.signal(signal.SIGUSR1, lambda s, f: got.append(s))
    try:
        signals.deliver_later(signal.SIGUSR1, 0)
        deadline = time.monotonic() + 2
        while not got and time.monotonic() < deadline:
            time.sleep(0.005)
    finally:
        signal.signal(signal.SIGUSR1, before)
    assert got == [signal.SIGUSR1]


@pytest.mark.skipif(not hasattr(signal, "pthread_kill"), reason="POSIX only")
def test_another_thread_holding_the_import_lock_doesnt_hold_up_ctrl_c():
    """Only the main thread's own importlib frames defer."""
    signal.signal(signal.SIGINT, signal.default_int_handler)
    signals.install_interrupt_handler()
    held, release = threading.Event(), threading.Event()

    def holder():
        _imp.acquire_lock()
        try:
            held.set()
            release.wait(10)
        finally:
            _imp.release_lock()

    t = threading.Thread(target=holder, daemon=True)
    t.start()
    held.wait(5)
    try:
        t0 = time.monotonic()
        with pytest.raises(KeyboardInterrupt):
            os.kill(os.getpid(), signal.SIGINT)
            while time.monotonic() - t0 < 2:
                time.sleep(0.001)
        assert time.monotonic() - t0 < 1.5
    finally:
        release.set()
        t.join(5)
