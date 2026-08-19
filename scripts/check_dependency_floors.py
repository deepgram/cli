#!/usr/bin/env python3
"""Check (or fix) intra-workspace dependency floors.

Two rules, learned from the 0.3.0 release (PRs #100/#102):

1. **Root floors equal workspace versions.** `dg update` runs
   `pip install --upgrade deepctl`, and pip's default `only-if-needed`
   strategy upgrades a sub-package only when the root floor forces it. Any
   root floor below the current version means that package's fixes are
   published but never delivered on upgrade — `dg --version` reports the new
   release while 13 of 17 packages stay stale, which is what happened before
   0.3.0. Root's dependency list is the delivery manifest, so each
   `deepctl-*` floor must equal that package's current workspace version.

2. **Sub-package floors stay satisfiable.** Sub-package floors are API
   contracts (e.g. deepctl-cmd-keys needs the deepctl-core that provides
   `get_status_console`), hand-raised when a package starts using a newer
   sibling API. They must never exceed the sibling's current version.
   Keeping them *accurate* is still on the developer: raise the floor in the
   same PR that starts importing the new API.

Run with --fix to rewrite root floors in place (used by the release
workflow's sync job so rule 1 holds automatically on every release PR).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        print("Python 3.11+ required (tomllib), or install tomli: pip install tomli")
        sys.exit(1)

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / ".github" / ".release-please-manifest.json"


def workspace_versions() -> dict[str, str]:
    """Map package name -> current workspace version, per the manifest."""
    versions: dict[str, str] = {}
    for path in json.loads(MANIFEST.read_text()):
        pyproject = REPO / (
            "pyproject.toml" if path == "." else f"{path}/pyproject.toml"
        )
        project = tomllib.loads(pyproject.read_text())["project"]
        versions[project["name"]] = project["version"]
    return versions


def floors(pyproject: Path) -> list[tuple[str, str, str]]:
    """Yield (name, floor, raw-spec) for each intra-workspace dependency."""
    deps = tomllib.loads(pyproject.read_text())["project"].get("dependencies", [])
    out = []
    for dep in deps:
        m = re.match(r"^(deepctl[\w-]*)>=([0-9][0-9.]*)", dep)
        if m:
            out.append((m.group(1), m.group(2), dep))
    return out


def vkey(version: str) -> list[int]:
    return [int(part) for part in version.split(".")]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fix",
        action="store_true",
        help="rewrite root pyproject.toml floors to the workspace versions",
    )
    args = parser.parse_args()

    versions = workspace_versions()
    problems: list[str] = []

    # Rule 1: root floors == workspace versions.
    root = REPO / "pyproject.toml"
    root_text = root.read_text()
    fixed = root_text
    for name, floor, _ in floors(root):
        current = versions.get(name)
        if current is None:
            problems.append(f"root depends on {name}, which is not in the manifest")
            continue
        if floor != current:
            if args.fix:
                fixed = fixed.replace(f'"{name}>={floor}"', f'"{name}>={current}"', 1)
            else:
                problems.append(
                    f"root floor {name}>={floor} != workspace version {current}"
                    " (published fixes will not be delivered by pip upgrades)"
                )
    if args.fix and fixed != root_text:
        root.write_text(fixed)
        print(f"fixed: root floors pinned to workspace versions in {root}")

    # Rule 2: every sub-package floor must be satisfiable at co-release.
    for pkg_dir in sorted((REPO / "packages").iterdir()):
        pyproject = pkg_dir / "pyproject.toml"
        if not pyproject.is_file():
            continue
        for name, floor, _ in floors(pyproject):
            current = versions.get(name)
            if current is None:
                problems.append(
                    f"{pkg_dir.name} depends on {name}, which is not in the manifest"
                )
            elif vkey(floor) > vkey(current):
                problems.append(
                    f"{pkg_dir.name}: floor {name}>={floor} exceeds"
                    f" workspace version {current} (unsatisfiable)"
                )

    if problems:
        print("dependency floor check FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "\nRun `python3 scripts/check_dependency_floors.py --fix` to pin"
            " root floors; sub-package floors are hand-maintained.",
            file=sys.stderr,
        )
        return 1

    print("dependency floors OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
