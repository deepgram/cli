"""deepctl_core's public names load lazily and still all resolve.

``import deepctl_core`` imports no submodule; each name imports only the
submodule that defines it. Fresh interpreters, so earlier tests' imports
don't hide anything.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import deepctl_core


def _fresh(code: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["DEEPCTL_TELEMETRY_DISABLED"] = "1"
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def _modules_after(statement: str) -> set[str]:
    proc = _fresh(
        f"""
        import json, sys
        {statement}
        print(json.dumps(sorted(sys.modules)))
        """
    )
    assert proc.returncode == 0, proc.stderr
    return set(json.loads(proc.stdout))


def test_every_public_name_resolves_to_its_definition() -> None:
    for name in deepctl_core.__all__:
        value = getattr(deepctl_core, name)
        module = importlib.import_module(
            f"deepctl_core.{deepctl_core._LAZY_NAMES[name]}"
        )
        assert value is getattr(module, name), name
    assert set(deepctl_core._LAZY_NAMES) == set(deepctl_core.__all__)


def test_star_import_resolves_everything() -> None:
    proc = _fresh(
        """
        import json
        from deepctl_core import *
        import deepctl_core
        missing = [n for n in deepctl_core.__all__ if n not in globals()]
        print(json.dumps(missing))
        """
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == []


def test_dir_lists_the_public_names_before_use() -> None:
    proc = _fresh(
        """
        import json, deepctl_core
        print(json.dumps(dir(deepctl_core)))
        """
    )
    names = set(json.loads(proc.stdout))
    assert set(deepctl_core.__all__) <= names
    assert {"config", "output"} <= names


def test_unknown_name_is_an_attribute_error() -> None:
    with pytest.raises(AttributeError, match="no attribute 'NoSuchName'"):
        deepctl_core.NoSuchName  # noqa: B018
    with pytest.raises(ImportError):
        exec("from deepctl_core import NoSuchName", {})


def test_submodules_are_attributes() -> None:
    proc = _fresh(
        """
        import deepctl_core
        print(deepctl_core.config.__name__, deepctl_core.output.__name__)
        """
    )
    assert proc.stdout.split() == ["deepctl_core.config", "deepctl_core.output"]


def test_import_deepctl_core_imports_no_submodule() -> None:
    modules = _modules_after("import deepctl_core")
    assert {m for m in modules if m.startswith("deepctl_core.")} == set()
    assert not modules & {"click", "pydantic", "rich", "yaml", "httpx"}


@pytest.mark.parametrize(
    ("statement", "absent"),
    [
        ("import deepctl_core.client", {"deepgram", "httpx"}),
        ("import deepctl_core.auth", {"deepgram", "httpx", "keyring"}),
        ("import deepctl_core.output", {"rich"}),
        ("from deepctl_core import BaseCommand", {"deepgram", "httpx", "keyring"}),
    ],
)
def test_heavy_imports_wait_for_use(statement: str, absent: set[str]) -> None:
    modules = _modules_after(statement)
    tops = {m.split(".")[0] for m in modules}
    assert not tops & absent, sorted(tops & absent)


def test_type_checkers_see_the_names(tmp_path: Path) -> None:
    """mypy --strict on code using 10 names: no attr-defined."""
    mypy = shutil.which("mypy") or (
        shutil.which("mypy", path=os.path.dirname(sys.executable))
    )
    if not mypy:
        pytest.skip("mypy is not installed")
    code = textwrap.dedent(
        """
        from deepctl_core import (
            AuthManager, BaseCommand, BaseResult, Config, DeepgramClient,
            ErrorResult, PluginManager, TimingContext, print_error, setup_output,
        )

        def use(config: Config) -> None:
            auth = AuthManager(config)
            DeepgramClient(config, auth)
            PluginManager()
            ErrorResult(error="x")
            BaseResult(status="ok")
            with TimingContext("t"):
                setup_output("json")
                print_error("x")
            assert issubclass(BaseCommand, object)

        reveal_type(Config)
        """
    )
    fixture = tmp_path / "uses_core.py"
    fixture.write_text(code)
    # Run from tmp_path, so the repo's [tool.mypy] `files` setting isn't used.
    proc = subprocess.run(
        [mypy, "--strict", "--no-incremental", str(fixture)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=tmp_path,
    )
    assert "attr-defined" not in proc.stdout, proc.stdout
    assert proc.returncode == 0, proc.stdout
    # The real class, not the Any a bare module __getattr__ would give.
    assert "deepctl_core.config.Config" in proc.stdout, proc.stdout

    # A misspelled name is an error for the type checker, not a silent Any.
    typo = tmp_path / "typo.py"
    typo.write_text(
        "from deepctl_core import Confg\nimport deepctl_core\ndeepctl_core.AuthManagr\n"
    )
    proc = subprocess.run(
        [mypy, "--strict", "--no-incremental", str(typo)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=tmp_path,
    )
    assert proc.returncode == 1, proc.stdout
    assert proc.stdout.count("error:") == 2, proc.stdout
