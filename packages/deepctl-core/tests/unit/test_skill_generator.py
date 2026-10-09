"""Unit tests for skill generator module."""

import contextlib
import errno
import hashlib
import json
import multiprocessing
import os
import shutil
import stat
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from deepctl_core import skill_bundle
from deepctl_core import skill_generator as sg
from deepctl_core.skill_bundle import RepoSkill
from deepctl_core.skill_generator import (
    SkillInstallError,
    SkillOwnershipError,
    _fingerprint,
    _marker_ok,
    _msg,
    _ownership,
    _place,
    detect_ai_clis,
    get_all_generators,
    get_skills_state,
    install_conflicts,
    install_tool,
    remove_tool,
    tool_status,
)


class TestSkillsState:
    """Test state management functions."""

    def test_get_skills_state_missing_file(self, tmp_path):
        with patch("deepctl_core.skill_generator._STATE_FILE", tmp_path / "nope.json"):
            state = get_skills_state()
            assert state == {"installed_skills": {}, "auto_update": True}


# ---------------------------------------------------------------------------
# Skill folder install: fixtures and helpers
# ---------------------------------------------------------------------------

POSIX = pytest.mark.skipif(os.name == "nt", reason="POSIX-only filesystem behavior")
NT = pytest.mark.skipif(os.name != "nt", reason="Windows-only filesystem behavior")
FP_A = "sha256:" + "a" * 64
FP_B = "sha256:" + "b" * 64
REF = skill_bundle.DEFAULT_SKILLS_COMMIT


@pytest.fixture(autouse=True)
def _throwaway_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(sg, "_SKILLS_DIR", home / ".deepctl" / "skills")
    monkeypatch.setattr(sg, "_STATE_FILE", home / ".deepctl" / "skills" / "skills.json")
    monkeypatch.setenv("COLUMNS", "400")
    monkeypatch.delenv(skill_bundle.REF_ENV_VAR, raising=False)
    return home


@pytest.fixture(autouse=True)
def _pinned_output():
    """S4: pin the agentic output mode so prefixes never depend on the env."""
    from deepctl_core import output

    saved = dict(output._output_config)
    output._output_config.update(agentic=True, format="default", quiet=False)
    yield
    output._output_config.clear()
    output._output_config.update(saved)


def make_bundle(tmp, names=("api", "docs"), body="v1"):
    """A fake validated bundle: each skill has SKILL.md and references/r.md."""
    base = Path(tmp) / f"bundle-{body}"
    skills = []
    for name in names:
        folder = base / "skills" / name
        (folder / "references").mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_bytes(f"---\nname: {name}\n---\n{body}\n".encode())
        (folder / "references" / "r.md").write_bytes(f"ref {name} {body}\n".encode())
        skills.append(RepoSkill(name, folder))
    return skills


def gen(cli):
    return next(g for g in get_all_generators() if g.cli_name == cli)


def root(cli="claude"):
    return gen(cli).skills_root()


def symlink_or_skip(link, target, *, is_dir):
    try:
        Path(link).symlink_to(target, target_is_directory=is_dir)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create a symlink here: {exc}")


def rel(target, link):
    """A relative symlink target, so no test ever compares a \\\\?\\ prefix."""
    return os.path.relpath(target, Path(link).parent)


def sha_tree(path):
    """Map every entry under ``path`` (or the path itself) to a content digest."""
    path = Path(path)
    st = os.lstat(path)
    if sg._is_link(st):
        return {"": "link"}
    if not stat.S_ISDIR(st.st_mode):
        return {"": hashlib.sha256(path.read_bytes()).hexdigest()}
    out = {}
    stack = [(path, "")]
    while stack:
        where, prefix = stack.pop()
        for e in os.scandir(where):
            est = e.stat(follow_symlinks=False)
            key = prefix + e.name
            if sg._is_link(est):
                out[key] = "link"
            elif stat.S_ISDIR(est.st_mode):
                out[key] = "dir"
                stack.append((Path(e.path), key + "/"))
            elif stat.S_ISREG(est.st_mode):
                out[key] = hashlib.sha256(Path(e.path).read_bytes()).hexdigest()
            else:
                out[key] = "special"
    return out


def edit(path, text="mine\n"):
    with open(Path(path) / "SKILL.md", "ab") as f:
        f.write(text.encode())


def state_bytes():
    try:
        return sg._STATE_FILE.read_bytes()
    except FileNotFoundError:
        return None


def disk_state():
    return json.loads(sg._STATE_FILE.read_bytes())


def records(cli="claude"):
    return disk_state().get("skill_folders", {}).get(cli, {}).get("folders", {})


def write_state(state):
    sg._STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    sg._STATE_FILE.write_text(json.dumps(state), encoding="utf-8")


def staging_dirs(where):
    if not os.path.isdir(where):
        return []
    return [n for n in os.listdir(where) if n.startswith(sg._STAGING_PREFIX)]


def install(skills, cli="claude", ref=REF):
    return install_tool(gen(cli), skills, ref=ref, version="9.9.9")


def installed_copy(tmp, monkeypatch, cli, name):
    """Install ``name`` for ``cli`` in a scratch home; return the folder and its fingerprint."""
    scratch = Path(tmp) / f"scratch-{cli}-{name}"
    scratch.mkdir()
    with monkeypatch.context() as m:
        m.setattr(Path, "home", staticmethod(lambda: scratch))
        m.setattr(sg, "_STATE_FILE", scratch / "skills.json")
        install(make_bundle(tmp, (name,), body=f"copy-{cli}"), cli)
        folder = gen(cli).skills_root() / name
    return folder, _fingerprint(folder)


def wrap(monkeypatch, owner, name, before=None):
    """Patch ``owner.name`` with a wrapper that runs ``before(*args)`` first."""
    real = getattr(owner, name)

    def wrapper(*args, **kwargs):
        if before is not None:
            result = before(*args, **kwargs)
            if result is not None:
                return result
        return real(*args, **kwargs)

    monkeypatch.setattr(owner, name, wrapper)
    return real


def is_aside(src, dst):
    """True for the move-aside rename: dest -> <staging>/old/<name>."""
    d = Path(dst)
    return d.parent.name == "old" and d.parent.parent.name.startswith(
        sg._STAGING_PREFIX
    )


# ---------------------------------------------------------------------------
# B5: a destination that appears after the check is never taken over
# ---------------------------------------------------------------------------


class TestB5:
    def test_dest_created_after_install_conflicts_survives(self, tmp_path):
        skills = make_bundle(tmp_path)
        assert install_conflicts([gen("claude")], skills) == ([], [])
        api = root() / "api"
        api.mkdir(parents=True)
        (api / "user.txt").write_bytes(b"mine")
        with pytest.raises(SkillOwnershipError) as exc:
            install(skills)
        assert str(exc.value) == _msg("E1", paths=str(api))
        assert (api / "user.txt").read_bytes() == b"mine"
        assert not (api / sg._MARKER).exists()
        assert state_bytes() is None

    def test_dest_created_during_staging_survives(self, tmp_path, monkeypatch):
        skills = make_bundle(tmp_path)
        api = root() / "api"

        def racer(src, dst, *a, **k):
            if Path(dst).name == "api":
                api.mkdir(parents=True)
                (api / "user.txt").write_bytes(b"mine")

        wrap(monkeypatch, shutil, "copytree", racer)
        with pytest.raises(SkillOwnershipError) as exc:
            install(skills)
        assert str(exc.value) == _msg("E2", gen("claude"), dest=api)
        assert sha_tree(api) == {"user.txt": hashlib.sha256(b"mine").hexdigest()}
        assert "api" not in records()
        assert staging_dirs(root()) == []

    @pytest.mark.parametrize("racer", ["made", "remade"])
    def test_empty_dir_made_right_before_the_move_survives(
        self, tmp_path, monkeypatch, racer
    ):
        """Greg r1 B2: the real no-replace move refuses another process's empty dir."""
        skills = make_bundle(tmp_path)
        api, made = root() / "api", []

        def race(src, dest):
            if Path(dest) == api and not made:
                api.mkdir()
                if racer == "remade":  # Greg's case: removed and made again.
                    api.rmdir()
                    api.mkdir()
                made.append(os.lstat(api).st_ino)

        wrap(monkeypatch, sg, "_rename_excl", race)
        with pytest.raises(SkillOwnershipError) as exc:
            install(skills)
        assert str(exc.value) == _msg("E2", gen("claude"), dest=api)
        assert os.lstat(api).st_ino == made[0] and os.listdir(api) == []
        assert "api" not in disk_state().get("skill_folders", {}).get("claude", {})
        assert staging_dirs(root()) == []

    def test_empty_dir_made_right_before_the_update_move_survives(
        self, tmp_path, monkeypatch
    ):
        install(make_bundle(tmp_path))
        api, docs, made = root() / "api", root() / "docs", []
        old, docs_rec = sha_tree(api), records()["docs"]

        def race(src, dest):
            if Path(dest) == api and Path(src).parent.name == "new":
                api.mkdir()  # After the move aside, before the place.
                made.append(os.lstat(api).st_ino)

        wrap(monkeypatch, sg, "_rename_excl", race)
        with pytest.raises(SkillInstallError) as exc:
            install(make_bundle(tmp_path, body="v2"))
        aside = exc.value.leftover / "old" / "api"
        assert str(exc.value) == _msg("E6", gen("claude"), name="api", aside=aside)
        assert os.lstat(api).st_ino == made[0] and os.listdir(api) == []
        assert sha_tree(aside) == old
        assert records()["docs"] == docs_rec
        assert _ownership(docs, "claude", "docs", docs_rec) == "ok"

    def test_old_copy_put_back_after_the_racer_leaves_keeps_its_record(
        self, tmp_path, monkeypatch
    ):
        """The racer's empty dir is gone by cleanup, so the old copy goes back proven."""
        install(make_bundle(tmp_path))
        docs, real_upd = root() / "docs", sg._update_state
        old = sha_tree(docs)

        def race(src, dest):
            if Path(dest) == docs and Path(src).parent.name == "new":
                docs.mkdir()

        def settle_then_leave(mutate, *a, **k):
            real_upd(mutate, *a, **k)
            if mutate.__name__ == "settle" and os.path.isdir(docs):
                docs.rmdir()  # After the settle write, before cleanup.

        with monkeypatch.context() as m:
            wrap(m, sg, "_rename_excl", race)
            m.setattr(sg, "_update_state", settle_then_leave)
            with pytest.raises(SkillInstallError) as exc:
                install(make_bundle(tmp_path, body="v2"))
        assert str(exc.value) == _msg("E6b", gen("claude"), name="docs")
        assert str(exc.value) == (
            "docs was not updated for Claude Code because something appeared at"
            " its folder during the update, so its previous copy is back in place;"
            " check that folder, then run the command again."
        )
        assert exc.value.leftover is None and staging_dirs(root()) == []
        assert sha_tree(docs) == old
        assert _ownership(docs, "claude", "docs", records()["docs"]) == "ok"
        install(make_bundle(tmp_path, body="v3"))
        assert b"v3" in (docs / "SKILL.md").read_bytes()

    @pytest.mark.parametrize("other", ["takes the old copy", "copies it to dest"])
    def test_old_copy_not_put_back_by_cleanup_still_gives_e6(
        self, tmp_path, monkeypatch, other
    ):
        install(make_bundle(tmp_path))
        docs, real_cleanup = root() / "docs", sg._cleanup

        def race(src, dest):
            if Path(dest) == docs and Path(src).parent.name == "new":
                docs.mkdir()

        def cleanup(staging, *a):
            old = staging / "old" / "docs"
            if other == "takes the old copy":
                shutil.move(old, tmp_path / "taken")  # Gone, but not back at dest.
            else:
                docs.rmdir()
                shutil.copytree(old, docs, symlinks=True)  # Proves, but not moved.
            return real_cleanup(staging, *a)

        wrap(monkeypatch, sg, "_rename_excl", race)
        monkeypatch.setattr(sg, "_cleanup", cleanup)
        with pytest.raises(SkillInstallError) as exc:
            install(make_bundle(tmp_path, body="v2"))
        assert str(exc.value).startswith("Could not install docs for Claude Code,")
        if exc.value.leftover:
            assert os.path.isdir(exc.value.leftover / "old" / "docs")

    @pytest.mark.parametrize(
        "kind", ["empty dir", "dir", "file", "dir link", "dangling link"]
    )
    def test_place_never_replaces_anything_at_dest(self, tmp_path, kind):
        src, dest, target = tmp_path / "src", tmp_path / "dest", tmp_path / "t"
        src.mkdir()
        (src / "f").write_bytes(b"ours")
        target.mkdir()
        if kind in ("empty dir", "dir"):
            dest.mkdir()
            if kind == "dir":
                (dest / "f").write_bytes(b"theirs")
        elif kind == "file":
            dest.write_bytes(b"theirs")
        else:
            gone = target if kind == "dir link" else tmp_path / "gone"
            symlink_or_skip(dest, rel(gone, dest), is_dir=True)
        before, ino = sha_tree(dest), os.lstat(dest).st_ino
        with pytest.raises(FileExistsError):
            _place(src, dest)
        assert (sha_tree(dest), os.lstat(dest).st_ino) == (before, ino)
        assert sha_tree(src) == {"f": hashlib.sha256(b"ours").hexdigest()}
        assert os.listdir(target) == []

    @pytest.mark.parametrize("kind", ["file", "dangling link", "fifo"])
    def test_place_moves_a_non_dir_whole(self, tmp_path, kind):
        src, dest = tmp_path / "src", tmp_path / "dest"
        if kind == "file":
            src.write_bytes(b"mine")
        elif kind == "fifo":
            if not hasattr(os, "mkfifo"):
                pytest.skip("no FIFOs here")
            os.mkfifo(src)
        else:
            symlink_or_skip(src, "nowhere", is_dir=False)
        before = os.lstat(src)
        _place(src, dest)
        after = os.lstat(dest)
        assert not os.path.lexists(src)
        assert (after.st_ino, after.st_mode) == (before.st_ino, before.st_mode)
        if kind == "dangling link":
            assert os.readlink(dest) == "nowhere"
        src.write_bytes(b"again")
        with pytest.raises(FileExistsError):
            _place(src, dest)
        assert os.lstat(dest).st_ino == before.st_ino
        assert src.read_bytes() == b"again"

    def test_windows_branch_uses_one_plain_rename(self, tmp_path, monkeypatch):
        src, dest = tmp_path / "src", tmp_path / "dest"
        src.mkdir()
        calls = []
        wrap(monkeypatch, os, "rename", lambda a, b, *x, **k: calls.append((a, b)))
        monkeypatch.setattr(sg, "_WINDOWS", True)
        monkeypatch.setattr(sg.ctypes, "CDLL", None)  # Never reached on Windows.
        _place(src, dest)
        assert [(Path(a), Path(b)) for a, b in calls] == [(src, dest)]
        assert dest.is_dir()

    @pytest.mark.parametrize(
        ("code", "why"),
        [
            (errno.EINVAL, sg._NO_EXCL),
            (errno.ENOSYS, sg._NO_EXCL_SYS),  # The kernel, not the filesystem.
            (errno.EXDEV, os.strerror(errno.EXDEV)),
        ]
        + [(e, sg._NO_EXCL) for e in sorted({errno.ENOTSUP, errno.EOPNOTSUPP})],
    )
    def test_no_replace_call_errors(self, tmp_path, monkeypatch, code, why):
        src, dest = tmp_path / "src", tmp_path / "dest"
        src.mkdir()
        monkeypatch.setattr(sg, "_WINDOWS", False)
        fake = SimpleNamespace(renameat2=lambda *a: -1, renamex_np=lambda *a: -1)
        monkeypatch.setattr(sg.ctypes, "CDLL", lambda *a, **k: fake)
        monkeypatch.setattr(sg.ctypes, "get_errno", lambda: code)
        with pytest.raises(OSError) as exc:
            _place(src, dest)
        assert (exc.value.errno, exc.value.strerror) == (code, why)
        monkeypatch.setattr(sg.ctypes, "CDLL", lambda *a, **k: SimpleNamespace())
        with pytest.raises(OSError) as exc:  # The call itself is missing.
            _place(src, dest)
        assert (exc.value.errno, exc.value.strerror) == (errno.ENOSYS, sg._NO_EXCL_SYS)
        assert src.is_dir() and not os.path.lexists(dest)

    @staticmethod
    def _old_glibc(monkeypatch, machine, *, bits64=True, plat="linux", ret=0, **libc):
        """A libc with ``syscall`` (plus any ``libc`` names); record syscall args."""
        calls = []

        def syscall(*args):
            calls.append(args)
            return ret

        monkeypatch.setattr(sg, "_WINDOWS", False)
        monkeypatch.setattr(
            sg,
            "sys",
            SimpleNamespace(platform=plat, maxsize=2**63 - 1 if bits64 else 2**31 - 1),
        )
        monkeypatch.setattr(sg, "platform", SimpleNamespace(machine=lambda: machine))
        monkeypatch.setattr(
            sg.ctypes,
            "CDLL",
            lambda *a, **k: SimpleNamespace(syscall=syscall, **libc),
        )
        return calls

    @pytest.mark.parametrize(
        ("machine", "bits64", "raw"),
        [
            ("x86_64", True, True),
            ("armv7l", False, True),
            ("mips64", True, False),  # No number: the wrapper.
            ("x86_64", False, False),  # x32 or 32-bit Python: the wrapper.
        ],
    )
    def test_listed_linux_machines_skip_the_glibc_wrapper(
        self, tmp_path, monkeypatch, machine, bits64, raw
    ):
        """glibc 2.28+ turns ENOSYS into EINVAL, so listed machines call the kernel."""
        src, dest = tmp_path / "src", tmp_path / "dest"
        wrapped = []
        calls = self._old_glibc(
            monkeypatch,
            machine,
            bits64=bits64,
            renameat2=lambda *a: wrapped.append(a) or 0,
        )
        _place(src, dest)
        assert (len(calls), len(wrapped)) == ((1, 0) if raw else (0, 1))
        args = [-100, os.fsencode(src), -100, os.fsencode(dest), 1]
        assert list((calls or wrapped)[0][raw:]) == args

    _NR = sg._NR_RENAMEAT2[sys.maxsize < 2**32].get(sg.platform.machine())

    @pytest.mark.skipif(
        sys.platform != "linux" or _NR is None, reason="Linux on a listed machine"
    )
    def test_real_linux_move_goes_through_the_raw_syscall(self, tmp_path, monkeypatch):
        real, used = sg.ctypes.CDLL(None, use_errno=True), []

        def syscall(*args):
            used.append(args[0].value)
            return real.syscall(*args)

        monkeypatch.setattr(
            sg.ctypes, "CDLL", lambda *a, **k: SimpleNamespace(syscall=syscall)
        )
        src, empty, dest = tmp_path / "src", tmp_path / "empty", tmp_path / "dest"
        src.mkdir()
        empty.mkdir()
        ino = os.lstat(empty).st_ino
        with pytest.raises(FileExistsError):
            _place(src, empty)
        assert os.lstat(empty).st_ino == ino and os.listdir(empty) == []
        _place(src, dest)
        assert dest.is_dir() and not os.path.lexists(src)
        assert used == [self._NR, self._NR]

    @pytest.mark.parametrize(
        ("machine", "bits64", "nr"),
        [
            ("x86_64", True, 316),
            ("aarch64", True, 276),
            ("arm64", True, 276),
            ("riscv64", True, 276),
            ("ppc64", True, 357),
            ("ppc64le", True, 357),
            ("s390x", True, 347),
            ("i386", False, 353),
            ("i686", False, 353),
            ("armv7l", False, 382),
            ("armv6l", False, 382),
            ("arm", False, 382),
        ],
    )
    def test_old_glibc_calls_the_renameat2_syscall_by_number(
        self, tmp_path, monkeypatch, machine, bits64, nr
    ):
        src, dest = tmp_path / "src", tmp_path / "dest"
        calls = self._old_glibc(monkeypatch, machine, bits64=bits64)
        _place(src, dest)
        [(num, *rest)] = calls
        assert isinstance(num, sg.ctypes.c_long) and num.value == nr
        assert rest == [-100, os.fsencode(src), -100, os.fsencode(dest), 1]

    @pytest.mark.parametrize(
        ("machine", "bits64", "plat"),
        [
            ("mips64", True, "linux"),
            ("armv8l", False, "linux"),
            ("", True, "linux"),
            # 32-bit Python on a 64-bit kernel, or x32: fail closed, never guess.
            ("x86_64", False, "linux"),
            ("aarch64", False, "linux"),
            # A 64-bit Python under the linux32 personality.
            ("i686", True, "linux"),
            ("armv7l", True, "linux"),
            # Only Linux has these numbers.
            ("x86_64", True, "freebsd14"),
            ("arm64", True, "darwin"),
        ],
    )
    def test_old_glibc_without_a_known_number_fails_closed(
        self, tmp_path, monkeypatch, machine, bits64, plat
    ):
        src, dest = tmp_path / "src", tmp_path / "dest"
        src.mkdir()
        calls = self._old_glibc(monkeypatch, machine, bits64=bits64, plat=plat)
        with pytest.raises(OSError) as exc:
            _place(src, dest)
        assert (exc.value.errno, exc.value.strerror) == (errno.ENOSYS, sg._NO_EXCL_SYS)
        assert calls == [] and src.is_dir() and not os.path.lexists(dest)

    def test_old_glibc_without_syscall_fails_closed(self, tmp_path, monkeypatch):
        src, dest = tmp_path / "src", tmp_path / "dest"
        self._old_glibc(monkeypatch, "x86_64")
        monkeypatch.setattr(sg.ctypes, "CDLL", lambda *a, **k: SimpleNamespace())
        with pytest.raises(OSError) as exc:
            _place(src, dest)
        assert (exc.value.errno, exc.value.strerror) == (errno.ENOSYS, sg._NO_EXCL_SYS)

    @pytest.mark.parametrize(
        ("code", "want", "why"),
        [
            (errno.ENOSYS, errno.ENOSYS, sg._NO_EXCL_SYS),  # A kernel older than 3.15.
            (errno.EINVAL, errno.EINVAL, sg._NO_EXCL),
            (errno.ENOTSUP, errno.ENOTSUP, sg._NO_EXCL),
            (errno.EOPNOTSUPP, errno.EOPNOTSUPP, sg._NO_EXCL),
            (errno.EEXIST, errno.EEXIST, os.strerror(errno.EEXIST)),
            (0, errno.EIO, os.strerror(errno.EIO)),  # Never "Success".
        ],
    )
    def test_raw_syscall_errors(self, tmp_path, monkeypatch, code, want, why):
        src, dest = tmp_path / "src", tmp_path / "dest"
        unused = lambda *a: pytest.fail("the glibc wrapper hides ENOSYS")  # noqa: E731
        self._old_glibc(monkeypatch, "aarch64", ret=-1, renameat2=unused)
        monkeypatch.setattr(sg.ctypes, "get_errno", lambda: code)
        with pytest.raises(OSError) as exc:
            _place(src, dest)
        assert (exc.value.errno, exc.value.strerror) == (want, why)

    def test_a_failed_call_that_leaves_errno_zero_never_reads_success(
        self, tmp_path, monkeypatch
    ):
        src, dest = tmp_path / "src", tmp_path / "dest"
        src.mkdir()
        monkeypatch.setattr(sg, "_WINDOWS", False)
        fake = SimpleNamespace(renameat2=lambda *a: -1, renamex_np=lambda *a: -1)
        monkeypatch.setattr(sg.ctypes, "CDLL", lambda *a, **k: fake)
        monkeypatch.setattr(sg.ctypes, "get_errno", lambda: 0)
        with pytest.raises(OSError) as exc:
            _place(src, dest)
        assert (exc.value.errno, exc.value.strerror) == (
            errno.EIO,
            os.strerror(errno.EIO),
        )
        assert src.is_dir() and not os.path.lexists(dest)

    def test_ctrl_c_at_the_remove_probe_leaves_no_staging(self, tmp_path, monkeypatch):
        install(make_bundle(tmp_path))
        saved, tree = state_bytes(), sha_tree(root())

        def interrupt(src, dest):
            if Path(dest).name == "old":
                raise KeyboardInterrupt

        wrap(monkeypatch, sg, "_rename_excl", interrupt)
        with pytest.raises(KeyboardInterrupt):
            remove_tool(gen("claude"))
        assert staging_dirs(root()) == []
        assert (state_bytes(), sha_tree(root())) == (saved, tree)

    @pytest.mark.parametrize("op", ["install", "update", "remove"])
    def test_unsupported_no_replace_move_changes_nothing(
        self, tmp_path, monkeypatch, op
    ):
        if op != "install":
            install(make_bundle(tmp_path))
        root().mkdir(parents=True, exist_ok=True)
        saved, tree = state_bytes(), sha_tree(root())

        def unsupported(src, dest):
            raise OSError(errno.EINVAL, sg._NO_EXCL, str(dest))

        monkeypatch.setattr(sg, "_rename_excl", unsupported)
        with pytest.raises(SkillInstallError) as exc:
            if op == "remove":
                remove_tool(gen("claude"))
            else:
                install(make_bundle(tmp_path, body="v2"))
        if op == "remove":
            assert str(exc.value) == _msg("E21", root=root(), reason=sg._NO_EXCL)
        else:
            assert str(exc.value) == _msg("E5", gen("claude"), reason=sg._NO_EXCL)
        assert (state_bytes(), sha_tree(root())) == (saved, tree)

    @NT
    def test_windows_rename_refuses_dest_created_just_before(
        self, tmp_path, monkeypatch
    ):
        skills = make_bundle(tmp_path)
        api = root() / "api"

        def racer(a, b, *x, **k):
            if Path(b) == api:
                api.mkdir()
                (api / "user.txt").write_bytes(b"mine")

        wrap(monkeypatch, os, "rename", racer)
        with pytest.raises(SkillOwnershipError) as exc:
            install(skills)
        assert str(exc.value) == _msg("E2", gen("claude"), dest=api)
        assert sha_tree(api) == {"user.txt": hashlib.sha256(b"mine").hexdigest()}


# ---------------------------------------------------------------------------
# B6: the record is saved before anything moves
# ---------------------------------------------------------------------------


class TestB6:
    def test_record_before_swap_failure_moves_nothing(self, tmp_path, monkeypatch):
        """_write_state is the one write the installer uses (via _update_state)."""
        install(make_bundle(tmp_path))
        v1, saved = sha_tree(root()), state_bytes()
        calls = []

        def fail(state):
            calls.append(1)
            if len(calls) == 1:
                raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

        wrap(monkeypatch, sg, "_write_state", fail)
        with pytest.raises(SkillInstallError) as exc:
            install(make_bundle(tmp_path, ("api", "docs", "new"), body="v2"))
        reason = os.strerror(errno.ENOSPC).rstrip(".")
        assert str(exc.value) == _msg("E9", gen("claude"), reason=reason)
        assert sha_tree(root()) == v1
        assert not (root() / "new").exists()
        assert staging_dirs(root()) == []
        assert state_bytes() == saved

    def test_mark_save_read_error_passes_through_unwrapped(self, tmp_path, monkeypatch):
        install(make_bundle(tmp_path))
        v1 = sha_tree(root())
        reads = []
        real = Path.read_bytes

        def read_bytes(self):
            if self == sg._STATE_FILE:
                reads.append(1)
                if len(reads) == 2:  # The mark's re-read.
                    raise PermissionError(errno.EACCES, "Permission denied")
            return real(self)

        monkeypatch.setattr(Path, "read_bytes", read_bytes)
        with pytest.raises(SkillInstallError) as exc:
            install(make_bundle(tmp_path, body="v2"))
        assert str(exc.value) == _msg("E8", reason="Permission denied")
        assert sha_tree(root()) == v1
        assert staging_dirs(root()) == []

    def test_final_save_failure_leaves_installing_records_that_prove_ownership(
        self, tmp_path, monkeypatch
    ):
        skills = make_bundle(tmp_path)
        calls = []

        def fail_second(state):
            calls.append(1)
            if len(calls) == 2:
                raise OSError(errno.ENOSPC, "No space left on device")

        with monkeypatch.context() as m:
            wrap(m, sg, "_write_state", fail_second)
            with pytest.raises(SkillInstallError) as exc:
                install(skills)
        assert str(exc.value) == _msg(
            "E9b", gen("claude"), reason="No space left on device"
        )
        for name in ("api", "docs"):
            folder = root() / name
            assert _marker_ok(folder, "claude", name)
            rec = records()[name]
            assert rec["state"] == "installing"
            assert rec["pending"] == _fingerprint(folder)
        assert install_conflicts([gen("claude")], skills) == ([], [])
        install(skills)
        assert records() == {
            n: {"state": "installed", "fingerprint": _fingerprint(root() / n)}
            for n in ("api", "docs")
        }
        assert sorted(remove_tool(gen("claude")).removed) == [
            root() / "api",
            root() / "docs",
        ]
        assert os.listdir(root()) == []


# ---------------------------------------------------------------------------
# B7: nothing is deleted by name
# ---------------------------------------------------------------------------


class TestB7:
    @pytest.mark.parametrize(
        "name", [".api.tmp-123", ".api.old-1-2", ".deepctl-staging-mine"]
    )
    def test_user_dot_names_survive_install_update_remove(self, tmp_path, name):
        mine = root() / name
        mine.mkdir(parents=True)
        (mine / "keep.txt").write_bytes(b"keep")
        _, leftover = install(make_bundle(tmp_path))
        assert leftover is None
        _, leftover = install(make_bundle(tmp_path, body="v2"))
        assert leftover is None
        if name.startswith(sg._STAGING_PREFIX):
            assert tool_status(gen("claude"), get_skills_state()).leftovers == [mine]
        res = remove_tool(gen("claude"))
        assert res.leftover is None
        assert len(res.removed) == 2
        assert (mine / "keep.txt").read_bytes() == b"keep"

    def test_state_dir_temp_lookalike_survives(self, tmp_path):
        lookalike = sg._STATE_FILE.parent / ".skills.json.x.tmp"
        lookalike.parent.mkdir(parents=True)
        lookalike.write_bytes(b"mine")
        install(make_bundle(tmp_path))
        sg._update_state(lambda state: None)  # One more skills.json write.
        remove_tool(gen("claude"))
        assert lookalike.read_bytes() == b"mine"


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


class TestOwnership:
    def test_unrelated_user_skill_survives_install_and_remove(self, tmp_path):
        mine = root() / "my-skill"
        mine.mkdir(parents=True)
        (mine / "SKILL.md").write_bytes(b"mine")
        install(make_bundle(tmp_path))
        remove_tool(gen("claude"))
        assert sha_tree(mine) == {"SKILL.md": hashlib.sha256(b"mine").hexdigest()}

    def test_same_name_unrecorded_folder_makes_install_refuse(self, tmp_path):
        api = root() / "api"
        api.mkdir(parents=True)
        (api / "SKILL.md").write_bytes(b"mine")
        with pytest.raises(SkillOwnershipError) as exc:
            install(make_bundle(tmp_path))
        assert str(exc.value) == _msg("E1", paths=str(api))
        assert sha_tree(api) == {"SKILL.md": hashlib.sha256(b"mine").hexdigest()}

    @pytest.mark.parametrize(
        "kind",
        [
            "dir",
            "empty dir",
            "file",
            "live dir link",
            "live file link",
            "dangling dir link",
            "dangling file link",
        ],
    )
    def test_unrecorded_dest_kinds_refuse(self, tmp_path, kind):
        api = root() / "api"
        root().mkdir(parents=True)
        target = tmp_path / "target"
        if kind == "dir":
            api.mkdir()
            (api / "x").write_bytes(b"x")
        elif kind == "empty dir":
            api.mkdir()
        elif kind == "file":
            api.write_bytes(b"x")
        elif kind == "live dir link":
            target.mkdir()
            (target / "x").write_bytes(b"x")
            symlink_or_skip(api, rel(target, api), is_dir=True)
        elif kind == "live file link":
            target.write_bytes(b"x")
            symlink_or_skip(api, rel(target, api), is_dir=False)
        elif kind == "dangling dir link":
            symlink_or_skip(api, rel(tmp_path / "gone", api), is_dir=True)
        else:
            symlink_or_skip(api, rel(tmp_path / "gone", api), is_dir=False)
        before = sha_tree(api)
        target_before = sha_tree(target) if os.path.lexists(target) else None
        with pytest.raises(SkillOwnershipError) as exc:
            install(make_bundle(tmp_path))
        assert str(exc.value) == _msg("E1", paths=str(api))
        assert state_bytes() is None  # No "installing" write happened (SF7).
        assert sha_tree(api) == before
        if target_before is not None:
            assert sha_tree(target) == target_before

    def test_empty_dir_at_a_recorded_name_is_left_alone(self, tmp_path):
        """deepctl never makes an empty folder at a skill name, so one is never its own."""
        install(make_bundle(tmp_path))
        docs = root() / "docs"
        shutil.rmtree(docs)
        docs.mkdir()
        ino = os.lstat(docs).st_ino
        st = tool_status(gen("claude"), get_skills_state())
        assert [p.name for p in st.kinds["unproven"]] == ["docs"]
        with pytest.raises(SkillOwnershipError) as exc:
            install(make_bundle(tmp_path, body="v2"))
        assert str(exc.value) == _msg("E1", paths=str(docs))
        res = remove_tool(gen("claude"))
        assert [p.name for p in res.removed] == ["api"]
        assert (res.left_alone, res.kept) == ([docs], [])
        assert "skill_folders" not in disk_state() or records() == {}
        assert os.lstat(docs).st_ino == ino and os.listdir(docs) == []
        crashed = {"state": "installing", "pending": FP_A, "run": "f" * 32}
        write_state({"skill_folders": {"claude": {"folders": {"docs": crashed}}}})
        with pytest.raises(SkillOwnershipError) as exc:  # A crashed run's record too.
            install(make_bundle(tmp_path))
        assert str(exc.value) == _msg("E1", paths=str(docs))
        assert os.lstat(docs).st_ino == ino and os.listdir(docs) == []

    def test_recorded_name_now_symlink_is_never_written_through(self, tmp_path):
        install(make_bundle(tmp_path))
        api, mine = root() / "api", Path.home() / "mine" / "api"
        mine.parent.mkdir()
        os.rename(api, mine)
        symlink_or_skip(api, rel(mine, api), is_dir=True)
        assert (
            _fingerprint(mine) == records()["api"]["fingerprint"]
        )  # A real install (SF7).
        before, saved = sha_tree(mine), state_bytes()
        with pytest.raises(SkillOwnershipError) as exc:
            install(make_bundle(tmp_path, body="v2"))
        assert str(exc.value) == _msg("E1", paths=str(api))
        assert state_bytes() == saved
        assert api.is_symlink()
        assert sha_tree(mine) == before

    def test_recorded_folder_without_marker_is_left_alone(self, tmp_path):
        install(make_bundle(tmp_path))
        api = root() / "api"
        (api / sg._MARKER).unlink()
        before = sha_tree(api)
        with pytest.raises(SkillOwnershipError) as exc:
            install(make_bundle(tmp_path, body="v2"))
        assert str(exc.value) == _msg("E1", paths=str(api))
        res = remove_tool(gen("claude"))
        assert res.left_alone == [api]
        assert sha_tree(api) == before

    def test_recorded_folder_losing_marker_after_precheck_gives_e3(
        self, tmp_path, monkeypatch
    ):
        install(make_bundle(tmp_path))
        api = root() / "api"

        def drop_marker(src, dst, *a, **k):
            if Path(dst).name == "api":
                (api / sg._MARKER).unlink()

        wrap(monkeypatch, shutil, "copytree", drop_marker)
        with pytest.raises(SkillOwnershipError) as exc:
            install(make_bundle(tmp_path, body="v2"))
        assert str(exc.value) == _msg("E3", gen("claude"), dest=api)
        assert not (api / sg._MARKER).exists()
        assert b"v1" in (api / "SKILL.md").read_bytes()
        assert staging_dirs(root()) == []

    def test_marker_is_bound_to_the_tool(self, tmp_path, monkeypatch):
        folder, fp = installed_copy(tmp_path, monkeypatch, "claude", "api")
        api = root("cursor") / "api"
        shutil.copytree(folder, api)
        write_state(
            {
                "skill_folders": {
                    "cursor": {
                        "folders": {"api": {"state": "installed", "fingerprint": fp}}
                    }
                }
            }
        )
        assert _fingerprint(api) == fp
        before, saved = sha_tree(api), state_bytes()
        with pytest.raises(SkillOwnershipError) as exc:
            install(make_bundle(tmp_path), "cursor")
        assert str(exc.value) == _msg("E1", paths=str(api))
        assert sha_tree(api) == before
        assert state_bytes() == saved

    @pytest.mark.parametrize("kind", ["dir", "symlink", "fifo", "big"])
    def test_marker_must_be_a_small_regular_file(self, tmp_path, kind):
        folder = tmp_path / "api"
        folder.mkdir()
        marker, text = folder / sg._MARKER, sg._marker_text("claude", "api").encode()
        if kind == "dir":
            marker.mkdir()
        elif kind == "symlink":
            (tmp_path / "real").write_bytes(text)
            symlink_or_skip(marker, rel(tmp_path / "real", marker), is_dir=False)
        elif kind == "fifo":
            if not hasattr(os, "mkfifo"):
                pytest.skip("no FIFOs on this OS")
            os.mkfifo(marker)
        else:
            marker.write_bytes(text + b"x" * 1024)
        assert _marker_ok(folder, "claude", "api") is False

    def test_marker_symlink_refused_without_o_nofollow(self, tmp_path, monkeypatch):
        folder = tmp_path / "api"
        folder.mkdir()
        marker, text = folder / sg._MARKER, sg._marker_text("claude", "api").encode()
        marker.write_bytes(text)
        assert _marker_ok(folder, "claude", "api") is True
        monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
        other = tmp_path / "other"
        other.write_bytes(text)  # Exact marker bytes, but a different file.
        swapped = []

        def swap_after_lstat(path, *a, **k):
            if Path(path) == marker and not swapped:
                swapped.append(1)
                os.replace(other, marker)

        with monkeypatch.context() as m:
            wrap(m, os, "open", swap_after_lstat)
            assert _marker_ok(folder, "claude", "api") is False
        assert swapped
        marker.unlink()
        (tmp_path / "real").write_bytes(text)
        symlink_or_skip(marker, rel(tmp_path / "real", marker), is_dir=False)
        assert _marker_ok(folder, "claude", "api") is False

    def test_skills_cli_symlink_farm_is_refused(self, tmp_path):
        agents_api = root("codex") / "api"
        agents_api.mkdir(parents=True)
        (agents_api / "SKILL.md").write_bytes(b"npx")
        claude_api = root("claude") / "api"
        root("claude").mkdir(parents=True)
        symlink_or_skip(claude_api, rel(agents_api, claude_api), is_dir=True)
        before = sha_tree(agents_api)
        for cli, dest in (("claude", claude_api), ("codex", agents_api)):
            with pytest.raises(SkillOwnershipError) as exc:
                install(make_bundle(tmp_path), cli)
            assert str(exc.value) == _msg("E1", paths=str(dest))
        assert claude_api.is_symlink()
        assert sha_tree(agents_api) == before
        assert state_bytes() is None

    def test_symlinked_root_is_followed(self, tmp_path):
        real = Path.home() / "dotfiles" / "skills"
        real.mkdir(parents=True)
        (Path.home() / ".claude").mkdir()
        symlink_or_skip(root(), rel(real, root()), is_dir=True)
        install(make_bundle(tmp_path))
        assert root().is_symlink()
        assert _marker_ok(real / "api", "claude", "api")

    @NT
    def test_junction_at_dest_is_a_conflict(self, tmp_path):
        import _winapi

        target = tmp_path / "target"
        target.mkdir()
        root().mkdir(parents=True)
        _winapi.CreateJunction(str(target), str(root() / "api"))
        with pytest.raises(SkillOwnershipError) as exc:
            install(make_bundle(tmp_path))
        assert str(exc.value) == _msg("E1", paths=str(root() / "api"))
        assert target.is_dir()

    def test_reparse_attribute_counts_as_link(self):
        fake = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_file_attributes=0x400)
        assert sg._is_link(fake) is True
        assert sg._is_link(SimpleNamespace(st_mode=stat.S_IFDIR | 0o755)) is False


# ---------------------------------------------------------------------------
# Edits: the content fingerprint
# ---------------------------------------------------------------------------


class TestEdits:
    def test_edited_folder_survives_update(self, tmp_path):
        install(make_bundle(tmp_path))
        api, docs = root() / "api", root() / "docs"
        edit(api)
        edited, docs_before, saved = sha_tree(api), sha_tree(docs), state_bytes()
        v2 = make_bundle(tmp_path, body="v2")
        assert install_conflicts([gen("claude")], v2) == ([], [api])
        with pytest.raises(SkillOwnershipError) as exc:
            install(v2)
        assert str(exc.value) == _msg("E22", dest=api)
        assert sha_tree(api) == edited
        assert sha_tree(docs) == docs_before
        assert staging_dirs(root()) == []
        assert state_bytes() == saved

    def test_edited_folder_survives_remove(self, tmp_path):
        install(make_bundle(tmp_path))
        api = root() / "api"
        edit(api)
        edited = sha_tree(api)
        res = remove_tool(gen("claude"))
        assert res.edited == [api]
        assert res.removed == [root() / "docs"]
        assert sha_tree(api) == edited
        assert not (root() / "docs").exists()
        state = disk_state()
        assert "claude" not in state.get("skill_folders", {})
        assert "claude" not in state["installed_skills"]
        again = remove_tool(gen("claude"))
        assert again.removed == again.edited == again.left_alone == []
        with pytest.raises(SkillOwnershipError) as exc:
            install(make_bundle(tmp_path, body="v2"))
        assert str(exc.value) == _msg("E1", paths=str(api))

    @pytest.mark.parametrize("op", ["update", "remove"])
    def test_edit_racing_move_is_caught_after_move_and_put_back(
        self, tmp_path, monkeypatch, op
    ):
        install(make_bundle(tmp_path))
        api = root() / "api"

        def racer(src, dst, *a, **k):
            if Path(src) == api and is_aside(src, dst):
                edit(api)  # Lands after the pre-check, before the move.

        wrap(monkeypatch, os, "rename", racer)
        expected = sha_tree(api)
        expected["SKILL.md"] = hashlib.sha256(
            (api / "SKILL.md").read_bytes() + b"mine\n"
        ).hexdigest()
        if op == "update":
            with pytest.raises(SkillOwnershipError) as exc:
                install(make_bundle(tmp_path, body="v2"))
            assert str(exc.value) == _msg("E22", dest=api)
        else:
            assert remove_tool(gen("claude")).edited == [api]
        assert sha_tree(api) == expected
        assert staging_dirs(root()) == []

    @pytest.mark.parametrize(
        "how", ["added link", "folder swapped for a link to an identical copy"]
    )
    def test_symlink_inside_deepctl_folder_blocks_replace_and_remove(
        self, tmp_path, how
    ):
        install(make_bundle(tmp_path))
        api, docs = root() / "api", root() / "docs"
        if how == "added link":
            link = api / "link"
            symlink_or_skip(link, os.path.join("..", "docs"), is_dir=True)
        else:  # Followed, the tree would hash exactly as installed.
            link, copy_ = api / "references", tmp_path / "references-copy"
            shutil.copytree(link, copy_)
            shutil.rmtree(link)
            symlink_or_skip(link, rel(copy_, link), is_dir=True)
        before = sha_tree(api)
        with pytest.raises(SkillOwnershipError) as exc:
            install(make_bundle(tmp_path, body="v2"))
        assert str(exc.value) == _msg("E22", dest=api)
        assert remove_tool(gen("claude")).edited == [api]
        assert sha_tree(api) == before
        assert link.is_symlink()
        assert not docs.exists()  # docs was ours and unedited, so remove took it.

    @POSIX
    def test_special_file_inside_blocks_replace_and_remove(self, tmp_path):
        install(make_bundle(tmp_path))
        api = root() / "api"
        os.mkfifo(api / "pipe")
        before = sha_tree(api)
        with pytest.raises(SkillOwnershipError) as exc:
            install(make_bundle(tmp_path, body="v2"))
        assert str(exc.value) == _msg("E22", dest=api)
        assert remove_tool(gen("claude")).edited == [api]
        assert sha_tree(api) == before

    @pytest.mark.parametrize("op", ["update", "remove"])
    def test_added_file_blocks_replace_and_remove(self, tmp_path, op):
        install(make_bundle(tmp_path))
        api = root() / "api"
        (api / "notes.md").write_bytes(b"my notes")
        before = sha_tree(api)
        if op == "update":
            with pytest.raises(SkillOwnershipError) as exc:
                install(make_bundle(tmp_path, body="v2"))
            assert str(exc.value) == _msg("E22", dest=api)
        else:
            assert remove_tool(gen("claude")).edited == [api]
        assert sha_tree(api) == before

    def test_edit_inside_staging_before_cleanup_keeps_the_copy(
        self, tmp_path, monkeypatch
    ):
        install(make_bundle(tmp_path))

        def tamper(mutate, *a, **k):
            if mutate.__name__ == "settle":
                (copy_,) = (root() / s / "old" / "api" for s in staging_dirs(root()))
                edit(copy_)

        wrap(monkeypatch, sg, "_update_state", tamper)
        _, leftover = install(make_bundle(tmp_path, body="v2"))
        assert leftover is not None
        assert b"mine" in (leftover / "old" / "api" / "SKILL.md").read_bytes()
        assert b"v2" in (root() / "api" / "SKILL.md").read_bytes()

    @pytest.mark.parametrize("op", ["update", "remove", "status"])
    def test_unreadable_folder_is_never_called_edited(self, tmp_path, monkeypatch, op):
        install(make_bundle(tmp_path))
        api = root() / "api"
        before, rec = sha_tree(api), records()["api"]
        target = os.fspath(api / "SKILL.md")

        def deny(path, *a, **k):
            if os.fspath(path) == target:
                raise PermissionError(errno.EACCES, "Permission denied", target)

        with monkeypatch.context() as m:
            wrap(m, os, "open", deny)
            if op == "update":
                with pytest.raises(SkillInstallError) as exc:
                    install(make_bundle(tmp_path, body="v2"))
                assert str(exc.value) == _msg(
                    "E5", gen("claude"), reason=f"could not read {api}"
                )
                assert staging_dirs(root()) == []
            elif op == "remove":
                res = remove_tool(gen("claude"))
                assert res.kept == [(api, "deepctl could not read it")]
                assert res.edited == []
                assert res.removed == [root() / "docs"]
                assert records()["api"] == rec
            else:
                st = tool_status(gen("claude"), get_skills_state())
                assert st.kinds["unreadable"] == [api]
                assert st.kinds["edited"] == []
        assert sha_tree(api) == before
        if op == "remove":
            assert remove_tool(gen("claude")).removed == [api]

    def test_settle_keeps_an_unreadable_record_exactly(self, tmp_path, monkeypatch):
        install(make_bundle(tmp_path))
        api, seen = root() / "api", {}

        def deny(path):
            raise PermissionError(errno.EACCES, "Permission denied", str(path))

        def unreadable_at_settle(mutate, *a, **k):
            if mutate.__name__ == "settle":  # Settle only: staging read fine.
                seen["rec"] = records()["api"]  # As mark left it.
                monkeypatch.setattr(sg, "_fingerprint", deny)

        wrap(monkeypatch, sg, "_update_state", unreadable_at_settle)
        install(make_bundle(tmp_path, body="v2"))
        assert seen["rec"]["state"] == "installing"
        assert records()["api"] == seen["rec"]
        assert b"v2" in (api / "SKILL.md").read_bytes()

    def test_fingerprint_definition(self, tmp_path, monkeypatch):
        def tree(where, order):
            where.mkdir()
            for name in order:
                if name == "sub":
                    (where / "sub").mkdir()
                    (where / "sub" / "b.md").write_bytes(b"b")
                else:
                    (where / name).write_bytes(name.encode())
            return where

        a = tree(tmp_path / "a", ["x.md", "sub", sg._MARKER])
        b = tree(tmp_path / "b", [sg._MARKER, "sub", "x.md"])
        base = _fingerprint(a)
        assert base == _fingerprint(a) == _fingerprint(b)
        assert base and sg._FP_PATTERN.fullmatch(base)

        def changed(mutate):
            mutate()
            fp = _fingerprint(b)
            shutil.rmtree(b)
            tree(b, ["x.md", "sub", sg._MARKER])
            return fp != base

        assert changed(lambda: (b / "x.md").write_bytes(b"y"))
        assert changed(lambda: (b / "new.md").write_bytes(b""))
        assert changed(lambda: (b / "x.md").unlink())
        assert changed(lambda: os.rename(b / "x.md", b / "z.md"))
        assert changed(lambda: (b / "empty").mkdir())
        assert changed(lambda: (b / sg._MARKER).write_bytes(b"other marker"))
        os.utime(b / "x.md", (1, 1))
        os.chmod(b / "x.md", 0o444)  # Still readable; on Windows, the read-only flag.
        assert _fingerprint(b) == base
        os.chmod(b / "x.md", 0o644)

        fake = SimpleNamespace(name="r", path=str(a / "x.md"))
        fake.stat = lambda follow_symlinks=True: SimpleNamespace(
            st_mode=stat.S_IFREG | 0o644, st_file_attributes=0x400
        )

        class Scan:
            def __enter__(self):
                return iter([fake])

            def __exit__(self, *exc):
                return False

        with monkeypatch.context() as m:
            m.setattr(os, "scandir", lambda p: Scan())
            assert _fingerprint(a) is None
        with monkeypatch.context() as m:
            m.setattr(
                os,
                "scandir",
                lambda p: (_ for _ in ()).throw(
                    PermissionError(errno.EACCES, "denied")
                ),
            )
            with pytest.raises(OSError):
                _fingerprint(a)
        if hasattr(os, "mkfifo"):
            os.mkfifo(b / "pipe")
            assert _fingerprint(b) is None
            os.unlink(b / "pipe")
        symlink_or_skip(b / "link", "x.md", is_dir=False)
        assert _fingerprint(b) is None

    def test_fingerprint_matches_a_reference_digest(self, tmp_path):
        top = tmp_path / "t"
        (top / "references" / "deep").mkdir(parents=True)
        (top / "SKILL.md").write_bytes(b"skill")
        (top / "references" / "r.md").write_bytes(b"ref")
        (top / "references" / "deep" / "empty").mkdir()
        (top / sg._MARKER).write_bytes(sg._marker_text("claude", "t").encode())
        entries = []
        for dirpath, dirs, files in os.walk(top):
            prefix = Path(dirpath).relative_to(top).as_posix()
            prefix = "" if prefix == "." else prefix + "/"
            entries += [(os.fsencode(prefix + d), b"d", None) for d in dirs]
            entries += [
                (os.fsencode(prefix + f), b"f", Path(dirpath) / f) for f in files
            ]
        h = hashlib.sha256(b"deepctl-skill-tree-v1\0")
        for key, kind, file in sorted(entries, key=lambda e: e[0]):
            h.update(kind + len(key).to_bytes(4, "big") + key)
            if file:
                h.update(hashlib.sha256(file.read_bytes()).digest())
        assert _fingerprint(top) == "sha256:" + h.hexdigest()

    def test_fingerprint_size_cap(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sg, "_MAX_TREE_BYTES", 10)
        top = tmp_path / "t"
        top.mkdir()
        (top / "a").write_bytes(b"12345")
        (top / "b").write_bytes(b"12345")
        assert _fingerprint(top) is not None
        (top / "c").write_bytes(b"1")
        assert _fingerprint(top) is None

    def test_record_without_fingerprint_is_not_ours(self, tmp_path):
        install(make_bundle(tmp_path))
        state = disk_state()
        state["skill_folders"]["claude"]["folders"]["api"] = {"state": "installed"}
        write_state(state)
        api = root() / "api"
        with pytest.raises(SkillOwnershipError) as exc:
            install(make_bundle(tmp_path, body="v2"))
        assert str(exc.value) == _msg("E1", paths=str(api))
        res = remove_tool(gen("claude"))
        assert res.left_alone == [api]
        assert api.is_dir()
        assert "claude" not in disk_state().get("skill_folders", {})

    @pytest.mark.parametrize("crash", ["before swap", "after swap", "neither"])
    def test_crashed_installing_record_proves_by_old_or_pending(self, tmp_path, crash):
        install(make_bundle(tmp_path))
        api = root() / "api"
        fp = records()["api"]["fingerprint"]
        rec = {
            "before swap": {"state": "installing", "fingerprint": fp, "pending": FP_A},
            "after swap": {"state": "installing", "pending": fp},
            "neither": {"state": "installing", "fingerprint": FP_A, "pending": FP_B},
        }[crash]
        state = disk_state()
        state["skill_folders"]["claude"]["folders"]["api"] = rec
        write_state(state)
        if crash == "neither":
            with pytest.raises(SkillOwnershipError) as exc:
                install(make_bundle(tmp_path, body="v2"))
            assert str(exc.value) == _msg("E22", dest=api)
            return
        install(make_bundle(tmp_path, body="v2"))
        assert b"v2" in (api / "SKILL.md").read_bytes()
        assert records()["api"] == {
            "state": "installed",
            "fingerprint": _fingerprint(api),
        }
        assert api in remove_tool(gen("claude")).removed


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


class TestUpdate:
    def test_update_replaces_recorded_folder(self, tmp_path):
        install(make_bundle(tmp_path))
        placed, leftover = install(make_bundle(tmp_path, body="v2"))
        api = root() / "api"
        assert placed == [api, root() / "docs"]
        assert leftover is None
        assert b"v2" in (api / "SKILL.md").read_bytes()
        assert records()["api"] == {
            "state": "installed",
            "fingerprint": _fingerprint(api),
        }
        assert disk_state()["skill_folders"]["claude"]["version"] == "9.9.9"

    def test_update_restores_old_when_new_place_fails(self, tmp_path, monkeypatch):
        install(make_bundle(tmp_path))
        api = root() / "api"
        old = sha_tree(api)

        def fail(src, dest):
            if Path(src).parent.name == "new" and Path(src).name == "api":
                raise OSError(errno.EIO, "I/O error")

        wrap(monkeypatch, sg, "_place", fail)
        with pytest.raises(SkillInstallError) as exc:
            install(make_bundle(tmp_path, body="v2"))
        assert str(exc.value) == _msg("E5", gen("claude"), reason="I/O error")
        assert sha_tree(api) == old
        assert staging_dirs(root()) == []
        assert records()["api"]["state"] == "installed"

    def test_restore_failure_keeps_previous_copy_where_e6_says(
        self, tmp_path, monkeypatch
    ):
        install(make_bundle(tmp_path))
        api = root() / "api"
        old = sha_tree(api)

        def racer(src, dest):
            if Path(src).parent.name == "new" and Path(src).name == "api":
                api.mkdir()
                (api / "user.txt").write_bytes(b"theirs")

        wrap(monkeypatch, sg, "_place", racer)
        with pytest.raises(SkillInstallError) as exc:
            install(make_bundle(tmp_path, body="v2"))
        aside = exc.value.leftover / "old" / "api"
        assert exc.value.leftover.parent == root()
        assert str(exc.value) == _msg("E6", gen("claude"), name="api", aside=aside)
        assert sha_tree(aside) == old
        assert sha_tree(api) == {"user.txt": hashlib.sha256(b"theirs").hexdigest()}

    def test_old_copy_stays_at_e6_path_when_staged_copy_and_dest_change(
        self, tmp_path, monkeypatch
    ):
        install(make_bundle(tmp_path))
        api, stash = root() / "api", tmp_path / "stash"
        old = sha_tree(api)

        def outsider(src, dest):
            if Path(src).parent.name == "new" and Path(src).name == "api":
                os.rename(src, stash)  # Moved away by something outside deepctl.
                api.mkdir()  # And a user recreates the destination.
                (api / "user.txt").write_bytes(b"theirs")

        wrap(monkeypatch, sg, "_place", outsider)
        with pytest.raises(SkillInstallError) as exc:
            install(make_bundle(tmp_path, body="v2"))
        aside = exc.value.leftover / "old" / "api"
        assert str(exc.value) == _msg("E6", gen("claude"), name="api", aside=aside)
        assert sha_tree(aside) == old
        assert sha_tree(api) == {"user.txt": hashlib.sha256(b"theirs").hexdigest()}
        assert b"v2" in (stash / "SKILL.md").read_bytes()

    def test_update_moved_aside_dir_failing_reproof_is_put_back(
        self, tmp_path, monkeypatch
    ):
        install(make_bundle(tmp_path))
        api, stash = root() / "api", tmp_path / "stash"

        def swap_in_user_dir(src, dst, *a, **k):
            if Path(src) == api and is_aside(src, dst):
                os.rename(api, stash)
                api.mkdir()
                (api / "user.txt").write_bytes(b"mine")

        wrap(monkeypatch, os, "rename", swap_in_user_dir)
        with pytest.raises(SkillOwnershipError) as exc:
            install(make_bundle(tmp_path, body="v2"))
        assert str(exc.value) == _msg("E3", gen("claude"), dest=api)
        assert sha_tree(api) == {"user.txt": hashlib.sha256(b"mine").hexdigest()}

    def test_update_moved_aside_file_kept_in_staging(self, tmp_path, monkeypatch):
        install(make_bundle(tmp_path))
        api, stash = root() / "api", tmp_path / "stash"
        state = {"moved": False}
        real_rename = os.rename

        def swap_in_file(src, dst, *a, **k):
            if Path(src) == api and is_aside(src, dst) and not state["moved"]:
                state["moved"] = True
                real_rename(api, stash)
                api.write_bytes(b"user file")
                real_rename(src, dst)
                api.mkdir()  # And a racer takes dest, so the put-back fails.
                (api / "racer.txt").write_bytes(b"racer")
                return True

        wrap(monkeypatch, os, "rename", swap_in_file)
        with pytest.raises(SkillInstallError) as exc:
            install(make_bundle(tmp_path, body="v2"))
        aside = exc.value.leftover / "old" / "api"
        assert str(exc.value) == _msg("E4", dest=api, aside=aside)
        assert aside.read_bytes() == b"user file"
        assert (api / "racer.txt").read_bytes() == b"racer"

    def test_update_ctrl_c_during_place_restores_old(self, tmp_path, monkeypatch):
        install(make_bundle(tmp_path))
        api = root() / "api"
        old = sha_tree(api)

        def interrupt(src, dest):
            if Path(src).parent.name == "new" and Path(src).name == "api":
                raise KeyboardInterrupt

        wrap(monkeypatch, sg, "_place", interrupt)
        with pytest.raises(KeyboardInterrupt):
            install(make_bundle(tmp_path, body="v2"))
        assert sha_tree(api) == old
        assert staging_dirs(root()) == []

    def test_update_ctrl_c_during_aside_reproof_puts_old_back(
        self, tmp_path, monkeypatch
    ):
        install(make_bundle(tmp_path))
        api = root() / "api"
        old = sha_tree(api)
        fired = []

        def interrupt(path):  # Every time: cleanup must never need to re-prove it.
            p = Path(path)
            if p.name == "api" and p.parent.name == "old":
                fired.append(1)
                raise KeyboardInterrupt

        wrap(monkeypatch, sg, "_fingerprint", interrupt)
        with pytest.raises(KeyboardInterrupt):
            install(make_bundle(tmp_path, body="v2"))
        assert fired
        assert sha_tree(api) == old
        assert _ownership(api, "claude", "api", records()["api"]) == "ok"
        assert staging_dirs(root()) == []

    def test_update_ctrl_c_during_mark_save_leaves_no_staging(
        self, tmp_path, monkeypatch
    ):
        install(make_bundle(tmp_path))
        before, saved = sha_tree(root()), state_bytes()

        def interrupt(state):
            raise KeyboardInterrupt

        wrap(monkeypatch, sg, "_write_state", interrupt)
        with pytest.raises(KeyboardInterrupt):
            install(make_bundle(tmp_path, body="v2"))
        assert sha_tree(root()) == before
        assert state_bytes() == saved
        assert staging_dirs(root()) == []

    def test_cleanup_deletes_old_only_when_new_is_in_place(self, tmp_path, monkeypatch):
        install(make_bundle(tmp_path))
        api, docs = root() / "api", root() / "docs"

        def interrupt_settle(mutate, *a, **k):
            if mutate.__name__ == "settle":
                raise KeyboardInterrupt

        with monkeypatch.context() as m:
            wrap(m, sg, "_update_state", interrupt_settle)
            with pytest.raises(KeyboardInterrupt):
                install(make_bundle(tmp_path, body="v2"))
        for folder in (api, docs):
            assert b"v2" in (folder / "SKILL.md").read_bytes()
            rec = records()[folder.name]
            assert rec["state"] == "installing"
            assert rec["pending"] == _fingerprint(folder)
        assert staging_dirs(root()) == []

        install(make_bundle(tmp_path, body="v3"))
        docs_v3 = sha_tree(docs)
        calls = []

        def interrupt_docs(src, dest):
            if Path(src).parent.name == "new" and Path(src).name == "docs":
                calls.append("place")
                raise KeyboardInterrupt
            if Path(src).parent.name == "old" and calls == ["place"]:
                calls.append("put-back")
                raise OSError(errno.EIO, "I/O error")

        wrap(monkeypatch, sg, "_place", interrupt_docs)
        with pytest.raises(KeyboardInterrupt):
            install(make_bundle(tmp_path, body="v4"))
        assert calls == ["place", "put-back"]
        assert sha_tree(docs) == docs_v3  # Cleanup's invariant put it back.
        assert b"v4" in (api / "SKILL.md").read_bytes()
        assert staging_dirs(root()) == []

    def test_ctrl_c_between_move_aside_and_place_keeps_the_record(
        self, tmp_path, monkeypatch
    ):
        """The put-back fails too, so cleanup restores the copy after settle."""
        install(make_bundle(tmp_path))
        api = root() / "api"
        old = sha_tree(api)
        calls = []

        def interrupt(src, dest):
            if Path(src).parent.name == "new" and Path(src).name == "api":
                calls.append("place")
                raise KeyboardInterrupt
            if Path(src).parent.name == "old" and calls == ["place"]:
                calls.append("put-back")
                raise OSError(errno.EIO, "I/O error")

        with monkeypatch.context() as m:
            wrap(m, sg, "_place", interrupt)
            with pytest.raises(KeyboardInterrupt):
                install(make_bundle(tmp_path, body="v2"))
        assert calls == ["place", "put-back"]
        assert sha_tree(api) == old
        assert _ownership(api, "claude", "api", records()["api"]) == "ok"
        assert staging_dirs(root()) == []
        assert install_conflicts([gen("claude")], make_bundle(tmp_path, body="v3")) == (
            [],
            [],
        )
        install(make_bundle(tmp_path, body="v3"))
        assert b"v3" in (api / "SKILL.md").read_bytes()
        assert records()["api"] == {
            "state": "installed",
            "fingerprint": _fingerprint(api),
        }

    def test_update_move_aside_permission_error_changes_nothing(
        self, tmp_path, monkeypatch
    ):
        install(make_bundle(tmp_path))
        api = root() / "api"
        old = sha_tree(api)

        def deny(src, dst, *a, **k):
            if Path(src) == api and is_aside(src, dst):
                raise PermissionError(errno.EACCES, "Permission denied")

        wrap(monkeypatch, os, "rename", deny)
        with pytest.raises(SkillInstallError) as exc:
            install(make_bundle(tmp_path, body="v2"))
        assert str(exc.value) == _msg("E5", gen("claude"), reason="Permission denied")
        assert sha_tree(api) == old
        assert records()["api"]["state"] == "installed"

    def test_failed_update_keeps_the_recorded_ref(self, tmp_path, monkeypatch):
        install(make_bundle(tmp_path))

        def fail(*a, **k):
            raise SkillInstallError("boom")

        monkeypatch.setattr(sg, "_swap", fail)
        with pytest.raises(SkillInstallError):
            install(make_bundle(tmp_path, body="v2"), ref="f" * 40)
        assert disk_state()["skill_folders"]["claude"]["skills_ref"] == REF

    def test_update_leaves_names_not_in_bundle_alone(self, tmp_path):
        install(make_bundle(tmp_path, ("api", "docs", "extra")))
        extra, rec = sha_tree(root() / "extra"), records()["extra"]
        install(make_bundle(tmp_path, body="v2"))
        assert sha_tree(root() / "extra") == extra
        assert records()["extra"] == rec


# ---------------------------------------------------------------------------
# Remove
# ---------------------------------------------------------------------------


class TestRemove:
    def test_a_user_made_staging_lookalike_without_a_marker_is_ignored(self, tmp_path):
        install(make_bundle(tmp_path))
        notes = root() / ".deepctl-staging-x" / "old" / "api" / "notes.txt"
        notes.parent.mkdir(parents=True)
        notes.write_text("user data\n")
        shutil.rmtree(root() / "api")
        res = remove_tool(gen("claude"))
        assert res.removed == [root() / "docs"]
        assert (res.stranded, res.kept, res.moved, res.leftover) == ([], [], [], None)
        assert records() == {} and notes.read_text() == "user data\n"

    def test_a_symlinked_staging_folder_is_ignored(self, tmp_path):
        install(make_bundle(tmp_path))
        outside = tmp_path / "outside" / "old" / "api"
        outside.mkdir(parents=True)  # A marked copy: ignored only for the link.
        (outside / sg._MARKER).write_bytes(sg._marker_text("claude", "api").encode())
        before = sha_tree(outside)
        link = root() / ".deepctl-staging-lnk"
        symlink_or_skip(link, rel(tmp_path / "outside", link), is_dir=True)
        shutil.rmtree(root() / "api")
        res = remove_tool(gen("claude"))
        assert res.removed == [root() / "docs"]
        assert (res.stranded, res.kept, res.moved, res.leftover) == ([], [], [], None)
        assert records() == {} and sha_tree(outside) == before

    def test_remove_deletes_only_proven_recorded_folders(self, tmp_path):
        install(make_bundle(tmp_path))
        mine = root() / "my-skill"
        mine.mkdir()
        (mine / "SKILL.md").write_bytes(b"mine")
        res = remove_tool(gen("claude"))
        assert sorted(res.removed) == [root() / "api", root() / "docs"]
        assert os.listdir(root()) == ["my-skill"]

    def test_remove_failed_move_aside_stays_recorded_and_retries(
        self, tmp_path, monkeypatch
    ):
        install(make_bundle(tmp_path))
        api = root() / "api"
        with monkeypatch.context() as m:

            def deny(src, dst, *a, **k):
                if Path(src) == api:
                    raise PermissionError(errno.EACCES, "Permission denied")

            wrap(m, os, "rename", deny)
            res = remove_tool(gen("claude"))
        assert res.kept == [(api, "Permission denied")]
        assert res.removed == [root() / "docs"]
        assert set(records()) == {"api"}
        assert remove_tool(gen("claude")).removed == [api]
        assert not api.exists()

    def test_remove_reproves_after_move_aside(self, tmp_path, monkeypatch):
        install(make_bundle(tmp_path))
        api, stash = root() / "api", tmp_path / "stash"

        def swap_in_user_dir(src, dst, *a, **k):
            if Path(src) == api and is_aside(src, dst):
                os.rename(api, stash)
                api.mkdir()
                (api / "user.txt").write_bytes(b"mine")

        wrap(monkeypatch, os, "rename", swap_in_user_dir)
        res = remove_tool(gen("claude"))
        assert res.left_alone == [api]
        assert sha_tree(api) == {"user.txt": hashlib.sha256(b"mine").hexdigest()}

    def test_remove_ctrl_c_during_aside_reproof_puts_it_back(
        self, tmp_path, monkeypatch
    ):
        install(make_bundle(tmp_path))
        api = root() / "api"
        old = sha_tree(api)
        fired = []

        def interrupt(path):  # Every time: cleanup must never need to re-prove it.
            p = Path(path)
            if p.name == "api" and p.parent.name == "old":
                fired.append(1)
                raise KeyboardInterrupt

        wrap(monkeypatch, sg, "_fingerprint", interrupt)
        with pytest.raises(KeyboardInterrupt):
            remove_tool(gen("claude"))
        assert sha_tree(api) == old
        assert "api" in records()
        assert staging_dirs(root()) == []

    def test_remove_puts_back_a_file_swapped_in_before_the_move(
        self, tmp_path, monkeypatch
    ):
        install(make_bundle(tmp_path))
        api, stash = root() / "api", tmp_path / "stash"

        def swap_in_file(src, dst, *a, **k):
            if Path(src) == api and is_aside(src, dst):
                os.rename(api, stash)
                api.write_bytes(b"user file")

        wrap(monkeypatch, os, "rename", swap_in_file)
        res = remove_tool(gen("claude"))
        assert res.left_alone == [api]
        assert api.read_bytes() == b"user file"
        assert "api" not in records()
        assert (res.moved, res.leftover) == ([], None)

    @pytest.mark.parametrize("times", [1, 2])
    def test_remove_ctrl_c_during_put_back(self, tmp_path, monkeypatch, times):
        install(make_bundle(tmp_path, ("api",)))
        api = root() / "api"
        real_rename, real_place, fired = os.rename, sg._place, []

        def rename(src, dst, *a, **k):
            real_rename(src, dst, *a, **k)
            if Path(src) == api and is_aside(src, dst):
                edit(dst, "raced the move\n")  # The edit lands in the moved copy.
            return True

        def place(src, dest):
            if Path(src).parent.name == "old" and len(fired) < times:
                fired.append(1)
                raise KeyboardInterrupt
            return real_place(src, dest)

        wrap(monkeypatch, os, "rename", rename)
        monkeypatch.setattr(sg, "_place", place)
        with pytest.raises(KeyboardInterrupt):
            remove_tool(gen("claude"))
        if times == 1:  # The retry puts the edited folder back.
            assert (api / "SKILL.md").read_bytes().endswith(b"raced the move\n")
            assert staging_dirs(root()) == []
        else:  # Still in staging, so its record stays.
            assert not os.path.lexists(api)
            (left,) = staging_dirs(root())
            assert (
                (root() / left / "old" / "api" / "SKILL.md")
                .read_bytes()
                .endswith(b"raced the move\n")
            )
            assert "api" in records()

    def test_remove_keeps_a_moved_copy_it_cannot_put_back(self, tmp_path, monkeypatch):
        install(make_bundle(tmp_path, ("api",)))
        api, moved = root() / "api", []

        def race(src, dst, *a, **k):
            if Path(src) == api and is_aside(src, dst):
                real(src, dst)
                edit(dst, "raced the move\n")  # The edit lands in the moved copy,
                api.mkdir()  # and something else takes the name.
                moved.append(Path(dst))
                return True
            return None

        real = wrap(monkeypatch, os, "rename", race)
        res = remove_tool(gen("claude"))
        assert res.moved == [(api, moved[0])]
        assert (moved[0] / "SKILL.md").read_bytes().endswith(b"raced the move\n")
        assert res.leftover == moved[0].parent.parent
        assert "api" in records()

    @pytest.mark.parametrize("put_back", ["works", "fails"])
    def test_remove_ctrl_c_right_after_move_aside(
        self, tmp_path, monkeypatch, put_back
    ):
        install(make_bundle(tmp_path, ("api",)))
        api = root() / "api"
        old, moved = sha_tree(api), []

        def interrupt(src, dst, *a, **k):
            if Path(src) == api and is_aside(src, dst):
                real(src, dst)
                moved.append(Path(dst))
                raise KeyboardInterrupt

        def place(src, dest):
            if put_back == "fails" and Path(src).parent.name == "old":
                raise OSError(errno.EACCES, "Permission denied")
            return real_place(src, dest)

        real = wrap(monkeypatch, os, "rename", interrupt)
        real_place = sg._place
        monkeypatch.setattr(sg, "_place", place)
        with pytest.raises(KeyboardInterrupt):
            remove_tool(gen("claude"))
        assert "api" in records()
        if put_back == "works":
            assert sha_tree(api) == old
            assert staging_dirs(root()) == []
        else:  # Stuck in staging, so its record stays.
            assert not os.path.lexists(api)
            assert sha_tree(moved[0]) == old

    def test_remove_rmtree_failure_reports_staging(self, tmp_path, monkeypatch):
        install(make_bundle(tmp_path))
        wrap(monkeypatch, shutil, "rmtree", lambda path, *a, **k: True)
        res = remove_tool(gen("claude"))
        assert res.leftover is not None
        assert res.leftover.parent == root()
        assert sorted(os.listdir(res.leftover / "old")) == ["api", "docs"]
        assert records() == {}  # Proven ours and gone from dest: E12 covers it.

    def test_remove_dest_vanishing_before_the_move_drops_its_record(
        self, tmp_path, monkeypatch
    ):
        install(make_bundle(tmp_path))
        api = root() / "api"

        def vanish(src, dst, *a, **k):
            if Path(src) == api and is_aside(src, dst):
                shutil.rmtree(api)

        wrap(monkeypatch, os, "rename", vanish)
        res = remove_tool(gen("claude"))
        assert res.removed == [root() / "docs"]
        assert records() == {}

    def test_remove_drops_tool_record_and_mirror_when_empty(self, tmp_path):
        install(make_bundle(tmp_path))
        assert "claude" in disk_state()["installed_skills"]
        remove_tool(gen("claude"))
        state = disk_state()
        assert "claude" not in state.get("skill_folders", {})
        assert "claude" not in state["installed_skills"]

    def test_remove_missing_root_drops_records(self, tmp_path):
        install(make_bundle(tmp_path))
        shutil.rmtree(root())
        res = remove_tool(gen("claude"))
        assert res.removed == []
        assert "claude" not in disk_state().get("skill_folders", {})

    @pytest.mark.parametrize("kind", ["file", "dangling link"])
    def test_remove_unreachable_root_refuses_and_keeps_records(self, tmp_path, kind):
        install(make_bundle(tmp_path))
        shutil.rmtree(root())
        if kind == "file":
            root().write_bytes(b"not a folder")
        else:
            symlink_or_skip(root(), rel(tmp_path / "gone", root()), is_dir=True)
        saved = state_bytes()
        with pytest.raises(SkillInstallError) as exc:
            remove_tool(gen("claude"))
        assert str(exc.value) == _msg("E18", gen("claude"))
        assert state_bytes() == saved

    def test_remove_ctrl_c_keeps_unprocessed_names_recorded(
        self, tmp_path, monkeypatch
    ):
        install(make_bundle(tmp_path))
        docs = root() / "docs"
        old = sha_tree(docs)

        def interrupt(path, *a, **k):
            raise KeyboardInterrupt

        wrap(monkeypatch, shutil, "rmtree", interrupt)
        with pytest.raises(KeyboardInterrupt):
            remove_tool(gen("claude"))
        assert "docs" in records()
        assert sha_tree(docs) == old


# ---------------------------------------------------------------------------
# Staging, paths and the bundle
# ---------------------------------------------------------------------------


class TestStaging:
    def test_crash_leftover_staging_is_reported_not_deleted(self, tmp_path):
        crash = root() / ".deepctl-staging-old1"
        crash.mkdir(parents=True)
        (crash / "f").write_bytes(b"x")
        _, leftover = install(make_bundle(tmp_path))
        assert leftover is None
        assert (crash / "f").read_bytes() == b"x"
        assert tool_status(gen("claude"), get_skills_state()).leftovers == [crash]

    @pytest.mark.parametrize("code", [errno.ENOSPC, errno.EACCES, errno.ENAMETOOLONG])
    def test_copy_failure_changes_nothing(self, tmp_path, monkeypatch, code):
        def fail(*a, **k):
            raise OSError(code, os.strerror(code))

        wrap(monkeypatch, shutil, "copytree", fail)
        with pytest.raises(SkillInstallError) as exc:
            install(make_bundle(tmp_path))
        assert str(exc.value) == _msg(
            "E5", gen("claude"), reason=os.strerror(code).rstrip(".")
        )
        assert os.listdir(root()) == []
        assert state_bytes() is None

    def test_bundle_marker_collision_refuses(self, tmp_path):
        skills = make_bundle(tmp_path)
        (skills[0].path / sg._MARKER).write_bytes(b"bundle's own")
        with pytest.raises(SkillInstallError) as exc:
            install(skills)
        assert str(exc.value) == _msg("E10", name="api")
        assert os.listdir(root()) == []
        assert state_bytes() is None


# ---------------------------------------------------------------------------
# skills.json
# ---------------------------------------------------------------------------


class TestState:
    @pytest.mark.parametrize(
        "content",
        [
            b"{",
            b"[]",
            b"\xff\xfe{}",
            b'{"skill_folders": []}',
            b'{"skill_folders": {"claude": {"folders": {"../x": {"state": "installed"}}}}}',
            b'{"skill_folders": {"claude": {"folders": {"CON": {"state": "installed"}}}}}',
            b'{"skill_folders": {"claude": {"folders": {"api": {"state": "weird"}}}}}',
            b'{"skill_folders": {"claude": {"folders": {"api": "installed"}}}}',
            b'{"skill_folders": {"claude": {"folders": {"api": {"state": "installed", "fingerprint": "md5:abc"}}}}}',
            b'{"skill_folders": {"claude": {"skills_ref": 5, "folders": {}}}}',
            b'{"skill_folders": {"claude": {"v03": "yes", "folders": {}}}}',
            b'{"installed_skills": {"claude": "x"}}',
            b'{"installed_skills": {"claude": {"paths": "/x/a.md"}}}',
            b'{"installed_skills": {"claude": {"paths": [5]}}}',
        ],
    )
    def test_corrupt_state_is_refused_and_left_byte_identical(self, tmp_path, content):
        sg._STATE_FILE.parent.mkdir(parents=True)
        sg._STATE_FILE.write_bytes(content)
        expected = _msg("E7")
        for call in (
            get_skills_state,
            lambda: sg._update_state(lambda state: None),
            lambda: install(make_bundle(tmp_path)),
        ):
            with pytest.raises(SkillInstallError) as exc:
                call()
            assert str(exc.value) == expected
        assert sg._STATE_FILE.read_bytes() == content
        assert not root().exists()

    def test_save_failure_leaves_no_temp(self, monkeypatch):
        def fail(fd):
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(os, "fsync", fail)
        with pytest.raises(SkillInstallError) as exc:
            sg._update_state(lambda state: None)
        assert str(exc.value) == _msg("E9c", reason="No space left on device")
        assert [
            n for n in os.listdir(sg._STATE_FILE.parent) if n.endswith(".tmp")
        ] == []

    def test_save_ctrl_c_removes_temp(self, monkeypatch):
        def interrupt(fd):
            raise KeyboardInterrupt

        monkeypatch.setattr(os, "fsync", interrupt)
        with pytest.raises(KeyboardInterrupt):
            sg._update_state(lambda state: None)
        assert os.listdir(sg._STATE_FILE.parent) == ["skills.json.lock"]

    def test_symlinked_state_file_stays_a_symlink(self, tmp_path):
        real = tmp_path / "dotfiles" / "skills.json"
        real.parent.mkdir()
        real.write_text('{"installed_skills": {}}', encoding="utf-8")
        sg._STATE_FILE.parent.mkdir(parents=True)
        symlink_or_skip(sg._STATE_FILE, rel(real, sg._STATE_FILE), is_dir=False)
        sg._update_state(lambda state: state.update(auto_update=False))
        assert sg._STATE_FILE.is_symlink()
        assert json.loads(real.read_text(encoding="utf-8"))["auto_update"] is False


# ---------------------------------------------------------------------------
# Survivors and the tool table
# ---------------------------------------------------------------------------


class TestSurvivors:
    """Each pins one guard a mutation sweep could otherwise drop unnoticed."""

    def test_recorded_ref_is_validated(self):
        state = {
            "installed_skills": {},
            "skill_folders": {"claude": {"skills_ref": "../x"}},
        }
        with pytest.raises(skill_bundle.SkillRefInvalidError):
            sg._ref_for("claude", state)

    def test_non_portable_skill_name_is_refused_before_any_write(self, tmp_path):
        (skill,) = make_bundle(tmp_path, ("api",))
        with pytest.raises(SkillInstallError) as exc:
            install([RepoSkill("CON", skill.path)])
        assert str(exc.value) == _msg("E11", name="CON")
        assert not root().exists()
        assert state_bytes() is None

    def test_install_into_a_root_that_is_a_file_gives_e18(self, tmp_path):
        root().parent.mkdir(parents=True)
        root().write_bytes(b"not a folder")
        with pytest.raises(SkillInstallError) as exc:
            install(make_bundle(tmp_path))
        assert str(exc.value) == _msg("E18", gen("claude"))
        assert state_bytes() is None

    def test_failed_first_install_leaves_no_empty_tool_record(
        self, tmp_path, monkeypatch
    ):
        def fail(*a, **k):
            raise OSError(errno.EIO, "I/O error")

        monkeypatch.setattr(sg, "_swap", fail)
        with pytest.raises(SkillInstallError):
            install(make_bundle(tmp_path))
        state = disk_state()
        assert "claude" not in state.get("skill_folders", {})
        assert "claude" not in state["installed_skills"]

    def test_unreadable_copy_after_move_aside_gives_e5_and_goes_back(
        self, tmp_path, monkeypatch
    ):
        install(make_bundle(tmp_path))
        api = root() / "api"
        before = sha_tree(api)

        def deny(path):
            if Path(path).parent.name == "old":
                raise PermissionError(errno.EACCES, "Permission denied", str(path))

        wrap(monkeypatch, sg, "_fingerprint", deny)
        with pytest.raises(SkillInstallError) as exc:
            install(make_bundle(tmp_path, body="v2"))
        assert str(exc.value) == _msg(
            "E5", gen("claude"), reason=f"could not read {api}"
        )
        assert sha_tree(api) == before

    @pytest.mark.parametrize("when", ["before", "after"])
    def test_ctrl_c_at_the_move_never_blocks_the_next_run(
        self, tmp_path, monkeypatch, when
    ):
        skills = make_bundle(tmp_path)
        docs, real, fired = root() / "docs", sg._rename_excl, []

        def interrupt(src, dest):
            if Path(dest) == docs and not fired:
                fired.append(1)
                if when == "after":
                    real(src, dest)
                raise KeyboardInterrupt
            return real(src, dest)

        with monkeypatch.context() as m:
            m.setattr(sg, "_rename_excl", interrupt)
            with pytest.raises(KeyboardInterrupt):
                install(skills)
        assert fired and os.path.lexists(docs) == (when == "after")
        assert install_conflicts([gen("claude")], skills) == ([], [])
        install(skills)
        for n in ("api", "docs"):
            assert records()[n]["state"] == "installed"
            assert _ownership(root() / n, "claude", n, records()[n]) == "ok"
        assert staging_dirs(root()) == []

    def test_copy_failure_reason_is_one_line(self, tmp_path, monkeypatch):
        many = [
            (
                f"/b/{i}",
                f"/s/{i}",
                f"[Errno 28] No space left on device: '/b/{i}' -> '/s/{i}'",
            )
            for i in range(3)
        ]

        def full(*a, **k):
            raise shutil.Error(many)

        monkeypatch.setattr(shutil, "copytree", full)
        with pytest.raises(SkillInstallError) as exc:
            install(make_bundle(tmp_path))
        assert str(exc.value) == _msg(
            "E5", gen("claude"), reason="No space left on device"
        )
        win = "[WinError 112] There is not enough space on the disk: 'C:\\b'"
        many[:] = [("C:\\b", "C:\\s", win)]
        with pytest.raises(SkillInstallError) as exc:
            install(make_bundle(tmp_path))
        assert str(exc.value) == _msg(
            "E5", gen("claude"), reason="There is not enough space on the disk"
        )

    def test_reason_drops_a_trailing_period(self):
        assert sg._reason(OSError(errno.EACCES, "Permission denied.")) == (
            "Permission denied"
        )

    def test_remove_e21_keeps_records(self, tmp_path, monkeypatch):
        install(make_bundle(tmp_path))
        saved = state_bytes()

        def denied(*a, **k):
            raise PermissionError(errno.EACCES, "Permission denied")

        monkeypatch.setattr(sg.tempfile, "mkdtemp", denied)
        with pytest.raises(SkillInstallError) as exc:
            remove_tool(gen("claude"))
        assert str(exc.value) == _msg("E21", root=root(), reason="Permission denied")
        assert state_bytes() == saved
        assert (root() / "api").is_dir()


class TestToolTable:
    def test_generator_has_no_install_shim(self):
        assert not hasattr(sg.SkillGenerator, "install")
        for name in (
            "save_skills_state",
            "collect_command_metadata",
            "_commands_hash",
            "skills_need_update",
            "render_developer_guide",
            "render_skill_content",
            "CommandMetadata",
        ):
            assert not hasattr(sg, name), name
        for name in ("detect_ai_clis", "get_all_generators", "get_skills_state"):
            assert callable(getattr(sg, name))
        for g in get_all_generators():
            assert g.cli_name and g.display_name
        assert [g.cli_name for g in get_all_generators()] == [
            "claude",
            "codex",
            "gemini",
            "amazonq",
            "aider",
            "opencode",
            "cursor",
            "cline",
        ]

    @pytest.mark.parametrize(
        ("cli", "parts", "homes", "binary"),
        [
            ("claude", (".claude", "skills"), [(".claude",)], "claude"),
            ("codex", (".agents", "skills"), [(".codex",)], "codex"),
            ("gemini", (".gemini", "skills"), [(".gemini",)], "gemini"),
            ("amazonq", None, [(".amazonq",)], None),
            ("aider", None, [], "aider"),
            (
                "opencode",
                (".config", "opencode", "skills"),
                [(".opencode",), (".config", "opencode")],
                "opencode",
            ),
            ("cursor", (".cursor", "skills"), [(".cursor",)], "cursor"),
            ("cline", (".cline", "skills"), [(".cline",)], None),
        ],
    )
    def test_tool_table_roots_and_detection(
        self, monkeypatch, cli, parts, homes, binary
    ):
        g = gen(cli)
        assert g.skills_root() == (Path.home().joinpath(*parts) if parts else None)
        monkeypatch.setattr(shutil, "which", lambda name: None)
        assert g.detect() is False
        for home in homes:
            Path.home().joinpath(*home).mkdir(parents=True)
            assert g.detect() is True
            shutil.rmtree(Path.home().joinpath(*home))
        monkeypatch.setattr(
            shutil, "which", lambda name: "/bin/x" if name == binary else None
        )
        assert g.detect() is (binary is not None)


# ---------------------------------------------------------------------------
# install_for: the one path for 'dg skills', login and plugin (B3)
# ---------------------------------------------------------------------------


class TestInstallFor:
    def _fetch(self, monkeypatch, skills):
        fetched = []
        monkeypatch.setattr(
            skill_bundle,
            "fetch_skill_bundle",
            lambda ref=None: fetched.append(ref) or skills,
        )
        return fetched

    def test_install_for_fetches_each_ref_once_and_preflights_every_tool(
        self, tmp_path, monkeypatch
    ):
        fetched = self._fetch(monkeypatch, make_bundle(tmp_path))
        mine = root("cursor") / "api"
        mine.mkdir(parents=True)
        (mine / "notes.md").write_bytes(b"mine")
        before = sha_tree(mine)
        plan = [(gen("claude"), REF), (gen("cursor"), REF)]
        with pytest.raises(SkillOwnershipError) as exc:
            list(sg.install_for(plan))
        assert str(exc.value) == _msg("E1", paths=str(mine))
        assert fetched == [REF]
        assert not root("claude").exists()
        assert sha_tree(mine) == before
        assert state_bytes() is None

    def test_install_for_fetches_each_distinct_ref_once(self, tmp_path, monkeypatch):
        fetched = self._fetch(monkeypatch, make_bundle(tmp_path))
        plan = [(gen("claude"), REF), (gen("cursor"), "b"), (gen("cline"), REF)]
        done = [g.cli_name for g, _, _ in sg.install_for(plan)]
        assert done == ["claude", "cursor", "cline"]
        assert fetched == [REF, "b"]
        assert disk_state()["skill_folders"]["cursor"]["skills_ref"] == "b"

    def test_install_for_skips_hint_only_tools_without_fetching(self, monkeypatch):
        def boom(ref=None):
            raise AssertionError("fetched")

        monkeypatch.setattr(skill_bundle, "fetch_skill_bundle", boom)
        assert list(sg.install_for([(gen("amazonq"), REF), (gen("aider"), REF)])) == []
        assert state_bytes() is None

    def test_install_for_second_tool_failure_keeps_first_recorded(
        self, tmp_path, monkeypatch
    ):
        skills = make_bundle(tmp_path)
        self._fetch(monkeypatch, skills)

        def fail_cursor(g, *a, **k):
            if g.cli_name == "cursor":
                raise sg._err("E5", g, reason="disk full")

        wrap(monkeypatch, sg, "install_tool", fail_cursor)
        done = []
        plan = [(gen("claude"), REF), (gen("cursor"), REF), (gen("cline"), REF)]
        with pytest.raises(SkillInstallError) as exc:
            for g, placed, _ in sg.install_for(plan):
                done.append((g.cli_name, len(placed)))
        assert str(exc.value) == _msg("E5", gen("cursor"), reason="disk full")
        assert done == [("claude", 2)]
        assert {n: r["state"] for n, r in records().items()} == {
            "api": "installed",
            "docs": "installed",
        }
        assert install_conflicts([gen("claude")], skills) == ([], [])
        assert set(disk_state()["skill_folders"]) == {"claude"}
        assert not root("cline").exists()

    def test_skills_json_has_one_writer(self, tmp_path, monkeypatch):
        self._fetch(monkeypatch, make_bundle(tmp_path))
        writes = []
        wrap(monkeypatch, sg, "_write_state", lambda s: writes.append(1) and None)
        updates = []
        wrap(monkeypatch, sg, "_update_state", lambda *a, **k: updates.append(1) and None)
        list(sg.install_for([(gen("claude"), REF), (gen("cursor"), REF)]))
        assert len(writes) == len(updates) == 4  # mark + settle per tool

    @pytest.mark.parametrize("agentic", [False, True])
    def test_warn_install_failure_is_one_stderr_line(self, capsys, agentic):
        from deepctl_core import output

        output._output_config["agentic"] = agentic
        retry = "run 'dg x' to try again"
        sg.warn_install_failure("Step did not finish", SkillInstallError("[b]x[/b]."), retry)
        sg.warn_install_failure("Step did not finish", RuntimeError(), retry)
        out, err = capsys.readouterr()
        assert out == ""
        lines = err.splitlines()
        assert len(lines) == 2
        assert lines[0].endswith("Step did not finish: [b]x[/b]; run 'dg x' to try again.")
        assert lines[1].endswith("Step did not finish: RuntimeError; run 'dg x' to try again.")

    def test_warn_install_failure_names_the_retry_command_not_a_rerun(self, capsys):
        e1 = _msg("E1", paths="/p")
        sg.warn_install_failure("Step", SkillInstallError(e1), "run 'dg x' to try again")
        _, err = capsys.readouterr()
        assert "run the command again" not in err
        assert err.splitlines() == [
            "WARN: Step: deepctl cannot prove it installed /p, so it will not replace"
            " anything there; move or rename what is there, then run 'dg x' to try again."
        ]

    def test_warn_install_failure_names_the_retry_in_every_sentence(
        self, capsys, monkeypatch
    ):
        from deepctl_core import output

        monkeypatch.setattr(output.stderr_console, "_width", 2000)
        home = Path.home()
        p1, p2, p3 = home / "a", home / "b", home / "c"
        sg.warn_install_failure("Step", SkillOwnershipError([p1], [p2, p3]), "retry")
        _, err = capsys.readouterr()
        [line] = err.splitlines()
        assert "run the command again" not in line
        assert line.count(", then retry.") == 3
        for p in (p1, p2, p3):
            assert str(p) in line
        assert line.endswith(", then retry.")

    def test_warn_install_failure_e27_says_stopped_not_changed_nothing(self, capsys):
        # Earlier tools may already be installed, so "changed nothing" is not true.
        sg.warn_install_failure("Step", SkillInstallError(_msg("E27")), "retry")
        _, err = capsys.readouterr()
        assert "changed nothing" not in err
        assert err.splitlines() == [
            "WARN: Step: "
            + _msg("E27")
            .replace("changed nothing", "stopped")
            .replace(", then run the command again.", ", then retry.")
        ]

    def test_warn_install_failure_e9b_has_no_not_updated_prefix(
        self, capsys, monkeypatch
    ):
        from deepctl_core import output

        monkeypatch.setattr(output.stderr_console, "_width", 2000)
        e9b = sg._err("E9b", gen("claude"), reason="disk full")
        sg.warn_install_failure("Step did not finish", e9b, "retry")
        _, err = capsys.readouterr()
        assert err.splitlines() == ["WARN: " + str(e9b).rstrip(".") + "; retry."]

    def test_warn_install_failure_reports_leftover_staging_first(self, capsys):
        exc = SkillInstallError("boom.")
        exc.leftover = Path.home() / ".deepctl-staging-1"
        sg.warn_install_failure("Step did not finish", exc, "retry")
        out, err = capsys.readouterr()
        assert out == ""
        assert err.splitlines() == [
            "WARN: " + _msg("E12", staging=exc.leftover),
            "WARN: Step did not finish: boom; retry.",
        ]


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_concurrent_install_fails_cleanly_and_keeps_records(
        self, tmp_path, monkeypatch
    ):
        skills = make_bundle(tmp_path)
        nested = []

        def other_run_first(src, dest):
            if Path(src).parent.name == "new" and not nested:
                nested.append(1)
                install(skills)

        wrap(monkeypatch, sg, "_place", other_run_first)
        with pytest.raises(SkillOwnershipError) as exc:
            install(skills)
        assert str(exc.value) == _msg("E2", gen("claude"), dest=root() / "api")
        assert exc.value.leftover is None
        assert {n: r["state"] for n, r in records().items()} == {
            "api": "installed",
            "docs": "installed",
        }
        for n in ("api", "docs"):
            assert _ownership(root() / n, "claude", n, records()[n]) == "ok"
        assert staging_dirs(root()) == []

    def test_root_alias_second_tool_refuses_without_writing(self, tmp_path):
        root("claude").mkdir(parents=True)
        (Path.home() / ".cursor").mkdir()
        symlink_or_skip(
            root("cursor"), rel(root("claude"), root("cursor")), is_dir=True
        )
        skills = make_bundle(tmp_path)
        assert install_conflicts([gen("claude"), gen("cursor")], skills) == ([], [])
        install(skills)
        claude = sha_tree(root("claude"))
        with pytest.raises(SkillOwnershipError) as exc:
            install(skills, "cursor")
        assert str(exc.value) == _msg(
            "E1", paths=f"{root('cursor') / 'api'}, {root('cursor') / 'docs'}"
        )
        assert sha_tree(root("claude")) == claude
        assert "cursor" not in disk_state()["skill_folders"]

    def test_concurrent_runs_never_drop_each_others_records(
        self, tmp_path, monkeypatch
    ):
        """B holds the lock in _stage; A waits for it, then installs over B (B1)."""
        skills = make_bundle(tmp_path, ("api", "docs", "starters"))
        b_staging, a_waiting = threading.Event(), threading.Event()
        real_stage, real_try, results = sg._stage, sg._try_lock, {}

        def stage(g, s, staging):
            if threading.current_thread().name == "B":
                b_staging.set()
                assert a_waiting.wait(10)  # A is blocked on the lock B holds.
            return real_stage(g, s, staging)

        def try_lock(lock):
            got = real_try(lock)
            if threading.current_thread().name == "A" and got < 0:
                a_waiting.set()
            return got

        def run(tag):
            try:
                results[tag] = [p.name for p in install(skills)[0]]
            except SkillInstallError as exc:
                results[tag] = str(exc)

        monkeypatch.setattr(sg, "_stage", stage)
        monkeypatch.setattr(sg, "_try_lock", try_lock)
        b = threading.Thread(target=run, args=("B",), name="B")
        b.start()
        assert b_staging.wait(10)
        a = threading.Thread(target=run, args=("A",), name="A")
        a.start()
        a.join(20)
        b.join(20)
        assert results == {"A": ["api", "docs", "starters"], "B": results["A"]}
        for n in ("api", "docs", "starters"):
            assert records()[n]["state"] == "installed"
            assert _ownership(root() / n, "claude", n, records()[n]) == "ok"
        assert staging_dirs(root()) == []

    def test_another_runs_pending_record_is_kept_and_a_crashed_one_settles(
        self, tmp_path, monkeypatch
    ):
        skills = make_bundle(tmp_path)
        other = {"state": "installing", "pending": FP_A, "run": "f" * 32}

        def other_run_marks_docs(g, name, staging, rec):
            if name == "api":
                sg._update_state(
                    lambda st: st["skill_folders"]["claude"]["folders"].update(
                        docs=dict(other)
                    )
                )
            else:
                raise OSError(errno.EIO, "I/O error")

        with monkeypatch.context() as m:
            wrap(m, sg, "_swap", other_run_marks_docs)
            with pytest.raises(SkillInstallError):
                install(skills)
        assert records()["docs"] == other  # Absent, but not this run's to drop.
        assert records()["api"]["state"] == "installed"
        install(skills)  # The other run never came back: this run settles it.
        for n in ("api", "docs"):
            assert records()[n]["state"] == "installed"
            assert _ownership(root() / n, "claude", n, records()[n]) == "ok"

    def test_remove_during_install_never_puts_the_old_copy_back(
        self, tmp_path, monkeypatch
    ):
        """Remove deletes the new api; cleanup must not restore the old one (S1)."""
        install(make_bundle(tmp_path))
        removed = []

        def remove_after_api(g, name, staging, rec):
            real(g, name, staging, rec)
            if name == "api":
                removed.append(remove_tool(gen("claude")))
            return True

        real = wrap(monkeypatch, sg, "_swap", remove_after_api)
        install(make_bundle(tmp_path, body="v2"))
        assert [p.name for p in removed[0].removed] == ["api", "docs"]
        assert not os.path.lexists(root() / "api")
        recs = records()
        for n in os.listdir(root()):  # No marked folder is left without a record.
            assert _ownership(root() / n, "claude", n, recs.get(n)) == "ok", n
        assert staging_dirs(root()) == []

    def test_a_later_runs_marks_survive_this_runs_settle(self, tmp_path, monkeypatch):
        skills = make_bundle(tmp_path)
        real_upd, nested = sg._update_state, []

        def later_run_marks_then_dies(g, name, staging, rec):
            if not nested:
                nested.append(1)

                def killed_after_mark(mutate, *a, **k):
                    real_upd(mutate, *a, **k)
                    raise KeyboardInterrupt

                with monkeypatch.context() as m:
                    m.setattr(sg, "_update_state", killed_after_mark)
                    with pytest.raises(KeyboardInterrupt):
                        install(skills)
            raise OSError(errno.EIO, "I/O error")

        with monkeypatch.context() as m:
            m.setattr(sg, "_swap", later_run_marks_then_dies)
            with pytest.raises(SkillInstallError):
                install(skills)
        recs = records()
        assert {n: r["state"] for n, r in recs.items()} == {
            "api": "installing",
            "docs": "installing",
        }  # The later run tagged them, so this run's settle left them.
        assert len({r["run"] for r in recs.values()}) == 1
        install(skills)
        for n in ("api", "docs"):
            assert _ownership(root() / n, "claude", n, records()[n]) == "ok"

    def test_a_crashed_runs_record_is_retagged_and_dropped(self, tmp_path, monkeypatch):
        skills = make_bundle(tmp_path)
        crashed = {"state": "installing", "pending": FP_A, "run": "f" * 32}
        write_state({"skill_folders": {"claude": {"folders": {"docs": crashed}}}})

        def fail_docs(g, name, staging, rec):
            if name == "docs":
                raise OSError(errno.EIO, "I/O error")

        with monkeypatch.context() as m:
            wrap(m, sg, "_swap", fail_docs)
            with pytest.raises(SkillInstallError):
                install(skills)
        assert not os.path.lexists(root() / "docs")
        assert set(records()) == {"api"}  # This run's tag: absent, so dropped.


# ---------------------------------------------------------------------------
# B1: one skills.json lock across each tool's whole install and remove
# ---------------------------------------------------------------------------


def _hold_lock_child(home, mode, held, release, base=""):
    """Spawned child: hold the lock, or park inside an install that holds it.

    Module level so a spawned interpreter can import it by name. The parent's
    monkeypatches do not cross the process boundary, so the home comes in.
    """
    home = Path(home)
    Path.home = staticmethod(lambda: home)  # type: ignore[method-assign]
    sg._STATE_FILE = home / ".deepctl" / "skills" / "skills.json"

    def park(*a, **k):
        held.set()
        release.wait(60)

    if mode == "hold":
        with sg._state_lock():
            park()
        return
    real_swap, real_place = sg._swap, sg._place

    def swap(*a, **k):
        sg._swap = real_swap
        park()
        return real_swap(*a, **k)

    def place(src, dest):  # Parks after _swap moved the old copy aside (S1).
        if Path(src).parent.name == "new":
            park()
        return real_place(src, dest)

    if mode == "aside":
        sg._place = place
    else:
        sg._swap = swap
    skills = [RepoSkill(n, Path(base) / n) for n in ("api", "docs")]
    install_tool(gen("claude"), skills, ref="child-ref", version="1")


@contextlib.contextmanager
def lock_child(monkeypatch, mode="hold", base=""):
    """A real second interpreter holding the lock until the block ends."""
    # pytest's importlib mode does not put the test root on sys.path; a
    # spawned child gets the parent's sys.path, so add it to import this file.
    depth = len(__name__.split("."))
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[depth - 1]))
    ctx = multiprocessing.get_context("spawn")
    held, release = ctx.Event(), ctx.Event()
    args = (str(Path.home()), mode, held, release, base)
    child = ctx.Process(target=_hold_lock_child, args=args, daemon=True)
    child.start()
    try:
        deadline = time.monotonic() + 60
        while not held.wait(0.05):  # Fails fast if the child dies on startup.
            assert child.is_alive(), f"the child exited ({child.exitcode})"
            assert time.monotonic() < deadline, "the child never took the lock"
        yield child, release
    finally:
        if child.is_alive():  # A killed sleeper would deadlock Event.set().
            release.set()
            child.join(30)
        if child.is_alive():
            child.kill()


@contextlib.contextmanager
def held_elsewhere():
    """Hold the lock on a second open file, as another process would."""
    lock = sg._STATE_FILE.with_name("skills.json.lock")
    fd = sg._try_lock(lock)
    assert fd >= 0
    try:
        yield lock
    finally:
        if sys.platform == "win32":
            sg.msvcrt.locking(fd, sg.msvcrt.LK_UNLCK, 1)
        os.close(fd)


class TestStateLock:
    def test_lock_held_by_another_process_gives_e27_and_changes_nothing(
        self, tmp_path, monkeypatch
    ):
        install(make_bundle(tmp_path))
        saved, tree = state_bytes(), sha_tree(root())
        monkeypatch.setattr(sg, "_LOCK_TIMEOUT", 0.3)
        with lock_child(monkeypatch) as (child, _):
            for op in (
                lambda: install(make_bundle(tmp_path, body="v2")),
                lambda: remove_tool(gen("claude")),
                lambda: sg._update_state(lambda state: None),
            ):
                with pytest.raises(SkillInstallError) as exc:
                    op()
                assert str(exc.value) == _msg("E27")
            assert (state_bytes(), sha_tree(root())) == (saved, tree)
        assert child.exitcode == 0
        with sg._state_lock():  # Released when the child let go.
            pass

    def test_second_process_waits_then_installs_over_the_first(
        self, tmp_path, monkeypatch
    ):
        v1, v2 = make_bundle(tmp_path), make_bundle(tmp_path, body="v2")
        done = {}
        with lock_child(monkeypatch, "install", str(v1[0].path.parent)) as (c, go):
            t = threading.Thread(
                target=lambda: done.update(r=install(v2, ref="parent-ref"))
            )
            t.start()
            t.join(0.5)
            assert t.is_alive()  # Waiting: the child is mid-install.
            go.set()
            t.join(30)
            c.join(30)
        assert c.exitcode == 0
        assert [p.name for p in done["r"][0]] == ["api", "docs"]
        for n in ("api", "docs"):
            assert records()[n]["state"] == "installed"
            assert _ownership(root() / n, "claude", n, records()[n]) == "ok"
            assert b"v2" in (root() / n / "SKILL.md").read_bytes()
        assert disk_state()["skill_folders"]["claude"]["skills_ref"] == "parent-ref"
        assert staging_dirs(root()) == []

    def test_a_killed_holder_never_leaves_a_stale_lock(self, monkeypatch):
        with lock_child(monkeypatch) as (child, _):
            child.kill()  # SIGKILL on POSIX, TerminateProcess on Windows.
            child.join(30)
        monkeypatch.setattr(sg, "_LOCK_TIMEOUT", 1.0)
        start = time.monotonic()
        with sg._state_lock():
            pass
        assert time.monotonic() - start < 1.0
        assert sg._STATE_FILE.with_name("skills.json.lock").exists()

    def test_a_killed_replacement_keeps_its_record_and_old_copy(
        self, tmp_path, monkeypatch
    ):
        install(make_bundle(tmp_path))
        old = sha_tree(root() / "api")
        v2 = make_bundle(tmp_path, body="v2")
        with lock_child(monkeypatch, "aside", str(v2[0].path.parent)) as (child, _):
            child.kill()  # Old api is in the child's staging/old; new not placed.
            child.join(30)
        [aside] = [root() / d / "old" / "api" for d in staging_dirs(root())]
        assert not os.path.lexists(root() / "api") and sha_tree(aside) == old
        res = remove_tool(gen("claude"))
        assert res.removed == [root() / "docs"]
        assert res.stranded == [(root() / "api", aside)]
        assert set(records()) == {"api"} and sha_tree(aside) == old
        st = tool_status(gen("claude"), get_skills_state())
        assert st.leftovers == [aside.parents[1]]
        install(v2)  # Replaces api: its record is this run's now.
        res = remove_tool(gen("claude"))
        assert set(res.removed) == {root() / "api", root() / "docs"}
        assert (res.stranded, res.kept, res.moved, res.leftover) == ([], [], [], None)
        assert records() == {} and sha_tree(aside) == old  # Left for the user.

    def test_a_killed_update_then_install_then_remove_does_not_fail(
        self, tmp_path, monkeypatch
    ):
        install(make_bundle(tmp_path))
        old = sha_tree(root() / "api")
        v2 = make_bundle(tmp_path, body="v2")
        with lock_child(monkeypatch, "aside", str(v2[0].path.parent)) as (child, _):
            child.kill()
            child.join(30)
        [aside] = [root() / d / "old" / "api" for d in staging_dirs(root())]
        install(v2)
        assert records()["api"]["state"] == "installed"
        res = remove_tool(gen("claude"))
        assert set(res.removed) == {root() / "api", root() / "docs"}
        assert (res.stranded, res.kept, res.moved, res.leftover) == ([], [], [], None)
        assert records() == {} and sha_tree(aside) == old

    def test_lock_is_reentrant_in_one_thread_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sg, "_LOCK_TIMEOUT", 0.3)
        seen = []

        def other():
            try:
                with sg._state_lock():
                    seen.append("took it")
            except SkillInstallError as exc:
                seen.append(str(exc))

        with sg._state_lock(), sg._state_lock():  # Nested: never waits.
            install(make_bundle(tmp_path))  # Takes it again, and _update_state too.
            t = threading.Thread(target=other)
            t.start()
            t.join(10)
        assert seen == [_msg("E27")]
        assert getattr(sg._LOCAL, "fd", None) is None
        t = threading.Thread(target=other)
        t.start()
        t.join(10)
        assert seen[1:] == ["took it"]

    def test_lock_file_sits_next_to_skills_json_and_is_never_rewritten(self, tmp_path):
        lock = sg._STATE_FILE.with_name("skills.json.lock")
        install(make_bundle(tmp_path))
        assert lock.is_file()
        if os.name != "nt":
            assert stat.S_IMODE(os.lstat(lock).st_mode) == 0o600
        lock.write_bytes(b"keep")
        install(make_bundle(tmp_path, body="v2"))
        remove_tool(gen("claude"))
        assert lock.read_bytes() == b"keep"

    @POSIX
    def test_symlinked_lock_or_read_only_folder_gives_e28(self, tmp_path):
        if os.geteuid() == 0:
            pytest.skip("root ignores file permissions")
        install(make_bundle(tmp_path))
        lock, target = sg._STATE_FILE.with_name("skills.json.lock"), tmp_path / "t"
        target.write_bytes(b"theirs")
        lock.unlink()
        lock.symlink_to(target)
        saved, tree = state_bytes(), sha_tree(root())
        with pytest.raises(SkillInstallError) as exc:
            install(make_bundle(tmp_path, body="v2"))
        reason = os.strerror(errno.ELOOP)
        assert str(exc.value) == _msg("E28", lock=lock, reason=reason)
        assert target.read_bytes() == b"theirs" and lock.is_symlink()
        lock.unlink()
        lock.parent.chmod(0o500)
        try:
            with pytest.raises(SkillInstallError) as exc:
                remove_tool(gen("claude"))
            reason = os.strerror(errno.EACCES)
            assert str(exc.value) == _msg("E28", lock=lock, reason=reason)
            st = tool_status(gen("claude"), get_skills_state())  # Status needs none.
            assert [p.name for p in st.kinds["ok"]] == ["api", "docs"]
            assert install_conflicts([gen("claude")], make_bundle(tmp_path)) == ([], [])
        finally:
            lock.parent.chmod(0o700)
        assert (state_bytes(), sha_tree(root())) == (saved, tree)

    @pytest.mark.parametrize(
        ("code", "key"), [(errno.ENOLCK, "E28"), (errno.EAGAIN, "E27")]
    )
    def test_only_a_busy_lock_is_waited_for(self, tmp_path, monkeypatch, code, key):
        def fail(*a):
            raise OSError(code, os.strerror(code))

        owner = sg.msvcrt if sys.platform == "win32" else sg.fcntl
        monkeypatch.setattr(
            owner, "locking" if sys.platform == "win32" else "flock", fail
        )
        wait = 5.0 if key == "E28" else 0.5  # E28 must return long before 5 s.
        monkeypatch.setattr(sg, "_LOCK_TIMEOUT", wait)
        skills = make_bundle(tmp_path)
        start = time.monotonic()
        with pytest.raises(SkillInstallError) as exc:
            install(skills)
        took = time.monotonic() - start
        lock = sg._STATE_FILE.with_name("skills.json.lock")
        assert str(exc.value) == _msg(key, lock=lock, reason=os.strerror(code))
        assert took < 2.0 if key == "E28" else took >= 0.5
        assert state_bytes() is None and not root().exists()

    def test_ctrl_c_while_waiting_closes_the_lock_and_writes_nothing(
        self, tmp_path, monkeypatch
    ):
        install(make_bundle(tmp_path))
        saved, tree, opened, closed = state_bytes(), sha_tree(root()), [], []
        real_open = os.open

        def open_(path, *a, **k):
            fd = real_open(path, *a, **k)
            if Path(path).name == "skills.json.lock":
                opened.append(fd)
            return fd

        def interrupt(seconds):
            raise KeyboardInterrupt

        with held_elsewhere(), monkeypatch.context() as m:
            m.setattr(os, "open", open_)
            wrap(m, os, "close", closed.append)
            m.setattr(sg.time, "sleep", interrupt)
            with pytest.raises(KeyboardInterrupt):
                install(make_bundle(tmp_path, body="v2"))
        assert len(opened) == 1 and opened[0] in closed
        assert getattr(sg._LOCAL, "fd", None) is None
        assert (state_bytes(), sha_tree(root())) == (saved, tree)
        assert staging_dirs(root()) == []

    def test_fetch_runs_without_the_lock(self, tmp_path, monkeypatch):
        skills = make_bundle(tmp_path)

        def fetch(ref=None):
            assert getattr(sg._LOCAL, "fd", None) is None
            return skills

        monkeypatch.setattr(skill_bundle, "fetch_skill_bundle", fetch)
        list(sg.install_for([(gen("claude"), "x")]))
        assert set(records()) == {"api", "docs"}

    def test_lock_messages_end_with_the_retry_phrase(self):
        for key in ("E27", "E28"):
            text = _msg(key, lock=Path("x"), reason="r")
            assert text.endswith(", then run the command again.")
        assert "on a local disk" in _msg("E28", lock=Path("x"), reason="r")
        assert sg._NO_EXCL.endswith("deepctl needs this folder on a local disk")
        assert "Linux 3.15 or later" in sg._NO_EXCL_SYS

    def test_ctrl_c_right_after_the_lock_is_taken_never_keeps_it(self, monkeypatch):
        win = sys.platform == "win32"
        owner, name = (sg.msvcrt, "locking") if win else (sg.fcntl, "flock")
        real = getattr(owner, name)

        def locked_then_interrupted(*a):
            real(*a)
            raise KeyboardInterrupt

        with monkeypatch.context() as m:
            m.setattr(owner, name, locked_then_interrupted)
            with pytest.raises(KeyboardInterrupt), sg._state_lock():
                pass
        monkeypatch.setattr(sg, "_LOCK_TIMEOUT", 5.0)
        start = time.monotonic()
        with sg._state_lock():  # The interrupted take did not keep the file locked.
            pass
        assert time.monotonic() - start < (5.0 if win else 1.0)
        assert getattr(sg._LOCAL, "fd", None) is None

    @POSIX
    def test_lock_file_replaced_before_the_lock_is_never_entered(self, monkeypatch):
        """Another run deletes and remakes the file between our open and our lock."""
        lock, real, other = (
            sg._STATE_FILE.with_name("skills.json.lock"),
            sg.fcntl.flock,
            [],
        )

        def flock(fd, op):
            if not other:
                other.append(-1)
                os.unlink(lock)
                other[0] = sg._try_lock(lock)  # Holds the new file.
            return real(fd, op)

        monkeypatch.setattr(sg.fcntl, "flock", flock)
        monkeypatch.setattr(sg, "_LOCK_TIMEOUT", 0.3)
        try:
            with pytest.raises(SkillInstallError) as exc, sg._state_lock():
                pass  # Never runs: the file it locked is no longer the lock.
        finally:
            os.close(other[0])
        assert other[0] >= 0 and str(exc.value) == _msg("E27")
        assert getattr(sg._LOCAL, "fd", None) is None
