#!/usr/bin/env python3
"""Fail if a package's code changed since its last release without a version bump.

The dependency floors (scripts/check_dependency_floors.py) compare against
*workspace* versions, so they are only right when release-please bumps
every package whose code changed. It does not always: a `chore:` or
`refactor:` commit under a package moves its code without a release. PyPI
then keeps serving the old code under the same version number, and a
sibling that needs the new code installs next to it and fails at import.
On this branch, a clean `pip install` of the root wheel resolved
deepctl-core 0.3.0 from PyPI, which has no `deepctl_core._launcher`.

So before anything is published: for every package in the release-please
manifest whose current version already has a release tag, `src/` and the
package's dependencies must be unchanged since that tag. A version with no
tag yet is about to be released from this tree, which is fine.

Dependencies are compared parsed, not as text, so the release PR's own
version edit does not count. Floors on other workspace packages
(`deepctl-*>=X`) are left out of the comparison: the release sync job raises
them mechanically in packages it is not bumping, and
check_dependency_floors.py already governs them. Adding, removing or
re-pinning any other dependency without a bump is caught.

Tag names follow .github/release-please-config.json: `v<version>` for a
package with `include-component-in-tag: false` (the root), otherwise
`<component>-v<version>`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

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

from packaging.requirements import Requirement

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / ".github" / "release-please-config.json"
MANIFEST = REPO / ".github" / ".release-please-manifest.json"


@dataclass(frozen=True)
class Package:
    path: str  # repo-relative, "." for the root
    version: str
    tag: str

    @property
    def src(self) -> str:
        return "src" if self.path == "." else f"{self.path}/src"

    @property
    def pyproject(self) -> str:
        return "pyproject.toml" if self.path == "." else f"{self.path}/pyproject.toml"


def dependency_set(pyproject_text: str) -> list[str]:
    """A package's dependencies, normalized for comparison across releases.

    Covers `dependencies` and every `optional-dependencies` group. A
    workspace package (`deepctl-*`) keeps its name, extras and marker but
    loses its version specifier; see the module docstring for why.
    """
    project = tomllib.loads(pyproject_text).get("project", {})
    groups = {"": project.get("dependencies", [])}
    groups.update(project.get("optional-dependencies", {}))
    out = []
    for group, deps in groups.items():
        for dep in deps:
            req = Requirement(dep)
            if req.name.lower().startswith("deepctl"):
                req.specifier = type(req.specifier)()
            out.append(f"{group}:{req}")
    return sorted(out)


def tag_name(config: dict[str, Any], path: str, version: str) -> str:
    """The tag release-please gives `path` at `version`."""
    entry = config.get("packages", {}).get(path, {})
    v = "v" if config.get("include-v-in-tag", True) else ""
    if not entry.get("include-component-in-tag", True):
        return f"{v}{version}"
    return f"{entry.get('component', Path(path).name)}-{v}{version}"


def packages(repo: Path = REPO) -> list[Package]:
    config = json.loads((repo / CONFIG.relative_to(REPO)).read_text(encoding="utf-8"))
    manifest = json.loads(
        (repo / MANIFEST.relative_to(REPO)).read_text(encoding="utf-8")
    )
    out = []
    for path in manifest:
        pyproject = repo / (
            "pyproject.toml" if path == "." else f"{path}/pyproject.toml"
        )
        version = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"][
            "version"
        ]
        out.append(Package(path, version, tag_name(config, path, version)))
    return out


def unbumped(
    pkgs: list[Package],
    tag_exists: Callable[[str], bool],
    changed_since: Callable[[str, str], bool],
    deps_changed: Callable[[str, Package], bool] = lambda _tag, _pkg: False,
) -> list[str]:
    """Problems for packages whose src/ or dependencies moved past their tag."""
    problems = []
    for pkg in pkgs:
        if not tag_exists(pkg.tag):
            continue  # This version has not been released yet.
        if changed_since(pkg.tag, pkg.src):
            problems.append(
                f"{pkg.path}: {pkg.src}/ changed since {pkg.tag}, but the version is"
                f" still {pkg.version}, so PyPI would keep serving the old code"
            )
        if deps_changed(pkg.tag, pkg):
            problems.append(
                f"{pkg.path}: dependencies in {pkg.pyproject} changed since {pkg.tag},"
                f" but the version is still {pkg.version}, so PyPI would keep"
                " serving the old dependency list"
            )
    return problems


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )


def git_tag_exists(repo: Path) -> Callable[[str], bool]:
    """A tag lookup that fails loudly rather than reading an error as "no tag".

    "No tag" means "first release, pass", so a git that cannot answer (not a
    repository, a shallow checkout without tags) must not look like one.
    """
    if not _git(repo, "tag", "--list").stdout.strip():
        raise RuntimeError(
            f"no tags in {repo}: fetch them (actions/checkout fetch-depth: 0)"
        )

    def exists(tag: str) -> bool:
        result = _git(repo, "rev-parse", "-q", "--verify", f"refs/tags/{tag}")
        if result.returncode not in (0, 1):
            raise RuntimeError(f"git rev-parse {tag} failed: {result.stderr.strip()}")
        return result.returncode == 0

    return exists


def git_changed_since(repo: Path) -> Callable[[str, str], bool]:
    def changed(tag: str, path: str) -> bool:
        result = _git(repo, "diff", "--quiet", tag, "HEAD", "--", path)
        if result.returncode not in (0, 1):
            raise RuntimeError(f"git diff {tag} failed: {result.stderr.strip()}")
        return result.returncode == 1

    return changed


def git_deps_changed(repo: Path) -> Callable[[str, Package], bool]:
    def changed(tag: str, pkg: Package) -> bool:
        old = _git(repo, "show", f"{tag}:{pkg.pyproject}")
        new = _git(repo, "show", f"HEAD:{pkg.pyproject}")
        for result, ref in ((old, tag), (new, "HEAD")):
            if result.returncode != 0:
                raise RuntimeError(
                    f"git show {ref}:{pkg.pyproject} failed: {result.stderr.strip()}"
                )
        return dependency_set(old.stdout) != dependency_set(new.stdout)

    return changed


def main(repo: Path = REPO) -> int:
    problems = unbumped(
        packages(repo),
        git_tag_exists(repo),
        git_changed_since(repo),
        git_deps_changed(repo),
    )
    if problems:
        print("release bump check FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "\nLand a `fix:` commit touching each package (or a `Release-As:`"
            " footer) so release-please bumps it, then release again.",
            file=sys.stderr,
        )
        return 1
    print(
        "release bumps OK: no package ships changed code or dependencies under an old version"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else REPO))
