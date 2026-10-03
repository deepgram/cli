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

# Supported, but with no skills directory to install into: `status` lists
# them with `skills_directory: null` and `install` prints a hint instead.
NO_SKILLS_DIRECTORY = {"Amazon Q Developer", "Aider"}

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
    # A failure, not a skip. RUN_SKILLS_E2E=1 is an explicit request to
    # run this suite; reporting "skipped" for a missing console script
    # tells the person who asked for it that it ran and found nothing
    # wrong. Install the package into the interpreter running pytest.
    raise AssertionError(
        "RUN_SKILLS_E2E=1 was set but no deepctl console script sits "
        f"next to {sys.executable}. Install deepctl into this "
        "interpreter's environment (e.g. 'uv sync') and run it again."
    )


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


def _status_rows(home: Path) -> dict[str, dict]:
    """`dg -o json skills status`, keyed by display name, installable tools only.

    The structured output is the contract scripts rely on, so it is also
    what this reads: stdout has to be exactly one JSON document, with the
    counts in it rather than searched for on a rendered screen where "14"
    in a path could pass for a skill count.

    Every supported tool is in the document. The two with no skills
    directory are checked for here, once, so the callers can reason about
    the six that have one.
    """
    result = _run(["-o", "json", "skills", "status"], home)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "success", payload
    by_name = {row["display_name"]: row for row in payload["tools"]}
    for name in NO_SKILLS_DIRECTORY:
        assert by_name[name]["skills_directory"] is None, by_name[name]
        assert by_name[name]["installed"] == 0, by_name[name]
    return {name: row for name, row in by_name.items() if row["skills_directory"]}


def _list_rows(home: Path) -> dict[str, dict]:
    """`dg -o json skills list`, keyed by cli name."""
    result = _run(["-o", "json", "skills", "list"], home)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    return {row["cli"]: row for row in payload["installed"]}


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
        from deepctl_core.skill_bundle import DEFAULT_SKILLS_REF

        rows = _status_rows(installed)
        assert set(rows) == set(TOOL_DISPLAY_NAMES.values())
        for cli_name, relative in EXPECTED_ROOTS.items():
            row = rows[TOOL_DISPLAY_NAMES[cli_name]]
            assert row["detected"] is True, row
            assert row["installed"] == 14, row
            assert row["skills_ref"] == DEFAULT_SKILLS_REF, row
            assert Path(row["skills_directory"]) == installed / relative, row

    def test_list_reports_the_ref_and_count_per_tool(self, installed: Path) -> None:
        from deepctl_core.skill_bundle import DEFAULT_SKILLS_REF

        rows = _list_rows(installed)
        assert set(rows) == set(EXPECTED_ROOTS)
        for cli_name, relative in EXPECTED_ROOTS.items():
            row = rows[cli_name]
            assert row["skills_ref"] == DEFAULT_SKILLS_REF, row
            assert row["count"] == 14, row
            assert Path(row["location"]) == installed / relative, row

    def test_status_and_list_stdout_hold_nothing_but_the_document(
        self, installed: Path
    ) -> None:
        """Every human line is on stderr, so `| jq` sees only JSON."""
        for subcommand in ("status", "list"):
            result = _run(["-o", "json", "skills", subcommand], installed)
            assert result.returncode == 0, result.stderr
            # json.loads would already reject a table in front of the
            # document; this also rejects a hint printed after it.
            assert result.stdout.strip().startswith("{"), result.stdout
            assert result.stdout.strip().endswith("}"), result.stdout
            json.loads(result.stdout)


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
        rows = _status_rows(home)
        assert set(rows) == set(TOOL_DISPLAY_NAMES.values())
        for tool, row in rows.items():
            count = row["installed"]
            assert count == 0, f"{tool} counted a skill it did not install: {count}"
            assert row["skills_ref"] is None, row


def _legacy_skill_copy(name: str) -> str:
    """One upstream SKILL.md as deepctl 0.2.16 through 0.3.1 copied it."""
    return (
        f"---\nname: {name}\ndescription: Deepgram {name} skill.\n---\n\n"
        f"# {name}\n\nUse this skill when working with Deepgram.\n"
    )


#: What 0.2.16 through 0.3.1 wrote to each tool's single rules file.
LEGACY_0_3_0_JOINED_SKILLS = "\n\n---\n\n".join(
    _legacy_skill_copy(name) for name in ("api", "docs", "setup-mcp", "starters")
)


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
        # exercised rather than only Claude Code's and Codex's. Written
        # with the content 0.2.16 through 0.3.1 actually produced: the
        # four upstream SKILL.md files verbatim, joined by a rule. The
        # cleanup deletes a file at a legacy path only when it carries
        # that shape, so anything else here would prove nothing.
        deleted_outright = []
        for relative in (
            Path(".cursor") / "rules" / "deepctl.mdc",
            Path(".cline") / "rules" / "deepctl.md",
            Path(".amazonq") / "rules" / "deepctl.md",
        ):
            target = home / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(LEGACY_0_3_0_JOINED_SKILLS)
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

    def test_cleanup_leaves_a_rules_file_deepctl_did_not_write(
        self, home: Path
    ) -> None:
        """Same path 0.3.0 used, but the user's own content: not deepctl's."""
        rules = home / ".cursor" / "rules" / "deepctl.mdc"
        rules.parent.mkdir(parents=True)
        rules.write_text("---\ndescription: My own Cursor rules\n---\n\nBe terse.\n")

        result = _run(["skills", "install", "--all"], home)
        assert result.returncode == 0, result.stderr

        assert rules.read_text() == (
            "---\ndescription: My own Cursor rules\n---\n\nBe terse.\n"
        )

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


class TestUpdateFollowsTheRecordedRef:
    """`install --ref main` then `update` stays on main.

    `update` used to resolve its ref the way `install` does (flag, env var,
    pinned tag), so a bare `update` silently moved a user who had installed
    from a branch back to the pinned tag.
    """

    def test_update_without_a_ref_reinstalls_the_recorded_ref(self, home: Path) -> None:
        from deepctl_core.skill_bundle import DEFAULT_SKILLS_REF

        result = _run(["skills", "install", "--all", "--ref", "main"], home)
        assert result.returncode == 0, result.stderr
        for row in _list_rows(home).values():
            assert row["skills_ref"] == "main", row

        result = _run(["skills", "update"], home)
        assert result.returncode == 0, result.stderr
        assert "deepgram/skills@main" in result.stderr
        assert DEFAULT_SKILLS_REF not in result.stderr
        for row in _list_rows(home).values():
            assert row["skills_ref"] == "main", row
        for row in _status_rows(home).values():
            assert row["skills_ref"] == "main", row

    def test_an_explicit_ref_still_wins(self, home: Path) -> None:
        from deepctl_core.skill_bundle import DEFAULT_SKILLS_REF

        assert (
            _run(["skills", "install", "--all", "--ref", "main"], home).returncode == 0
        )

        result = _run(["skills", "update", "--ref", DEFAULT_SKILLS_REF], home)
        assert result.returncode == 0, result.stderr
        for row in _list_rows(home).values():
            assert row["skills_ref"] == DEFAULT_SKILLS_REF, row


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


class TestRecordsThatAreNotAMapOfTools:
    """`installed_skills` holding a string is a damaged file, not "nothing".

    `update` and `remove --all` used to print INFO and exit 0 for this,
    and `install` replaced the file. Each must exit 1, name the file, and
    leave it exactly as it was.
    """

    @pytest.mark.parametrize(
        "args",
        [
            ["skills", "update"],
            ["skills", "remove", "--all"],
            ["skills", "install", "--all"],
        ],
        ids=["update", "remove", "install"],
    )
    def test_the_command_exits_one_and_leaves_the_file_alone(
        self, home: Path, args: list[str]
    ) -> None:
        state_file = home / ".deepctl" / "skills" / "skills.json"
        state_file.parent.mkdir(parents=True)
        before = json.dumps({"installed_skills": "x"})
        state_file.write_text(before)

        result = _run(args, home)

        assert result.returncode == 1, result.stdout + result.stderr
        combined = " ".join((result.stdout + result.stderr).split())
        assert "cannot read its own records" in combined
        assert "skills.json" in combined
        assert state_file.read_text() == before
        assert not (home / ".claude" / "skills").exists()
