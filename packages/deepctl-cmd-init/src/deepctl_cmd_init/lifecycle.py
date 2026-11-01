"""Clone template repos and run lifecycle steps from deepgram.toml."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from deepctl_core.output import print_hint, print_info, print_warning
from rich.console import Console

if TYPE_CHECKING:
    from pathlib import Path

    from .models import LifecycleStep

console = Console()

# ── Prerequisite tools ────────────────────────────────────────────────────────

#: Each entry: tool key → (display name, install hint)
PREREQS: dict[str, tuple[str, str]] = {
    "git": (
        "git",
        "https://git-scm.com/downloads",
    ),
    "node": (
        "Node.js",
        "https://nodejs.org",
    ),
    "npm": (
        "npm",
        "bundled with Node.js — reinstall from https://nodejs.org",
    ),
    "make": (
        "make",
        "macOS: xcode-select --install  |  Linux: sudo apt-get install make",
    ),
    "curl": (
        "curl",
        "macOS: brew install curl  |  Linux: sudo apt-get install curl",
    ),
}


def check_prereqs() -> list[tuple[str, str, str]]:
    """Check for required tools.

    Returns:
        List of ``(key, display_name, install_hint)`` tuples for every missing tool.
        Empty list means all tools are present.
    """
    missing: list[tuple[str, str, str]] = []
    for key, (name, hint) in PREREQS.items():
        if shutil.which(key) is None:
            missing.append((key, name, hint))
    return missing


# ── Running git ───────────────────────────────────────────────────────────────

# Settings every git command dg runs gets on its command line, so that
# configuration inside a repository can't run a program: no fsmonitor
# daemon command, no hooks, no pager, no external diff. (The commands dg
# runs in a repository, rev-parse, ls-files and check-ignore, apply no
# filters and open no connection.)
_NULL_DEVICE = "NUL" if os.name == "nt" else "/dev/null"
GIT_SAFE_CONFIG = (
    "-c",
    "core.fsmonitor=false",
    "-c",
    f"core.hooksPath={_NULL_DEVICE}",
    "-c",
    "core.pager=cat",
    "-c",
    "diff.external=",
)


def run_git(
    *args: str, repo: Path | None = None, inherit_git_env: bool = False
) -> subprocess.CompletedProcess[str] | None:
    """Run ``git [-C repo] args`` with GIT_SAFE_CONFIG; None if git can't run.

    Inherited GIT_* variables are dropped unless ``inherit_git_env`` (for a
    clone, which works on no existing repository): GIT_DIR, GIT_WORK_TREE
    or GIT_INDEX_FILE would point git at another repository. Call it with
    ``repo`` only for a directory dg cloned itself.
    """
    if shutil.which("git") is None:
        return None
    env = None
    if not inherit_git_env:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    argv = ["git", "--no-pager", *GIT_SAFE_CONFIG]
    if repo is not None:
        argv += ["-C", str(repo)]
    try:
        return subprocess.run([*argv, *args], capture_output=True, text=True, env=env)
    except OSError:
        return None


# ── Git clone ─────────────────────────────────────────────────────────────────

#: The GitHub organizations dg clones templates from.
TEMPLATE_ORGS = ("deepgram", "deepgram-starters")

# A repository or template name: ASCII letters, digits, ".", "_" and "-",
# not starting with "-" (git or a shell would read it as an option).
_NAME = re.compile(r"[A-Za-z0-9._-]+")


def valid_name(name: object) -> bool:
    """Whether ``name`` is a usable template or repository name."""
    return (
        isinstance(name, str)
        and _NAME.fullmatch(name) is not None
        and not name.startswith("-")
        and name not in (".", "..")
    )


def is_template_repo(url: str) -> bool:
    """Whether ``url`` is an https GitHub repository of a Deepgram organization.

    The template service supplies the URL, so only
    ``https://github.com/deepgram/<repo>`` and
    ``https://github.com/deepgram-starters/<repo>`` are cloned: no other
    host, scheme, port, user name, query or fragment, and the repository
    name is plain (``valid_name``): no escapes, separators or option-like
    leading ``-``.
    """
    if not url.isprintable() or any(c.isspace() for c in url):
        return False  # urlsplit drops tabs and newlines; git wouldn't
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or parts.netloc != "github.com" or port is not None:
        return False
    if parts.query or parts.fragment or "\\" in url:
        return False
    segments = parts.path.split("/")
    # "", org, repo (and an optional trailing "")
    if len(segments) == 4 and segments[3] == "":
        segments = segments[:3]
    if len(segments) != 3 or segments[0] != "":
        return False
    org, repo = segments[1], segments[2]
    return org in TEMPLATE_ORGS and valid_name(repo)


def directory_in_use(target_dir: Path) -> bool:
    """Whether ``target_dir`` exists as anything but an empty directory."""
    if target_dir.is_symlink() or (target_dir.exists() and not target_dir.is_dir()):
        return True
    if not target_dir.exists():
        return False
    return any(target_dir.iterdir())


def clone_template(github_url: str, target_dir: Path) -> None:
    """Clone a template repository, keeping the .git directory intact.

    The cloned directory is a fully functional git repository so users can
    commit changes, add their own remote, and push to their own repo.

    Args:
        github_url: Full GitHub URL to clone.
        target_dir: Local directory to clone into (must not exist, or be
            an empty directory).

    Raises:
        RuntimeError: If the directory is in use or git clone fails.
    """
    if directory_in_use(target_dir):
        raise RuntimeError(f"Directory already exists: {target_dir}")

    # "--" ends git's options: a server-supplied URL that starts with "-"
    # (say --upload-pack=...) is taken as the repository, never an option.
    result = run_git("clone", "--", github_url, str(target_dir), inherit_git_env=True)
    if result is None:
        raise RuntimeError("git clone failed: git could not be run")
    if result.returncode != 0:
        raise RuntimeError(f"git clone failed:\n{result.stderr.strip()}")


# ── The clone dg init made ────────────────────────────────────────────────────

_COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def _git_out(target_dir: Path, *args: str) -> str | None:
    """Output of a git command in ``target_dir``, or None if it failed."""
    result = run_git(*args, repo=target_dir)
    if result is None or result.returncode != 0:
        return None
    return result.stdout.strip() or None


def head_commit(target_dir: Path) -> str | None:
    """``HEAD`` of the repository dg just cloned into ``target_dir``."""
    commit = _git_out(target_dir, "rev-parse", "--verify", "HEAD")
    return commit if commit and _COMMIT.fullmatch(commit) else None


_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


def same_directory(target_dir: Path, dev: int, ino: int) -> bool:
    """Whether ``target_dir`` is still the directory dg cloned into.

    Checked before any git command runs in it. The directory, opened
    without following a symbolic link, must have the recorded device and
    inode (a directory deleted and made again at the same path has another
    inode), and it and its ``.git`` must be owned by this user, with
    ``.git`` a real directory.

    On Windows, where ``st_ino`` can be 0 or unstable and there are no
    POSIX owners, only the path checks apply: no link, ``.git`` a real
    directory.
    """
    if os.name == "nt":
        try:
            top = os.lstat(target_dir)
            git = os.lstat(target_dir / ".git")
        except OSError:
            return False
        return stat.S_ISDIR(top.st_mode) and stat.S_ISDIR(git.st_mode)
    try:
        fd = os.open(target_dir, os.O_RDONLY | _O_DIRECTORY | _NOFOLLOW)
    except OSError:
        return False
    try:
        st = os.fstat(fd)
        git = os.stat(".git", dir_fd=fd, follow_symlinks=False)
    except OSError:
        return False
    finally:
        os.close(fd)
    uid = os.geteuid()
    return (
        stat.S_ISDIR(st.st_mode)
        and stat.S_ISDIR(git.st_mode)
        and (st.st_dev, st.st_ino) == (dev, ino)
        and st.st_uid == uid
        and git.st_uid == uid
    )


def directory_identity(target_dir: Path) -> tuple[int, int] | None:
    """Device and inode of ``target_dir`` (not following a link), or None."""
    try:
        st = os.lstat(target_dir)
    except OSError:
        return None
    return (st.st_dev, st.st_ino) if stat.S_ISDIR(st.st_mode) else None


def clone_matches(target_dir: Path, commit: str) -> bool:
    """Whether ``target_dir`` is still at ``commit``, the clone dg recorded.

    Only for a directory dg's own clone record names: git runs in it. Its
    own ``.git`` directory (not a link, or a file pointing elsewhere) must
    make it the top of a work tree, with ``HEAD`` at ``commit``.
    """
    git_dir = target_dir / ".git"
    if git_dir.is_symlink() or not git_dir.is_dir():
        return False
    top = _git_out(target_dir, "rev-parse", "--show-toplevel")
    if top is None or os.path.realpath(top) != os.path.realpath(target_dir):
        return False
    return head_commit(target_dir) == commit


# ── .env injection ────────────────────────────────────────────────────────────


#: Lines in a .gitignore that already keep ``.env`` out of git. Used only
#: when git itself isn't available to ask.
_GITIGNORE_ENV_PATTERNS = {".env", "/.env", ".env*", "/.env*", "*.env"}

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _write_private(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` readable by the owner only (0600).

    The file is created 0600 (no window where the umask applies), and an
    existing file is narrowed to 0600 before its new content is written.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _NOFOLLOW, 0o600)
    try:
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        os.write(fd, text.encode())
    finally:
        os.close(fd)


def _restrict_to_owner(path: Path) -> None:
    """Narrow an existing file that holds a key to owner-only (POSIX)."""
    if os.name != "posix":
        return
    fd = os.open(path, os.O_RDONLY | _NOFOLLOW)
    try:
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)


def _git(target_dir: Path, *args: str) -> int | None:
    """Exit code of a git command in ``target_dir``, or None if git can't run."""
    result = run_git(*args, repo=target_dir)
    return None if result is None else result.returncode


def _env_ignored(target_dir: Path) -> bool | None:
    """git's answer to "is .env ignored?", or None when git can't say."""
    # No global excludes file: one that is a FIFO would block the check.
    # The repository's own .gitignore is what keeps .env out of a commit.
    rc = _git(target_dir, "-c", "core.excludesFile=", "check-ignore", "-q", ".env")
    if rc in (0, 1):
        return rc == 0
    return None


def _append_env_line(gitignore: Path) -> None:
    """Append ``.env`` as the last line: the last matching pattern wins,
    so it also overrides an earlier ``!.env``."""
    content = gitignore.read_bytes() if gitignore.exists() else b""
    newline = b"\r\n" if b"\r\n" in content else b"\n"
    sep = newline if content and not content.endswith(b"\n") else b""
    with open(gitignore, "ab") as f:
        f.write(sep + b".env" + newline)


def ensure_env_gitignored(target_dir: Path) -> None:
    """Keep ``.env`` out of git in ``target_dir`` and say what happened.

    Asks git (``check-ignore``) when it can, so negations and order count,
    and warns when the template already tracks ``.env``: ignoring a
    tracked file doesn't stop a commit from including it.
    """
    gitignore = target_dir / ".gitignore"
    tracked = _git(target_dir, "ls-files", "--error-unmatch", ".env") == 0
    if tracked:
        # Ignoring a tracked file changes nothing; untracking it does.
        print_warning(
            "This template tracks .env in git, so a commit would include your API key.",
            stderr=True,
        )
        print_hint("Before you commit, run: git rm --cached .env")
        return
    if gitignore.is_symlink():
        # A cloned repo controls its links: never write through one.
        print_warning(
            "Did not add .env to .gitignore: it is a symbolic link. "
            "Make sure git ignores .env before you commit.",
            stderr=True,
        )
        return
    ignored = _env_ignored(target_dir)
    if ignored is None:
        lines = (
            {ln.strip() for ln in gitignore.read_text().splitlines()}
            if gitignore.exists()
            else set()
        )
        if not lines & _GITIGNORE_ENV_PATTERNS:
            _append_env_line(gitignore)
            print_info(_ADDED, stderr=True)
    elif not ignored:
        _append_env_line(gitignore)
        if _env_ignored(target_dir):
            print_info(_ADDED, stderr=True)


_ADDED = "Added .env to .gitignore to keep your API key out of git."


def inject_env(target_dir: Path, api_key: str) -> None:
    """Create or update DEEPGRAM_API_KEY in .env inside target_dir.

    The .env file is owner-only (0600) and kept out of git, since it holds
    the API key.

    Args:
        target_dir: Directory containing the cloned template.
        api_key: Deepgram API key to inject.
    """
    env_path = target_dir / ".env"

    if env_path.is_symlink():
        # A cloned repo controls its links: never write the key through one.
        print_warning(
            "Not writing your API key to .env: it is a symbolic link.", stderr=True
        )
        print_hint(
            "If you trust the file it links to, add DEEPGRAM_API_KEY there yourself."
        )
        return

    if env_path.exists():
        content = env_path.read_text()
        if "DEEPGRAM_API_KEY=" in content and "DEEPGRAM_API_KEY=$" not in content:
            _restrict_to_owner(env_path)
            console.print("[dim]DEEPGRAM_API_KEY already set in .env[/dim]")
            ensure_env_gitignored(target_dir)
            return
        # Replace placeholder or append
        if "DEEPGRAM_API_KEY=" in content:
            lines = content.splitlines()
            new_lines = [
                f"DEEPGRAM_API_KEY={api_key}"
                if ln.startswith("DEEPGRAM_API_KEY=")
                else ln
                for ln in lines
            ]
            _write_private(env_path, "\n".join(new_lines) + "\n")
        else:
            sep = "" if content.endswith("\n") or not content else "\n"
            _write_private(env_path, f"{content}{sep}DEEPGRAM_API_KEY={api_key}\n")
    else:
        _write_private(env_path, f"DEEPGRAM_API_KEY={api_key}\n")

    console.print("[green]✓[/green] Injected DEEPGRAM_API_KEY into .env")
    ensure_env_gitignored(target_dir)


# ── make init ─────────────────────────────────────────────────────────────────


def run_make_init(dest: Path) -> bool:
    """Run ``make check-prereqs && make init`` in the cloned directory.

    Used for starter repos that ship a Makefile with these standard targets.

    Args:
        dest: The cloned project directory.

    Returns:
        True if both commands succeeded, False otherwise.
    """
    makefile = dest / "Makefile"
    if not makefile.exists():
        return False

    for target in ("check-prereqs", "init"):
        result = subprocess.run(
            ["make", target],
            cwd=str(dest),
        )
        if result.returncode != 0:
            console.print(
                f"\n[red]✗[/red] [bold]make {target}[/bold] failed "
                f"(exit {result.returncode})"
            )
            if target == "check-prereqs":
                console.print(
                    "[dim]Fix the missing prerequisites above and run "
                    f"[bold]make init[/bold] inside [cyan]{dest.name}[/cyan][/dim]"
                )
            else:
                console.print(
                    f"[dim]Re-run manually: cd {dest.name} && make init[/dim]"
                )
            return False

    return True


# ── TOML lifecycle (fallback) ─────────────────────────────────────────────────


def run_lifecycle_step(step: LifecycleStep, cwd: Path) -> bool:
    """Execute a lifecycle step from deepgram.toml.

    Used as a fallback for repos that don't have a Makefile.

    Args:
        step: The lifecycle step definition.
        cwd: Working directory for command execution.

    Returns:
        True if the step succeeded, False otherwise.
    """
    if step.command is None:
        return True

    commands = step.command if isinstance(step.command, list) else [step.command]

    for cmd in commands:
        if step.message:
            console.print(f"[blue]{step.message}[/blue]")
        else:
            console.print(f"[dim]Running: {cmd}[/dim]")

        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            shell=True,
        )
        if result.returncode != 0:
            console.print(
                f"[red]✗[/red] Command failed (exit {result.returncode}): {cmd}"
            )
            return False

    return True
