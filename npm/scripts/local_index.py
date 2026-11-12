#!/usr/bin/env python3
"""Lay out wheels and sdists as a static package index, for tests and CI.

    python3 npm/scripts/local_index.py --out idx dist/ wheels/
    python3 -m http.server -d idx 8000

Writes a PEP 503 simple index (``idx/simple/<project>/``, for pip and uv)
and the slice of PyPI's JSON API that write_requirements.py reads
(``idx/pypi/<project>/<version>/json``), so the launcher's hash-locked
install can be exercised end to end without PyPI. Each file's
``upload_time_iso_8601`` is its modification time.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse(filename: str) -> tuple[str, str, str] | None:
    """(project, version, packagetype) from a wheel or sdist file name."""
    if filename.endswith(".whl"):
        name, version = filename.split("-")[:2]
        return canonical(name), version, "bdist_wheel"
    if filename.endswith(".tar.gz"):
        name, _, version = filename[: -len(".tar.gz")].rpartition("-")
        return canonical(name), version, "sdist"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("sources", type=Path, nargs="+")
    args = parser.parse_args()

    projects: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for source in args.sources:
        for path in sorted(source.iterdir()):
            parsed = parse(path.name)
            if not parsed:
                continue
            project, version, packagetype = parsed
            target = args.out / "simple" / project / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            mtime = datetime.fromtimestamp(os.stat(path).st_mtime, timezone.utc)
            projects.setdefault(project, {}).setdefault(version, []).append(
                {
                    "filename": path.name,
                    "packagetype": packagetype,
                    "digests": {
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()
                    },
                    "upload_time_iso_8601": mtime.isoformat().replace("+00:00", "Z"),
                    "url": f"../../../simple/{project}/{path.name}",
                }
            )

    for project, versions in projects.items():
        files = [f for entries in versions.values() for f in entries]
        links = "\n".join(
            f'<a href="{html.escape(f["filename"])}#sha256={f["digests"]["sha256"]}">'
            f"{html.escape(f['filename'])}</a>"
            for f in files
        )
        page = f"<!DOCTYPE html><html><body>\n{links}\n</body></html>\n"
        (args.out / "simple" / project / "index.html").write_text(page)
        for version, entries in versions.items():
            api = args.out / "pypi" / project / version / "json"
            api.parent.mkdir(parents=True, exist_ok=True)
            info = {"name": project, "version": version}
            api.write_text(json.dumps({"info": info, "urls": entries}))
    names = "\n".join(f'<a href="{p}/">{p}</a>' for p in sorted(projects))
    (args.out / "simple" / "index.html").write_text(
        f"<!DOCTYPE html><html><body>\n{names}\n</body></html>\n"
    )
    print(f"{len(projects)} projects in {args.out}")


if __name__ == "__main__":
    main()
