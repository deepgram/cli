"""dg init's rerun contract: a rerun into a clone dg made resumes there, by
dg's own record of it; any other directory is refused untouched, and no git
command runs in it."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from deepctl_cmd_init import clones, lifecycle
from deepctl_cmd_init.models import InitResult

# The shared harness, loaded by path under its own name: a relative import
# depends on which package pytest's importlib mode names "tests.unit".
_HARNESS = "deepctl_cmd_init_tests_harness"
if _HARNESS not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        _HARNESS, Path(__file__).with_name("_init_harness.py")
    )
    assert _spec is not None and _spec.loader is not None
    sys.modules[_HARNESS] = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(sys.modules[_HARNESS])
Run = sys.modules[_HARNESS].Run
git = sys.modules[_HARNESS].git
template_detail = sys.modules[_HARNESS].template_detail

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Run:
    return Run(tmp_path, monkeypatch)


@pytest.fixture
def make_detail() -> Any:
    """``make_detail(install=..., start=..., url=..., name=...)``."""
    return template_detail


@pytest.fixture
def git_cmd() -> Any:
    """``git_cmd(repo, *args)``: run git with a clean environment."""
    return git


KEY = "fa1ce0000000000000000000000000000000000d"
FORGED_MAKEFILE = "check-prereqs:\n\t@true\ninit:\n\ttouch {flag}\n"


def _assert_untouched(run: Any, env_before: str | None = None) -> None:
    assert run.clones == 0
    assert run.setup_runs == run.start_runs == []
    env = run.dest / ".env"
    if env_before is None:
        assert not env.exists()
    else:
        assert env.read_text() == env_before


@needs_git
class TestResume:
    def test_rerun_resumes_without_cloning_again(self, run: Any) -> None:
        run.makefile = True
        assert run(no_install=True).status == "success"
        record = run.record()
        assert record is not None and record.phases == ["clone"]
        assert len(record.commit) == 40
        assert record.url == run.detail.links["github"]
        result = run(no_install=True)
        assert isinstance(result, InitResult)
        assert result.status == "success"
        assert run.clones == 1

    def test_finished_setup_is_not_run_again(self, run: Any) -> None:
        run.makefile = True
        run(install=True, no_start=True)
        run(install=True, no_start=True)
        assert run.setup_runs == ["make"]
        assert run.record().phases == ["clone", "setup"]

    def test_other_template_is_invalid_argument(
        self, run: Any, make_detail: Any
    ) -> None:
        run(no_install=True)
        run.detail = make_detail(
            name="flask-transcription",
            url="https://github.com/deepgram-starters/flask-transcription",
        )
        result = run(no_install=True)
        assert result.code == "invalid_argument"
        assert "flask-transcription" in result.error
        assert result.next_step == "dg init --help"
        assert run.clones == 1

    def test_nothing_is_written_into_the_clone(self, run: Any, git_cmd: Any) -> None:
        run(no_install=True)
        assert git_cmd(run.dest, "status", "--porcelain") == ""

    def test_clone_moved_on_is_not_resumed(self, run: Any, git_cmd: Any) -> None:
        run(no_install=True)
        git_cmd(run.dest, "commit", "-q", "--allow-empty", "-m", "later")
        assert run(api_key=KEY, install=True).code == "invalid_argument"
        assert run.setup_runs == []

    def test_record_for_another_path_does_not_count(
        self, run: Any, tmp_path: Path
    ) -> None:
        run(no_install=True)
        moved = tmp_path / "moved"
        run.dest.rename(moved)
        run.dest = moved
        assert run(api_key=KEY, install=True).code == "invalid_argument"
        assert not (moved / ".env").exists()


def _record_git(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Every git command dg runs from here on (and still runs it)."""
    calls: list[list[str]] = []
    real_run = subprocess.run

    def recording(argv: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(list(argv))
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(lifecycle.subprocess, "run", recording)
    return calls


@needs_git
class TestRecordedDirectoryReplaced:
    """The record names a directory, not just a path: one made again at the
    path, one owned by someone else, or a link swapped in is refused before
    any git command runs in it."""

    def _clone(self, run: Any) -> None:
        assert run(no_install=True).status == "success"
        assert run.record() is not None

    def _refused(self, run: Any, calls: list[list[str]], **flags: Any) -> None:
        result = run(api_key=KEY, **flags)
        assert result.code == "invalid_argument"
        assert run.setup_runs == run.start_runs == []
        assert not [c for c in calls if "-C" in c], calls

    def test_made_again_at_the_same_path(
        self, run: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._clone(run)
        copy = tmp_path / "copy"
        shutil.copytree(run.dest, copy, symlinks=True)
        shutil.rmtree(run.dest)
        copy.rename(run.dest)  # same contents and HEAD, another inode
        calls = _record_git(monkeypatch)
        self._refused(run, calls, install=True)
        assert not (run.dest / ".env").exists()

    def test_owned_by_someone_else(
        self, run: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._clone(run)
        uid = os.geteuid()
        monkeypatch.setattr(lifecycle.os, "geteuid", lambda: uid + 1)
        calls = _record_git(monkeypatch)
        self._refused(run, calls, install=True)
        assert not (run.dest / ".env").exists()

    def test_link_swapped_in_after_the_record_check(
        self, run: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._clone(run)
        real = tmp_path / "real"
        real_load = clones.load

        def load_then_swap(path: str) -> Any:
            record = real_load(path)
            run.dest.rename(real)
            run.dest.symlink_to(real)
            return record

        monkeypatch.setattr(clones, "load", load_then_swap)
        calls = _record_git(monkeypatch)
        self._refused(run, calls, install=True, yes=True)
        assert not (real / ".env").exists()


class TestDirectoryNotFromInit:
    def test_non_empty_directory_without_record(self, run: Any) -> None:
        run.dest.mkdir()
        (run.dest / "notes.txt").write_text("mine\n")
        result = run(no_install=True)
        assert result.code == "invalid_argument"
        assert result.error.startswith("Directory already exists: ")
        assert run.clones == 0

    def test_empty_directory_is_cloned_into(self, run: Any) -> None:
        run.dest.mkdir()
        assert run(no_install=True).status == "success"
        assert run.clones == 1

    def test_a_file_in_the_way(self, run: Any) -> None:
        run.dest.write_text("x")
        assert run(no_install=True).code == "invalid_argument"


def _forge(dest: Path, tmp_path: Path, git_cmd: Any, flag: Path) -> None:
    """What an archive or a "fork this starter" zip can deliver: its own
    .git with origin set to the Deepgram repository, HEAD committed, and a
    0600 .deepctl-init.json naming HEAD (the marker earlier versions
    trusted). Built elsewhere and unpacked from a tarball."""
    work = tmp_path / "forge" / "node-transcription"
    work.mkdir(parents=True)
    (work / "Makefile").write_text(FORGED_MAKEFILE.format(flag=flag))
    git_cmd(work, "init", "-q")
    git_cmd(work, "add", "-A")
    git_cmd(work, "commit", "-q", "-m", "x")
    git_cmd(work, "remote", "add", "origin", template_detail().links["github"])
    head = git_cmd(work, "rev-parse", "HEAD")
    marker = work / ".deepctl-init.json"
    marker.write_text(
        json.dumps(
            {"template": "node-transcription", "commit": head, "phases": ["clone"]}
        )
    )
    marker.chmod(0o600)
    archive = tmp_path / "forge.tar"
    with tarfile.open(archive, "w") as tar:
        tar.add(work, arcname=".")
    dest.mkdir()
    with tarfile.open(archive) as tar:
        tar.extractall(dest, filter="tar")  # type: ignore[call-arg]


@needs_git
class TestForgedClone:
    """A self-consistent forged clone gets nothing done under an AI agent:
    no .env, no setup, no start, and no git command in it."""

    @pytest.fixture(autouse=True)
    def _agent(self, run: Any) -> None:
        run.agent = True

    @pytest.mark.parametrize(
        "flags",
        [{}, {"yes": True}, {"yes": True, "start": True}, {"install": True}],
    )
    def test_refused(
        self,
        run: Any,
        git_cmd: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        make_detail: Any,
        flags: dict[str, Any],
    ) -> None:
        run.detail = make_detail(install="npm install", start="npm start")
        flag = tmp_path / "pwned"
        _forge(run.dest, tmp_path, git_cmd, flag)
        calls: list[list[str]] = []
        real_run = subprocess.run

        def recording(argv: Any, *args: Any, **kwargs: Any) -> Any:
            calls.append(list(argv))
            return real_run(argv, *args, **kwargs)

        monkeypatch.setattr(lifecycle.subprocess, "run", recording)
        result = run(api_key=KEY, **flags)
        assert result.code == "invalid_argument"
        assert result.error.startswith("Directory already exists: ")
        _assert_untouched(run)
        assert not flag.exists()
        assert not [c for c in calls if str(run.dest) in " ".join(c)], calls


class TestRecord:
    @pytest.fixture(autouse=True)
    def _home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path / "home"))

    def _record(self, path: str = "/x/app") -> Any:
        return clones.CloneRecord(
            path, "t", "https://github.com/deepgram/t", "a" * 40, ["clone"], 1, 2
        )

    def _file(self, path: str = "/x/app") -> Path:
        name = clones._name(path) + ".json"
        return Path(os.environ["HOME"]) / ".deepctl" / clones.DIR_NAME / name

    def test_round_trip_private(self) -> None:
        clones.save(self._record())
        assert clones.load("/x/app") == self._record()
        assert clones.load("/x/other") is None
        if os.name == "posix":
            assert stat.S_IMODE(self._file().stat().st_mode) == 0o600
            assert stat.S_IMODE(self._file().parent.stat().st_mode) == 0o700

    @pytest.mark.skipif(os.name != "posix", reason="POSIX modes")
    def test_loose_mode_is_ignored(self) -> None:
        clones.save(self._record())
        self._file().chmod(0o644)
        assert clones.load("/x/app") is None

    @pytest.mark.skipif(os.name != "posix", reason="POSIX modes")
    def test_loose_directory_is_ignored(self) -> None:
        clones.save(self._record())
        self._file().parent.chmod(0o755)
        assert clones.load("/x/app") is None

    def test_link_is_not_followed(self, tmp_path: Path) -> None:
        clones.save(self._record())
        real = tmp_path / "real.json"
        self._file().rename(real)
        self._file().symlink_to(real)
        assert clones.load("/x/app") is None

    def test_save_replaces_a_link_without_writing_through_it(
        self, tmp_path: Path
    ) -> None:
        clones.save(self._record())
        outside = tmp_path / "outside"
        outside.write_text("keep\n")
        self._file().unlink()
        self._file().symlink_to(outside)
        clones.save(self._record())
        assert outside.read_text() == "keep\n"
        assert not self._file().is_symlink()

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs FIFOs")
    def test_fifo_is_not_read(self) -> None:
        clones.save(self._record())
        self._file().unlink()
        os.mkfifo(self._file())
        assert clones.load("/x/app") is None

    @pytest.mark.parametrize(
        "data",
        [
            [],
            {
                "path": "/x/other",
                "template": "t",
                "url": "u",
                "commit": "a" * 40,
                "phases": [],
                "dev": 1,
                "ino": 2,
            },
            {
                "path": "/x/app",
                "template": 1,
                "url": "u",
                "commit": "a" * 40,
                "phases": [],
                "dev": 1,
                "ino": 2,
            },
            {
                "path": "/x/app",
                "template": "t",
                "url": "u",
                "commit": "HEAD",
                "phases": [],
                "dev": 1,
                "ino": 2,
            },
            {
                "path": "/x/app",
                "template": "t",
                "url": "u",
                "commit": "a" * 40,
                "phases": [1],
                "dev": 1,
                "ino": 2,
            },
            {
                "path": "/x/app",
                "template": "t",
                "url": "u",
                "commit": "a" * 40,
                "phases": [],
                "ino": 2,
            },
            {
                "path": "/x/app",
                "template": "t",
                "url": "u",
                "commit": "a" * 40,
                "phases": [],
                "dev": True,
                "ino": 2,
            },
        ],
    )
    def test_invalid_content_is_ignored(self, data: Any) -> None:
        clones.save(self._record())
        self._file().write_text(json.dumps(data))
        assert clones.load("/x/app") is None
