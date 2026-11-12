"""Static checks on the release-please config for lockstep versions.

One release-please component (`.`) versions every first-party package: its
`extra-files` move each sibling's `version` and every `==` pin line that
carries the `x-release-please-version` marker, so one release PR proposes one
version and one tag. The plugin example is the only other component: it is the
third-party fixture and versions on its own.

`tests/upgrade/release-please/run.sh` runs release-please itself over the tree;
these tests catch a config edit that would make that run drift.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONFIG = json.loads(
    (REPO / ".github" / "release-please-config.json").read_text("utf-8")
)
MANIFEST = json.loads(
    (REPO / ".github" / ".release-please-manifest.json").read_text("utf-8")
)
EXAMPLE = "packages/deepctl-plugin-example"
MARKER = "x-release-please-version"
SIBLINGS = sorted(
    p.parent.relative_to(REPO).as_posix()
    for p in (REPO / "packages").glob("*/pyproject.toml")
    if p.parent.relative_to(REPO).as_posix() != EXAMPLE
)
ROOT = CONFIG["packages"]["."]


def extra_paths(kind: str) -> list[str]:
    out = []
    for entry in ROOT.get("extra-files", []):
        path = entry["path"] if isinstance(entry, dict) else entry
        if path.endswith(kind):
            out.append(path)
    return out


def test_there_are_29_lockstep_siblings() -> None:
    assert len(SIBLINGS) == 29, SIBLINGS


def test_one_lockstep_component() -> None:
    """The root is the only component besides the plugin example."""
    assert set(CONFIG["packages"]) == {".", EXAMPLE}
    assert ROOT["component"] == "deepctl"
    assert ROOT["include-component-in-tag"] is False
    assert ROOT["make-latest"] is True
    assert CONFIG["packages"][EXAMPLE]["make-latest"] is False
    assert CONFIG.get("separate-pull-requests") is False


def test_root_ignores_plugin_example_commits() -> None:
    """A commit that only touches the example must not cut a CLI release."""
    assert EXAMPLE in ROOT.get("exclude-paths", [])


def test_manifest_matches_the_components() -> None:
    assert set(MANIFEST) == set(CONFIG["packages"])


def test_extra_files_cover_exactly_the_sibling_pyprojects() -> None:
    sibling_tomls = sorted(p for p in extra_paths(".toml") if p != "pyproject.toml")
    assert sibling_tomls == sorted(f"{s}/pyproject.toml" for s in SIBLINGS)
    # The root pyproject is listed too, so its pin lines move with the version.
    assert "pyproject.toml" in extra_paths(".toml")


def test_toml_extra_files_use_the_generic_marker_updater() -> None:
    """A bare `.toml` path also runs a `$.version` TOML updater that matches
    nothing in a pyproject and warns on every release; `generic` only rewrites
    lines carrying the marker, which is what the pins need."""
    for entry in ROOT["extra-files"]:
        path = entry["path"] if isinstance(entry, dict) else entry
        if path.endswith(".toml"):
            assert entry == {"type": "generic", "path": path}


def test_every_marked_version_file_is_an_extra_file() -> None:
    marked = sorted(
        p.relative_to(REPO).as_posix()
        for p in [
            REPO / "src" / "deepctl" / "__init__.py",
            *(REPO / "packages").glob("*/src/*/__init__.py"),
        ]
        if MARKER in p.read_text("utf-8") and EXAMPLE not in p.as_posix()
    )
    assert marked
    assert sorted(extra_paths(".py")) == marked


def test_every_first_party_pin_line_carries_the_marker() -> None:
    """Any `deepctl*==` requirement without the marker would be left behind."""
    pin = re.compile(r"[\"']deepctl[a-z0-9-]*\s*==")
    unmarked = []
    pins = 0
    for path in [
        REPO / "pyproject.toml",
        *(REPO / "packages").glob("*/pyproject.toml"),
    ]:
        for n, line in enumerate(path.read_text("utf-8").splitlines(), 1):
            if pin.search(line):
                pins += 1
                if MARKER not in line:
                    unmarked.append(f"{path.relative_to(REPO)}:{n}")
    assert not unmarked
    # Root -> 29 siblings, plus every sibling's back-edge to the root.
    assert pins >= 29 + 29


def test_every_lockstep_version_line_carries_the_marker() -> None:
    for rel in [".", *SIBLINGS]:
        text = (REPO / rel / "pyproject.toml").read_text("utf-8")
        line = next(ln for ln in text.splitlines() if ln.startswith("version"))
        assert MARKER in line, rel
