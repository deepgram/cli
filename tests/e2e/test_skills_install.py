"""End-to-end install of the real deepgram/skills bundle.

Runs ``dg skills install`` as a subprocess against a throwaway ``HOME``,
then checks what actually landed on disk: every skill the upstream
manifest lists, as a folder, with its ``references/`` intact and
frontmatter a real YAML parser can read.

Opt-in, because it reaches the network. Set ``RUN_SKILLS_E2E=1``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_SKILLS_E2E") != "1",
    reason="RUN_SKILLS_E2E must be set to 1 (this test downloads deepgram/skills)",
)

# The tools deepctl can install skills for, and the user-scope directory
# each one's own documentation names.
EXPECTED_ROOTS = {
    "claude": Path(".claude") / "skills",
    "codex": Path(".agents") / "skills",
    "gemini": Path(".gemini") / "skills",
    "cursor": Path(".cursor") / "skills",
}

# Directories deepctl <= 0.3.0 wrote, none of which are skills directories.
LEGACY_PATHS = [
    Path(".claude") / "commands" / "deepgram",
    Path(".codex") / "instructions.md",
    Path(".gemini") / "GEMINI.md",
    Path(".cursor") / "rules" / "deepctl.mdc",
]


def _deepctl_executable() -> Path:
    """The installed console script, next to the interpreter running pytest."""
    for name in ("dg", "deepctl", "dg.exe", "deepctl.exe"):
        candidate = Path(sys.executable).parent / name
        if candidate.exists():
            return candidate
    pytest.skip("deepctl console script is not installed in this environment")


def _run(args: list[str], home: Path) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        # Never let a developer's real credentials or config leak in.
        "DEEPGRAM_API_KEY": "",
        "NO_COLOR": "1",
    }
    return subprocess.run(
        [str(_deepctl_executable()), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A throwaway HOME with the four tools' marker directories present."""
    fake = tmp_path / "home"
    for marker in (".claude", ".codex", ".gemini", ".cursor"):
        (fake / marker).mkdir(parents=True)
    return fake


@pytest.fixture
def installed(home: Path) -> Path:
    result = _run(["skills", "install", "--all"], home)
    assert result.returncode == 0, result.stderr
    return home


class TestSkillsLandWhereTheToolReadsThem:
    def test_every_manifest_skill_is_installed_for_every_tool(
        self, installed: Path
    ) -> None:
        state = json.loads(
            (installed / ".deepctl" / "skills" / "skills.json").read_text()
        )
        expected = state["installed_skills"]["claude"]["skills"]
        assert len(expected) == 14, expected

        for cli_name, relative in EXPECTED_ROOTS.items():
            root = installed / relative
            assert root.is_dir(), f"{cli_name}: {root} was not created"
            found = sorted(p.name for p in root.iterdir() if p.is_dir())
            assert found == sorted(expected), cli_name

    def test_each_skill_is_a_folder_with_a_skill_file(self, installed: Path) -> None:
        for relative in EXPECTED_ROOTS.values():
            for skill_dir in (installed / relative).iterdir():
                assert skill_dir.is_dir()
                assert (skill_dir / "SKILL.md").is_file()

    def test_reference_subdirectories_survive(self, installed: Path) -> None:
        """skills/api and skills/self-hosted each ship a references/ folder."""
        for relative in EXPECTED_ROOTS.values():
            root = installed / relative
            for name in ("api", "self-hosted"):
                refs = root / name / "references"
                assert refs.is_dir(), f"{root / name} lost its references/"
                files = [p for p in refs.iterdir() if p.suffix == ".md"]
                assert files, f"{refs} is empty"

    def test_frontmatter_parses_and_names_match_their_directories(
        self, installed: Path
    ) -> None:
        for relative in EXPECTED_ROOTS.values():
            for skill_dir in sorted((installed / relative).iterdir()):
                text = (skill_dir / "SKILL.md").read_text()
                assert text.startswith("---\n"), skill_dir
                _, _, rest = text.partition("---\n")
                front, sep, _ = rest.partition("\n---")
                assert sep, f"{skill_dir}: unterminated frontmatter"
                data = yaml.safe_load(front)
                assert isinstance(data, dict), skill_dir
                assert data.get("name") == skill_dir.name, skill_dir
                assert data.get("description"), skill_dir

    def test_nothing_lands_in_the_old_locations(self, installed: Path) -> None:
        for relative in LEGACY_PATHS:
            assert not (installed / relative).exists(), relative

    def test_skills_are_byte_identical_across_tools(self, installed: Path) -> None:
        """Each tool gets the same bundle, not a per-tool rendering of it."""
        roots = [installed / relative for relative in EXPECTED_ROOTS.values()]
        reference = roots[0]
        for path in reference.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(reference)
            for other in roots[1:]:
                assert (other / relative).read_bytes() == path.read_bytes(), relative

    def test_state_records_the_pinned_ref(self, installed: Path) -> None:
        from deepctl_core.skill_bundle import DEFAULT_SKILLS_REF

        state = json.loads(
            (installed / ".deepctl" / "skills" / "skills.json").read_text()
        )
        for entry in state["installed_skills"].values():
            assert entry["skills_ref"] == DEFAULT_SKILLS_REF


class TestUpgradeFromTheOldLayout:
    def test_install_clears_what_deepctl_0_3_0_wrote(self, home: Path) -> None:
        legacy_dir = home / ".claude" / "commands" / "deepgram"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "api.md").write_text("---\nname: api\n---\n\nstale\n")

        instructions = home / ".codex" / "instructions.md"
        instructions.write_text(
            "# My own Codex notes\n\n"
            "<!-- BEGIN deepctl CLI Reference (auto-generated by deepctl) -->\n"
            "four skills concatenated into one blob\n"
            "<!-- END deepctl CLI Reference -->\n"
            "# More of my own notes\n"
        )

        result = _run(["skills", "install", "--all"], home)
        assert result.returncode == 0, result.stderr

        assert not legacy_dir.exists()
        remaining = instructions.read_text()
        assert "BEGIN deepctl" not in remaining
        assert "concatenated into one blob" not in remaining
        # The user's own content is not collateral damage.
        assert "# My own Codex notes" in remaining
        assert "# More of my own notes" in remaining


class TestFailurePathsExitNonZero:
    def test_unknown_ref_fails_loudly(self, home: Path) -> None:
        result = _run(
            ["skills", "install", "--all", "--ref", "no-such-tag-12345"], home
        )
        assert result.returncode != 0
        combined = " ".join((result.stdout + result.stderr).split())
        assert "404" in combined or "no ref" in combined
        assert not (home / ".claude" / "skills").exists()

    def test_no_network_fails_loudly(self, home: Path) -> None:
        env_overrides = {
            "https_proxy": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "http_proxy": "http://127.0.0.1:9",
        }
        previous = {k: os.environ.get(k) for k in env_overrides}
        os.environ.update(env_overrides)
        try:
            result = _run(["skills", "install", "--all"], home)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        assert result.returncode != 0
        combined = " ".join((result.stdout + result.stderr).split())
        assert "No skills were installed" in combined
        assert not (home / ".claude" / "skills").exists()
