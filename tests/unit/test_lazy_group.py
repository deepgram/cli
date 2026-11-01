"""The lazy root group: commands load on use, plugins on demand.

Subprocess tests run the real entry point in a fresh interpreter, the way
the console script does, with a throwaway HOME.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import click
import pytest

from deepctl import _cli, _manifest

ENTRY = "import sys; from deepctl.main import main; sys.exit(main())"
IMPORT_LINE = re.compile(r"^import time:\s+\d+\s+\|\s+\d+\s+\|\s?(\s*)(\S+)\s*$")


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("DEEPGRAM_", "PYTHON", "DEEPCTL_"))
    }
    env.update(
        HOME=str(tmp_path),
        DEEPCTL_TELEMETRY_DISABLED="1",
        DEEPCTL_TELEMETRY_DSN="http://k@127.0.0.1:9/1",
        CI="1",
    )
    env.update(extra)
    return env


def _dg(
    args: list[str], tmp_path: Path, *, module: bool = False, **env: str
) -> subprocess.CompletedProcess[str]:
    argv = (
        [sys.executable, "-m", "deepctl", *args]
        if module
        else [sys.executable, "-c", ENTRY, *args]
    )
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        env=_env(tmp_path, **env),
        stdin=subprocess.DEVNULL,
        timeout=120,
    )


def _imported(args: list[str], tmp_path: Path) -> list[str]:
    proc = subprocess.run(
        [sys.executable, "-X", "importtime", "-c", ENTRY, *args],
        capture_output=True,
        text=True,
        env=_env(tmp_path),
        stdin=subprocess.DEVNULL,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return [m.group(2) for m in map(IMPORT_LINE.match, proc.stderr.splitlines()) if m]


@pytest.fixture
def fresh_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _cli.LazyGroup:
    """``cli`` as a new process sees it: nothing resolved or discovered, and
    an empty cache (never the user's own) for the discovery and help caches."""
    from deepctl import _cache

    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.setattr(_cache, "_real_home", lambda: str(home))
    group = _cli.cli
    monkeypatch.setattr(group, "commands", {})
    monkeypatch.setattr(group, "eager", False)
    monkeypatch.setattr(group, "_discovered", False)
    monkeypatch.setattr(group, "_manager", None)
    return group


def _invoke(group: click.Group, args: list[str]) -> None:
    try:
        group.main(args=args, prog_name="dg", standalone_mode=False)
    except click.exceptions.Exit:
        pass


class TestDiscovery:
    """Plugins are discovered only for unknown names, --help, plugin list
    and an allowed override."""

    @pytest.mark.parametrize(
        ("args", "discovers"),
        [
            (["models", "--help"], False),
            (["debug-audio", "--help"], False),
            (["--help"], True),
            (["plugin", "list", "--help"], True),
        ],
    )
    def test_when_discovery_runs(
        self,
        fresh_cli: _cli.LazyGroup,
        args: list[str],
        discovers: bool,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        with patch(
            "deepctl_core.plugin_manager.PluginManager.discover_plugins"
        ) as discover:
            _invoke(fresh_cli, args)
        assert discover.called is discovers
        capsys.readouterr()

    def test_unknown_name_discovers(self, fresh_cli: _cli.LazyGroup) -> None:
        with patch(
            "deepctl_core.plugin_manager.PluginManager.discover_plugins"
        ) as discover:
            with pytest.raises(click.UsageError, match="No such command"):
                fresh_cli.main(args=["nosuchcmd"], standalone_mode=False)
        discover.assert_called_once()

    def test_allowed_override_discovers_first(self, fresh_cli: _cli.LazyGroup) -> None:
        plugin = click.Command("models", short_help="Plugin models")

        def register(self: Any, group: click.Group) -> None:
            group.add_command(plugin, name="models")

        with (
            patch(
                "deepctl_core._plugin_trust.allow_override_names",
                return_value=frozenset({"models"}),
            ),
            patch(
                "deepctl_core.plugin_manager.PluginManager.discover_plugins", register
            ),
        ):
            ctx = click.Context(fresh_cli)
            assert fresh_cli.get_command(ctx, "models") is plugin

    def test_builtin_never_loads_through_an_entry_point(
        self, fresh_cli: _cli.LazyGroup
    ) -> None:
        with patch("importlib.metadata.entry_points") as entry_points:
            ctx = click.Context(fresh_cli)
            command = fresh_cli.get_command(ctx, "models")
        assert command is not None and command.name == "models"
        entry_points.assert_not_called()

    def test_listing_comes_from_the_manifest(self, fresh_cli: _cli.LazyGroup) -> None:
        ctx = click.Context(fresh_cli)
        names = fresh_cli.list_commands(ctx)
        assert set(_manifest.top_level()) <= set(names)
        assert fresh_cli.commands == {}


class TestDependencyMissing:
    @pytest.fixture
    def ghost(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        entry = {
            "module": "deepctl_cmd_ghost.command",
            "attr": "GhostCommand",
            "dist": "deepctl-cmd-ghost",
            "summary": "A command whose package is gone",
            "hidden": False,
            "requires_auth": False,
        }
        commands = dict(_manifest.commands(), ghost=entry)
        monkeypatch.setattr(_manifest, "commands", lambda: commands)
        return entry

    def test_human(
        self, fresh_cli: _cli.LazyGroup, ghost: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with (
            patch("deepctl_core.output.is_agentic", return_value=False),
            patch("deepctl_core.output.is_ai_agent", return_value=False),
        ):
            with pytest.raises(SystemExit) as exc:
                _cli.run(["ghost"], prog="dg")
        assert exc.value.code == 1
        out = capsys.readouterr()
        assert "dg ghost needs deepctl-cmd-ghost, which isn't installed." in out.err
        assert "Reinstall dg with the tool you installed it with." in out.err
        assert "Traceback" not in out.err

    def test_agent_hint(
        self, fresh_cli: _cli.LazyGroup, ghost: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch("deepctl_core.output.is_ai_agent", return_value=True):
            with pytest.raises(SystemExit) as exc:
                _cli.run(["-o", "json", "ghost"], prog="dg")
        assert exc.value.code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["hint"] == (
            "Tell the user dg needs deepctl-cmd-ghost. "
            "Suggested fix: Reinstall dg with the tool you installed it with."
        )

    def test_json(
        self, fresh_cli: _cli.LazyGroup, ghost: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            _cli.run(["-o", "json", "ghost", "--help"], prog="dg")
        assert exc.value.code == 1
        payload = json.loads(capsys.readouterr().out)
        assert list(payload) == [
            "error",
            "status",
            "code",
            "hint",
            "next_step",
            "schema_version",
            "details",
        ]
        assert payload["code"] == "dependency_missing"
        assert payload["next_step"] is None
        assert payload["details"] == {"dependency": "deepctl-cmd-ghost"}

    def test_transitive_module_is_named(self) -> None:
        error = ImportError("No module named 'ffmpeg'", name="ffmpeg")
        entry = {"module": "deepctl_cmd_debug_audio.command", "dist": "x"}
        assert _cli._missing_dependency(error, entry) == "ffmpeg"
        own = ImportError("gone", name="deepctl_cmd_debug_audio")
        assert _cli._missing_dependency(own, entry) == "x"

    def test_missing_subcommand(
        self,
        fresh_cli: _cli.LazyGroup,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        entry = {
            "module": "deepctl_cmd_debug_ghost.command",
            "attr": "GhostCommand",
            "dist": "deepctl-cmd-debug-ghost",
            "summary": "Gone",
            "hidden": False,
            "requires_auth": False,
        }
        commands = dict(_manifest.commands(), **{"debug ghost": entry})
        monkeypatch.setattr(_manifest, "commands", lambda: commands)
        with pytest.raises(SystemExit) as exc:
            _cli.run(["debug", "ghost"], prog="dg")
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "dg debug ghost needs deepctl-cmd-debug-ghost" in err


class TestSubprocess:
    def test_root_help_equals_eager(self, tmp_path: Path) -> None:
        lazy = _dg(["--help"], tmp_path)
        eager = _dg(["--help"], tmp_path, DEEPCTL_EAGER_LOAD="1")
        assert lazy.returncode == eager.returncode == 0
        assert lazy.stdout == eager.stdout
        listed = re.findall(r"^  ([a-z][a-z0-9-]*)  ", lazy.stdout, re.M)
        assert len(listed) >= 24
        assert "transcribe" not in listed  # hidden
        assert not any(name.startswith("debug-") for name in listed)

    @pytest.mark.parametrize(
        "args", [["models", "--help"], ["debug-audio", "--help"], ["listen", "--help"]]
    )
    def test_subcommand_help_equals_eager(
        self, tmp_path: Path, args: list[str]
    ) -> None:
        lazy = _dg(args, tmp_path)
        eager = _dg(args, tmp_path, DEEPCTL_EAGER_LOAD="1")
        assert lazy.returncode == eager.returncode == 0
        assert lazy.stdout == eager.stdout

    def test_command_help_imports_no_command(self, tmp_path: Path) -> None:
        """A built-in's help renders from its help spec: no command module."""
        modules = _imported(["models", "--help"], tmp_path)
        assert not [m for m in modules if m.startswith("deepctl_cmd_")]

    def test_running_a_command_imports_only_that_command(self, tmp_path: Path) -> None:
        modules = _imported(["models", "--agent-friendly"], tmp_path)
        commands = {m.split(".")[0] for m in modules if m.startswith("deepctl_cmd_")}
        # The startup checks a run (not help) starts live in these two.
        commands -= {"deepctl_cmd_update", "deepctl_cmd_skills"}
        assert commands == {"deepctl_cmd_models"}

    def test_dash_m_usage_names_dg(self, tmp_path: Path) -> None:
        # CI=1 (from _env): agent mode, two prefixed lines and no Usage line.
        proc = _dg(["nosuchcmd"], tmp_path, module=True)
        assert proc.returncode == 1
        assert proc.stderr.splitlines() == [
            "ERROR: No such command 'nosuchcmd'.",
            "HINT: Run dg --help to see valid options.",
        ]

    def test_dash_m_usage_names_dg_for_people(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("NO_COLOR", raising=False)
        proc = _dg(["nosuchcmd"], tmp_path, module=True, CI="", TERM="xterm")
        assert proc.returncode == 1
        assert proc.stderr.startswith("Usage: dg [OPTIONS] COMMAND [ARGS]...\n")
        assert "Try 'dg --help' for help." in proc.stderr
        assert "No such command 'nosuchcmd'" in proc.stderr

    def test_prog_name_in_agent_usage_hint(self, tmp_path: Path) -> None:
        proc = _dg(["models", "--bogus"], tmp_path, DEEPCTL_PROG_NAME="foo")
        assert proc.returncode == 1
        assert proc.stdout == ""
        assert proc.stderr.splitlines()[1:] == [
            "HINT: Run foo models --help to see valid options."
        ]

    def test_prog_name_env(self, tmp_path: Path) -> None:
        proc = _dg(["--help"], tmp_path, DEEPCTL_PROG_NAME="foo")
        assert proc.stdout.startswith("Usage: foo [OPTIONS] COMMAND [ARGS]...")
        version = _dg(["--version"], tmp_path, DEEPCTL_PROG_NAME="foo")
        assert version.stdout.startswith("deepctl, version ")

    def test_alias_help(self, tmp_path: Path) -> None:
        proc = _dg(["debug-audio", "--help"], tmp_path)
        assert proc.returncode == 0
        assert proc.stdout.startswith("Usage: ")
        assert " debug audio [OPTIONS]" in proc.stdout.splitlines()[0]


class TestAliasesAreReserved:
    """A plugin can't claim a hidden alias such as ``debug-audio``."""

    def test_admit_refuses_an_alias(self) -> None:
        from types import SimpleNamespace

        from deepctl_core.plugin_manager import BUILTIN_COMMAND_MODULES, PluginManager

        manager = PluginManager()
        entry_point = SimpleNamespace(value="evil:Cmd", dist=None, name="x")
        with patch.object(manager, "_warn_shadow") as warn:
            kind = manager._admit(
                "debug-audio", entry_point, BUILTIN_COMMAND_MODULES, frozenset()
            )
        assert kind is None
        warn.assert_called_once_with("debug-audio", entry_point)
        manager._allow_override = frozenset({"debug-audio"})
        assert (
            manager._admit(
                "debug-audio", entry_point, BUILTIN_COMMAND_MODULES, frozenset()
            )
            == "override"
        )

    def test_eager_alias_runs_the_builtin(
        self, fresh_cli: _cli.LazyGroup, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Even with a command of that name loaded (eager mode), the alias
        resolves to the built-in path first."""
        monkeypatch.setattr(fresh_cli, "eager", True)
        fresh_cli.commands["debug-audio"] = click.Command("debug-audio")
        with patch(
            "deepctl_core._plugin_trust.allow_override_names",
            return_value=frozenset(),
        ):
            assert fresh_cli._expand_command_word(
                click.Context(fresh_cli), ["debug-audio", "-f", "x.wav"]
            ) == ["debug", "audio", "-f", "x.wav"]
        with patch(
            "deepctl_core._plugin_trust.allow_override_names",
            return_value=frozenset({"debug-audio"}),
        ):
            assert fresh_cli._expand_command_word(
                click.Context(fresh_cli), ["debug-audio"]
            ) == ["debug-audio"]


class TestHyphenatedSubcommands:
    """``dg <group>-<x>`` runs ``dg <group> <x>`` for any subcommand of a
    built-in group, a plugin's included (0.4.2 parity)."""

    @pytest.fixture
    def debug_with_extra(
        self, fresh_cli: _cli.LazyGroup, monkeypatch: pytest.MonkeyPatch
    ) -> _cli.LazyGroup:
        group = click.Group("debug")
        group.add_command(click.Command("extra"))
        group.add_command(click.Command("audio"))
        fresh_cli.commands["debug"] = group
        return fresh_cli

    @pytest.mark.parametrize(
        ("args", "expected"),
        [
            (["debug-extra"], ["debug", "extra"]),
            (["debug-extra", "--x", "debug-y"], ["debug", "extra", "--x", "debug-y"]),
            (["debug-nosuch"], ["debug-nosuch"]),
            (["debug-"], ["debug-"]),
            (["listen-now"], ["listen-now"]),
            (["transcribe", "debug-call.wav"], ["transcribe", "debug-call.wav"]),
        ],
    )
    def test_rewrite(
        self, debug_with_extra: _cli.LazyGroup, args: list[str], expected: list[str]
    ) -> None:
        ctx = click.Context(debug_with_extra)
        assert debug_with_extra._expand_command_word(ctx, args) == expected

    def test_groups_come_from_the_manifest(self) -> None:
        assert _manifest.groups() == {"debug", "help", "plugin", "skills"}

    def test_hot_paths_never_scan_subcommand_entry_points(
        self,
        fresh_cli: _cli.LazyGroup,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from importlib import metadata

        seen: list[str] = []
        real = metadata.EntryPoints.select

        def spy(self: Any, **params: Any) -> Any:
            seen.append(str(params.get("group")))
            return real(self, **params)

        monkeypatch.setattr(metadata.EntryPoints, "select", spy)
        monkeypatch.setattr(sys, "argv", ["dg", "--version"])
        from deepctl.main import main

        main()
        for args in (["--help"], ["login", "--help"]):
            try:
                _cli.run(args, prog="dg")
            except SystemExit as exc:
                assert not exc.code
        capsys.readouterr()
        assert not [g for g in seen if g.startswith("deepctl.subcommands.")], seen


class TestCompletion:
    def test_plugins_complete(self, tmp_path: Path) -> None:
        """Completion offers plugin commands, as 0.4.x did."""
        proc = _dg(
            [],
            tmp_path,
            DEEPCTL_PROG_NAME="dg",
            _DG_COMPLETE="bash_complete",
            COMP_WORDS="dg ex",
            COMP_CWORD="1",
        )
        assert proc.stdout.splitlines() == ["plain,example"], proc.stderr


class TestHumanDependencyMessage:
    def test_prog_is_not_markup(
        self,
        fresh_cli: _cli.LazyGroup,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """DEEPCTL_PROG_NAME is shown as text on stderr."""
        error = _cli.DependencyMissingError("[bold]x[/bold]", "models", "pkg")
        with (
            patch("deepctl_core.output.is_agentic", return_value=False),
            patch("deepctl_core.output.is_ai_agent", return_value=False),
        ):
            error.show()
        err = capsys.readouterr().err
        assert "[bold]x[/bold] models needs pkg" in err
        assert "Reinstall [bold]x[/bold]" in err


class TestDashMSafePath:
    def test_cwd_plugins_are_ignored(self, tmp_path: Path) -> None:
        """`python -m deepctl` doesn't load a plugin planted in the cwd."""
        work = tmp_path / "work"
        work.mkdir()
        sentinel = tmp_path / "IMPORTED"
        (work / "cwdplug.py").write_text(
            "import pathlib\n"
            f"pathlib.Path({str(sentinel)!r}).write_text('x')\n"
            "from deepctl_core import BaseCommand\n"
            "class Cmd(BaseCommand):\n"
            "    name = 'cwd-evil'\n    help = 'planted'\n"
            "    def handle(self, *a, **k):\n        print('CWD PLUGIN RAN')\n"
        )
        info = work / "cwdplug-1.0.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: cwdplug\nVersion: 1.0\n"
        )
        (info / "entry_points.txt").write_text(
            "[deepctl.plugins]\ncwd-evil = cwdplug:Cmd\n"
        )
        for args in (["--help"], ["cwd-evil"]):
            proc = subprocess.run(
                [sys.executable, "-m", "deepctl", *args],
                capture_output=True,
                text=True,
                cwd=work,
                env=_env(tmp_path),
                stdin=subprocess.DEVNULL,
                timeout=120,
            )
            assert "cwd-evil" not in proc.stdout or args != ["--help"]
            assert "CWD PLUGIN RAN" not in proc.stdout
        assert not sentinel.exists()


class TestOverrideWithoutPlugin:
    """allow_override naming an alias or a name that no plugin took
    still runs the built-in, lazily and eagerly."""

    @pytest.mark.parametrize("eager", ["", "1"])
    @pytest.mark.parametrize(
        ("args", "usage"),
        [
            (["debug-audio", "--help"], " debug audio [OPTIONS]"),
            (["login", "--help"], " login [OPTIONS]"),
        ],
    )
    def test_builtin_runs(
        self, tmp_path: Path, eager: str, args: list[str], usage: str
    ) -> None:
        config_dir = tmp_path / "config" / "deepctl"
        config_dir.mkdir(parents=True, mode=0o700)
        os.chmod(tmp_path / "config", 0o700)
        config = config_dir / "config.yaml"
        config.write_text("plugins:\n  allow_override: [debug-audio, login]\n")
        os.chmod(config, 0o600)
        extra = {"XDG_CONFIG_HOME": str(tmp_path / "config")}
        if eager:
            extra["DEEPCTL_EAGER_LOAD"] = eager
        proc = _dg(args, tmp_path, **extra)
        assert proc.returncode == 0, proc.stderr
        assert usage in proc.stdout.splitlines()[0]


class TestGroupDistMissing:
    def test_hyphen_word_falls_through(
        self, fresh_cli: _cli.LazyGroup, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A plugin named <group>-<x> isn't hidden when the group's
        dist is missing; the word is left for plugin discovery."""

        def missing(ctx: click.Context, name: str) -> click.Command | None:
            raise _cli.DependencyMissingError("dg", name, f"deepctl-cmd-{name}")

        monkeypatch.setattr(fresh_cli, "get_command", missing)
        ctx = click.Context(fresh_cli)
        assert fresh_cli._expand_command_word(ctx, ["debug-foo"]) == ["debug-foo"]


class TestHelpFromSpec:
    """`dg <cmd> --help` and `dg <group> <sub> --help` for a built-in leaf
    render from its help spec, without importing the command."""

    def _resolved(self, group: _cli.LazyGroup, args: list[str]) -> click.Command:
        ctx = click.Context(group, info_name="dg")
        _, command, _ = group.resolve_command(ctx, list(args))
        assert command is not None
        return command

    @pytest.mark.parametrize(
        "args", [["listen", "--help"], ["debug", "probe", "--help"]]
    )
    def test_spec_command_has_no_callback(
        self, fresh_cli: _cli.LazyGroup, args: list[str]
    ) -> None:
        with patch.object(_cli.LazyGroup, "_load_builtin") as load:
            command = self._resolved(fresh_cli, args)
        load.assert_not_called()
        assert fresh_cli.help_from_spec is not None
        leaf = command.commands[args[1]] if len(args) == 3 else command  # type: ignore[attr-defined]
        assert leaf.callback is None
        assert command.callback is None
        assert fresh_cli.commands == {}

    @pytest.mark.parametrize(
        "args",
        [
            ["listen"],
            ["listen", "--help", "x"],
            ["listen", "-o", "json", "--help"],
            ["debug", "--help"],
            ["debug", "toolkit", "--help"],
            ["debug-audio", "--help"],
            ["plugin", "install", "--help"],
            ["nosuch", "--help"],
            ["--help"],
        ],
    )
    def test_other_argv_takes_the_full_path(
        self, fresh_cli: _cli.LazyGroup, args: list[str]
    ) -> None:
        ctx = click.Context(fresh_cli, info_name="dg")
        assert fresh_cli._help_command(ctx, args) is None

    def test_eager_load_takes_the_full_path(
        self, fresh_cli: _cli.LazyGroup, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_cli.EAGER_LOAD_ENV_VAR, "1")
        ctx = click.Context(fresh_cli, info_name="dg")
        assert fresh_cli._help_command(ctx, ["listen", "--help"]) is None

    @pytest.mark.parametrize(
        ("allowed", "args"),
        [
            ({"listen"}, ["listen", "--help"]),
            ({"debug probe"}, ["debug", "probe", "--help"]),
            ({"debug"}, ["debug", "probe", "--help"]),
        ],
    )
    def test_allowed_override_takes_the_full_path(
        self, fresh_cli: _cli.LazyGroup, allowed: set[str], args: list[str]
    ) -> None:
        with patch(
            "deepctl_core._plugin_trust.allow_override_names",
            return_value=frozenset(allowed),
        ):
            ctx = click.Context(fresh_cli, info_name="dg")
            assert fresh_cli._help_command(ctx, args) is None

    def test_foreign_subcommand_entry_point_takes_the_full_path(
        self, fresh_cli: _cli.LazyGroup, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from importlib import metadata

        from deepctl import _cache

        real = [ep for dist in metadata.distributions() for ep in dist.entry_points]
        assert _cli._only_builtin_subcommands("debug") is True
        for index, (group, name, value) in enumerate(
            [
                ("deepctl.subcommands.debug", "extra", "evil.cmd:Extra"),
                ("deepctl.subcommands.debug", "probe", "evil.cmd:Probe"),
                ("deepctl.subplugins.debug", "extra", "evil.cmd:Extra"),
            ]
        ):
            # Installing a distribution changes site-packages: a new stamp.
            monkeypatch.setattr(_cache, "plugin_stamp", lambda i=index: f"new-{i}")
            extra = metadata.EntryPoint(name=name, value=value, group=group)
            fake = metadata.EntryPoints([*real, extra])
            monkeypatch.setattr(metadata, "entry_points", lambda fake=fake: fake)
            assert _cli._only_builtin_subcommands("debug") is False
            ctx = click.Context(fresh_cli, info_name="dg")
            assert fresh_cli._help_command(ctx, ["debug", "probe", "--help"]) is None

    def test_builtin_only_scan_is_cached_per_stamp(
        self, fresh_cli: _cli.LazyGroup, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from importlib import metadata

        from deepctl import _cache

        scans: list[int] = []
        real_scan = _cli._scan_builtin_only

        def counted(group: str) -> bool:
            scans.append(1)
            return real_scan(group)

        monkeypatch.setattr(_cli, "_scan_builtin_only", counted)
        monkeypatch.setattr(_cache, "plugin_stamp", lambda: "stamp-1")
        assert _cli._only_builtin_subcommands("debug") is True
        assert _cli._only_builtin_subcommands("debug") is True
        assert len(scans) == 1
        # Another stamp scans again; a "no" is never kept.
        monkeypatch.setattr(_cache, "plugin_stamp", lambda: "stamp-2")
        extra = metadata.EntryPoint(
            name="x", value="evil:X", group="deepctl.subplugins.debug"
        )
        real = [ep for dist in metadata.distributions() for ep in dist.entry_points]
        fake = metadata.EntryPoints([*real, extra])
        monkeypatch.setattr(metadata, "entry_points", lambda: fake)
        assert _cli._only_builtin_subcommands("debug") is False
        assert _cli._only_builtin_subcommands("debug") is False
        assert len(scans) == 3
        directory = _cache._discovery_dir(create=False)
        assert directory is not None
        cached = json.loads(Path(_cache._subcommands_file(directory)).read_text())
        assert cached == {"stamp": "stamp-1", "builtin_only": ["debug"]}

    @pytest.mark.parametrize(
        "content",
        [
            "not json",
            '{"stamp": "s", "builtin_only": "debug"}',
            '{"stamp": "s", "builtin_only": [1]}',
            '{"stamp": "other", "builtin_only": ["debug"]}',
        ],
    )
    def test_builtin_only_cache_ignores_a_changed_file(
        self, fresh_cli: _cli.LazyGroup, content: str
    ) -> None:
        from deepctl import _cache

        directory = _cache._discovery_dir(create=True)
        assert directory is not None
        path = Path(_cache._subcommands_file(directory))
        path.write_text(content)
        path.chmod(0o600)
        assert _cache.read_builtin_only("s", "debug") is False
        path.chmod(0o666)
        path.write_text('{"stamp": "s", "builtin_only": ["debug"]}')
        assert _cache.read_builtin_only("s", "debug") is False  # others can write

    def test_no_spec_takes_the_full_path(
        self, fresh_cli: _cli.LazyGroup, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_manifest, "help_spec", lambda path: None)
        ctx = click.Context(fresh_cli, info_name="dg")
        assert fresh_cli._help_command(ctx, ["listen", "--help"]) is None

    def test_missing_module_still_prints_help(
        self,
        fresh_cli: _cli.LazyGroup,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A built-in whose package is gone still shows its help; running it
        reports dependency_missing."""
        gone = dict(_manifest.commands()["models"], module="deepctl_cmd_gone.command")
        commands = dict(_manifest.commands(), models=gone)
        monkeypatch.setattr(_manifest, "commands", lambda: commands)
        _cli.run(["models", "--help"], prog="dg")
        out = capsys.readouterr()
        assert out.out.startswith("Usage: dg models [OPTIONS]")
        with pytest.raises(SystemExit) as exc:
            _cli.run(["models"], prog="dg")
        assert exc.value.code == 1
        assert "dg models needs deepctl-cmd-models" in capsys.readouterr().err

    def test_root_callback_only_scans_the_config(
        self, fresh_cli: _cli.LazyGroup
    ) -> None:
        seen: dict[str, Any] = {}
        real = _cli.cli.callback
        assert real is not None

        def spy(**kwargs: Any) -> None:
            real(**kwargs)
            seen["config"] = click.get_current_context().obj["config"]

        with patch.object(_cli.cli, "callback", spy):
            _invoke(fresh_cli, ["listen", "--help"])
            assert seen["config"].scan_only is True
            _invoke(fresh_cli, ["debug", "--help"])
            assert seen["config"].scan_only is False

    def test_never_writes_the_help_cache(
        self,
        fresh_cli: _cli.LazyGroup,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import deepctl.main
        from deepctl import _cache

        monkeypatch.setattr(deepctl.main, "HELP_CACHE", True, raising=False)
        writes: list[Any] = []
        monkeypatch.setattr(_cache, "write_help", lambda *a: writes.append(a))
        with patch("deepctl_core.output.is_ai_agent", return_value=False):
            _cli.run(["listen", "--help"], prog="dg")
            _cli.run(["debug", "probe", "--help"], prog="dg")
            assert writes == []
            _cli.run(["--help"], prog="dg")  # the one help the cache holds
        assert len(writes) == 1
        capsys.readouterr()

    def test_a_later_run_in_the_process_resolves_normally(
        self, fresh_cli: _cli.LazyGroup
    ) -> None:
        self._resolved(fresh_cli, ["listen", "--help"])
        command = self._resolved(fresh_cli, ["listen", "x.wav"])
        assert command.callback is not None
        assert fresh_cli.help_from_spec is None

    def test_imports_no_command_module(self, tmp_path: Path) -> None:
        modules = _imported(["-q", "debug", "probe", "--help"], tmp_path)
        assert not [m for m in modules if m.startswith("deepctl_cmd_")]
        assert "deepctl_core.plugin_manager" not in modules
        assert "pydantic" not in modules


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0, reason="needs an unreadable HOME"
)
class TestUnreadableHomeSubcommandHelp:
    """`dg debug <sub> --help` with an unreadable HOME prints help:
    Click calls the group's callback first, which must not need a Config."""

    SUBS = sorted(_manifest.subcommands("debug"))

    @pytest.mark.parametrize("eager", ["", "1"], ids=["lazy", "eager"])
    @pytest.mark.parametrize("sub", SUBS)
    def test_exits_zero(self, sub: str, eager: str, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir(mode=0)
        try:
            proc = _dg(
                ["debug", sub, "--help"],
                tmp_path,
                HOME=str(home),
                DEEPCTL_EAGER_LOAD=eager,
            )
        finally:
            home.chmod(0o700)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.startswith("Usage: ")
        assert f" debug {sub} [OPTIONS]" in proc.stdout.splitlines()[0]
        # Eager mode loads plugins too: one line for their unreadable home.
        assert proc.stderr == "" or (
            eager
            and proc.stderr.startswith("WARN: Couldn't load plugins: ")
            and proc.stderr.count("\n") == 1
        )

    @pytest.mark.parametrize("sub", SUBS)
    def test_full_lazy_path(
        self,
        sub: str,
        fresh_cli: _cli.LazyGroup,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        home = tmp_path / "noperm"
        home.mkdir(mode=0)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setattr(_cli.LazyGroup, "_help_command", lambda *a: None)
        try:
            _cli.run(["debug", sub, "--help"], prog="dg")
        finally:
            home.chmod(0o700)
        assert capsys.readouterr().out.startswith(f"Usage: dg debug {sub} [OPTIONS]")

    def test_running_the_subcommand_still_fails(
        self,
        fresh_cli: _cli.LazyGroup,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``-f --help`` makes --help a value: the subcommand runs, and its
        own Config fails as before."""
        home = tmp_path / "noperm"
        home.mkdir(mode=0)
        monkeypatch.setenv("HOME", str(home))
        try:
            with pytest.raises(SystemExit) as exc:
                _cli.run(["debug", "audio", "-f", "--help"], prog="dg")
        finally:
            home.chmod(0o700)
        assert exc.value.code == 1
