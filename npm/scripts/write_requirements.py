#!/usr/bin/env python3
"""Write npm/requirements-<version>.txt, the only thing the launcher installs.

    python3 npm/scripts/write_requirements.py --dist dist/ --uploaded-since <ISO time>

The release runs this after PyPI serves the version (verify-published), and
`npm publish` ships the result inside the package. The launcher installs
from it with ``--require-hashes --no-deps --only-binary :all:`` (pip or
``uv pip install``), so every line pins one distribution with its sha256
hashes:

* deepctl and every workspace package it depends on, at its workspace
  version, with the hashes of the files PyPI serves for that version (its
  JSON API, ``/pypi/<name>/<version>/json``). Not hashes of a local build:
  wheels aren't byte-reproducible, and a version that was already on PyPI
  (skip-existing) keeps its original files.
* every other dependency at its uv.lock version, with uv.lock's hashes
  (``uv export --frozen --no-dev --no-emit-workspace``).

``--dist`` cross-checks the release's own build. A file PyPI received
during this run (uploaded at or after ``--uploaded-since``) must match it
byte for byte; a file PyPI already had is kept as PyPI serves it, and the
differing rebuild is only reported. Without ``--uploaded-since``, any
difference fails.

``--index-url`` points the JSON lookups at another index (tests and CI
serve one locally; see local_index.py). Needs uv on PATH and Python 3.11+
(or tomli).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - only taken on Python 3.10
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        sys.exit("error: Python 3.11+ required (tomllib), or install tomli")

ROOT = Path(__file__).resolve().parents[2]
NPM_DIR = ROOT / "npm"
EXPORT = ["uv", "export", "--frozen", "--no-dev", "--no-header", "--no-annotate"]
PYPI = "https://pypi.org"


def normalize(name: str) -> str:
    """Distribution file-name form: lowercase, runs of -_. become _."""
    return re.sub(r"[-_.]+", "_", name).lower()


def canonical(name: str) -> str:
    """Project-name form used in index URLs."""
    return re.sub(r"[-_.]+", "-", name).lower()


def uv_export(*extra: str) -> str:
    return subprocess.run(
        [*EXPORT, *extra], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout


def workspace_members() -> list[tuple[str, str, str]]:
    """(name, version, marker) for deepctl and each workspace dependency."""
    members = []
    for line in uv_export("--no-hashes").splitlines():
        if not line.startswith("-e "):
            continue
        target, _, marker = line[3:].partition(";")
        pyproject = tomllib.loads(
            (ROOT / target.strip() / "pyproject.toml").read_text(encoding="utf-8")
        )
        project = pyproject["project"]
        members.append((project["name"], project["version"], marker.strip()))
    if not members:
        sys.exit("error: uv export listed no workspace packages")
    return sorted(members)


def fetch_release(index: str, name: str, version: str) -> list[dict[str, Any]]:
    """The files the index serves for name==version (its JSON API ``urls``)."""
    url = f"{index.rstrip('/')}/pypi/{canonical(name)}/{version}/json"
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                files: list[dict[str, Any]] = json.load(response)["urls"]
                return files
        except Exception as err:  # retried, then reported
            if attempt == 3:
                sys.exit(f"error: could not read {url}: {err}")
            time.sleep(5 * attempt)
    raise AssertionError("unreachable")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def served_hashes(
    files: list[dict[str, Any]],
    name: str,
    version: str,
    dist: Path | None,
    since: datetime | None,
) -> list[str]:
    """sha256 of every file served for name==version, cross-checked with dist."""
    if not any(f.get("packagetype") == "bdist_wheel" for f in files):
        sys.exit(f"error: the index serves no wheel for {name}=={version}")
    served = {f["filename"]: f for f in files}
    for path in local_files(dist, name, version) if dist else []:
        entry = served.get(path.name)
        if entry is None:
            sys.exit(f"error: {path.name} was built but the index does not serve it")
        local = hashlib.sha256(path.read_bytes()).hexdigest()
        if local == entry["digests"]["sha256"]:
            continue
        uploaded = parse_time(entry["upload_time_iso_8601"])
        if since is None or uploaded >= since:
            sys.exit(
                f"error: {path.name} on the index (sha256 {entry['digests']['sha256']}) "
                f"differs from the file this release built (sha256 {local})"
            )
        print(
            f"note: keeping the index's {path.name}, uploaded {uploaded:%Y-%m-%d} "
            "before this run; the local rebuild differs",
            file=sys.stderr,
        )
    return [served[f]["digests"]["sha256"] for f in sorted(served)]


def local_files(dist: Path, name: str, version: str) -> list[Path]:
    """Wheels and sdists in ``dist`` for name==version."""
    found = []
    for path in sorted(dist.iterdir()):
        if path.name.endswith(".whl"):
            dist_name, dist_version = path.name.split("-")[:2]
        elif path.name.endswith(".tar.gz"):
            dist_name, _, dist_version = path.name[: -len(".tar.gz")].rpartition("-")
        else:
            continue
        if normalize(dist_name) == normalize(name) and dist_version == version:
            found.append(path)
    return found


def requirement(name: str, version: str, marker: str, hashes: list[str]) -> str:
    head = f"{name}=={version}" + (f" ; {marker}" if marker else "")
    return " \\\n".join([head, *(f"    --hash=sha256:{h}" for h in hashes)]) + "\n"


def check(text: str, members: list[tuple[str, str, str]]) -> None:
    """Every requirement carries a hash; every workspace pin is exact."""
    for block in re.split(r"\n(?=\S)", text.strip()):
        if "--hash=sha256:" not in block:
            sys.exit(f"error: no hash for: {block.splitlines()[0]}")
    for name, version, _ in members:
        if not re.search(rf"^{re.escape(name)}=={re.escape(version)}\b", text, re.M):
            sys.exit(f"error: {name}=={version} is missing")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument(
        "--index-url", default=PYPI, help=f"JSON API base (default: {PYPI})"
    )
    parser.add_argument(
        "--dist",
        type=Path,
        help="the release's built wheels and sdists, to cross-check",
    )
    parser.add_argument(
        "--uploaded-since",
        type=parse_time,
        help="when this release run started (ISO 8601): files the index got later must match --dist",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=NPM_DIR,
        help="directory to write into (default: npm/)",
    )
    args = parser.parse_args(argv)

    root = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = root["project"]["version"]
    package = json.loads((NPM_DIR / "package.json").read_text(encoding="utf-8"))
    npm_version = package["version"]
    if npm_version != version:
        sys.exit(f"error: npm/package.json is {npm_version} but deepctl is {version}")

    members = workspace_members()
    ours = ""
    for name, member_version, marker in members:
        files = fetch_release(args.index_url, name, member_version)
        hashes = served_hashes(
            files, name, member_version, args.dist, args.uploaded_since
        )
        ours += requirement(name, member_version, marker, hashes)
    text = ours + uv_export("--no-emit-workspace")
    check(text, members)
    out = args.out / f"requirements-{version}.txt"
    out.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {out}: {len(members)} workspace packages", file=sys.stderr)


if __name__ == "__main__":
    main()
