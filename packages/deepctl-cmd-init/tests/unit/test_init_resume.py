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
import types
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from deepctl_cmd_init import clones, lifecycle
from deepctl_core import login_state
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


def _rmtree(path: Path) -> None:
    """``shutil.rmtree`` that also removes read-only files, as git leaves
    its objects on Windows: the read-only bit is cleared and the removal
    tried again. ``onexc`` is 3.12+, and ``onerror`` is deprecated there."""

    def clear_and_retry(func: Any, name: str, _exc: Any) -> None:
        os.chmod(name, stat.S_IWRITE)
        func(name)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=clear_and_retry)
    else:
        shutil.rmtree(path, onerror=clear_and_retry)


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
        _rmtree(run.dest)
        copy.rename(run.dest)  # same contents and HEAD, another inode
        calls = _record_git(monkeypatch)
        self._refused(run, calls, install=True)
        assert not (run.dest / ".env").exists()

    @pytest.mark.skipif(os.name != "posix", reason="POSIX owners")
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


def _swap(dest: Path, aside: Path) -> None:
    """Put another directory, with the same contents, at ``dest``."""
    shutil.copytree(dest, aside, symlinks=True)
    dest.rename(aside.with_name(aside.name + "-old"))
    aside.rename(dest)


@needs_git
class TestSwappedWhileRunning:
    """The directory is checked again right before the API key is written
    and before each setup or start command: one swapped in after the
    earlier checks is refused, and nothing more is done in it."""

    def _assert_changed(self, run: Any, result: Any) -> None:
        assert result.code == "invalid_argument"
        assert result.error.endswith(
            "changed while dg init was running, so nothing more was done there."
        )
        assert result.next_step == "dg init --help"

    def test_swapped_after_the_clone(
        self, run: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real_save = clones.save

        def save_then_swap(record: Any) -> None:
            real_save(record)
            _swap(run.dest, tmp_path / "other")

        monkeypatch.setattr(clones, "save", save_then_swap)
        run.makefile = True
        result = run(api_key=KEY, install=True, no_start=True)
        self._assert_changed(run, result)
        assert not (run.dest / ".env").exists()
        assert not (tmp_path / "other-old" / ".env").exists()
        assert run.setup_runs == []

    def test_swapped_before_setup_on_a_resume(
        self, run: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run.makefile = True
        assert run(no_install=True).status == "success"
        real_inject = lifecycle.inject_env

        def inject_then_swap(dest: Path, api_key: str) -> None:
            real_inject(dest, api_key)
            _swap(dest, tmp_path / "other")

        monkeypatch.setattr(lifecycle, "inject_env", inject_then_swap)
        result = run(api_key=KEY, install=True, no_start=True)
        self._assert_changed(run, result)
        assert run.setup_runs == []
        assert run.record().phases == ["clone"]

    def test_swapped_before_start(
        self,
        run: Any,
        tmp_path: Path,
        make_detail: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run.detail = make_detail(install="npm install", start="npm start")

        def make_then_swap(step: Any, dest: Path) -> bool:
            run.setup_runs.append(step.command)
            _swap(dest, tmp_path / "other")
            return True

        monkeypatch.setattr(lifecycle, "run_lifecycle_step", make_then_swap)
        result = run(api_key=KEY, yes=True, start=True)
        self._assert_changed(run, result)
        assert run.setup_runs == ["npm install"]
        assert run.start_runs == []


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
        _extract(tar, dest)


def _extract(tar: tarfile.TarFile, dest: Path) -> None:
    """extractall() with the "tar" filter where the runtime has one. Python
    3.10 builds before 3.10.12 (setup-python's macOS 3.10) have no filter
    argument, so check every member by hand there: only directories and
    regular files, all inside dest."""
    if hasattr(tarfile, "tar_filter"):
        tar.extractall(dest, filter="tar")  # type: ignore[call-arg,unused-ignore]
        return
    root = dest.resolve()
    for member in tar.getmembers():
        target = (root / member.name).resolve()
        if not (member.isdir() or member.isreg()) or not target.is_relative_to(root):
            raise ValueError(f"unsafe tar member: {member.name!r}")
        tar.extract(member, root)


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


def _windows_directories(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows' rules on any OS: a directory can't be opened (os.open raises
    PermissionError, as on Windows) and there is no O_NOFOLLOW."""
    real_open = os.open

    def open_(path: Any, flags: int, mode: int = 0o777, *, dir_fd: Any = None) -> int:
        assert dir_fd is None, "no directory descriptors on Windows"
        if os.path.isdir(path):
            raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, flags, mode)

    monkeypatch.setattr(login_state, "DIR_FDS", False)
    monkeypatch.setattr(login_state, "_O_NOFOLLOW", 0)
    monkeypatch.setattr(clones, "_NOFOLLOW", 0)
    monkeypatch.setattr(os, "open", open_)


class TestRecord:
    @pytest.fixture(autouse=True)
    def _home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))

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


class TestRecordWithoutDirectoryHandles(TestRecord):
    """The same contract where a directory can't be opened (Windows)."""

    @pytest.fixture(autouse=True)
    def _windows(self, _home: None, monkeypatch: pytest.MonkeyPatch) -> None:
        _windows_directories(monkeypatch)

    @pytest.mark.skip(reason="Windows has no POSIX directory modes to check")
    def test_loose_directory_is_ignored(self) -> None:
        pass


@needs_git
class TestResumeWithoutDirectoryHandles(TestResume):
    """A rerun resumes where a directory can't be opened (Windows): the
    record is written and read by path."""

    @pytest.fixture(autouse=True)
    def _windows(self, run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        _windows_directories(monkeypatch)


@needs_git
class TestRecordedDirectoryReplacedWithoutDirectoryHandles(
    TestRecordedDirectoryReplaced
):
    """The same refusals where a directory can't be opened (Windows): the
    directory's volume and file ID, read by path, must be the recorded
    ones. Only dg's switch is turned off here, not the whole of
    ``_windows_directories``: the test's own ``shutil.rmtree`` opens
    directories by descriptor on POSIX, which that forbids."""

    @pytest.fixture(autouse=True)
    def _windows(self, run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(login_state, "DIR_FDS", False)

    @pytest.mark.skip(reason="Windows has no POSIX owners to check")
    def test_owned_by_someone_else(self) -> None:
        pass


@needs_git
class TestSwappedWhileRunningWithoutDirectoryHandles(TestSwappedWhileRunning):
    """A directory swapped in while dg init runs is refused where a
    directory can't be opened (Windows)."""

    @pytest.fixture(autouse=True)
    def _windows(self, run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        _windows_directories(monkeypatch)


class TestSameDirectoryWithoutDirectoryHandles:
    """``same_directory`` where a directory can't be opened (Windows): the
    volume and file ID ``lstat`` gives are compared with the recorded ones,
    and a file ID of 0 (now or recorded) is unknown, not a mismatch."""

    @pytest.fixture(autouse=True)
    def _windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _windows_directories(monkeypatch)

    @pytest.fixture
    def dest(self, tmp_path: Path) -> Path:
        dest = tmp_path / "app"
        (dest / ".git").mkdir(parents=True)
        return dest

    def _identity(self, dest: Path) -> tuple[int, int]:
        identity = lifecycle.directory_identity(dest)
        assert identity is not None
        return identity

    def _no_file_id(self, monkeypatch: pytest.MonkeyPatch, dest: Path) -> None:
        """``lstat`` of ``dest`` reports file ID 0, as some file systems do."""
        real_lstat = os.lstat

        def lstat(path: Any, *args: Any, **kwargs: Any) -> Any:
            st = real_lstat(path, *args, **kwargs)
            if os.fspath(path) != os.fspath(dest):
                return st
            return types.SimpleNamespace(st_mode=st.st_mode, st_dev=st.st_dev, st_ino=0)

        monkeypatch.setattr(os, "lstat", lstat)

    def test_still_the_same_after_files_change_inside(self, dest: Path) -> None:
        identity = self._identity(dest)
        (dest / "notes.txt").write_text("x\n")
        (dest / "src").mkdir()
        (dest / "src" / "a.js").write_text("a\n")
        (dest / "notes.txt").rename(dest / "src" / "notes.txt")
        (dest / ".git" / "index").write_text("i\n")
        assert lifecycle.same_directory(dest, *identity)

    def test_made_again_at_the_same_path(self, dest: Path, tmp_path: Path) -> None:
        identity = self._identity(dest)
        copy = tmp_path / "copy"
        shutil.copytree(dest, copy)
        dest.rename(tmp_path / "old")
        copy.rename(dest)  # same contents, another file ID
        assert not lifecycle.same_directory(dest, *identity)

    def test_another_file_id_is_refused(self, dest: Path) -> None:
        dev, ino = self._identity(dest)
        assert not lifecycle.same_directory(dest, dev, ino + 1)

    def test_another_volume_is_refused(self, dest: Path) -> None:
        dev, ino = self._identity(dest)
        assert not lifecycle.same_directory(dest, dev + 1, ino)

    def test_no_file_id_now_is_unknown(
        self, dest: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dev, ino = self._identity(dest)
        self._no_file_id(monkeypatch, dest)
        assert lifecycle.same_directory(dest, dev, ino)
        assert not lifecycle.same_directory(dest, dev + 1, ino)

    def test_no_file_id_recorded_is_unknown(self, dest: Path) -> None:
        dev, _ = self._identity(dest)
        assert lifecycle.same_directory(dest, dev, 0)
        assert not lifecycle.same_directory(dest, dev + 1, 0)


@needs_git
class TestAgentRecordsUnderTheRealHome:
    """In an AI agent session the records live under the account's own
    home, never $HOME, which the agent can point anywhere."""

    @pytest.fixture
    def real_home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        from deepctl_core import config as config_module

        real = tmp_path / "real-home"
        real.mkdir()
        monkeypatch.setattr(config_module, "_real_home", lambda: str(real))
        return real

    def _agent(self, run: Any, monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
        from deepctl_core import output

        run.agent = on
        monkeypatch.setattr(output, "is_ai_agent", lambda: on)

    def test_a_record_under_home_is_not_used(
        self, run: Any, real_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run.makefile = True
        assert run(no_install=True).status == "success"  # a person's clone
        assert (run.home / ".deepctl" / clones.DIR_NAME).is_dir()
        self._agent(run, monkeypatch, True)
        result = run(api_key=KEY, install=True)
        assert result.code == "invalid_argument"
        assert run.setup_runs == []

    def test_the_agent_resumes_its_own_clone(
        self, run: Any, real_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run.makefile = True
        self._agent(run, monkeypatch, True)
        assert run(no_install=True).status == "success"
        assert (real_home / ".deepctl" / clones.DIR_NAME).is_dir()
        assert not (run.home / ".deepctl").exists()
        assert run(no_install=True).status == "success"
        assert run.clones == 1
