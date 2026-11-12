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
manifest whose current version already has a release tag, everything the
package ships must be unchanged since that tag. A version with no tag yet is
about to be released from this tree, which is fine.

What a package ships: its `src/`, and what its pyproject.toml puts in the
wheel (the [project] table, [build-system], [tool.setuptools], and the
[tool.hatch.build] and [tool.hatch.metadata] tables that pick its files).
The root package also ships the npm package, @deepgram/cli, which
release.yml publishes at the root's version: every entry in the `files` of
npm/package.json as committed at HEAD, and package.json itself. A `files`
glob with no slash, like `requirements-*.txt`, matches at any depth under
npm/, as npm matches it. npm/README.md and npm/LICENSE ship too but are left out, as the
packages' READMEs are: a docs change should not force a release.

pyproject.toml is compared parsed, not as text, so the release PR's own
version edit does not count. Version specifiers on other workspace
packages (`deepctl-*`) are left out of the comparison: the release sync job
raises their floors mechanically in packages it is not bumping, and
check_dependency_floors.py already governs them. Adding, removing or
re-pinning any other dependency, or changing an entry point, without a bump
is caught. npm/package.json is compared as text: release-please writes its
version only on the release PR, when the new version has no tag yet.

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
NPM_PACKAGE = "npm/package.json"
NPM_DIR = Path(NPM_PACKAGE).parent.as_posix()
GLOB_CHARS = "*?["
# The [tool.*] tables a build backend reads to decide what goes in the wheel:
# a name maps to None to compare the whole table, or to the subtables that
# count. [tool.hatch.envs] and the like are dev tooling and never ship.
BUILD_TOOLS: dict[str, tuple[str, ...] | None] = {
    "setuptools": None,
    "hatch": ("build", "metadata"),
}


@dataclass(frozen=True)
class Package:
    path: str  # repo-relative, "." for the root
    version: str
    tag: str
    npm: tuple[str, ...] = ()  # npm package paths it ships too (the root)

    @property
    def src(self) -> str:
        return "src" if self.path == "." else f"{self.path}/src"

    @property
    def pyproject(self) -> str:
        return "pyproject.toml" if self.path == "." else f"{self.path}/pyproject.toml"

    @property
    def shipped(self) -> tuple[str, ...]:
        """Repo-relative paths whose content ships; a trailing / is a directory."""
        return (f"{self.src}/", *self.npm)


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


def shipped_metadata(pyproject_text: str) -> dict[str, Any]:
    """What a pyproject.toml puts in the wheel, normalized for comparison.

    The [project] table without `version`, its dependencies as
    dependency_set gives them, [build-system], and the build backend's own
    tables. [tool.ruff], [tool.mypy] and the like never reach the wheel.
    """
    data = tomllib.loads(pyproject_text)
    project = {
        key: value
        for key, value in data.get("project", {}).items()
        if key not in ("version", "dependencies", "optional-dependencies")
    }
    tool = data.get("tool", {})
    build_tools: dict[str, Any] = {}
    for name, subtables in BUILD_TOOLS.items():
        if name not in tool:
            continue
        table = tool[name]
        if subtables is not None:
            table = {key: table[key] for key in subtables if key in table}
        build_tools[name] = table
    return {
        "project": project,
        "dependencies": dependency_set(pyproject_text),
        "build-system": data.get("build-system", {}),
        "tool": build_tools,
    }


def npm_paths(repo: Path = REPO) -> tuple[str, ...]:
    """The repo paths the npm package ships, from its package.json `files`.

    Read from `files` rather than listed here, so an entry added there is
    watched too, and read at HEAD, the commit compared against the tags.
    npm always adds package.json itself. Entries keep the spelling they have
    in `files`; git_pathspec turns a glob into what git should match.
    """
    listed = _git(repo, "ls-tree", "--name-only", "HEAD", "--", NPM_PACKAGE)
    if listed.returncode != 0:
        raise RuntimeError(f"git ls-tree HEAD failed: {listed.stderr.strip()}")
    if not listed.stdout.strip():
        return ()
    shown = _git(repo, "show", f"HEAD:{NPM_PACKAGE}")
    if shown.returncode != 0:
        raise RuntimeError(
            f"git show HEAD:{NPM_PACKAGE} failed: {shown.stderr.strip()}"
        )
    files = json.loads(shown.stdout).get("files", [])
    return (*(f"{NPM_DIR}/{entry}" for entry in files), NPM_PACKAGE)


def git_pathspec(path: str) -> str:
    """The git pathspec for a shipped path.

    A plain path is a literal prefix. A glob gets `:(glob)` magic, and an
    npm `files` glob with no slash (`requirements-*.txt`) matches at any
    depth, as it does for npm: `**/` in glob magic also matches no
    directory, so the top level is still covered.
    """
    if not any(c in path for c in GLOB_CHARS):
        return path
    entry = path.removeprefix(f"{NPM_DIR}/")
    if entry != path and "/" not in entry:
        return f":(glob){NPM_DIR}/**/{entry}"
    return f":(glob){path}"


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
        npm = npm_paths(repo) if path == "." else ()
        out.append(Package(path, version, tag_name(config, path, version), npm))
    return out


def unbumped(
    pkgs: list[Package],
    tag_exists: Callable[[str], bool],
    changed_since: Callable[[str, str], bool],
    metadata_changed: Callable[[str, Package], bool] = lambda _tag, _pkg: False,
) -> list[str]:
    """Problems for packages whose shipped files or metadata moved past their tag."""
    problems = []
    for pkg in pkgs:
        if not tag_exists(pkg.tag):
            continue  # This version has not been released yet.
        for path in pkg.shipped:
            if changed_since(pkg.tag, path):
                registry = "npm" if path.startswith("npm/") else "PyPI"
                problems.append(
                    f"{pkg.path}: {path} changed since {pkg.tag}, but the version is"
                    f" still {pkg.version}, so {registry} would keep serving the old code"
                )
        if metadata_changed(pkg.tag, pkg):
            problems.append(
                f"{pkg.path}: packaging metadata in {pkg.pyproject} (dependencies,"
                f" entry points or build settings) changed since {pkg.tag}, but the"
                f" version is still {pkg.version}, so PyPI would keep serving the old"
                " metadata"
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
        result = _git(repo, "diff", "--quiet", tag, "HEAD", "--", git_pathspec(path))
        if result.returncode not in (0, 1):
            raise RuntimeError(f"git diff {tag} failed: {result.stderr.strip()}")
        return result.returncode == 1

    return changed


def git_metadata_changed(repo: Path) -> Callable[[str, Package], bool]:
    def changed(tag: str, pkg: Package) -> bool:
        old = _git(repo, "show", f"{tag}:{pkg.pyproject}")
        new = _git(repo, "show", f"HEAD:{pkg.pyproject}")
        for result, ref in ((old, tag), (new, "HEAD")):
            if result.returncode != 0:
                raise RuntimeError(
                    f"git show {ref}:{pkg.pyproject} failed: {result.stderr.strip()}"
                )
        return shipped_metadata(old.stdout) != shipped_metadata(new.stdout)

    return changed


def main(repo: Path = REPO) -> int:
    problems = unbumped(
        packages(repo),
        git_tag_exists(repo),
        git_changed_since(repo),
        git_metadata_changed(repo),
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
        "release bumps OK: no package ships changed code or metadata under an old version"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else REPO))
