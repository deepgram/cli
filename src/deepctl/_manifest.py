"""The command manifest at run time.

``load()`` returns the manifest ``deepctl._manifest_build`` describes. An
installed wheel carries ``deepctl/_manifest.json``, written by the build
hook, and that file is read as is. A source tree (an editable install, unit
CI) carries the fragments in ``deepctl/_manifest.d/`` instead; they are
assembled in memory on each run, so the manifest can't go stale while the
fragments are edited. ``source`` records which one was used ("built" or
"fragments"), so a test can show a wheel never takes the fallback.

Stdlib only: ``dg --help`` reads this before Click formats anything. A
missing or unreadable manifest ends the run with one line on stderr and
exit 1 (a broken install), never a traceback.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))

# "built" or "fragments" once load() has run.
source: str | None = None
_manifest: dict[str, Any] | None = None


def load() -> dict[str, Any]:
    """The manifest, read once per process."""
    global _manifest, source
    if _manifest is not None:
        return _manifest
    fragments = os.path.join(_HERE, "_manifest.d")
    try:
        if os.path.isdir(fragments):
            from deepctl._manifest_build import assemble

            manifest, kind = assemble(fragments), "fragments"
        else:
            with open(os.path.join(_HERE, "_manifest.json"), encoding="utf-8") as f:
                manifest, kind = json.load(f), "built"
        _check(manifest)
    except (OSError, ValueError) as exc:
        _unavailable(exc)
    _manifest, source = manifest, kind
    return manifest


def _check(manifest: Any) -> None:
    """The shape every reader relies on (format 1)."""
    if not isinstance(manifest, dict) or manifest.get("manifest_version") != 1:
        raise ValueError("unknown manifest format")
    for key in ("commands", "aliases", "tools"):
        if not isinstance(manifest.get(key), dict):
            raise ValueError(f"no '{key}' table")


def _unavailable(exc: BaseException) -> None:
    """End the run: without the manifest dg has no commands."""
    if isinstance(exc, OSError) and exc.strerror:
        reason = f"{exc.strerror}: {exc.filename}" if exc.filename else exc.strerror
    else:
        reason = str(exc) or type(exc).__name__
    try:
        sys.stderr.write(
            f"dg: command manifest missing or invalid ({reason}); reinstall deepctl\n"
        )
        sys.stderr.flush()
    except (OSError, ValueError):
        pass
    raise SystemExit(1)


def commands() -> dict[str, dict[str, Any]]:
    """Command path (``"listen"``, ``"debug audio"``) to its entry."""
    result: dict[str, dict[str, Any]] = load()["commands"]
    return result


def top_level() -> dict[str, dict[str, Any]]:
    """The top-level commands, by name."""
    return {path: entry for path, entry in commands().items() if " " not in path}


def subcommands(group: str) -> dict[str, dict[str, Any]]:
    """The built-in subcommands of ``group``, by name."""
    prefix = f"{group} "
    return {
        path[len(prefix) :]: entry
        for path, entry in commands().items()
        if path.startswith(prefix)
    }


def aliases() -> dict[str, list[str]]:
    """Hidden alias to the argv words it stands for."""
    return {alias: path.split(" ") for alias, path in load()["aliases"].items()}


def groups() -> set[str]:
    """Top-level commands that are groups: they have built-in subcommands,
    or their fragment says so (plugins can add subcommands to them)."""
    names = {path.partition(" ")[0] for path in commands() if " " in path}
    names |= {name for name, entry in top_level().items() if entry.get("group")}
    return names


def builtin_command_modules() -> dict[str, str]:
    """Built-in command name to the one module it may be loaded from."""
    return {name: entry["module"] for name, entry in top_level().items()}


def builtin_subcommand_modules() -> dict[str, dict[str, str]]:
    """Built-in group name to its subcommands' modules, by name."""
    groups: dict[str, dict[str, str]] = {}
    for path, entry in commands().items():
        group, sep, name = path.partition(" ")
        if sep:
            groups.setdefault(group, {})[name] = entry["module"]
    return groups


def help_spec(path: str) -> dict[str, Any] | None:
    """The help spec of the built-in at ``path`` (``"listen"``, ``"debug
    audio"``), or ``None`` when there is none or it can't be read.

    An installed wheel has one file per command in ``deepctl/_help/``; a
    source tree reads the ``help`` block of the command's fragment. Neither
    is ever parsed for root ``--help``.
    """
    load()
    group, _, name = path.rpartition(" ")
    file_name = f"{group}-{name}.json" if group else f"{name}.json"
    try:
        if source == "fragments":
            with open(
                os.path.join(_HERE, "_manifest.d", file_name), encoding="utf-8"
            ) as f:
                data = json.load(f)
            spec = data.get("help") if isinstance(data, dict) else None
        else:
            with open(os.path.join(_HERE, "_help", file_name), encoding="utf-8") as f:
                spec = json.load(f)
    except (OSError, ValueError):
        return None
    return spec if isinstance(spec, dict) and spec.get("name") == name else None
