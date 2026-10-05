"""Fetch the deepgram/skills bundle from an immutable pin and list its skills.

The repository is downloaded as one codeload tarball, unpacked with explicit
safety checks and published atomically into deepctl's cache. The skills come
from the upstream ``.claude-plugin/marketplace.json``, and each name is checked
to be one plain directory name before any path is built from it.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable

SKILLS_REPO = "deepgram/skills"
REF_ENV_VAR = "DEEPCTL_SKILLS_REF"

# The default bundle is pinned by full commit SHA plus the sha256 of its
# codeload tarball, because the release tag is a lightweight, movable tag.
DEFAULT_SKILLS_COMMIT = "0fc13fad726fb78e17fb1f05ba5942f0d022990f"
DEFAULT_SKILLS_SHA256 = (
    "5b7f975378110372c8ae3a3c712b72ba2fa43b06f2d1d93497abf87262a23980"
)
#: The release that DEFAULT_SKILLS_COMMIT is. A label for people; never fetched.
DEFAULT_SKILLS_RELEASE = "deepgram-skills-v1.7.0"

_MANIFEST_PATH = ".claude-plugin/marketplace.json"
_PLUGIN_NAME = "deepgram"
_SKILL_ENTRY_FILE = "SKILL.md"

_DOWNLOAD_TIMEOUT = 30.0
# Caps on the download and what it may unpack to. The real bundle is about
# 160 KB with 65 members, so anything near these is not the bundle we expect.
# They bound member count, names and file data, but tarfile reads pax/GNU header
# metadata before they run. That is accepted: the pin is hash-checked first, and
# a user ref's tarball comes from codeload's git archive, not crafted pax data.
_MAX_BUNDLE_BYTES = 64 * 1024 * 1024
_MAX_EXTRACTED_BYTES = 256 * 1024 * 1024
_MAX_MEMBERS = 10_000
_MAX_MEMBER_NAME_BYTES = 512

_MAX_REF_LENGTH = 100
# The longest valid ref, "a/" * 49 + "aa", spells to 4 + 49 * 4 + 2 = 202 bytes.
# 120 leaves a typical Windows home, the cache root and the bundle's deepest
# file (46 characters) under MAX_PATH (260). "pinned-<sha>" is 47 bytes.
_MAX_CACHE_NAME_BYTES = 120
_REF_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
# A manifest entry must be exactly ``./skills/<name>`` or ``skills/<name>``,
# and ``<name>`` must be one plain directory name.
_ENTRY_PATTERN = re.compile(r"(?:\./)?skills/([A-Za-z0-9][A-Za-z0-9._-]*)")
# A path component Windows cannot store as written: a reserved device name,
# with or without an extension, or a name it would strip a trailing ' '/'.' from.
_WINDOWS_UNSAFE = re.compile(
    r"(?i)(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[0-9\u00b9\u00b2\u00b3])"
    r"(?:\..*)?|.*[ .]"
)

# Written into every cache directory this module publishes. A non-empty directory
# at a cache path without it is never moved or deleted. POSIX rename replaces an
# empty one, so one appearing in the rename window can go (no file data at risk).
_CACHE_MARKER = ".deepctl-skills-cache"
_STAGING_PREFIX = ".tmp-"


class SkillFetchError(Exception):
    """The skills bundle could not be fetched, unpacked or trusted."""


class SkillRefNotFoundError(SkillFetchError):
    """The skills repository has no such ref (HTTP 404)."""


class SkillRefInvalidError(SkillFetchError):
    """The ref is not one this module will put in a URL or a directory name."""


@dataclass(frozen=True)
class RepoSkill:
    """One validated skill: a plain directory name and its folder in the cache."""

    name: str
    path: Path


def _cache_name(ref: str) -> str:
    """Return the cache directory name for a validated ``ref``."""
    # The prefixes keep every user ref out of the hash-checked pin's directory,
    # even on a case-insensitive filesystem. '%' is not a ref character, so
    # spelling '/' as '%2F' cannot make two refs collide.
    if ref == DEFAULT_SKILLS_COMMIT:
        return f"pinned-{ref}"
    return "ref-" + ref.replace("/", "%2F")


def portable_name(name: str) -> bool:
    """True if ``name`` is one plain folder name safe on every OS (as entries)."""
    match = _ENTRY_PATTERN.fullmatch(f"skills/{name}")
    return bool(match) and not _WINDOWS_UNSAFE.fullmatch(name)


def validate_ref(ref: str) -> str:
    """Return ``ref`` if it is safe in a URL path and a cache name, else raise."""
    if len(ref) > _MAX_REF_LENGTH:
        raise SkillRefInvalidError(
            f"The skills ref is {len(ref)} characters, more than the "
            f"{_MAX_REF_LENGTH} allowed."
        )
    if (
        not _REF_PATTERN.fullmatch(ref)
        or ".." in ref
        or "//" in ref
        or "/." in ref
        or ref.endswith(("/", "."))
    ):
        raise SkillRefInvalidError(
            f"The skills ref {ref!r} must start with a letter or digit, use only "
            "letters, digits and '._/-', and not contain '..', empty segments or "
            "segments starting with '.', or end in '.' or '/'."
        )
    if len(_cache_name(ref).encode("utf-8")) > _MAX_CACHE_NAME_BYTES:
        raise SkillRefInvalidError(
            f"The skills ref {ref!r} would need a cache directory name longer "
            f"than {_MAX_CACHE_NAME_BYTES} bytes."
        )
    return ref


def resolve_skills_ref(ref: str | None = None) -> str:
    """Pick the ref: ``ref``, then a non-blank ``DEEPCTL_SKILLS_REF``, then the pin.

    Raises :class:`SkillRefInvalidError` if the chosen ref fails validation.
    """
    if ref is not None:
        return validate_ref(ref)
    from_env = os.environ.get(REF_ENV_VAR, "").strip()
    if from_env:
        return validate_ref(from_env)
    return DEFAULT_SKILLS_COMMIT


def bundle_url(ref: str) -> str:
    """Return the codeload tarball URL for a validated ``ref``."""
    return f"https://codeload.github.com/{SKILLS_REPO}/tar.gz/{ref}"


def fetch_skill_bundle(
    ref: str | None = None,
    *,
    cache_dir: Path | None = None,
    force: bool = False,
    download: Callable[[str], bytes] | None = None,
) -> list[RepoSkill]:
    """Fetch the skills bundle into the cache and return its skills.

    A user ref (argument or ``DEEPCTL_SKILLS_REF``) has no known hash, so only
    the pinned commit is checked against :data:`DEFAULT_SKILLS_SHA256`.

    Only the pinned commit, whose content cannot change, is served from the
    cache. Its directory is published only after the hash check and full
    validation pass, and is validated again on every hit. Other refs may move,
    so they are always downloaded. ``force`` skips the cache hit. ``cache_dir``
    must be a directory deepctl owns. ``download`` returns a URL's bytes. The
    returned paths are valid until the same ref is fetched again.

    Raises :class:`SkillRefInvalidError`, :class:`SkillRefNotFoundError` or,
    for any other failure, :class:`SkillFetchError`.
    """
    resolved = resolve_skills_ref(ref)
    pinned = resolved == DEFAULT_SKILLS_COMMIT
    root = cache_dir or Path.home() / ".deepctl" / "skills" / "repo_cache"
    target = root / _cache_name(resolved)

    if pinned and not force and _is_our_cache(target):
        try:
            return read_manifest_skills(target)
        except SkillFetchError:
            pass  # Incomplete or damaged: download and replace it below.

    data = (download or _download)(bundle_url(resolved))
    if pinned and hashlib.sha256(data).hexdigest() != DEFAULT_SKILLS_SHA256:
        raise SkillFetchError(
            f"The downloaded {DEFAULT_SKILLS_RELEASE} bundle does not match its "
            "pinned sha256, so it was not used."
        )
    return _publish(data, target)


def read_manifest_skills(root: Path) -> list[RepoSkill]:
    """Return the skills ``root``'s manifest lists for the deepgram plugin.

    Every entry is validated before any path is built from it. Other plugins
    point at other repositories and are ignored. Raises
    :class:`SkillFetchError` for a bad manifest, entry or skill folder.
    """
    try:
        raw = json.loads((root / _MANIFEST_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SkillFetchError(f"Could not read the skills manifest: {exc}.")

    plugins = raw.get("plugins") if isinstance(raw, dict) else None
    if not isinstance(plugins, list):
        plugins = []
    matches = [
        p for p in plugins if isinstance(p, dict) and p.get("name") == _PLUGIN_NAME
    ]
    entries = matches[0].get("skills") if len(matches) == 1 else None
    if not isinstance(entries, list) or not entries:
        raise SkillFetchError(
            f"The skills manifest has no single {_PLUGIN_NAME!r} plugin listing skills."
        )

    names: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        match = _ENTRY_PATTERN.fullmatch(entry) if isinstance(entry, str) else None
        # The pattern already refuses '/', '\\', ':', '.' and '..'.
        if match is None or _WINDOWS_UNSAFE.fullmatch(match.group(1)):
            raise SkillFetchError(
                f"The skills manifest entry {entry!r} is not ./skills/<portable name>."
            )
        name = match.group(1)
        if name.casefold() in seen:
            raise SkillFetchError(f"The skills manifest lists {name!r} twice.")
        seen.add(name.casefold())
        names.append(name)

    skills = [RepoSkill(name, root / "skills" / name) for name in names]
    for skill in skills:
        try:
            found = (skill.path / _SKILL_ENTRY_FILE).is_file()
        except OSError:  # For example, a name too long for the filesystem.
            found = False
        if not found:
            raise SkillFetchError(
                f"The skill {skill.name!r} has no {_SKILL_ENTRY_FILE} in the bundle."
            )
    return skills


def _download(url: str, *, transport: httpx.BaseTransport | None = None) -> bytes:
    """Return the bytes at ``url``, refusing more than the bundle size cap."""
    try:
        with (
            httpx.Client(
                transport=transport, timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True
            ) as client,
            client.stream("GET", url) as resp,
        ):
            if resp.status_code == 404:
                raise SkillRefNotFoundError(f"No skills bundle was found at {url}.")
            if resp.status_code != 200:
                raise SkillFetchError(
                    f"Downloading {url} failed with HTTP {resp.status_code}."
                )
            data = bytearray()
            for chunk in resp.iter_bytes():
                data += chunk
                if len(data) > _MAX_BUNDLE_BYTES:
                    raise SkillFetchError(
                        f"The skills bundle is larger than {_MAX_BUNDLE_BYTES} bytes."
                    )
            return bytes(data)
    except httpx.HTTPError as exc:
        raise SkillFetchError(f"Could not download {url}: {str(exc).rstrip('.')}.")


def _is_our_cache(path: Path) -> bool:
    """True if ``path`` is a real directory that this module published."""
    try:
        return not path.is_symlink() and (path / _CACHE_MARKER).is_file()
    except OSError:
        return False


def _safe_members(tar: tarfile.TarFile) -> list[tuple[tarfile.TarInfo, str]]:
    """Check every member, then return each with its top-level dir stripped.

    Nothing is written until every member has passed.
    """
    checked: list[tuple[tarfile.TarInfo, str]] = []
    tops: set[str] = set()
    total = 0
    for member in tar:
        name = member.name
        if len(checked) >= _MAX_MEMBERS:
            raise SkillFetchError(f"The bundle has more than {_MAX_MEMBERS} members.")
        if len(name.encode("utf-8", "surrogateescape")) > _MAX_MEMBER_NAME_BYTES:
            raise SkillFetchError("The bundle has a member name that is too long.")
        if not (member.isreg() or member.isdir()):
            # Symlinks, hardlinks, devices and fifos never belong in a bundle.
            raise SkillFetchError(
                f"The bundle member {name!r} is not a regular file or directory."
            )
        parts = [p for p in name.split("/") if p not in ("", ".")]
        # '\\' and ':' cover Windows separators, drives (C:\x, C:x) and streams.
        windows = "\\" in name or ":" in name
        windows = windows or any(_WINDOWS_UNSAFE.fullmatch(p) for p in parts)
        if not parts or ".." in parts or name.startswith("/") or windows:
            raise SkillFetchError(f"The bundle member {name!r} is not a safe path.")
        total += member.size if member.isreg() else 0
        if total > _MAX_EXTRACTED_BYTES:
            raise SkillFetchError(
                f"The bundle unpacks to more than {_MAX_EXTRACTED_BYTES} bytes."
            )
        tops.add(parts[0])
        checked.append((member, "/".join(parts[1:])))
    if len(tops) != 1:
        raise SkillFetchError("The bundle does not have a single top-level directory.")
    return checked


def _extract(data: bytes, dest: Path) -> None:
    """Unpack the tarball ``data`` into the new directory ``dest``."""
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            members = _safe_members(tar)
            dest.mkdir()
            for member, rel in members:
                path = dest.joinpath(*rel.split("/")) if rel else dest
                if member.isdir():
                    path.mkdir(parents=True, exist_ok=True)
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                src = tar.extractfile(member)
                if src is None:
                    raise SkillFetchError(f"Could not read {member.name!r}.")
                # 'xb' refuses to overwrite, so a duplicate member (or a
                # case-insensitive clash) fails instead of replacing a file.
                with src, path.open("xb") as out:
                    shutil.copyfileobj(src, out)
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise SkillFetchError(f"Could not unpack the skills bundle: {exc}.")


def _publish(data: bytes, target: Path) -> list[RepoSkill]:
    """Unpack, validate and atomically put the bundle at ``target``.

    Work happens in a fresh ``mkdtemp`` directory beside ``target``, so every
    rename stays on one filesystem. A previous cache is renamed into staging,
    the new tree is renamed into place, and if that fails a directory is put
    back; anything else is kept in staging, so nothing is lost or overwritten.
    If another process publishes the same ref first, its copy is used.
    """
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=_STAGING_PREFIX, dir=target.parent))
    except OSError as exc:
        raise SkillFetchError(f"Could not prepare the skills cache: {exc}.")
    new, previous, swapping = staging / "new", staging / "previous", False
    try:
        _extract(data, new)
        skills = read_manifest_skills(new)
        try:
            (new / _CACHE_MARKER).write_text(target.name, encoding="utf-8")
            if target.is_symlink() or target.exists():
                if not _is_our_cache(target):
                    raise SkillFetchError(
                        f"{target} was not created by deepctl, so it was left alone"
                    )
                os.replace(target, previous)
                if not _is_our_cache(previous):  # Swapped in after the check.
                    raise SkillFetchError(
                        f"{target} changed while publishing, so it was not replaced"
                    )
            swapping = True
            os.replace(new, target)  # Can replace an empty dir; see _CACHE_MARKER.
        except BaseException as exc:  # Ctrl-C too: never rmtree the only copy.
            if swapping and isinstance(exc, OSError) and _is_our_cache(target):
                # Another process published this ref after ours moved aside.
                return read_manifest_skills(target)
            try:  # Put back only a real dir: renaming a file or link clobbers.
                if os.path.isdir(previous) and not os.path.islink(previous):
                    os.replace(previous, target)  # Same empty-dir caveat.
            except OSError:
                pass  # The finally sees that staging holds the only copy.
            mine = _is_our_cache(target) and _is_our_cache(previous)
            kept = os.path.lexists(previous) and not mine
            where = f"; what was there is kept in {previous}" if kept else ""
            if not isinstance(exc, (OSError, SkillFetchError)):
                raise
            raise SkillFetchError(
                f"Could not publish the skills bundle to {target}: {exc}{where}."
            )
        return [RepoSkill(s.name, target / "skills" / s.name) for s in skills]
    finally:
        # Ownership: mkdtemp made ``staging`` in this call. Delete it only if
        # ``previous`` is absent, or both it and ``target`` are proven ours, so
        # a sole or foreign copy is kept. State, not a flag, survives a Ctrl-C.
        if not os.path.lexists(previous) or (
            _is_our_cache(target) and _is_our_cache(previous)
        ):
            shutil.rmtree(staging, ignore_errors=True)
