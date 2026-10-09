"""Install the Deepgram skills as folders in each AI coding tool's skills root.

deepctl replaces or deletes a skill folder only when skills.json records it for
that tool, it is a real folder directly in the tool's root, it holds the exact
``.deepctl-skill`` marker, and its contents still hash to what deepctl placed.
"""

from __future__ import annotations

import contextlib
import copy
import ctypes
import errno
import functools
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.markup import escape

from deepctl_core import skill_bundle
from deepctl_core.output import _output_config, print_info, print_warning
from deepctl_core.skill_bundle import portable_name

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from deepctl_core.skill_bundle import RepoSkill

# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

# Tests patch these two, so read them at call time, never in a default.
_SKILLS_DIR = Path.home() / ".deepctl" / "skills"
_STATE_FILE = _SKILLS_DIR / "skills.json"
_RECORDS_KEY = "skill_folders"
_MARKER = ".deepctl-skill"
_STAGING_PREFIX = ".deepctl-staging-"
_STATES = ("installed", "installing")
_MAX_MARKER_BYTES = 512
_MAX_TREE_BYTES = 64 << 20  # fingerprint read cap; a folder holding more is "edited"
_FP_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
_FP_DOMAIN = b"deepctl-skill-tree-v1\0"
_NO_REPLACE = (errno.EEXIST, errno.ENOTEMPTY, errno.ENOTDIR)
_WINDOWS = os.name == "nt"  # patched by tests to exercise the Windows branch
_LOCK_TIMEOUT = 30.0  # seconds; tests patch it
_LOCAL = threading.local()  # .fd: this thread's lock, so nested takes never wait
_BUSY = (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES, errno.EDEADLK, errno.ENOENT)
_NO_EXCL = "this filesystem cannot move a folder without the risk of replacing one; deepctl needs this folder on a local disk"
_NO_EXCL_SYS = "this system cannot move a folder without the risk of replacing one; deepctl needs macOS, Windows, or Linux 3.15 or later on a supported architecture with a Python that matches the kernel's word size"
# renameat2 numbers by uname machine for [64-bit, 32-bit] Python. Linux calls them raw, so errno is the kernel's
# (glibc 2.28+ turns ENOSYS into EINVAL); other pairs, such as x32, need the renameat2 wrapper or fail closed.
_NR_RENAMEAT2 = (
    {"x86_64": 316, "aarch64": 276, "arm64": 276, "riscv64": 276, "s390x": 347}
    | {"ppc64": 357, "ppc64le": 357},
    {"i386": 353, "i686": 353, "armv7l": 382, "armv6l": 382, "arm": 382},
)

_V03_LINES = "only the lines from '<!-- BEGIN deepctl CLI Reference' to '<!-- END deepctl CLI Reference -->' yourself and keep the rest of the file"  # In E34 and E40.
# One sentence each. {file} is skills.json; {display} and {root} name the tool.
_MSG = {
    "E1": "deepctl cannot prove it installed {paths}, so it will not replace anything there; move or rename what is there, then run the command again.",
    "E2": "{dest} appeared while deepctl was installing, so it was left alone and {display} was not fully installed.",
    "E3": "{dest} changed while deepctl was replacing it, so it was left in place and {display} was not fully installed.",
    "E4": "{dest} changed while deepctl was replacing or removing it, so what was there is now in {aside}; move it back by hand if you need it.",
    "E5": "Could not install the skills for {display} in {root}: {reason}.",
    "E6": "Could not install {name} for {display}, and its previous copy could not be put back, so deepctl kept it in {aside}; move it back by hand.",
    "E6b": "{name} was not updated for {display} because something appeared at its folder during the update, so its previous copy is back in place; check that folder, then run the command again.",
    "E7": "{file} is not a skills record deepctl can read, so deepctl will not change it; fix or delete that file, then run the command again.",
    "E8": "Could not read {file}: {reason}.",
    "E9": "Could not save {file}: {reason}, so nothing was installed for {display}.",
    "E9b": "The skills for {display} are installed, but {file} could not be saved: {reason}; the next 'dg skills' command still treats them as deepctl's.",
    "E9c": "Could not save {file}: {reason}.",
    "E10": "The skill {name} in the bundle contains a .deepctl-skill file, a link or a special file, or is larger than 64 MiB, so deepctl will not install it.",
    "E11": "The skill name {name!r} is not a plain folder name, so deepctl will not install it.",
    "E12": "deepctl could not finish cleaning up {staging}, so it was left in place; check it and delete it by hand.",
    "E13": "Could not remove {dest}: {reason}; it is still recorded, so check its permissions or close any tool using it, then run 'dg skills remove' again.",
    "E14": "{dest} is recorded but deepctl cannot prove it installed it, so it was left in place.",
    "E15": "{display} does not load skill folders, so deepctl does not install skills for it; see https://github.com/deepgram/skills to add the skills by hand.",
    "E16": "{path} looks like staging from an interrupted deepctl run; deepctl never deletes it, so check it and delete it by hand.",
    "E18": "{root} exists but is not a folder, so deepctl changed nothing for {display}; move it away or point it at a folder, then run the command again.",
    "E21": "Could not remove the skills from {root}: {reason}.",
    "E33": "deepctl can't prove it wrote {path} ({why}), so it left it in place and no longer tracks it; if it's an old deepctl 0.3.x copy you don't need, delete it.",
    "E34": "deepctl can't safely remove its 0.3.x section from {path} ({why}), so it left the file as it is and won't warn about it again; a later install or update removes the section once it can, or remove {what}.",
    "E35": "Could not remove deepctl 0.3.x content from {path}: {reason}; {path} is unchanged and still recorded, so the next install or update tries again.",
    "E35b": "Could not remove deepctl 0.3.x content from {path}: {reason}; it is still recorded, so the next install or update tries again.",
    "E36": "The skills for {display} are installed, but deepctl could not finish removing its 0.3.x files: {reason}; the next install or update tries again.",
    "E37": "{dest} was saved while deepctl was removing its 0.3.x content, so deepctl kept your save; the earlier version is in {aside}. Compare them before you delete {aside}.",
    "E38": "{aside}, left by an earlier deepctl run, holds an earlier version of {dest}, so deepctl changed neither; compare them, keep what you want in {dest}, then delete {aside}.",
    "E39": "{dest} was missing, so deepctl put it back from {aside}, where an interrupted deepctl run had moved it.",
    "E40": "{why.strerror}, so deepctl left {path} as it is and won't warn about it again; if it holds deepctl 0.3.x content you don't need, remove {what}.",
    "E41": "{aside} is no longer the file deepctl moved there (a link or folder is there now), so deepctl did not put it back and {dest} is missing; restore {dest} from a backup if you need it, then delete {aside}.",
    "E42": "{aside} is not a file deepctl moved there, so deepctl changed neither it nor {dest}; delete {aside} if you don't need it.",
    "E43": "deepctl removed its 0.3.x content from {dest}, but kept the original file in {aside} because a program that already had it open can still write it; compare it, then delete {aside} if you don't need it.",
    "E44": "deepctl does not clean 0.3.x content on Windows because a parent folder can become a junction during cleanup; it left these recorded paths untouched and will not try again: {paths}. Delete the content you don't need yourself.",
    "E22": "{dest} was edited since deepctl installed it, so deepctl left it alone and did not install over it; rename or move your edited folder, then run the command again.",
    "E23": "{dest} was edited since deepctl installed it, so deepctl left it in place and no longer tracks it; delete it yourself if you don't need it.",
    "E24": "{dest} was edited since deepctl installed it, so 'dg skills remove' leaves it alone and 'dg skills update' stops until you rename or move it to keep your edits, or delete it to get deepctl's copy back.",
    "E25": "deepctl could not read {dest}, so it cannot tell whether that folder is still its own copy; check its permissions or close any tool using it, then run the command again.",
    "E26": "deepctl cannot prove it installed {dest}, so it left it in place and no longer tracks it.",
    "E27": "Another deepctl command is installing or removing skills, so this one waited 30 seconds and changed nothing; wait for it to finish, then run the command again.",
    "E29": "An interrupted deepctl run left the previous copy of {dest} in {aside}; delete it, or move it out of the skills folder if you want to keep it, then run the command again.",
    "E30": "Another deepctl command installed {display}'s skills from a different ref while this one was running, so deepctl kept that ref and did not update {display}.",
    "E31": "Another deepctl command removed {display}'s skills while this one was running, so deepctl did not install them again.",
    "E32": "Another deepctl command changed {display}'s skills while this one was running, so deepctl left them as that command left them and did not update {display}; run 'dg skills update' to refresh them.",
    "E28": "Could not lock {lock}: {reason}, so deepctl changed nothing; check that you own that file and its folder and that they are on a local disk, then run the command again.",
}


def _msg(key: str, gen: SkillGenerator | None = None, **kw: Any) -> str:
    if gen is not None:
        kw.update(display=gen.display_name, root=gen.skills_root())
    return _MSG[key].format(file=_STATE_FILE, **kw)


def _err(key: str, gen: SkillGenerator | None = None, **kw: Any) -> SkillInstallError:
    return SkillInstallError(_msg(key, gen, **kw))


def _reason(exc: BaseException) -> str:
    if isinstance(exc, shutil.Error) and exc.args and isinstance(exc.args[0], list):
        exc = OSError(re.sub(r"^\[\w+ \d+\] |: '.*", "", str(exc.args[0][0][-1])))
    return str(getattr(exc, "strerror", None) or exc).rstrip(".")


class SkillInstallError(Exception):
    """An install, remove or skills.json failure; the message is one sentence per problem."""

    leftover: Path | None = None  # This run's staging, if cleanup left it (SF3).
    kept: Path | None = None  # E4 or E6: where what was at dest was kept.


class SkillOwnershipError(SkillInstallError):
    """deepctl cannot prove it owns ``paths``, or the user edited ``edited``."""

    def __init__(
        self, paths: list[Path], edited: Sequence[Path] = (), message: str | None = None
    ) -> None:
        self.paths, self.edited = list(paths), list(edited)
        parts = [_msg("E1", paths=", ".join(map(str, paths)))] if paths else []
        parts += [_msg("E22", dest=p) for p in edited]
        super().__init__(message or " ".join(parts))


class SkillSkipped(SkillInstallError):
    """Another command changed or removed the tool's record after a refresh planned; nothing was written."""


def _validated(raw: Any) -> dict[str, Any]:
    """Return ``raw`` with defaults filled in, or raise E7 if it is not valid."""
    tools = raw.get(_RECORDS_KEY, {}) if isinstance(raw, dict) else None
    legacy = raw.get("installed_skills", {}) if isinstance(raw, dict) else None
    if not isinstance(tools, dict) or not isinstance(legacy, dict):
        raise _err("E7")
    for info in legacy.values():
        paths = info.get("paths", []) if isinstance(info, dict) else None
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise _err("E7")
    for tool in tools.values():
        folders = tool.get("folders", {}) if isinstance(tool, dict) else None
        if (
            not isinstance(folders, dict)
            or not isinstance(tool.get("skills_ref", ""), str)
            or not isinstance(tool.get("v03", False), bool)
        ):
            raise _err("E7")
        for name, rec in folders.items():
            ok = portable_name(name) and isinstance(rec, dict)
            fps = [rec[k] for k in ("fingerprint", "pending") if k in rec] if ok else []
            if (
                not ok
                or rec.get("state") not in _STATES
                or not all(isinstance(v, str) and _FP_PATTERN.fullmatch(v) for v in fps)
            ):
                raise _err("E7")
    raw.setdefault("installed_skills", {})
    raw.setdefault("auto_update", True)
    return raw  # type: ignore[no-any-return]


def get_skills_state() -> dict[str, Any]:
    """Read the skills state file (E8 if unreadable, E7 if not valid)."""
    try:
        data = _STATE_FILE.read_bytes()
    except FileNotFoundError:
        return {"installed_skills": {}, "auto_update": True}
    except OSError as exc:
        raise _err("E8", reason=_reason(exc))
    try:
        raw = json.loads(data.decode("utf-8"))
    except ValueError:
        raise _err("E7")
    return _validated(raw)


def _write_state(state: dict[str, Any]) -> None:
    """Validate, then atomically replace skills.json (a symlink stays one)."""
    _validated(copy.deepcopy(state))
    path = Path(os.path.realpath(_STATE_FILE))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".skills.json.", suffix=".tmp", dir=path.parent)
    try:
        # The ``with`` closes the handle first: Windows can't unlink an open file.
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)  # Proof: mkstemp made it in this call.
        raise


def _try_lock(lock: Path) -> int:  # Lock without waiting: fd, -1 if busy, or E28.
    fd = -1
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(lock, flags, 0o600)  # Never truncates, writes or deletes it.
        if sys.platform == "win32":  # Windows cannot delete or replace an open file.
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not os.path.samestat(os.fstat(fd), os.lstat(lock)):  # Deleted/replaced.
                raise FileNotFoundError(errno.ENOENT, "replaced")  # Lock the new one.
        fd, got = -1, fd  # Ours now: the finally must not close it.
        return got
    except OSError as exc:
        if fd < 0 or exc.errno not in _BUSY:  # Busy, or deleted or replaced.
            raise _err("E28", lock=lock, reason=_reason(exc)) from exc
        return -1
    finally:
        if fd >= 0:
            os.close(fd)  # Busy, E28 or Ctrl-C: never keep this file open.


@contextlib.contextmanager
def _state_lock() -> Iterator[None]:
    """Hold skills.json.lock against other deepctl processes and threads (B1).

    Re-entrant per thread. The OS drops it when its holder exits, even on a
    kill, so the file is never deleted and a stale lock cannot exist.
    """
    if getattr(_LOCAL, "fd", None) is not None:
        yield  # This thread already holds it.
        return
    lock, deadline = _STATE_FILE.with_name("skills.json.lock"), time.monotonic()
    while (fd := _try_lock(lock)) < 0:
        if time.monotonic() >= deadline + _LOCK_TIMEOUT:
            raise _err("E27")
        time.sleep(0.05)
    try:
        _LOCAL.fd = fd
        yield
    finally:
        _LOCAL.fd = None
        if sys.platform == "win32":
            with contextlib.suppress(OSError):
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        os.close(fd)  # Releases the POSIX lock.


@_state_lock()
def _update_state(
    mutate: Callable[[dict[str, Any]], None],
    failure: str = "E9c",
    gen: SkillGenerator | None = None,
) -> None:
    """Read skills.json, apply ``mutate`` and write it: every write goes here."""
    # Re-entrant: install_tool and remove_tool hold it for the whole operation (B1).
    state = get_skills_state()  # E7 and E8 pass through unchanged (N5).
    mutate(state)
    try:
        _write_state(state)
    except (OSError, TypeError) as exc:
        raise _err(failure, gen, reason=_reason(exc)) from exc


def _folders(state: dict[str, Any], cli: str) -> dict[str, Any]:
    folders: dict[str, Any] = (
        state.get(_RECORDS_KEY, {}).get(cli, {}).get("folders", {})
    )
    return folders


def _recorded(state: dict[str, Any], cli: str) -> tuple[Any, Any] | None:
    """The tool's whole records (folders, 0.3.x), or None when it is not listed."""
    rec = (
        state.get(_RECORDS_KEY, {}).get(cli),
        state.get("installed_skills", {}).get(cli),
    )
    return None if rec == (None, None) else rec


def _is_link(st: os.stat_result) -> bool:
    """True for a symlink, or any reparse point (a Windows junction included)."""
    attrs = getattr(st, "st_file_attributes", 0)
    return stat.S_ISLNK(st.st_mode) or bool(attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _marker_text(cli: str, name: str) -> str:
    return f"deepctl installed this folder ({cli}/{name}); 'dg skills update' replaces it and 'dg skills remove' deletes it.\n"


def _read_regular(path: str | Path, limit: int, at: int | None = None) -> bytes | None:
    """Read one regular file of at most ``limit`` bytes, never via a link, else None."""
    lst = os.stat(path, dir_fd=at, follow_symlinks=False)  # Windows: no O_NOFOLLOW.
    if _is_link(lst) or not stat.S_ISREG(lst.st_mode) or lst.st_size > limit:
        return None
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags | getattr(os, "O_NONBLOCK", 0), dir_fd=at)
    try:
        st, data = os.fstat(fd), bytearray()
        if (st.st_dev, st.st_ino) != (lst.st_dev, lst.st_ino) or st.st_size > limit:
            return None  # Swapped between the lstat and the open.
        while (chunk := os.read(fd, 65536)) and len(data) <= limit:
            data += chunk
        return bytes(data) if len(data) <= limit else None
    finally:
        os.close(fd)


def _marker_ok(path: Path, cli: str, name: str) -> bool:
    """True if ``path`` is a real directory holding the exact marker for cli/name."""
    try:
        st = os.lstat(path)
        if _is_link(st) or not stat.S_ISDIR(st.st_mode):
            return False
        data = _read_regular(path / _MARKER, _MAX_MARKER_BYTES)
    except (FileNotFoundError, NotADirectoryError):
        return False  # Missing: not ours. Any other OSError propagates.
    return data == _marker_text(cli, name).encode()


def _fingerprint(path: str | Path) -> str | None:
    """Hash each entry's name, kind and bytes; None for a link, special file or cap."""
    entries: list[tuple[bytes, bytes, str | None]] = []
    stack = [("", os.fspath(path))]
    while stack:
        rel, where = stack.pop()
        with os.scandir(where) as it:
            for e in it:
                st, sub = e.stat(follow_symlinks=False), f"{rel}{e.name}"
                if _is_link(st) or not (
                    stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)
                ):
                    return None
                if stat.S_ISDIR(st.st_mode):
                    entries.append((os.fsencode(sub), b"d", None))
                    stack.append((sub + "/", e.path))
                else:
                    entries.append((os.fsencode(sub), b"f", e.path))
    h, total = hashlib.sha256(_FP_DOMAIN), 0
    for key, kind, file in sorted(entries, key=lambda x: x[0]):
        h.update(kind + len(key).to_bytes(4, "big") + key)
        if file is not None:
            data = _read_regular(file, _MAX_TREE_BYTES - total)
            if data is None:
                return None
            total += len(data)
            h.update(hashlib.sha256(data).digest())
    return "sha256:" + h.hexdigest()


def _ownership(path: Path, cli: str, name: str, rec: dict[str, Any] | None) -> str:
    """Return "ok", "edited", "unproven" or "unreadable" for ``path``."""
    want = {rec.get("fingerprint"), rec.get("pending")} - {None} if rec else set()
    if not want:
        return "unproven"
    try:
        if not _marker_ok(path, cli, name):
            return "unproven"
        fp = _fingerprint(path)
    except OSError:
        return "unreadable"  # Never "edited", which would drop a record (SF4).
    return "ok" if fp in want else "edited"


def _rename_excl(src: str | Path, dest: str | Path, at: int | None = None) -> None:
    """Rename ``src`` to ``dest`` in one step that fails if anything is at ``dest``."""
    if _WINDOWS:
        os.rename(src, dest)  # Windows rename refuses any existing dest.
        return
    mac, libc = sys.platform == "darwin", ctypes.CDLL(None, use_errno=True)
    fn = getattr(libc, "renamex_np" if mac else "renameat2", None)
    if mac and at is not None:  # Names relative to folder fd ``at`` (macOS 10.12+).
        fn = getattr(libc, "renameatx_np", None)
    nr = _NR_RENAMEAT2[sys.maxsize < 2**32].get(platform.machine())
    if nr and sys.platform == "linux" and hasattr(libc, "syscall"):  # Every glibc.
        fn = functools.partial(libc.syscall, ctypes.c_long(nr))  # The kernel's errno.
    if fn is None:  # No call to make on this OS, machine or Python.
        raise OSError(errno.ENOSYS, _NO_EXCL_SYS, str(dest))
    a, b = os.fsencode(src), os.fsencode(dest)
    d = -100 if at is None else at  # AT_FDCWD (Linux); RENAME_EXCL 4, NOREPLACE 1.
    if (fn(a, b, 4) if mac and at is None else fn(d, a, d, b, 4 if mac else 1)) != 0:
        e = ctypes.get_errno() or errno.EIO  # Never "Success" for a failed call.
        bad = e in (errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP)  # The filesystem.
        why = _NO_EXCL_SYS if e == errno.ENOSYS else _NO_EXCL if bad else None
        raise OSError(e, why or os.strerror(e), str(dest))  # ENOSYS: kernel < 3.15.


def _place(src: Path, dest: Path) -> None:
    """Move ``src`` to ``dest``; if anything is at ``dest``, the OS refuses atomically (B2)."""
    _rename_excl(src, dest)


# "bytes:sha256" of each SKILL.md 0.2.16-0.3.2 copied (legacy_v03/allowlist.tsv).
_V03_BLOBS = {
    "api": (
        "2271:0e84ca7cdbfecde6ccbad869ac1368ffc70e500d7fd63cba317ef5c5a010bc39",
        "6892:1e3c33188e3b6548adac918916e489cecc9dd408cc63eccc7645846a9bf8b5ef",
        "7229:37b0c83a184100354b58dbd6f63fe086e11018aec0e29730a58a71cb72ba8569",
        "7810:87682eb16a5fe904bad30ee1f68c43dc1a6db31217c252e1d2b94e89c8c810ac",
        "7558:ab6dcec901dbe89994ee8b5f43649d488fca95d3ad591d910f6109b5946fea97",
        "7769:644f06c0a29a2251a556c5669d26f636074ebfb1ab57f61d7c298d34f5810553",
        "11915:db3f40de8edb8b810ec9636cf2d5ac8916a4c760555373d5b4f4a75607b9ec59",
        "12087:523e206af4c33a07175d7fd6d190b70ed7b1c7ec89cb3b6b4575669abf02e5a2",
        "19480:b2855ce6bcc9d8e6744c9b669c8ad0de624100f779a80d9139b73333bfd5e408",
        "19496:b193fe2baed574077026cf2b60f5d07985ad27899e2e8ffdab6a2657d70601a6",
        "21751:8cda50a65b00eb45b3789fd3e29bc998f7737aa0ad03bb563e782cc3924b556b",
        "25667:545d78a1b2735479237fe7703128ca033279126772eca2a6dbf1a5a878ea12f2",
        "26114:f24514384d9662214b973923117802bf5b7329be17b787be0ed5495cb668eab1",
        "29407:959031e436ae0eb50bb5139a30acca1607c3f4828061e655b6b7e3d10be1ea92",
    ),
    "docs": (
        "1683:a3d3c853b73b6e0f56cba86a6be91bcba936135d4a58fa0ecc62958de1349e12",
        "3511:e987e38d0e832c11949a21395c38cec2a0e7c275acfce26e65a0c7c36cffaa0b",
        "4875:cef47147b79e903c72b3d27a9bd8dcca3ddeb5e0f9f6bbc36da4dd6f13ad8336",
        "4925:1c0457b580de0620b8953bb6029873d614eb96e586b7e6be72c83fd63c2e51a8",
        "5249:64e23bb3edd797bc149a3fd26034170e1c8e2850d608ad15e59915bfa84f289e",
    ),
    "setup-mcp": (
        "4647:8ce952a6d4322ea883028c9a548be58fc7178bbba4148baae257aa57d3a7d68a",
        "10674:251db18b9a887660093e82a09b4c3ff02d464e02df1450484a87261d2ce836b3",
        "12301:f5000298802362356907889bfcd90cba430c528d466c3f27bddd3923274d9a54",
    ),
    "starters": (
        "8790:9ba321bc8cf444c8b493d290d61e5dda00fb21bb7a56b55bef7b92952a841b2c",
        "10027:6a5622355f3c185b2eafd3dad5b54aca01bda47d11d03dff79f6ab4228194c70",
        "11464:0074b2b8f7677624ee0a7f94d373085ea70c87e5057b601adf549094e9f61258",
        "11489:b92ba4683fc740b77858a3f7b2f9845b6ce07fee91d17ba7ec67d1417b4c70cb",
        "16472:40c3ec366c632145a619276fee54f426a124a496ebcfcee4b1fa0f34d7a25b9b",
        "16572:950c42c6c7c01f5692a5a2cdc00c6e1bbded51dab0e261e290f890f8eda035a0",
        "17376:aba86630c4872031d3c66dc100e58b3878a3c9b4cbbfad4af87a12102ba8e228",
    ),
}
_V03_BEGIN = b"<!-- BEGIN deepctl CLI Reference (auto-generated by deepctl) -->"
_V03_END = b"<!-- END deepctl CLI Reference -->"
_V03_SEP = b"\n\n---\n\n"  # 0.3.x joined the skills with this, in _V03_BLOBS order.
_V03_ASIDE = ".deepctl-v03-"  # Not _STAGING_PREFIX: README names 0.3.x leftovers.
_V03_MAX = 16 << 20
_V03_DIR = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
_V03_NOFOLLOW = _V03_DIR | getattr(os, "O_NOFOLLOW", 0)
_V03_NEW = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
_V03_SHARED = ("codex", "gemini", "opencode")  # A section in the user's own file.
_V03_CLAUDE = ".claude/commands/deepgram"
_V03_PATHS = {  # Under the home folder, from the v0.3.2 generator.
    "claude": [f"{_V03_CLAUDE}/{n}.md" for n in _V03_BLOBS],
    "cursor": [".cursor/rules/deepctl.mdc"],
    "cline": [".cline/rules/deepctl.md"],
    "codex": [".codex/instructions.md"],
    "gemini": [".gemini/GEMINI.md"],
    "opencode": [".opencode/agents.md"],
}


def _v03_join(data: bytes, names: list[str]) -> bool:
    """True if ``data`` is allowlisted blobs, one per name at most, in order, joined."""
    for i, n in enumerate(names):
        for blob in _V03_BLOBS[n]:
            size, sha = blob.split(":")
            head, rest = data[: int(size)], data[int(size) :]
            tail = rest[len(_V03_SEP) :] if rest.startswith(_V03_SEP) else None
            if hashlib.sha256(head).hexdigest() == sha and (
                not rest or (tail is not None and _v03_join(tail, names[i + 1 :]))
            ):
                return True
    return False


def _v03_gone(path: str | Path) -> bool:
    """True only when ``path`` provably does not exist; an unreadable one is there."""
    try:
        return not os.lstat(path)  # A stat result is never empty: it is there.
    except OSError as exc:
        return isinstance(exc, (FileNotFoundError, NotADirectoryError))


class _V03Link(OSError): ...  # A folder between home and a legacy file is a link.


@dataclass(frozen=True)
class _V03Done:
    """A finished cleanup and the original inode retained for late writers."""

    removed: bool
    aside: Path


class _V03Dir(contextlib.AbstractContextManager["_V03Dir"]):
    """Folder of ``rel``, reached through no link: by fd (POSIX), rechecked (Windows)."""

    def __init__(self, rel: str) -> None:
        *self.parts, self.name = rel.split("/")
        self.where = Path.home().joinpath(*self.parts)
        self.walk()

    def walk(self, err: type[OSError] = _V03Link) -> None:
        home = Path.home()  # Followed: HOME itself may be a link (/home -> /data/home).
        self.fd: int | None = None if _WINDOWS else os.open(home, _V03_DIR)
        try:
            for i, part in enumerate(self.parts):
                if _is_link(os.lstat(sub := home.joinpath(*self.parts[: i + 1]))):
                    why = f"{sub} is a link, which deepctl doesn't follow"
                    raise err(errno.ELOOP, why, str(sub))
                if self.fd is not None:  # A link swapped in since the lstat fails here.
                    up, self.fd = self.fd, os.open(part, _V03_NOFOLLOW, dir_fd=self.fd)
                    os.close(up)
        except BaseException:
            self.__exit__()
            raise

    def __call__(self, name: str) -> str:
        if self.fd is not None:
            return name  # Used with dir_fd=self.fd.
        self.walk(OSError)  # Not E40: a file may have moved already (E35, tracked).
        return str(self.where / name)

    def lstat(self, name: str) -> os.stat_result | None:
        try:
            return os.stat(self(name), dir_fd=self.fd, follow_symlinks=False)
        except FileNotFoundError:
            return None

    def back(self, aside: str) -> bool:
        """Put ``aside`` back (never over a newer file); False after E4, E37 or E41."""
        try:
            if (s := self.lstat(aside)) and not stat.S_ISREG(s.st_mode):
                return self.warn("E41", aside)  # A link or folder is there now.
            if s:  # Nothing if nothing moved.
                _rename_excl(self(aside), self(self.name), self.fd)
            return True
        except OSError as exc:  # A save at the name since (E37), else E4.
            return self.warn("E37" if exc.errno in _NO_REPLACE else "E4", aside)

    def warn(self, key: str, aside: str) -> bool:
        text = _msg(key, dest=self.where / self.name, aside=self.where / aside)
        print_warning(escape(text), stderr=True)
        return False  # For back(): the file is not back.

    def __exit__(self, *exc: object) -> None:
        if self.fd is not None:
            os.close(self.fd)


def _v03_mv(
    d: _V03Dir, data: bytes, st: os.stat_result, new: bytes | None, why: str
) -> _V03Done | str:
    """After a re-proof, delete or publish ``new`` while retaining the original inode."""
    aside, tmp, pub = _V03_ASIDE + d.name, f"{_V03_ASIDE}{uuid.uuid4().hex}.tmp", False
    kept = f".deepctl-kept-v03-{uuid.uuid4().hex}-{d.name}"
    made: tuple[int, ...] | None = None
    try:
        if new is not None:
            with os.fdopen(os.open(d(tmp), _V03_NEW, 0o600, dir_fd=d.fd), "wb") as f:
                f.write(new)
                f.flush()
                os.fsync(f.fileno())
                at = d(tmp) if _WINDOWS else f.fileno()  # POSIX: by fd, never a link.
                os.chmod(at, stat.S_IMODE(st.st_mode))  # Like copystat.
                made = os.fstat(f.fileno())[:3]  # Its type and mode, inode, device.
                if not _WINDOWS:
                    os.utime(at, ns=(st.st_atime_ns, st.st_mtime_ns))
            if _WINDOWS:  # After the close, which would reset the time there.
                os.utime(at, ns=(st.st_atime_ns, st.st_mtime_ns))
        try:  # Ctrl-C right after the move still puts it back.
            _rename_excl(d(d.name), d(aside), d.fd)  # A save from now on lands at name.
            if (bad := _read_regular(d(aside), _V03_MAX, d.fd) != data) or made != (
                (s := d.lstat(tmp)) and s[:3]  # The temp it wrote, not a link; a delete
            ):  # has no temp, so None == None there.
                why = why if bad else "deepctl's temporary copy of it was replaced"
                return why if d.back(aside) else ""  # E4, E37 or E41 said where it is.
            if new is None:
                _rename_excl(d(aside), d(kept), d.fd)
                return _V03Done(True, d.where / kept)
            pub = True
            _rename_excl(d(tmp), d(d.name), d.fd)  # Refused if a new file is there.
        except BaseException as exc:
            if pub and isinstance(exc, OSError) and exc.errno in _NO_REPLACE:
                d.warn("E37", aside)
                return ""
            left = True  # Unless it was published: the next run names it (E38).
            with contextlib.suppress(OSError):  # A link now: Ctrl-C still re-raises.
                left = not pub or isinstance(exc, OSError) or bool(d.lstat(tmp))
            vars(exc)["v03_moved"] = left and not d.back(aside)  # E35b, not E35.
            raise
        _rename_excl(d(aside), d(kept), d.fd)
        return _V03Done(False, d.where / kept)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(d(tmp), dir_fd=d.fd)  # Gone already once it was published.


def _v03_file(rel: str, names: list[str], shared: bool) -> _V03Done | str | _V03Link:
    """A completed cleanup, no-op, or reason kept; raises OSError if I/O fails."""
    try:
        with _V03Dir(rel) as d:
            return _v03_cut(d, names, shared)
    except _V03Link as exc:
        return exc
    except (FileNotFoundError, NotADirectoryError):
        return ""


def _v03_cut(d: _V03Dir, names: list[str], shared: bool) -> _V03Done | str:
    aside = _V03_ASIDE + d.name  # The same name each run, so a leftover is found.
    st, old = d.lstat(d.name), d.lstat(aside)
    if old and st:  # E38 for a file an earlier run moved; else not deepctl's (E42).
        d.warn("E38" if stat.S_ISREG(old.st_mode) else "E42", aside)
        return ""
    if old and stat.S_ISREG(old.st_mode) and not _is_link(old):  # An interrupted run's.
        try:
            _rename_excl(d(aside), d(d.name), d.fd)  # Refused if a file appeared since.
        except OSError as exc:
            vars(exc)["v03_moved"] = True  # E35b: it is still in the aside.
            raise
        d.warn("E39", aside)
        st = old
    if st is None:
        return ""
    data = _read_regular(d(d.name), _V03_MAX, d.fd)
    if data is None:
        if _is_link(st) or not stat.S_ISREG(st.st_mode):
            return "it is a link" if _is_link(st) else "it is not a file"
        big = st.st_size > _V03_MAX  # Else it changed between the lstat and the read.
        return "it is larger than 16 MiB" if big else "it changed while deepctl read it"
    eol = b"\r\n" if b"\r\n" in data else b"\n"
    mixed = b"\n" in data.replace(eol, b"")  # CRLF and LF: 0.3.x never did.
    if not shared:
        if mixed or not _v03_join(data.replace(b"\r\n", b"\n"), names):
            return "it differs from every deepgram/skills version deepctl 0.3.x copied"
        return _v03_mv(d, data, st, None, "it changed while deepctl was removing it")
    if _V03_BEGIN not in data and _V03_END not in data:
        return ""
    i, j = data.find(_V03_BEGIN), data.find(_V03_END) + len(_V03_END)
    if mixed:
        return "it mixes line endings"
    if not (
        data.count(_V03_BEGIN) == data.count(_V03_END) == 1
        and i < j
        and (i == 0 or data[i - 1 : i] == b"\n")
        and data[i + len(_V03_BEGIN) :].startswith(eol)
        and data[: j - len(_V03_END)].endswith(eol)
        and (j == len(data) or data[j:].startswith(eol))
    ):
        return "its deepctl section is incomplete, repeated or not on lines of its own"
    if st.st_nlink > 1 or (hasattr(os, "getuid") and st.st_uid != os.getuid()):
        return "it has other hard links or another user owns it"
    ro = not st.st_mode & 0o222 or not os.access(d(d.name), os.W_OK, dir_fd=d.fd)
    if ro or getattr(st, "st_flags", 0) & (stat.UF_IMMUTABLE | stat.SF_IMMUTABLE):
        return "it is read-only or locked"
    head, tail = data[:i], data[j + len(eol) :]
    if head.endswith(eol * 2) and not head[: -2 * len(eol)].endswith(b"\n"):
        head = head[: -len(eol)]  # The one blank line 0.3.x added before its section.
    new = head + tail or None  # None: 0.3.x created the file for its section.
    return _v03_mv(d, data, st, new, "it changed while deepctl was editing it")


def _clean_v03(gen: SkillGenerator, root: Path) -> None:
    """Remove the 0.3.x content of ``gen`` deepctl can prove; only Ctrl-C raises."""
    cli, notes, untrack = gen.cli_name, list[str](), set[Path]()
    removed, cut, kept, retry = (
        list[Path](),
        list[Path](),
        list[tuple[Path, Path]](),
        set[Path](),
    )
    try:
        state = get_skills_state()
        tool = state.get(_RECORDS_KEY, {}).get(cli, {})
        folders = tool.get("folders", {})
        landed = {n for n, r in folders.items() if r.get("state") == "installed"}
        rec = state["installed_skills"].get(cli)
        recorded = {Path(p) for p in rec["paths"]} if rec else set()
        if _WINDOWS:
            legacy = [p for p in recorded if p.parent != root]
            if rec and legacy:
                paths = [p for p in rec["paths"] if Path(p).parent == root] or [
                    str(root / n) for n in sorted(landed)
                ]

                def clear_windows(state: dict[str, Any]) -> None:
                    state["installed_skills"][cli]["paths"] = paths
                    state.get(_RECORDS_KEY, {}).get(cli, {}).pop("v03", None)

                _update_state(clear_windows, "E9c", gen)
                print_warning(
                    escape(_msg("E44", paths=", ".join(map(str, legacy)))), stderr=True
                )
            return
        for rel in _V03_PATHS.get(cli, []):
            path = Path.home().joinpath(*rel.split("/"))
            names = [path.stem] if cli == "claude" else list(_V03_BLOBS)
            if not set(names) <= landed:
                continue  # The folders that replace it did not all land: keep it.
            try:
                why = _v03_file(rel, names, cli in _V03_SHARED)
            except OSError as exc:  # Warned only if 0.3.x recorded it (E35 says so).
                if path in recorded:  # E35b after E4, E37, E41 or a failed E39.
                    key = "E35b" if getattr(exc, "v03_moved", False) else "E35"
                    notes.append(_msg(key, path=path, reason=_reason(exc)))
                retry.add(path)  # Tracked, even if a link now hides it from the prune.
                continue
            if isinstance(why, _V03Done):
                (removed if why.removed else cut).append(path)
                kept.append((path, why.aside))
            elif why and path in recorded:  # Once, then untracked (as E23/E26).
                if _output_config["quiet"]:
                    continue  # Unseen: stay tracked so a later run warns.
                key = "E34" if cli in _V03_SHARED else "E33"
                key = "E40" if isinstance(why, _V03Link) else key
                what = _V03_LINES if cli in _V03_SHARED else "that content yourself"
                notes.append(_msg(key, path=path, why=why, what=what))
            untrack.add(path)
        if removed and cli == "claude":  # Only when empty: the user's files stay.
            with contextlib.suppress(OSError), _V03Dir(_V03_CLAUDE) as d:
                if (s := d.lstat(d.name)) and not _is_link(s):  # Windows junction.
                    os.rmdir(d(d.name), dir_fd=d.fd)
        if rec:
            paths = [
                p
                for p in rec["paths"]
                if Path(p) not in untrack
                and (Path(p).parent == root or Path(p) in retry or not _v03_gone(p))
            ] or [str(root / n) for n in sorted(landed)]
            v03 = any(Path(p).parent != root for p in paths)

            def clear(state: dict[str, Any]) -> None:
                state["installed_skills"][cli]["paths"] = paths
                if not v03:
                    state[_RECORDS_KEY][cli].pop("v03", None)

            if paths != rec["paths"] or (not v03 and "v03" in tool):
                _update_state(clear, "E9c", gen)
    except Exception as exc:  # Never fail an install that landed.
        why = str(exc) if isinstance(exc, SkillInstallError) else _reason(exc)
        notes.append(_msg("E36", gen, reason=why.rstrip(".")))
    if removed:
        done = f"Removed deepctl 0.3.x files for {gen.display_name}: {', '.join(map(str, removed))}."
        print_info(escape(done), stderr=True)
    for p in cut:
        text = f"Removed the deepctl 0.3.x section from {p}; the rest of the file is unchanged."
        print_info(escape(text), stderr=True)
    for path, aside in kept:
        print_info(escape(_msg("E43", dest=path, aside=aside)), stderr=True)
    for note in notes:
        print_warning(escape(note), stderr=True)


@dataclass(frozen=True)
class SkillGenerator:
    """One AI coding tool and the skills root deepctl installs into."""

    cli_name: str
    display_name: str
    root_parts: tuple[str, ...] | None  # under Path.home(); None = hint-only
    homes: tuple[tuple[str, ...], ...]  # dirs whose presence means "detected"
    binary: str | None

    def skills_root(self) -> Path | None:
        return Path.home().joinpath(*self.root_parts) if self.root_parts else None

    def detect(self) -> bool:
        found = any(Path.home().joinpath(*h).is_dir() for h in self.homes)
        return found or bool(self.binary and shutil.which(self.binary))


_GENERATORS = [
    SkillGenerator(*row)
    for row in (
        ("claude", "Claude Code", (".claude", "skills"), ((".claude",),), "claude"),
        ("codex", "OpenAI Codex", (".agents", "skills"), ((".codex",),), "codex"),
        ("gemini", "Gemini CLI", (".gemini", "skills"), ((".gemini",),), "gemini"),
        ("amazonq", "Amazon Q Developer", None, ((".amazonq",),), None),
        ("aider", "Aider", None, (), "aider"),
        (
            "opencode",
            "OpenCode",
            (".config", "opencode", "skills"),
            ((".opencode",), (".config", "opencode")),
            "opencode",
        ),
        ("cursor", "Cursor", (".cursor", "skills"), ((".cursor",),), "cursor"),
        ("cline", "Cline", (".cline", "skills"), ((".cline",),), None),
    )
]


def get_all_generators() -> list[SkillGenerator]:
    """Return instances of all registered generators."""
    return list(_GENERATORS)


def detect_ai_clis() -> list[SkillGenerator]:
    """Return generators for detected AI CLIs."""
    return [g for g in get_all_generators() if g.detect()]


def _ref_for(cli: str, state: dict[str, Any], explicit: str | None = None) -> str:
    """``explicit``, then the env var, then the recorded ref, then the pin."""
    if explicit or os.environ.get(skill_bundle.REF_ENV_VAR, "").strip():
        return skill_bundle.resolve_skills_ref(explicit)
    recorded = state.get(_RECORDS_KEY, {}).get(cli, {}).get("skills_ref")
    if recorded:
        return skill_bundle.validate_ref(recorded)
    return skill_bundle.DEFAULT_SKILLS_COMMIT


def _conflicts(
    gen: SkillGenerator, skills: Sequence[RepoSkill], recorded: dict[str, Any]
) -> tuple[list[Path], list[Path]]:
    """Return the (unproven, edited) destinations; moves nothing, writes no state."""
    root = gen.skills_root()
    assert root is not None
    if os.path.lexists(root) and not os.path.isdir(root):
        raise _err("E18", gen)
    found: dict[str, list[Path]] = {"unproven": [], "edited": [], "ok": []}
    for s in skills:
        dest, rec = root / s.name, recorded.get(s.name)
        if os.path.lexists(dest):
            kind = _ownership(dest, gen.cli_name, s.name, rec)
            if kind == "unreadable":
                raise _err("E5", gen, reason=f"could not read {dest}")
            found[kind].append(dest)
    return found["unproven"], found["edited"]


def install_conflicts(
    generators: Sequence[SkillGenerator], skills: Sequence[RepoSkill]
) -> tuple[list[Path], list[Path]]:
    """Return (unproven, edited) across the folder tools; writes no state."""
    state, unproven, edited = get_skills_state(), list[Path](), list[Path]()
    for gen in generators:
        if gen.skills_root() is not None:
            u, e = _conflicts(gen, skills, _folders(state, gen.cli_name))
            unproven, edited = unproven + u, edited + e
    return unproven, edited


def _refusal(gen: SkillGenerator, dest: Path, kind: str, rec: Any) -> SkillInstallError:
    if kind == "unreadable":
        return _err("E5", gen, reason=f"could not read {dest}")
    if kind == "edited":
        return SkillOwnershipError([], [dest])
    msg = _msg("E3" if rec else "E2", gen, dest=dest)
    return SkillOwnershipError([dest], message=msg)


def _stage(
    gen: SkillGenerator, skills: Sequence[RepoSkill], staging: Path
) -> dict[str, str]:
    """Copy each skill into staging/new, add its marker, and fingerprint the copy."""
    fps: dict[str, str] = {}
    os.mkdir(staging / "new")
    _place(staging / "new", staging / "old")  # Fails closed before anything moves (B2).
    os.mkdir(staging / "new")
    for s in skills:
        copy_ = staging / "new" / s.name
        shutil.copytree(s.path, copy_)
        try:
            with open(copy_ / _MARKER, "x", encoding="utf-8", newline="\n") as f:
                f.write(_marker_text(gen.cli_name, s.name))
            fp = _fingerprint(copy_)
        except FileExistsError:
            fp = None
        if fp is None:
            raise _err("E10", name=s.name)
        fps[s.name] = fp
    return fps


def _swap(gen: SkillGenerator, name: str, staging: Path, rec: Any) -> None:
    """Move a proven old folder aside, then place the staged copy."""
    cli, dest, aside = gen.cli_name, staging.parent / name, staging / "old" / name
    moved, refusal = False, None
    if os.path.lexists(dest):
        if (kind := _ownership(dest, cli, name, rec)) != "ok":  # The pre-check.
            raise _refusal(gen, dest, kind, rec)
        try:
            os.rename(dest, aside)
            moved = True
        except FileNotFoundError:
            pass  # Vanished: install as absent.
        except OSError as exc:
            raise _err("E5", gen, reason=_reason(exc))
    try:  # Starts right after the move, so the copy always goes back (SF1).
        if moved and (kind := _ownership(aside, cli, name, rec)) != "ok":
            raise (refusal := _refusal(gen, dest, kind, rec))  # The real proof.
        _place(staging / "new" / name, dest)
    except BaseException as exc:
        if os.path.lexists(aside):
            try:
                _place(aside, dest)  # Put back whatever was moved; never overwrites.
            except OSError:
                if not isinstance(exc, Exception):
                    raise exc  # Ctrl-C: cleanup's invariant decides the copy.
                key = "E4" if exc is refusal else "E6"
                err = _err(key, gen, dest=dest, aside=aside, name=name)
                err.kept = aside  # Cleanup may yet put an E6 copy back.
                raise err from exc
        if getattr(exc, "errno", None) in _NO_REPLACE:  # Someone else's dest.
            raise _refusal(gen, dest, "unproven", None) from exc
        raise


def _cleanup(staging: Path, cli: str, before: dict[str, Any]) -> Path | None:
    """Remove this run's staging; an old copy goes back only if its new one never left."""
    for a in _scan(staging / "old"):  # Before staging/new, which is the proof.
        if _ownership(a, cli, a.name, before.get(a.name)) != "ok":
            continue  # Not exactly the old folder: keep it for the user.
        dest = staging.parent / a.name
        with contextlib.suppress(OSError):
            if os.path.lexists(staging / "new" / a.name):
                _place(a, dest)  # Stranded by a Ctrl-C.
            elif not os.path.lexists(dest) or _marker_ok(dest, cli, a.name):
                shutil.rmtree(a, ignore_errors=True)  # Replaced (B1): never back.
    for e in _scan(staging / "new"):
        shutil.rmtree(e, ignore_errors=True)  # Copied by this call.
    for d in (staging / "old", staging / "new", staging):
        with contextlib.suppress(OSError):
            os.rmdir(d)  # Removes only an empty dir.
    return staging if os.path.lexists(staging) else None


def _real_dir(path: Path) -> bool:
    """True for a real directory, never a link to one."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return not _is_link(st) and stat.S_ISDIR(st.st_mode)


def _old_copy(staging: Path, cli: str, name: str) -> Path | None:
    """A marked old copy of cli/name in a real staging folder; anything else is ignored."""
    old = staging / "old" / name
    try:
        ok = _real_dir(staging) and _real_dir(old.parent) and _marker_ok(old, cli, name)
    except OSError:
        return None
    return old if ok else None


def _scan(path: Path) -> list[Path]:
    try:
        return [Path(e.path) for e in os.scandir(path)]
    except OSError:
        return []


@_state_lock()
def install_tool(
    gen: SkillGenerator,
    skills: Sequence[RepoSkill],
    *,
    ref: str,
    version: str,
    since: dict[str, Any] | None = None,
    explicit_ref: bool = False,
) -> tuple[list[Path], Path | None]:
    """Install ``skills`` for ``gen``; return (placed folders, leftover staging).

    A refresh passes ``since``, the skills.json it planned from: if the tool's
    record changed at all since then, SkillSkipped is raised before anything
    is written: E31 if removed; else, unless ``explicit_ref``, E30 if it now
    names another ref, E32 if not (a moving ref's copy may be newer).
    """
    root, cli = gen.skills_root(), gen.cli_name
    if root is None:
        return [], None
    for s in skills:
        if not portable_name(s.name):
            raise _err("E11", name=s.name)
    state = get_skills_state()
    if since is not None and (now := _recorded(state, cli)) != _recorded(since, cli):
        if now is None:
            raise SkillSkipped(_msg("E31", gen))
        if not explicit_ref:
            other = (now[0] or {}).get("skills_ref") not in (None, "", ref)
            raise SkillSkipped(_msg("E30" if other else "E32", gen))
    before = {n: dict(r) for n, r in _folders(state, cli).items()}
    unproven, edited = _conflicts(gen, skills, before)
    if unproven or edited:
        raise SkillOwnershipError(unproven, edited)
    fps: dict[str, str] = {}
    placed: list[str] = []
    error: BaseException | None = None
    run = uuid.uuid4().hex  # Tags this run's "installing" records (S1).

    def mark(state: dict[str, Any]) -> None:
        tool = state.setdefault(_RECORDS_KEY, {}).setdefault(cli, {})
        folders = tool.setdefault("folders", {})
        for n, fp in fps.items():  # Keeps the old fingerprint beside ``pending``.
            folders[n] = {**folders.get(n, {}), "state": "installing", "pending": fp}
            folders[n]["run"] = run  # Retags a crashed run's record too.

    def settle(state: dict[str, Any]) -> None:  # From proof on disk, not ``placed``.
        tool = state.setdefault(_RECORDS_KEY, {}).setdefault(cli, {})
        folders = tool.setdefault("folders", {})
        for s in skills:
            rec, dest = folders.get(s.name), root / s.name
            if all(os.path.lexists(staging / d / s.name) for d in ("old", "new")):
                continue  # Cleanup may yet put the old copy back: keep its record.
            # This run's staged copy is proof too: another run may drop the record (S1).
            recs = (rec or {}, before.get(s.name, {}), {"pending": fps.get(s.name)})
            want = {r.get(k) for r in recs for k in ("fingerprint", "pending")} - {None}
            try:
                ok = _marker_ok(dest, cli, s.name)
                fp = _fingerprint(dest) if ok else None
            except OSError:
                continue  # Unreadable keeps its record exactly as it is (SF4).
            if fp in want:
                folders[s.name] = {"state": "installed", "fingerprint": fp}
            elif rec and rec.get("run", run) != run:
                continue  # Another run settles its own.
            else:
                folders.pop(s.name, None)
        legacy = state["installed_skills"].get(cli, {}).get("paths", [])
        if any(Path(p).parent != root for p in legacy):  # 0.3.x files remain.
            tool["v03"] = True
        now = datetime.now(timezone.utc).isoformat()
        if placed:
            tool.update(skills_ref=ref, version=version, installed_at=now)
        if not folders:
            del state[_RECORDS_KEY][cli]
        elif cli not in state["installed_skills"]:  # Login and startup key on it.
            paths = [str(root / n) for n in folders]
            mirror = {"paths": paths, "installed_at": now, "version": version}
            state["installed_skills"][cli] = mirror

    try:
        root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=_STAGING_PREFIX, dir=root))
    except OSError as exc:
        raise _err("E5", gen, reason=_reason(exc))
    try:  # Right after mkdtemp, so cleanup runs after any failure or Ctrl-C (SF1).
        fps.update(_stage(gen, skills, staging))
        _update_state(mark, "E9", gen)  # Record before swap (B6): nothing moved.
        for s in skills:
            try:
                _swap(gen, s.name, staging, before.get(s.name))
            except BaseException as exc:
                error = exc
                break
            placed.append(s.name)
        _update_state(settle, "E9b", gen)
    except BaseException as exc:
        error = error or exc
    finally:
        leftover = _cleanup(staging, cli, before)
    if isinstance(error, OSError):
        error = _err("E5", gen, reason=_reason(error))
    n = a.name if (a := getattr(error, "kept", None)) and not os.path.lexists(a) else ""
    if n and _ownership(root / n, cli, n, before.get(n)) == "ok":
        error = _err("E6b", gen, name=n)  # E6, but cleanup put the old copy back.
    if isinstance(error, SkillInstallError):
        error.leftover = leftover
    if error is not None:
        raise error
    _clean_v03(gen, root)
    return [root / n for n in placed], leftover


def _deepctl_version() -> str:
    try:
        return metadata.version("deepctl")
    except metadata.PackageNotFoundError:
        return "0.0.0"


def install_for(
    plan: Sequence[tuple[SkillGenerator, str]],
    *,
    since: dict[str, Any] | None = None,
    explicit_ref: bool = False,
) -> Iterator[tuple[SkillGenerator, list[Path], Path | None]]:
    """The one install path for 'dg skills', login and plugin (B3).

    Fetches each ref once and preflights every folder tool before anything is
    written, then installs tool by tool, yielding (tool, placed, leftover).
    Hint-only tools are skipped. A refresh passes ``since`` (see install_tool):
    a tool whose record changed is skipped with a warning on stderr. The first
    failure is raised; every tool yielded before it stays recorded.
    """
    plan = [(g, r) for g, r in plan if g.skills_root() is not None]
    bundles = {
        r: skill_bundle.fetch_skill_bundle(r) for r in dict.fromkeys(r for _, r in plan)
    }
    unproven: list[Path] = []
    edited: list[Path] = []
    for ref, skills in bundles.items():
        u, e = install_conflicts([g for g, r in plan if r == ref], skills)
        unproven += u
        edited += e
    if unproven or edited:  # Nothing is written for any tool.
        raise SkillOwnershipError(unproven, edited)
    version = _deepctl_version()
    for gen, ref in plan:
        try:
            placed, leftover = install_tool(
                gen,
                bundles[ref],
                ref=ref,
                version=version,
                since=since,
                explicit_ref=explicit_ref,
            )
        except SkillSkipped as exc:
            print_warning(escape(str(exc)), stderr=True)
            continue
        yield gen, placed, leftover


def warn_install_failure(prefix: str, exc: Exception, retry: str) -> None:
    """A plain warning on stderr, after an E12 line when staging was left.

    For best-effort callers (login, plugin): rerunning them does not retry the
    skills step, so ``retry`` names the command that does.
    """
    leftover = getattr(exc, "leftover", None)
    if leftover:
        print_warning(escape(_msg("E12", staging=leftover)), stderr=True)
    text = str(exc) or type(exc).__name__
    text = text.replace("and changed nothing;", "and stopped;")  # E27
    again = ", then run the command again."
    if again in text:  # Every sentence: an ownership error joins one per folder.
        text = text.replace(again, f", then {retry}.")
    else:
        text = f"{text.rstrip('.')}; {retry}."
    head = "" if " are installed, but " in text else f"{prefix}: "  # E9b
    print_warning(escape(f"{head}{text}"), stderr=True)


@dataclass
class RemoveResult:
    """What ``remove_tool`` did with each recorded folder."""

    removed: list[Path] = field(default_factory=list)
    kept: list[tuple[Path, str]] = field(default_factory=list)
    left_alone: list[Path] = field(default_factory=list)
    edited: list[Path] = field(default_factory=list)
    moved: list[tuple[Path, Path]] = field(default_factory=list)
    stranded: list[tuple[Path, Path]] = field(default_factory=list)  # E29
    leftover: Path | None = None

    def refused(self, dest: Path, kind: str) -> None:
        if kind == "unreadable":
            self.kept.append((dest, "deepctl could not read it"))
        else:
            (self.edited if kind == "edited" else self.left_alone).append(dest)


@_state_lock()
def remove_tool(gen: SkillGenerator) -> RemoveResult:
    """Delete ``gen``'s recorded folders that prove ours; leave everything else."""
    cli, root, res, staging = gen.cli_name, gen.skills_root(), RemoveResult(), None
    folders = dict(_folders(get_skills_state(), cli))

    def settle(state: dict[str, Any]) -> None:  # Keeps only what proves on disk.
        fs, res.stranded = _folders(state, cli), []
        dirs = (
            [d for d in _scan(root) if d.name.startswith(_STAGING_PREFIX)]
            if root
            else []
        )
        for n in list(fs):
            kind = _ownership(root / n, cli, n, fs[n]) if root else "unproven"
            # An old copy in staging, even a killed run's, keeps its record (S1).
            olds = [a for d in dirs if (a := _old_copy(d, cli, n))]
            stuck = [a for a in olds if a.parents[2] / n not in res.removed]
            if kind not in ("ok", "unreadable") and not stuck:
                del fs[n]  # Gone, edited or unproven: no longer deepctl's.
                continue  # Removed or replaced here: an old copy is only a leftover.
            res.stranded += [
                (a.parents[2] / n, a) for a in stuck if a.parents[1] != staging
            ]
        if not fs:
            state.get(_RECORDS_KEY, {}).pop(cli, None)
            state["installed_skills"].pop(cli, None)

    if root is None or not folders:
        _update_state(settle)
        return res
    if os.path.lexists(root) and not os.path.isdir(root):
        raise _err("E18", gen)  # The records stay: the folders may come back.
    if any(os.path.lexists(root / n) for n in folders):
        try:
            staging = Path(tempfile.mkdtemp(prefix=_STAGING_PREFIX, dir=root))
            os.mkdir(staging / "new")
            _place(staging / "new", staging / "old")  # Fails closed first (B2).
        except BaseException as exc:  # Ctrl-C too: never leave this run's empty dirs.
            if staging:
                _cleanup(staging, cli, {})
            if not isinstance(exc, OSError):
                raise
            raise _err("E21", root=root, reason=_reason(exc))
    try:
        for name, rec in folders.items():
            dest = root / name
            if staging is None or not os.path.lexists(dest):
                continue  # Dropped at settle.
            aside = staging / "old" / name
            if (kind := _ownership(dest, cli, name, rec)) != "ok":  # The pre-check.
                res.refused(dest, kind)
                continue
            try:  # Covers the move too, so a Ctrl-C right after it puts it back.
                os.rename(dest, aside)
                kind = _ownership(aside, cli, name, rec)  # The real proof (SF1).
            except FileNotFoundError:
                continue
            except OSError as exc:  # Nothing moved: _ownership never raises it.
                res.kept.append((dest, _reason(exc)))  # Stays recorded (B2).
                continue
            except BaseException:
                with contextlib.suppress(OSError):
                    _place(aside, dest)
                raise
            if kind == "ok":
                shutil.rmtree(aside, ignore_errors=True)
                res.removed.append(dest)
                continue
            try:
                _place(aside, dest)
                res.refused(dest, kind)
            except OSError:
                res.moved.append((dest, aside))
            except BaseException:
                with contextlib.suppress(OSError):
                    _place(aside, dest)
                raise
    finally:
        _update_state(settle)
        if staging is not None:
            for d in (staging / "old", staging):
                with contextlib.suppress(OSError):
                    os.rmdir(d)
            res.leftover = staging if os.path.lexists(staging) else None
    return res


@dataclass(frozen=True)
class ToolStatus:
    """Read-only view of one tool's recorded folders and staging leftovers."""

    root: Path | None
    kinds: dict[str, list[Path]]  # "ok"/"unproven"/"edited"/"unreadable"
    leftovers: list[Path]
    skills_ref: str | None


def tool_status(gen: SkillGenerator, state: dict[str, Any]) -> ToolStatus:
    """Classify ``gen``'s recorded folders on disk; changes nothing."""
    root, cli = gen.skills_root(), gen.cli_name
    kinds = {k: list[Path]() for k in ("ok", "unproven", "edited", "unreadable")}
    leftovers: list[Path] = []
    if root is not None:
        for n, rec in _folders(state, cli).items():
            if os.path.lexists(root / n):
                kinds[_ownership(root / n, cli, n, rec)].append(root / n)
        leftovers = sorted(root.glob(_STAGING_PREFIX + "*")) if root.is_dir() else []
    ref = state.get(_RECORDS_KEY, {}).get(cli, {}).get("skills_ref")
    return ToolStatus(root, kinds, leftovers, ref)
