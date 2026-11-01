"""The command manifest: fragments, assembly, schema and drift.

CI runs these on every PR, so a fragment that breaks the schema or a rule
that spans fragments (a duplicate command, alias or tool name) fails there.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from deepctl import _manifest, _manifest_build
from deepctl._manifest_build import ManifestError, assemble, build, load_fragments

REPO = Path(__file__).resolve().parents[2]
SCHEMA = REPO / "docs" / "contracts" / "manifest.schema.json"
FRAGMENTS = REPO / "src" / "deepctl" / "_manifest.d"


def _fragment(name: str, **extra: Any) -> dict[str, Any]:
    data = {
        "name": name,
        "module": f"pkg_{name.replace('-', '_')}.command",
        "attr": "Command",
        "dist": f"pkg-{name}",
        "summary": f"The {name} command",
    }
    data.update(extra)
    return data


def _write(directory: Path, *fragments: dict[str, Any]) -> Path:
    directory.mkdir(exist_ok=True)
    for fragment in fragments:
        stem = (
            f"{fragment['parent']}-{fragment['name']}"
            if "parent" in fragment
            else fragment["name"]
        )
        (directory / f"{stem}.json").write_text(json.dumps(fragment))
    return directory


class TestRepoManifest:
    def test_assembles(self) -> None:
        manifest = assemble(str(FRAGMENTS))
        assert manifest["manifest_version"] == 1
        assert len([p for p in manifest["commands"] if " " not in p]) == 24

    def test_runtime_reads_the_same_manifest(self) -> None:
        assert _manifest.load() == assemble(str(FRAGMENTS))
        # From the source tree the fragments are used, never a stale file.
        assert _manifest.source == "fragments"

    def test_validates_against_the_schema(self) -> None:
        import jsonschema  # a test dependency: this must never skip

        schema = json.loads(SCHEMA.read_text())
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(assemble(str(FRAGMENTS)), schema)
        fragment_schema = {**schema["$defs"]["fragment"], "$defs": schema["$defs"]}
        for path in sorted(FRAGMENTS.glob("*.json")):
            jsonschema.validate(json.loads(path.read_text()), fragment_schema)

    def test_check_cli(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "deepctl._manifest_build", "--check"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr

    def test_builder_is_stdlib_only(self, tmp_path: Path) -> None:
        """It runs in the build_py hook, where only setuptools is installed."""
        code = (
            "import sys; before = set(sys.modules); "
            "import deepctl._manifest_build as b; "
            f"b.assemble(None, None, {str(tmp_path / '_help')!r}); "
            "new = {m.split('.')[0] for m in set(sys.modules) - before}; "
            "bad = sorted(m for m in new if m not in sys.stdlib_module_names "
            "and m != 'deepctl'); print(bad); sys.exit(1 if bad else 0)"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr


class TestDrift:
    """Every fragment matches the command class it names."""

    @pytest.mark.parametrize("path", sorted(_manifest.commands()))
    def test_fragment_matches_its_command(self, path: str) -> None:
        import importlib

        from deepctl_core.plugin_manager import PluginManager

        entry = _manifest.commands()[path]
        command_class = getattr(importlib.import_module(entry["module"]), entry["attr"])
        instance = command_class()
        assert instance.name == path.split(" ")[-1]
        assert bool(getattr(instance, "requires_auth", False)) is entry["requires_auth"]
        click_command = PluginManager()._create_click_command(instance)
        assert click_command.short_help == entry["summary"]
        assert bool(click_command.hidden) is entry["hidden"]
        assert (click_command.deprecated or False) == entry.get("deprecated", False)
        if " " not in path:
            import click

            assert bool(entry.get("group")) is isinstance(click_command, click.Group)
            if isinstance(click_command, click.Group):
                assert path in _manifest.groups()

    def test_every_first_party_entry_point_has_a_fragment(self) -> None:
        """Reverse drift: a built-in declared in a workspace pyproject but
        missing from the manifest would be unreserved (a plugin could take
        its name), so the two sets must be equal."""
        try:
            import tomllib
        except ModuleNotFoundError:  # Python 3.10
            import tomli as tomllib

        declared: dict[str, str] = {}
        for pyproject in sorted(REPO.glob("packages/deepctl-cmd-*/pyproject.toml")):
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            groups = data.get("project", {}).get("entry-points", {})
            for group, entries in groups.items():
                if group == "deepctl.commands":
                    prefix = ""
                elif group.startswith("deepctl.subcommands."):
                    prefix = group.removeprefix("deepctl.subcommands.") + " "
                else:
                    continue
                for name, value in entries.items():
                    declared[f"{prefix}{name}"] = value.replace(" ", "")
        manifest = {
            path: f"{entry['module']}:{entry['attr']}"
            for path, entry in _manifest.commands().items()
        }
        assert declared == manifest

    def test_each_dist_declares_its_command(self) -> None:
        """The named distribution declares the same entry point (entry points
        stay declared for now), so ``dependency_missing`` names the right
        package."""
        from importlib import metadata

        for path, entry in _manifest.commands().items():
            group, _, name = path.rpartition(" ")
            ep_group = f"deepctl.subcommands.{group}" if group else "deepctl.commands"
            declared = {
                (ep.group, ep.name): ep.value
                for ep in metadata.distribution(entry["dist"]).entry_points
            }
            assert declared.get((ep_group, name)) == (
                f"{entry['module']}:{entry['attr']}"
            ), path


class TestAssembleRules:
    def test_duplicate_command_fails(self, tmp_path: Path) -> None:
        # Same command path from two fragments (a subcommand fragment can't
        # share a file name with the top-level one, so use the group twice).
        fragments = [_fragment("a"), _fragment("a")]
        with pytest.raises(ManifestError, match="duplicate command 'a'"):
            build(fragments)

    def test_duplicate_alias_across_fragments_fails(self, tmp_path: Path) -> None:
        directory = _write(
            tmp_path / "d",
            _fragment("a", aliases=["shared"]),
            _fragment("b", aliases=["shared"]),
        )
        with pytest.raises(ManifestError, match="duplicate command or alias name"):
            assemble(str(directory))

    def test_alias_that_is_a_command_name_fails(self, tmp_path: Path) -> None:
        directory = _write(
            tmp_path / "d", _fragment("a", aliases=["b"]), _fragment("b")
        )
        with pytest.raises(ManifestError, match="'b'"):
            assemble(str(directory))

    def test_duplicate_tool_across_fragments_fails(self, tmp_path: Path) -> None:
        directory = _write(
            tmp_path / "d",
            _fragment("a", tools=["transcribe_file"]),
            _fragment("b", tools=["transcribe_file"]),
        )
        with pytest.raises(ManifestError, match="duplicate tool name"):
            assemble(str(directory))

    def test_subcommand_needs_its_group(self, tmp_path: Path) -> None:
        directory = _write(tmp_path / "d", _fragment("x", parent="nogroup"))
        with pytest.raises(ManifestError, match="no fragment for its group"):
            assemble(str(directory))

    def test_file_name_must_match(self, tmp_path: Path) -> None:
        directory = tmp_path / "d"
        directory.mkdir()
        (directory / "other.json").write_text(json.dumps(_fragment("a")))
        with pytest.raises(ManifestError, match="must be named a.json"):
            assemble(str(directory))

    @pytest.mark.parametrize(
        "bad",
        [
            {"name": "Bad"},
            {"name": "a_b"},
            {"module": "not a module"},
            {"summary": ""},
            {"hidden": "yes"},
            {"deprecated": False},
            {"aliases": ["Debug-Audio"]},
            {"unknown": 1},
        ],
    )
    def test_bad_fragment_fails(self, tmp_path: Path, bad: dict[str, Any]) -> None:
        fragment = {**_fragment("a"), **bad}
        directory = tmp_path / "d"
        directory.mkdir()
        (directory / f"{fragment['name']}.json").write_text(json.dumps(fragment))
        with pytest.raises(ManifestError):
            assemble(str(directory))

    def test_assemble_writes_the_file(self, tmp_path: Path) -> None:
        out = tmp_path / "_manifest.json"
        manifest = assemble(str(FRAGMENTS), str(out))
        assert json.loads(out.read_text()) == manifest
        assert out.read_text() == _manifest_build.dumps(manifest)

    def test_cli_check_fails_on_a_duplicate(self, tmp_path: Path) -> None:
        directory = _write(
            tmp_path / "d",
            _fragment("a", aliases=["shared"]),
            _fragment("b", aliases=["shared"]),
        )
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "deepctl._manifest_build",
                "--check",
                str(directory),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 1
        assert "duplicate" in proc.stderr


class TestHelpSpecs:
    """Each fragment's ``help`` block is its command's --help as data."""

    def test_every_help_block_is_fresh(self) -> None:
        """The drift check CI runs on every PR: each block matches its
        command as installed, and rebuilds to the same help text."""
        assert _manifest_build.stale_help(str(FRAGMENTS)) == []

    def test_every_fragment_has_a_help_block(self) -> None:
        assert all("help" in f for f in load_fragments(str(FRAGMENTS)))

    def test_help_stays_out_of_the_manifest(self) -> None:
        text = _manifest_build.dumps(assemble(str(FRAGMENTS)))
        assert '"params"' not in text
        assert '"help"' not in text

    def _check(self, directory: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "deepctl._manifest_build",
                "--check",
                str(directory),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_check_fails_on_a_stale_help_block(self, tmp_path: Path) -> None:
        directory = tmp_path / "d"
        shutil.copytree(FRAGMENTS, directory)
        assert self._check(directory).returncode == 0
        listen = directory / "listen.json"
        data = json.loads(listen.read_text(encoding="utf-8"))
        data["help"]["params"][0]["help"] = "An older description"
        listen.write_text(_manifest_build.dump_fragment(data), encoding="utf-8")
        proc = self._check(directory)
        assert proc.returncode == 1
        assert "help blocks out of date" in proc.stderr
        assert "listen.json" in proc.stderr

    def test_check_fails_on_a_missing_help_block(self, tmp_path: Path) -> None:
        directory = tmp_path / "d"
        shutil.copytree(FRAGMENTS, directory)
        speak = directory / "speak.json"
        data = json.loads(speak.read_text(encoding="utf-8"))
        del data["help"]
        speak.write_text(_manifest_build.dump_fragment(data), encoding="utf-8")
        proc = self._check(directory)
        assert proc.returncode == 1
        assert "speak.json" in proc.stderr

    def test_write_help_restores_the_block(self, tmp_path: Path) -> None:
        directory = tmp_path / "d"
        shutil.copytree(FRAGMENTS, directory)
        listen = directory / "listen.json"
        data = json.loads(listen.read_text(encoding="utf-8"))
        data["help"]["help"] = "stale"
        listen.write_text(_manifest_build.dump_fragment(data), encoding="utf-8")
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "deepctl._manifest_build",
                "--write-help",
                str(directory),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stderr == "manifest: wrote the help block of listen.json\n"
        assert listen.read_bytes() == (FRAGMENTS / "listen.json").read_bytes()

    def test_assemble_writes_one_help_file_per_command(self, tmp_path: Path) -> None:
        help_dir = tmp_path / "_help"
        help_dir.mkdir()
        (help_dir / "removed-command.json").write_text("{}")
        assemble(str(FRAGMENTS), None, str(help_dir))
        fragments = load_fragments(str(FRAGMENTS))
        files = sorted(p.name for p in help_dir.iterdir())
        assert files == sorted(_manifest_build.fragment_file_name(f) for f in fragments)
        for fragment in fragments:
            path = help_dir / _manifest_build.fragment_file_name(fragment)
            assert json.loads(path.read_text(encoding="utf-8")) == fragment["help"]

    @pytest.mark.parametrize(
        "bad",
        [
            [],
            {"name": "other", "params": []},
            {"name": "a", "params": "x"},
            {"name": "a", "params": [1]},
        ],
    )
    def test_bad_help_block_fails(self, tmp_path: Path, bad: Any) -> None:
        directory = _write(tmp_path / "d", _fragment("a", help=bad))
        with pytest.raises(ManifestError, match="'help"):
            assemble(str(directory))

    def test_help_dir_without_a_value(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "deepctl._manifest_build", "--help-dir"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 2
        assert proc.stderr == "manifest: --help-dir needs a directory\n"


class TestSpecFormat:
    """``to_spec`` keeps only what help rendering reads, and refuses what it
    can't carry rather than writing an incomplete spec."""

    def _round_trip(self, command: Any) -> None:
        from deepctl import _help_spec

        spec = _help_spec.to_spec(command)
        assert json.loads(json.dumps(spec)) == spec
        _help_spec.verify(command, spec)

    def test_types_round_trip(self) -> None:
        import click

        @click.command()
        @click.argument("src", type=click.Path(exists=True, dir_okay=False))
        @click.argument("rest", nargs=-1, required=False)
        @click.option("--level", type=click.IntRange(0, 9, clamp=True))
        @click.option("--ratio", type=click.FloatRange(0.0, 1.0, max_open=True))
        @click.option("--mode", type=click.Choice(["a", "b"], case_sensitive=False))
        @click.option("--out", type=click.File("w"), metavar="FILE")
        @click.option("--id", type=click.UUID, required=True)
        @click.option("--color/--no-color", "-c/-C", help="Colour")
        @click.option("-v", "--verbose", count=True, hidden=True)
        @click.option("--tag", multiple=True, help="Repeatable")
        @click.option("--conv", type=int, help="A converter")
        def command(**kwargs: Any) -> None:
            """Do things.

            \b
            Kept   as is.
            """

        self._round_trip(command)

    def test_function_type_round_trips(self) -> None:
        import click

        def parse_pair(value: str) -> tuple[str, str]:
            return tuple(value.split("=", 1))  # type: ignore[return-value]

        command = click.Command(
            "pair",
            params=[
                click.Option(["--pair"], type=click.types.FuncParamType(parse_pair))
            ],
        )
        self._round_trip(command)

    @pytest.mark.parametrize(
        "option",
        [
            {"show_default": True, "default": "x"},
            {"envvar": "DG_X"},
            {"prompt": True},
            {"show_choices": False, "type": ["a", "b"]},
        ],
    )
    def test_refuses_what_help_would_show_differently(
        self, option: dict[str, Any]
    ) -> None:
        import click

        from deepctl import _help_spec

        kwargs = dict(option)
        if isinstance(kwargs.get("type"), list):
            kwargs["type"] = click.Choice(kwargs["type"])
        command = click.Command("x", params=[click.Option(["--x"], **kwargs)])
        with pytest.raises(_help_spec.SpecError):
            _help_spec.to_spec(command)

    def test_refuses_a_custom_command_class(self) -> None:
        import click

        from deepctl import _help_spec

        class Custom(click.Command):
            pass

        with pytest.raises(_help_spec.SpecError):
            _help_spec.to_spec(Custom("x"))

    def test_verify_catches_help_the_spec_does_not_carry(self) -> None:
        """The spec round-trips, but the real command renders something the
        spec has no field for: verify() compares the rendered help too."""
        import click

        from deepctl import _help_spec

        command = click.Command("x", help="Does x.", params=[click.Option(["--y"])])
        spec = _help_spec.to_spec(command)
        _help_spec.verify(command, spec)  # the same help: passes

        def format_epilog(ctx: click.Context, formatter: click.HelpFormatter) -> None:
            formatter.write_paragraph()
            formatter.write_text("Written by a method the spec can't see.")

        command.format_epilog = format_epilog  # type: ignore[method-assign]
        assert _help_spec.to_spec(command) == spec  # still round-trips
        with pytest.raises(_help_spec.SpecError, match="help differs at width 50"):
            _help_spec.verify(command, spec)

    def test_rebuilt_command_has_no_callback(self) -> None:
        from deepctl import _help_spec

        spec = load_fragments(str(FRAGMENTS))[0]["help"]
        assert _help_spec.from_spec(spec).callback is None


class TestBuilderCli:
    def test_out_without_a_value(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "deepctl._manifest_build", "--out"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 2
        assert proc.stderr == "manifest: --out needs a file name\n"


class TestUnavailable:
    """A missing or broken manifest is one line and exit 1, not a traceback."""

    @pytest.fixture
    def package(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        package = tmp_path / "deepctl"
        package.mkdir()
        monkeypatch.setattr(_manifest, "_HERE", str(package))
        monkeypatch.setattr(_manifest, "_manifest", None)
        monkeypatch.setattr(_manifest, "source", None)
        return package

    def test_missing(self, package: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc:
            _manifest.load()
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert err.startswith("dg: command manifest missing or invalid (No such file")
        assert err.endswith("); reinstall deepctl\n")
        assert err.count("\n") == 1

    @pytest.mark.parametrize("text", ["{not json", '{"manifest_version": 2}', "[]"])
    def test_invalid(
        self, package: Path, text: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (package / "_manifest.json").write_text(text)
        with pytest.raises(SystemExit) as exc:
            _manifest.load()
        assert exc.value.code == 1
        assert capsys.readouterr().err.startswith(
            "dg: command manifest missing or invalid ("
        )

    def test_bad_fragment(
        self, package: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (package / "_manifest.d").mkdir()
        (package / "_manifest.d" / "x.json").write_text('{"name": "y"}')
        with pytest.raises(SystemExit):
            _manifest.load()
        assert "reinstall deepctl" in capsys.readouterr().err

    def test_subprocess_has_no_traceback(self, tmp_path: Path) -> None:
        code = (
            "import deepctl._manifest as m; m._HERE = '/nonexistent'; "
            "import sys; sys.argv = ['dg', 'models']; "
            "from deepctl.main import main; main()"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            env={
                **os.environ,
                "HOME": str(tmp_path),
                "DEEPCTL_TELEMETRY_DISABLED": "1",
            },
        )
        assert proc.returncode == 1
        assert "Traceback" not in proc.stderr
        assert "dg: command manifest missing or invalid (" in proc.stderr


class TestBuiltFile:
    def test_built_file_wins_without_fragments(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An installed wheel has _manifest.json and no _manifest.d/."""
        package = tmp_path / "deepctl"
        package.mkdir()
        assemble(str(FRAGMENTS), str(package / "_manifest.json"))
        monkeypatch.setattr(_manifest, "_HERE", str(package))
        monkeypatch.setattr(_manifest, "_manifest", None)
        monkeypatch.setattr(_manifest, "source", None)
        assert _manifest.load() == assemble(str(FRAGMENTS))
        assert _manifest.source == "built"

    def test_built_help_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        package = tmp_path / "deepctl"
        package.mkdir()
        assemble(
            str(FRAGMENTS), str(package / "_manifest.json"), str(package / "_help")
        )
        monkeypatch.setattr(_manifest, "_HERE", str(package))
        monkeypatch.setattr(_manifest, "_manifest", None)
        monkeypatch.setattr(_manifest, "source", None)
        listen = json.loads((FRAGMENTS / "listen.json").read_text())["help"]
        probe = json.loads((FRAGMENTS / "debug-probe.json").read_text())["help"]
        assert _manifest.help_spec("listen") == listen
        assert _manifest.help_spec("debug probe") == probe
        assert _manifest.help_spec("nosuch") is None
        (package / "_help" / "speak.json").write_text("{not json")
        assert _manifest.help_spec("speak") is None
        (package / "_help" / "models.json").write_text('{"name": "other"}')
        assert _manifest.help_spec("models") is None

    def test_fragments_win_in_a_source_tree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        package = tmp_path / "deepctl"
        shutil.copytree(FRAGMENTS, package / "_manifest.d")
        (package / "_manifest.json").write_text('{"stale": true}')
        monkeypatch.setattr(_manifest, "_HERE", str(package))
        monkeypatch.setattr(_manifest, "_manifest", None)
        assert _manifest.load() == assemble(str(FRAGMENTS))
        assert _manifest.source == "fragments"
        listen = json.loads((FRAGMENTS / "listen.json").read_text())["help"]
        assert _manifest.help_spec("listen") == listen


def test_load_fragments_ignores_other_files(tmp_path: Path) -> None:
    directory = _write(tmp_path / "d", _fragment("a"))
    (directory / "README").write_text("not a fragment")
    assert [f["name"] for f in load_fragments(str(directory))] == ["a"]
