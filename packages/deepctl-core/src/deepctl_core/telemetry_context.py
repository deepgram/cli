"""Per-run command context that telemetry may attach to an error report.

SDK-free on purpose: it uses only the stdlib and Click, which every command
has already loaded. Commands record their context here on every run, and the
telemetry client reads it only if an error is reported.

Only the command path and the *names* of flags the user passed are kept.
Flag values, positional arguments and the program name from argv are never
recorded.
"""

from __future__ import annotations

import re

import click

# Names that look like identifiers. Anything else is dropped rather than
# escaped, so a crafted command or option name can't smuggle data out.
_TOKEN_RE = re.compile(r"^-{0,2}[A-Za-z][A-Za-z0-9_-]{0,63}$")

# Replaced as a whole on each record, never mutated, so a reader on another
# thread sees either the old or the new context, not a mix.
_context: dict[str, str] = {}


def _safe(token: str) -> str | None:
    return token if _TOKEN_RE.match(token) else None


def record_command(ctx: click.Context) -> None:
    """Record the command path and user-supplied flag names for this run."""
    chain: list[click.Context] = []
    node: click.Context | None = ctx
    while node is not None:
        chain.append(node)
        node = node.parent
    chain.reverse()

    # The root's info_name comes from argv[0]; always report it as `dg`.
    parts = ["dg"]
    for node in chain[1:]:
        name = _safe(node.info_name or "")
        if name:
            parts.append(name)

    flags: set[str] = set()
    for node in chain:
        for param in node.command.params:
            if not isinstance(param, click.Option) or not param.name:
                continue
            src = node.get_parameter_source(param.name)
            if src is None or src.name != "COMMANDLINE":
                continue
            long_opts = [o for o in param.opts if o.startswith("--")]
            flag = _safe((long_opts or list(param.opts) or [""])[0])
            if flag:
                flags.add(flag)

    global _context
    _context = {"command": " ".join(parts), "flags": ",".join(sorted(flags))}


def command_context() -> dict[str, str]:
    """Return a copy of the recorded context (empty before any command)."""
    return dict(_context)


def reset_command_context() -> None:
    """Forget the recorded context. Used by tests."""
    global _context
    _context = {}
