"""Fetch the deepgram/skills bundle and expose it as installable skill folders.

A Deepgram agent skill is a *folder* — ``SKILL.md`` plus whatever supporting
files it ships, notably a ``references/`` subdirectory. The authoritative list
of skills lives in the upstream repo's ``.claude-plugin/marketplace.json``,
which that repo's CI validates against the directories on disk in both
directions on every pull request. Reading that manifest is therefore the only
way to stay in step with upstream without hardcoding a list that goes stale.

The whole repository is fetched as a single tarball rather than file-by-file:
one request gets the manifest, every ``SKILL.md`` and every ``references/``
file at a consistent revision, and it cannot half-succeed the way a loop of
per-file requests can.
"""

from __future__ import annotations

import json
import os
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = [
    "DEFAULT_SKILLS_REF",
    "RepoSkill",
    "SkillFetchError",
    "bundle_url",
    "fetch_skill_bundle",
    "resolve_skills_ref",
]

# Pinned to a released tag, not a branch: an install of a given deepctl
# version should produce the same skills today and in six months. Override
# with `--ref` or DEEPCTL_SKILLS_REF to track `main` or test a branch.
DEFAULT_SKILLS_REF = "deepgram-skills-v1.6.0"

SKILLS_REPO = "deepgram/skills"
REF_ENV_VAR = "DEEPCTL_SKILLS_REF"

_MANIFEST_PATH = ".claude-plugin/marketplace.json"
_PLUGIN_NAME = "deepgram"
_SKILL_ENTRY_FILE = "SKILL.md"
_DOWNLOAD_TIMEOUT = 30
_MAX_BUNDLE_BYTES = 64 * 1024 * 1024


class SkillFetchError(RuntimeError):
    """Raised when the upstream skill bundle cannot be fetched or trusted.

    Always fatal: installing a subset of the skills, or a stale cached
    subset, is worse than not installing at all because the user has no way
    to tell the difference from a complete install.
    """


@dataclass(frozen=True)
class RepoSkill:
    """One skill from the upstream repo, as a directory on disk."""

    name: str
    path: Path

    @property
    def entry_file(self) -> Path:
        """Path to this skill's ``SKILL.md``."""
        return self.path / _SKILL_ENTRY_FILE


def resolve_skills_ref(ref: str | None = None) -> str:
    """Resolve which upstream ref to install from.

    Precedence: explicit argument, then ``DEEPCTL_SKILLS_REF``, then the
    pinned default tag.
    """
    if ref:
        return ref
    from_env = os.environ.get(REF_ENV_VAR, "").strip()
    return from_env or DEFAULT_SKILLS_REF


def bundle_url(ref: str) -> str:
    """Return the codeload tarball URL for ``ref`` (tag, branch or SHA)."""
    return f"https://codeload.github.com/{SKILLS_REPO}/tar.gz/{ref}"


def fetch_skill_bundle(
    ref: str | None = None,
    *,
    cache_dir: Path | None = None,
    force: bool = False,
) -> list[RepoSkill]:
    """Download the upstream skill bundle and return its skills.

    Args:
        ref: Upstream git ref. Defaults to :func:`resolve_skills_ref`.
        cache_dir: Where extracted bundles are kept. Defaults to
            ``~/.deepctl/skills/repo_cache``.
        force: Re-download even if this ref is already cached.

    Returns:
        One :class:`RepoSkill` per entry in the upstream manifest, in
        manifest order.

    Raises:
        SkillFetchError: The bundle could not be downloaded, unpacked, or
            reconciled with its manifest.
    """
    resolved = resolve_skills_ref(ref)
    root = cache_dir or (Path.home() / ".deepctl" / "skills" / "repo_cache")
    target = root / _cache_key(resolved)

    if force or not (target / _MANIFEST_PATH).is_file():
        _download_and_extract(resolved, target)

    return read_manifest_skills(target)


def read_manifest_skills(root: Path) -> list[RepoSkill]:
    """Read ``.claude-plugin/marketplace.json`` under ``root``.

    Raises:
        SkillFetchError: The manifest is missing, malformed, lists no
            skills, or names a directory that is not a skill folder.
    """
    manifest_path = root / _MANIFEST_PATH
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SkillFetchError(
            f"Skill manifest {_MANIFEST_PATH} is missing from the {SKILLS_REPO} bundle."
        )
    except (OSError, UnicodeDecodeError) as exc:
        raise SkillFetchError(f"Could not read {manifest_path}: {exc}")
    except json.JSONDecodeError as exc:
        raise SkillFetchError(
            f"Skill manifest {_MANIFEST_PATH} is not valid JSON: {exc}"
        )

    entries = _manifest_skill_entries(raw)

    skills: list[RepoSkill] = []
    seen: set[str] = set()
    for entry in entries:
        name = _skill_name(entry)
        if name in seen:
            raise SkillFetchError(f"Skill manifest lists {name!r} more than once.")
        seen.add(name)
        path = _resolve_skill_dir(root, entry)
        skills.append(RepoSkill(name=name, path=path))

    return skills


# ---------------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------------


def _manifest_skill_entries(raw: object) -> list[str]:
    """Pull ``plugins[deepgram].skills`` out of a parsed manifest."""
    if not isinstance(raw, dict):
        raise SkillFetchError("Skill manifest is not a JSON object.")

    plugins = raw.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        raise SkillFetchError("Skill manifest has no 'plugins' array.")

    entries: object = None
    found = False
    for candidate in plugins:
        if isinstance(candidate, dict) and candidate.get("name") == _PLUGIN_NAME:
            entries = candidate.get("skills")
            found = True
            break
    if not found:
        raise SkillFetchError(f"Skill manifest has no plugin named {_PLUGIN_NAME!r}.")

    if not isinstance(entries, list) or not entries:
        raise SkillFetchError(
            f"Plugin {_PLUGIN_NAME!r} in the skill manifest lists no skills."
        )
    if not all(isinstance(e, str) and e.strip() for e in entries):
        raise SkillFetchError(
            f"Plugin {_PLUGIN_NAME!r} in the skill manifest has a "
            "non-string skill entry."
        )
    return [str(e) for e in entries]


def _skill_name(entry: str) -> str:
    """Derive a skill's directory name from a manifest entry."""
    name = entry.strip().strip("/").rsplit("/", 1)[-1]
    if not name or name in {".", ".."}:
        raise SkillFetchError(f"Skill manifest entry {entry!r} has no name.")
    return name


def _resolve_skill_dir(root: Path, entry: str) -> Path:
    """Resolve a manifest entry to a skill directory inside ``root``."""
    relative = _relative_parts(entry)
    if relative is None:
        raise SkillFetchError(
            f"Skill manifest entry {entry!r} escapes the bundle root."
        )

    path = root.joinpath(*relative)
    if not path.is_dir():
        raise SkillFetchError(
            f"Skill manifest lists {entry!r} but that directory is not in "
            f"the {SKILLS_REPO} bundle."
        )
    if not (path / _SKILL_ENTRY_FILE).is_file():
        raise SkillFetchError(
            f"Skill manifest lists {entry!r} but it has no {_SKILL_ENTRY_FILE}."
        )
    return path


def _relative_parts(entry: str) -> list[str] | None:
    """Split a manifest entry into safe relative path parts, or None."""
    parts: list[str] = []
    for part in entry.strip().split("/"):
        if part in ("", "."):
            continue
        if part == ".." or part.startswith("/"):
            return None
        parts.append(part)
    return parts or None


# ---------------------------------------------------------------------------
# Download + extraction
# ---------------------------------------------------------------------------


def _cache_key(ref: str) -> str:
    """Filesystem-safe directory name for a ref."""
    return "".join(c if c.isalnum() or c in "-._" else "_" for c in ref)


def _download_and_extract(ref: str, target: Path) -> None:
    """Download the bundle for ``ref`` and replace ``target`` with it."""
    url = bundle_url(ref)
    with tempfile.TemporaryDirectory(prefix="deepctl-skills-") as tmp:
        tmp_path = Path(tmp)
        archive = tmp_path / "bundle.tar.gz"
        _download(url, ref, archive)

        unpacked = tmp_path / "unpacked"
        unpacked.mkdir()
        _extract(archive, unpacked, ref)

        roots = [p for p in unpacked.iterdir() if p.is_dir()]
        if len(roots) != 1:
            raise SkillFetchError(
                f"The {SKILLS_REPO}@{ref} bundle does not have the expected "
                "single top-level directory."
            )

        # Validate before publishing to the cache, so a bad bundle never
        # replaces a good one.
        read_manifest_skills(roots[0])

        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(roots[0]), str(target))


def _download(url: str, ref: str, dest: Path) -> None:
    """Fetch ``url`` into ``dest``, mapping every failure to SkillFetchError."""
    try:
        with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT) as resp:
            _copy_limited(resp, dest)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise SkillFetchError(
                f"{SKILLS_REPO} has no ref {ref!r} (HTTP 404 from {url}). "
                "Check the --ref value."
            )
        raise SkillFetchError(
            f"Could not download {SKILLS_REPO}@{ref}: HTTP {exc.code} from {url}."
        )
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise SkillFetchError(
            f"Could not download {SKILLS_REPO}@{ref} from {url}: {exc}"
        )


def _copy_limited(src: IO[bytes], dest: Path) -> None:
    """Stream ``src`` to ``dest``, refusing an implausibly large bundle."""
    total = 0
    with dest.open("wb") as fh:
        while chunk := src.read(64 * 1024):
            total += len(chunk)
            if total > _MAX_BUNDLE_BYTES:
                raise SkillFetchError(
                    f"The {SKILLS_REPO} bundle exceeded "
                    f"{_MAX_BUNDLE_BYTES} bytes; refusing to unpack it."
                )
            fh.write(chunk)


def _extract(archive: Path, dest: Path, ref: str) -> None:
    """Unpack ``archive`` into ``dest``, rejecting unsafe members."""
    try:
        with tarfile.open(archive, "r:gz") as tar:
            # _safe_members already rejects anything that escapes dest; the
            # stdlib filter is belt-and-braces where the interpreter has it
            # (3.12+, and the backports in 3.10.12 / 3.11.4).
            extra: dict[str, Any] = {}
            if hasattr(tarfile, "data_filter"):
                extra["filter"] = "data"
            tar.extractall(dest, members=_safe_members(tar, dest), **extra)
    except SkillFetchError:
        raise
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise SkillFetchError(
            f"The {SKILLS_REPO}@{ref} download is not a readable tar.gz archive: {exc}"
        )


def _safe_members(tar: tarfile.TarFile, dest: Path) -> Iterator[tarfile.TarInfo]:
    """Yield only regular files and directories that stay inside ``dest``.

    ``tarfile``'s ``filter="data"`` argument is not available on every
    Python version this CLI supports, so the checks are explicit.
    """
    root = dest.resolve()
    for member in tar:
        if not (member.isfile() or member.isdir()):
            # Symlinks, hardlinks and devices have no place in a skill
            # bundle and are how tar extraction turns into arbitrary writes.
            continue
        name = member.name
        if name.startswith("/") or ".." in Path(name).parts:
            raise SkillFetchError(
                f"Refusing to unpack {name!r}: it escapes the bundle root."
            )
        resolved = (root / name).resolve()
        if resolved != root and root not in resolved.parents:
            raise SkillFetchError(
                f"Refusing to unpack {name!r}: it escapes the bundle root."
            )
        member.mode = 0o755 if member.isdir() else 0o644
        yield member
