"""Every advertised example must actually parse.

Regression cover for #105: `dg usage` shipped three examples, two of which the
command could not parse (`--days`, `--start`/`--end` against real options
`--start-date`/`--end-date`). The help text contradicted its own options list
eight lines further down.

This matters beyond `--help`. The same `examples` array is what
`--agent-friendly` emits, so an agent asking the CLI how to use itself was
handed commands that fail. A sweep at the time this test was written found
four broken examples across three commands, so the class needed a gate rather
than three fixes.

Parsing only -- `parse_args` resolves the command and validates the options
without invoking the handler, so nothing here touches the network.
"""

from __future__ import annotations

import re
import shlex
from importlib import metadata
from pathlib import Path

import click
import pytest

# Entry point groups that carry command classes.
COMMAND_GROUPS = ["deepctl.commands", "deepctl.subcommands.debug"]

BINARY_NAMES = ("dg", "deepctl", "deepgram")

# Groups whose subcommands are built at runtime from state this test cannot
# see, so their examples are unverifiable rather than wrong. The value is the
# set of subcommands that *are* statically present, and those stay checked --
# exempting the whole prefix would have excused `dg debug toolkit refresh`,
# the one subcommand the comment below says a clean checkout always has.
#   toolkit: the rest come from a manifest fetched by `dg debug toolkit
#            refresh` and cached on disk.
DYNAMIC_SUBCOMMAND_PREFIXES: dict[tuple[str, ...], frozenset[str]] = {
    ("debug", "toolkit"): frozenset({"refresh"}),
}


def _is_dynamic(argv: list[str]) -> bool:
    """True when argv names a subcommand only a populated cache would define."""
    for prefix, static in DYNAMIC_SUBCOMMAND_PREFIXES.items():
        if tuple(argv[: len(prefix)]) != prefix:
            continue
        rest = argv[len(prefix) :]
        # `dg debug toolkit` itself, and its statically defined subcommands,
        # resolve in a clean checkout -- check them.
        if not rest or rest[0].startswith("-") or rest[0] in static:
            return False
        return True
    return False


ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# `$(...)` or `` `...` `` -- non-nested, which is all our examples use.
SUBSTITUTION = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")

# Stands in for whatever a command substitution would expand to. Options that
# take a path validate their argument at runtime, not at parse time, so any
# non-empty token is enough to check the *shape* of the invocation.
SUBSTITUTION_PLACEHOLDER = "SUBSTITUTED"


def _split_substitutions(example: str) -> tuple[str, list[str]]:
    """Split a snippet into its outer command and its substituted snippets.

    `eval "$(dg completion bash)"` and `dg ffprobe --path $(which ffprobe)`
    are both advertised. Dropping them, as this test first did, left 2 of the
    advertised examples unchecked -- including the only one that invokes
    `dg completion`. Instead, lift each substitution out as a snippet in its
    own right and leave a placeholder token behind, so both the inner and the
    outer invocation get validated.
    """
    inner: list[str] = []

    def take(match: re.Match[str]) -> str:
        inner.append(match.group(1) if match.group(1) is not None else match.group(2))
        return SUBSTITUTION_PLACEHOLDER

    return SUBSTITUTION.sub(take, example), inner


def _dg_invocations(example: str) -> list[list[str]]:
    """Extract the argv of each `dg ...` invocation in a shell example.

    Examples are shell snippets, not bare argv: they contain pipelines
    (`dg speak "hi" | ffplay -`), upstream producers (`cat f | dg read`),
    command substitutions, and trailing `# comments`. Only the segments that
    invoke our own binary are ours to validate.
    """
    outer, inner = _split_substitutions(example)

    invocations = []
    for snippet in [outer, *inner]:
        for segment in re.split(r"\|\||&&|\|", snippet):
            try:
                argv = shlex.split(segment, comments=True)
            except ValueError:
                continue
            # `CI=1 dg listen x.wav` and `DEEPCTL_TELEMETRY_DISABLED=1 dg …`
            # are advertised too; the assignments are shell, the rest is ours.
            while argv and ENV_ASSIGNMENT.match(argv[0]):
                argv = argv[1:]
            if argv and argv[0] in BINARY_NAMES:
                invocations.append(argv[1:])
    return invocations


def _parse(cli: click.Group, argv: list[str]) -> None:
    """Resolve the command path and parse its options. Never invokes."""
    ctx = click.Context(cli, info_name="dg")
    command: click.Command = cli
    args = list(argv)

    while isinstance(command, click.Group) and args and not args[0].startswith("-"):
        name, sub, args = command.resolve_command(ctx, args)
        if sub is None:
            raise click.UsageError(f"No such command {name!r}")
        ctx = click.Context(sub, parent=ctx, info_name=name)
        command = sub

    command.parse_args(ctx, list(args))


def _collect() -> list[tuple[str, str, str, list[str]]]:
    """(group, command name, example string, argv) for every advertised example."""
    entry_points = metadata.entry_points()
    collected = []
    for group in COMMAND_GROUPS:
        for entry_point in entry_points.select(group=group):
            try:
                command_class = entry_point.load()
            except Exception:  # pragma: no cover - a broken package fails elsewhere
                continue
            for example in getattr(command_class, "examples", None) or []:
                for argv in _dg_invocations(example):
                    if _is_dynamic(argv):
                        continue
                    collected.append((group, entry_point.name, example, argv))
    return collected


CASES = _collect()


@pytest.mark.parametrize("group", COMMAND_GROUPS)
def test_examples_were_discovered(group: str) -> None:
    """Guard the guard: a stale entry point group must not pass unnoticed.

    A floor on the *total* does not do that. `deepctl.commands` alone supplies
    the overwhelming majority of cases, so dropping
    `deepctl.subcommands.debug` -- the group that carried the broken
    `dg debug stream` example -- still cleared a total-count check. Require
    every group to contribute.
    """
    from_group = [case for case in CASES if case[0] == group]
    assert from_group, (
        f"no examples discovered from the {group!r} entry point group -- it is "
        "probably stale or its packages are not installed, so this file is "
        "silently testing less than it claims"
    )


@pytest.mark.parametrize(
    ("group", "command_name", "example", "argv"),
    CASES,
    ids=[f"{name}: {example}" for _, name, example, _ in CASES],
)
def test_example_parses(
    group: str, command_name: str, example: str, argv: list[str]
) -> None:
    """Every string in every `examples` array must parse against the real CLI."""
    from deepctl.main import cli

    try:
        _parse(cli, argv)
    except (SystemExit, click.exceptions.Exit):
        # An eager option such as --help short-circuits; it parsed fine.
        pass
    except click.ClickException as exc:
        pytest.fail(
            f"`{example}` is advertised by `dg {command_name}` but does not "
            f"parse: {type(exc).__name__}: {exc}\n"
            "Fix the example, or add the option/subcommand it promises. This "
            "array is also what --agent-friendly emits."
        )


# ---------------------------------------------------------------------------
# The same guarantee, for the commands advertised in developer-facing docs.
#
# The `examples` arrays are not the only place the CLI tells people how to run
# it. The README and the two `llms*.txt` files agents read carry ~190 more
# command strings, and the #105 sweep did not reach them: `llms-full.txt` still
# advertised `dg usage --start/--end`, the exact option pair #105 fixed in
# `--help`, plus stale command names and options.
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]

DOC_FILES = [
    "README.md",
    "web/public/llms.txt",
    "web/public/llms-full.txt",
]

# Templates, not runnable commands: `dg ... -o json | jq`, `dg <command>
# --agent-friendly`, `dg keys --delete KEY_ID`, `dg login --api-key SK`.
# Apply this to the extracted CLI argv rather than a whole shell snippet: a
# downstream jq filter may contain `{}` while the preceding `dg` invocation is
# still a valid command that we must validate.
PLACEHOLDER = re.compile(r"\.\.\.|[<>{}]|YOUR_|\bKEY_ID\b|\bSK\b")

# Lines that start a shell snippet we own.
SNIPPET_START = ("dg ", "deepctl ", "dg\t", "eval ")


def _doc_commands(path: str) -> list[tuple[int, str]]:
    """(line number, command string) for every `dg …` a doc file advertises.

    Two sources, because both are read as instructions: lines inside fenced
    code blocks, and inline `code spans` in prose. Non-ASCII candidates are
    prose, not commands (the README banner caption is `deepctl \u2014 Official
    Deepgram CLI \u2026`), and templates carrying a placeholder are skipped --
    with the placeholder set spelled out above rather than dropped silently.
    """
    found: list[tuple[int, str]] = []
    fenced = False
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        candidates = []
        stripped = line.strip()
        if fenced and (
            stripped.startswith(SNIPPET_START)
            or (ENV_ASSIGNMENT.match(stripped) and " dg " in stripped)
        ):
            candidates.append(stripped)
        candidates += [
            span
            for span in re.findall(r"`([^`]+)`", line)
            if span.startswith(("dg ", "deepctl "))
        ]
        for candidate in candidates:
            if not candidate.isascii():
                continue
            if any(
                not PLACEHOLDER.search(" ".join(argv))
                for argv in _dg_invocations(candidate)
            ):
                found.append((lineno, candidate))
    return found


DOC_CASES = [
    (path, lineno, command)
    for path in DOC_FILES
    for lineno, command in _doc_commands(path)
]


@pytest.mark.parametrize("path", DOC_FILES)
def test_doc_commands_were_discovered(path: str) -> None:
    """A docs refactor that stops matching must fail rather than pass empty."""
    assert [case for case in DOC_CASES if case[0] == path], (
        f"no `dg ...` commands found in {path} -- the extractor is stale, so "
        "this file is testing nothing"
    )


@pytest.mark.parametrize(
    ("path", "lineno", "command"),
    DOC_CASES,
    ids=[f"{path}:{lineno}" for path, lineno, _ in DOC_CASES],
)
def test_doc_command_parses(path: str, lineno: int, command: str) -> None:
    """Every command a doc advertises must resolve against the real CLI."""
    from deepctl.main import cli

    for argv in _dg_invocations(command):
        if PLACEHOLDER.search(" ".join(argv)):
            continue
        try:
            _parse(cli, argv)
        except (SystemExit, click.exceptions.Exit):
            # An eager option such as --help short-circuits; it parsed fine.
            pass
        except (click.exceptions.NoArgsIsHelpError, click.MissingParameter):
            # The docs list bare command names (`dg debug`, `dg plugin`) in
            # tables. Those resolve; they just need arguments to run.
            pass
        except click.ClickException as exc:
            pytest.fail(
                f"{path}:{lineno} advertises `{command}`, which does not "
                f"parse: {type(exc).__name__}: {exc}\n"
                "Fix the doc, or add the option/subcommand it promises."
            )
