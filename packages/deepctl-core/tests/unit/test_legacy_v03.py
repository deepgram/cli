"""deepctl 0.3.x cleanup: remove only the files and sections deepctl can prove it wrote."""

import contextlib
import csv
import hashlib
import importlib.util
import json
import os
import shutil
import signal
import stat
from pathlib import Path

import pytest
from deepctl_core import output, skill_bundle
from deepctl_core import skill_generator as sg
from deepctl_core.skill_bundle import RepoSkill

POSIX = pytest.mark.skipif(os.name == "nt", reason="POSIX-only filesystem behavior")
pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="legacy cleanup is intentionally disabled on Windows"
)
REF = skill_bundle.DEFAULT_SKILLS_COMMIT
FIX = Path(__file__).parent / "fixtures" / "legacy_v03"
NAMES = ("api", "docs", "setup-mcp", "starters")
BLOB = {n: (FIX / f"{n}.md").read_bytes() for n in NAMES}
JOINED = (FIX / "deepctl.mdc").read_bytes()
BLOCK = (FIX / "GEMINI.md").read_bytes()
SHARED = {
    "codex": ".codex/instructions.md",
    "gemini": ".gemini/GEMINI.md",
    "opencode": ".opencode/agents.md",
}
STANDALONE = {"cursor": ".cursor/rules/deepctl.mdc", "cline": ".cline/rules/deepctl.md"}
DIFFERS = "it differs from every deepgram/skills version deepctl 0.3.x copied"


def _writer():
    spec = importlib.util.spec_from_file_location("v032_writer", FIX / "v032_writer.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


V032 = _writer()


@pytest.fixture(autouse=True)
def _throwaway_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(sg, "_SKILLS_DIR", home / ".deepctl" / "skills")
    monkeypatch.setattr(sg, "_STATE_FILE", home / ".deepctl" / "skills" / "skills.json")
    monkeypatch.setenv("COLUMNS", "400")
    for con in (output.console, output.stderr_console):
        monkeypatch.setattr(con, "_width", 400)
    monkeypatch.delenv(skill_bundle.REF_ENV_VAR, raising=False)
    return home


@pytest.fixture(autouse=True)
def _pinned_output():
    saved = dict(output._output_config)
    output._output_config.update(agentic=True, format="default", quiet=False)
    yield
    output._output_config.clear()
    output._output_config.update(saved)


def gen(cli):
    return next(g for g in sg.get_all_generators() if g.cli_name == cli)


def at(rel):
    return Path.home().joinpath(*rel.split("/"))


def claude(name):
    return at(f".claude/commands/deepgram/{name}.md")


def bundle(tmp, names=NAMES):
    skills = []
    for name in names:
        folder = Path(tmp) / "bundle" / "skills" / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_bytes(f"---\nname: {name}\n---\nnew\n".encode())
        skills.append(RepoSkill(name, folder))
    return skills


def seed(files, record=True, extra=None):
    """Write ``files`` ({path: bytes}) and 0.3.x's record listing them."""
    try:
        state = disk()  # Keep the folder records of an earlier install.
    except FileNotFoundError:
        state = {"installed_skills": {}, "auto_update": True}
    for path, data in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        cli = next(c for c, rels in sg._V03_PATHS.items() if path in map(at, rels))
        if record:
            rec = state["installed_skills"].setdefault(cli, {"paths": []})
            rec["paths"] = [*rec["paths"], str(path)]
    state["installed_skills"].update(extra or {})
    sg._STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    sg._STATE_FILE.write_text(json.dumps(state), encoding="utf-8")


def install(tmp, cli="claude", names=NAMES):
    return sg.install_tool(gen(cli), bundle(tmp, names), ref=REF, version="0.0.0")


def disk():
    return json.loads(sg._STATE_FILE.read_bytes())


def legacy_paths(cli):
    return disk()["installed_skills"].get(cli, {}).get("paths")


def folder_paths(cli, names=NAMES):
    return [str(gen(cli).skills_root() / n) for n in names]


def err(capsys):
    return " ".join(capsys.readouterr().err.split())


def note(key, **kw):
    return " ".join(sg._msg(key, **kw).split())


def ident(path):
    st = os.lstat(path)
    return st.st_ino, path.read_bytes()


def crlf(data):
    return data.replace(b"\n", b"\r\n")


def link_kept(tmp_path, capsys, cli, path, data, dangling):
    """A link at a legacy path, dangling or not, is kept and warned about once."""
    target = tmp_path / "target.md"
    target.write_bytes(data)
    seed({path: b""})
    path.unlink()
    try:
        path.symlink_to(os.path.relpath(target, path.parent))
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create a symlink here: {exc}")
    if dangling:
        target.unlink()
    install(tmp_path, cli)
    assert path.is_symlink()
    assert dangling or target.read_bytes() == data
    assert "(it is a link)" in err(capsys)
    install(tmp_path, cli)
    assert err(capsys) == ""


class TestAllowlist:
    def test_allowlist_matches_trace(self):
        lines = (FIX / "allowlist.tsv").read_text(encoding="utf-8").splitlines()
        assert "0fc13fa" in lines[0]
        rows = list(csv.DictReader(lines[1:], delimiter="\t"))
        assert len(rows) == 29
        assert all(r["live_releases"] for r in rows)
        want = {
            n: {f"{r['bytes']}:{r['sha256']}" for r in rows if r["skill"] == n}
            for n in NAMES
        }
        assert {n: set(v) for n, v in sg._V03_BLOBS.items()} == want
        assert sum(len(v) for v in sg._V03_BLOBS.values()) == 29

    def test_fixtures_regenerate_from_v032_writer(self, tmp_path, capsys):
        skills = {n: BLOB[n].decode("utf-8") for n in NAMES}
        for n in NAMES:
            entry = f"{len(BLOB[n])}:{hashlib.sha256(BLOB[n]).hexdigest()}"
            assert entry in sg._V03_BLOBS[n]
        joined = V032.joined(skills).encode("utf-8")
        assert joined == JOINED
        assert hashlib.sha256(joined).hexdigest().startswith("c6302c78")
        seed({at(STANDALONE["cursor"]): joined})
        install(tmp_path, "cursor")
        assert not at(STANDALONE["cursor"]).exists()


class TestStandalone:
    def test_claude_files_removed_records_cleared(self, tmp_path, capsys):
        seed({claude(n): BLOB[n] for n in NAMES})
        mine = claude("mine")
        mine.write_bytes(b"the user's own command")
        placed, _ = install(tmp_path)
        assert len(placed) == 4
        assert [n for n in NAMES if claude(n).exists()] == []
        assert mine.read_bytes() == b"the user's own command"  # The dir stays.
        text = err(capsys)
        assert "INFO: Removed deepctl 0.3.x files for Claude Code:" in text
        assert str(claude("api")) in text
        assert "WARN" not in text
        state = disk()
        assert "v03" not in state["skill_folders"]["claude"]
        assert state["skill_folders"]["claude"]["skills_ref"] == REF
        assert legacy_paths("claude") == folder_paths("claude", sorted(NAMES))

    def test_v03_cleared_after_cleanup(self, tmp_path):
        seed({claude(n): BLOB[n] for n in NAMES})
        state = disk()
        state["skill_folders"] = {
            "claude": {"folders": {}, "v03": True, "skills_ref": "old"}
        }
        sg._STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
        install(tmp_path)
        state = disk()
        assert "v03" not in state["skill_folders"]["claude"]
        assert state["skill_folders"]["claude"]["skills_ref"] == REF
        assert "claude" in state["installed_skills"]
        assert legacy_paths("claude") == folder_paths("claude", sorted(NAMES))

    def test_stale_v03_flag_popped_without_legacy_paths(self, tmp_path, capsys):
        state = {
            "installed_skills": {"claude": {"paths": folder_paths("claude")}},
            "skill_folders": {"claude": {"folders": {}, "v03": True}},
        }
        sg._STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        sg._STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
        install(tmp_path)
        assert "v03" not in disk()["skill_folders"]["claude"]
        assert err(capsys) == ""

    def test_command_dir_keeps_retained_legacy_copies(self, tmp_path):
        seed({claude(n): BLOB[n] for n in NAMES})
        install(tmp_path)
        kept = [
            n
            for n in os.listdir(claude("api").parent)
            if n.startswith(".deepctl-kept-v03-")
        ]
        assert len(kept) == len(NAMES)

    @pytest.mark.parametrize("cli", ["cursor", "cline"])
    @pytest.mark.parametrize(
        "names", [NAMES, ("api", "starters"), ("docs",), ("api", "docs", "setup-mcp")]
    )
    @pytest.mark.parametrize("eol", ["lf", "crlf"])
    def test_joined_file_removed(self, tmp_path, capsys, cli, names, eol):
        data = V032.joined({n: BLOB[n].decode() for n in names}).encode()
        data = crlf(data) if eol == "crlf" else data
        seed({at(STANDALONE[cli]): data})
        install(tmp_path, cli)
        assert not at(STANDALONE[cli]).exists()
        assert at(STANDALONE[cli]).parent.is_dir()  # Only Claude's folder is ours.
        assert "Removed deepctl 0.3.x files for" in err(capsys)
        assert legacy_paths(cli) == folder_paths(cli, sorted(NAMES))
        assert "v03" not in disk()["skill_folders"][cli]

    @pytest.mark.parametrize(
        "data",
        [
            BLOB["api"] + b"x",
            BLOB["api"][:-2] + b"X\n",  # Same size, one byte changed.
            BLOB["api"].replace(b"\n", b"\r\n", 1),  # Mixed line endings.
            b"",
            b"---\nname: api\n---\nmy own api notes\n",
            BLOB["docs"],  # Another skill's text at api.md.
            BLOB["api"] + b"\n\n---\n\n",
        ],
        ids=["plus-byte", "same-size", "mixed-eol", "empty", "own", "docs", "sep"],
    )
    def test_user_api_md_with_name_api_survives(self, tmp_path, capsys, data):
        """B8: only exact deepgram/skills bytes prove; frontmatter never does."""
        api = claude("api")
        seed({api: data})
        before = ident(api)
        install(tmp_path)
        assert ident(api) == before
        text = err(capsys)
        assert note("E33", path=api, why=DIFFERS) in text
        assert text.count(str(api)) == 1
        assert str(api) not in legacy_paths("claude")
        assert "v03" not in disk()["skill_folders"]["claude"]
        install(tmp_path)  # Warned once, then untracked: silent.
        assert err(capsys) == ""
        assert ident(api) == before

    def test_quiet_run_keeps_the_record_so_a_later_run_warns(self, tmp_path, capsys):
        api = claude("api")
        seed({api: b"mine"})
        output._output_config["quiet"] = True
        install(tmp_path)
        assert err(capsys) == ""
        assert str(api) in legacy_paths("claude")
        output._output_config["quiet"] = False
        install(tmp_path)
        assert note("E33", path=api, why=DIFFERS) in err(capsys)
        install(tmp_path)
        assert err(capsys) == ""
        assert api.read_bytes() == b"mine"

    def test_joined_with_a_reordered_or_repeated_skill_kept(self, tmp_path, capsys):
        sep = b"\n\n---\n\n"
        for data in (BLOB["docs"] + sep + BLOB["api"], BLOB["api"] + sep + BLOB["api"]):
            seed({at(STANDALONE["cursor"]): data})
            install(tmp_path, "cursor")
            assert at(STANDALONE["cursor"]).read_bytes() == data
            assert DIFFERS in err(capsys)

    def test_unrecorded_unprovable_file_is_silent(self, tmp_path, capsys):
        api = claude("api")
        seed({api: b"mine"}, record=False)
        install(tmp_path)
        assert api.read_bytes() == b"mine"
        assert err(capsys) == ""

    def test_gate_keeps_files_whose_folder_did_not_land(self, tmp_path, capsys):
        files = {claude(n): BLOB[n] for n in NAMES}
        files[at(STANDALONE["cursor"])] = JOINED
        seed(files)
        without_api = ("docs", "setup-mcp", "starters")
        install(tmp_path, "claude", without_api)
        install(tmp_path, "cursor", without_api)
        assert claude("api").read_bytes() == BLOB["api"]
        assert not claude("docs").exists()
        assert at(STANDALONE["cursor"]).read_bytes() == JOINED
        assert "WARN" not in err(capsys)
        assert legacy_paths("cursor") == [str(at(STANDALONE["cursor"]))]
        assert str(claude("api")) in legacy_paths("claude")
        assert disk()["skill_folders"]["claude"]["v03"] is True

    def test_amazonq_and_aider_files_untouched(self, tmp_path, capsys):
        q, aider = (
            at(".amazonq/rules/deepctl.md"),
            at(".deepctl/skills/deepctl-conventions.md"),
        )
        for p in (q, aider):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(JOINED)
        extra = {"amazonq": {"paths": [str(q)]}, "aider": {"paths": [str(aider)]}}
        seed({claude("api"): BLOB["api"]}, extra=extra)
        for g in sg.get_all_generators():
            sg.install_tool(g, bundle(tmp_path), ref=REF, version="0.0.0")
        assert q.read_bytes() == JOINED and aider.read_bytes() == JOINED
        assert {c: disk()["installed_skills"][c] for c in extra} == extra

    def test_moved_home_record_pruned(self, tmp_path):
        gone = tmp_path / "old-home" / ".claude" / "commands" / "deepgram" / "api.md"
        seed({claude("docs"): b"mine"}, record=False)
        state = disk()
        state["installed_skills"] = {
            "claude": {"paths": [str(gone), str(claude("docs"))]}
        }
        sg._STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
        install(tmp_path)
        assert str(gone) not in legacy_paths("claude")


class TestShared:
    @pytest.mark.parametrize("cli", sorted(SHARED))
    def test_block_removal_keeps_user_text(self, tmp_path, capsys, cli):
        seed_text = b"# My notes\n\nKeep this.\n"
        data = V032.shared({n: BLOB[n].decode() for n in NAMES}, seed_text.decode())
        path = at(SHARED[cli])
        seed({path: data.encode()})
        install(tmp_path, cli)
        assert path.read_bytes() == seed_text  # cmp-equal to the pre-0.3.x file.
        text = err(capsys)
        assert f"INFO: Removed the deepctl 0.3.x section from {path}; the rest" in text
        assert "Removed deepctl 0.3.x files" not in text
        assert legacy_paths(cli) == folder_paths(cli, sorted(NAMES))
        assert "v03" not in disk()["skill_folders"][cli]

    def test_shared_cleanup_then_second_install_is_silent(
        self, tmp_path, capsys, monkeypatch
    ):
        path = at(SHARED["gemini"])
        seed({path: b"user\n\n" + BLOCK})
        install(tmp_path, "gemini")
        assert path.read_bytes() == b"user\n"
        capsys.readouterr()
        calls = []
        real = sg._update_state
        monkeypatch.setattr(
            sg, "_update_state", lambda *a, **k: (calls.append(a[1]), real(*a, **k))
        )
        install(tmp_path, "gemini")
        assert err(capsys) == ""
        assert calls == ["E9", "E9b"]  # The cleanup wrote nothing.
        assert "v03" not in disk()["skill_folders"]["gemini"]

    def test_shared_file_without_markers_is_untouched_and_silent(
        self, tmp_path, capsys, monkeypatch
    ):
        path = at(SHARED["gemini"])
        seed({path: b"user text only\n"}, record=False)
        before = ident(path)
        calls = []
        real = sg._update_state
        monkeypatch.setattr(
            sg, "_update_state", lambda *a, **k: (calls.append(a[1]), real(*a, **k))
        )
        install(tmp_path, "gemini")
        assert ident(path) == before
        assert err(capsys) == ""
        assert calls == ["E9", "E9b"]

    def test_recorded_file_without_markers_is_done_and_silent(self, tmp_path, capsys):
        path = at(SHARED["gemini"])
        seed({path: b"I removed the section myself.\n"})
        before = ident(path)
        install(tmp_path, "gemini")
        assert ident(path) == before
        assert err(capsys) == ""
        assert legacy_paths("gemini") == folder_paths("gemini", sorted(NAMES))
        assert "v03" not in disk()["skill_folders"]["gemini"]

    @pytest.mark.parametrize(
        ("data", "want"),
        [
            (BLOCK, None),  # Block only: 0.3.x created the file, so it is deleted.
            (b"user\n\n" + BLOCK + b"after\n", b"user\nafter\n"),
            (crlf(b"user\n\n" + BLOCK + b"after\n"), b"user\r\nafter\r\n"),
            (b"\xef\xbb\xbfuser\n\n" + BLOCK, b"\xef\xbb\xbfuser\n"),  # BOM kept.
            (b"\n\n" + BLOCK, b"\n"),  # An empty user file: one newline left.
            (b"user\n\n\n" + BLOCK, b"user\n\n\n"),  # Not 0.3.x's shape: kept as is.
            (b"user" + b"\n" + BLOCK, b"user\n"),  # No blank line before it.
        ],
        ids=["only", "after", "crlf", "bom", "empty", "three-eol", "no-blank"],
    )
    def test_block_shapes(self, tmp_path, data, want):
        path = at(SHARED["codex"])
        seed({path: data})
        install(tmp_path, "codex")
        assert (path.read_bytes() if path.exists() else None) == want
        assert legacy_paths("codex") == folder_paths("codex", sorted(NAMES))

    @pytest.mark.parametrize(
        "data",
        [
            BLOCK + BLOCK,  # Duplicate.
            BLOCK[: BLOCK.index(b"\n") + 1] + BLOCK,  # Nested BEGIN.
            BLOCK[: -len(sg._V03_END) - 1],  # Unterminated.
            b"a\n" + sg._V03_END + b"\nx\n" + sg._V03_BEGIN + b"\n",  # END first.
            b"text " + BLOCK,  # BEGIN not on its own line.
            BLOCK[:-1] + b" trailing\n",  # END not on its own line.
            b"user\r\n\r\n" + BLOCK,  # Mixed line endings.
            b"```\n" + BLOCK + b"```\n" + BLOCK,  # A second copy in a code fence.
        ],
        ids=["dup", "nested", "open", "end-first", "begin-inline", "end-inline"]
        + ["mixed-eol", "fenced"],
    )
    def test_incomplete_repeated_or_mixed_sections_kept(self, tmp_path, capsys, data):
        path = at(SHARED["opencode"])
        seed({path: data})
        before = ident(path)
        install(tmp_path, "opencode")
        assert ident(path) == before
        text = err(capsys)
        assert "WARN: deepctl can't safely remove its 0.3.x section from" in text
        assert "delete it" not in text  # Never for a file with the user's text.
        assert legacy_paths("opencode") == folder_paths("opencode", sorted(NAMES))
        install(tmp_path, "opencode")
        assert err(capsys) == ""

    @POSIX
    def test_hard_link_and_foreign_owner_kept(self, tmp_path, capsys, monkeypatch):
        path = at(SHARED["gemini"])
        seed({path: b"u\n\n" + BLOCK})
        os.link(path, tmp_path / "other-name")
        install(tmp_path, "gemini")
        assert path.read_bytes() == b"u\n\n" + BLOCK
        assert "it has other hard links or another user owns it" in err(capsys)
        os.unlink(tmp_path / "other-name")
        seed({path: b"u\n\n" + BLOCK})
        monkeypatch.setattr(os, "getuid", lambda: os.stat(path).st_uid + 1)
        install(tmp_path, "gemini")
        assert path.read_bytes() == b"u\n\n" + BLOCK
        assert "it has other hard links or another user owns it" in err(capsys)

    @POSIX
    def test_mode_times_and_line_endings_kept(self, tmp_path):
        path = at(SHARED["gemini"])
        seed({path: crlf(b"u\n\n" + BLOCK)})
        path.chmod(0o640)
        os.utime(path, ns=(1_600_000_000_000_000_000, 1_600_000_000_123_456_789))
        install(tmp_path, "gemini")
        assert path.read_bytes() == b"u\r\n"
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o640
        assert os.stat(path).st_mtime_ns == 1_600_000_000_123_456_789

    @pytest.mark.parametrize("lock", ["read-only", "root", "uchg"])
    def test_read_only_or_locked_file_kept_once(
        self, tmp_path, capsys, monkeypatch, lock
    ):
        if lock == "root":  # os.access lets root write anything: the mode decides.
            monkeypatch.setattr(os, "access", lambda *a, **k: True)
        path = at(SHARED["gemini"])
        seed({path: b"u\n\n" + BLOCK})
        try:
            if lock == "uchg":
                try:
                    os.chflags(path, stat.UF_IMMUTABLE)
                except (AttributeError, OSError) as exc:
                    pytest.skip(f"no chflags uchg here: {exc}")
            else:
                path.chmod(0o444)
            install(tmp_path, "gemini")
            why = "it is read-only or locked"
            assert note("E34", path=path, why=why, what=sg._V03_LINES) in err(capsys)
            install(tmp_path, "gemini")
            assert err(capsys) == ""
        finally:
            with contextlib.suppress(AttributeError, OSError):
                os.chflags(path, 0)
            path.chmod(0o644)
        assert path.read_bytes() == b"u\n\n" + BLOCK
        assert [n for n in os.listdir(path.parent) if n.startswith(sg._V03_ASIDE)] == []

    def test_windows_branch_leaves_legacy_content_for_manual_removal(
        self, tmp_path, capsys, monkeypatch
    ):
        monkeypatch.setattr(sg, "_WINDOWS", True)
        monkeypatch.delattr(os, "getuid", raising=False)
        shared, cursor = at(SHARED["gemini"]), at(STANDALONE["cursor"])
        seed({shared: b"u\n\n" + BLOCK, cursor: JOINED})
        monkeypatch.setattr(
            sg,
            "_v03_file",
            lambda *a: pytest.fail("Windows must not mutate legacy paths"),
        )
        install(tmp_path, "gemini")
        install(tmp_path, "cursor")
        assert shared.read_bytes() == b"u\n\n" + BLOCK
        assert cursor.read_bytes() == JOINED
        assert "does not clean 0.3.x content on Windows" in err(capsys)

    def test_concurrent_change_keeps_file_and_drops_temp(
        self, tmp_path, capsys, monkeypatch
    ):
        path = at(SHARED["gemini"])
        seed({path: b"u\n\n" + BLOCK})
        real = sg._read_regular

        def read(p, limit, fd=None):  # The re-proof reads the moved file.
            data = real(p, limit, fd)
            return b"changed" if Path(p).name.startswith(sg._V03_ASIDE) else data

        monkeypatch.setattr(sg, "_read_regular", read)
        install(tmp_path, "gemini")
        assert path.read_bytes() == b"u\n\n" + BLOCK
        assert "it changed while deepctl was editing it" in err(capsys)
        assert [n for n in os.listdir(path.parent) if n.startswith(sg._V03_ASIDE)] == []

    def test_section_line_reports_a_completed_cut(self, tmp_path, capsys):
        path = at(SHARED["gemini"])
        seed({path: b"u\n\n" + BLOCK})
        install(tmp_path, "gemini")
        text = err(capsys)
        assert f"Removed the deepctl 0.3.x section from {path}" in text
        assert "Removed deepctl 0.3.x files" not in text

    def test_e35_on_replace_failure(self, tmp_path, capsys, monkeypatch):
        path = at(SHARED["gemini"])
        seed({path: b"u\n\n" + BLOCK})
        real = sg._rename_excl

        def rename(src, dest, fd=None):  # The publish: the temp onto the name.
            if Path(dest).name == path.name and Path(src).name.endswith(".tmp"):
                raise PermissionError(13, "Permission denied")
            return real(src, dest, fd)

        monkeypatch.setattr(sg, "_rename_excl", rename)
        install(tmp_path, "gemini")
        assert path.read_bytes() == b"u\n\n" + BLOCK
        assert note("E35", path=path, reason="Permission denied") in err(capsys)
        assert [n for n in os.listdir(path.parent) if n.startswith(sg._V03_ASIDE)] == []
        assert legacy_paths("gemini") == [str(path)]

    def test_block_fixture_regenerates_from_v032_writer(self, tmp_path):
        block = V032.shared({n: BLOB[n].decode() for n in NAMES}, None).encode()
        assert block == BLOCK
        assert hashlib.sha256(block).hexdigest().startswith("b6158ef6")
        seed({at(SHARED["gemini"]): block})
        install(tmp_path, "gemini")
        assert not at(SHARED["gemini"]).exists()

    def test_gate_keeps_section_when_a_folder_did_not_land(self, tmp_path, capsys):
        seed({at(SHARED["gemini"]): b"u\n\n" + BLOCK})
        install(tmp_path, "gemini", ("docs", "setup-mcp", "starters"))
        assert at(SHARED["gemini"]).read_bytes() == b"u\n\n" + BLOCK
        assert err(capsys) == ""
        assert legacy_paths("gemini") == [str(at(SHARED["gemini"]))]

    @pytest.mark.parametrize("dangling", [False, True])
    def test_link_kept(self, tmp_path, capsys, dangling):
        link_kept(tmp_path, capsys, "gemini", at(SHARED["gemini"]), BLOCK, dangling)

    def test_temp_prefix_is_not_staging(self, tmp_path, monkeypatch):
        seed({at(SHARED["codex"]): b"u\n\n" + BLOCK})
        names, real = [], os.open

        def open_(p, *a, **k):
            names.append(Path(p).name)
            return real(p, *a, **k)

        monkeypatch.setattr(os, "open", open_)
        install(tmp_path, "codex")
        assert [n for n in names if n.startswith(sg._V03_ASIDE) and n.endswith(".tmp")]
        assert at(SHARED["codex"]).read_bytes() == b"u\n"


class TestLinksAndKinds:
    @pytest.mark.parametrize("dangling", [False, True])
    @pytest.mark.parametrize("kind", ["claude", "cursor"])
    def test_link_at_legacy_path_kept(self, tmp_path, capsys, kind, dangling):
        path = {"claude": claude("api"), "cursor": at(STANDALONE["cursor"])}[kind]
        data = {"claude": BLOB["api"], "cursor": JOINED}[kind]
        link_kept(tmp_path, capsys, kind, path, data, dangling)

    def test_directory_at_legacy_path_kept(self, tmp_path, capsys):
        seed({claude("api"): b""})
        claude("api").unlink()
        claude("api").mkdir()
        install(tmp_path)
        assert claude("api").is_dir()
        assert "(it is not a file)" in err(capsys)

    def test_oversized_file_kept(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(sg, "_V03_MAX", 10)
        seed({claude("api"): BLOB["api"]})
        install(tmp_path)
        assert claude("api").read_bytes() == BLOB["api"]
        assert "(it is larger than 16 MiB)" in err(capsys)

    def test_a_file_that_changes_before_the_read_is_not_called_too_large(
        self, tmp_path, capsys, monkeypatch
    ):
        seed({claude("api"): BLOB["api"]})
        real = sg._read_regular

        def read(p, n, fd=None):  # A save between the lstat and the read.
            return None if Path(p).name == "api.md" else real(p, n, fd)

        monkeypatch.setattr(sg, "_read_regular", read)
        install(tmp_path)
        text = err(capsys)
        assert "(it changed while deepctl read it)" in text
        assert "larger than 16 MiB" not in text
        assert claude("api").read_bytes() == BLOB["api"]

    @POSIX
    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads anything"
    )
    @pytest.mark.parametrize("record", [True, False], ids=["recorded", "unrecorded"])
    def test_unreadable_parent_is_e35_only_if_recorded(self, tmp_path, capsys, record):
        seed({claude("api"): BLOB["api"]}, record=record)
        claude("api").parent.chmod(0)
        try:
            install(tmp_path)
            text = err(capsys)
        finally:
            claude("api").parent.chmod(0o755)
        assert ("Could not remove deepctl 0.3.x content from" in text) is record
        assert ("is unchanged and still recorded" in text) is record  # E35, not E35b
        assert (str(claude("api")) in legacy_paths("claude")) is record
        assert claude("api").read_bytes() == BLOB["api"]


class TestFailures:
    @pytest.mark.parametrize("how", ["swap", "swap-last", "E1", "E9b", "E27"])
    def test_v03_untouched_when_install_fails(self, tmp_path, monkeypatch, how):
        files = {claude(n): BLOB[n] for n in NAMES}
        seed(files)
        state = disk()
        state["skill_folders"] = {"claude": {"folders": {}, "v03": True}}
        sg._STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
        before = {p: ident(p) for p in files}
        if how == "swap":
            monkeypatch.setattr(
                sg, "_swap", _raise(OSError(28, "No space left on device"))
            )
        elif how == "swap-last":  # Three folders land, then the install fails.
            real_swap = sg._swap

            def swap(g, name, *a):
                if name == "starters":
                    raise OSError(28, "No space left on device")
                return real_swap(g, name, *a)

            monkeypatch.setattr(sg, "_swap", swap)
        elif how == "E1":
            (gen("claude").skills_root() / "api").mkdir(parents=True)
        elif how == "E9b":
            real = sg._update_state

            def update(mutate, failure="E9c", g=None):
                if failure == "E9b":
                    raise sg._err("E9b", g, reason="disk full")
                return real(mutate, failure, g)

            monkeypatch.setattr(sg, "_update_state", update)
        else:
            monkeypatch.setattr(sg, "_LOCK_TIMEOUT", 0.0)
            monkeypatch.setattr(sg, "_try_lock", lambda lock: -1)
        with pytest.raises(sg.SkillInstallError):
            install(tmp_path)
        assert {p: ident(p) for p in files} == before
        if how != "E27":
            assert disk()["installed_skills"]["claude"]["paths"] == [
                str(p) for p in files
            ]

    def test_e35_on_retained_copy_failure_install_succeeds(
        self, tmp_path, capsys, monkeypatch
    ):
        seed({claude("api"): BLOB["api"]})
        real = sg._rename_excl

        def rename(src, dest, fd=None):
            if Path(dest).name.startswith(".deepctl-kept-v03-"):
                raise PermissionError(13, "Permission denied", str(dest))
            return real(src, dest, fd)

        monkeypatch.setattr(sg, "_rename_excl", rename)
        placed, _ = install(tmp_path)
        assert len(placed) == 4
        assert claude("api").read_bytes() == BLOB["api"]  # Put back.
        text = err(capsys)
        assert note("E35", path=claude("api"), reason="Permission denied") in text
        assert f"{claude('api')} is unchanged and still recorded" in text
        assert str(claude("api")) in legacy_paths("claude")
        assert disk()["skill_folders"]["claude"]["v03"] is True

    def test_e36_when_the_hook_fails(self, tmp_path, capsys, monkeypatch):
        seed({claude("api"): BLOB["api"]})
        real = sg._clean_v03

        def clean(g, root):
            monkeypatch.setattr(
                sg, "get_skills_state", _raise(sg._err("E8", reason="I/O error"))
            )
            real(g, root)

        monkeypatch.setattr(sg, "_clean_v03", clean)
        placed, _ = install(tmp_path)
        assert len(placed) == 4
        text = err(capsys)
        reason = sg._msg("E8", reason="I/O error").rstrip(".")
        assert note("E36", display="Claude Code", reason=reason) in text
        assert claude("api").read_bytes() == BLOB["api"]

    def test_reproof_fails_after_move_puts_it_back(self, tmp_path, capsys, monkeypatch):
        seed({claude("api"): BLOB["api"]})
        real = sg._read_regular
        monkeypatch.setattr(
            sg,
            "_read_regular",
            lambda p, n, fd=None: (
                b"other" if Path(p).name.startswith(sg._V03_ASIDE) else real(p, n, fd)
            ),
        )
        install(tmp_path)
        assert claude("api").read_bytes() == BLOB["api"]
        assert "it changed while deepctl was removing it" in err(capsys)
        assert os.listdir(claude("api").parent) == ["api.md"]

    @pytest.mark.parametrize("key", ["E4", "E37"])
    def test_e4_or_e37_when_put_back_fails(self, tmp_path, capsys, monkeypatch, key):
        seed({claude("api"): BLOB["api"]})
        real_read, real_rename = sg._read_regular, sg._rename_excl
        monkeypatch.setattr(
            sg,
            "_read_regular",
            lambda p, n, fd=None: (
                b"other"
                if Path(p).name.startswith(sg._V03_ASIDE)
                else real_read(p, n, fd)
            ),
        )

        def rename(src, dest, fd=None):  # E37: something was saved at the name.
            if Path(src).name.startswith(sg._V03_ASIDE) and key == "E37":
                raise FileExistsError(17, "File exists")
            if Path(src).name.startswith(sg._V03_ASIDE):
                raise PermissionError(13, "Permission denied")
            return real_rename(src, dest, fd)

        monkeypatch.setattr(sg, "_rename_excl", rename)
        install(tmp_path)
        text = err(capsys)
        aside = claude("api").with_name(sg._V03_ASIDE + "api.md")
        assert note(key, dest=claude("api"), aside=aside) in text
        if key == "E37":  # Moving it back by hand would replace the save.
            assert "move it back by hand" not in text
        assert aside.read_bytes() == BLOB["api"]

    @pytest.mark.parametrize("fails_back", [False, True])
    def test_ctrl_c_during_the_move_puts_it_back(
        self, tmp_path, capsys, monkeypatch, fails_back
    ):
        seed({claude("api"): BLOB["api"]})
        real_read, real_rename = sg._read_regular, sg._rename_excl

        def read(p, n, fd=None):
            if Path(p).name.startswith(sg._V03_ASIDE):
                raise KeyboardInterrupt
            return real_read(p, n, fd)

        def rename(src, dest, fd=None):
            if fails_back and Path(src).name.startswith(sg._V03_ASIDE):
                raise FileExistsError(17, "File exists")
            return real_rename(src, dest, fd)

        monkeypatch.setattr(sg, "_read_regular", read)
        monkeypatch.setattr(sg, "_rename_excl", rename)
        with pytest.raises(KeyboardInterrupt):
            install(tmp_path)
        text = err(capsys)
        if fails_back:  # A file is at the name now: compare, never move back over it.
            assert "was saved while deepctl was removing its 0.3.x content" in text
            assert "move it back by hand" not in text
        else:
            assert claude("api").read_bytes() == BLOB["api"]
            assert "WARN" not in text

    @POSIX
    def test_an_aside_swapped_for_a_link_is_never_put_back(
        self, tmp_path, capsys, monkeypatch
    ):
        seed({claude("api"): BLOB["api"]})
        aside = claude("api").with_name(sg._V03_ASIDE + "api.md")
        victim, real = tmp_path / "victim", sg._read_regular
        victim.write_bytes(b"secret")

        def read(p, n, fd=None):  # Another process swaps the aside during the re-proof.
            if Path(p).name.startswith(sg._V03_ASIDE):
                aside.unlink()
                aside.symlink_to(victim)
                return b"changed"
            return real(p, n, fd)

        monkeypatch.setattr(sg, "_read_regular", read)
        install(tmp_path)
        assert not claude("api").exists() and not claude("api").is_symlink()
        assert aside.is_symlink() and victim.read_bytes() == b"secret"
        text = err(capsys)
        assert note("E41", dest=claude("api"), aside=aside) in text
        assert "move it back by hand" not in text  # E4 would point at the link.
        assert "left it in place" not in text and "is still recorded" not in text

    def test_a_successful_cleanup_uses_a_nonrecovery_backup_name(
        self, tmp_path, capsys
    ):
        seed({claude("api"): BLOB["api"]})
        install(tmp_path)
        backups = kept(claude("api").parent)
        assert not claude("api").exists() and len(backups) == 1
        assert not (claude("api").parent / (sg._V03_ASIDE + "api.md")).exists()
        assert "kept the original file" in err(capsys)

    @POSIX
    def test_after_e41_a_restored_file_gets_e42_not_e38(
        self, tmp_path, capsys, monkeypatch
    ):
        seed({claude("api"): BLOB["api"]})
        aside = claude("api").with_name(sg._V03_ASIDE + "api.md")
        victim, real = tmp_path / "victim", sg._read_regular
        victim.write_bytes(b"secret")

        def read(p, n, fd=None):  # Another process swaps the aside during the re-proof.
            if Path(p).name == aside.name:
                aside.unlink()
                aside.symlink_to(victim)
                return b"changed"
            return real(p, n, fd)

        monkeypatch.setattr(sg, "_read_regular", read)
        install(tmp_path)
        assert note("E41", dest=claude("api"), aside=aside) in err(capsys)
        monkeypatch.setattr(sg, "_read_regular", real)
        claude("api").write_bytes(b"restored")  # The user restores it from a backup.
        for _ in range(2):  # Each run, until the user deletes the aside.
            install(tmp_path)
            text = err(capsys)
            assert note("E42", dest=claude("api"), aside=aside) in text
            assert "holds an earlier version" not in text  # E38 is for its own files.
        assert claude("api").read_bytes() == b"restored" and aside.is_symlink()
        assert victim.read_bytes() == b"secret"

    def test_aside_prefix_is_not_staging(self, tmp_path, monkeypatch):
        assert sg._V03_ASIDE == ".deepctl-v03-"
        assert not sg._V03_ASIDE.startswith(sg._STAGING_PREFIX)
        seed({claude("api"): BLOB["api"]})
        names, real = [], sg._rename_excl

        def rename(src, dest, fd=None):
            names.append(Path(dest).name)
            return real(src, dest, fd)

        monkeypatch.setattr(sg, "_rename_excl", rename)
        install(tmp_path)
        assert [n for n in names if n.startswith(sg._V03_ASIDE)] == [
            sg._V03_ASIDE + "api.md"  # The same name each run: a leftover is found.
        ]
        assert not claude("api").exists()


class TestOutput:
    def test_everything_on_stderr_outside_agentic_mode(self, tmp_path, capsys):
        output._output_config.update(agentic=False)
        seed({claude("api"): BLOB["api"], claude("docs"): b"mine"})
        install(tmp_path)
        out, error = capsys.readouterr()
        assert out == ""
        assert "Removed deepctl 0.3.x files for Claude Code" in error
        assert "deepctl can't prove it wrote" in error


def _raise(exc):
    def fail(*a, **k):
        raise exc

    return fail


def asides(folder):
    names = os.listdir(folder) if folder.is_dir() else []  # Claude's may be gone.
    return sorted(n for n in names if n.startswith(sg._V03_ASIDE))


def kept(folder):
    names = os.listdir(folder) if folder.is_dir() else []
    return sorted(n for n in names if n.startswith(".deepctl-kept-v03-"))


def open_fds():
    return len(
        os.listdir("/proc/self/fd" if os.path.isdir("/proc/self/fd") else "/dev/fd")
    )


def relink(tmp_path, rel):
    """Move ~/rel elsewhere and put a link to it at ~/rel."""
    link, real = at(rel), tmp_path / "elsewhere"
    link.rename(real)
    try:
        link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create a symlink here: {exc}")
    return link, real


CASES = {  # cli: (its 0.3.x file, what it holds, the folders a link can replace)
    "claude": (".claude/commands/deepgram/api.md", BLOB["api"], 3),
    "cursor": (STANDALONE["cursor"], JOINED, 2),
    "cline": (STANDALONE["cline"], JOINED, 2),
    **{cli: (rel, b"u\n\n" + BLOCK, 1) for cli, rel in SHARED.items()},
}
STEPS = {cli: ["aside", "proof"] for cli in CASES}
STEPS.update({cli: ["temp", "aside", "proof", "publish"] for cli in SHARED})
DONE = {cli: b"u\n" if cli in SHARED else None for cli in CASES}  # Once it's done.
# What the file and its aside hold right after a step is interrupted:
# "legacy" (0.3.x's bytes), "done" (DONE), or None (nothing there).
CTRL_C = {
    "before": ("legacy", None),
    "temp": ("legacy", None),
    "aside": ("legacy", None),
    "proof": ("legacy", None),
    "publish": ("done", "legacy"),
}
KILL = {
    "temp": ("legacy", None),
    "aside": (None, "legacy"),
    "proof": (None, "legacy"),
    "publish": ("done", "legacy"),
}
AFTER_KILL = {"aside": "E39", "proof": "E39", "publish": "E38"}  # The next run.


def holds(path):
    """What ``path`` and its aside hold, or None."""
    aside = path.with_name(sg._V03_ASIDE + path.name)
    return tuple(p.read_bytes() if p.exists() else None for p in (path, aside))


def expect(cli, row):
    return tuple({"legacy": CASES[cli][1], "done": DONE[cli]}.get(x) for x in row)


def hooks(monkeypatch, cli, step, act):
    """Call ``act`` once, right after ``step`` of the move-aside protocol (or before
    the move for "before"); every call after that is the real one."""
    path, fired = at(CASES[cli][0]), []
    name = path.name
    real_ren, real_read = sg._rename_excl, sg._read_regular
    real_unlink, real_fsync = os.unlink, os.fsync

    def once(now):
        if now and not fired:
            fired.append(step)
            act()

    def ren(src, dest, fd=None):
        moving = Path(src).name == name and Path(dest).name.startswith(sg._V03_ASIDE)
        once(step == "before" and moving)
        real_ren(src, dest, fd)
        once(step == "aside" and moving)
        once(step == "publish" and Path(src).name.endswith(".tmp"))

    def read(p, limit, fd=None):
        data = real_read(p, limit, fd)
        once(step == "proof" and Path(p).name.startswith(sg._V03_ASIDE))
        return data

    def unlink(p, *a, **k):
        real_unlink(p, *a, **k)
        once(step == "unlink" and Path(p).name == sg._V03_ASIDE + name)

    def fsync(fd):
        real_fsync(fd)
        once(step == "temp" and any(n.endswith(".tmp") for n in asides(path.parent)))

    monkeypatch.setattr(sg, "_rename_excl", ren)
    monkeypatch.setattr(sg, "_read_regular", read)
    monkeypatch.setattr(os, "unlink", unlink)
    monkeypatch.setattr(os, "fsync", fsync)
    return fired


class TestLinkedFolders:
    @POSIX
    @pytest.mark.parametrize(
        "cli,depth", [(c, i) for c, (_, _, n) in CASES.items() for i in range(1, n + 1)]
    )
    def test_a_link_at_any_folder_keeps_the_file(self, tmp_path, capsys, cli, depth):
        rel, data, _ = CASES[cli]
        path = at(rel)
        seed({path: data})
        link, real = relink(tmp_path, "/".join(rel.split("/")[:depth]))
        legacy = real.joinpath(*rel.split("/")[depth:])
        before = open_fds()
        install(tmp_path, cli)
        assert open_fds() == before
        assert link.is_symlink() and legacy.read_bytes() == data
        assert not list(real.rglob(sg._V03_ASIDE + "*"))
        why = f"{link} is a link, which deepctl doesn't follow"
        text, what = err(capsys), "that content yourself"
        if cli in SHARED:  # Only the marker lines, as E34 says.
            what = "only the lines from '<!-- BEGIN deepctl CLI Reference' to '<!-- END"
            what += " deepctl CLI Reference -->' yourself and keep the rest of the file"
        assert note("E40", path=path, why=sg._V03Link(0, why), what=what) in text
        assert str(path) not in legacy_paths(cli)
        install(tmp_path, cli)
        assert err(capsys) == ""
        assert legacy.read_bytes() == data

    @POSIX
    @pytest.mark.parametrize("cli", ["cursor", "gemini"])
    def test_home_itself_a_link_still_cleans(self, tmp_path, monkeypatch, cli):
        real = Path.home()
        alias = tmp_path / "home-link"
        alias.symlink_to(real, target_is_directory=True)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: alias))
        seed({at(CASES[cli][0]): CASES[cli][1]})
        install(tmp_path, cli)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: real))
        assert holds(at(CASES[cli][0])) == expect(cli, ("done", None))

    @POSIX
    @pytest.mark.parametrize("cli", ["cursor", "gemini"])
    def test_a_folder_swapped_for_a_link_after_the_walk_is_not_followed(
        self, tmp_path, monkeypatch, cli
    ):
        rel, data, _ = CASES[cli]
        top = rel.split("/")[0]
        seed({at(rel): data})
        victim, moved = tmp_path / "victim", tmp_path / "moved"
        (victim / rel).parent.mkdir(parents=True)
        (victim / rel).write_bytes(data)
        real_walk = sg._V03Dir.walk

        def walk(d):
            real_walk(d)
            if d.parts[0] == top and not moved.exists():  # Right after the check.
                at(top).rename(moved)
                at(top).symlink_to(victim / top, target_is_directory=True)

        monkeypatch.setattr(sg._V03Dir, "walk", walk)
        install(tmp_path, cli)
        assert (victim / rel).read_bytes() == data
        assert os.listdir((victim / rel).parent) == [Path(rel).name]
        at(top).unlink()
        moved.rename(at(top))
        assert holds(at(rel)) == expect(
            cli, ("done", None)
        )  # In the folder it checked.

    @POSIX
    @pytest.mark.parametrize("cli", ["cursor", "gemini"])
    def test_a_folder_swapped_for_a_link_between_its_check_and_open_fails_closed(
        self, tmp_path, capsys, monkeypatch, cli
    ):
        rel, data, _ = CASES[cli]
        top = rel.split("/")[0]
        seed({at(rel): data})
        victim, moved = tmp_path / "victim", tmp_path / "moved"
        (victim / rel).parent.mkdir(parents=True)
        (victim / rel).write_bytes(data)
        real = os.lstat

        def lstat(p, *a, **k):
            st = real(p, *a, **k)
            if Path(p) == at(top) and not moved.exists():  # Checked: now swap it.
                at(top).rename(moved)
                at(top).symlink_to(victim / top, target_is_directory=True)
            return st

        monkeypatch.setattr(os, "lstat", lstat)
        install(tmp_path, cli)
        assert (victim / rel).read_bytes() == data  # O_NOFOLLOW: never opened.
        assert os.listdir((victim / rel).parent) == [Path(rel).name]
        assert moved.joinpath(*rel.split("/")[1:]).read_bytes() == data

    def test_a_file_in_place_of_a_folder_is_left_alone(self, tmp_path, capsys):
        path = at(STANDALONE["cursor"])
        seed({path: JOINED})
        shutil.rmtree(path.parent)
        path.parent.write_bytes(b"mine")
        install(tmp_path, "cursor")
        assert path.parent.read_bytes() == b"mine"
        assert err(capsys) == ""

    def test_a_failed_publish_puts_it_back_even_if_the_temp_is_gone(
        self, tmp_path, capsys, monkeypatch
    ):
        path, real = at(SHARED["gemini"]), sg._rename_excl
        seed({path: b"u\n\n" + BLOCK})

        def ren(src, dest, fd=None):  # The temp vanishes, then the publish fails.
            if str(src).endswith(".tmp"):
                os.remove(path.parent / Path(src).name)
                raise OSError(5, "Input/output error")
            return real(src, dest, fd)

        monkeypatch.setattr(sg, "_rename_excl", ren)
        install(tmp_path, "gemini")
        assert holds(path) == (b"u\n\n" + BLOCK, None)
        text = err(capsys)
        assert f"{path} is unchanged and still recorded" in text
        assert legacy_paths("gemini") == [str(path)]

    @pytest.mark.skipif(os.name == "nt", reason="the Windows branch uses os.rename")
    def test_rename_excl_names_relative_to_the_cwd_or_a_folder_fd(
        self, tmp_path, monkeypatch
    ):
        (tmp_path / "cwd").mkdir()
        monkeypatch.chdir(tmp_path / "cwd")
        Path("a").write_bytes(b"a")
        Path("c").write_bytes(b"c")
        sg._rename_excl("a", "b")  # AT_FDCWD: -2 on macOS, -100 on Linux.
        with pytest.raises(FileExistsError):
            sg._rename_excl("b", "c")
        fd = os.open(tmp_path / "cwd", os.O_RDONLY)
        try:
            sg._rename_excl("b", "d", fd)
            with pytest.raises(FileExistsError):
                sg._rename_excl("d", "c", fd)
        finally:
            os.close(fd)
        assert sorted(os.listdir()) == ["c", "d"]
        assert Path("d").read_bytes() == b"a" and Path("c").read_bytes() == b"c"


class TestInterrupted:
    @pytest.mark.parametrize(
        "cli,step", [(c, s) for c in CASES for s in ["before", *STEPS[c]]]
    )
    def test_ctrl_c_at_each_step_loses_nothing(
        self, tmp_path, capsys, monkeypatch, cli, step
    ):
        path = at(CASES[cli][0])
        seed({path: CASES[cli][1]})

        def ctrl_c():
            raise KeyboardInterrupt

        fired = hooks(monkeypatch, cli, step, ctrl_c)
        with pytest.raises(KeyboardInterrupt):
            install(tmp_path, cli)
        assert fired == [step]
        text = err(capsys)
        assert holds(path) == expect(cli, CTRL_C[step])
        assert not [n for n in asides(path.parent) if n.endswith(".tmp")]
        aside = path.with_name(sg._V03_ASIDE + path.name)
        assert note("E4", dest=path, aside=aside) not in text  # Published: E38 next.
        assert note("E37", dest=path, aside=aside) not in text  # Nor a put-back try.
        install(tmp_path, cli)  # The next run finishes, or names what it kept.
        assert holds(path) == expect(cli, ("done", CTRL_C[step][1]))
        assert (note("E38", dest=path, aside=aside) in err(capsys)) is (
            step == "publish"
        )

    @POSIX
    @pytest.mark.parametrize("sig", ["SIGKILL", "SIGTERM", "SIGHUP"])
    @pytest.mark.parametrize("cli,step", [(c, s) for c in CASES for s in STEPS[c]])
    def test_killed_at_each_step_the_next_run_recovers(
        self, tmp_path, capsys, monkeypatch, sig, cli, step
    ):
        path, signum = at(CASES[cli][0]), getattr(signal, sig)
        aside = path.with_name(sg._V03_ASIDE + path.name)
        seed({path: CASES[cli][1]})
        pid = os.fork()
        if pid == 0:  # The child dies at the step: no except or finally runs.
            try:
                if signum != signal.SIGKILL:  # Which can't have a handler.
                    signal.signal(signum, signal.SIG_DFL)
                hooks(monkeypatch, cli, step, lambda: os.kill(os.getpid(), signum))
                install(tmp_path, cli)
            finally:
                os._exit(3)
        _, status = os.waitpid(pid, 0)
        assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signum
        capsys.readouterr()
        assert holds(path) == expect(cli, KILL[step])
        stale = [n for n in asides(path.parent) if n.endswith(".tmp")]
        assert len(stale) == (
            step in ("temp", "aside", "proof") and "temp" in STEPS[cli]
        )
        install(tmp_path, cli)
        text = err(capsys)
        assert holds(path) == expect(
            cli, ("done", KILL[step][1] if step == "publish" else None)
        )
        temps = [n for n in asides(path.parent) if n.endswith(".tmp")]
        assert temps == stale  # The README says what a leftover temp is.
        for key in ("E38", "E39"):
            assert (note(key, dest=path, aside=aside) in text) is (
                AFTER_KILL.get(step) == key
            )

    def test_a_file_and_its_aside_are_both_kept_and_named_each_run(
        self, tmp_path, capsys
    ):
        path = at(STANDALONE["cursor"])
        aside = path.with_name(sg._V03_ASIDE + path.name)
        seed({path: JOINED})
        aside.write_bytes(b"earlier")
        for _ in range(2):
            install(tmp_path, "cursor")
            assert note("E38", dest=path, aside=aside) in err(capsys)
            assert path.read_bytes() == JOINED and aside.read_bytes() == b"earlier"

    def test_the_put_back_never_replaces_a_file_that_appears(
        self, tmp_path, capsys, monkeypatch
    ):
        path = at(STANDALONE["cursor"])
        aside = path.with_name(sg._V03_ASIDE + path.name)
        seed({path: JOINED})
        path.rename(aside)
        real, refused = sg._rename_excl, []

        def ren(src, dest, fd=None):
            if Path(src).name == aside.name:
                path.write_bytes(b"new")  # Saved right before the put-back.
            try:
                real(src, dest, fd)
            except OSError as exc:  # The OS's own text: Windows words it differently.
                refused.append(exc)
                raise

        monkeypatch.setattr(sg, "_rename_excl", ren)
        install(tmp_path, "cursor")
        assert path.read_bytes() == b"new" and aside.read_bytes() == JOINED
        text = err(capsys)
        assert note("E35b", path=path, reason=sg._reason(refused[0])) in text
        assert "is unchanged" not in text
        assert legacy_paths("cursor") == [str(path)]

    def test_a_failed_put_back_of_an_interrupted_run_is_e35b(
        self, tmp_path, capsys, monkeypatch
    ):
        path = at(STANDALONE["cursor"])
        aside = path.with_name(sg._V03_ASIDE + path.name)
        seed({path: JOINED})
        path.rename(aside)  # A killed run left it here.
        real = sg._rename_excl

        def ren(src, dest, fd=None):
            if Path(src).name == aside.name:
                raise PermissionError(13, "Permission denied")
            real(src, dest, fd)

        monkeypatch.setattr(sg, "_rename_excl", ren)
        install(tmp_path, "cursor")
        assert not path.exists() and aside.read_bytes() == JOINED
        text = err(capsys)
        assert note("E35b", path=path, reason="Permission denied") in text
        assert "is unchanged" not in text and "put it back" not in text
        assert legacy_paths("cursor") == [str(path)]

    @pytest.mark.parametrize("kind", ["folder", pytest.param("link", marks=POSIX)])
    def test_an_aside_that_is_not_a_file_is_not_put_back(self, tmp_path, capsys, kind):
        path = at(STANDALONE["cursor"])
        aside = path.with_name(sg._V03_ASIDE + path.name)
        seed({path: JOINED})
        target = tmp_path / "target"
        path.rename(target)
        if kind == "folder":
            aside.mkdir()
        else:
            aside.symlink_to(target)
        install(tmp_path, "cursor")
        assert not path.exists() and target.read_bytes() == JOINED
        assert aside.is_dir() if kind == "folder" else aside.is_symlink()
        assert err(capsys) == ""


class TestSharedRaces:
    @POSIX
    @pytest.mark.parametrize("step", ["temp", "proof"])
    def test_a_temp_swapped_for_a_link_is_never_followed_or_published(
        self, tmp_path, capsys, monkeypatch, step
    ):
        path = at(SHARED["gemini"])
        seed({path: b"u\n\n" + BLOCK})
        victim = tmp_path / "victim"
        victim.write_bytes(b"secret")
        victim.chmod(0o600)
        os.utime(victim, ns=(1_500_000_000_000_000_000, 1_500_000_000_000_000_000))
        before = os.stat(victim)

        def swap():  # Right before the chmod ("temp") or the publish ("proof").
            (tmp,) = [n for n in asides(path.parent) if n.endswith(".tmp")]
            os.remove(path.parent / tmp)
            (path.parent / tmp).symlink_to(victim)

        hooks(monkeypatch, "gemini", step, swap)
        install(tmp_path, "gemini")
        after = os.stat(victim)
        assert (after.st_mode, after.st_mtime_ns) == (
            before.st_mode,
            before.st_mtime_ns,
        )
        assert victim.read_bytes() == b"secret"
        assert not path.is_symlink() and holds(path) == (b"u\n\n" + BLOCK, None)
        assert asides(path.parent) == []  # The swapped-in link is gone too.
        text = err(capsys)  # GEMINI.md itself never changed.
        assert "(deepctl's temporary copy of it was replaced)" in text
        assert "it changed while deepctl was editing it" not in text

    def test_a_save_between_the_proof_and_the_publish_is_kept(
        self, tmp_path, capsys, monkeypatch
    ):
        path = at(SHARED["gemini"])
        aside = path.with_name(sg._V03_ASIDE + path.name)
        seed({path: b"u\n\n" + BLOCK})

        def save():  # An editor saves atomically: a temp file renamed over the name.
            (path.parent / "editor.swp").write_bytes(b"EDITOR\n")
            os.replace(path.parent / "editor.swp", path)

        hooks(monkeypatch, "gemini", "proof", save)
        install(tmp_path, "gemini")
        text = err(capsys)
        assert holds(path) == (b"EDITOR\n", b"u\n\n" + BLOCK)
        assert asides(path.parent) == [aside.name]  # No temp left.
        assert note("E37", dest=path, aside=aside) in text
        assert "move it back by hand" not in text  # E4 would undo the save.
        assert "can't safely remove its 0.3.x section" not in text  # E34 contradicts.
        install(tmp_path, "gemini")
        assert note("E38", dest=path, aside=aside) in err(capsys)
        assert holds(path) == (b"EDITOR\n", b"u\n\n" + BLOCK)

    @POSIX
    @pytest.mark.parametrize(
        ("cli", "path", "data", "active"),
        [
            ("cursor", STANDALONE["cursor"], JOINED, None),
            ("gemini", SHARED["gemini"], b"u\n\n" + BLOCK, b"u\n"),
        ],
    )
    def test_a_write_through_an_open_descriptor_stays_recoverable(
        self, tmp_path, capsys, cli, path, data, active
    ):
        path = at(path)
        seed({path: data})
        with open(path, "r+b") as writer:
            install(tmp_path, cli)
            writer.seek(0)
            writer.write(b"LATE")
            writer.flush()
            os.fsync(writer.fileno())
        backups = kept(path.parent)
        assert (path.read_bytes() if path.exists() else None) == active
        assert len(backups) == 1
        assert (path.parent / backups[0]).read_bytes().startswith(b"LATE")
        assert "kept the original file" in err(capsys)

    @POSIX
    def test_a_refused_publish_puts_it_back_and_leaves_no_fd(
        self, tmp_path, capsys, monkeypatch
    ):
        path = at(SHARED["gemini"])
        seed({path: b"u\n\n" + BLOCK})
        before, real = open_fds(), sg._rename_excl

        def ren(src, dest, fd=None):
            if Path(src).name.endswith(".tmp"):
                raise PermissionError(13, "Permission denied")
            return real(src, dest, fd)

        monkeypatch.setattr(sg, "_rename_excl", ren)
        install(tmp_path, "gemini")
        assert open_fds() == before
        assert holds(path) == (b"u\n\n" + BLOCK, None) and asides(path.parent) == []
        assert note("E35", path=path, reason="Permission denied") in err(capsys)
        assert legacy_paths("gemini") == [str(path)]
        monkeypatch.setattr(sg, "_rename_excl", real)
        install(tmp_path, "gemini")
        assert open_fds() == before
        assert holds(path) == (b"u\n", None)

    def test_a_full_disk_while_writing_the_temp_leaves_the_file(
        self, tmp_path, capsys, monkeypatch
    ):
        path = at(SHARED["gemini"])
        seed({path: b"u\n\n" + BLOCK})

        def full():
            raise OSError(28, "No space left on device")

        hooks(monkeypatch, "gemini", "temp", full)
        install(tmp_path, "gemini")
        assert holds(path) == (b"u\n\n" + BLOCK, None) and asides(path.parent) == []
        reason = "No space left on device"
        text = err(capsys)
        assert note("E35", path=path, reason=reason) in text
        assert f"{path} is unchanged and still recorded" in text
        assert legacy_paths("gemini") == [str(path)]

    def test_a_published_cut_keeps_the_original_copy(self, tmp_path, capsys):
        path = at(SHARED["gemini"])
        seed({path: b"u\n\n" + BLOCK})
        install(tmp_path, "gemini")
        text = err(capsys)
        backups = kept(path.parent)
        assert path.read_bytes() == b"u\n"
        assert len(backups) == 1
        assert (path.parent / backups[0]).read_bytes() == b"u\n\n" + BLOCK
        assert f"Removed the deepctl 0.3.x section from {path}" in text
        assert "kept the original file" in text

    def test_a_removed_section_only_file_keeps_the_original_copy(
        self, tmp_path, capsys
    ):
        path = at(SHARED["codex"])
        seed({path: BLOCK})
        install(tmp_path, "codex")
        backups = kept(path.parent)
        assert not path.exists()
        assert len(backups) == 1
        assert (path.parent / backups[0]).read_bytes() == BLOCK
        assert "kept the original file" in err(capsys)
