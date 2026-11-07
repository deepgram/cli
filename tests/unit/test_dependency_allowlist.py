"""The third-party closure of the 30 lockstep packages is allowlisted.

Every distribution a plain `pip install deepctl` can pull in, on any
platform or Python the lock covers, must be named in
`dependency_allowlist.txt`. A new runtime dependency, direct or transitive,
fails here until someone adds it on purpose. numpy, ffmpeg-python, future
and aiohttp were dropped in 0.5.0 (the install diet) and must not come back,
allowlisted or not.

Two views of the closure are checked:

* the lock: `uv.lock`'s runtime `dependencies` for every lockstep package,
  across all markers, followed transitively (extras included). This is the
  same graph the Requires-Dist metadata describes, read without installing.
* the installed environment: Requires-Dist of the installed lockstep
  distributions, evaluated for this interpreter and followed transitively.
"""

from __future__ import annotations

import os
import re
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - only taken on Python 3.10
    import tomli as tomllib

REPO = Path(__file__).resolve().parents[2]
ALLOWLIST = Path(__file__).with_name("dependency_allowlist.txt")
ROOT_NAME = "deepctl"
# Versioned on its own, the way an outside plugin would be.
NOT_LOCKSTEP = {"deepctl-plugin-example"}
FORBIDDEN = {"numpy", "ffmpeg-python", "future", "aiohttp"}


def normalize(name: str) -> str:
    """PEP 503 normalized distribution name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def lockstep_names(repo: Path = REPO) -> set[str]:
    """The root and every first-party sibling that versions with it."""
    names = {ROOT_NAME}
    for pyproject in sorted((repo / "packages").glob("*/pyproject.toml")):
        with pyproject.open("rb") as fh:
            name = normalize(tomllib.load(fh)["project"]["name"])
        if name not in NOT_LOCKSTEP:
            names.add(name)
    return names


FLAGS = {"floors-only"}
#: Set by tests/upgrade/lowest_direct.sh: this process runs in the
#: environment resolved at the declared floors.
LOWEST_DIRECT_ENV = "DEEPCTL_LOWEST_DIRECT"


def parse_allowlist(path: Path = ALLOWLIST) -> list[tuple[str, frozenset[str]]]:
    """(name, flags) per entry, in file order, without comments or blanks."""
    entries = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        fields = line.split("#", 1)[0].split()
        if not fields:
            continue
        name, flags = fields[0], frozenset(fields[1:])
        unknown = flags - FLAGS
        if unknown:
            raise ValueError(f"{path.name}:{number}: unknown flag(s) {sorted(unknown)}")
        entries.append((name, flags))
    return entries


def read_allowlist(path: Path = ALLOWLIST) -> list[str]:
    """Allowlisted names, in file order."""
    return [name for name, _ in parse_allowlist(path)]


def floors_only(path: Path = ALLOWLIST) -> set[str]:
    """Entries reached only at declared floors, not through uv.lock."""
    return {name for name, flags in parse_allowlist(path) if "floors-only" in flags}


def lock_closure(lock: dict[str, Any], roots: set[str]) -> set[str]:
    """Third-party names reachable from `roots` through runtime deps."""
    by_name: dict[str, list[dict[str, Any]]] = {}
    for package in lock["package"]:
        by_name.setdefault(normalize(package["name"]), []).append(package)

    seen: set[tuple[str, str | None]] = set()
    todo: list[tuple[str, str | None]] = [(root, None) for root in roots]
    reached: set[str] = set()
    while todo:
        name, extra = todo.pop()
        if (name, extra) in seen:
            continue
        seen.add((name, extra))
        reached.add(name)
        for package in by_name.get(name, []):
            if extra is None:
                deps = package.get("dependencies", [])
            else:
                deps = package.get("optional-dependencies", {}).get(extra, [])
            for dep in deps:
                dep_name = normalize(dep["name"])
                todo.append((dep_name, None))
                todo.extend((dep_name, e) for e in dep.get("extra", []))
    return reached - roots


def installed_closure(roots: set[str]) -> set[str]:
    """Third-party names reachable from the installed `roots` through
    Requires-Dist, with markers evaluated for this interpreter."""
    from packaging.requirements import Requirement

    seen: set[tuple[str, str]] = set()
    todo: list[tuple[str, str]] = [(root, "") for root in roots]
    reached: set[str] = set()
    while todo:
        name, extra = todo.pop()
        if (name, extra) in seen:
            continue
        seen.add((name, extra))
        try:
            requires = metadata.requires(name) or []
        except metadata.PackageNotFoundError:
            # A marker can name a dist this platform never installs.
            continue
        reached.add(name)
        for raw in requires:
            req = Requirement(raw)
            if req.marker is not None and not req.marker.evaluate({"extra": extra}):
                continue
            dep = normalize(req.name)
            todo.append((dep, ""))
            todo.extend((dep, e) for e in req.extras)
    return reached - roots


def violations(closure: set[str], allowlist: set[str]) -> list[str]:
    """Why the closure fails: unlisted names, then forbidden ones."""
    problems = [
        f"{name} is a runtime dependency but is not in {ALLOWLIST.name}"
        for name in sorted(closure - allowlist)
    ]
    problems += [
        f"{name} was dropped in 0.5.0 and must not come back"
        for name in sorted(closure & FORBIDDEN)
    ]
    return problems


@pytest.fixture(scope="module")
def lock() -> dict[str, Any]:
    with (REPO / "uv.lock").open("rb") as fh:
        return tomllib.load(fh)


@pytest.fixture(scope="module")
def roots() -> set[str]:
    names = lockstep_names()
    assert len(names) == 30, sorted(names)
    return names


def test_allowlist_is_sorted_unique_and_normalized() -> None:
    entries = read_allowlist()
    assert entries == sorted(set(entries)), "keep the allowlist sorted, no repeats"
    assert all(entry == normalize(entry) for entry in entries)
    assert not FORBIDDEN & set(entries)


def test_locked_closure_is_allowlisted(lock: dict[str, Any], roots: set[str]) -> None:
    closure = lock_closure(lock, roots)
    assert violations(closure, set(read_allowlist())) == []


def test_allowlist_has_no_stale_entries(lock: dict[str, Any], roots: set[str]) -> None:
    """An entry nothing depends on any more is removed, so the list stays a
    true picture of what installs."""
    stale = set(read_allowlist()) - lock_closure(lock, roots) - floors_only()
    assert not stale, f"no longer a runtime dependency: {sorted(stale)}"


def test_installed_closure_is_allowlisted(roots: set[str]) -> None:
    missing = sorted(r for r in roots if not _installed(r))
    if missing:
        pytest.skip(f"lockstep packages not installed here: {missing}")
    closure = installed_closure(roots)
    assert violations(closure, set(read_allowlist())) == []


def test_floors_only_entries_are_installed_at_the_floors(roots: set[str]) -> None:
    """In the lowest-direct environment, every floors-only entry must really
    be pulled in; one that is not is stale and comes off the list."""
    if os.environ.get(LOWEST_DIRECT_ENV) != "1":
        pytest.skip(f"only in the lowest-direct job ({LOWEST_DIRECT_ENV}=1)")
    missing = sorted(floors_only() - installed_closure(roots))
    assert not missing, f"floors-only but not reached at the floors: {missing}"


def test_unknown_flags_are_refused(tmp_path: Path) -> None:
    listing = tmp_path / "allow.txt"
    listing.write_text("idna\nsix floors-onyl  # typo\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown flag"):
        parse_allowlist(listing)


def test_flags_and_comments_parse(tmp_path: Path) -> None:
    listing = tmp_path / "allow.txt"
    listing.write_text("# header\nidna\nsix floors-only  # why\n\n", encoding="utf-8")
    assert parse_allowlist(listing) == [
        ("idna", frozenset()),
        ("six", frozenset({"floors-only"})),
    ]


def _installed(name: str) -> bool:
    try:
        metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return False
    return True


# -- negative fixtures: the check catches what it is for -------------------


def _planted(lock: dict[str, Any], extra_dep: str) -> dict[str, Any]:
    """A copy of the lock where deepctl-core gains `extra_dep`, which in turn
    depends on six (a transitive addition)."""
    packages = []
    for package in lock["package"]:
        package = dict(package)
        if package["name"] == "deepctl-core":
            package["dependencies"] = [
                *package.get("dependencies", []),
                {"name": extra_dep},
            ]
        packages.append(package)
    packages.append(
        {"name": extra_dep, "version": "1.0", "dependencies": [{"name": "six"}]}
    )
    packages.append({"name": "six", "version": "1.17.0"})
    return {**lock, "package": packages}


def test_a_planted_dependency_fails(lock: dict[str, Any], roots: set[str]) -> None:
    allowlist = set(read_allowlist())
    closure = lock_closure(_planted(lock, "left-pad-py"), roots)
    problems = violations(closure, allowlist)
    assert any(p.startswith("left-pad-py ") for p in problems)
    if "six" not in allowlist:
        assert any(p.startswith("six ") for p in problems)


@pytest.mark.parametrize("dropped", sorted(FORBIDDEN))
def test_a_dropped_dependency_fails_even_if_allowlisted(
    lock: dict[str, Any], roots: set[str], dropped: str
) -> None:
    allowlist = set(read_allowlist()) | {dropped}
    closure = lock_closure(_planted(lock, dropped), roots)
    assert violations(closure, allowlist | {"six"}) == [
        f"{dropped} was dropped in 0.5.0 and must not come back"
    ]


def test_extras_are_followed(lock: dict[str, Any], roots: set[str]) -> None:
    """A dependency requested with an extra pulls in that extra's deps."""
    planted = _planted(lock, "pkg-with-extra")
    for package in planted["package"]:
        if package["name"] == "deepctl-core":
            package["dependencies"][-1] = {"name": "pkg-with-extra", "extra": ["x"]}
        if package["name"] == "pkg-with-extra":
            package["optional-dependencies"] = {"x": [{"name": "only-via-extra"}]}
    assert "only-via-extra" in lock_closure(planted, roots)
