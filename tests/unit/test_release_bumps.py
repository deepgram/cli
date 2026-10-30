"""Tests for scripts/check_release_bumps.py, the pre-publish bump gate."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_release_bumps.py"


def load_gate():
    spec = importlib.util.spec_from_file_location("release_bumps", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # @dataclass needs the module registered
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]
    return module


gate = load_gate()

CONFIG = {
    "include-v-in-tag": True,
    "packages": {
        ".": {"component": "deepctl", "include-component-in-tag": False},
        "packages/deepctl-core": {
            "component": "deepctl-core",
            "include-component-in-tag": True,
        },
    },
}


def test_tag_names_follow_the_release_please_config() -> None:
    assert gate.tag_name(CONFIG, ".", "0.4.0") == "v0.4.0"
    assert (
        gate.tag_name(CONFIG, "packages/deepctl-core", "0.3.0") == "deepctl-core-v0.3.0"
    )
    # release-please's default includes the component.
    assert (
        gate.tag_name({"packages": {}}, "packages/deepctl-x", "1.0.0")
        == "deepctl-x-v1.0.0"
    )


def test_the_real_config_names_every_manifest_package() -> None:
    names = {pkg.path: pkg.tag for pkg in gate.packages(REPO)}
    assert names["."].startswith("v")
    assert names["packages/deepctl-core"].startswith("deepctl-core-v")


CORE = gate.Package("packages/deepctl-core", "0.3.0", "deepctl-core-v0.3.0")
ROOT = gate.Package(".", "0.4.0", "v0.4.0")


def test_changed_code_under_a_released_version_fails() -> None:
    problems = gate.unbumped([CORE], lambda tag: True, lambda tag, path: True)
    assert len(problems) == 1
    assert "packages/deepctl-core/src/ changed since deepctl-core-v0.3.0" in problems[0]


def test_unchanged_code_passes() -> None:
    assert gate.unbumped([CORE, ROOT], lambda tag: True, lambda tag, path: False) == []


def test_a_version_without_a_tag_is_a_first_release_and_passes() -> None:
    def changed(tag: str, path: str) -> bool:
        raise AssertionError("no diff without a tag")

    assert gate.unbumped([CORE], lambda tag: False, changed) == []


def test_only_src_is_compared() -> None:
    seen = []
    gate.unbumped(
        [CORE, ROOT], lambda tag: True, lambda tag, path: seen.append(path) or False
    )
    assert seen == ["packages/deepctl-core/src", "src"]


def pyproject_text(version: str, deps: list[str], optional: dict | None = None) -> str:
    text = f'[project]\nname = "deepctl-core"\nversion = "{version}"\ndependencies = {json.dumps(deps)}\n'
    for group, items in (optional or {}).items():
        text += f"[project.optional-dependencies]\n{group} = {json.dumps(items)}\n"
    return text


def test_a_version_bump_alone_is_not_a_dependency_change() -> None:
    old = pyproject_text("0.3.0", ["click>=8", "rich>=13"])
    new = pyproject_text("0.3.1", ["rich>=13", "click>=8"])
    assert gate.dependency_set(old) == gate.dependency_set(new)


def test_workspace_floors_are_ignored_but_their_presence_is_not() -> None:
    old = pyproject_text("0.3.0", ["deepctl-core>=0.2.19"])
    # The release sync raising a sibling floor is mechanical: not a change.
    assert gate.dependency_set(old) == gate.dependency_set(
        pyproject_text("0.3.0", ["deepctl-core>=0.4.0"])
    )
    # Adding or dropping a workspace dependency is.
    assert gate.dependency_set(old) != gate.dependency_set(
        pyproject_text("0.3.0", ["deepctl-core>=0.2.19", "deepctl-cmd-update>=0.2.7"])
    )


@pytest.mark.parametrize(
    "new_deps",
    [["click>=8", "httpx>=0.27"], ["click>=9"], ["click>=8; python_version<'3.12'"]],
)
def test_third_party_changes_are_dependency_changes(new_deps: list[str]) -> None:
    old = pyproject_text("0.3.0", ["click>=8"])
    assert gate.dependency_set(old) != gate.dependency_set(
        pyproject_text("0.3.0", new_deps)
    )


def test_optional_dependencies_count() -> None:
    old = pyproject_text("0.3.0", [], {"audio": ["numpy>=1"]})
    assert gate.dependency_set(old) != gate.dependency_set(
        pyproject_text("0.3.0", [], {"audio": ["numpy>=2"]})
    )


def test_a_dependency_change_under_a_released_version_fails() -> None:
    problems = gate.unbumped(
        [CORE], lambda tag: True, lambda tag, path: False, lambda tag, pkg: True
    )
    assert problems == [
        "packages/deepctl-core: dependencies in packages/deepctl-core/pyproject.toml changed"
        " since deepctl-core-v0.3.0, but the version is still 0.3.0, so PyPI would keep"
        " serving the old dependency list"
    ]


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_against_a_real_git_repository(tmp_path: Path) -> None:
    """The CLI end to end: tags, a bump, and a forgotten bump."""

    def git(*args: str) -> None:
        subprocess.run(
            [
                "git",
                "-C",
                str(tmp_path),
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                *args,
            ],
            check=True,
            capture_output=True,
        )

    def write(rel: str, text: str) -> None:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def pyproject(rel: str, name: str, version: str) -> None:
        write(rel, f'[project]\nname = "{name}"\nversion = "{version}"\n')

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(tmp_path)], capture_output=True, text=True
        )

    write(".github/release-please-config.json", json.dumps(CONFIG))
    write(
        ".github/.release-please-manifest.json",
        json.dumps({".": "0.4.0", "packages/deepctl-core": "0.3.0"}),
    )
    pyproject("pyproject.toml", "deepctl", "0.4.0")
    pyproject("packages/deepctl-core/pyproject.toml", "deepctl-core", "0.3.0")
    write("src/deepctl/__init__.py", "")
    write("packages/deepctl-core/src/deepctl_core/__init__.py", "")
    git("init", "-q")
    git("add", "-A")
    git("commit", "-qm", "release")
    git("tag", "v0.4.0")
    git("tag", "deepctl-core-v0.3.0")

    # Fresh tags at HEAD: nothing changed.
    assert run().returncode == 0

    # Docs-only and test-only changes are fine.
    write("packages/deepctl-core/README.md", "docs\n")
    git("add", "-A")
    git("commit", "-qm", "docs")
    assert run().returncode == 0

    # A new third-party dependency under the released version.
    write(
        "packages/deepctl-core/pyproject.toml",
        '[project]\nname = "deepctl-core"\nversion = "0.3.0"\ndependencies = ["httpx>=0.27"]\n',
    )
    git("add", "-A")
    git("commit", "-qm", "chore: add httpx")
    result = run()
    assert result.returncode == 1
    assert (
        "dependencies in packages/deepctl-core/pyproject.toml changed" in result.stderr
    )

    # Code changed under the released version too: the 0.3.0 trap.
    write("packages/deepctl-core/src/deepctl_core/launcher.py", "X = 1\n")
    git("add", "-A")
    git("commit", "-qm", "chore: add launcher")
    result = run()
    assert result.returncode == 1
    assert (
        "packages/deepctl-core/src/ changed since deepctl-core-v0.3.0" in result.stderr
    )
    assert "src/ changed since v0.4.0" not in result.stderr

    # Bumped (no tag for 0.3.1 yet): about to be released, so it passes.
    pyproject("packages/deepctl-core/pyproject.toml", "deepctl-core", "0.3.1")
    git("add", "-A")
    git("commit", "-qm", "chore: release core 0.3.1")
    assert run().returncode == 0, run().stderr


def test_a_repository_without_tags_fails_instead_of_passing(tmp_path: Path) -> None:
    """A shallow checkout has no tags; that must not read as "first release"."""
    if shutil.which("git") is None:
        pytest.skip("needs git")
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    with pytest.raises(RuntimeError, match="no tags"):
        gate.git_tag_exists(tmp_path)


def test_not_a_repository_fails(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        gate.git_tag_exists(tmp_path)("v1.0.0")
