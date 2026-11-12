"""Tests for scripts/check_dependency_floors.py, the lockstep check.

The check is a required status on every PR, and the release workflow's
fix-up job runs it with `--fix`, then `uv lock`, then plain. Anything that
makes `--fix` disagree with the next check wedges the release PR, so these
tests drive the real script over throwaway workspaces.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_dependency_floors.py"
MARKER = "# x-release-please-version"
SIBLINGS = ["deepctl-core", "deepctl-cmd-keys", "deepctl-cmd-listen"]


def pin(name: str, version: str) -> str:
    return f'    "{name}=={version}", {MARKER}\n'


def project_toml(name: str, version: str, deps: list[str], sources: list[str]) -> str:
    text = (
        "[project]\n"
        f'name = "{name}"\n'
        f'version = "{version}" {MARKER}\n'
        "dependencies = [\n" + "".join(deps) + '    "click>=8.0.0,<9",\n]\n'
    )
    if sources:
        text += "\n[tool.uv.sources]\n" + "".join(
            f"{s} = {{ workspace = true }}\n" for s in sources
        )
    return text


def make_workspace(tmp_path: Path, version: str = "1.0.0") -> Path:
    """A correct lockstep workspace: root, three siblings, the plugin example."""
    (tmp_path / "scripts").mkdir()
    shutil.copy(SCRIPT, tmp_path / "scripts" / SCRIPT.name)
    (tmp_path / "pyproject.toml").write_text(
        project_toml(
            "deepctl", version, [pin(s, version) for s in SIBLINGS], sources=SIBLINGS
        ),
        encoding="utf-8",
    )
    for name in SIBLINGS:
        deps = [pin("deepctl", version)]
        sources = ["deepctl"]
        if name != "deepctl-core":
            deps.append(pin("deepctl-core", version))
            sources.append("deepctl-core")
        pkg = tmp_path / "packages" / name
        pkg.mkdir(parents=True)
        (pkg / "pyproject.toml").write_text(
            project_toml(name, version, deps, sources), encoding="utf-8"
        )
    example = tmp_path / "packages" / "deepctl-plugin-example"
    example.mkdir(parents=True)
    (example / "pyproject.toml").write_text(
        project_toml(
            "deepctl-plugin-example",
            "0.1.12",
            ['    "deepctl-core>=0.1.10",\n'],
            sources=["deepctl-core"],
        ),
        encoding="utf-8",
    )
    init = tmp_path / "src" / "deepctl" / "__init__.py"
    init.parent.mkdir(parents=True)
    init.write_text(f'__version__ = "{version}"  {MARKER}\n', encoding="utf-8")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / ".release-please-manifest.json").write_text(
        json.dumps({".": version, "packages/deepctl-plugin-example": "0.1.12"}),
        encoding="utf-8",
    )
    return tmp_path


def run_check(workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(workspace / "scripts" / SCRIPT.name), *args],
        capture_output=True,
        text=True,
    )


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, (old, text)
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def sibling(workspace: Path, name: str) -> Path:
    return workspace / "packages" / name / "pyproject.toml"


def test_lockstep_workspace_passes(tmp_path: Path) -> None:
    result = run_check(make_workspace(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "lockstep OK: 4 packages at 1.0.0" in result.stdout


def test_real_repository_is_in_lockstep() -> None:
    """The tree this test ships in passes its own required check."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "lockstep OK: 30 packages at" in result.stdout


# -- each drift fails closed, naming the file ----------------------------------


def break_old_pin(ws: Path) -> str:
    keys = sibling(ws, "deepctl-cmd-keys")
    edit(keys, '"deepctl-core==1.0.0"', '"deepctl-core==0.9.0"')
    return "packages/deepctl-cmd-keys/pyproject.toml: 'deepctl-core==0.9.0' must be exactly 'deepctl-core==1.0.0'"


def break_back_edge(ws: Path) -> str:
    edit(sibling(ws, "deepctl-cmd-listen"), pin("deepctl", "1.0.0"), "")
    return "packages/deepctl-cmd-listen/pyproject.toml: missing the back-edge deepctl==1.0.0"


def break_floor(ws: Path) -> str:
    edit(sibling(ws, "deepctl-core"), '"deepctl==1.0.0"', '"deepctl>=1.0.0"')
    return "packages/deepctl-core/pyproject.toml: 'deepctl>=1.0.0' must be exactly 'deepctl==1.0.0'"


def break_root_list(ws: Path) -> str:
    edit(ws / "pyproject.toml", pin("deepctl-cmd-listen", "1.0.0"), "")
    return "pyproject.toml: missing deepctl-cmd-listen==1.0.0"


BREAKS = {
    "sibling pin ==V-1": break_old_pin,
    "sibling missing deepctl==V": break_back_edge,
    "floor >= instead of ==": break_floor,
    "root missing a sibling": break_root_list,
}


@pytest.mark.parametrize("breaker", BREAKS.values(), ids=BREAKS.keys())
def test_each_drift_fails_naming_the_file(
    tmp_path: Path, breaker: Callable[[Path], str]
) -> None:
    ws = make_workspace(tmp_path)
    expected = breaker(ws)

    result = run_check(ws)

    assert result.returncode == 1
    assert expected in result.stderr, result.stderr


def test_fix_repairs_all_four_at_once(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    clean = {p: p.read_text("utf-8") for p in ws.rglob("pyproject.toml")}
    for breaker in BREAKS.values():
        breaker(ws)
    assert run_check(ws).returncode == 1

    fix = run_check(ws, "--fix")

    assert fix.returncode == 0, fix.stderr
    assert run_check(ws).returncode == 0
    # Order inside the dependency array may differ; the set of lines may not.
    for path, text in clean.items():
        assert sorted(path.read_text("utf-8").splitlines()) == sorted(
            text.splitlines()
        ), path


def test_fix_is_idempotent(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    before = {p: p.read_bytes() for p in ws.rglob("*") if p.is_file()}

    assert run_check(ws, "--fix").returncode == 0

    assert {p: p.read_bytes() for p in ws.rglob("*") if p.is_file()} == before


# -- the rest of the contract ------------------------------------------------


def test_floor_equal_to_the_version_is_still_a_failure(tmp_path: Path) -> None:
    """`>=V` lets pip pair this package with a newer sibling later on."""
    ws = make_workspace(tmp_path)
    edit(ws / "pyproject.toml", '"deepctl-core==1.0.0"', '"deepctl-core>=1.0.0"')

    result = run_check(ws)

    assert result.returncode == 1
    assert "'deepctl-core>=1.0.0' must be exactly" in result.stderr


def test_pin_above_the_version_fails(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    edit(sibling(ws, "deepctl-cmd-keys"), '"deepctl==1.0.0"', '"deepctl==1.0.1"')
    assert run_check(ws).returncode == 1


def test_sibling_version_drift_fails_and_is_fixed(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    edit(sibling(ws, "deepctl-cmd-keys"), 'version = "1.0.0"', 'version = "0.9.0"')

    result = run_check(ws)
    assert result.returncode == 1
    assert (
        "packages/deepctl-cmd-keys/pyproject.toml: version '0.9.0' must be '1.0.0'"
        in result.stderr
    )

    assert run_check(ws, "--fix").returncode == 0
    assert 'version = "1.0.0"' in sibling(ws, "deepctl-cmd-keys").read_text("utf-8")


def test_pin_without_marker_fails(tmp_path: Path) -> None:
    """release-please only moves lines carrying the marker."""
    ws = make_workspace(tmp_path)
    edit(
        sibling(ws, "deepctl-cmd-keys"),
        pin("deepctl-core", "1.0.0"),
        '    "deepctl-core==1.0.0",\n',
    )

    result = run_check(ws)

    assert result.returncode == 1
    assert "missing the `# x-release-please-version` marker" in result.stderr
    assert run_check(ws, "--fix").returncode == 0


def test_extras_and_markers_are_not_exact_pins(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    edit(
        ws / "pyproject.toml",
        '"deepctl-core==1.0.0"',
        "\"deepctl-core==1.0.0; python_version >= '3.10'\"",
    )
    assert run_check(ws).returncode == 1


def test_missing_workspace_source_fails(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    edit(sibling(ws, "deepctl-cmd-keys"), "deepctl = { workspace = true }\n", "")

    result = run_check(ws)

    assert result.returncode == 1
    assert "has no `deepctl = { workspace = true }`" in result.stderr
    assert run_check(ws, "--fix").returncode == 0


def test_sibling_manifest_entry_fails(tmp_path: Path) -> None:
    """Siblings no longer have their own release-please component."""
    ws = make_workspace(tmp_path)
    manifest = ws / ".github" / ".release-please-manifest.json"
    data = json.loads(manifest.read_text("utf-8"))
    data["packages/deepctl-cmd-keys"] = "1.0.0"
    manifest.write_text(json.dumps(data), encoding="utf-8")

    result = run_check(ws)
    assert result.returncode == 1
    assert "'packages/deepctl-cmd-keys' is a lockstep package" in result.stderr

    assert run_check(ws, "--fix").returncode == 0
    assert set(json.loads(manifest.read_text("utf-8"))) == {
        ".",
        "packages/deepctl-plugin-example",
    }


def test_dunder_version_drift_fails(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    edit(ws / "src" / "deepctl" / "__init__.py", '"1.0.0"', '"0.9.0"')
    assert run_check(ws).returncode == 1
    assert run_check(ws, "--fix").returncode == 0


def test_first_party_dependency_outside_the_lockstep_set_fails(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    edit(
        sibling(ws, "deepctl-cmd-keys"),
        '    "click>=8.0.0,<9",\n',
        '    "click>=8.0.0,<9",\n    "deepctl-plugin-example>=0.1",\n',
    )

    result = run_check(ws)

    assert result.returncode == 1
    assert (
        "depends on deepctl-plugin-example, which is not a lockstep package"
        in result.stderr
    )


def test_plugin_example_is_not_pinned_but_must_accept_the_version(
    tmp_path: Path,
) -> None:
    ws = make_workspace(tmp_path)
    example = sibling(ws, "deepctl-plugin-example")
    assert run_check(ws).returncode == 0  # its `>=` floor is fine

    edit(example, '"deepctl-core>=0.1.10"', '"deepctl-core>=9.9.9"')
    result = run_check(ws)

    assert result.returncode == 1
    assert "does not accept the lockstep version 1.0.0 (unsatisfiable)" in result.stderr


@pytest.mark.parametrize("version", ["1.0.0rc1", "1.0.0.dev1", "0.5.0.post1"])
def test_fix_then_check_round_trips_on_pre_release_versions(
    tmp_path: Path, version: str
) -> None:
    """A pre-release root version must not turn --fix into a loop."""
    ws = make_workspace(tmp_path)
    edit(ws / "pyproject.toml", 'version = "1.0.0"', f'version = "{version}"')

    assert run_check(ws).returncode == 1
    fix = run_check(ws, "--fix")
    assert fix.returncode == 0, fix.stderr
    assert f'"deepctl-core=={version}"' in (ws / "pyproject.toml").read_text("utf-8")
    assert run_check(ws).returncode == 0


def test_single_line_dependency_array_is_reported_not_guessed(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    keys = sibling(ws, "deepctl-cmd-keys")
    text = keys.read_text("utf-8")
    start = text.index("dependencies = [")
    end = text.index("]\n", start) + 2
    keys.write_text(
        text[:start] + 'dependencies = ["deepctl==1.0.0"]\n' + text[end:],
        encoding="utf-8",
    )

    result = run_check(ws, "--fix")

    assert result.returncode == 1
    assert "must be a multi-line array" in result.stderr


# -- layouts the line scan can't read must fail closed ------------------------

LAYOUTS = {
    "single quotes": "    'deepctl-cmd-listen==1.0.0',\n",
    "two on one line": '    "deepctl-cmd-listen==1.0.0", "httpx>=0.24",\n',
}


@pytest.mark.parametrize("layout", ["single quotes", "two on one line"])
def test_unreadable_first_party_line_fails_closed(tmp_path: Path, layout: str) -> None:
    """A requirement the line scan skips must not pass as "lockstep OK"."""
    ws = make_workspace(tmp_path)
    edit(
        sibling(ws, "deepctl-cmd-keys"),
        '    "click>=8.0.0,<9",\n',
        '    "click>=8.0.0,<9",\n' + LAYOUTS[layout],
    )

    result = run_check(ws)

    assert result.returncode == 1, result.stdout
    assert (
        "packages/deepctl-cmd-keys/pyproject.toml: deepctl-cmd-listen: a"
        " first-party requirement must be on its own double-quoted line"
    ) in result.stderr
    # --fix can't safely rewrite it either; it stays a hand edit.
    assert run_check(ws, "--fix").returncode == 1


def test_well_formed_multi_line_array_passes(tmp_path: Path) -> None:
    """Control: one double-quoted requirement per line, comments allowed."""
    ws = make_workspace(tmp_path)
    edit(
        sibling(ws, "deepctl-cmd-keys"),
        '    "click>=8.0.0,<9",\n',
        '    # a comment line\n    "click>=8.0.0,<9",  # trailing comment\n',
    )
    result = run_check(ws)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "spec",
    ["deepctl-core[speech]==1.0.0", "deepctl-core==1.0.0; python_version >= '3.10'"],
    ids=["extras", "marker"],
)
def test_extras_or_marker_on_a_pin_fail(tmp_path: Path, spec: str) -> None:
    ws = make_workspace(tmp_path)
    edit(sibling(ws, "deepctl-cmd-keys"), '"deepctl-core==1.0.0"', f'"{spec}"')

    result = run_check(ws)

    assert result.returncode == 1
    assert "must be exactly 'deepctl-core==1.0.0'" in result.stderr


@pytest.mark.parametrize(
    "table",
    [
        '\n[project.optional-dependencies]\nspeech = ["deepctl-cmd-listen>=0.1"]\n',
        '\n[dependency-groups]\ndev = ["deepctl-cmd-listen>=0.1"]\n',
    ],
    ids=["optional-dependencies", "dependency-groups"],
)
def test_lockstep_package_outside_project_dependencies_fails(
    tmp_path: Path, table: str
) -> None:
    ws = make_workspace(tmp_path)
    keys = sibling(ws, "deepctl-cmd-keys")
    text = keys.read_text("utf-8")
    # Put the table before [tool.uv.sources] so the TOML stays valid.
    keys.write_text(
        text.replace("\n[tool.uv.sources]", table + "\n[tool.uv.sources]", 1), "utf-8"
    )

    result = run_check(ws)

    assert result.returncode == 1
    assert "requires the lockstep package deepctl-cmd-listen" in result.stderr


def test_short_version_is_not_an_exact_pin(tmp_path: Path) -> None:
    """release-please rewrites X.Y.Z literally; `==1.0` would be left behind."""
    ws = make_workspace(tmp_path)
    edit(
        sibling(ws, "deepctl-cmd-keys"), '"deepctl-core==1.0.0"', '"deepctl-core==1.0"'
    )
    assert run_check(ws).returncode == 1


def test_version_outside_project_table_is_ignored(tmp_path: Path) -> None:
    """Only [project].version counts, not the first `version =` in the file."""
    ws = make_workspace(tmp_path)
    keys = sibling(ws, "deepctl-cmd-keys")
    keys.write_text(
        '[tool.example]\nversion = "9.9.9"\n\n' + keys.read_text("utf-8"), "utf-8"
    )
    result = run_check(ws)
    assert result.returncode == 0, result.stderr
