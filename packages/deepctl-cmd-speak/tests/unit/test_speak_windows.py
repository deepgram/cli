"""``--out-file`` and ``-o`` where the OS is Windows.

These run on every OS: each test takes away what Windows lacks (os.fchmod
before Python 3.13, signal masks, O_NONBLOCK, os.set_blocking before 3.12)
or adds what only it has (O_BINARY), so the Windows paths are exercised
everywhere.
"""

from __future__ import annotations

import errno
import os
import signal
import stat

import pytest
from deepctl_cmd_speak import sinks


@pytest.fixture
def no_fchmod(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows before Python 3.13: there is no os.fchmod."""
    monkeypatch.delattr(os, "fchmod", raising=False)


def _take_away_sigmask(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("pthread_sigmask", "SIG_BLOCK", "SIG_SETMASK"):
        monkeypatch.delattr(signal, name, raising=False)


@pytest.fixture
def no_sigmask(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every Windows version: no signal.pthread_sigmask, SIG_BLOCK or
    SIG_SETMASK."""
    _take_away_sigmask(monkeypatch)


def test_a_new_file_is_written_without_a_signal_mask(tmp_path, no_sigmask):
    """Every open, write and commit used to end with "module 'signal' has
    no attribute 'SIG_BLOCK'", so `dg speak` couldn't write a file."""
    path = tmp_path / "a.mp3"
    out = sinks.AudioFile(sinks.plan_target(str(path), overwrite=False))
    out.write(b"audio")
    out.commit()
    assert path.read_bytes() == b"audio"


def test_a_failed_write_without_a_signal_mask_reports_the_real_error(
    tmp_path, monkeypatch
):
    """The cleanup after a failed write goes through abort(): it must
    remove the partial file and let the write's own error through, not
    replace it with an AttributeError."""
    path = tmp_path / "a.mp3"
    out = sinks.AudioFile(sinks.plan_target(str(path), overwrite=False))
    out.write(b"first")
    _take_away_sigmask(monkeypatch)

    def full(_fd, _data):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(sinks.os, "write", full)
    with pytest.raises(sinks.WriteFailed) as caught:
        try:
            out.write(b"more")
        except sinks.WriteFailed:
            out.abort()
            raise
    assert caught.value.error.errno == errno.ENOSPC
    monkeypatch.undo()
    assert not path.exists()


def test_replace_commits_without_fchmod(tmp_path, no_fchmod, no_sigmask):
    """`-o existing.mp3` (and `--out-file X --force`) used to end with an
    AttributeError that skipped the cleanup and left the .part file."""
    path = tmp_path / "a.mp3"
    path.write_bytes(b"old")
    out = sinks.AudioFile(sinks.plan_target(str(path), overwrite=True))
    out.write(b"new")
    out.commit()
    assert path.read_bytes() == b"new"
    assert [p.name for p in tmp_path.iterdir()] == ["a.mp3"]


def test_replace_over_a_symlink_commits_without_fchmod(
    tmp_path, no_fchmod, no_sigmask
):
    target = tmp_path / "elsewhere.mp3"
    target.write_bytes(b"keep")
    link = tmp_path / "a.mp3"
    try:
        link.symlink_to(target)
    except OSError as exc:  # Windows without the symlink privilege
        pytest.skip(f"can't make a symlink: {exc}")
    out = sinks.AudioFile(sinks.plan_target(str(link), overwrite=True))
    out.write(b"new")
    out.commit()
    assert not link.is_symlink() and link.read_bytes() == b"new"
    assert target.read_bytes() == b"keep"


def test_a_failed_commit_without_fchmod_still_cleans_up(
    tmp_path, no_fchmod, no_sigmask, monkeypatch
):
    path = tmp_path / "a.mp3"
    path.write_bytes(b"old")
    out = sinks.AudioFile(sinks.plan_target(str(path), overwrite=True))
    out.write(b"new")

    def fail(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(sinks.os, "replace", fail)
    with pytest.raises(sinks.WriteFailed):
        out.commit()
    monkeypatch.undo()
    assert path.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["a.mp3"]


def test_a_new_file_is_opened_in_binary_mode(tmp_path, no_sigmask, monkeypatch):
    """On Windows a file opened without O_BINARY is in text mode: every
    0x0A in the audio would be written as 0x0D 0x0A."""
    binary = getattr(os, "O_BINARY", 0) or 0x8000
    monkeypatch.setattr(os, "O_BINARY", binary, raising=False)
    monkeypatch.setattr(
        sinks, "_NEW_FLAGS", os.O_WRONLY | os.O_CREAT | os.O_EXCL | binary
    )
    real_open = os.open
    flags_seen: list[int] = []

    def open_(path, flags, mode=0o777, **kwargs):
        flags_seen.append(flags)
        if os.name != "nt":
            flags &= ~binary  # the stand-in bit means something else here
        return real_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(sinks.os, "open", open_)
    path = tmp_path / "a.mp3"
    out = sinks.AudioFile(sinks.plan_target(str(path), overwrite=False))
    out.write(b"\n\r\n\x1a")
    out.commit()
    monkeypatch.undo()
    assert flags_seen and all(f & binary for f in flags_seen)
    assert path.read_bytes() == b"\n\r\n\x1a"


def test_the_new_file_flags_include_o_binary_where_it_exists():
    assert sinks._NEW_FLAGS & getattr(os, "O_BINARY", 0) == getattr(
        os, "O_BINARY", 0
    )


@pytest.mark.skipif(os.name == "nt", reason="needs a real character device")
def test_null_device_opens_without_o_nonblock_or_set_blocking(
    no_sigmask, monkeypatch
):
    """Windows has no O_NONBLOCK, and os.set_blocking (none before 3.12)
    works only on pipes: `--out-file NUL` must not need it."""
    monkeypatch.delattr(os, "O_NONBLOCK", raising=False)
    monkeypatch.delattr(os, "set_blocking", raising=False)
    target = sinks.plan_target(os.devnull, overwrite=False)
    assert isinstance(target, sinks.Target), target
    assert target.kind == "special"
    out = sinks.AudioFile(target)
    out.write(b"audio")
    out.commit()


# ── File identity: Windows gives pipes, consoles and devices index 0 ─────


def _no_index(mode: int) -> os.stat_result:
    """What Windows' fstat says about a pipe, a console or NUL."""
    return os.stat_result((mode, 0, 0, 1, 0, 0, 0, 0, 0, 0))


def test_files_without_an_index_are_never_the_same():
    pipe, console = _no_index(stat.S_IFIFO), _no_index(stat.S_IFCHR)
    assert os.path.samestat(pipe, console)  # what made them look alike
    assert not sinks.same_file(pipe, console)
    assert not sinks.same_file(pipe, pipe)


@pytest.mark.parametrize(
    ("mode", "takes"), [(stat.S_IFIFO, False), (stat.S_IFCHR, True)]
)
def test_stdout_without_an_index(monkeypatch, mode, takes):
    """A piped stdout isn't taken for NUL in an agent session; a console
    or NUL still takes audio."""
    monkeypatch.setattr(sinks, "stdout_stat", lambda: _no_index(mode))
    real_stat = os.stat

    def stat_(path, *args, **kwargs):
        if path == os.devnull:
            return _no_index(stat.S_IFCHR)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(sinks.os, "stat", stat_)
    monkeypatch.setattr(sinks.os, "isatty", lambda _fd: False)
    assert sinks.stdout_takes_audio() is takes


@pytest.mark.skipif(os.name == "nt", reason="needs a real character device")
def test_null_device_is_not_taken_for_stderr(monkeypatch):
    """`--out-file NUL` with stderr on a console or a pipe: neither has an
    index, so NUL used to be refused as "stderr"."""
    monkeypatch.setattr(sinks, "_stderr_stat", lambda: _no_index(stat.S_IFCHR))
    monkeypatch.setattr(sinks, "stdout_stat", lambda: _no_index(stat.S_IFIFO))
    real_stat = os.stat

    def stat_(path, *args, **kwargs):
        if path == os.devnull:
            return _no_index(stat.S_IFCHR)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(sinks.os, "stat", stat_)
    target = sinks.plan_target(os.devnull, overwrite=False)
    assert isinstance(target, sinks.Target), target
    assert target.kind == "special"
    monkeypatch.undo()
    os.close(target.fd)


# ── A pipe whose reader went away: Windows says EINVAL, not EPIPE ────────


@pytest.fixture
def pipe_fd():
    """The write end of a real pipe: what fd 1 is under `dg speak | x`."""
    r, w = os.pipe()
    yield w
    os.close(r)
    os.close(w)


class _Buffer:
    """sys.stdout.buffer whose writes fail with ``error``, on descriptor fd."""

    def __init__(self, fd: int, error: OSError) -> None:
        self._fd, self._error = fd, error

    def fileno(self) -> int:
        return self._fd

    def write(self, _data: bytes) -> int:
        raise self._error

    def flush(self) -> None:
        pass


def _audio_to_stdout(monkeypatch, fd: int, error: OSError) -> sinks.AudioFile:
    """`--out-file /dev/stdout` with fd 1 on ``fd``: a pipe or a file."""
    st = os.fstat(fd)
    monkeypatch.setattr(sinks, "stdout_stat", lambda: st)
    out = type("Stdout", (), {"buffer": _Buffer(fd, error)})()
    monkeypatch.setattr(sinks.sys, "stdout", out)
    return sinks.AudioFile(
        sinks.Target("/dev/stdout", "stdout", ident=(st.st_dev, st.st_ino))
    )


def _einval() -> OSError:
    return OSError(errno.EINVAL, "Invalid argument")


def test_einval_writing_audio_to_a_piped_stdout_is_a_reader_gone(
    monkeypatch, pipe_fd
):
    monkeypatch.setattr(sinks.sys, "platform", "win32")
    out = _audio_to_stdout(monkeypatch, pipe_fd, _einval())
    with pytest.raises(sinks.ReaderGone):
        out.write(b"audio")


def test_einval_writing_audio_to_stdout_on_a_file_is_a_write_failure(
    monkeypatch, tmp_path
):
    """Only a pipe: a real EINVAL from a file is still reported."""
    monkeypatch.setattr(sinks.sys, "platform", "win32")
    fd = os.open(tmp_path / "out", os.O_WRONLY | os.O_CREAT)
    try:
        out = _audio_to_stdout(monkeypatch, fd, _einval())
        with pytest.raises(sinks.WriteFailed) as caught:
            out.write(b"audio")
    finally:
        os.close(fd)
    assert caught.value.stdout and caught.value.error.errno == errno.EINVAL


def test_einval_writing_audio_to_a_pipe_elsewhere_is_a_write_failure(
    monkeypatch, pipe_fd
):
    """POSIX keeps EPIPE as the only sign of a reader that went away."""
    monkeypatch.setattr(sinks.sys, "platform", "linux")
    out = _audio_to_stdout(monkeypatch, pipe_fd, _einval())
    with pytest.raises(sinks.WriteFailed):
        out.write(b"audio")
