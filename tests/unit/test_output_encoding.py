"""Output never fails on characters the stream's encoding lacks.

Windows gives a redirected stdout the ANSI code page (cp1252), which has no
block art or check mark. PYTHONIOENCODING=cp1252 reproduces that stream on
any platform.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from deepctl.main import safe_output_streams

if TYPE_CHECKING:
    from pathlib import Path


def _stream(encoding: str, errors: str = "strict") -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors=errors)


def _written(stream: io.TextIOWrapper) -> bytes:
    stream.flush()
    buffer = stream.buffer
    assert isinstance(buffer, io.BytesIO)
    return buffer.getvalue()


@pytest.mark.parametrize("errors", ["strict", "surrogateescape"])
def test_cp1252_stream_escapes_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch, errors: str
) -> None:
    # surrogateescape is what Python picks under a C locale (Wine has one).
    out, err = _stream("cp1252", errors), _stream("cp1252", errors)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    safe_output_streams()
    out.write("ok ✓ café █")
    err.write("✗")
    assert _written(out) == b"ok \\u2713 caf\xe9 \\u2588"
    assert _written(err) == b"\\u2717"
    # The encoding itself is kept: only unencodable characters are escaped.
    assert out.encoding == "cp1252"


def test_utf8_stream_is_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _stream("UTF-8")
    monkeypatch.setattr(sys, "stdout", out)
    safe_output_streams()
    assert out.errors == "strict"
    assert out.encoding == "UTF-8"


def test_chosen_error_handler_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _stream("cp1252", "replace")
    monkeypatch.setattr(sys, "stdout", out)
    safe_output_streams()
    assert out.errors == "replace"


@pytest.mark.parametrize("stream", [None, object(), io.StringIO()])
def test_odd_streams_are_skipped(
    monkeypatch: pytest.MonkeyPatch, stream: object
) -> None:
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)
    safe_output_streams()  # no error


def test_closed_stream_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _stream("cp1252")
    out.close()
    monkeypatch.setattr(sys, "stdout", out)
    safe_output_streams()


@pytest.mark.parametrize("args", [["--help"], ["ffprobe", "--help"], ["--version"]])
def test_redirected_cp1252_output_does_not_crash(
    tmp_path: Path, args: list[str]
) -> None:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("DEEPCTL_", "DEEPGRAM_", "PYTHONIO", "PYTHONUTF8"))
    }
    env.update(
        HOME=str(tmp_path),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
        XDG_STATE_HOME=str(tmp_path / "state"),
        PYTHONIOENCODING="cp1252",
        PYTHONUTF8="0",
        DEEPCTL_TELEMETRY_DISABLED="1",
        NO_COLOR="1",
    )
    proc = subprocess.run(
        [sys.executable, "-c", "from deepctl.main import main; main()", *args],
        capture_output=True,
        env=env,
        cwd=tmp_path,
        timeout=60,
        check=False,
    )
    assert b"codec can't encode" not in proc.stderr, proc.stderr[-2000:]
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout
