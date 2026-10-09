"""Unit tests for deepctl_core.skill_bundle. No test touches the network."""

from __future__ import annotations

import functools
import hashlib
import io
import json
import os
import shutil
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

    @pytest.mark.parametrize("via", ["env", "argument"])
    def test_user_ref_equal_to_the_pin_is_still_hash_checked(
        self, via: str, cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ref = None
        if via == "env":
            monkeypatch.setenv(REF_ENV_VAR, DEFAULT_SKILLS_COMMIT)
        else:
            ref = DEFAULT_SKILLS_COMMIT
        tampered = _serve(_tarball(_members(version="evil")))
        with pytest.raises(SkillFetchError, match="sha256"):
            fetch_skill_bundle(ref, cache_dir=cache, download=tampered)
        assert tampered.calls == [bundle_url(DEFAULT_SKILLS_COMMIT)]
        assert not cache.exists()

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
        ["main", "v1.2.3", "release/1.x", "a_b-c", DEFAULT_SKILLS_COMMIT, "a" * 100],
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
            "a/./b",
            "a/.b",
            "a/",
            "a.",
            "a b",
            "a\\b",
            "C:x",
            "a?b",
            "a%2Fb",
            "a~1",
            "é",
            "a" * 101,
        ],
    )
    def test_bad_refs(self, ref: str) -> None:
        with pytest.raises(SkillRefInvalidError):
            validate_ref(ref)

    @pytest.mark.parametrize("ref", ["v1.7.", "release/"])
    def test_trailing_dot_or_slash_is_named_in_the_error(self, ref: str) -> None:
        with pytest.raises(SkillRefInvalidError, match=r"or end in '\.' or '/'"):
            validate_ref(ref)

    def test_cache_name_byte_cap(self) -> None:
        # Each '/' spells as '%2F', so a slash-heavy ref under the 100-character
        # cap still trips the 120-byte cache name cap.
        slashy = "a/" * 30 + "a"
        assert len(slashy) <= 100
        assert len(("ref-" + slashy.replace("/", "%2F")).encode()) == 125
        with pytest.raises(SkillRefInvalidError, match="120 bytes"):
            validate_ref(slashy)
        plain = "a" * 100
        assert validate_ref(plain) == plain
        assert len(f"pinned-{DEFAULT_SKILLS_COMMIT}") == 47
        assert validate_ref(DEFAULT_SKILLS_COMMIT) == DEFAULT_SKILLS_COMMIT

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

    @pytest.mark.parametrize("text", ["no route", "no route."])
    def test_network_error(self, text: str) -> None:
        def fail(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(text, request=request)

        with pytest.raises(SkillFetchError) as info:
            skill_bundle._download(
                "https://x.test/a", transport=httpx.MockTransport(fail)
            )
        # One final period, even when httpx's own text already ends in one.
        assert str(info.value) == "Could not download https://x.test/a: no route."

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
    # Short components the OS accepts, but over the module's 512-byte cap.
    "long-name": (
        _with([(f"{TOP}/" + "a/" * 300 + "x", b"x")]),
        "member name that is too long",
    ),
    "reserved-con": (_with([(f"{TOP}/CON", b"x")]), UNSAFE),
    "reserved-ext": (_with([(f"{TOP}/skills/nul.txt", b"x")]), UNSAFE),
    "reserved-superscript": (_with([(f"{TOP}/COM\u00b9", b"x")]), UNSAFE),
    "reserved-conout": (_with([(f"{TOP}/CONOUT$.txt", b"x")]), UNSAFE),
    "trailing-dot": (_with([(f"{TOP}/a./x", b"x")]), UNSAFE),
    "trailing-space": (_with([(f"{TOP}/a ", b"x")]), UNSAFE),
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
# Windows device names. No such directory is in the bundle, so these must be
# refused by the name check alone, which also keeps the test Windows-safe.
BAD_NAMES += ["con", "NUL", "com1", "lpt9.x", "Aux.md"]
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
        with pytest.raises(SkillFetchError, match=r"not \./skills/<portable name>"):
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

    @pytest.mark.parametrize(
        ("restore_error", "expected", "match"),
        [
            (OSError("disk full"), SkillFetchError, "what was there is kept in"),
            # A second Ctrl-C during the restore must not delete the only copy.
            (KeyboardInterrupt(), KeyboardInterrupt, None),
        ],
    )
    def test_failed_restore_keeps_the_old_copy_in_staging(
        self,
        restore_error: BaseException,
        expected: type[BaseException],
        match: str | None,
        cache: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _good_user_cache(cache)
        real_replace = os.replace

        def flaky(src: object, dst: object) -> None:
            name = Path(str(src)).name
            if name == "new":
                raise OSError("disk full")
            if name == "previous":
                raise restore_error
            real_replace(src, dst)  # type: ignore[arg-type]

        monkeypatch.setattr(skill_bundle.os, "replace", flaky)
        data = _tarball(_members(version="2"))
        with pytest.raises(expected, match=match):
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

    @pytest.mark.parametrize("with_previous", [False, True])
    def test_concurrent_publish_uses_the_other_copy(
        self, with_previous: bool, cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        if with_previous:
            _good_user_cache(cache)  # So ours moves it into staging first.
        target = cache / f"ref-{USER_REF}"
        real_replace = os.replace

        def racing(src: object, dst: object) -> None:
            if Path(str(src)).name == "new":
                # Another process publishes a valid marked cache into the gap.
                shutil.copytree(str(src), target)
                (target / "skills" / "api" / "SKILL.md").write_text("# other\n")
            real_replace(src, dst)  # type: ignore[arg-type]

        monkeypatch.setattr(skill_bundle.os, "replace", racing)
        data = _tarball(_members(version="2"))
        skills = fetch_skill_bundle(USER_REF, cache_dir=cache, download=_serve(data))
        assert skills == [RepoSkill(n, target / "skills" / n) for n in NAMES]
        assert (target / "skills" / "api" / "SKILL.md").read_text() == "# other\n"
        assert [p.name for p in cache.iterdir()] == [target.name]

    def test_symlinked_pinned_cache_is_not_served(
        self, cache: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data = _tarball(_members())
        _pin(monkeypatch, data)
        elsewhere = tmp_path / "elsewhere"
        fetch_skill_bundle(cache_dir=elsewhere, download=_serve(data))
        real = elsewhere / f"pinned-{DEFAULT_SKILLS_COMMIT}"
        before = _snapshot(real)
        link = cache / real.name
        cache.mkdir()
        try:
            link.symlink_to(real, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks are not available here")
        download = _serve(data)
        with pytest.raises(SkillFetchError, match="not created by deepctl"):
            fetch_skill_bundle(cache_dir=cache, download=download)
        assert len(download.calls) == 1  # Not a cache hit.
        assert link.is_symlink()
        assert _snapshot(real) == before

    def test_failed_move_aside_keeps_the_cache(
        self, cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        before = _good_user_cache(cache)
        real_replace = os.replace

        def flaky(src: object, dst: object) -> None:
            if Path(str(dst)).name == "previous":
                raise OSError("busy")
            real_replace(src, dst)  # type: ignore[arg-type]

        monkeypatch.setattr(skill_bundle.os, "replace", flaky)
        data = _tarball(_members(version="2"))
        with pytest.raises(SkillFetchError, match="busy"):
            fetch_skill_bundle(USER_REF, cache_dir=cache, download=_serve(data))
        assert _snapshot(cache) == before

    def test_ctrl_c_on_the_final_rename_is_not_swallowed(
        self, cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = cache / f"ref-{USER_REF}"

        def racing(src: object, dst: object) -> None:
            # Another process publishes a valid marked cache, then Ctrl-C.
            shutil.copytree(str(src), target)
            raise KeyboardInterrupt

        monkeypatch.setattr(skill_bundle.os, "replace", racing)
        data = _tarball(_members(version="2"))
        with pytest.raises(KeyboardInterrupt):
            fetch_skill_bundle(USER_REF, cache_dir=cache, download=_serve(data))

    @pytest.mark.parametrize("restore_fails", [False, True])
    def test_unmarked_swap_in_after_the_check_is_put_back(
        self, restore_fails: bool, cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _good_user_cache(cache)
        target = cache / f"ref-{USER_REF}"
        real_cache = cache / "moved-away"
        real_replace = os.replace

        def swapped(src: object, dst: object) -> None:
            if Path(str(dst)).name == "previous":
                # Something replaces our cache after the ownership check.
                real_replace(target, real_cache)
                target.mkdir()
                (target / "notes.txt").write_text("not ours")
            elif restore_fails and Path(str(src)).name == "previous":
                raise OSError("busy")
            real_replace(src, dst)  # type: ignore[arg-type]

        monkeypatch.setattr(skill_bundle.os, "replace", swapped)
        data = _tarball(_members(version="2"))
        with pytest.raises(SkillFetchError, match="so it was not replaced"):
            fetch_skill_bundle(USER_REF, cache_dir=cache, download=_serve(data))
        assert (real_cache / "skills" / "api" / "SKILL.md").read_text() == "# api v1\n"
        staged = list(cache.glob(".tmp-*/previous/notes.txt"))
        if restore_fails:  # Not ours and not restored: kept in staging.
            assert not target.exists()
            assert [p.read_text() for p in staged] == ["not ours"]
        else:
            assert (target / "notes.txt").read_text() == "not ours"
            assert not (target / ".deepctl-skills-cache").exists()
            assert list(cache.glob(".tmp-*")) == []

    @pytest.mark.parametrize("kind", ["file", "dangling symlink", "live symlink"])
    def test_non_directory_swapped_in_after_the_check_is_kept_in_staging(
        self, kind: str, cache: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        if kind != "file":
            try:
                os.symlink(tmp_path / "probe-missing", tmp_path / "probe")
            except (OSError, NotImplementedError):
                pytest.skip("symlinks cannot be created here")
        _good_user_cache(cache)
        target = cache / f"ref-{USER_REF}"
        real_cache = tmp_path / "moved-away"
        # Relative, so Windows readlink returns it as written (no \\?\ prefix).
        link_to = ".." if kind == "live symlink" else "user-link-target-missing"
        real_replace = os.replace

        def swapped(src: object, dst: object) -> None:
            if Path(str(dst)).name == "previous":
                # A user file or symlink replaces our cache after the check.
                real_replace(target, real_cache)
                if kind == "file":
                    target.write_text("user file")
                else:
                    os.symlink(link_to, target, target_is_directory=link_to == "..")
            real_replace(src, dst)  # type: ignore[arg-type]

        monkeypatch.setattr(skill_bundle.os, "replace", swapped)
        data = _tarball(_members(version="2"))
        with pytest.raises(SkillFetchError, match="changed while publishing") as info:
            fetch_skill_bundle(USER_REF, cache_dir=cache, download=_serve(data))
        # Glob the staging dirs: Python 3.10 glob skips a dangling symlink.
        kept = [d / "previous" for d in cache.glob(".tmp-*")]
        assert len(kept) == 1
        assert str(kept[0]) in str(info.value)
        if kind == "file":
            assert not kept[0].is_symlink()
            assert kept[0].read_text() == "user file"
        else:
            assert kept[0].is_symlink()
            assert os.readlink(kept[0]) == link_to
        assert not os.path.lexists(target)
        assert kind != "dangling symlink" or not (cache / link_to).exists()
        assert (real_cache / "skills" / "api" / "SKILL.md").read_text() == "# api v1\n"

    def test_second_file_at_target_before_the_restore_is_not_overwritten(
        self, cache: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _good_user_cache(cache)
        target = cache / f"ref-{USER_REF}"
        real_cache = tmp_path / "moved-away"
        real_replace, real_is_ours = os.replace, skill_bundle._is_our_cache

        def swapped(src: object, dst: object) -> None:
            if Path(str(dst)).name == "previous":
                real_replace(target, real_cache)
                target.write_text("X")  # Swapped in after the check.
            real_replace(src, dst)  # type: ignore[arg-type]

        def is_ours(path: Path) -> bool:
            ours = real_is_ours(path)
            if path.name == "previous" and not os.path.lexists(target):
                target.write_text("Y")  # A second file appears before the restore.
            return ours

        monkeypatch.setattr(skill_bundle.os, "replace", swapped)
        monkeypatch.setattr(skill_bundle, "_is_our_cache", is_ours)
        data = _tarball(_members(version="2"))
        with pytest.raises(SkillFetchError, match="what was there is kept in"):
            fetch_skill_bundle(USER_REF, cache_dir=cache, download=_serve(data))
        assert target.read_text() == "Y"
        kept = list(cache.glob(".tmp-*/previous"))
        assert [p.read_text() for p in kept] == ["X"]
        assert (real_cache / "skills" / "api" / "SKILL.md").read_text() == "# api v1\n"

    def test_foreign_dir_kept_after_a_double_race_is_named(
        self, cache: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _good_user_cache(cache)
        target = cache / f"ref-{USER_REF}"
        real_cache = tmp_path / "moved-away"
        real_replace, real_is_ours = os.replace, skill_bundle._is_our_cache

        def swapped(src: object, dst: object) -> None:
            if Path(str(dst)).name == "previous":
                real_replace(target, real_cache)  # A user dir is swapped in.
                target.mkdir()
                (target / "notes.txt").write_text("not ours")
            real_replace(src, dst)  # type: ignore[arg-type]

        def is_ours(path: Path) -> bool:
            ours = real_is_ours(path)
            if path.name == "previous" and not os.path.lexists(target):
                # A marked cache lands at target first, so the restore fails.
                shutil.copytree(real_cache, target)
            return ours

        monkeypatch.setattr(skill_bundle.os, "replace", swapped)
        monkeypatch.setattr(skill_bundle, "_is_our_cache", is_ours)
        data = _tarball(_members(version="2"))
        with pytest.raises(SkillFetchError, match="changed while publishing") as info:
            fetch_skill_bundle(USER_REF, cache_dir=cache, download=_serve(data))
        kept = [d / "previous" for d in cache.glob(".tmp-*")]
        assert len(kept) == 1
        assert f"; what was there is kept in {kept[0]}." in str(info.value)
        assert (kept[0] / "notes.txt").read_text() == "not ours"
        assert (target / "skills" / "api" / "SKILL.md").read_text() == "# api v1\n"
