"""Shared harness for dg init's command tests: a fake clone (a real git
repository when git is installed), recorded setup and start steps.

The tests import it and define their own fixtures; a conftest.py in this
directory would clash with other packages' tests/unit/conftest.py."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock, patch

import pytest
from deepctl_cmd_init import clones, lifecycle
from deepctl_cmd_init.command import InitCommand
from deepctl_cmd_init.models import LifecycleStep, TemplateDetail, TomlConfig
from deepctl_core import AuthManager, Config, DeepgramClient

if TYPE_CHECKING:
    from pathlib import Path

HAVE_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not HAVE_GIT, reason="git not installed")


def git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.com",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.com",
    )
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    ).stdout.strip()


REPO_URL = "https://github.com/deepgram-starters/node-transcription"


def template_detail(
    *,
    install: str | list[str] | None = None,
    start: str | None = None,
    url: str = REPO_URL,
    name: str = "node-transcription",
) -> TemplateDetail:
    config = None
    if install is not None or start is not None:
        config = TomlConfig(
            install=LifecycleStep(command=install) if install is not None else None,
            start=LifecycleStep(command=start) if start is not None else None,
        )
    return TemplateDetail(
        name=name,
        title="Node Transcription",
        description="Get started with Node",
        language="javascript",
        links={"github": url},
        config=config,
    )


class Run:
    """Drive InitCommand.handle with a fake clone and recorded steps."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.dest = tmp_path / "app"
        # dg's state directory (the clone records) lives under HOME.
        self.home = tmp_path / "home"
        self.home.mkdir()
        monkeypatch.setenv("HOME", str(self.home))
        self.makefile = False
        self.clones = 0
        self.setup_runs: list[Any] = []
        self.start_runs: list[Any] = []
        self.person = False
        self.agent = False
        monkeypatch.setattr(lifecycle, "check_prereqs", lambda: [])
        monkeypatch.setattr(lifecycle, "clone_template", self._clone)
        monkeypatch.setattr(lifecycle, "run_make_init", self._make)
        monkeypatch.setattr(lifecycle, "run_lifecycle_step", self._step)
        monkeypatch.setattr("deepctl_core.transport.prog", lambda: "dg")
        monkeypatch.setattr(
            "deepctl_cmd_init.command._person_at_terminal",
            lambda: self.person,
            raising=False,
        )
        monkeypatch.setattr(
            "deepctl_cmd_init.command._hard_signal", lambda: self.agent, raising=False
        )
        self.detail = template_detail()

    def _clone(self, url: str, dest: Path) -> None:
        """A clone: a real git repository with ``origin`` set when git is
        installed (a resume checks both), else a plain directory."""
        assert not lifecycle.directory_in_use(dest)
        self.clones += 1
        dest.mkdir(exist_ok=True)
        (dest / "README.md").write_text("starter\n")
        if self.makefile:
            (dest / "Makefile").write_text("init:\n")
        if HAVE_GIT:
            git(dest, "init", "-q")
            git(dest, "add", "-A")
            git(dest, "commit", "-q", "-m", "init")
            git(dest, "remote", "add", "origin", url)
        else:
            (dest / ".git").mkdir()

    def _make(self, dest: Path) -> bool:  # noqa: ARG002
        self.setup_runs.append("make")
        return True

    def _step(self, step: LifecycleStep, dest: Path) -> bool:  # noqa: ARG002
        assert self.detail.config is not None
        if step is self.detail.config.start:
            self.start_runs.append(step.command)
        else:
            self.setup_runs.append(step.command)
        return True

    def __call__(self, api_key: str | None = None, **flags: Any) -> Any:
        kwargs = {
            "template": self.detail.name,
            "dir": str(self.dest),
            "search": None,
            "list": False,
            "install": False,
            "no_install": False,
            "start": False,
            "no_start": False,
            "yes": False,
            **flags,
        }
        auth = Mock(spec=AuthManager)
        auth.get_api_key.return_value = api_key
        with patch(
            "deepctl_cmd_init.command.templates_api.get_template",
            return_value=self.detail,
        ):
            return InitCommand().handle(
                config=Mock(spec=Config),
                auth_manager=auth,
                client=Mock(spec=DeepgramClient),
                **kwargs,
            )

    def record(self) -> Any:
        """dg's record of the clone in ``dest``, or None."""
        return clones.load(os.path.realpath(self.dest))
