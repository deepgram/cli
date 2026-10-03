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

import http.client
import importlib.metadata
import json
import os
import posixpath
import re
import secrets
import shutil
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = [
    "DEFAULT_SKILLS_REF",
    "PINNED_REF_SOURCE",
    "RepoSkill",
    "SkillFetchError",
    "SkillRefInvalidError",
    "SkillRefNotFoundError",
    "bundle_url",
    "fetch_skill_bundle",
    "resolve_skills_ref",
    "validate_ref",
]

# Pinned to a released tag, not a branch: an install of a given deepctl
# version should produce the same skills today and in six months. Override
# with `--ref` or DEEPCTL_SKILLS_REF to track `main` or test a branch.
DEFAULT_SKILLS_REF = "deepgram-skills-v1.7.0"

SKILLS_REPO = "deepgram/skills"
REF_ENV_VAR = "DEEPCTL_SKILLS_REF"

#: The ``ref_source`` of a ref that came from :data:`DEFAULT_SKILLS_REF`.
PINNED_REF_SOURCE = "deepctl's pinned default"

_MANIFEST_PATH = ".claude-plugin/marketplace.json"
_PLUGIN_NAME = "deepgram"
_SKILL_ENTRY_FILE = "SKILL.md"
_DOWNLOAD_TIMEOUT = 30

# The compressed download, and three caps on what it may expand into. A
# skills checkout is a few megabytes of Markdown; anything near these limits
# is not the bundle we expect, whatever codeload says it is.
_MAX_BUNDLE_BYTES = 64 * 1024 * 1024
_MAX_EXTRACTED_BYTES = 256 * 1024 * 1024
_MAX_MEMBERS = 10_000
_MAX_MEMBER_NAME_BYTES = 512

# A git ref as this module accepts it: tag, branch or SHA, made only of
# characters that are safe in a URL path and a directory name, starting with a
# letter or digit (so it cannot read as an option or a hidden directory) and
# never containing ``..``. _REF_SHAPE states the same rule to the user.
_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
_REF_SHAPE = "letters, digits, '.', '_', '-' and '/', starting with a letter or digit"
# Staging and retired cache directories left behind by an interrupted
# publish are swept on the next fetch once they are older than this.
_STAGING_PREFIX = ".tmp-"
_RETIRED_PREFIX = ".old-"
_STALE_AFTER_SECONDS = 60 * 60
#: Random bytes in a retired directory's name, hex-encoded after a '-'.
_RETIRED_TOKEN_BYTES = 4
#: Characters ``tempfile.mkdtemp`` appends to the staging prefix.
_MKDTEMP_RANDOM_CHARS = 8

#: 255 bytes is the filename limit on every filesystem deepctl runs on.
_MAX_FILENAME_BYTES = 255
#: Bytes a cache directory name grows by in the longest name this module
#: ever gives it: ``_publish`` renames the old copy to
#: ``.old-<key>-<8 hex>`` before swapping the new one in. The staging
#: directory (``.tmp-<random>``) does not embed the key at all, so it adds
#: nothing on top of the key -- but it must still fit on its own.
_RETIRED_NAME_OVERHEAD = len(_RETIRED_PREFIX) + 1 + 2 * _RETIRED_TOKEN_BYTES
assert len(_STAGING_PREFIX) + _MKDTEMP_RANDOM_CHARS <= _MAX_FILENAME_BYTES
#: The longest cache key, in bytes (and so the longest ref, in
#: characters): one whose retired name is exactly the filename limit.
#: Longer than any real tag, branch or SHA, so nothing legitimate is
#: refused, and short enough that a forced refresh can always rename the
#: previous copy aside.
_MAX_REF_LENGTH = _MAX_FILENAME_BYTES - _RETIRED_NAME_OVERHEAD


class SkillFetchError(RuntimeError):
    """Raised when the upstream skill bundle cannot be fetched or trusted.

    Always fatal: installing a subset of the skills, or a stale cached
    subset, is worse than not installing at all because the user has no way
    to tell the difference from a complete install.
    """


class SkillRefNotFoundError(SkillFetchError):
    """The upstream repo answered 404: the ref does not exist there.

    Not a network failure, so retrying cannot help; the way out is a
    different ref. ``ref_source`` says where the ref came from (``--ref``,
    :data:`REF_ENV_VAR`, the skill records, or :data:`PINNED_REF_SOURCE`)
    when the caller knew, so the message, and the caller's advice, can
    name the one setting to change. ``None`` means it was not passed.
    """

    def __init__(self, ref: str, url: str, ref_source: str | None = None) -> None:
        self.ref = ref
        self.url = url
        self.ref_source = ref_source
        if ref_source:
            where = f"Check the ref name, which came from {ref_source}."
        else:
            where = (
                f"Check the ref name, which came from --ref, {REF_ENV_VAR}, "
                f"the last install's record or {PINNED_REF_SOURCE}."
            )
        super().__init__(
            f"{SKILLS_REPO} has no ref {ref!r} (HTTP 404 from {url}). {where}"
        )

    def __reduce__(self) -> tuple[Any, ...]:
        # BaseException pickles and copies by calling the class with
        # ``self.args``, which here is only the formatted message.
        # The third item restores ``__dict__``, so a note added with
        # ``add_note()`` or an attribute set later survives too.
        return (type(self), (self.ref, self.url, self.ref_source), self.__dict__)

    def advice(self, command: str) -> str:
        """What to do instead of retrying, for a caller that takes no --ref.

        ``command`` is the ``dg skills`` subcommand that would install
        again (``'dg skills install'`` or ``'dg skills update'``). Run as
        is, it would reuse this ref and fail the same way.
        """
        if self.ref_source == REF_ENV_VAR:
            return (
                f"Set {REF_ENV_VAR} to another ref, or unset it, then run '{command}'."
            )
        return f"Run '{command} --ref <tag>' to choose another ref."


#: What a bad ref from :data:`REF_ENV_VAR` ends with, wherever it is reported.
_ENV_REF_WAY_OUT = "Set it to another ref, or unset it to use the pinned release."


class SkillRefInvalidError(SkillFetchError):
    """The ref is not one this module will put in a URL: wrong shape.

    Refused before any download, so retrying cannot help either; the way
    out is a different ref. ``reason`` is what is wrong with it.
    ``ref_source`` says where it came from, as for
    :class:`SkillRefNotFoundError`. A ref from :data:`REF_ENV_VAR` is the
    one case the message has to name the source: nothing on the command
    line shows the user that variable is set.
    """

    def __init__(self, ref: str, reason: str, ref_source: str | None = None) -> None:
        self.ref = ref
        self.reason = reason
        self.ref_source = ref_source
        message = reason
        if ref_source == REF_ENV_VAR:
            message = f"{reason} That ref came from {REF_ENV_VAR}. {_ENV_REF_WAY_OUT}"
        super().__init__(message)

    def __reduce__(self) -> tuple[Any, ...]:
        # BaseException rebuilds from ``self.args`` (the message alone).
        return (type(self), (self.ref, self.reason, self.ref_source), self.__dict__)

    def advice(self, command: str) -> str:
        """What to do instead of retrying, for a caller that takes no --ref.

        The same contract as :meth:`SkillRefNotFoundError.advice`. For a
        ref from :data:`REF_ENV_VAR` the message already says how to change
        it, so this only adds the command to run once it is changed.
        """
        if self.ref_source == REF_ENV_VAR:
            return f"Then run '{command}'."
        return f"Run '{command} --ref <tag>' to choose another ref."


@dataclass(frozen=True)
class RepoSkill:
    """One skill from the upstream repo, as a directory on disk."""

    name: str
    path: Path


def validate_ref(ref: str, ref_source: str | None = None) -> str:
    """Return ``ref`` if it is a plausible git ref, else raise.

    The ref ends up in a URL path and a cache directory name, so this is
    stricter than git itself: only ``[A-Za-z0-9._/-]``, a leading letter or
    digit, no ``..`` anywhere, no empty path segment, and at most
    :data:`_MAX_REF_LENGTH` characters, and a cache directory name
    (:func:`_cache_key`) of at most :data:`_MAX_REF_LENGTH` bytes.

    ``ref_source`` is where ``ref`` came from. Passed as
    :data:`REF_ENV_VAR`, the error names the variable and how to change it.

    Raises:
        SkillRefInvalidError: ``ref`` is empty, too long, or has a
            character or shape this module will not put in a URL.
    """

    def invalid(reason: str) -> SkillRefInvalidError:
        return SkillRefInvalidError(ref, reason, ref_source)

    if not ref:
        raise invalid(
            f"Skills ref is empty. Pass a tag, branch or commit SHA "
            f"({_REF_SHAPE}), for example {DEFAULT_SKILLS_REF!r}."
        )
    if len(ref) > _MAX_REF_LENGTH:
        raise invalid(
            f"Invalid skills ref: {len(ref)} characters is longer than the "
            f"{_MAX_REF_LENGTH} a ref may have (it names a cache directory, "
            f"and replacing that directory renames it {_RETIRED_NAME_OVERHEAD} "
            f"bytes longer, up to the {_MAX_FILENAME_BYTES}-byte filename limit). "
            f"Starts {ref[:40]!r}..."
        )
    if (
        not _REF_PATTERN.fullmatch(ref)
        or ".." in ref
        or ref.endswith("/")
        or "//" in ref
    ):
        raise invalid(
            f"Invalid skills ref {ref!r}: a ref may contain only {_REF_SHAPE}, "
            "and may not contain '..' or an empty path segment."
        )
    # The character cap alone is not enough: _cache_key spells each '/' as
    # '%2F', so a ref within it can name a directory three times as long.
    key_bytes = len(_cache_key(ref).encode())
    if key_bytes > _MAX_REF_LENGTH:
        raise invalid(
            f"Invalid skills ref: its cache directory name would be "
            f"{key_bytes} bytes (each '/' is stored as '%2F'), longer than "
            f"the {_MAX_REF_LENGTH} a cache directory name may have (replacing "
            f"it adds {_RETIRED_NAME_OVERHEAD} bytes, up to the "
            f"{_MAX_FILENAME_BYTES}-byte filename limit). "
            f"Starts {ref[:40]!r}..."
        )
    return ref


def resolve_skills_ref(ref: str | None = None) -> str:
    """Resolve which upstream ref to install from.

    Precedence: explicit argument, then ``DEEPCTL_SKILLS_REF``, then the
    pinned default tag. A blank environment variable is treated as unset;
    an explicit empty argument is an error, because ``--ref ""`` is far
    more likely a shell-quoting slip than a request for the default.

    Raises:
        SkillRefInvalidError: The chosen ref fails :func:`validate_ref`.
            One from the environment says so.
    """
    if ref is not None:
        return validate_ref(ref)
    from_env = os.environ.get(REF_ENV_VAR, "").strip()
    if from_env:
        return validate_ref(from_env, REF_ENV_VAR)
    return validate_ref(DEFAULT_SKILLS_REF, PINNED_REF_SOURCE)


def bundle_url(ref: str) -> str:
    """Return the codeload tarball URL for ``ref`` (tag, branch or SHA).

    The ref is percent-encoded (keeping ``/``) so that whatever reaches
    this function, it can only ever name a path under the repo's tarball
    endpoint. Callers are expected to have run :func:`validate_ref` first.
    """
    return (
        f"https://codeload.github.com/{SKILLS_REPO}/tar.gz/"
        f"{urllib.parse.quote(ref, safe='/')}"
    )


def fetch_skill_bundle(
    ref: str | None = None,
    *,
    cache_dir: Path | None = None,
    force: bool = False,
    ref_source: str | None = None,
) -> list[RepoSkill]:
    """Download the upstream skill bundle and return its skills.

    Args:
        ref: Upstream git ref. Defaults to :func:`resolve_skills_ref`.
        cache_dir: Where extracted bundles are kept. Defaults to
            ``~/.deepctl/skills/repo_cache``.
        force: Re-download even if this ref is already cached.
        ref_source: Where ``ref`` came from, for a
            :class:`SkillRefNotFoundError`. Worked out here when ``ref``
            is ``None``.

    Returns:
        One :class:`RepoSkill` per entry in the upstream manifest, in
        manifest order.

    Raises:
        SkillRefNotFoundError: The upstream repo has no such ref.
        SkillFetchError: The ref is invalid, or the bundle could not be
            downloaded, unpacked, or reconciled with its manifest.
    """
    resolved = resolve_skills_ref(ref)
    if ref is None and ref_source is None:
        from_env = os.environ.get(REF_ENV_VAR, "").strip()
        ref_source = REF_ENV_VAR if from_env else PINNED_REF_SOURCE
    root = cache_dir or (Path.home() / ".deepctl" / "skills" / "repo_cache")
    target = root / _cache_key(resolved)

    if force or not _cache_is_complete(target):
        _download_and_extract(resolved, target, ref_source)

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
        try:
            path = _resolve_skill_dir(root, entry)
        except OSError as exc:
            # is_dir()/is_file() swallow "not found" but not, say, a name
            # too long for the filesystem or a permission error.
            raise SkillFetchError(
                f"Could not read skill {entry!r} in {root}: {exc}"
            ) from exc
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
# Cache layout
# ---------------------------------------------------------------------------


def _cache_key(ref: str) -> str:
    """Filesystem-safe directory name for a validated ref.

    ``/`` is the only character :func:`validate_ref` admits that a path
    cannot hold, and it is spelled ``%2F``: ``%`` is not a ref character, so
    ``release/1`` and ``release_1`` can never share a directory. Any other
    character (only reachable if validation was bypassed) becomes ``_``.
    """
    return "".join(
        "%2F" if c == "/" else c if c.isalnum() or c in "-._" else "_" for c in ref
    )


def _cache_is_complete(target: Path) -> bool:
    """True if ``target`` holds a manifest and every skill it lists.

    A manifest alone is not enough: an interrupted or partially deleted
    cache can keep the manifest and lose a skill folder, and serving that
    would silently install a subset.

    Raises:
        SkillFetchError: The filesystem refused to look at ``target`` at
            all (a name too long, a permission error), which a download
            into the same path could not fix either.
    """
    try:
        if not (target / _MANIFEST_PATH).is_file():
            return False
    except OSError as exc:
        raise SkillFetchError(
            f"Could not read the skills cache {target}: {exc}"
        ) from exc
    try:
        read_manifest_skills(target)
    except SkillFetchError:
        return False
    return True


def _prune_stale_dirs(cache_root: Path) -> None:
    """Best-effort removal of staging and retired dirs older than an hour.

    Never raises: the cache still works with leftovers in it, so pruning
    must not be able to fail a fetch.
    """
    try:
        entries = list(cache_root.iterdir())
    except OSError:
        return
    cutoff = time.time() - _STALE_AFTER_SECONDS
    for entry in entries:
        if not entry.name.startswith((_STAGING_PREFIX, _RETIRED_PREFIX)):
            continue
        try:
            if entry.is_symlink() or not entry.is_dir():
                continue
            if entry.stat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        shutil.rmtree(entry, ignore_errors=True)


def _unique_sibling(target: Path, prefix: str) -> Path:
    """A not-yet-existing path next to ``target`` with ``prefix``."""
    while True:
        candidate = target.with_name(
            f"{prefix}{target.name}-{secrets.token_hex(_RETIRED_TOKEN_BYTES)}"
        )
        if not candidate.exists() and not candidate.is_symlink():
            return candidate


def _publish(new: Path, target: Path) -> None:
    """Put ``new`` at ``target`` so that ``target`` is never half-written.

    ``os.replace`` is atomic on the same filesystem, which is why the
    staging directory lives inside the cache root. An existing ``target``
    is renamed aside first (``os.replace`` will not overwrite a non-empty
    directory) and put back if the swap fails, so a failed refresh leaves
    the previous bundle exactly where it was.

    Raises:
        SkillFetchError: Any rename failed.
    """
    retired: Path | None = None
    try:
        if target.exists() or target.is_symlink():
            retired = _unique_sibling(target, _RETIRED_PREFIX)
            os.rename(target, retired)
        try:
            os.replace(new, target)
        except OSError:
            if retired is not None and not target.exists():
                os.rename(retired, target)
                retired = None
            raise
    except (OSError, shutil.Error) as exc:
        raise SkillFetchError(
            f"Could not publish the {SKILLS_REPO} bundle to {target}: {exc}"
        )
    if retired is not None:
        shutil.rmtree(retired, ignore_errors=True)


# ---------------------------------------------------------------------------
# Download + extraction
# ---------------------------------------------------------------------------


def _download_and_extract(
    ref: str, target: Path, ref_source: str | None = None
) -> None:
    """Download the bundle for ``ref`` and atomically replace ``target``.

    Everything happens in a unique ``.tmp-*`` sibling of ``target`` so two
    concurrent fetches cannot see each other's partial work, and the
    staging directory is removed whether or not the fetch succeeds.
    """
    url = bundle_url(ref)
    cache_root = target.parent
    try:
        cache_root.mkdir(parents=True, exist_ok=True)
        _prune_stale_dirs(cache_root)
        staging = Path(tempfile.mkdtemp(prefix=_STAGING_PREFIX, dir=cache_root))
    except OSError as exc:
        raise SkillFetchError(f"Could not prepare skills cache {cache_root}: {exc}")

    try:
        archive = staging / "bundle.tar.gz"
        _download(url, ref, archive, ref_source)

        unpacked = staging / "unpacked"
        try:
            unpacked.mkdir()
        except OSError as exc:
            raise SkillFetchError(f"Could not prepare {unpacked}: {exc}")
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
        _publish(roots[0], target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _user_agent() -> str:
    """``deepctl/<version>``, so GitHub can tell this client apart."""
    try:
        version = importlib.metadata.version("deepctl")
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"
    return f"deepctl/{version}"


def _download(url: str, ref: str, dest: Path, ref_source: str | None = None) -> None:
    """Fetch ``url`` into ``dest``, mapping every failure to SkillFetchError."""
    request = urllib.request.Request(url, headers={"User-Agent": _user_agent()})
    try:
        with urllib.request.urlopen(request, timeout=_DOWNLOAD_TIMEOUT) as resp:
            _copy_limited(resp, dest)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # Its own class: retrying a missing ref fails the same way, so
            # callers advise choosing another ref, not waiting for the network.
            raise SkillRefNotFoundError(ref, url, ref_source)
        if exc.code in (403, 429):
            raise SkillFetchError(
                f"Could not download {SKILLS_REPO}@{ref}: HTTP {exc.code} from "
                f"{url}. GitHub answers {exc.code} when it is rate limiting; "
                "wait a few minutes and retry."
            )
        raise SkillFetchError(
            f"Could not download {SKILLS_REPO}@{ref}: HTTP {exc.code} from {url}."
        )
    except (
        urllib.error.URLError,
        http.client.HTTPException,
        OSError,
        ValueError,
    ) as exc:
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
    Python version this CLI supports, so the checks are explicit. The same
    pass enforces the expansion caps, because the compressed size says
    little about what a gzip stream unpacks to.
    """
    root = dest.resolve()
    members = 0
    extracted_bytes = 0
    seen: set[str] = set()
    for member in tar:
        members += 1
        if members > _MAX_MEMBERS:
            raise SkillFetchError(
                f"Refusing to unpack the {SKILLS_REPO} bundle: it has more "
                f"than {_MAX_MEMBERS} members."
            )
        name = member.name
        if len(name.encode("utf-8", "surrogateescape")) > _MAX_MEMBER_NAME_BYTES:
            raise SkillFetchError(
                f"Refusing to unpack the {SKILLS_REPO} bundle: a member name "
                f"is longer than {_MAX_MEMBER_NAME_BYTES} bytes."
            )
        if not (member.isfile() or member.isdir()):
            # Symlinks, hardlinks and devices have no place in a skill
            # bundle and are how tar extraction turns into arbitrary writes.
            continue
        if name.startswith("/") or ".." in Path(name).parts:
            raise SkillFetchError(
                f"Refusing to unpack {name!r}: it escapes the bundle root."
            )
        resolved = (root / name).resolve()
        if resolved != root and root not in resolved.parents:
            raise SkillFetchError(
                f"Refusing to unpack {name!r}: it escapes the bundle root."
            )
        key = posixpath.normpath(name)
        if key in seen:
            raise SkillFetchError(
                f"Refusing to unpack the {SKILLS_REPO} bundle: it lists "
                f"{name!r} more than once."
            )
        seen.add(key)
        if member.isfile():
            extracted_bytes += member.size
            if extracted_bytes > _MAX_EXTRACTED_BYTES:
                raise SkillFetchError(
                    f"Refusing to unpack the {SKILLS_REPO} bundle: it expands "
                    f"to more than {_MAX_EXTRACTED_BYTES} bytes."
                )
        member.mode = 0o755 if member.isdir() else 0o644
        yield member
