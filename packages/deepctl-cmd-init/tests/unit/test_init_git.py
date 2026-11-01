"""Every git command dg init runs gets settings that keep configuration
inside a repository from running a program."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import TYPE_CHECKING, Any

import pytest
from deepctl_cmd_init import lifecycle

if TYPE_CHECKING:
    from pathlib import Path

KEY = "fa1ce0000000000000000000000000000000000d"


def _git(repo: Path, *args: str) -> str:
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


def test_every_git_command_gets_the_safe_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def fake(argv: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(lifecycle.shutil, "which", lambda name: "/usr/bin/git")
    monkeypatch.setattr(lifecycle.subprocess, "run", fake)
    lifecycle.run_git("status", repo=tmp_path)
    lifecycle.clone_template("https://github.com/deepgram/x", tmp_path / "c")
    lifecycle.ensure_env_gitignored(tmp_path)
    assert len(calls) >= 3
    for argv in calls:
        assert argv[:2] == ["git", "--no-pager"]
        assert "core.fsmonitor=false" in argv
        assert any(a.startswith("core.hooksPath=") for a in argv)
        assert "core.pager=cat" in argv


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_repository_config_runs_nothing(tmp_path: Path) -> None:
    """A repository whose own config names programs: writing .env (which
    asks git whether .env is tracked and ignored) runs none of them."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "x")
    flag = tmp_path / "ran"
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    for name in ("post-index-change", "reference-transaction", "pre-auto-gc"):
        hook = hooks / name
        hook.write_text(f"#!/bin/sh\ntouch {flag}-{name}\n")
        hook.chmod(0o755)
    for key, value in (
        ("core.fsmonitor", f"touch {flag}-fsmonitor; false"),
        ("core.hooksPath", str(hooks)),
        ("core.pager", f"touch {flag}-pager"),
        ("diff.external", f"touch {flag}-diff"),
    ):
        _git(repo, "config", key, value)
    lifecycle.inject_env(repo, KEY)
    assert (repo / ".env").exists()
    assert lifecycle.run_git("status", "--porcelain", repo=repo) is not None
    assert not list(tmp_path.glob("ran-*"))


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_without_the_settings_fsmonitor_would_run(tmp_path: Path) -> None:
    """Control: the same config does run code when git runs without them."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "x")
    flag = tmp_path / "ran-fsmonitor"
    _git(repo, "config", "core.fsmonitor", f"touch {flag}; false")
    subprocess.run(["git", "-C", str(repo), "status"], capture_output=True)
    assert flag.exists()


@pytest.mark.skipif(
    shutil.which("git") is None or not hasattr(os, "mkfifo"), reason="git, FIFOs"
)
def test_excludes_file_fifo_does_not_hang(tmp_path: Path) -> None:
    """core.excludesFile naming a FIFO: the .env check doesn't block on it."""
    import threading

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    fifo = tmp_path / "excludes"
    os.mkfifo(fifo)
    _git(repo, "config", "core.excludesFile", str(fifo))
    done = threading.Event()

    def work() -> None:
        lifecycle.ensure_env_gitignored(repo)
        done.set()

    threading.Thread(target=work, daemon=True).start()
    assert done.wait(15), "check-ignore blocked on the excludes FIFO"
    # Unblock a stuck reader, if any, so the test process can exit.
    if not done.is_set():
        with open(fifo, "w"):
            pass
