"""Result files: short writes, a FIFO in the way, and the Windows path.

The Windows path (``_save_paths``) uses plain paths, so it runs on any
platform with ``_posix`` patched to False; a reparse point is simulated
with a stat result that carries ``st_file_attributes``.
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from deepctl_core import result_store

needs_fifo = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs mkfifo")
posix_only = pytest.mark.skipif(
    not result_store._posix(), reason="POSIX descriptors"
)


@pytest.fixture
def tmp_root(tmp_path, monkeypatch):
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp))
    return tmp


@pytest.fixture
def windows_path(monkeypatch):
    monkeypatch.setattr(result_store, "_posix", lambda: False)


@pytest.fixture
def short_writes(monkeypatch):
    """``os.write`` takes at most 7 bytes per call, as a pipe or a slow
    file system may."""
    real = os.write
    calls = []

    def write(fd, data):
        calls.append(len(data))
        return real(fd, bytes(data[:7]))

    monkeypatch.setattr(result_store.os, "write", write)
    return calls


def _bounded(fn, seconds: float = 10.0):
    """Run ``fn`` in a thread; fail (instead of hanging) if it blocks."""
    box: dict[str, object] = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # pragma: no cover - reported below
            box["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(seconds)
    assert not thread.is_alive(), "blocked on a FIFO"
    if "error" in box:
        raise box["error"]  # type: ignore[misc]
    return box["value"]


FULL = b'{"transcript": "' + b"x" * 200 + b'"}\n'
TEXT = b"hello there, a transcript longer than one short write\n"


# ── every write loops until all bytes are out ─────────────────────────


@posix_only
def test_posix_short_writes_still_write_everything(tmp_path, tmp_root, short_writes):
    saved = result_store.save(FULL, TEXT, state=tmp_path / "state")
    assert saved.full_result_path.read_bytes() == FULL
    assert saved.transcript_path.read_bytes() == TEXT
    assert len(short_writes) > 2


def test_windows_short_writes_still_write_everything(
    tmp_path, tmp_root, windows_path, short_writes
):
    saved = result_store.save(FULL, TEXT, state=tmp_path / "state")
    assert saved.full_result_path.parent == (tmp_path / "state" / "results").resolve()
    assert saved.full_result_path.read_bytes() == FULL
    assert saved.transcript_path.read_bytes() == TEXT
    assert len(short_writes) > 2


# ── the Windows path prunes like the POSIX one ───────────────────────


def test_windows_path_prunes_old_and_oversized_pairs(
    tmp_path, tmp_root, windows_path, monkeypatch
):
    results = tmp_path / "state" / "results"
    results.mkdir(parents=True)
    now = time.time()

    def pair(stem: str, age_s: float, size: int) -> None:
        for suffix in (".json", ".txt"):
            p = results / (stem + suffix)
            p.write_bytes(b"x" * size)
            os.utime(p, (now - age_s, now - age_s))

    pair("20261030T000000Z-00000001", 25 * 3600, 10)  # older than 24 h
    pair("20261031T000000Z-00000002", 3600 * 3, 400)
    pair("20261031T010000Z-00000003", 3600 * 2, 400)
    (results / "unrelated.json").write_bytes(b"keep me")
    monkeypatch.setattr(result_store, "MAX_TOTAL_BYTES", 1700)
    saved = result_store.save(b"y" * 100, b"z" * 100, state=tmp_path / "state")
    left = sorted(p.name for p in results.iterdir())
    assert "20261030T000000Z-00000001.json" not in left
    assert "20261031T000000Z-00000002.json" not in left
    assert "20261031T010000Z-00000003.json" in left
    assert saved.full_result_path.name in left
    assert saved.transcript_path.name in left
    assert "unrelated.json" in left


# ── the Windows path refuses links and junctions ─────────────────────


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_windows_path_refuses_a_symlinked_results_dir(
    tmp_path, tmp_root, windows_path
):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    try:
        (state / "results").symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("can't create a symlink here")
    saved = result_store.save(FULL, TEXT, state=state)
    assert list(elsewhere.iterdir()) == []
    assert saved.full_result_path.parent == (tmp_root / "deepctl" / "results").resolve()


def test_reparse_point_is_not_a_plain_dir(tmp_path, monkeypatch):
    target = tmp_path / "junction"
    target.mkdir()
    real = os.lstat

    def lstat(path, *args, **kwargs):
        st = real(path, *args, **kwargs)
        if Path(path) == target:
            return SimpleNamespace(st_mode=st.st_mode, st_file_attributes=0x400)
        return st

    assert result_store._is_plain_dir(target) is True
    monkeypatch.setattr(result_store.os, "lstat", lstat)
    assert result_store._is_plain_dir(target) is False
    assert result_store._save_paths(target, FULL, TEXT) is None
    assert list(target.iterdir()) == []


def test_windows_path_refuses_a_file_named_results(tmp_path, tmp_root, windows_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "results").write_text("not a directory")
    saved = result_store.save(FULL, TEXT, state=state)
    assert saved.full_result_path.parent == (tmp_root / "deepctl" / "results").resolve()


# ── a FIFO where the results directory goes ──────────────────────────


@needs_fifo
@posix_only
def test_fifo_in_the_state_dir_falls_back_without_blocking(tmp_path, tmp_root):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    os.mkfifo(state / "results", 0o600)
    saved = _bounded(lambda: result_store.save(FULL, TEXT, state=state))
    assert stat.S_ISFIFO(os.lstat(state / "results").st_mode)  # left alone
    expected = (tmp_root / f"deepctl-{os.geteuid()}" / "results").resolve()
    assert saved.full_result_path.parent == expected
    assert saved.full_result_path.read_bytes() == FULL


@needs_fifo
@posix_only
def test_fifo_in_the_temp_dir_is_refused_with_one_warning(tmp_path, tmp_root):
    state = tmp_path / "file"
    state.write_text("not a directory")
    mine = tmp_root / f"deepctl-{os.geteuid()}"
    mine.mkdir(mode=0o700)
    os.mkfifo(mine / "results", 0o600)
    warned: list[str] = []
    saved = _bounded(
        lambda: result_store.save(FULL, TEXT, state=state, warn=warned.append)
    )
    assert warned == [str(mine / "results")]
    # A fresh directory of this run's own took the pair.
    assert saved.full_result_path.parent.parent.name.startswith(
        f"deepctl-{os.geteuid()}-"
    )
    assert saved.full_result_path.read_bytes() == FULL
    assert stat.S_ISFIFO(os.lstat(mine / "results").st_mode)


@needs_fifo
@posix_only
def test_symlinked_results_in_the_temp_dir_is_not_followed(tmp_path, tmp_root):
    state = tmp_path / "file"
    state.write_text("not a directory")
    mine = tmp_root / f"deepctl-{os.geteuid()}"
    mine.mkdir(mode=0o700)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    (mine / "results").symlink_to(elsewhere)
    warned: list[str] = []
    saved = result_store.save(FULL, TEXT, state=state, warn=warned.append)
    assert warned == [str(mine / "results")]
    assert list(elsewhere.iterdir()) == []
    assert saved.full_result_path.read_bytes() == FULL


@needs_fifo
def test_fifo_on_the_windows_path_is_refused_without_blocking(
    tmp_path, tmp_root, windows_path
):
    state = tmp_path / "state"
    state.mkdir()
    os.mkfifo(state / "results", 0o600)
    saved = _bounded(lambda: result_store.save(FULL, TEXT, state=state))
    assert saved.full_result_path.parent == (tmp_root / "deepctl" / "results").resolve()


@needs_fifo
@posix_only
def test_fifo_named_like_a_result_is_never_opened(tmp_path, tmp_root, monkeypatch):
    state = tmp_path / "state"
    results = state / "results"
    results.mkdir(parents=True, mode=0o700)
    os.chmod(state, 0o700)
    monkeypatch.setattr(result_store, "_stem", lambda: "20261101T000000Z-0000abcd")
    os.mkfifo(results / "20261101T000000Z-0000abcd.json", 0o600)
    # Every location gets the same name here, so each O_EXCL open fails.
    tmp_results = tmp_root / f"deepctl-{os.geteuid()}" / "results"
    tmp_results.mkdir(parents=True, mode=0o700)
    os.chmod(tmp_results.parent, 0o700)
    os.mkfifo(tmp_results / "20261101T000000Z-0000abcd.json", 0o600)
    with pytest.raises(result_store.ResultStoreError):
        _bounded(lambda: result_store.save(FULL, TEXT, state=state))
    assert stat.S_ISFIFO(os.lstat(results / "20261101T000000Z-0000abcd.json").st_mode)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_write_all_handles_an_empty_payload(tmp_path):
    path = tmp_path / "empty"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        result_store._write_all(fd, b"")
    finally:
        os.close(fd)
    assert path.read_bytes() == b""


@needs_fifo
def test_windows_path_leaves_a_file_it_did_not_create(
    tmp_path, tmp_root, windows_path, monkeypatch
):
    state = tmp_path / "state"
    results = state / "results"
    results.mkdir(parents=True)
    monkeypatch.setattr(result_store, "_stem", lambda: "20261101T000000Z-0000abcd")
    os.mkfifo(results / "20261101T000000Z-0000abcd.txt", 0o600)
    saved = _bounded(lambda: result_store.save(FULL, TEXT, state=state))
    # The .json this call wrote before the clash is gone; the FIFO stays.
    assert sorted(p.name for p in results.iterdir()) == [
        "20261101T000000Z-0000abcd.txt"
    ]
    assert saved.full_result_path.parent == (tmp_root / "deepctl" / "results").resolve()
