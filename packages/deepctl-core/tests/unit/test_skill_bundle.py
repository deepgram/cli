"""Unit tests for deepctl_core.skill_bundle. No test touches the network."""

from __future__ import annotations

import functools
import hashlib
import io
import json
import os
import tarfile
from pathlib import Path

import httpx
import pytest
from deepctl_core import skill_bundle
from deepctl_core.skill_bundle import (
    DEFAULT_SKILLS_COMMIT,
    REF_ENV_VAR,
    RepoSkill,
    SkillFetchError,
    SkillRefInvalidError,
    SkillRefNotFoundError,
    bundle_url,
    fetch_skill_bundle,
    read_manifest_skills,
    resolve_skills_ref,
    validate_ref,
)

TOP = f"skills-{DEFAULT_SKILLS_COMMIT}"
NAMES = ["speech-to-text", "api", "voice-agent"]
USER_REF = "my-branch"


def _manifest(entries: list[object]) -> bytes:
    other = {"name": "deepgram-python-sdk", "skills": [".agents/skills/x"]}
    deepgram = {"name": "deepgram", "source": "./", "skills": entries}
    return json.dumps({"plugins": [other, deepgram]}).encode()


def _members(names: list[str] = NAMES, *, version: str = "1") -> list[tuple]:
    """``(name, payload)`` pairs for a good bundle; ``None`` is a directory."""
    members: list[tuple] = [
        (TOP, None),
        (f"{TOP}/.claude-plugin", None),
        (
            f"{TOP}/.claude-plugin/marketplace.json",
            _manifest([f"./skills/{n}" for n in names]),
        ),
    ]
    for name in names:
        members += [
            (f"{TOP}/skills/{name}", None),
            (f"{TOP}/skills/{name}/SKILL.md", f"# {name} v{version}\n".encode()),
        ]
    return members


# Special member kinds, by the name a test uses for them.
KINDS = {
    "symlink": tarfile.SYMTYPE,
    "hardlink": tarfile.LNKTYPE,
    "fifo": tarfile.FIFOTYPE,
    "chardev": tarfile.CHRTYPE,
}


def _tarball(members: list[tuple]) -> bytes:
    """Build a tar.gz in memory. A payload is bytes, None (a dir) or a KINDS key."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, payload in members:
            info = tarfile.TarInfo(name)
            if isinstance(payload, bytes):
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
                continue
            info.type = tarfile.DIRTYPE if payload is None else KINDS[payload]
            info.linkname = "../../outside"
            tar.addfile(info)
    return buf.getvalue()


class _serve:
    """A fake download that returns ``data`` and records each URL."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        return self.data


def _pin(monkeypatch: pytest.MonkeyPatch, data: bytes) -> None:
    """Make ``data`` the archive the pinned default expects."""
    monkeypatch.setattr(
        skill_bundle, "DEFAULT_SKILLS_SHA256", hashlib.sha256(data).hexdigest()
    )


def _snapshot(root: Path) -> dict[str, bytes | None]:
    return {
        str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None)
        for p in sorted(root.rglob("*"))
    }


@pytest.fixture(autouse=True)
def _no_env_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(REF_ENV_VAR, raising=False)


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    return tmp_path / "cache"


def _good_user_cache(cache: Path) -> dict[str, bytes | None]:
    """Publish a good cache for USER_REF and return a snapshot of it."""
    fetch_skill_bundle(USER_REF, cache_dir=cache, download=_serve(_tarball(_members())))
    return _snapshot(cache)


# ---------------------------------------------------------------------------
# S3: the pinned default is hash-checked
# ---------------------------------------------------------------------------


class TestPinnedDefault:
    def test_default_is_the_full_commit_sha(self) -> None:
        assert resolve_skills_ref() == DEFAULT_SKILLS_COMMIT
        assert len(DEFAULT_SKILLS_COMMIT) == 40
        assert bundle_url(DEFAULT_SKILLS_COMMIT).endswith(
            f"/deepgram/skills/tar.gz/{DEFAULT_SKILLS_COMMIT}"
        )

    def test_tampered_archive_is_refused_and_leaves_nothing(
        self, cache: Path, tmp_path: Path
    ) -> None:
        # The real pinned hash, and an archive that is not the real bundle.
        with pytest.raises(SkillFetchError, match="sha256"):
            fetch_skill_bundle(cache_dir=cache, download=_serve(_tarball(_members())))
        assert not cache.exists()
        assert [p.name for p in tmp_path.iterdir()] == []

    def test_tampered_archive_keeps_the_previous_cache(
        self, cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        good = _tarball(_members())
        _pin(monkeypatch, good)
        fetch_skill_bundle(cache_dir=cache, download=_serve(good))
        before = _snapshot(cache)
        evil = _tarball(_members(version="evil"))
        with pytest.raises(SkillFetchError, match="sha256"):
            fetch_skill_bundle(cache_dir=cache, force=True, download=_serve(evil))
        assert _snapshot(cache) == before

    def test_matching_archive_is_published(
        self, cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data = _tarball(_members())
        _pin(monkeypatch, data)
        download = _serve(data)
        skills = fetch_skill_bundle(cache_dir=cache, download=download)
        target = cache / f"pinned-{DEFAULT_SKILLS_COMMIT}"
        assert skills == [RepoSkill(n, target / "skills" / n) for n in NAMES]
        assert (target / "skills" / "api" / "SKILL.md").read_text() == "# api v1\n"
        assert [p.name for p in cache.iterdir()] == [target.name]
        assert download.calls == [bundle_url(DEFAULT_SKILLS_COMMIT)]

    def test_cache_hit_skips_the_download(
        self, cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data = _tarball(_members())
        _pin(monkeypatch, data)
        fetch_skill_bundle(cache_dir=cache, download=_serve(data))
        download = _serve(b"")
        assert len(fetch_skill_bundle(cache_dir=cache, download=download)) == 3
        assert download.calls == []

    def test_damaged_cache_is_downloaded_again(
        self, cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data = _tarball(_members())
        _pin(monkeypatch, data)
        fetch_skill_bundle(cache_dir=cache, download=_serve(data))
        target = cache / f"pinned-{DEFAULT_SKILLS_COMMIT}"
        (target / "skills" / "api" / "SKILL.md").unlink()
        download = _serve(data)
        assert len(fetch_skill_bundle(cache_dir=cache, download=download)) == 3
        assert len(download.calls) == 1
        assert (target / "skills" / "api" / "SKILL.md").is_file()

    def test_user_ref_equal_to_the_pin_is_still_hash_checked(
        self, cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(REF_ENV_VAR, DEFAULT_SKILLS_COMMIT)
        with pytest.raises(SkillFetchError, match="sha256"):
            fetch_skill_bundle(cache_dir=cache, download=_serve(_tarball(_members())))

    def test_user_ref_is_not_hash_checked_and_always_downloads(
        self, cache: Path
    ) -> None:
        data = _tarball(_members())
        for _ in range(2):
            download = _serve(data)
            assert (
                len(fetch_skill_bundle(USER_REF, cache_dir=cache, download=download))
                == 3
            )
            assert download.calls == [bundle_url(USER_REF)]
        assert [p.name for p in cache.iterdir()] == [f"ref-{USER_REF}"]


# ---------------------------------------------------------------------------
# Ref validation
# ---------------------------------------------------------------------------


class TestRefs:
    @pytest.mark.parametrize(
        "ref",
        ["main", "v1.2.3", "release/1.x", "a_b-c", DEFAULT_SKILLS_COMMIT, "a" * 200],
    )
    def test_good_refs(self, ref: str) -> None:
        assert validate_ref(ref) == ref

    @pytest.mark.parametrize(
        "ref",
        [
            "",
            "-rf",
            "/etc",
            ".hidden",
            "a..b",
            "../x",
            "a//b",
            "a/",
            "a.",
            "a b",
            "a\\b",
            "C:x",
            "a?b",
            "a%2Fb",
            "a~1",
            "é",
            "a" * 201,
        ],
    )
    def test_bad_refs(self, ref: str) -> None:
        with pytest.raises(SkillRefInvalidError):
            validate_ref(ref)

    def test_cache_name_byte_cap(self) -> None:
        # 199 characters, under the length cap, but each '/' becomes '%2F'.
        ref = "a/" * 99 + "a"
        assert len(ref) <= 200
        with pytest.raises(SkillRefInvalidError, match="255 bytes"):
            validate_ref(ref)

    def test_env_var_is_used_and_validated(
        self, monkeypatch: pytest.MonkeyPatch, cache: Path
    ) -> None:
        monkeypatch.setenv(REF_ENV_VAR, "main")
        assert resolve_skills_ref() == "main"
        assert resolve_skills_ref("other") == "other"
        monkeypatch.setenv(REF_ENV_VAR, "  ")
        assert resolve_skills_ref() == DEFAULT_SKILLS_COMMIT
        monkeypatch.setenv(REF_ENV_VAR, "../evil")
        download = _serve(b"")
        with pytest.raises(SkillRefInvalidError):
            fetch_skill_bundle(cache_dir=cache, download=download)
        assert download.calls == []
        assert not cache.exists()

    def test_bad_argument_is_refused_before_download(self, cache: Path) -> None:
        with pytest.raises(SkillRefInvalidError):
            fetch_skill_bundle("", cache_dir=cache, download=_serve(b""))


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def _transport(status: int, body: bytes = b"") -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(status, content=body))


class TestDownload:
    def test_404_is_ref_not_found(self, cache: Path) -> None:
        download = functools.partial(skill_bundle._download, transport=_transport(404))
        with pytest.raises(SkillRefNotFoundError):
            fetch_skill_bundle("nope", cache_dir=cache, download=download)
        assert not cache.exists()

    def test_other_http_error(self) -> None:
        with pytest.raises(SkillFetchError, match="HTTP 500") as info:
            skill_bundle._download("https://x.test/a", transport=_transport(500))
        assert not isinstance(info.value, SkillRefNotFoundError)

    def test_network_error(self) -> None:
        def fail(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route", request=request)

        with pytest.raises(SkillFetchError, match="Could not download"):
            skill_bundle._download(
                "https://x.test/a", transport=httpx.MockTransport(fail)
            )

    def test_size_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(skill_bundle, "_MAX_BUNDLE_BYTES", 10)
        with pytest.raises(SkillFetchError, match="larger than"):
            skill_bundle._download(
                "https://x.test/a", transport=_transport(200, b"x" * 11)
            )

    def test_success(self) -> None:
        body = skill_bundle._download(
            "https://x.test/a", transport=_transport(200, b"ok")
        )
        assert body == b"ok"


# ---------------------------------------------------------------------------
# Tar safety
# ---------------------------------------------------------------------------


def _with(extra: list[tuple]) -> list[tuple]:
    return [*_members(), *extra]


UNSAFE, NOT_REGULAR = "not a safe path", "not a regular file"
BAD_TARS = {
    "traversal": (_with([("../x", b"x")]), UNSAFE),
    "nested-traversal": (_with([(f"{TOP}/a/../../x", b"x")]), UNSAFE),
    "absolute": (_with([("/etc/x", b"x")]), UNSAFE),
    "symlink": (_with([(f"{TOP}/link", "symlink")]), NOT_REGULAR),
    "hardlink": (_with([(f"{TOP}/hard", "hardlink")]), NOT_REGULAR),
    "fifo": (_with([(f"{TOP}/fifo", "fifo")]), NOT_REGULAR),
    "chardev": (_with([(f"{TOP}/dev", "chardev")]), NOT_REGULAR),
    "backslash": (_with([(f"{TOP}\\..\\x", b"x")]), UNSAFE),
    "drive-backslash": (_with([("C:\\x", b"x")]), UNSAFE),
    "drive-slash": (_with([("C:/x", b"x")]), UNSAFE),
    "drive-relative": (_with([(f"{TOP}/C:x", b"x")]), UNSAFE),
    "unc": (_with([("\\\\server\\x", b"x")]), UNSAFE),
    "two-top-dirs": (_with([("other/x", b"x")]), "single top-level"),
    "duplicate": (_with([(f"{TOP}/skills/api/SKILL.md", b"again")]), "exists"),
    "long-name": (_with([(f"{TOP}/" + "a" * 600, b"x")]), "too long"),
}


class TestTarSafety:
    @pytest.mark.parametrize("case", sorted(BAD_TARS))
    def test_bad_member_is_refused_and_keeps_the_cache(
        self, case: str, cache: Path, tmp_path: Path
    ) -> None:
        before = _good_user_cache(cache)
        members, match = BAD_TARS[case]
        with pytest.raises(SkillFetchError, match=match):
            fetch_skill_bundle(
                USER_REF, cache_dir=cache, download=_serve(_tarball(members))
            )
        assert _snapshot(cache) == before
        assert sorted(p.name for p in tmp_path.iterdir()) == ["cache"]

    def test_bad_member_is_refused_before_anything_is_written(
        self, cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        written: list[Path] = []
        real_open = Path.open

        def spy(self: Path, *args: object, **kwargs: object) -> object:
            if args and "x" in str(args[0]):
                written.append(self)
            return real_open(self, *args, **kwargs)  # type: ignore[call-overload]

        monkeypatch.setattr(Path, "open", spy)
        data = _tarball(BAD_TARS["symlink"][0])
        with pytest.raises(SkillFetchError):
            fetch_skill_bundle(USER_REF, cache_dir=cache, download=_serve(data))
        assert written == []

    @pytest.mark.parametrize(
        ("cap", "value", "match"),
        [("_MAX_MEMBERS", 5, "members"), ("_MAX_EXTRACTED_BYTES", 10, "unpacks")],
    )
    def test_caps(
        self,
        cap: str,
        value: int,
        match: str,
        cache: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        before = _good_user_cache(cache)
        monkeypatch.setattr(skill_bundle, cap, value)
        with pytest.raises(SkillFetchError, match=match):
            fetch_skill_bundle(
                USER_REF, cache_dir=cache, download=_serve(_tarball(_members()))
            )
        assert _snapshot(cache) == before

    def test_not_a_tarball(self, cache: Path) -> None:
        with pytest.raises(SkillFetchError, match="unpack"):
            fetch_skill_bundle(USER_REF, cache_dir=cache, download=_serve(b"nope"))
        assert [p.name for p in cache.iterdir()] == []


# ---------------------------------------------------------------------------
# B4: manifest entries become plain names before any path is built
# ---------------------------------------------------------------------------


BAD_NAMES = ["..", "a/b", "a\\b", "/abs", "C:\\x", ".", "C:x", "", "a.", "-a"]
BAD_ENTRIES = [
    "/abs",
    "./skills/../x",
    "./skills/a/b",
    "./skills/a\\b",
    "skills",
    "./skills/",
    "other/api",
    "C:\\skills\\api",
    "../skills/api",
    "./skills/api/",
    7,
    None,
]


class TestManifest:
    @pytest.mark.parametrize(
        "entry", [f"./skills/{n}" for n in BAD_NAMES] + BAD_NAMES + BAD_ENTRIES
    )
    def test_bad_entry_is_refused_before_any_path_is_built(
        self,
        entry: object,
        cache: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        before = _good_user_cache(cache)
        members = _with([])
        members[2] = (members[2][0], _manifest(["./skills/api", entry]))
        built: list[str] = []
        monkeypatch.setattr(
            skill_bundle, "RepoSkill", lambda name, path: built.append(name)
        )
        with pytest.raises(SkillFetchError, match=r"not \./skills/<name>"):
            fetch_skill_bundle(
                USER_REF, cache_dir=cache, download=_serve(_tarball(members))
            )
        assert built == []
        monkeypatch.undo()
        assert _snapshot(cache) == before
        assert sorted(p.name for p in tmp_path.iterdir()) == ["cache"]

    @pytest.mark.parametrize("entry", ["./skills/api", "skills/api"])
    def test_both_entry_forms_are_accepted(self, entry: str, tmp_path: Path) -> None:
        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin" / "marketplace.json").write_bytes(
            _manifest([entry])
        )
        (tmp_path / "skills" / "api").mkdir(parents=True)
        (tmp_path / "skills" / "api" / "SKILL.md").write_text("# api\n")
        skills = read_manifest_skills(tmp_path)
        assert skills == [RepoSkill("api", tmp_path / "skills" / "api")]

    @pytest.mark.parametrize(
        ("manifest", "match"),
        [
            (_manifest(["./skills/api", "./skills/API"]), "twice"),
            (_manifest(["./skills/missing"]), "SKILL.md"),
            (_manifest([]), "no single"),
            (json.dumps({"plugins": 3}).encode(), "no single"),
            (json.dumps([]).encode(), "no single"),
            (b"{not json", "Could not read"),
        ],
    )
    def test_bad_manifest(self, manifest: bytes, match: str, cache: Path) -> None:
        members = _members()
        members[2] = (members[2][0], manifest)
        with pytest.raises(SkillFetchError, match=match):
            fetch_skill_bundle(
                USER_REF, cache_dir=cache, download=_serve(_tarball(members))
            )

    def test_missing_manifest(self, cache: Path) -> None:
        members = [m for m in _members() if not m[0].endswith("marketplace.json")]
        with pytest.raises(SkillFetchError, match="manifest"):
            fetch_skill_bundle(
                USER_REF, cache_dir=cache, download=_serve(_tarball(members))
            )


# ---------------------------------------------------------------------------
# Atomic publish
# ---------------------------------------------------------------------------


class TestPublish:
    def test_success_replaces_the_previous_cache(self, cache: Path) -> None:
        _good_user_cache(cache)
        data = _tarball(_members(["api", "docs"], version="2"))
        skills = fetch_skill_bundle(USER_REF, cache_dir=cache, download=_serve(data))
        target = cache / f"ref-{USER_REF}"
        assert [s.name for s in skills] == ["api", "docs"]
        assert (target / "skills" / "api" / "SKILL.md").read_text() == "# api v2\n"
        assert not (target / "skills" / "voice-agent").exists()
        assert [p.name for p in cache.iterdir()] == [target.name]

    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (OSError("disk full"), SkillFetchError),
            (KeyboardInterrupt(), KeyboardInterrupt),
        ],
    )
    def test_failed_swap_restores_the_previous_cache(
        self,
        error: BaseException,
        expected: type[BaseException],
        cache: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        before = _good_user_cache(cache)
        real_replace = os.replace

        def flaky(src: object, dst: object) -> None:
            if Path(str(src)).name == "new":
                raise error
            real_replace(src, dst)  # type: ignore[arg-type]

        monkeypatch.setattr(skill_bundle.os, "replace", flaky)
        data = _tarball(_members(version="2"))
        with pytest.raises(expected):
            fetch_skill_bundle(USER_REF, cache_dir=cache, download=_serve(data))
        assert _snapshot(cache) == before

    def test_failed_restore_keeps_the_old_copy_in_staging(
        self, cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _good_user_cache(cache)
        real_replace = os.replace

        def flaky(src: object, dst: object) -> None:
            if Path(str(src)).name in ("new", "previous"):
                raise OSError("disk full")
            real_replace(src, dst)  # type: ignore[arg-type]

        monkeypatch.setattr(skill_bundle.os, "replace", flaky)
        data = _tarball(_members(version="2"))
        with pytest.raises(SkillFetchError, match="previous copy is in"):
            fetch_skill_bundle(USER_REF, cache_dir=cache, download=_serve(data))
        kept = list(cache.glob(".tmp-*/previous/skills/api/SKILL.md"))
        assert [p.read_text() for p in kept] == ["# api v1\n"]

    def test_directory_deepctl_did_not_create_is_left_alone(self, cache: Path) -> None:
        foreign = cache / f"ref-{USER_REF}"
        foreign.mkdir(parents=True)
        (foreign / "notes.txt").write_text("mine")
        with pytest.raises(SkillFetchError, match="not created by deepctl"):
            fetch_skill_bundle(
                USER_REF, cache_dir=cache, download=_serve(_tarball(_members()))
            )
        assert [p.name for p in cache.iterdir()] == [foreign.name]
        assert (foreign / "notes.txt").read_text() == "mine"

    def test_foreign_pinned_directory_is_not_a_cache_hit(
        self, cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        foreign = cache / f"pinned-{DEFAULT_SKILLS_COMMIT}"
        foreign.mkdir(parents=True)
        data = _tarball(_members())
        _pin(monkeypatch, data)
        download = _serve(data)
        with pytest.raises(SkillFetchError, match="not created by deepctl"):
            fetch_skill_bundle(cache_dir=cache, download=download)
        assert len(download.calls) == 1
        assert foreign.is_dir()
