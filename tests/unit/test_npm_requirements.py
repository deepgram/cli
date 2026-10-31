"""npm/scripts/write_requirements.py: workspace hashes come from the index."""

from __future__ import annotations

import functools
import hashlib
import importlib.util
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2] / "npm" / "scripts" / "write_requirements.py"
)


@pytest.fixture
def wr():
    spec = importlib.util.spec_from_file_location("write_requirements", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def index(tmp_path):
    """A static JSON API served over HTTP; returns (base_url, publish)."""
    root = tmp_path / "idx"
    root.mkdir()

    def publish(name: str, version: str, files: dict[str, bytes], uploaded: str):
        entries = [
            {
                "filename": fn,
                "packagetype": "bdist_wheel" if fn.endswith(".whl") else "sdist",
                "digests": {"sha256": hashlib.sha256(data).hexdigest()},
                "upload_time_iso_8601": uploaded,
            }
            for fn, data in files.items()
        ]
        api = root / "pypi" / name / version / "json"
        api.parent.mkdir(parents=True, exist_ok=True)
        api.write_text(json.dumps({"urls": entries}))

    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a: None  # type: ignore[attr-defined]
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", publish
    server.shutdown()


WHEEL = "deepctl_cmd_billing-0.1.0-py3-none-any.whl"
PYPI_BYTES = b"the wheel PyPI has served since 2025"
REBUILT = b"the same source, rebuilt today with new mtimes"


def run(wr, monkeypatch, tmp_path, url, *extra):
    monkeypatch.setattr(
        wr, "workspace_members", lambda: [("deepctl-cmd-billing", "0.1.0", "")]
    )
    monkeypatch.setattr(
        wr, "uv_export", lambda *a: "click==8.1.7 \\\n    --hash=sha256:aa\n"
    )
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    # The repository's root and npm versions agree; only --out is redirected.
    wr.main(["--index-url", url, "--out", str(out), *extra])
    return next(out.iterdir()).read_text()


def test_hashes_come_from_the_index_not_a_local_build(wr, index, monkeypatch, tmp_path):
    url, publish = index
    publish("deepctl-cmd-billing", "0.1.0", {WHEEL: PYPI_BYTES}, "2025-01-01T00:00:00Z")
    text = run(wr, monkeypatch, tmp_path, url)
    assert f"--hash=sha256:{hashlib.sha256(PYPI_BYTES).hexdigest()}" in text
    assert text.startswith("deepctl-cmd-billing==0.1.0 \\\n")


def test_unchanged_sibling_rebuilt_differently_keeps_the_index_hash(
    wr, index, monkeypatch, tmp_path, capsys
):
    """A version already on the index (skip-existing) whose local rebuild differs."""
    url, publish = index
    publish("deepctl-cmd-billing", "0.1.0", {WHEEL: PYPI_BYTES}, "2025-01-01T00:00:00Z")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / WHEEL).write_bytes(REBUILT)
    text = run(
        wr,
        monkeypatch,
        tmp_path,
        url,
        "--dist",
        str(dist),
        "--uploaded-since",
        "2026-10-31T00:00:00Z",
    )
    assert hashlib.sha256(PYPI_BYTES).hexdigest() in text
    assert hashlib.sha256(REBUILT).hexdigest() not in text
    assert "keeping the index's" in capsys.readouterr().err


def test_a_file_uploaded_by_this_run_must_match_the_build(
    wr, index, monkeypatch, tmp_path
):
    url, publish = index
    publish("deepctl-cmd-billing", "0.1.0", {WHEEL: PYPI_BYTES}, "2026-10-31T12:00:00Z")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / WHEEL).write_bytes(REBUILT)
    with pytest.raises(SystemExit, match="differs from the file this release built"):
        run(
            wr,
            monkeypatch,
            tmp_path,
            url,
            "--dist",
            str(dist),
            "--uploaded-since",
            "2026-10-31T00:00:00Z",
        )
    # Without --uploaded-since every difference fails.
    with pytest.raises(SystemExit, match="differs"):
        run(wr, monkeypatch, tmp_path, url, "--dist", str(dist))


def test_a_built_file_the_index_does_not_serve_fails(wr, index, monkeypatch, tmp_path):
    url, publish = index
    publish("deepctl-cmd-billing", "0.1.0", {WHEEL: PYPI_BYTES}, "2025-01-01T00:00:00Z")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "deepctl_cmd_billing-0.1.0.tar.gz").write_bytes(b"sdist")
    with pytest.raises(SystemExit, match="does not serve it"):
        run(wr, monkeypatch, tmp_path, url, "--dist", str(dist))


def test_no_wheel_on_the_index_fails(wr, index, monkeypatch, tmp_path):
    url, publish = index
    publish(
        "deepctl-cmd-billing",
        "0.1.0",
        {"deepctl_cmd_billing-0.1.0.tar.gz": b"s"},
        "2025-01-01T00:00:00Z",
    )
    with pytest.raises(SystemExit, match="no wheel"):
        run(wr, monkeypatch, tmp_path, url)
