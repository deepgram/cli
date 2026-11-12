"""Check built wheels for lockstep versions, with pkginfo.

Usage: python check_wheels.py DIST_DIR VERSION [EXAMPLE_VERSION]

DIST_DIR holds the output of `uv build --all-packages --wheel`: the 30
lockstep dists plus the plugin example. Every lockstep wheel's Version must be
VERSION and every Requires-Dist on a first-party dist must be exactly
`==VERSION`. Every sibling requires `deepctl==VERSION`, and the root requires
all 29 siblings. The plugin example keeps its own version and its `>=` floor.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from pkginfo import Wheel

dist_dir, version = Path(sys.argv[1]), sys.argv[2]
example_version = sys.argv[3] if len(sys.argv) > 3 else None
EXAMPLE = "deepctl-plugin-example"
fails = 0


def check(ok: bool, msg: str) -> None:
    global fails
    print(("PASS " if ok else "FAIL ") + msg)
    fails += not ok


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


wheels = {}
for path in sorted(dist_dir.glob("*.whl")):
    w = Wheel(str(path))
    wheels[norm(w.name)] = w
lockstep = {n: w for n, w in wheels.items() if n != EXAMPLE}
check(len(wheels) == 31, f"31 wheels ({len(wheels)})")
check(len(lockstep) == 30 and "deepctl" in lockstep, f"30 lockstep dists incl. root ({len(lockstep)})")
check(
    all(w.version == version for w in lockstep.values()),
    f"every lockstep Version == {version}"
    f" (off: {sorted((n, w.version) for n, w in lockstep.items() if w.version != version)})",
)

req = re.compile(r"^\s*(?P<name>deepctl[A-Za-z0-9._-]*)\s*(?P<spec>[^;]*)(?P<marker>;.*)?$")
edges = 0
for name, w in sorted(lockstep.items()):
    first_party = {}
    for r in w.requires_dist:
        m = req.match(r)
        if not m:
            continue
        edges += 1
        first_party[norm(m.group("name"))] = (m.group("spec").replace(" ", ""), m.group("marker"))
    bad = {
        dep: spec
        for dep, (spec, marker) in first_party.items()
        if spec != f"=={version}" or marker or dep not in lockstep
    }
    check(not bad, f"{name}: every first-party Requires-Dist =={version} (bad: {bad})")
    if name == "deepctl":
        check(
            set(first_party) == set(lockstep) - {"deepctl"},
            f"root requires all 29 siblings ({len(first_party)})",
        )
    else:
        check("deepctl" in first_party, f"{name}: back-edge deepctl=={version}")
check(edges >= 29 + 29, f"{edges} first-party edges")

ex = wheels.get(EXAMPLE)
check(ex is not None, "plugin example built")
if ex is not None:
    if example_version:
        check(ex.version == example_version, f"plugin example version {ex.version} untouched")
    ex_fp = [r for r in ex.requires_dist if r.startswith("deepctl")]
    check(
        all(">=" in r and "==" not in r for r in ex_fp),
        f"plugin example keeps its floor: {ex_fp}",
    )
print(f"check_wheels: {fails} failures")
sys.exit(1 if fails else 0)
