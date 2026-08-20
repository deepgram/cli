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

import click
import pytest

# Entry point groups that carry command classes.
COMMAND_GROUPS = ["deepctl.commands", "deepctl.subcommands.debug"]

BINARY_NAMES = ("dg", "deepctl", "deepgram")

# Groups whose subcommands are built at runtime from state this test cannot
# see, so their examples are unverifiable rather than wrong.
#   toolkit: subcommands come from a manifest fetched by `dg debug toolkit
#            refresh` and cached on disk; a clean checkout has only `refresh`.
DYNAMIC_SUBCOMMAND_PREFIXES = [("debug", "toolkit")]


def _dg_invocations(example: str) -> list[list[str]]:
    """Extract the argv of each `dg ...` invocation in a shell example.

    Examples are shell snippets, not bare argv: they contain pipelines
    (`dg speak "hi" | ffplay -`), upstream producers (`cat f | dg read`), and
    trailing `# comments`. Only the segments that invoke our own binary are
    ours to validate.
    """
    if "$(" in example or "`" in example:
        # Command substitution -- `eval "$(dg completion bash)"` and friends.
        # The inner dg call is real but the surrounding shell is not argv.
        return []

    invocations = []
    for segment in re.split(r"\|\||&&|\|", example):
        try:
            argv = shlex.split(segment, comments=True)
        except ValueError:
            continue
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


def _collect() -> list[tuple[str, str, list[str]]]:
    """(command name, example string, argv) for every advertised example."""
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
                    if any(
                        tuple(argv[: len(prefix)]) == prefix
                        for prefix in DYNAMIC_SUBCOMMAND_PREFIXES
                    ):
                        continue
                    collected.append((entry_point.name, example, argv))
    return collected


CASES = _collect()


def test_examples_were_discovered() -> None:
    """Guard the guard: an import change that empties CASES must not pass."""
    assert len(CASES) > 50, (
        f"only {len(CASES)} examples discovered -- the entry point groups in "
        "COMMAND_GROUPS are probably stale, so this file is testing nothing"
    )


@pytest.mark.parametrize(
    ("command_name", "example", "argv"),
    CASES,
    ids=[f"{name}: {example}" for name, example, _ in CASES],
)
def test_example_parses(command_name: str, example: str, argv: list[str]) -> None:
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
