"""Assemble ``deepctl/_manifest.json`` from the per-command fragments.

Each built-in command owns one fragment, ``deepctl/_manifest.d/<cmd>.json``
(``<parent>-<name>.json`` for a subcommand). ``assemble()`` checks every
fragment and the rules that span fragments, then builds the manifest that
``deepctl._manifest`` reads at run time:

- ``manifest_version``: the format version (``MANIFEST_VERSION``).
- ``commands``: command path (``"listen"``, ``"debug audio"``) to its import
  path, distribution, help summary and flags.
- ``aliases``: hidden alias (``"debug-audio"``) to the command path it runs.
- ``tools``: MCP tool name to command path (empty until a command declares
  one).

The JSON Schema is ``docs/contracts/manifest.schema.json``. This module is
stdlib only: it runs in the ``build_py`` hook (no third-party imports are
available there) and, from a source tree, at run time. ``python -m
deepctl._manifest_build [--check] [--out FILE] [FRAGMENT_DIR]`` runs it by
hand; ``--check`` validates without writing.

Each fragment can also carry a ``help`` block: its command's ``--help`` as
data (``deepctl._help_spec``). ``assemble()`` copies those blocks to
``deepctl/_help/<fragment>.json`` when given a ``help_dir`` (never into
``_manifest.json``, which root ``--help`` and ``dg mcp`` parse). The blocks
are written at dev time by ``--write-help``, which imports every command
through ``PluginManager``; ``--check`` then fails on a block that no longer
matches its command. Those two options, unlike ``assemble()``, need the
installed CLI.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

MANIFEST_VERSION = 1

FRAGMENT_DIR_NAME = "_manifest.d"
MANIFEST_NAME = "_manifest.json"
HELP_DIR_NAME = "_help"

# Command, alias and tool names: the plugin name rule (0.4.1), so a built-in
# name is always one a plugin could also try to claim.
NAME = re.compile(r"[a-z][a-z0-9-]{0,63}")
TOOL_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
MODULE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*")
ATTR = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
DIST = re.compile(r"[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?")

_REQUIRED = {
    "name": str,
    "module": str,
    "attr": str,
    "dist": str,
    "summary": str,
}
_OPTIONAL: dict[str, type | tuple[type, ...]] = {
    "parent": str,
    "hidden": bool,
    "group": bool,
    "requires_auth": bool,
    "deprecated": (bool, str),
    "aliases": list,
    "tools": list,
    "help": dict,
}


class ManifestError(ValueError):
    """A fragment, or the set of fragments, breaks a manifest rule."""


def fragment_dir() -> str:
    """The fragment directory next to this module."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), FRAGMENT_DIR_NAME)


def _check_fragment(file_name: str, data: Any) -> dict[str, Any]:
    where = f"{FRAGMENT_DIR_NAME}/{file_name}"
    if not isinstance(data, dict):
        raise ManifestError(f"{where}: a fragment must be a JSON object")
    unknown = sorted(set(data) - set(_REQUIRED) - set(_OPTIONAL))
    if unknown:
        raise ManifestError(f"{where}: unknown keys {unknown}")
    for key, kind in _REQUIRED.items():
        if not isinstance(data.get(key), kind) or not data[key]:
            raise ManifestError(f"{where}: '{key}' must be a non-empty string")
    for key, kinds in _OPTIONAL.items():
        if key in data and (
            not isinstance(data[key], kinds)
            # bool is an int subclass; keep "deprecated": 1 out
            or (kinds is str and not data[key])
        ):
            raise ManifestError(f"{where}: '{key}' has the wrong type")
    if data.get("deprecated") is False or data.get("deprecated") == "":
        raise ManifestError(f"{where}: 'deprecated' is true or a non-empty string")
    checks = (
        ("name", NAME),
        ("module", MODULE),
        ("attr", ATTR),
        ("dist", DIST),
    )
    for key, pattern in checks:
        if pattern.fullmatch(data[key]) is None:
            raise ManifestError(f"{where}: '{key}' {data[key]!r} is not valid")
    if "parent" in data and NAME.fullmatch(data["parent"]) is None:
        raise ManifestError(f"{where}: 'parent' {data['parent']!r} is not valid")
    for alias in data.get("aliases", []):
        if not isinstance(alias, str) or NAME.fullmatch(alias) is None:
            raise ManifestError(f"{where}: alias {alias!r} is not valid")
    for tool in data.get("tools", []):
        if not isinstance(tool, str) or TOOL_NAME.fullmatch(tool) is None:
            raise ManifestError(f"{where}: tool {tool!r} is not valid")
    if "help" in data:
        _check_help(where, data["help"], data["name"])
    if file_name != fragment_file_name(data):
        raise ManifestError(
            f"{where}: this fragment must be named {fragment_file_name(data)}"
        )
    return data


def fragment_file_name(fragment: dict[str, Any]) -> str:
    """``<name>.json``, or ``<parent>-<name>.json`` for a subcommand: the
    fragment's file name, and its help file's in ``_help/``."""
    if "parent" in fragment:
        return f"{fragment['parent']}-{fragment['name']}.json"
    return f"{fragment['name']}.json"


def _check_help(where: str, spec: Any, name: str) -> None:
    """The shape the runtime relies on; ``--check`` compares the content."""
    if not isinstance(spec, dict) or spec.get("name") != name:
        raise ManifestError(f"{where}: 'help' must be an object named {name!r}")
    if not isinstance(spec.get("params"), list) or not all(
        isinstance(p, dict) for p in spec["params"]
    ):
        raise ManifestError(f"{where}: 'help.params' must be a list of objects")


def load_fragments(directory: str | None = None) -> list[dict[str, Any]]:
    """Every fragment in ``directory``, checked one by one, in name order."""
    directory = directory or fragment_dir()
    fragments = []
    for file_name in sorted(os.listdir(directory)):
        if not file_name.endswith(".json"):
            continue
        with open(os.path.join(directory, file_name), encoding="utf-8") as f:
            try:
                data = json.load(f)
            except ValueError as exc:
                raise ManifestError(
                    f"{FRAGMENT_DIR_NAME}/{file_name}: not valid JSON ({exc})"
                ) from None
        fragments.append(_check_fragment(file_name, data))
    if not fragments:
        raise ManifestError(f"{directory}: no fragments")
    return fragments


def build(fragments: list[dict[str, Any]]) -> dict[str, Any]:
    """The manifest for ``fragments``; raises ``ManifestError`` on a
    duplicate command, alias or tool name, or a subcommand without its group."""
    commands: dict[str, dict[str, Any]] = {}
    top = {f["name"] for f in fragments if "parent" not in f}
    for fragment in fragments:
        parent = fragment.get("parent")
        path = f"{parent} {fragment['name']}" if parent else fragment["name"]
        if path in commands:
            raise ManifestError(f"duplicate command '{path}'")
        if parent is not None and parent not in top:
            raise ManifestError(f"'{path}': no fragment for its group '{parent}'")
        entry = {
            "module": fragment["module"],
            "attr": fragment["attr"],
            "dist": fragment["dist"],
            "summary": fragment["summary"],
            "hidden": fragment.get("hidden", False),
            "requires_auth": fragment.get("requires_auth", False),
        }
        if fragment.get("group"):
            entry["group"] = True
        if "deprecated" in fragment:
            entry["deprecated"] = fragment["deprecated"]
        commands[path] = entry

    aliases: dict[str, str] = {}
    tools: dict[str, str] = {}
    for fragment in fragments:
        parent = fragment.get("parent")
        path = f"{parent} {fragment['name']}" if parent else fragment["name"]
        for alias in fragment.get("aliases", []):
            if alias in aliases or alias in top:
                raise ManifestError(f"duplicate command or alias name '{alias}'")
            aliases[alias] = path
        for tool in fragment.get("tools", []):
            if tool in tools:
                raise ManifestError(f"duplicate tool name '{tool}'")
            tools[tool] = path

    return {
        "manifest_version": MANIFEST_VERSION,
        "commands": dict(sorted(commands.items())),
        "aliases": dict(sorted(aliases.items())),
        "tools": dict(sorted(tools.items())),
    }


def dumps(manifest: dict[str, Any]) -> str:
    """The manifest as written to ``_manifest.json`` (stable bytes)."""
    return json.dumps(manifest, indent=1, ensure_ascii=False, sort_keys=False) + "\n"


def _write_atomic(path: str, text: str) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def write_help_files(fragments: list[dict[str, Any]], help_dir: str) -> list[str]:
    """Write each fragment's ``help`` block to ``help_dir/<fragment>.json``
    and remove any other file there; returns the paths written."""
    os.makedirs(help_dir, exist_ok=True)
    written = []
    for fragment in fragments:
        if "help" not in fragment:
            continue
        path = os.path.join(help_dir, fragment_file_name(fragment))
        text = json.dumps(fragment["help"], ensure_ascii=False, separators=(",", ":"))
        _write_atomic(path, text + "\n")
        written.append(path)
    keep = {os.path.basename(p) for p in written}
    for file_name in os.listdir(help_dir):
        if file_name.endswith(".json") and file_name not in keep:
            os.remove(os.path.join(help_dir, file_name))
    return written


def assemble(
    directory: str | None = None,
    out_path: str | None = None,
    help_dir: str | None = None,
) -> dict[str, Any]:
    """Build the manifest from the fragments in ``directory``. When
    ``out_path`` is given, write it there atomically; when ``help_dir`` is,
    write the fragments' help specs there (``write_help_files``)."""
    fragments = load_fragments(directory)
    manifest = build(fragments)
    if out_path is not None:
        _write_atomic(out_path, dumps(manifest))
    if help_dir is not None:
        write_help_files(fragments, help_dir)
    return manifest


def dump_fragment(fragment: dict[str, Any]) -> str:
    """A fragment as stored in ``_manifest.d`` (stable bytes)."""
    return json.dumps(fragment, indent=2, ensure_ascii=False) + "\n"


def generate_help(directory: str | None = None) -> dict[str, dict[str, Any]]:
    """Fragment file name to the help spec of its command as installed.

    Each command is built the way ``dg`` builds it
    (``PluginManager._create_click_command``) and its spec is checked by
    rebuilding the command and rendering both (``_help_spec.verify``).
    Needs Click and the installed commands.
    """
    import importlib

    from deepctl_core.plugin_manager import PluginManager

    from deepctl import _help_spec

    manager = PluginManager()
    specs: dict[str, dict[str, Any]] = {}
    for fragment in load_fragments(directory):
        command_class = getattr(
            importlib.import_module(fragment["module"]), fragment["attr"]
        )
        command = manager._create_click_command(command_class())
        try:
            spec = _help_spec.to_spec(command)
            _help_spec.verify(command, spec)
        except _help_spec.SpecError as exc:
            raise ManifestError(
                f"{FRAGMENT_DIR_NAME}/{fragment_file_name(fragment)}: {exc}"
            ) from None
        specs[fragment_file_name(fragment)] = spec
    return specs


def stale_help(directory: str | None = None, *, write: bool = False) -> list[str]:
    """Fragments whose ``help`` block doesn't match their command. With
    ``write``, those fragments are rewritten with the current block."""
    directory = directory or fragment_dir()
    stale = []
    for file_name, spec in sorted(generate_help(directory).items()):
        path = os.path.join(directory, file_name)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("help") == spec:
            continue
        stale.append(file_name)
        if write:
            data["help"] = spec
            _write_atomic(path, dump_fragment(data))
    return stale


def __getattr__(name: str) -> Any:
    # ``BuildPy`` imports setuptools, so it's built only when the build
    # backend asks for it (``[tool.setuptools.cmdclass]``), never at run time.
    if name == "BuildPy":
        return _build_py_class()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _build_py_class() -> type:
    import importlib

    # setuptools is there only when a build backend runs this.
    build_py: Any = importlib.import_module("setuptools.command.build_py").build_py

    class BuildPy(build_py):  # type: ignore[misc]
        """``build_py`` that also writes ``deepctl/_manifest.json`` into the
        build tree, so every wheel (and a wheel built from the sdist)
        carries the assembled manifest. Editable installs read the
        fragments from the source tree instead."""

        def _manifest_path(self) -> str:
            return os.path.join(self.build_lib, "deepctl", MANIFEST_NAME)

        def _help_dir(self) -> str:
            return os.path.join(self.build_lib, "deepctl", HELP_DIR_NAME)

        def run(self) -> None:
            super().run()
            if getattr(self, "editable_mode", False):
                return
            os.makedirs(os.path.dirname(self._manifest_path()), exist_ok=True)
            assemble(fragment_dir(), self._manifest_path(), self._help_dir())

        def get_outputs(self, include_bytecode: bool = True) -> list[str]:
            outputs: list[str] = super().get_outputs(include_bytecode)
            if not getattr(self, "editable_mode", False):
                outputs.append(self._manifest_path())
                outputs.extend(
                    os.path.join(self._help_dir(), fragment_file_name(f))
                    for f in load_fragments(fragment_dir())
                    if "help" in f
                )
            return outputs

    return BuildPy


def _option_value(args: list[str], option: str) -> tuple[str | None, bool]:
    """Remove ``option VALUE`` from ``args``: ``(value, ok)``."""
    if option not in args:
        return None, True
    index = args.index(option)
    if index + 1 >= len(args):
        return None, False
    value = args[index + 1]
    del args[index : index + 2]
    return value, True


def _main(argv: list[str]) -> int:
    check = "--check" in argv
    write_help = "--write-help" in argv
    args = [a for a in argv if a not in ("--check", "--write-help")]
    out, ok = _option_value(args, "--out")
    if not ok:
        sys.stderr.write("manifest: --out needs a file name\n")
        return 2
    help_dir, ok = _option_value(args, "--help-dir")
    if not ok:
        sys.stderr.write("manifest: --help-dir needs a directory\n")
        return 2
    directory = args[0] if args else None
    try:
        if write_help:
            for file_name in stale_help(directory, write=True):
                sys.stderr.write(f"manifest: wrote the help block of {file_name}\n")
        manifest = assemble(
            directory,
            None if check else out,
            None if check else help_dir,
        )
        if check:
            stale = stale_help(directory)
            if stale:
                sys.stderr.write(
                    "manifest: help blocks out of date (run python -m "
                    f"deepctl._manifest_build --write-help): {', '.join(stale)}\n"
                )
                return 1
    except (ManifestError, OSError) as exc:
        sys.stderr.write(f"manifest: {exc}\n")
        return 1
    except ImportError as exc:
        sys.stderr.write(f"manifest: can't import a command to check help: {exc}\n")
        return 1
    if out is None and not check and not write_help:
        sys.stdout.write(dumps(manifest))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
