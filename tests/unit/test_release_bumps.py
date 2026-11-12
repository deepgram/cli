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


def _is_git_checkout() -> bool:
    """Whether git is installed and REPO is a git checkout it can read HEAD in."""
    if shutil.which("git") is None:
        return False
    result = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "-q", "--verify", "HEAD"],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
needs_git_checkout = pytest.mark.skipif(
    not _is_git_checkout(), reason="needs git and a git checkout of this repository"
)

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


@needs_git_checkout
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


def test_only_shipped_paths_are_compared() -> None:
    seen = []
    root = gate.Package(".", "0.4.0", "v0.4.0", ("npm/lib/", "npm/package.json"))
    gate.unbumped(
        [CORE, root], lambda tag: True, lambda tag, path: seen.append(path) or False
    )
    assert seen == [
        "packages/deepctl-core/src/",
        "src/",
        "npm/lib/",
        "npm/package.json",
    ]


@needs_git_checkout
def test_the_root_ships_the_npm_package_files() -> None:
    root = next(pkg for pkg in gate.packages(REPO) if pkg.path == ".")
    assert root.npm == (
        "npm/bin/",
        "npm/lib/",
        "npm/requirements-*.txt",
        "npm/package.json",
    )
    core = next(
        pkg for pkg in gate.packages(REPO) if pkg.path == "packages/deepctl-core"
    )
    assert core.npm == ()


def test_a_changed_npm_file_names_npm() -> None:
    root = gate.Package(".", "0.4.0", "v0.4.0", ("npm/lib/",))
    problems = gate.unbumped(
        [root], lambda tag: True, lambda tag, path: path == "npm/lib/"
    )
    assert problems == [
        ".: npm/lib/ changed since v0.4.0, but the version is still 0.4.0,"
        " so npm would keep serving the old code"
    ]


def test_npm_globs_without_a_slash_match_at_any_depth() -> None:
    # npm reads `requirements-*.txt` like .gitignore: any depth under npm/.
    assert (
        gate.git_pathspec("npm/requirements-*.txt")
        == ":(glob)npm/**/requirements-*.txt"
    )
    # An entry with a slash is anchored, and plain paths stay literal.
    assert gate.git_pathspec("npm/lib/*.js") == ":(glob)npm/lib/*.js"
    assert gate.git_pathspec("npm/bin/") == "npm/bin/"


@needs_git
def test_an_npm_glob_matches_top_level_and_nested_files(tmp_path: Path) -> None:
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

    (tmp_path / "npm").mkdir()
    (tmp_path / "npm" / "package.json").write_text("{}", encoding="utf-8")
    git("init", "-q")
    git("add", "-A")
    git("commit", "-qm", "release")
    git("tag", "v0.4.0")
    changed = gate.git_changed_since(tmp_path)
    spec = "npm/requirements-*.txt"
    for rel, expected in [
        ("npm/notes.txt", False),
        ("npm/requirements-0.4.0.txt", True),
        ("npm/vendor/requirements-0.4.0.txt", True),
    ]:
        git("reset", "-q", "--hard", "v0.4.0")
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-qm", f"add {rel}")
        assert changed("v0.4.0", spec) is expected, rel


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


def test_a_metadata_change_under_a_released_version_fails() -> None:
    problems = gate.unbumped(
        [CORE], lambda tag: True, lambda tag, path: False, lambda tag, pkg: True
    )
    assert problems == [
        "packages/deepctl-core: packaging metadata in packages/deepctl-core/pyproject.toml"
        " (dependencies, entry points or build settings) changed since"
        " deepctl-core-v0.3.0, but the version is still 0.3.0, so PyPI would keep"
        " serving the old metadata"
    ]


BASE = """\
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "deepctl"
version = "{version}"
requires-python = ">=3.10"
dependencies = ["click>=8", "deepctl-core>={floor}"]

[project.scripts]
dg = "deepctl.main:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.ruff]
line-length = {line_length}
"""


def base(version: str = "0.4.0", floor: str = "0.3.0", line_length: int = 88) -> str:
    return BASE.format(version=version, floor=floor, line_length=line_length)


def test_version_floors_and_lint_settings_are_not_shipped_metadata() -> None:
    assert gate.shipped_metadata(base()) == gate.shipped_metadata(
        base(version="0.4.1", floor="0.4.0", line_length=100)
    )


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('dg = "deepctl.main:main"', 'dg = "deepctl.cli:main"'),  # entry point
        ('requires-python = ">=3.10"', 'requires-python = ">=3.11"'),
        ('where = ["src"]', 'where = ["lib"]'),  # which files go in the wheel
        ('"setuptools>=61"', '"setuptools>=70"'),  # build backend
        ('"click>=8"', '"click>=9"'),  # still a dependency change
    ],
)
def test_shipped_metadata_changes_are_caught(old: str, new: str) -> None:
    assert old in BASE
    assert gate.shipped_metadata(base()) != gate.shipped_metadata(
        base().replace(old, new)
    )


HATCH = """\
[project]
name = "deepctl"
version = "0.4.0"

[tool.hatch.build.targets.wheel]
packages = ["src/deepctl"]

[tool.hatch.envs.default]
dependencies = ["pytest"]
"""


def test_only_hatch_build_tables_are_shipped_metadata() -> None:
    dev_env = HATCH.replace('["pytest"]', '["pytest", "coverage"]')
    assert gate.shipped_metadata(HATCH) == gate.shipped_metadata(dev_env)
    wheel = HATCH.replace('["src/deepctl"]', '["src/deepctl", "src/extra"]')
    assert gate.shipped_metadata(HATCH) != gate.shipped_metadata(wheel)


@needs_git
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
    assert "packaging metadata in packages/deepctl-core/pyproject.toml" in result.stderr
    assert "changed since deepctl-core-v0.3.0" in result.stderr

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


@needs_git
def test_the_npm_package_and_root_metadata_against_a_real_git_repository(
    tmp_path: Path,
) -> None:
    """The root's npm files and pyproject metadata need a root bump too."""

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

    def commit(message: str) -> None:
        git("add", "-A")
        git("commit", "-qm", message)

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(tmp_path)], capture_output=True, text=True
        )

    def package_json(version: str, engines: str = ">=20") -> str:
        return json.dumps(
            {
                "name": "@deepgram/cli",
                "version": version,
                "files": ["bin/", "lib/", "requirements-*.txt"],
                "engines": {"node": engines},
            }
        )

    root_only = {"packages": {".": CONFIG["packages"]["."]}}
    write(".github/release-please-config.json", json.dumps(root_only))
    write(".github/.release-please-manifest.json", json.dumps({".": "0.4.0"}))
    write("pyproject.toml", base())
    write("src/deepctl/__init__.py", "")
    write("npm/package.json", package_json("0.4.0"))
    write("npm/bin/dg.js", "require('../lib/launcher');\n")
    write("npm/lib/launcher.js", "module.exports = {};\n")
    git("init", "-q")
    commit("release")
    git("tag", "v0.4.0")
    assert run().returncode == 0, run().stderr

    # npm docs and the npm package's tests do not ship as code: no bump needed.
    write("npm/README.md", "docs\n")
    write("npm/test/launcher.test.js", "// test\n")
    commit("docs(npm): readme")
    assert run().returncode == 0, run().stderr

    # A chore to the launcher under the released version: the B3 trap.
    write("npm/lib/launcher.js", "module.exports = { x: 1 };\n")
    commit("chore(npm): tidy the launcher")
    result = run()
    assert result.returncode == 1
    assert "npm/lib/ changed since v0.4.0" in result.stderr
    assert "so npm would keep serving the old code" in result.stderr
    git("revert", "--no-edit", "HEAD")
    assert run().returncode == 0, run().stderr

    # The launcher's entry script, under bin/.
    write("npm/bin/dg.js", "require('../lib/launcher').main();\n")
    commit("chore(npm): call main")
    result = run()
    assert result.returncode == 1
    assert "npm/bin/ changed since v0.4.0" in result.stderr
    git("revert", "--no-edit", "HEAD")
    assert run().returncode == 0, run().stderr

    # A committed requirements file matches the `files` glob, so it ships.
    write("npm/requirements-0.4.0.txt", "deepctl==0.4.0\n")
    commit("chore(npm): pin requirements")
    result = run()
    assert result.returncode == 1
    assert "npm/requirements-*.txt changed since v0.4.0" in result.stderr
    git("revert", "--no-edit", "HEAD")
    assert run().returncode == 0, run().stderr

    # A package.json change other than the version.
    write("npm/package.json", package_json("0.4.0", engines=">=22"))
    commit("chore(npm): raise engines")
    result = run()
    assert result.returncode == 1
    assert "npm/package.json changed since v0.4.0" in result.stderr
    git("revert", "--no-edit", "HEAD")
    assert run().returncode == 0, run().stderr

    # A root entry point changed under the released version.
    write("pyproject.toml", base().replace("deepctl.main:main", "deepctl.cli:main"))
    commit("refactor: move main")
    result = run()
    assert result.returncode == 1
    assert "packaging metadata in pyproject.toml" in result.stderr

    # The release PR: root version and npm/package.json bumped together. The
    # new version has no tag yet, so everything above ships with it.
    write(
        "pyproject.toml",
        base(version="0.4.1").replace("deepctl.main:main", "deepctl.cli:main"),
    )
    write("npm/package.json", package_json("0.4.1"))
    write("npm/lib/launcher.js", "module.exports = { x: 2 };\n")
    commit("chore: release 0.4.1")
    assert run().returncode == 0, run().stderr


@needs_git
def test_a_repository_without_tags_fails_instead_of_passing(tmp_path: Path) -> None:
    """A shallow checkout has no tags; that must not read as "first release"."""
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    with pytest.raises(RuntimeError, match="no tags"):
        gate.git_tag_exists(tmp_path)


@needs_git
def test_not_a_repository_fails(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        gate.git_tag_exists(tmp_path)("v1.0.0")
