#!/usr/bin/env python3
"""Check (or fix) lockstep versions across the first-party packages.

Every first-party distribution -- the root `deepctl` plus every package under
`packages/` except `deepctl-plugin-example` -- ships at one version, V, the
root's `project.version`. The check fails when any of these is not true:

1. **One version.** Every lockstep package's `project.version` is V, and so is
   every `__version__` line carrying an `x-release-please-version` marker.
   The release-please manifest has a single lockstep entry, `"."`, at V.

2. **Exact pins.** Every dependency on a first-party package is exactly
   `name==V` (no `>=`, no range, no extras, no environment marker), and its
   line carries the `x-release-please-version` marker so release-please moves
   it with the version. A `>=` floor is a failure even when it equals V: a
   floor lets pip pair a new package with an old sibling, which is what the
   `==` pins exist to prevent, and a floor that is too low is the same bug.

3. **Root lists every package.** `pip install --upgrade deepctl` (what
   `dg update` runs) only installs what root depends on.

4. **The back-edge.** Every lockstep sibling declares `deepctl==V`. Without
   it, `pip install -U deepctl-cmd-listen` upgrades that one package and
   leaves the root and the other siblings behind; with it, the resolver moves
   the whole set or refuses.

5. **Workspace sources.** Every first-party dependency is resolved from the
   workspace (`[tool.uv.sources] name = { workspace = true }`), so `uv lock`
   never pins a lockstep sibling to a PyPI release.

The plugin example is the third-party fixture and versions separately. Its
first-party requirements must only be satisfiable at V.

Run with --fix to rewrite every version, pin, back-edge, manifest entry and
workspace source in place. The release workflow's fix-up job runs it on the
release PR after release-please bumps the versions, then `uv lock`, then the
check again.

Versions are compared with `packaging.version.Version`, so a pre-release
(1.0.0rc1) behaves like any other version.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - only taken on Python 3.10
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        print(
            "Python 3.11+ required (tomllib), or install tomli: pip install tomli",
            file=sys.stderr,
        )
        sys.exit(1)

from typing import TYPE_CHECKING

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

if TYPE_CHECKING:
    from collections.abc import Iterator

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / ".github" / ".release-please-manifest.json"

ROOT_NAME = "deepctl"
#: Packages under packages/ that version on their own. The plugin example is
#: the third-party fixture: it must keep working against a released deepctl
#: the way an outside plugin would, so it is not part of the lockstep set.
NOT_LOCKSTEP = {"deepctl-plugin-example"}
MARKER = "# x-release-please-version"

_VERSION_LINE = re.compile(r'^(?P<key>version\s*=\s*)"(?P<version>[^"]*)"(?P<rest>.*)$')
_DUNDER_VERSION = re.compile(
    r'^(?P<head>__version__\s*=\s*)"(?P<version>[^"]*)"(?P<rest>.*)$'
)
_DEP_LINE = re.compile(r'^(?P<indent>\s*)"(?P<req>[^"]*)"\s*,?\s*(?P<comment>#.*)?$')


@dataclass
class Project:
    """One pyproject.toml, plus enough of its text to rewrite it."""

    path: Path
    name: str
    version: str
    lines: list[str]
    deps_start: int  # index of the `dependencies = [` line, or -1
    deps_end: int  # index of the closing `]`, or -1
    version_line: int  # index of [project]'s `version = ...` line, or -1
    data: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, object] = field(default_factory=dict)

    @property
    def rel(self) -> str:
        return self.path.relative_to(REPO).as_posix()

    def dep_lines(self) -> Iterator[tuple[int, Requirement | None, str]]:
        """Yield (line index, parsed requirement, raw text) inside the array."""
        for i in range(self.deps_start + 1, self.deps_end):
            m = _DEP_LINE.match(self.lines[i])
            if not m:
                continue
            raw = m.group("req")
            try:
                yield i, Requirement(raw), raw
            except InvalidRequirement:
                yield i, None, raw


def load(path: Path) -> Project:
    text = path.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    project = data.get("project", {})
    lines = text.splitlines(keepends=True)
    start = end = version_line = -1
    in_project = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and not (start >= 0 and end < 0):
            in_project = stripped == "[project]"
            continue
        if in_project and version_line < 0 and _VERSION_LINE.match(line):
            version_line = i
            continue
        if in_project and start < 0 and re.match(r"^dependencies\s*=\s*\[\s*$", line):
            start = i
            continue
        if start >= 0 and end < 0 and stripped == "]":
            end = i
    return Project(
        path=path,
        name=canonicalize_name(project.get("name", "")),
        version=str(project.get("version", "")),
        lines=lines,
        deps_start=start,
        deps_end=end,
        version_line=version_line,
        data=data,
        sources=data.get("tool", {}).get("uv", {}).get("sources", {}),
    )


def _first_party(reqs: list[str]) -> set[str]:
    """Canonical names of the deepctl* requirements in a TOML list."""
    out: set[str] = set()
    for raw in reqs:
        try:
            name = canonicalize_name(Requirement(raw).name)
        except InvalidRequirement:
            name = canonicalize_name(
                re.split(r"[^A-Za-z0-9._-]", raw.strip(), maxsplit=1)[0]
            )
        if name.startswith("deepctl"):
            out.add(name)
    return out


def discover() -> tuple[Project, list[Project], list[Project]]:
    """Return (root, lockstep siblings, separately versioned packages)."""
    root = load(REPO / "pyproject.toml")
    siblings: list[Project] = []
    others: list[Project] = []
    for pkg_dir in sorted((REPO / "packages").iterdir()):
        pyproject = pkg_dir / "pyproject.toml"
        if not pyproject.is_file():
            continue
        project = load(pyproject)
        (others if project.name in NOT_LOCKSTEP else siblings).append(project)
    return root, siblings, others


def parse(version: str) -> Version | None:
    try:
        return Version(version)
    except InvalidVersion:
        return None


def version_files(siblings: list[Project]) -> list[Path]:
    """`__init__.py` files whose `__version__` release-please also bumps."""
    candidates = [REPO / "src" / "deepctl" / "__init__.py"]
    for project in siblings:
        candidates.extend(sorted((project.path.parent / "src").glob("*/__init__.py")))
    return [p for p in candidates if p.is_file() and MARKER in p.read_text("utf-8")]


class Checker:
    def __init__(self, fix: bool) -> None:
        self.fix = fix
        self.problems: list[str] = []
        self.fixed: list[str] = []
        self.version = ""
        self.count = 0

    def problem(self, where: str, message: str) -> None:
        self.problems.append(f"{where}: {message}")

    # -- pyproject files -------------------------------------------------

    def check_project(
        self,
        project: Project,
        v: str,
        lockstep: set[str],
        required: list[str],
    ) -> None:
        """Version line, every first-party pin, required deps, sources."""
        changed = False
        where = project.rel

        # [project]'s version line, with its marker. Compared as a string:
        # release-please rewrites the literal X.Y.Z, so `0.5` != `0.5.0` here.
        i = project.version_line
        m = _VERSION_LINE.match(project.lines[i]) if i >= 0 else None
        if m is None:
            self.problem(where, '[project] has no `version = "..."` line')
        elif m.group("version") != v or MARKER not in project.lines[i]:
            if self.fix:
                project.lines[i] = f'{m.group("key")}"{v}" {MARKER}\n'
                changed = True
            else:
                self.problem(
                    where,
                    f"version {m.group('version')!r} must be {v!r} with the"
                    f" `{MARKER}` marker (lockstep)",
                )

        if project.deps_start < 0 or project.deps_end < 0:
            if required:
                self.problem(
                    where,
                    "[project] dependencies must be a multi-line array so the"
                    " lockstep pins can be checked and fixed",
                )
            return

        seen: set[str] = set()
        for i, req, raw in project.dep_lines():
            if req is None:
                if raw.strip().lower().startswith("deepctl"):
                    self.problem(where, f"cannot parse requirement {raw!r}")
                continue
            name = canonicalize_name(req.name)
            if not name.startswith("deepctl"):
                continue
            if name not in lockstep:
                self.problem(
                    where,
                    f"depends on {name}, which is not a lockstep package"
                    " (first-party dependencies must be one of the"
                    f" {len(lockstep)} lockstep packages)",
                )
                continue
            seen.add(name)
            want = f"{name}=={v}"
            exact = (
                len(req.specifier) == 1
                and next(iter(req.specifier)).operator == "=="
                and next(iter(req.specifier)).version == v
                and not req.extras
                and req.marker is None
            )
            marked = MARKER in project.lines[i]
            if exact and marked:
                continue
            if self.fix:
                indent = _DEP_LINE.match(project.lines[i]).group("indent")  # type: ignore[union-attr]
                project.lines[i] = f'{indent}"{want}", {MARKER}\n'
                changed = True
            elif not exact:
                self.problem(
                    where,
                    f"{raw!r} must be exactly {want!r}"
                    " (lockstep: a floor or range lets pip mix versions)",
                )
            else:
                self.problem(
                    where,
                    f"the {raw!r} line is missing the `{MARKER}` marker"
                    " (release-please would leave the pin behind)",
                )

        # Anything first-party the TOML holds but the line scan did not see
        # (single quotes, two requirements on a line, an inline table...) is
        # invisible to the pin check and to release-please: fail closed.
        for stray in sorted(
            _first_party(project.data["project"].get("dependencies", [])) - seen
        ):
            self.problem(
                where,
                f"{stray}: a first-party requirement must be on its own"
                ' double-quoted line in [project] dependencies (`"name==V",`)',
            )
        extra_tables = {
            f"[project.optional-dependencies] {k}": v_
            for k, v_ in project.data["project"]
            .get("optional-dependencies", {})
            .items()
        }
        extra_tables.update(
            {
                f"[dependency-groups] {k}": [x for x in v_ if isinstance(x, str)]
                for k, v_ in project.data.get("dependency-groups", {}).items()
            }
        )
        for table, reqs in extra_tables.items():
            for extra in sorted(_first_party(reqs) & lockstep):
                self.problem(
                    where,
                    f"{table} requires the lockstep package {extra}; lockstep"
                    " pins belong in [project] dependencies only",
                )

        missing = [name for name in required if name not in seen]
        for dep_name in missing:
            if self.fix:
                insert_at = project.deps_start + 1
                project.lines.insert(insert_at, f'    "{dep_name}=={v}", {MARKER}\n')
                project.deps_end += 1
                changed = True
            elif dep_name == ROOT_NAME:
                self.problem(
                    where,
                    f"missing the back-edge {ROOT_NAME}=={v} (without it,"
                    f" `pip install -U {project.name}` upgrades only that"
                    " package and leaves the CLI behind)",
                )
            else:
                self.problem(
                    where,
                    f"missing {dep_name}=={v} (root is the delivery manifest:"
                    " `pip install -U deepctl` never installs a package root"
                    " does not list)",
                )

        # Workspace sources for every first-party dependency.
        for source_name in sorted(seen | set(missing)):
            source = project.sources.get(source_name)
            if isinstance(source, dict) and source.get("workspace") is True:
                continue
            if self.fix:
                self._add_source(project, source_name)
                changed = True
            else:
                self.problem(
                    where,
                    f"[tool.uv.sources] has no `{source_name} = {{ workspace = true }}`"
                    " (uv would resolve the sibling from PyPI)",
                )

        if changed:
            project.path.write_text("".join(project.lines), encoding="utf-8")
            self.fixed.append(where)

    @staticmethod
    def _add_source(project: Project, name: str) -> None:
        entry = f"{name} = {{ workspace = true }}\n"
        lines = project.lines
        header = next(
            (i for i, line in enumerate(lines) if line.strip() == "[tool.uv.sources]"),
            None,
        )
        if header is None:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.extend(["\n", "[tool.uv.sources]\n", entry])
            return
        # Drop a non-workspace entry for the same name, then append after the
        # last key of the table.
        end = header + 1
        while end < len(lines) and not lines[end].lstrip().startswith("["):
            end += 1
        for i in range(header + 1, end):
            if re.match(rf"^\s*{re.escape(name)}\s*=", lines[i]):
                lines[i] = entry
                return
        insert_at = end
        while insert_at > header + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines.insert(insert_at, entry)

    # -- the separately versioned packages --------------------------------

    def check_independent(self, project: Project, v: str) -> None:
        """The plugin example's first-party requirements accept V."""
        if project.deps_start < 0:
            return
        for _, req, raw in project.dep_lines():
            if req is None or not canonicalize_name(req.name).startswith("deepctl"):
                continue
            if not req.specifier.contains(v, prereleases=True):
                self.problem(
                    project.rel,
                    f"{raw!r} does not accept the lockstep version {v} (unsatisfiable)",
                )

    # -- __version__ lines and the manifest -------------------------------

    def check_version_file(self, path: Path, v: str) -> None:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        changed = False
        for i, line in enumerate(lines):
            m = _DUNDER_VERSION.match(line)
            if not m or MARKER not in line:
                continue
            if m.group("version") == v:
                continue
            if self.fix:
                lines[i] = f'{m.group("head")}"{v}"{m.group("rest")}\n'
                changed = True
            else:
                self.problem(
                    path.relative_to(REPO).as_posix(),
                    f"__version__ {m.group('version')!r} must be {v!r}",
                )
        if changed:
            path.write_text("".join(lines), encoding="utf-8")
            self.fixed.append(path.relative_to(REPO).as_posix())

    def check_manifest(self, v: str, others: list[Project]) -> None:
        where = MANIFEST.relative_to(REPO).as_posix()
        manifest: dict[str, str] = json.loads(MANIFEST.read_text(encoding="utf-8"))
        allowed = {
            project.path.parent.relative_to(REPO).as_posix() for project in others
        }
        want = {".": v}
        want.update({k: val for k, val in manifest.items() if k in allowed})
        if manifest == want:
            return
        if self.fix:
            MANIFEST.write_text(json.dumps(want, indent=2) + "\n", encoding="utf-8")
            self.fixed.append(where)
            return
        if manifest.get(".") != v:
            self.problem(where, f'"." is {manifest.get(".")!r}, must be {v!r}')
        for key in sorted(set(manifest) - {"."} - allowed):
            self.problem(
                where,
                f"{key!r} is a lockstep package and must not have its own entry"
                ' (one release-please component, ".")',
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="rewrite every version, pin, back-edge and source to the root version",
    )
    args = parser.parse_args()

    if args.fix:
        fixer = run(fix=True)
        for where in fixer.fixed:
            print(f"fixed: {where}")
    # Always finish with a plain check of what is on disk, so `--fix` can
    # never report success on a tree the next check would reject.
    checker = run(fix=False)

    if checker.problems:
        print("lockstep check FAILED:", file=sys.stderr)
        for problem in checker.problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "\nRun `make floors-fix` to set every version and pin to the root"
            " version. Anything listed after a fix needs a hand edit.",
            file=sys.stderr,
        )
        return 1

    print(f"lockstep OK: {checker.count} packages at {checker.version}")
    return 0


def run(fix: bool) -> Checker:
    root, siblings, others = discover()
    v = root.version
    checker = Checker(fix=fix)
    checker.version = v
    checker.count = len(siblings) + 1
    if parse(v) is None:
        checker.problem(
            "pyproject.toml", f"version {v!r} is not a valid PEP 440 version"
        )
        return checker

    lockstep = {ROOT_NAME, *(project.name for project in siblings)}
    sibling_names = sorted(project.name for project in siblings)
    checker.check_project(root, v, lockstep, required=sibling_names)
    for project in siblings:
        checker.check_project(project, v, lockstep, required=[ROOT_NAME])
    for project in others:
        checker.check_independent(project, v)
    for path in version_files(siblings):
        checker.check_version_file(path, v)
    checker.check_manifest(v, others)
    return checker


if __name__ == "__main__":
    sys.exit(main())
