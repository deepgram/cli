"""Static wheel checks (no install): pkginfo specs, two-owner paths, package-data superset.
Usage: python -I static_checks.py WHEELHOUSE W050 W100RC1 W100"""
import re
import sys
import zipfile
from email.parser import Parser
from pathlib import Path

wh, w050, wrc, w100 = map(Path, sys.argv[1:5])
fail = 0


def meta(whl: Path):
    with zipfile.ZipFile(whl) as z:
        name = next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))
        m = Parser().parsestr(z.read(name).decode())
        files = [n for n in z.namelist() if ".dist-info/" not in n]
    return m["Name"], m["Version"], m.get_all("Requires-Dist") or [], files


def check(cond, msg):
    global fail
    print(("PASS " if cond else "FAIL ") + msg)
    fail += not cond


def norm(n):
    return re.sub(r"[-_.]+", "-", n).lower()


# 1. 0.5.0 lockstep pins
dists = [meta(w) for w in sorted(w050.glob("*.whl"))]
check(len(dists) == 30 and all(v == "0.5.0" for _, v, _, _ in dists), f"0.5.0: 30 dists all at 0.5.0 ({len(dists)})")
fp = [(n, r) for n, _, rs, _ in dists for r in rs if r.startswith("deepctl")]
bad = [x for x in fp if not re.fullmatch(r"deepctl[-a-z]*==0\.5\.0", x[1].replace(" ", ""))]
check(not bad, f"0.5.0: every first-party Requires-Dist is ==0.5.0 ({len(fp)} edges, bad={bad[:3]})")

# 2. relocated specs
for wd, V, tomb in ((wrc, "1.0.0rc1", "deepctl>=1.0.0rc1"), (w100, "1.0.0", "deepctl>=1.0")):
    ds = {norm(n): (v, rs, fs) for n, v, rs, fs in (meta(w) for w in wd.glob("*.whl"))}
    tombs = {n: x for n, x in ds.items() if n not in ("deepctl", "deepctl-core")}
    check(len(tombs) == 28, f"{V}: 28 tombstones ({len(tombs)})")
    check(all(x[1] == [tomb] and x[2] == [] for x in tombs.values()),
          f"{V}: every tombstone Requires-Dist exactly [{tomb}] and ships no files")
    root_rs = [r.replace(" ", "") for r in ds["deepctl"][1]]
    want_t = {f"{t}{tomb[7:]}" for t in tombs}
    check(want_t <= set(root_rs), f"{V}: root lists all 28 tombstones with {tomb[7:]}")
    check(f"deepctl-core=={V}" in root_rs, f"{V}: root pins deepctl-core=={V}")
    check(ds["deepctl-core"][1][0].replace(" ", "") == f"deepctl=={V}", f"{V}: core back-edge deepctl=={V}")

# 3. two-owner paths: a path a 1.0 dist ships must not be shipped by a DIFFERENT dist in any 0.x wheel
old = {}
for w in wh.glob("deepctl*.whl"):
    n, v, _, fs = meta(w)
    for f in fs:
        old.setdefault(f, set()).add(norm(n))
for wd, V in ((wrc, "1.0.0rc1"), (w100, "1.0.0"), (w050, "0.5.0")):
    clashes = []
    for w in wd.glob("*.whl"):
        n, _, _, fs = meta(w)
        for f in fs:
            others = old.get(f, set()) - {norm(n)}
            if others:
                clashes.append((norm(n), f, sorted(others)))
    check(not clashes, f"{V}: no path owned by a different dist in 0.3.3/0.4.0 (clashes={clashes[:3]})")

# 4. Union of 0.x non-.py package data is present (relocated) in the 1.0 root wheel
def reloc(path: str) -> str:
    top, _, rest = path.partition("/")
    if top == "deepctl_cmd_mcp":
        return "deepctl/mcp/" + rest
    if top == "deepctl_shared_utils":
        return "deepctl/_shared/" + rest
    if top == "deepctl_telemetry":
        return "deepctl/_telemetry/" + rest
    if top.startswith("deepctl_cmd_"):
        return "deepctl/_commands/" + top.removeprefix("deepctl_cmd_") + "/" + rest
    return path
for wd, V in ((wrc, "1.0.0rc1"), (w100, "1.0.0")):
    new = set()
    for w in wd.glob("*.whl"):
        new |= set(meta(w)[3])
    need = {reloc(f) for f in old if not f.endswith(".py")}
    missing = sorted(need - new)
    check(not missing, f"{V}: 0.x package data superset ({len(need)} non-.py files; missing={missing[:5]})")
    needpy = {reloc(f) for f in old if f.endswith(".py")}
    gone = sorted(needpy - new)
    print(f"INFO {V}: {len(gone)} 0.x .py modules with no 1.0 counterpart (expected: removed-in-1.0 modules only): {gone[:8]}")
print(f"static checks: {fail} failures")
sys.exit(1 if fail else 0)
