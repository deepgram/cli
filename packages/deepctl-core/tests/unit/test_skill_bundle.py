"""Unit tests for the upstream skill bundle fetcher."""

import copy
import http.client
import importlib.metadata
import io
import json
import os
import pickle
import sys
import tarfile
import time
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest
from deepctl_core import skill_bundle
from deepctl_core.skill_bundle import (
    DEFAULT_SKILLS_REF,
    PINNED_REF_SOURCE,
    REF_ENV_VAR,
    SkillFetchError,
    SkillRefInvalidError,
    SkillRefNotFoundError,
    bundle_url,
    fetch_skill_bundle,
    read_manifest_skills,
    resolve_skills_ref,
    validate_ref,
)
from deepctl_core.skill_generator import SKILL_ENTRY_FILE

SKILL_NAMES = [
    "speech-to-text",
    "text-to-speech",
    "voice-agent",
    "audio-intelligence",
    "text-intelligence",
    "browser-agent",
    "api",
    "docs",
    "starters",
    "recipes",
    "examples",
    "cli",
    "setup-mcp",
    "self-hosted",
]

# Mirrors the two upstream skills that ship a references/ subdirectory.
SKILLS_WITH_REFERENCES = {"api": ["listen.md", "speak.md"], "self-hosted": ["k8s.md"]}


def _manifest(names, plugin_name="deepgram"):
    return {
        "name": "deepgram-agent-skills",
        "plugins": [
            {
                "name": plugin_name,
                "source": "./",
                "skills": [f"./skills/{n}" for n in names],
            }
        ],
    }


def _build_repo(root: Path, names=None, manifest=None) -> Path:
    """Create a fake deepgram/skills checkout under ``root``."""
    names = SKILL_NAMES if names is None else names
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    payload = _manifest(names) if manifest is None else manifest
    (root / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps(payload) if not isinstance(payload, str) else payload
    )
    for name in names:
        skill_dir = root / "skills" / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Test skill {name}\n---\n\n# {name}\n"
        )
        for ref_file in SKILLS_WITH_REFERENCES.get(name, []):
            refs = skill_dir / "references"
            refs.mkdir(exist_ok=True)
            (refs / ref_file).write_text(f"# {name} / {ref_file}\n")
    return root


def _tarball(source: Path, top="skills-deepgram-skills-v1.6.0") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(source, arcname=top)
    return buf.getvalue()


def _raw_tarball(members) -> bytes:
    """Build a tar.gz from ``(name, payload)`` pairs; ``None`` is a directory."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, payload in members:
            info = tarfile.TarInfo(name)
            if payload is None:
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tar.addfile(info)
            else:
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


def _leftovers(cache: Path) -> list[str]:
    """Names of staging or retired directories still in the cache root."""
    return sorted(
        p.name for p in cache.iterdir() if p.name.startswith((".tmp-", ".old-"))
    )


class _FakeResponse(io.BytesIO):
    """Minimal stand-in for urlopen's context-managed response."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class TestResolveSkillsRef:
    def test_defaults_to_the_pinned_tag(self, monkeypatch):
        monkeypatch.delenv(REF_ENV_VAR, raising=False)
        assert resolve_skills_ref() == DEFAULT_SKILLS_REF
        assert DEFAULT_SKILLS_REF.startswith("deepgram-skills-v")

    def test_env_var_overrides_the_default(self, monkeypatch):
        monkeypatch.setenv(REF_ENV_VAR, "main")
        assert resolve_skills_ref() == "main"

    def test_explicit_ref_wins_over_env(self, monkeypatch):
        monkeypatch.setenv(REF_ENV_VAR, "main")
        assert resolve_skills_ref("my-branch") == "my-branch"

    def test_blank_env_var_falls_back(self, monkeypatch):
        monkeypatch.setenv(REF_ENV_VAR, "   ")
        assert resolve_skills_ref() == DEFAULT_SKILLS_REF

    def test_bundle_url_uses_the_ref(self):
        assert bundle_url("v1.2.3").endswith("/deepgram/skills/tar.gz/v1.2.3")

    def test_explicit_empty_ref_raises_instead_of_falling_back(self):
        """``--ref ""`` is a quoting slip, not a request for the default."""
        with pytest.raises(SkillFetchError, match="ref is empty"):
            resolve_skills_ref("")

    def test_invalid_env_ref_raises(self, monkeypatch):
        monkeypatch.setenv(REF_ENV_VAR, "../x")
        with pytest.raises(SkillFetchError, match=r"Invalid skills ref '\.\./x'"):
            resolve_skills_ref()

    @pytest.mark.parametrize("bad", ["bad ref", "../x", "a" * 300])
    def test_an_invalid_env_ref_names_the_variable_and_the_way_out(
        self, monkeypatch, bad
    ):
        """Nothing on the command line shows the variable is set."""
        monkeypatch.setenv(REF_ENV_VAR, bad)
        with pytest.raises(SkillRefInvalidError) as excinfo:
            resolve_skills_ref()
        exc = excinfo.value
        assert exc.ref_source == REF_ENV_VAR
        assert f"That ref came from {REF_ENV_VAR}." in str(exc)
        assert str(exc).endswith(
            "Set it to another ref, or unset it to use the pinned release."
        )
        assert exc.advice("dg skills install") == "Then run 'dg skills install'."

    def test_an_invalid_explicit_ref_keeps_its_message(self, monkeypatch):
        """`--ref` is on the command line already; nothing to add."""
        monkeypatch.setenv(REF_ENV_VAR, "main")
        with pytest.raises(SkillRefInvalidError) as excinfo:
            resolve_skills_ref("bad ref")
        assert str(excinfo.value) == (
            "Invalid skills ref 'bad ref': a ref may contain only letters, "
            "digits, '.', '_', '-' and '/', starting with a letter or digit, "
            "and may not "
            "contain '..' or an empty path segment."
        )
        assert REF_ENV_VAR not in str(excinfo.value)
        assert excinfo.value.advice("dg skills update") == (
            "Run 'dg skills update --ref <tag>' to choose another ref."
        )


class TestValidateRef:
    @pytest.mark.parametrize(
        "ref",
        [
            "main",
            "deepgram-skills-v1.7.0",
            "release/1.2",
            "user/feature_branch",
            "0123abcd",
            "v1.0.0-rc.1",
        ],
    )
    def test_accepts_plausible_refs(self, ref):
        assert validate_ref(ref) == ref

    @pytest.mark.parametrize(
        "ref",
        [
            "",
            "a b",
            "../x",
            "-x",
            "x\nHost: y",
            "a/../b",
            "a..b",
            "a//b",
            "trailing/",
            "x?y=1",
            "tag@{1}",
            ".hidden",
        ],
    )
    def test_rejects_unsafe_refs(self, ref):
        with pytest.raises(SkillFetchError):
            validate_ref(ref)

    def test_message_names_the_value_and_the_accepted_shape(self):
        with pytest.raises(SkillFetchError) as excinfo:
            validate_ref("x\nHost: y")
        message = str(excinfo.value)
        assert repr("x\nHost: y") in message
        assert "letters, digits" in message
        assert "'..'" in message

    @pytest.mark.parametrize("ref", [".foo", "_x"])
    def test_a_ref_not_starting_with_a_letter_or_digit_says_so(self, ref):
        """The old text said only "not starting with '-'", so these read as allowed."""
        with pytest.raises(SkillFetchError) as excinfo:
            validate_ref(ref)
        message = str(excinfo.value)
        assert repr(ref) in message
        assert "starting with a letter or digit" in message
        assert "not starting with '-'" not in message

    def test_a_ref_at_the_length_cap_is_accepted(self):
        ref = "a" * skill_bundle._MAX_REF_LENGTH
        # 255-byte filenames, less the '.old-' + '-<8 hex>' a refresh adds.
        assert skill_bundle._MAX_REF_LENGTH == 241
        assert validate_ref(ref) == ref

    def test_one_byte_over_the_cap_is_refused(self):
        ref = "a" * (skill_bundle._MAX_REF_LENGTH + 1)
        with pytest.raises(SkillFetchError, match="242 characters"):
            validate_ref(ref)

    def test_a_ref_over_the_length_cap_is_refused_with_the_limit_named(self):
        """The ref names a cache directory, and 255 is the filename limit."""
        ref = "a" * 256
        with pytest.raises(SkillFetchError) as excinfo:
            validate_ref(ref)
        message = str(excinfo.value)
        assert "256 characters" in message
        assert "241" in message
        assert "255-byte filename limit" in message
        # The whole 256-character value is not echoed back.
        assert ref not in message

    def test_fetch_with_an_overlong_ref_raises_before_touching_the_network(
        self, tmp_path
    ):
        with (
            patch("urllib.request.urlopen", side_effect=AssertionError) as opener,
            pytest.raises(SkillFetchError, match="longer than the 241"),
        ):
            fetch_skill_bundle("b" * 300, cache_dir=tmp_path / "cache")
        assert opener.call_count == 0

    def test_a_ref_whose_cache_name_overflows_the_cap_is_refused(self):
        """Under the character cap, but every '/' becomes '%2F' on disk."""
        ref = "a/" * 80 + "b"
        assert len(ref) == 161
        assert len(skill_bundle._cache_key(ref).encode()) == 321
        with pytest.raises(SkillFetchError) as excinfo:
            validate_ref(ref)
        message = str(excinfo.value)
        assert "321 bytes" in message
        assert "%2F" in message
        assert "241" in message
        assert ref not in message

    def test_a_slashed_ref_whose_cache_name_fits_is_accepted(self):
        ref = "a/" * 60 + "b"  # 4 * 60 + 1 = 241 bytes once encoded
        assert len(skill_bundle._cache_key(ref).encode()) == 241
        assert validate_ref(ref) == ref

    def test_a_slashed_ref_one_byte_over_the_cache_name_cap_is_refused(self):
        ref = "a/" * 60 + "bc"
        assert len(skill_bundle._cache_key(ref).encode()) == 242
        with pytest.raises(SkillFetchError, match="242 bytes"):
            validate_ref(ref)

    def test_fetch_with_an_overflowing_cache_name_never_touches_disk_or_network(
        self, tmp_path
    ):
        cache = tmp_path / "cache"
        with (
            patch("urllib.request.urlopen", side_effect=AssertionError) as opener,
            pytest.raises(SkillFetchError, match="cache directory name"),
        ):
            fetch_skill_bundle("a/" * 80 + "b", cache_dir=cache)
        assert opener.call_count == 0
        assert not cache.exists()

    def test_a_cache_check_the_filesystem_refuses_is_a_fetch_error(self, tmp_path):
        """ENAMETOOLONG and friends are not "missing"; they must not escape raw."""
        too_long = OSError(36, "File name too long")
        with (
            patch.object(Path, "is_file", side_effect=too_long),
            pytest.raises(SkillFetchError, match="File name too long"),
        ):
            skill_bundle._cache_is_complete(tmp_path / "x")

    def test_a_skill_dir_the_filesystem_refuses_is_a_fetch_error(self, tmp_path):
        root = _build_repo(tmp_path)
        denied = PermissionError(13, "Permission denied")
        with (
            patch.object(Path, "is_dir", side_effect=denied),
            pytest.raises(SkillFetchError, match="Permission denied") as excinfo,
        ):
            read_manifest_skills(root)
        assert excinfo.value.__cause__ is denied

    def test_fetch_with_empty_ref_raises_before_touching_the_network(self, tmp_path):
        with (
            patch("urllib.request.urlopen", side_effect=AssertionError) as opener,
            pytest.raises(SkillFetchError, match="ref is empty"),
        ):
            fetch_skill_bundle("", cache_dir=tmp_path / "cache")
        assert opener.call_count == 0

    def test_fetch_with_header_injecting_ref_raises(self, tmp_path):
        with (
            patch("urllib.request.urlopen", side_effect=AssertionError),
            pytest.raises(SkillFetchError, match="Invalid skills ref"),
        ):
            fetch_skill_bundle("x\nHost: y", cache_dir=tmp_path / "cache")


class TestBundleUrl:
    def test_percent_encodes_the_ref(self):
        """Defence in depth: even an unvalidated ref cannot break the URL."""
        assert bundle_url("a b").endswith("/tar.gz/a%20b")
        url = bundle_url("x\nHost: y")
        assert "\n" not in url
        assert url.endswith("/tar.gz/x%0AHost%3A%20y")

    def test_keeps_slashes_in_the_ref(self):
        assert bundle_url("release/1.2").endswith("/tar.gz/release/1.2")


class TestCacheKey:
    def test_plain_tag_keeps_its_name(self):
        assert skill_bundle._cache_key(DEFAULT_SKILLS_REF) == DEFAULT_SKILLS_REF

    def test_slash_and_underscore_refs_do_not_collide(self):
        assert skill_bundle._cache_key("release/1") == "release%2F1"
        assert skill_bundle._cache_key("release/1") != skill_bundle._cache_key(
            "release_1"
        )

    def test_cache_key_never_looks_like_a_staging_dir(self):
        # Staging dirs are ".tmp-*"; a valid ref cannot start with ".".
        for ref in ("tmp-x", "old-x", "main"):
            assert not skill_bundle._cache_key(validate_ref(ref)).startswith(".")


class TestReadManifestSkills:
    def test_returns_every_manifest_entry_in_order(self, tmp_path):
        skills = read_manifest_skills(_build_repo(tmp_path))
        assert [s.name for s in skills] == SKILL_NAMES
        assert len(skills) == 14

    def test_skill_paths_point_into_the_bundle_that_was_read(self, tmp_path):
        """read_manifest_skills() only returns folders that exist, so the
        interesting part is *where*: a caller copies from these paths, and
        one resolving outside the extracted bundle would copy the wrong
        tree. The entry file is named too, because that is what the
        installer and `status` look for.
        """
        root = _build_repo(tmp_path)
        for skill in read_manifest_skills(root):
            assert skill.path.parent == root / "skills"
            assert skill.path.name == skill.name
            # The name the installer and `status` look for, not a copy.
            assert (skill.path / SKILL_ENTRY_FILE).is_file()

    def test_missing_manifest(self, tmp_path):
        with pytest.raises(SkillFetchError, match=r"marketplace\.json is missing"):
            read_manifest_skills(tmp_path)

    def test_malformed_json(self, tmp_path):
        _build_repo(tmp_path, manifest="{not json")
        with pytest.raises(SkillFetchError, match="not valid JSON"):
            read_manifest_skills(tmp_path)

    def test_manifest_without_plugins(self, tmp_path):
        _build_repo(tmp_path, manifest={"name": "x"})
        with pytest.raises(SkillFetchError, match="no 'plugins' array"):
            read_manifest_skills(tmp_path)

    def test_manifest_without_the_deepgram_plugin(self, tmp_path):
        _build_repo(tmp_path, manifest=_manifest(SKILL_NAMES, plugin_name="other"))
        with pytest.raises(SkillFetchError, match="no plugin named 'deepgram'"):
            read_manifest_skills(tmp_path)

    def test_manifest_with_empty_skill_list(self, tmp_path):
        _build_repo(tmp_path, manifest=_manifest([]))
        with pytest.raises(SkillFetchError, match="lists no skills"):
            read_manifest_skills(tmp_path)

    def test_manifest_with_non_string_entry(self, tmp_path):
        payload = _manifest(["api"])
        payload["plugins"][0]["skills"] = [{"path": "./skills/api"}]
        _build_repo(tmp_path, names=["api"], manifest=payload)
        with pytest.raises(SkillFetchError, match="non-string skill entry"):
            read_manifest_skills(tmp_path)

    def test_manifest_entry_without_a_directory(self, tmp_path):
        """A manifest/disk mismatch must fail, never silently install a subset."""
        _build_repo(tmp_path, names=["api"], manifest=_manifest(["api", "ghost"]))
        with pytest.raises(SkillFetchError, match=r"'\./skills/ghost'"):
            read_manifest_skills(tmp_path)

    def test_manifest_entry_without_a_skill_file(self, tmp_path):
        _build_repo(tmp_path, names=["api"])
        (tmp_path / "skills" / "api" / "SKILL.md").unlink()
        with pytest.raises(SkillFetchError, match=r"no SKILL\.md"):
            read_manifest_skills(tmp_path)

    def test_duplicate_manifest_entries(self, tmp_path):
        _build_repo(tmp_path, names=["api"], manifest=_manifest(["api", "api"]))
        with pytest.raises(SkillFetchError, match="more than once"):
            read_manifest_skills(tmp_path)

    def test_traversing_manifest_entry_is_rejected(self, tmp_path):
        # Matched on the message: without the traversal guard the entry
        # falls through to "that directory is not in the bundle", which
        # is also a SkillFetchError, so a bare raises() proves nothing.
        _build_repo(tmp_path, names=["api"], manifest=_manifest(["../../etc"]))
        with pytest.raises(SkillFetchError, match="escapes the bundle root"):
            read_manifest_skills(tmp_path)


class TestFetchSkillBundle:
    def _fetch(self, tmp_path, payload, **kwargs):
        cache = tmp_path / "cache"
        with patch(
            "urllib.request.urlopen", return_value=_FakeResponse(payload)
        ) as opener:
            skills = fetch_skill_bundle(cache_dir=cache, **kwargs)
        return skills, opener, cache

    def test_extracts_all_fourteen_skills(self, tmp_path):
        payload = _tarball(_build_repo(tmp_path / "repo"))
        skills, _, _ = self._fetch(tmp_path, payload)
        assert [s.name for s in skills] == SKILL_NAMES

    def test_preserves_reference_subdirectories(self, tmp_path):
        payload = _tarball(_build_repo(tmp_path / "repo"))
        skills, _, _ = self._fetch(tmp_path, payload)
        by_name = {s.name: s for s in skills}
        for name, files in SKILLS_WITH_REFERENCES.items():
            refs = by_name[name].path / "references"
            assert refs.is_dir(), f"{name} lost its references/ directory"
            assert sorted(p.name for p in refs.iterdir()) == sorted(files)

    def test_requests_the_pinned_tag_by_default(self, tmp_path, monkeypatch):
        monkeypatch.delenv(REF_ENV_VAR, raising=False)
        payload = _tarball(_build_repo(tmp_path / "repo"))
        _, opener, _ = self._fetch(tmp_path, payload)
        assert DEFAULT_SKILLS_REF in opener.call_args[0][0].full_url

    def test_sends_a_deepctl_user_agent(self, tmp_path):
        payload = _tarball(_build_repo(tmp_path / "repo"))
        with patch("importlib.metadata.version", return_value="9.9.9"):
            _, opener, _ = self._fetch(tmp_path, payload)
        request = opener.call_args[0][0]
        assert request.get_header("User-agent") == "deepctl/9.9.9"

    def test_user_agent_falls_back_when_deepctl_is_not_installed(self, tmp_path):
        payload = _tarball(_build_repo(tmp_path / "repo"))
        with patch(
            "importlib.metadata.version",
            side_effect=importlib.metadata.PackageNotFoundError("deepctl"),
        ):
            _, opener, _ = self._fetch(tmp_path, payload)
        assert opener.call_args[0][0].get_header("User-agent") == "deepctl/unknown"

    def test_slash_ref_is_cached_under_one_directory(self, tmp_path):
        payload = _tarball(_build_repo(tmp_path / "repo"))
        skills, opener, cache = self._fetch(tmp_path, payload, ref="release/1")
        assert len(skills) == 14
        assert opener.call_args[0][0].full_url.endswith("/tar.gz/release/1")
        assert (cache / "release%2F1" / ".claude-plugin" / "marketplace.json").is_file()
        assert not (cache / "release").exists()

    def test_second_call_uses_the_cache(self, tmp_path):
        payload = _tarball(_build_repo(tmp_path / "repo"))
        _, opener, cache = self._fetch(tmp_path, payload)
        assert opener.call_count == 1
        with patch("urllib.request.urlopen", side_effect=AssertionError) as second:
            skills = fetch_skill_bundle(cache_dir=cache)
        assert second.call_count == 0
        assert len(skills) == 14

    def test_truncated_cache_is_refetched(self, tmp_path):
        """A manifest with a missing skill folder is not a valid cache."""
        payload = _tarball(_build_repo(tmp_path / "repo"))
        _, _, cache = self._fetch(tmp_path, payload)
        target = cache / skill_bundle._cache_key(resolve_skills_ref())
        (target / "skills" / "api" / "SKILL.md").unlink()

        with patch(
            "urllib.request.urlopen", return_value=_FakeResponse(payload)
        ) as opener:
            skills = fetch_skill_bundle(cache_dir=cache)
        assert opener.call_count == 1
        assert len(skills) == 14
        assert (target / "skills" / "api" / "SKILL.md").is_file()

    def test_network_failure_raises(self, tmp_path):
        with (
            patch(
                "urllib.request.urlopen",
                side_effect=urllib.error.URLError("Name or service not known"),
            ),
            pytest.raises(SkillFetchError, match="Could not download"),
        ):
            fetch_skill_bundle(cache_dir=tmp_path / "cache")

    @pytest.mark.parametrize(
        "exc",
        [
            http.client.InvalidURL("URL can't contain control characters"),
            http.client.IncompleteRead(b"partial"),
            http.client.RemoteDisconnected("Remote end closed connection"),
        ],
        ids=["InvalidURL", "IncompleteRead", "RemoteDisconnected"],
    )
    def test_http_client_exceptions_become_fetch_errors(self, tmp_path, exc):
        with (
            patch("urllib.request.urlopen", side_effect=exc),
            pytest.raises(SkillFetchError, match="Could not download"),
        ):
            fetch_skill_bundle(cache_dir=tmp_path / "cache")

    @pytest.mark.parametrize(
        ("ref", "env", "ref_source", "expected_ref", "names"),
        [
            # No ref and no env var: the pinned tag, which none of the
            # other three sources would explain.
            (None, None, None, DEFAULT_SKILLS_REF, "deepctl's pinned default."),
            (None, "nope", None, "nope", "DEEPCTL_SKILLS_REF."),
            ("nope", None, "--ref", "nope", "--ref."),
        ],
    )
    def test_a_404_raises_ref_not_found_naming_where_the_ref_came_from(
        self, tmp_path, monkeypatch, ref, env, ref_source, expected_ref, names
    ):
        if env is None:
            monkeypatch.delenv(REF_ENV_VAR, raising=False)
        else:
            monkeypatch.setenv(REF_ENV_VAR, env)
        err = urllib.error.HTTPError(
            "https://codeload.github.com/x", 404, "Not Found", {}, None
        )
        with (
            patch("urllib.request.urlopen", side_effect=err),
            pytest.raises(SkillRefNotFoundError) as excinfo,
        ):
            fetch_skill_bundle(ref, cache_dir=tmp_path / "cache", ref_source=ref_source)
        exc = excinfo.value
        # Still a SkillFetchError, so every existing catch keeps working.
        assert isinstance(exc, SkillFetchError)
        assert exc.ref == expected_ref
        message = str(exc)
        assert f"has no ref {expected_ref!r}" in message
        assert message.endswith(f"Check the ref name, which came from {names}")

    def test_a_404_with_no_known_source_lists_all_four(self, tmp_path):
        err = urllib.error.HTTPError(
            "https://codeload.github.com/x", 404, "Not Found", {}, None
        )
        with (
            patch("urllib.request.urlopen", side_effect=err),
            pytest.raises(SkillRefNotFoundError) as excinfo,
        ):
            fetch_skill_bundle("nope", cache_dir=tmp_path / "cache")
        assert excinfo.value.ref_source is None
        assert str(excinfo.value).endswith(
            "Check the ref name, which came from --ref, DEEPCTL_SKILLS_REF, "
            "the last install's record or deepctl's pinned default."
        )

    def test_ref_not_found_advice_chooses_another_ref_rather_than_a_retry(self):
        recorded = SkillRefNotFoundError("gone", "u", "the last install's record")
        pinned = SkillRefNotFoundError("gone", "u", PINNED_REF_SOURCE)
        from_env = SkillRefNotFoundError("gone", "u", REF_ENV_VAR)
        for exc in (recorded, pinned):
            assert exc.advice("dg skills update") == (
                "Run 'dg skills update --ref <tag>' to choose another ref."
            )
        assert from_env.advice("dg skills install") == (
            "Set DEEPCTL_SKILLS_REF to another ref, or unset it, then run "
            "'dg skills install'."
        )

    def test_other_http_errors_are_not_ref_not_found(self, tmp_path):
        err = urllib.error.HTTPError(
            "https://codeload.github.com/x", 500, "Server Error", {}, None
        )
        with (
            patch("urllib.request.urlopen", side_effect=err),
            pytest.raises(SkillFetchError) as excinfo,
        ):
            fetch_skill_bundle("nope", cache_dir=tmp_path / "cache")
        assert not isinstance(excinfo.value, SkillRefNotFoundError)

    def test_forbidden_hints_at_rate_limiting(self, tmp_path):
        err = urllib.error.HTTPError(
            "https://codeload.github.com/x", 403, "Forbidden", {}, None
        )
        with (
            patch("urllib.request.urlopen", side_effect=err),
            pytest.raises(SkillFetchError, match=r"HTTP 403.*rate limiting"),
        ):
            fetch_skill_bundle(cache_dir=tmp_path / "cache")

    def test_too_many_requests_hints_at_rate_limiting(self, tmp_path):
        """429 is GitHub's other rate-limit answer; same hint as 403."""
        err = urllib.error.HTTPError(
            "https://codeload.github.com/x", 429, "Too Many Requests", {}, None
        )
        with (
            patch("urllib.request.urlopen", side_effect=err),
            pytest.raises(SkillFetchError, match=r"HTTP 429.*rate limiting"),
        ):
            fetch_skill_bundle(cache_dir=tmp_path / "cache")

    def test_server_error_reports_the_status(self, tmp_path):
        err = urllib.error.HTTPError(
            "https://codeload.github.com/x", 503, "Unavailable", {}, None
        )
        with (
            patch("urllib.request.urlopen", side_effect=err),
            pytest.raises(SkillFetchError, match="HTTP 503"),
        ):
            fetch_skill_bundle(cache_dir=tmp_path / "cache")

    def test_corrupt_archive_raises(self, tmp_path):
        with pytest.raises(SkillFetchError, match=r"not a readable tar\.gz"):
            self._fetch(tmp_path, b"this is not a tarball")

    def test_malformed_manifest_does_not_replace_a_good_cache(self, tmp_path):
        good = _tarball(_build_repo(tmp_path / "repo"))
        _, _, cache = self._fetch(tmp_path, good)

        bad_repo = _build_repo(tmp_path / "bad", manifest="{broken")
        with (
            patch(
                "urllib.request.urlopen", return_value=_FakeResponse(_tarball(bad_repo))
            ),
            pytest.raises(SkillFetchError, match="not valid JSON"),
        ):
            fetch_skill_bundle(cache_dir=cache, force=True)

        # The previously cached, valid bundle survived the failed refresh.
        assert len(fetch_skill_bundle(cache_dir=cache)) == 14

    def test_absolute_member_is_rejected(self, tmp_path):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            info = tarfile.TarInfo("/etc/passwd")
            info.size = 3
            tar.addfile(info, io.BytesIO(b"bad"))
        with pytest.raises(SkillFetchError, match="escapes the bundle root"):
            self._fetch(tmp_path, buf.getvalue())

    def test_symlink_members_are_skipped(self, tmp_path, monkeypatch):
        """A skill bundle has no business shipping links."""
        monkeypatch.delenv(REF_ENV_VAR, raising=False)
        repo = _build_repo(tmp_path / "repo")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            tar.add(repo, arcname="top")
            link = tarfile.TarInfo("top/escape")
            link.type = tarfile.SYMTYPE
            link.linkname = "/etc/passwd"
            tar.addfile(link)
        skills, _, cache = self._fetch(tmp_path, buf.getvalue())
        assert len(skills) == 14
        target = cache / DEFAULT_SKILLS_REF
        assert (target / "skills" / "api" / "SKILL.md").is_file()
        assert not (target / "escape").exists()


class TestExtractionCaps:
    def _fetch(self, tmp_path, payload):
        with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
            return fetch_skill_bundle(cache_dir=tmp_path / "cache")

    def test_limits_are_what_the_review_asked_for(self):
        assert skill_bundle._MAX_BUNDLE_BYTES == 64 * 1024 * 1024
        assert skill_bundle._MAX_EXTRACTED_BYTES == 256 * 1024 * 1024
        assert skill_bundle._MAX_MEMBERS == 10_000
        assert skill_bundle._MAX_MEMBER_NAME_BYTES == 512

    def test_too_many_members(self, tmp_path, monkeypatch):
        monkeypatch.setattr(skill_bundle, "_MAX_MEMBERS", 8)
        members = [("top", None)] + [(f"top/f{i}", b"x") for i in range(8)]
        with pytest.raises(SkillFetchError, match="more than 8 members"):
            self._fetch(tmp_path, _raw_tarball(members))

    def test_member_count_at_the_limit_is_fine(self, tmp_path, monkeypatch):
        monkeypatch.setattr(skill_bundle, "_MAX_MEMBERS", 40)
        payload = _tarball(_build_repo(tmp_path / "repo", names=["api"]))
        assert [s.name for s in self._fetch(tmp_path, payload)] == ["api"]

    def test_too_many_uncompressed_bytes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(skill_bundle, "_MAX_EXTRACTED_BYTES", 1000)
        # Each file is under the cap on its own; the total is not.
        members = [("top", None), ("top/a", b"a" * 600), ("top/b", b"b" * 600)]
        with pytest.raises(SkillFetchError, match="more than 1000 bytes"):
            self._fetch(tmp_path, _raw_tarball(members))

    def test_member_name_too_long(self, tmp_path):
        long_name = "top/" + "n" * 600
        members = [("top", None), (long_name, b"x")]
        with pytest.raises(SkillFetchError, match="longer than 512 bytes"):
            self._fetch(tmp_path, _raw_tarball(members))

    def test_duplicate_member_names(self, tmp_path):
        members = [("top", None), ("top/a", b"one"), ("top/a", b"two")]
        with pytest.raises(SkillFetchError, match="'top/a' more than once"):
            self._fetch(tmp_path, _raw_tarball(members))

    def test_a_rejected_bundle_leaves_no_staging_dir(self, tmp_path):
        members = [("top", None), ("top/a", b"one"), ("top/a", b"two")]
        with pytest.raises(SkillFetchError):
            self._fetch(tmp_path, _raw_tarball(members))
        assert _leftovers(tmp_path / "cache") == []


class TestAtomicPublish:
    def _fetch(self, cache, payload, **kwargs):
        with patch(
            "urllib.request.urlopen", return_value=_FakeResponse(payload)
        ) as opener:
            skills = fetch_skill_bundle(cache_dir=cache, **kwargs)
        return skills, opener

    def test_successful_fetch_leaves_only_the_bundle(self, tmp_path):
        cache = tmp_path / "cache"
        self._fetch(cache, _tarball(_build_repo(tmp_path / "repo")))
        assert [p.name for p in cache.iterdir()] == [
            skill_bundle._cache_key(resolve_skills_ref())
        ]

    def test_forced_refresh_replaces_the_bundle(self, tmp_path):
        cache = tmp_path / "cache"
        self._fetch(cache, _tarball(_build_repo(tmp_path / "v1")))
        new = _tarball(_build_repo(tmp_path / "v2", names=["api", "docs"]))
        skills, _ = self._fetch(cache, new, force=True)
        assert [s.name for s in skills] == ["api", "docs"]
        assert _leftovers(cache) == []
        assert not (skills[0].path.parent / "cli").exists()

    @pytest.mark.parametrize(
        "ref",
        [
            "a" * skill_bundle._MAX_REF_LENGTH,
            "a/" * 60 + "b",
        ],
        ids=["plain", "slashed"],
    )
    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="a 255-byte component overflows MAX_PATH under the Windows temp dir",
    )
    def test_a_ref_at_the_cap_survives_repeated_forced_refreshes(self, tmp_path, ref):
        """The second publish renames the first aside as .old-<key>-<hex>.

        A cap that ignored those 14 bytes let a ref install once and then
        fail every refresh with ENAMETOOLONG.
        """
        cache = tmp_path / "c"
        payload = _tarball(_build_repo(tmp_path / "repo"))
        key = skill_bundle._cache_key(ref)
        assert len(key.encode()) == skill_bundle._MAX_REF_LENGTH
        retired = (
            skill_bundle._unique_sibling(cache / key, skill_bundle._RETIRED_PREFIX)
        ).name
        assert len(retired.encode()) == 255
        for _ in range(3):
            skills, _ = self._fetch(cache, payload, ref=ref, force=True)
            assert [s.name for s in skills] == SKILL_NAMES
        assert _leftovers(cache) == []
        assert [p.name for p in cache.iterdir()] == [key]

    def test_failed_publish_keeps_the_old_cache_intact(self, tmp_path):
        cache = tmp_path / "cache"
        self._fetch(cache, _tarball(_build_repo(tmp_path / "v1")))
        new = _tarball(_build_repo(tmp_path / "v2", names=["api", "docs"]))

        with (
            patch("os.replace", side_effect=PermissionError("denied")),
            pytest.raises(SkillFetchError, match="Could not publish"),
        ):
            self._fetch(cache, new, force=True)

        assert _leftovers(cache) == []
        with patch("urllib.request.urlopen", side_effect=AssertionError):
            skills = fetch_skill_bundle(cache_dir=cache)
        assert [s.name for s in skills] == SKILL_NAMES

    def test_failed_first_publish_leaves_an_empty_cache(self, tmp_path):
        cache = tmp_path / "cache"
        with (
            patch("os.replace", side_effect=PermissionError("denied")),
            pytest.raises(SkillFetchError, match="Could not publish"),
        ):
            self._fetch(cache, _tarball(_build_repo(tmp_path / "repo")))
        assert list(cache.iterdir()) == []

    def test_download_failure_leaves_no_staging_dir(self, tmp_path):
        cache = tmp_path / "cache"
        with (
            patch("urllib.request.urlopen", side_effect=urllib.error.URLError("x")),
            pytest.raises(SkillFetchError),
        ):
            fetch_skill_bundle(cache_dir=cache)
        assert list(cache.iterdir()) == []

    def test_stale_staging_and_retired_dirs_are_pruned(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        two_hours_ago = time.time() - 2 * 60 * 60
        for name in (".tmp-stale", ".old-stale"):
            (cache / name).mkdir()
            (cache / name / "junk").write_text("x")
            os.utime(cache / name, (two_hours_ago, two_hours_ago))
        (cache / ".tmp-fresh").mkdir()
        (cache / "unrelated").mkdir()
        os.utime(cache / "unrelated", (two_hours_ago, two_hours_ago))

        self._fetch(cache, _tarball(_build_repo(tmp_path / "repo")))

        names = {p.name for p in cache.iterdir()}
        assert ".tmp-stale" not in names
        assert ".old-stale" not in names
        assert {".tmp-fresh", "unrelated"} <= names

    def test_pruning_tolerates_a_missing_root_and_non_directories(self, tmp_path):
        skill_bundle._prune_stale_dirs(tmp_path / "does-not-exist")
        cache = tmp_path / "cache"
        cache.mkdir()
        stale_file = cache / ".tmp-not-a-dir"
        stale_file.write_text("x")
        two_hours_ago = time.time() - 2 * 60 * 60
        os.utime(stale_file, (two_hours_ago, two_hours_ago))
        skill_bundle._prune_stale_dirs(cache)
        assert stale_file.is_file()


class TestSkillRefNotFoundErrorRoundTrips:
    """Its __init__ takes three arguments, but BaseException rebuilds a
    copy or an unpickled error from ``args``, which held only the message."""

    @pytest.mark.parametrize("ref_source", [None, "--ref", REF_ENV_VAR])
    @pytest.mark.parametrize(
        "clone",
        [lambda e: pickle.loads(pickle.dumps(e)), copy.copy, copy.deepcopy],
        ids=["pickle", "copy", "deepcopy"],
    )
    def test_ref_not_found_error_survives_pickle_and_copy(self, clone, ref_source):
        exc = SkillRefNotFoundError("no-such-ref-zz9", "https://x/y", ref_source)

        again = clone(exc)

        assert type(again) is SkillRefNotFoundError
        assert again.ref == "no-such-ref-zz9"
        assert again.url == "https://x/y"
        assert again.ref_source == ref_source
        assert str(again) == str(exc)

    @pytest.mark.parametrize(
        "exc",
        [
            SkillRefNotFoundError("no-such-ref-zz9", "https://x/y", REF_ENV_VAR),
            SkillRefInvalidError("bad ref", "Invalid skills ref 'bad ref'.", None),
            SkillRefInvalidError(
                "bad ref", "Invalid skills ref 'bad ref'.", REF_ENV_VAR
            ),
        ],
        ids=["not-found", "invalid", "invalid-from-env"],
    )
    @pytest.mark.parametrize(
        "clone",
        [lambda e: pickle.loads(pickle.dumps(e)), copy.copy, copy.deepcopy],
        ids=["pickle", "copy", "deepcopy"],
    )
    def test_an_attribute_set_later_survives(self, clone, exc):
        exc.retried = 2

        again = clone(exc)

        assert type(again) is type(exc)
        assert again.retried == 2
        assert str(again) == str(exc)
        assert again.ref_source == exc.ref_source

    @pytest.mark.skipif(sys.version_info < (3, 11), reason="add_note is 3.11+")
    @pytest.mark.parametrize(
        "clone",
        [lambda e: pickle.loads(pickle.dumps(e)), copy.copy, copy.deepcopy],
        ids=["pickle", "copy", "deepcopy"],
    )
    def test_a_note_survives(self, clone):
        for exc in (
            SkillRefNotFoundError("gone", "https://x/y", None),
            SkillRefInvalidError("bad ref", "Invalid.", REF_ENV_VAR),
        ):
            exc.add_note("while installing for Claude Code")

            again = clone(exc)

            assert again.__notes__ == ["while installing for Claude Code"]
