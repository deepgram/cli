"""Unit tests for the upstream skill bundle fetcher."""

import io
import json
import tarfile
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest
from deepctl_core.skill_bundle import (
    DEFAULT_SKILLS_REF,
    REF_ENV_VAR,
    SkillFetchError,
    bundle_url,
    fetch_skill_bundle,
    read_manifest_skills,
    resolve_skills_ref,
)

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


class TestReadManifestSkills:
    def test_returns_every_manifest_entry_in_order(self, tmp_path):
        skills = read_manifest_skills(_build_repo(tmp_path))
        assert [s.name for s in skills] == SKILL_NAMES
        assert len(skills) == 14

    def test_skill_paths_are_directories_with_an_entry_file(self, tmp_path):
        for skill in read_manifest_skills(_build_repo(tmp_path)):
            assert skill.path.is_dir()
            assert skill.entry_file.is_file()

    def test_missing_manifest(self, tmp_path):
        with pytest.raises(SkillFetchError, match="marketplace.json is missing"):
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
        with pytest.raises(SkillFetchError, match="'./skills/ghost'"):
            read_manifest_skills(tmp_path)

    def test_manifest_entry_without_a_skill_file(self, tmp_path):
        _build_repo(tmp_path, names=["api"])
        (tmp_path / "skills" / "api" / "SKILL.md").unlink()
        with pytest.raises(SkillFetchError, match="no SKILL.md"):
            read_manifest_skills(tmp_path)

    def test_duplicate_manifest_entries(self, tmp_path):
        _build_repo(tmp_path, names=["api"], manifest=_manifest(["api", "api"]))
        with pytest.raises(SkillFetchError, match="more than once"):
            read_manifest_skills(tmp_path)

    def test_traversing_manifest_entry_is_rejected(self, tmp_path):
        _build_repo(tmp_path, names=["api"], manifest=_manifest(["../../etc"]))
        with pytest.raises(SkillFetchError):
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
        assert DEFAULT_SKILLS_REF in opener.call_args[0][0]

    def test_second_call_uses_the_cache(self, tmp_path):
        payload = _tarball(_build_repo(tmp_path / "repo"))
        _, opener, cache = self._fetch(tmp_path, payload)
        assert opener.call_count == 1
        with patch("urllib.request.urlopen", side_effect=AssertionError) as second:
            skills = fetch_skill_bundle(cache_dir=cache)
        assert second.call_count == 0
        assert len(skills) == 14

    def test_network_failure_raises(self, tmp_path):
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Name or service not known"),
        ):
            with pytest.raises(SkillFetchError, match="Could not download"):
                fetch_skill_bundle(cache_dir=tmp_path / "cache")

    def test_unknown_ref_reports_a_404(self, tmp_path):
        err = urllib.error.HTTPError(
            "https://codeload.github.com/x", 404, "Not Found", {}, None
        )
        with patch("urllib.request.urlopen", side_effect=err):
            with pytest.raises(SkillFetchError, match="has no ref 'nope'"):
                fetch_skill_bundle("nope", cache_dir=tmp_path / "cache")

    def test_server_error_reports_the_status(self, tmp_path):
        err = urllib.error.HTTPError(
            "https://codeload.github.com/x", 503, "Unavailable", {}, None
        )
        with patch("urllib.request.urlopen", side_effect=err):
            with pytest.raises(SkillFetchError, match="HTTP 503"):
                fetch_skill_bundle(cache_dir=tmp_path / "cache")

    def test_corrupt_archive_raises(self, tmp_path):
        with pytest.raises(SkillFetchError, match="not a readable tar.gz"):
            self._fetch(tmp_path, b"this is not a tarball")

    def test_malformed_manifest_does_not_replace_a_good_cache(self, tmp_path):
        good = _tarball(_build_repo(tmp_path / "repo"))
        _, _, cache = self._fetch(tmp_path, good)

        bad_repo = _build_repo(tmp_path / "bad", manifest="{broken")
        with patch(
            "urllib.request.urlopen", return_value=_FakeResponse(_tarball(bad_repo))
        ):
            with pytest.raises(SkillFetchError, match="not valid JSON"):
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

    def test_symlink_members_are_skipped(self, tmp_path):
        """A skill bundle has no business shipping links."""
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
        assert not (cache / "deepgram-skills-v1.6.0" / "escape").exists()
