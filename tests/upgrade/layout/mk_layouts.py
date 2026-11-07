#!/usr/bin/env python3
"""Derive release layouts from a clean export of this tree, for the upgrade matrix.

Usage: mk_layouts.py SRC OUT MODE VERSION
  MODE = lockstep   -> the current layout: 30 dists (root plus 29 packages, the
                       plugin example excluded) at one version. Sets the root
                       version and runs scripts/check_dependency_floors.py --fix,
                       which is what the release fix-up does.
  MODE = relocated  -> the planned 1.0 layout: `deepctl` carries all first-party
                       code under deepctl/_commands/<x>/, deepctl/_shared/,
                       deepctl/_telemetry/, deepctl/mcp/; `deepctl-core` stays a
                       real dist (deepctl_core import path) and declares
                       `deepctl==VERSION`; the other 28 packages become file-less
                       tombstones declaring `deepctl>=1.0` (final) or
                       `deepctl>=1.0.0rc1` (prerelease). Root lists core
                       `==VERSION` and all 28 tombstones with the same spec.
SRC is never modified; OUT must not exist. Prints the list of project dirs to build.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

SRC, OUT, MODE, VERSION = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
EXCLUDE = {"deepctl-plugin-example"}
FIRST_PARTY_DEP = re.compile(r'"(deepctl(?:-[a-z0-9-]+)?)\s*(?:[<>=~!]=?[^";]*)?"')
FIRST_PARTY_REQ = re.compile(r"^deepctl(?:-[a-z0-9-]+)?\s*(?:[<>=~!;\[]|$)")
VERSION_LINE = re.compile(r'^version = "[^"]*"', re.M)


def siblings() -> list[Path]:
    return sorted(p for p in (OUT / "packages").iterdir()
                  if p.is_dir() and p.name not in EXCLUDE and (p / "pyproject.toml").exists())


def set_version(text: str) -> str:
    return VERSION_LINE.sub(f'version = "{VERSION}"', text, count=1)


def pin_first_party(text: str, spec_for) -> str:
    def repl(m: re.Match[str]) -> str:
        name = m.group(1)
        if name in EXCLUDE:
            return m.group(0)
        return f'"{name}{spec_for(name)}"'
    # only rewrite inside dependency arrays: lines that are a bare quoted requirement
    out = []
    for line in text.splitlines(keepends=True):
        s = line.split("#", 1)[0].strip()
        if s.startswith('"deepctl') and s.rstrip(",").endswith('"'):
            line = FIRST_PARTY_DEP.sub(repl, line)
        out.append(line)
    return "".join(out)


def lockstep() -> list[Path]:
    """Set the root version, then let the lockstep check rewrite everything else."""
    f = OUT / "pyproject.toml"
    f.write_text(set_version(f.read_text()))
    subprocess.run([sys.executable, "scripts/check_dependency_floors.py", "--fix"], cwd=OUT, check=True,
                   stdout=subprocess.DEVNULL)
    return [OUT, *siblings()]


def new_module(dist: str) -> str:
    """Old import package -> 1.0 import path."""
    if dist == "deepctl-cmd-mcp":
        return "deepctl.mcp"
    if dist == "deepctl-shared-utils":
        return "deepctl._shared"
    if dist == "deepctl-telemetry":
        return "deepctl._telemetry"
    assert dist.startswith("deepctl-cmd-"), dist
    return "deepctl._commands." + dist.removeprefix("deepctl-cmd-").replace("-", "_")


def relocated() -> list[Path]:
    is_pre = bool(re.search(r"(a|b|rc|\.dev)\d+$", VERSION))
    tomb_spec = ">=1.0.0rc1" if is_pre else ">=1.0"
    sibs = siblings()
    core = OUT / "packages" / "deepctl-core"
    movers = [p for p in sibs if p != core]
    assert len(movers) == 28, len(movers)

    # old import name -> new dotted path
    mapping: dict[str, str] = {}
    for p in movers:
        old = p.name.replace("-", "_")
        assert (p / "src" / old).is_dir(), p
        mapping[old] = new_module(p.name)

    root_pkg = OUT / "src" / "deepctl"
    (root_pkg / "_commands").mkdir()
    (root_pkg / "_commands" / "__init__.py").write_text('"""First-party commands."""\n')

    third_party: list[str] = []
    entry_points: dict[str, dict[str, str]] = {}
    package_data: dict[str, list[str]] = {}
    for p in movers:
        meta = tomllib.loads((p / "pyproject.toml").read_text())
        proj = meta["project"]
        old = p.name.replace("-", "_")
        dest = root_pkg.joinpath(*mapping[old].split(".")[1:])
        shutil.copytree(p / "src" / old, dest, ignore=shutil.ignore_patterns("__pycache__"))
        for d in proj.get("dependencies", []):
            if not FIRST_PARTY_REQ.match(d):
                third_party.append(d)
        for grp, eps in proj.get("entry-points", {}).items():
            for k, v in eps.items():
                entry_points.setdefault(grp, {})[k] = v
        pd = meta.get("tool", {}).get("setuptools", {}).get("package-data", {})
        for k, v in pd.items():
            package_data[mapping.get(k, k)] = v
        assert not proj.get("scripts"), f"{p.name} has console scripts"

    # rewrite imports + string module refs across the whole root package
    pat = re.compile(r"\b(" + "|".join(sorted(mapping, key=len, reverse=True)) + r")\b")
    rewrites = 0
    for f in root_pkg.rglob("*.py"):
        t = f.read_text()
        n, c = pat.subn(lambda m: mapping[m.group(1)], t)
        if c:
            f.write_text(n)
            rewrites += c
    for grp in entry_points:
        for k, v in entry_points[grp].items():
            entry_points[grp][k] = pat.sub(lambda m: mapping[m.group(1)], v)

    # root pyproject: keep root metadata, swap deps/entry-points/packaging
    root_t = (OUT / "pyproject.toml").read_text()
    root_meta = tomllib.loads(root_t)["project"]
    root_third = [d for d in root_meta["dependencies"] if not FIRST_PARTY_REQ.match(d)]
    deps = [f"deepctl-core=={VERSION}"] + [f"{p.name}{tomb_spec}" for p in movers]
    seen: set[str] = set()
    for d in root_third + third_party:
        if d not in seen:
            seen.add(d)
            deps.append(d)
    ep_toml = "".join(
        f'\n[project.entry-points."{g}"]\n' + "".join(f'{k} = "{v}"\n' for k, v in sorted(eps.items()))
        for g, eps in sorted(entry_points.items())
    )
    pd_toml = "\n[tool.setuptools.package-data]\n" + "".join(
        f'"{k}" = {json.dumps(v)}\n' for k, v in package_data.items()) if package_data else ""
    root_new = f'''[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "deepctl"
version = "{VERSION}"
description = {json.dumps(root_meta["description"])}
readme = "README.md"
license = "MIT"
requires-python = ">=3.10"
dependencies = [
{"".join(f'    "{d}",{chr(10)}' for d in deps)}]

[project.scripts]
deepctl = "deepctl.main:main"
deepgram = "deepctl.main:main"
dg = "deepctl.main:main"
{ep_toml}
[tool.setuptools]
package-dir = {{ "" = "src" }}

[tool.setuptools.packages.find]
where = ["src"]
include = ["deepctl", "deepctl.*"]
{pd_toml}'''
    (OUT / "pyproject.toml").write_text(root_new)

    # deepctl-core: a real dist, same import path, back-edge deepctl==VERSION
    cf = core / "pyproject.toml"
    ct = pin_first_party(set_version(cf.read_text()), lambda n: f"=={VERSION}")
    if f'"deepctl=={VERSION}"' not in ct:
        ct = ct.replace('dependencies = [\n', f'dependencies = [\n    "deepctl=={VERSION}",\n', 1)
    cf.write_text(ct)

    # 28 tombstones: no files, Requires-Dist deepctl{tomb_spec}
    for p in movers:
        name = p.name
        shutil.rmtree(p)
        p.mkdir()
        (p / "pyproject.toml").write_text(f'''[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{name}"
version = "{VERSION}"
description = "Tombstone: this command now ships inside the deepctl package."
requires-python = ">=3.10"
dependencies = ["deepctl{tomb_spec}"]

[tool.setuptools]
packages = []
py-modules = []
''')
    print(f"# relocated: {len(mapping)} packages moved, {rewrites} identifier rewrites, "
          f"{sum(len(v) for v in entry_points.values())} entry points, tomb spec {tomb_spec}", file=sys.stderr)
    return [OUT, core, *movers]


if __name__ == "__main__":
    assert not OUT.exists(), OUT
    shutil.copytree(SRC, OUT, ignore=shutil.ignore_patterns("__pycache__", ".git", "web", "tests", "docs"))
    projs = lockstep() if MODE == "lockstep" else relocated()
    for p in projs:
        print(p)
