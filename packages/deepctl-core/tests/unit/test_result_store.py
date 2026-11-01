"""Files for compacted results: modes, fallback, pruning."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import time
from pathlib import Path  # noqa: TC003

import pytest
from deepctl_core import result_store

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX modes and descriptors"
)


@pytest.fixture
def tmp_root(tmp_path, monkeypatch):
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp))
    return tmp


def mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def test_pair_is_written_private_under_the_state_dir(tmp_path, tmp_root):
    state = tmp_path / "state"
    saved = result_store.save(b'{"a":1}\n', b"hello\n", state=state)
    assert saved.full_result_path.parent == (state / "results").resolve()
    assert saved.full_result_path.is_absolute()
    assert saved.full_result_path.read_bytes() == b'{"a":1}\n'
    assert saved.transcript_path.read_bytes() == b"hello\n"
    assert saved.full_result_path.stem == saved.transcript_path.stem
    assert result_store._NAME.match(saved.full_result_path.name)
    assert mode(state / "results") == 0o700
    assert mode(saved.full_result_path) == 0o600
    assert mode(saved.transcript_path) == 0o600


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores directory modes",
)
def test_unwritable_state_dir_falls_back_to_the_user_temp_dir(tmp_path, tmp_root):
    state = tmp_path / "ro"
    state.mkdir()
    os.chmod(state, 0o500)
    try:
        saved = result_store.save(b"{}\n", b"", state=state)
    finally:
        os.chmod(state, 0o700)
    expected = (tmp_root / f"deepctl-{os.geteuid()}" / "results").resolve()
    assert saved.full_result_path.parent == expected
    assert mode(tmp_root / f"deepctl-{os.geteuid()}") == 0o700


def test_own_readable_temp_dir_is_tightened(tmp_path, tmp_root):
    mine = tmp_root / f"deepctl-{os.geteuid()}"
    mine.mkdir(mode=0o755)
    os.chmod(mine, 0o755)
    state = tmp_path / "file"
    state.write_text("not a directory")
    saved = result_store.save(b"{}\n", b"", state=state)
    assert saved.full_result_path.parent == (mine / "results").resolve()
    assert mode(mine) == 0o700


@pytest.mark.parametrize("plant", ["symlink", "writable"])
def test_planted_temp_dir_is_refused_with_one_warning(tmp_path, tmp_root, plant):
    name = tmp_root / f"deepctl-{os.geteuid()}"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    if plant == "symlink":
        name.symlink_to(elsewhere)
    else:
        name.mkdir()
        os.chmod(name, 0o777)
    state = tmp_path / "file"
    state.write_text("not a directory")
    warned: list[str] = []
    saved = result_store.save(b"{}\n", b"", state=state, warn=warned.append)
    assert warned == [str(name)]
    assert not str(saved.full_result_path).startswith(str(elsewhere.resolve()))
    assert saved.full_result_path.parent.parent.name.startswith(
        f"deepctl-{os.geteuid()}-"
    )
    assert list(elsewhere.iterdir()) == []


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores directory modes",
)
def test_both_failing_raises_with_the_state_dir(tmp_path, tmp_root, monkeypatch):
    state = tmp_path / "file"
    state.write_text("not a directory")
    os.chmod(tmp_root, 0o500)
    monkeypatch.setattr(
        tempfile, "mkdtemp", lambda **kw: (_ for _ in ()).throw(OSError(13, "denied"))
    )
    try:
        with pytest.raises(result_store.ResultStoreError) as err:
            result_store.save(b"{}\n", b"", state=state)
    finally:
        os.chmod(tmp_root, 0o700)
    assert err.value.directory == state


def test_a_failed_write_leaves_no_partial_file(tmp_path, tmp_root, monkeypatch):
    real = result_store._write_at
    calls = []

    def flaky(dir_fd, name, data):
        calls.append(name)
        if name.endswith(".txt") and len(calls) == 2:
            real(dir_fd, name, data[:1])
            raise OSError(28, "No space left on device")
        real(dir_fd, name, data)

    monkeypatch.setattr(result_store, "_write_at", flaky)
    state = tmp_path / "state"
    saved = result_store.save(b"{}\n", b"text\n", state=state)
    # The state dir's partial pair is gone; the temp dir took the result.
    assert list((state / "results").iterdir()) == []
    assert saved.full_result_path.parent.parent.name == f"deepctl-{os.geteuid()}"


def test_pruning_drops_old_pairs_then_the_oldest_over_the_cap(
    tmp_path, tmp_root, monkeypatch
):
    state = tmp_path / "state"
    results = state / "results"
    state.mkdir(mode=0o700)
    results.mkdir(mode=0o700)
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
    saved = result_store.save(b"y" * 100, b"z" * 100, state=state)
    left = sorted(p.name for p in results.iterdir())
    assert "20261030T000000Z-00000001.json" not in left
    assert "20261031T000000Z-00000002.json" not in left  # oldest over the cap
    assert "20261031T010000Z-00000003.json" in left
    assert saved.full_result_path.name in left
    assert "unrelated.json" in left


@pytest.mark.parametrize(
    ("target", "expected"), [("file", True), ("devnull", True), ("pipe", False)]
)
def test_stdout_file_or_null(tmp_path, monkeypatch, target, expected):
    import io

    if target == "file":
        handle = open(tmp_path / "out", "w")  # noqa: SIM115
    elif target == "devnull":
        handle = open(os.devnull, "w")  # noqa: SIM115
    else:
        r, w = os.pipe()
        os.close(r)
        handle = io.TextIOWrapper(io.FileIO(w, "w"))
    try:
        monkeypatch.setattr(sys, "stdout", handle)
        assert result_store.stdout_is_file_or_null() is expected
    finally:
        monkeypatch.undo()
        handle.close()
