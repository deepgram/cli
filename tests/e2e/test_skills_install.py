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

# Every tool deepctl can install skills for, and the user-scope directory
# each one's own documentation names. All six, because a destination that
# nothing exercises end to end is a destination nobody has checked.
EXPECTED_ROOTS = {
    "claude": Path(".claude") / "skills",
    "codex": Path(".agents") / "skills",
    "gemini": Path(".gemini") / "skills",
    "cursor": Path(".cursor") / "skills",
    "opencode": Path(".config") / "opencode" / "skills",
    "cline": Path(".cline") / "skills",
}

# How `dg skills status` labels each of them.
TOOL_DISPLAY_NAMES = {
    "claude": "Claude Code",
    "codex": "OpenAI Codex",
    "gemini": "Gemini CLI",
    "cursor": "Cursor",
    "opencode": "OpenCode",
    "cline": "Cline",
}

# The directory whose presence makes each tool "detected". OpenCode and
# Cline are detected by their own config directories, not by the skills
# directory deepctl writes into.
DETECTION_MARKERS = [
    Path(".claude"),
    Path(".codex"),
    Path(".gemini"),
    Path(".cursor"),
    Path(".config") / "opencode",
    Path(".cline"),
]

# Directories deepctl <= 0.3.0 wrote, none of which are skills directories.
LEGACY_PATHS = [
    Path(".claude") / "commands" / "deepgram",
    Path(".codex") / "instructions.md",
    Path(".gemini") / "GEMINI.md",
    Path(".cursor") / "rules" / "deepctl.mdc",
    Path(".opencode") / "agents.md",
    Path(".cline") / "rules" / "deepctl.md",
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
        # Rich sizes the status table to the terminal; without this the
        # 80-column default truncates the longest skills path and the
        # assertions on it fail for reasons that have nothing to do with
        # what the command did.
        "COLUMNS": "200",
        # A developer's own pin must not decide which ref the test asserts.
        "DEEPCTL_SKILLS_REF": "",
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
    """A throwaway HOME with all six tools' marker directories present."""
    fake = tmp_path / "home"
    for marker in DETECTION_MARKERS:
        (fake / marker).mkdir(parents=True)
    return fake


@pytest.fixture
def installed(home: Path) -> Path:
    result = _run(["skills", "install", "--all"], home)
    assert result.returncode == 0, result.stderr
    return home


def _state(home: Path) -> dict:
    return json.loads((home / ".deepctl" / "skills" / "skills.json").read_text())


def _status_counts(result: subprocess.CompletedProcess[str]) -> dict[str, str]:
    """The "Skills Installed" cell of the `skills status` table, per tool.

    Parses the rendered table rather than searching the whole screen for a
    number, so "14" appearing in a path cannot pass for a skill count.
    """
    assert result.returncode == 0, result.stderr
    counts: dict[str, str] = {}
    for line in result.stdout.splitlines():
        cells = [cell.strip() for cell in line.split("│")]
        # Rich draws the row as: "" | CLI | Detected | Installed | Dir | ""
        if len(cells) != 6:
            continue
        if cells[1] in TOOL_DISPLAY_NAMES.values():
            counts[cells[1]] = cells[3]
    return counts


class TestSkillsLandWhereTheToolReadsThem:
    def test_every_manifest_skill_is_installed_for_every_tool(
        self, installed: Path
    ) -> None:
        expected = _state(installed)["installed_skills"]["claude"]["skills"]
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

        state = _state(installed)
        for entry in state["installed_skills"].values():
            assert entry["skills_ref"] == DEFAULT_SKILLS_REF

    def test_every_tool_is_recorded_as_installed(self, installed: Path) -> None:
        """Each of the six, with the folders it wrote, so remove can undo it."""
        state = _state(installed)
        assert set(state["installed_skills"]) == set(EXPECTED_ROOTS)
        for cli_name, relative in EXPECTED_ROOTS.items():
            recorded = state["installed_skills"][cli_name]["paths"]
            assert len(recorded) == 14, cli_name
            assert sorted(recorded) == sorted(
                str(p) for p in (installed / relative).iterdir()
            ), cli_name

    def test_status_reports_every_tool_as_installed(self, installed: Path) -> None:
        result = _run(["skills", "status"], installed)
        assert result.returncode == 0, result.stderr
        rendered = " ".join(result.stdout.split())
        for relative in EXPECTED_ROOTS.values():
            assert f"~/{relative.as_posix()}" in rendered, relative
        counts = _status_counts(result)
        assert counts == {name: "14" for name in TOOL_DISPLAY_NAMES.values()}


class TestUnrelatedSkillsAreNotDeepctlsToTouch:
    """These directories hold other people's skills. deepctl leaves them."""

    NAME = "my-private-skill"

    def _seed_unrelated(self, home: Path, name: str) -> list[Path]:
        seeded = []
        for relative in EXPECTED_ROOTS.values():
            folder = home / relative / name
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: Mine, not Deepgram's.\n---\n"
            )
            seeded.append(folder)
        return seeded

    def test_remove_preserves_an_unrelated_skill(self, home: Path) -> None:
        seeded = self._seed_unrelated(home, self.NAME)

        assert _run(["skills", "install", "--all"], home).returncode == 0
        removed = _run(["skills", "remove", "--all"], home)
        assert removed.returncode == 0, removed.stderr

        for folder in seeded:
            assert folder.is_dir(), f"{folder} was deleted"
            assert "not Deepgram's" in (folder / "SKILL.md").read_text()
        for relative in EXPECTED_ROOTS.values():
            remaining = sorted(p.name for p in (home / relative).iterdir())
            assert remaining == [self.NAME], relative

    def test_install_refuses_to_overwrite_an_unrelated_skill(self, home: Path) -> None:
        """A folder called `api` that deepctl did not write is not its `api`."""
        seeded = self._seed_unrelated(home, "api")

        result = _run(["skills", "install", "--all"], home)
        assert result.returncode != 0
        combined = " ".join((result.stdout + result.stderr).split())
        assert "Refusing to overwrite" in combined

        for folder in seeded:
            assert "not Deepgram's" in (folder / "SKILL.md").read_text()
        # Nothing was installed anywhere, not even for tools with no clash.
        for relative in EXPECTED_ROOTS.values():
            assert sorted(p.name for p in (home / relative).iterdir()) == ["api"]
        assert not (home / ".deepctl" / "skills" / "skills.json").exists()

    def test_a_clash_in_one_tool_installs_nothing_for_the_others(
        self, home: Path
    ) -> None:
        """Five clean destinations must not be written when the sixth clashes."""
        clash = home / EXPECTED_ROOTS["cline"] / "api"
        clash.mkdir(parents=True)
        (clash / "SKILL.md").write_text("---\nname: api\n---\nMine, not Deepgram's.\n")

        result = _run(["skills", "install", "--all"], home)
        assert result.returncode != 0
        assert "Refusing to overwrite" in " ".join(
            (result.stdout + result.stderr).split()
        )
        assert "not Deepgram's" in (clash / "SKILL.md").read_text()
        for cli_name, relative in EXPECTED_ROOTS.items():
            if cli_name == "cline":
                continue
            root = home / relative
            assert not root.exists() or not list(root.iterdir()), cli_name
        assert not (home / ".deepctl" / "skills" / "skills.json").exists()

    def test_status_does_not_count_an_unrelated_skill(self, home: Path) -> None:
        self._seed_unrelated(home, self.NAME)
        counts = _status_counts(_run(["skills", "status"], home))
        assert set(counts) == set(TOOL_DISPLAY_NAMES.values())
        for tool, cell in counts.items():
            assert cell == "No", f"{tool} counted a skill it did not install: {cell}"


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

        # Every remaining artifact 0.3.0 wrote, so each tool's cleanup is
        # exercised rather than only Claude Code's and Codex's.
        deleted_outright = []
        for relative in (
            Path(".cursor") / "rules" / "deepctl.mdc",
            Path(".cline") / "rules" / "deepctl.md",
            Path(".amazonq") / "rules" / "deepctl.md",
        ):
            target = home / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("stale deepctl rules\n")
            deleted_outright.append(target)

        shared = []
        for relative in (
            Path(".gemini") / "GEMINI.md",
            Path(".opencode") / "agents.md",
        ):
            target = home / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "# My own notes\n"
                "<!-- BEGIN deepctl CLI Reference (auto-generated by deepctl) -->\n"
                "four skills concatenated into one blob\n"
                "<!-- END deepctl CLI Reference -->\n"
            )
            shared.append(target)

        result = _run(["skills", "install", "--all"], home)
        assert result.returncode == 0, result.stderr

        assert not legacy_dir.exists()
        for target in deleted_outright:
            assert not target.exists(), target
        for target in shared:
            text = target.read_text()
            assert "BEGIN deepctl" not in text, target
            assert "# My own notes" in text, target
        remaining = instructions.read_text()
        assert "BEGIN deepctl" not in remaining
        assert "concatenated into one blob" not in remaining
        # The user's own content is not collateral damage.
        assert "# My own Codex notes" in remaining
        assert "# More of my own notes" in remaining

    def test_cleanup_leaves_a_slash_command_the_user_added(self, home: Path) -> None:
        """A slash command is a .md file too, so the cleanup names its files."""
        legacy_dir = home / ".claude" / "commands" / "deepgram"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "api.md").write_text("---\nname: api\n---\n\nstale\n")
        (legacy_dir / "deploy.md").write_text("my own slash command")

        result = _run(["skills", "install", "--all"], home)
        assert result.returncode == 0, result.stderr

        assert not (legacy_dir / "api.md").exists()
        assert (legacy_dir / "deploy.md").read_text() == "my own slash command"


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
