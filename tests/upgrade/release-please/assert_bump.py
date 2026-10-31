"""Assert what a release-please run changed in a lockstep tree.

Usage: python3 assert_bump.py REPO OLD NEW

REPO is a git repository whose HEAD is the tree before the release PR and
whose working tree holds the release PR's changes. Every lockstep version and
`==` pin must move from OLD to NEW, the plugin example must be untouched, and
nothing else may change (the root CHANGELOG.md excepted).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

repo, old, new = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
EXAMPLE = "packages/deepctl-plugin-example"
fails = 0


def check(ok: bool, msg: str) -> None:
    global fails
    print(("PASS " if ok else "FAIL ") + msg)
    fails += not ok


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


siblings = sorted(
    p.parent.relative_to(repo).as_posix()
    for p in (repo / "packages").glob("*/pyproject.toml")
    if p.parent.name != "deepctl-plugin-example"
)
check(len(siblings) == 29, f"29 lockstep siblings ({len(siblings)})")
version_files = [
    p.relative_to(repo).as_posix()
    for p in [repo / "src/deepctl/__init__.py", *repo.glob("packages/*/src/*/__init__.py")]
    if "x-release-please-version" in p.read_text() and EXAMPLE not in p.as_posix()
]
allowed = {
    "pyproject.toml",
    "CHANGELOG.md",
    ".github/.release-please-manifest.json",
    *(f"{s}/pyproject.toml" for s in siblings),
    *version_files,
}
changed = set(git("diff", "--name-only").split())
check(not (changed - allowed), f"only lockstep files changed (unexpected: {sorted(changed - allowed)})")
check(
    allowed - {"CHANGELOG.md"} <= changed,
    f"every lockstep file changed (missing: {sorted(allowed - {'CHANGELOG.md'} - changed)})",
)
check(not any(c.startswith(EXAMPLE) for c in changed), "plugin example untouched")

# Every changed line is the same line with OLD replaced by NEW.
for f in sorted(changed - {"CHANGELOG.md"}):
    before = git("show", f"HEAD:{f}").splitlines()
    after = (repo / f).read_text().splitlines()
    same_shape = len(before) == len(after)
    stray = [
        (b, a)
        for b, a in zip(before, after)
        if b != a and a.replace(new, old) != b
    ]
    check(same_shape and not stray, f"{f}: only {old}->{new} substitutions {stray[:2]}")

# Every lockstep version and first-party pin is NEW.
req = re.compile(r"^(deepctl[a-z0-9-]*)\s*(.*)$")
pins = 0
for f in ["pyproject.toml", *(f"{s}/pyproject.toml" for s in siblings)]:
    project = tomllib.loads((repo / f).read_text())["project"]
    check(project["version"] == new, f"{f}: version {project['version']}")
    for dep in project.get("dependencies", []):
        m = req.match(dep)
        if m:
            pins += 1
            if m.group(2) != f"=={new}":
                check(False, f"{f}: pin {dep}")
check(pins >= 29 + 29, f"{pins} first-party pins all =={new}")
for f in version_files:
    check(f'__version__ = "{new}"' in (repo / f).read_text(), f"{f}: __version__ {new}")
manifest = json.loads((repo / ".github/.release-please-manifest.json").read_text())
check(manifest.get(".") == new, f'manifest "." = {manifest.get(".")}')
check(set(manifest) == {".", EXAMPLE}, f"manifest keys {sorted(manifest)}")
print(f"assert_bump: {fails} failures")
sys.exit(1 if fails else 0)
