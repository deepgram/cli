"""Skills command for managing AI coding assistant integrations."""

from __future__ import annotations

import contextlib
import csv
import importlib.metadata
import io
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
from deepctl_core.auth import AuthManager
from deepctl_core.base_group_command import BaseGroupCommand
from deepctl_core.client import DeepgramClient
from deepctl_core.config import Config
from deepctl_core.models import BaseResult
from deepctl_core.output import (
    get_console,
    get_output_format,
    get_status_console,
    is_agentic,
)
from deepctl_core.skill_bundle import (
    DEFAULT_SKILLS_REF,
    PINNED_REF_SOURCE,
    REF_ENV_VAR,
    SkillFetchError,
    SkillRefInvalidError,
    SkillRefNotFoundError,
    resolve_skills_ref,
    validate_ref,
)
from deepctl_core.skill_generator import (
    RemoveReport,
    SkillsStateError,
    SkillsStateLockTimeout,
    skills_state_file,
    skills_state_lock,
)
from pydantic import BaseModel, Field
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

    from deepctl_core.skill_bundle import RepoSkill
    from deepctl_core.skill_generator import SkillInstallReport

# The result tables, and only those, go to stdout: in default mode they are
# the command's output. Everything a human reads alongside them -- banners,
# progress, hints -- goes through the stderr console so `-o json` and
# `-o yaml` stay a single parseable document.
console = Console()
status_console = get_status_console()


def _say(message: str, glyph: str, prefix: str, *, error: bool = False) -> None:
    """Human chrome, always on stderr.

    ``deepctl_core.output.print_info`` and friends write to stdout outside
    agentic mode, which is exactly where `dg -o json skills list | jq`
    broke: the hint landed in front of the document. Same rendering as
    those helpers -- glyph for a person, prefix for an agent -- but on the
    stderr console, and still silenced by ``--quiet``.

    ``error`` lines are not chrome and print under ``--quiet`` too, as
    ``print_error`` does: the exit-1 message after them says "Fix the
    problem above", so there has to be one.
    """
    if get_console().quiet and not error:
        return
    status_console.print(
        f"{prefix} {message}" if is_agentic() else f"{glyph} {message}"
    )


def _info(message: str) -> None:
    _say(message, "[blue]ℹ[/blue]", "INFO:")


def _success(message: str) -> None:
    _say(message, "[green]✓[/green]", "OK:")


def _warning(message: str) -> None:
    _say(message, "[yellow]⚠[/yellow]", "WARN:")


def _failure(message: str) -> None:
    _say(message, "[red]✗[/red]", "ERROR:", error=True)


def _failed_tools(
    report: SkillInstallReport,
    done: str,
    nothing: str,
    verb: str,
    recorded: Iterable[str] = (),
) -> str:
    """The exit-1 message once keep_going has attempted every tool.

    ``done`` is what landed, already worded ("Updated 4 tool(s)"), and
    ``nothing`` the same when no tool landed ("No tool was updated").
    ``recorded`` names the tools that had a record before the run: only
    those are still recorded at a previous release. Each failure was
    printed above this, with its own fix, even under ``--quiet``.
    """
    names = ", ".join(name for name, _ in report.failures)
    count = len(report.failures)
    lead = (
        f"{done}, {count} failed ({names})."
        if report.written
        else f"{nothing}: {count} failed ({names})."
    )
    kept = [name for name, _ in report.failures if name in set(recorded)]
    if len(kept) == 1:
        lead += f" {kept[0]} stays recorded at its previous release."
    elif kept:
        lead += (
            f" {', '.join(kept[:-1])} and {kept[-1]} stay recorded at their "
            "previous release."
        )
    return f"{lead} Fix the problem above, then {verb} again."


#: Printed after a successful install: the one skill that needs a follow-up.
_MCP_HINT = (
    "One of the installed skills is 'setup-mcp'. Ask your assistant to "
    "set up the Deepgram MCP server, or run 'dg mcp' to start it directly."
)

#: What `install` and `setup` say when the records are not a map of tools.
_NOTHING_INSTALLED = (
    "Nothing was installed. Fix or delete that file, then run "
    "'dg skills install' again."
)

#: Where `--ref` comes from when it is not given, in the order it is looked
#: up. `update` adds the recorded ref between the environment and the pin.
_REF_PRECEDENCE_INSTALL = (
    f"Precedence: --ref, then {REF_ENV_VAR}, then the pinned release "
    f"({DEFAULT_SKILLS_REF})."
)
_REF_PRECEDENCE_UPDATE = (
    f"Precedence: --ref, then {REF_ENV_VAR}, then the ref recorded in "
    f"skills.json by the last install, then the pinned release "
    f"({DEFAULT_SKILLS_REF})."
)


class SkillsToolStatus(BaseModel):
    """One row of `dg skills status`."""

    cli: str
    display_name: str
    detected: bool
    #: Skill folders deepctl recorded installing that are still on disk,
    #: or None when the records in skills.json cannot be read.
    installed: int | None
    #: The deepgram/skills ref the last install for this tool recorded.
    skills_ref: str | None = None
    #: Where this tool reads skills from; None when it has no such place.
    skills_directory: str | None = None
    #: The record is a 'dg skills remove' that has not finished, not an
    #: install. 'dg skills list' reports the same flag.
    remove_pending: bool = False


class SkillsStatusResult(BaseResult):
    """Structured `dg skills status`, for `-o json` and `-o yaml`."""

    tools: list[SkillsToolStatus] = Field(default_factory=list)


class SkillsInstalledTool(BaseModel):
    """One row of `dg skills list`: what skills.json records for a tool."""

    cli: str
    deepctl_version: str | None = None
    skills_ref: str | None = None
    skills: list[str] = Field(default_factory=list)
    count: int = 0
    location: str | None = None
    #: The record is a 'dg skills remove' that has not finished, not an
    #: install: what is left is waiting on a retry of that remove.
    remove_pending: bool = False


class SkillsListResult(BaseResult):
    """Structured `dg skills list`, for `-o json` and `-o yaml`."""

    installed: list[SkillsInstalledTool] = Field(default_factory=list)


def _state_file() -> str:
    """The skills.json path, for messages that have to name the file.

    Core's ``skills_state_file()`` follows an overridden skills directory.
    """
    return str(skills_state_file())


def _removed_during_update(cli_name: str) -> str:
    """Why ``update`` left a tool alone that it set out to update."""
    return f"  {cli_name}: removed while the update ran, so it was left alone."


def _removal_finished(display_name: str) -> str:
    """What ``update`` says after finishing a remove that had failed."""
    return (
        f"  {display_name}: finished the earlier 'dg skills remove', so no "
        "skills were reinstalled."
    )


def _removals_summary(report: SkillInstallReport) -> str:
    """What ``update`` says when all it did was retry earlier removes."""
    parts = []
    if report.removals_finished:
        parts.append(f"Finished {len(report.removals_finished)} earlier remove(s)")
    if report.removals_pending:
        parts.append(
            f"{len(report.removals_pending)} earlier remove(s) still could not finish"
        )
    return "; ".join(parts) + ". No skills were reinstalled."


def _removal_still_pending(display_name: str, cli_name: str) -> str:
    """What ``update`` says about a remove that still cannot finish."""
    return (
        f"  {display_name}: an earlier 'dg skills remove' has not finished, so "
        "no skills were reinstalled. Fix the permissions and run "
        f"'dg skills remove --cli {cli_name}' again."
    )


#: The sentence an error ends with when the command changed nothing.
#: :func:`_after_removals` rewrites it when a pending remove finished first.
_NOTHING_CHANGED = "Nothing was changed."


def _held_by_another(message: str) -> str:
    """``message`` plus what it means for this command.

    Core's message already says another deepctl holds the lock and to
    wait and retry; repeating that read as the same advice twice.
    """
    return f"{message} {_NOTHING_CHANGED}"


def _after_removals(message: str, finished: list[tuple[str, str]]) -> str:
    """``message`` made true for a run that finished pending removes first.

    ``finished`` is what :func:`removals_finished_by` read off the error:
    removes that are done and saved, so "Nothing was changed." is false.
    Named in the error itself, which is all ``-q`` prints.
    """
    if not finished:
        return message
    names = [display_name for _cli, display_name in finished]
    who = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    note = f"The earlier 'dg skills remove' for {who} did finish first."
    if _NOTHING_CHANGED in message:
        return message.replace(_NOTHING_CHANGED, f"No skills were installed. {note}")
    return f"{message} {note}"


def _legacy_left_in_place(display_name: str, path: object, reason: str) -> str:
    """One warning per deepctl <= 0.3.0 leftover the cleanup declined to touch."""
    return f"  {display_name}: left {path} in place: {reason}"


def _unknown_cli(cli_name: str) -> click.ClickException:
    """The exit-1 error for a ``--cli`` that names no supported tool."""
    return click.ClickException(
        f"Unknown AI CLI: {cli_name}. Run 'dg skills status' to see supported CLIs."
    )


def _names_the_file(message: str) -> str:
    """``message`` with the skills.json path and the fix, if it lacks them.

    Core's own error already says which file and what to do; a message
    from anywhere else gets the same two facts appended rather than
    leaving the user to guess which file "not valid JSON" was about.
    """
    if _state_file() in message:
        return message
    return (
        f"{message}\n\ndeepctl cannot read {_state_file()}. Fix or delete "
        "that file, then run 'dg skills install'."
    )


class SkillsCommand(BaseGroupCommand):
    """AI coding assistant skill management."""

    name = "skills"
    help = "Install Deepgram agent skills into AI coding assistants"
    #: Display names of the tools the last ``_install_for`` found recorded
    #: before it wrote anything, for the exit-1 summary.
    _recorded_before: tuple[str, ...] = ()
    examples = [
        "dg skills status",
        "dg skills install",
        "dg skills install --all",
        "dg skills install --all --ref main",
        "dg skills update",
        "dg skills remove --all",
    ]
    agent_help = (
        "Install the Deepgram agent skills from github.com/deepgram/skills "
        "into AI coding assistants (Claude Code, Codex, Gemini CLI, Cursor, "
        "OpenCode, Cline). Each skill is copied as a folder into the "
        "user-scope skills directory that tool reads. Use 'skills status' to "
        "see which tools are present and where their skills go, "
        "'skills install' to install, 'skills list' to see what is installed "
        "and from which upstream ref, and 'skills update' to reinstall. "
        "Installs are pinned to a released deepgram/skills tag; override with "
        "--ref or DEEPCTL_SKILLS_REF. 'skills update' without either "
        "reinstalls the ref the last install recorded in skills.json, so an "
        "install from a branch stays on that branch. 'skills status' and "
        "'skills list' return structured output under -o json or -o yaml. "
        "A fetch failure exits non-zero with "
        "nothing written rather than installing a subset. Those skills "
        "directories are shared with the user's own skills and other "
        "publishers', so every subcommand operates only on the folders "
        "deepctl recorded installing: install refuses to overwrite an "
        "unrecorded folder of the same name and remove never deletes one. "
        "'skills setup' runs the same install, so the same rules apply to it. "
        "A recorded path that is now a symlink is not deepctl's either: "
        "install and update exit non-zero naming it, and remove reports it "
        "instead of deleting through it. A recorded folder remove cannot "
        "delete stays recorded and remove exits non-zero, so a later remove "
        "or update can still reach it. 'dg login' and 'dg plugin' follow the "
        "same ownership rules but warn and exit 0 instead of failing."
    )

    def execute(self, ctx: click.Context, **kwargs: Any) -> None:
        """Execute skills group command."""
        config = ctx.obj.get("config") if ctx.obj else None
        if not config:
            config = Config()

        auth_manager = AuthManager(config)
        client = DeepgramClient(config, auth_manager)

        ctx.obj = ctx.obj or {}
        ctx.obj["config"] = config
        ctx.obj["auth_manager"] = auth_manager
        ctx.obj["client"] = client

        super().execute(ctx, **kwargs)

    def setup_commands(self) -> list[click.Command]:
        """Set up skills management subcommands."""

        def context_wrapper(func: Any) -> Any:
            """Wrap subcommand to provide config and auth.

            A subcommand that returns a result hands it to
            :meth:`output_result`, the same serialiser every BaseCommand
            uses, so `-o json` and `-o yaml` emit the result and nothing
            else. In default mode that is a no-op: the handler has already
            rendered its table.
            """

            @click.pass_context
            def wrapper(ctx: click.Context, /, **kwargs: Any) -> Any:
                config = auth_manager = client = None
                if ctx.parent and ctx.parent.obj:
                    config = ctx.parent.obj.get("config")
                    auth_manager = ctx.parent.obj.get("auth_manager")
                    client = ctx.parent.obj.get("client")
                if not (config and auth_manager and client):
                    config = Config()
                    auth_manager = AuthManager(config)
                    client = DeepgramClient(config, auth_manager)

                result = func(config, auth_manager, client, **kwargs)
                if result is not None:
                    self.output_result(result, config)
                    # Payload first, then the exit code that agrees with
                    # it: a result reporting "error" is a failed command,
                    # which the README documents as exit 1. SystemExit
                    # rather than ClickException, because the handler has
                    # already said what went wrong on stderr.
                    exit_code = self.exit_code_for(result)
                    if exit_code:
                        raise SystemExit(exit_code)
                return result

            wrapper.__name__ = func.__name__
            wrapper.__doc__ = func.__doc__
            return wrapper

        return [
            self._create_status_command(context_wrapper),
            self._create_install_command(context_wrapper),
            self._create_update_command(context_wrapper),
            self._create_remove_command(context_wrapper),
            self._create_list_command(context_wrapper),
            self._create_setup_command(context_wrapper),
        ]

    # ------------------------------------------------------------------
    # Subcommand factories
    # ------------------------------------------------------------------

    def _create_status_command(self, context_wrapper: Any) -> click.Command:
        """Create the status subcommand."""

        @click.command(
            name="status",
            help="Show detected AI CLIs and skill installation status",
        )
        def status_cmd(**kwargs: Any) -> None:
            pass

        status_cmd.callback = context_wrapper(
            lambda config, auth_manager, client, **kw: self._handle_status()
        )
        return status_cmd

    def _create_install_command(self, context_wrapper: Any) -> click.Command:
        """Create the install subcommand."""

        @click.command(
            name="install",
            help="Detect AI CLIs and install the Deepgram skill folders",
        )
        @click.option(
            "--all",
            "install_all",
            is_flag=True,
            help="Install for all detected CLIs without prompting",
        )
        @click.option(
            "--cli",
            "cli_name",
            help="Install for a specific AI CLI only",
        )
        @click.option(
            "--ref",
            "ref",
            metavar="REF",
            help=(
                "Install from this deepgram/skills git ref (tag, branch or "
                f"SHA). {_REF_PRECEDENCE_INSTALL}"
            ),
        )
        def install_cmd(**kwargs: Any) -> None:
            pass

        install_cmd.callback = context_wrapper(
            lambda config, auth_manager, client, **kw: self._handle_install(**kw)
        )
        return install_cmd

    def _create_update_command(self, context_wrapper: Any) -> click.Command:
        """Create the update subcommand."""

        @click.command(
            name="update",
            help=(
                "Reinstall every installed tool's skills from upstream, "
                "following the ref the last install recorded unless --ref or "
                f"{REF_ENV_VAR} is given"
            ),
        )
        @click.option(
            "--ref",
            "ref",
            metavar="REF",
            help=(
                "Update to this deepgram/skills git ref (tag, branch or SHA). "
                f"{_REF_PRECEDENCE_UPDATE}"
            ),
        )
        def update_cmd(**kwargs: Any) -> None:
            pass

        update_cmd.callback = context_wrapper(
            lambda config, auth_manager, client, **kw: self._handle_update(**kw)
        )
        return update_cmd

    def _create_remove_command(self, context_wrapper: Any) -> click.Command:
        """Create the remove subcommand."""

        @click.command(
            name="remove",
            help=("Remove only the skill folders deepctl installed"),
        )
        @click.option(
            "--all",
            "remove_all",
            is_flag=True,
            help="Remove deepctl's skills from every tool it installed into",
        )
        @click.option(
            "--cli",
            "cli_name",
            help="Remove deepctl's skills for a specific AI CLI",
        )
        def remove_cmd(**kwargs: Any) -> None:
            pass

        remove_cmd.callback = context_wrapper(
            lambda config, auth_manager, client, **kw: self._handle_remove(**kw)
        )
        return remove_cmd

    def _create_list_command(self, context_wrapper: Any) -> click.Command:
        """Create the list subcommand."""

        @click.command(
            name="list",
            help="Show what is installed, from which upstream ref, and where",
        )
        def list_cmd(**kwargs: Any) -> None:
            pass

        list_cmd.callback = context_wrapper(
            lambda config, auth_manager, client, **kw: self._handle_list()
        )
        return list_cmd

    def _create_setup_command(self, context_wrapper: Any) -> click.Command:
        """Create the setup subcommand — interactive first-run wizard."""

        @click.command(
            name="setup",
            help="Interactive setup: detect AI tools and install Deepgram skills",
        )
        @click.option(
            "--all",
            "install_all",
            is_flag=True,
            help="Install for all detected tools without prompting",
        )
        @click.option(
            "--ref",
            "ref",
            metavar="REF",
            help=(
                "Install from this deepgram/skills git ref (tag, branch or "
                f"SHA). {_REF_PRECEDENCE_INSTALL}"
            ),
        )
        def setup_cmd(**kwargs: Any) -> None:
            pass

        setup_cmd.callback = context_wrapper(
            lambda config, auth_manager, client, **kw: self._handle_setup(**kw)
        )
        return setup_cmd

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def output_result(self, result: Any, config: Config) -> None:
        """Keep ``status`` and ``list`` as their table under -o table/csv.

        The generic serialiser would print the result document as key and
        value pairs, with the list of tools as one Python repr. Like
        ``dg listen``, this command renders its own human view instead:
        ``-o table`` is default mode, whose handler has already printed
        the table, and ``-o csv`` is one row per tool under that table's
        column names. Every other format, and every other result, goes to
        the framework unchanged.
        """
        fmt = get_output_format()
        ours = isinstance(result, (SkillsStatusResult, SkillsListResult))
        if not ours or fmt not in ("table", "csv"):
            super().output_result(result, config)
            return
        if fmt == "table":
            # Printed by the handler, as in default mode, so the stderr
            # lines that follow the table there follow it here too.
            return
        table = (
            _status_table(result.tools)
            if isinstance(result, SkillsStatusResult)
            else _list_table(result.installed)
        )
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([str(c.header) for c in table.columns])
        writer.writerows(_plain_rows(table))
        self._write_payload(buffer.getvalue().rstrip("\r\n"))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _installed_records(state: dict[str, Any]) -> dict[str, Any] | None:
        """``installed_skills`` as a map of tool to record, or None.

        A hand-edited ``skills.json`` can carry a list or a string here,
        and every handler below iterates it. None means the file cannot
        be read as records, so the caller says which file is wrong --
        ``main.py`` would otherwise turn the attribute error into
        "Error: 'list' object has no attribute 'keys'", which names a
        Python type rather than the file to fix.
        """
        # No key at all is a file with nothing recorded yet; a key that
        # holds null, a list, a string or a number is a damaged one.
        if "installed_skills" not in state:
            return {}
        installed = state["installed_skills"]
        return installed if isinstance(installed, dict) else None

    @staticmethod
    def _unreadable_records(consequence: str) -> str:
        """Message for a ``skills.json`` whose records are not a map."""
        return (
            "deepctl cannot read its own records: 'installed_skills' in "
            f"{_state_file()} is not a set of entries. {consequence}"
        )

    def _records_or_fail(
        self, state: dict[str, Any], consequence: str
    ) -> dict[str, Any]:
        """``installed_skills`` as a map, or the command fails naming the file.

        For the subcommands that write: a value that is valid JSON but not
        a map of tools is the same kind of damage as a file that does not
        parse, and the README promises exit 1 for both. Core's installer
        would quietly replace it with an empty map, which is how
        ``install`` once overwrote a hand-edited file and exited 0.
        """
        installed = self._installed_records(state)
        if installed is None:
            raise click.ClickException(self._unreadable_records(consequence))
        return installed

    @staticmethod
    def _load_state() -> dict[str, Any]:
        """Read ``skills.json``, or fail the command naming the file.

        A corrupt or unreadable file is a failed command (exit 1), not a
        silent reset: resetting would make the next install treat every
        folder deepctl wrote as somebody else's.
        """
        from deepctl_core.skill_generator import get_skills_state

        try:
            return get_skills_state()
        except SkillsStateError as exc:
            raise click.ClickException(_names_the_file(str(exc)))

    @staticmethod
    def _read_state() -> tuple[dict[str, Any], str | None]:
        """``skills.json``, or an empty state and why it could not be read.

        For ``status`` and ``list``, which report a damaged file in their
        result rather than raising: a file that is not valid JSON is the
        same damage as records of the wrong shape, so under ``-o json`` it
        gets the same error document and exit 1, not an empty stdout.
        """
        from deepctl_core.skill_generator import get_skills_state

        try:
            return get_skills_state(), None
        except SkillsStateError as exc:
            return {}, _names_the_file(str(exc))

    @contextlib.contextmanager
    def _state_guard(self) -> Iterator[None]:
        """Hold ``skills.json`` for a read-modify-write, failing as exit 1.

        Core's ``skills_state_lock`` keeps two deepctl processes from
        saving over each other's records. For ``remove``, which downloads
        nothing. An install goes through :meth:`_state_errors` instead and
        lets core take the lock after the fetch.
        """
        with self._state_errors(), skills_state_lock():
            yield

    @contextlib.contextmanager
    def _state_errors(self) -> Iterator[None]:
        """Turn a ``skills.json`` problem raised inside into exit 1.

        The lock timing out, or a save refusing to replace a file that
        does not parse, is a failed command that names the file. Holds no
        lock itself: ``install_skills_for`` takes it once the download is
        done, so a slow fetch never makes another deepctl wait.
        """
        try:
            yield
        except SkillsStateLockTimeout as exc:
            # Before the parent class: the file is fine, it is busy.
            raise click.ClickException(_held_by_another(str(exc)))
        except SkillsStateError as exc:
            raise click.ClickException(_names_the_file(str(exc)))

    def _checked_state(self, consequence: str) -> dict[str, Any]:
        """Read ``skills.json`` and refuse records that are not a map.

        Handed to ``install_skills_for`` as ``load_state``, which calls it
        under the lock after the fetch. The same refusal as before the
        prompts, so a file damaged in between still exits 1 with nothing
        written rather than being replaced with an empty map.
        """
        state = self._load_state()
        self._records_or_fail(state, consequence)
        return state

    @staticmethod
    def _recorded_ref(installed: dict[str, Any], cli_name: str) -> str | None:
        """The deepgram/skills ref ``skills.json`` records for one tool."""
        info = installed.get(cli_name)
        if not isinstance(info, dict):
            return None
        ref = info.get("skills_ref")
        return ref if isinstance(ref, str) and ref else None

    @staticmethod
    def _explicit_ref(ref: str | None) -> tuple[str, str] | None:
        """The ref the user asked for, checked, or None if they asked for none.

        ``--ref`` wins and is checked even when empty: ``--ref ""`` is far
        more likely a shell-quoting slip than a request for the default,
        and ``install`` refuses it the same way. A blank
        ``DEEPCTL_SKILLS_REF`` is treated as unset.

        Raises:
            SkillRefInvalidError: The chosen ref fails :func:`validate_ref`.
                One from the environment names the variable.
        """
        if ref is not None:
            return validate_ref(ref), "--ref"
        from_env = os.environ.get(REF_ENV_VAR, "").strip()
        if from_env:
            return validate_ref(from_env, REF_ENV_VAR), REF_ENV_VAR
        return None

    def _update_ref(
        self, ref: str | None, installed: dict[str, Any]
    ) -> tuple[str, str, str]:
        """Which ref ``update`` reinstalls, and where that choice came from.

        Precedence: ``--ref``, then ``DEEPCTL_SKILLS_REF``, then the ref the
        last install recorded, then the pinned release. Without the third
        step, ``install --ref main`` followed by a bare ``update`` silently
        switched the user back to the pinned tag.

        Returns the ref, the label ``update`` announces it with, and the
        ``ref_source`` a :class:`SkillRefNotFoundError` names.
        """
        explicit = self._explicit_ref(ref)
        if explicit is not None:
            return explicit[0], explicit[1], explicit[1]
        recorded = {r for cli in installed if (r := self._recorded_ref(installed, cli))}
        if len(recorded) == 1:
            return (
                recorded.pop(),
                f"recorded in {_state_file()}",
                f"the last install's record in {_state_file()}",
            )
        if len(recorded) > 1:
            _warning(
                f"{_state_file()} records more than one deepgram/skills ref "
                f"({', '.join(sorted(recorded))}), so every tool is updated "
                f"to the pinned release. Pass --ref to choose one."
            )
        return DEFAULT_SKILLS_REF, "pinned release", PINNED_REF_SOURCE

    def _fetch_skills(
        self, ref: str | None, ref_source: str | None = None
    ) -> list[RepoSkill]:
        """Fetch the upstream skills, or fail the command outright.

        A partial install is worse than none: once the files are on disk
        there is nothing to tell the user that four of fourteen skills
        arrived. ClickException is what main.py turns into exit 1.

        ``ref_source`` is where ``ref`` came from; a ref with none given
        came from ``--ref``. With no ref at all, core works it out.
        """
        from deepctl_core.skill_generator import fetch_repo_skills

        if ref is not None and ref_source is None:
            ref_source = "--ref"
        try:
            return fetch_repo_skills(ref, force=True, ref_source=ref_source)
        except SkillRefNotFoundError as exc:
            # The ref does not exist upstream, so waiting for the network
            # cannot help. Core's message already names where the ref came
            # from. A ref typed with --ref needs nothing more; one from the
            # environment needs the way out of it, as login and the plugin
            # refresh give; any other needs --ref.
            advice = "No skills were installed."
            if exc.ref_source == REF_ENV_VAR:
                advice += (
                    f" Set {REF_ENV_VAR} to another ref, or unset it to use "
                    "the pinned release."
                )
            elif exc.ref_source != "--ref":
                advice += " Pass --ref to choose another deepgram/skills ref."
            raise click.ClickException(f"{exc}\n\n{advice}")
        except SkillFetchError as exc:
            raise click.ClickException(
                f"{exc}\n\nNo skills were installed. Retry when the "
                "network is available, or pass --ref to pick another "
                "deepgram/skills revision."
            )

    def _install_for(
        self,
        generators: list[Any],
        state: dict[str, Any],
        ref: str | None,
        load_state: Callable[[], dict[str, Any]] | None = None,
        only_recorded: bool = False,
        ref_source: str | None = None,
        keep_going: bool = False,
        retry: str | None = None,
    ) -> SkillInstallReport:
        """Install for every selected tool, then report what landed.

        The ownership contract lives in
        :func:`deepctl_core.skill_generator.install_skills_for`, which
        login and the plugin refresh call too. All this adds is the exit
        code: a collision is a failed command, not a warning, so it
        becomes the ClickException main.py turns into exit 1.

        ``only_recorded`` is for ``update``: a tool removed while the
        download ran is no longer recorded when core re-reads the records,
        so core leaves it alone instead of installing it again.

        ``keep_going`` is for a run over several tools: a tool whose skills
        directory cannot be written is printed here, with ``retry`` as the
        command to run again, and the next tool is still attempted. The
        caller then fails the command if ``report.failures`` is non-empty.
        Without it the first such tool fails the command on the spot.
        """
        from deepctl_core.skill_generator import (
            SkillOwnershipError,
            SkillWriteError,
            collect_command_metadata,
            install_skills_for,
            removals_finished_by,
        )

        def announce(gen: Any, paths: list[Path]) -> None:
            # Printed as each tool lands, not once they all have: a later
            # tool failing must not hide the ones that did install and
            # are now recorded as deepctl's.
            _success(
                f"  {gen.display_name}: {len(paths)} skills -> {gen.skills_root()}"
            )

        # Which selected tools had a record before anything was written,
        # read from the records core works from: the summary says a failed
        # tool "stays recorded" only when it was recorded to begin with.
        def recorded_now(loaded: dict[str, Any]) -> None:
            found = loaded.get("installed_skills")
            names = found if isinstance(found, dict) else {}
            self._recorded_before = tuple(
                gen.display_name for gen in generators if gen.cli_name in names
            )

        def fresh_state() -> dict[str, Any]:
            assert load_state is not None
            loaded = load_state()
            recorded_now(loaded)
            return loaded

        recorded_now(state)
        try:
            report = install_skills_for(
                generators,
                state,
                commands=collect_command_metadata(),
                version=_deepctl_version(),
                ref=ref,
                fetch=lambda: self._fetch_skills(ref, ref_source),
                on_installed=announce,
                load_state=None if load_state is None else fresh_state,
                only_recorded=only_recorded,
                keep_going=keep_going,
            )
        except Exception as exc:
            # A remove left pending was finished before this failed. It is
            # done and saved, so the error that ends the run says so: it
            # is the one line -q prints, and "Nothing was changed." would
            # be false.
            finished = removals_finished_by(exc)
            if isinstance(exc, SkillOwnershipError):
                raise click.ClickException(_after_removals(str(exc), finished))
            if isinstance(exc, SkillWriteError):
                # Core names the skills directory; the OSError behind it
                # named a staging folder inside it the user cannot act on.
                raise click.ClickException(
                    _after_removals(f"{exc} {exc.advice()}", finished)
                ) from None
            if isinstance(exc, SkillsStateLockTimeout):
                # Before the parent class: the file is fine, it is busy.
                raise click.ClickException(
                    _after_removals(_held_by_another(str(exc)), finished)
                ) from None
            if isinstance(exc, SkillsStateError):
                raise click.ClickException(
                    _after_removals(_names_the_file(str(exc)), finished)
                ) from None
            if isinstance(exc, click.ClickException):
                # A fetch failure, or skills.json damaged in between.
                if not finished:
                    raise
                raise click.ClickException(
                    _after_removals(exc.format_message(), finished)
                ) from None
            # Anything else has no message of ours to carry the line.
            for _cli, display_name in finished:
                _info(_removal_finished(display_name))
            raise

        for name in report.skipped_unrecorded:
            _info(_removed_during_update(name))
        # A record that is a remove still to finish was retried, not
        # reinstalled: the user asked for those skills to go. When a tool
        # then failed, the caller's exit-1 error names these instead: it
        # is the one line -q prints, and saying it here too said it twice.
        if not report.failures:
            for _cli, display_name in report.removals_finished:
                _info(_removal_finished(display_name))
        for cli, display_name in report.removals_pending:
            _warning(_removal_still_pending(display_name, cli))
        # A recorded skill the ref no longer ships was deleted; say so.
        for notice in report.pruned_notices:
            _info(notice)

        for gen in report.unsupported:
            _warning(f"  {gen.manual_hint()}")
        # A deepctl <= 0.3.0 file the cleanup found but would not touch:
        # the content was not deepctl's, or the file could not be edited
        # safely. Left where it is, and said so, rather than leaving the
        # user to find a stale copy next to the fresh install.
        for display_name, path, reason in report.legacy_skipped:
            _warning(_legacy_left_in_place(display_name, path, reason))
        # Each tool that could not be written, after the ones that landed:
        # keep_going attempted every tool, so none is hidden behind it.
        for display_name, failure in report.failures:
            advice = (
                failure.advice(retry)
                if isinstance(failure, SkillWriteError)
                else f"Run '{retry or 'the command'}' again."
            )
            _failure(escape(f"  {display_name}: {failure} {advice}"))
        return report

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _handle_status(self) -> SkillsStatusResult:
        """Show detected AI CLIs and whether skills are installed."""
        from deepctl_core.skill_generator import (
            SKILLS_CLI_HINT,
            get_all_generators,
            is_pending_removal,
            recorded_skill_paths,
        )

        generators = get_all_generators()
        state, unparsable = self._read_state()
        installed = None if unparsable else self._installed_records(state)

        rows: list[SkillsToolStatus] = []
        for gen in generators:
            root = gen.skills_root()
            # Deepgram's own skills only. These directories are shared, so
            # counting every folder in them would report the user's skills
            # and other publishers' skills as deepctl installs.
            # None, not 0, when the records cannot be read: a zero would
            # claim deepctl knows nothing is installed, and it does not.
            count: int | None = None
            if installed is not None:
                count = len(
                    gen.installed_skill_paths(recorded_skill_paths(state, gen.cli_name))
                )
            rows.append(
                SkillsToolStatus(
                    cli=gen.cli_name,
                    display_name=gen.display_name,
                    detected=gen.detect(),
                    installed=count,
                    skills_ref=self._recorded_ref(installed or {}, gen.cli_name),
                    skills_directory=None if root is None else str(root),
                    remove_pending=is_pending_removal(
                        (installed or {}).get(gen.cli_name)
                    ),
                )
            )

        # The table is the result in default mode and under -o table,
        # printed before the stderr lines below so both modes show them in
        # the same order. In json/yaml/csv output_result() serialises the
        # returned result, so printing it here would put a table in front
        # of the document.
        if _renders_table():
            console.print(_status_table(rows))

        message: str | None = unparsable
        if unparsable:
            _warning(unparsable)
        elif installed is None:
            # The table above is still worth printing -- it is how a user
            # finds which tools are present and where their skills go --
            # but every count in it is unknown, so say why. The command
            # still fails: a records file deepctl cannot read is exit 1
            # for every `dg skills` subcommand.
            message = self._unreadable_records(
                "Nothing is counted as deepctl's until that file is fixed or deleted."
            )
            _warning(message)

        if any(g.detect() and g.skills_root() is None for g in generators):
            _info(f"Tools with no skills directory: install with '{SKILLS_CLI_HINT}'.")

        detected_count = sum(1 for g in generators if g.detect())
        # Only when the records were read and hold nothing. With records
        # deepctl cannot read, an install exits 1 on the same file, so
        # "run install" after "cannot read its own records" is wrong.
        if detected_count > 0 and installed == {}:
            _info("Run 'dg skills install' to set up AI assistant integrations.")

        return SkillsStatusResult(
            status="error" if message else "success",
            message=message,
            tools=rows,
        )

    def _handle_install(
        self,
        install_all: bool = False,
        cli_name: str | None = None,
        ref: str | None = None,
    ) -> None:
        """Detect AI CLIs, prompt the user, and install the Deepgram skills."""
        from deepctl_core.skill_generator import (
            detect_ai_clis,
            get_all_generators,
        )

        # An unknown --cli is refused first, so it reads the same whatever
        # the records hold. Then the records: a skills.json deepctl cannot
        # read is a failed command before any prompt is answered, not
        # after. A bad --ref or DEEPCTL_SKILLS_REF likewise.
        if cli_name:
            generators = [g for g in get_all_generators() if g.cli_name == cli_name]
            if not generators:
                # ClickException, not print_error + return: a bare return
                # exits 0, and the README documents 1 for a command that
                # fails. main.py prints the message and exits 1.
                raise _unknown_cli(cli_name)
        self._records_or_fail(self._load_state(), _NOTHING_INSTALLED)
        resolve_skills_ref(ref)

        # If a specific CLI was requested, filter
        if cli_name:
            if not generators[0].detect():
                _warning(
                    f"{generators[0].display_name} was not detected on this system."
                )
                if not self._ask("Install anyway?", default=False):
                    # Abort rather than return: these subcommands are plain
                    # click callbacks, so nothing maps a returned result to an
                    # exit code and a bare return exits 0 -- indistinguishable
                    # from a successful install. main.py turns Abort into
                    # exit 2, which it reserves for user cancellation.
                    raise click.Abort()
        else:
            generators = detect_ai_clis()

        if not generators:
            _info("No AI coding assistants detected.")
            _info("Supported CLIs:")
            for g in get_all_generators():
                _info(f"  - {g.display_name}")
            return

        selected = [
            g
            for g in generators
            if install_all
            or cli_name
            or self._ask(
                f"Install Deepgram skills for {g.display_name}?",
                default=True,
                skip_with="--all",
            )
        ]
        if not selected:
            # Every prompt answered no: the user declined, which the
            # README documents as exit 2, the same as declining
            # "Install anyway?" above.
            _info("No tools selected, so nothing was installed.")
            raise click.Abort()

        # No lock here: core fetches first, then takes it and re-reads the
        # records through _checked_state. The copy read above predates the
        # prompts, and another deepctl may have installed while they were
        # open; holding the lock across the download would make that other
        # deepctl wait for the network.
        with self._state_errors():
            report = self._install_for(
                selected,
                {},
                ref,
                load_state=lambda: self._checked_state(_NOTHING_INSTALLED),
                # --cli names one tool: its failure is the command's.
                keep_going=not cli_name,
            )

        if report.failures:
            if report.written:
                _info(_MCP_HINT)
            raise click.ClickException(
                _failed_tools(
                    report,
                    f"Installed skills for {len(report.written)} tool(s)",
                    "No tool was installed",
                    "run the same command",
                    self._recorded_before,
                )
            )
        if report.total_written:
            _success(
                f"Installed {report.total_written} skill folder(s) "
                f"from deepgram/skills@{report.ref}"
            )
            _info(_MCP_HINT)
        elif not report.unsupported:
            _info("No skills were installed.")

    def _ask(self, message: str, default: bool, skip_with: str | None = None) -> bool:
        """A yes/no prompt on stderr, where EOF counts as declining.

        stderr so stdout stays the command's payload. ``BaseCommand.confirm``
        turns an unanswerable prompt into ``False``, so a bare ``install``
        with stdin at EOF used to exit 0 as if every tool had been
        declined one by one. An EOF here is a decline, exit 2 like any
        other, with a message saying no answer was read and, when there
        is one, the flag that skips the prompt.
        """
        from deepctl_core import output

        # The same shortcut confirm() takes: nobody is there to answer.
        if (
            output._agentic
            or not self.ci_friendly
            or not getattr(self, "_guided", True)
        ):
            return default
        try:
            return bool(click.confirm(message, default=default, err=True))
        except click.Abort as exc:
            # click raises Abort for Ctrl-C and for EOF alike; the EOF is
            # still its context. Ctrl-C keeps main.py's own message.
            if not isinstance(exc.__context__, EOFError):
                raise
            hint = (
                f" Pass {skip_with} to install without prompting." if skip_with else ""
            )
            _warning(f"No answer was read from stdin, so nothing was installed.{hint}")
            raise SystemExit(2) from None

    def _handle_update(self, ref: str | None = None) -> None:
        """Reinstall every installed tool's skills from upstream.

        Without ``--ref`` or ``DEEPCTL_SKILLS_REF`` this reinstalls the ref
        the last install recorded, so an install from ``main`` stays on
        ``main`` until the user says otherwise.

        Holds no lock of its own. The targets and ref are worked out from
        a first read; core then fetches, takes the lock and re-reads the
        records through :meth:`_checked_state` before writing anything.
        """
        from deepctl_core.skill_generator import (
            get_all_generators,
            is_pending_removal,
        )

        unreadable = (
            f"There is no list of tools to update. {_NOTHING_CHANGED} "
            "Fix or delete that file, then run 'dg skills install'."
        )
        # Checked before the records are read, so a bad --ref or
        # DEEPCTL_SKILLS_REF is exit 1 whether or not anything is installed.
        self._explicit_ref(ref)
        state = self._load_state()
        installed = self._records_or_fail(state, unreadable)

        if not installed:
            _info("No skills installed. Run 'dg skills install' first.")
            return

        generators = {g.cli_name: g for g in get_all_generators()}
        targets = []
        for cli_key in list(installed.keys()):
            gen = generators.get(cli_key)
            if gen is None:
                _warning(f"Unknown CLI '{cli_key}', skipping.")
                continue
            # A tool with no skills directory is handed on rather than
            # filtered out here. install_skills_for() is what cleans up
            # its deepctl <= 0.3.0 files and drops the record that should
            # never have existed; skipping it meant update printed the
            # same "no skills directory" warning on every run forever,
            # with no command on this path that would ever resolve it.
            targets.append(gen)

        if not targets:
            _info("Nothing to update.")
            return

        ref, source, ref_source = self._update_ref(ref, installed)
        # Refused before it is announced: "Updating to deepgram/skills@bad
        # ref" followed by "Invalid skills ref" read as a half-run update.
        try:
            validate_ref(ref, ref_source)
        except SkillRefInvalidError as exc:
            # --ref and the environment were checked above, so this is the
            # recorded ref: say which file holds it and how to get past it,
            # as the 404 for a recorded ref does.
            raise click.ClickException(
                f"{exc} That ref came from {exc.ref_source}.\n\n"
                "Nothing was updated. Pass --ref to choose another "
                "deepgram/skills ref."
            ) from None
        # Announced only when some tool is about to be written. A remove
        # still to finish is retried, not reinstalled, and a tool with no
        # skills directory has nothing written for it, so for those alone
        # "Updating to ..." read as an install that never came.
        if any(
            gen.skills_root() is not None
            and not is_pending_removal(installed.get(gen.cli_name))
            for gen in targets
        ):
            _info(f"Updating to deepgram/skills@{ref} ({source})")

        with self._state_errors():
            report = self._install_for(
                targets,
                {},
                ref,
                load_state=lambda: self._checked_state(unreadable),
                only_recorded=True,
                ref_source=ref_source,
                keep_going=True,
                retry="dg skills update",
            )
        if report.failures:
            if report.written:
                _info(_MCP_HINT)
            # A pending remove finished before the failure is done and
            # saved: named in the error, which is all -q prints.
            raise click.ClickException(
                _after_removals(
                    _failed_tools(
                        report,
                        f"Updated {len(report.written)} tool(s) to "
                        f"deepgram/skills@{report.ref}",
                        "No tool was updated",
                        "run 'dg skills update'",
                        self._recorded_before,
                    ),
                    report.removals_finished,
                )
            )
        if report.written:
            _success(
                f"Updated {len(report.written)} tool(s) to deepgram/skills@{report.ref}"
            )
            _info(_MCP_HINT)
        elif report.removals_finished or report.removals_pending:
            # Not "Nothing to update": folders were just deleted, or a
            # remove was retried. Each tool already has its own line.
            _info(_removals_summary(report))
        elif not report.unsupported:
            _info("Nothing to update.")

    def _handle_remove(
        self,
        remove_all: bool = False,
        cli_name: str | None = None,
    ) -> None:
        """Remove the skill folders deepctl recorded installing.

        Only those. These are shared directories, so a folder deepctl has
        no record of installing belongs to the user or another publisher
        and is never deleted, including when ``skills.json`` is gone, in
        which case there is nothing deepctl can prove it owns.
        """
        if not (remove_all or cli_name):
            # A usage error, which main.py turns into exit 1, checked before
            # the records are read so it is one whether or not anything is
            # installed. Printing the hint and exiting 0 made "you forgot a
            # flag" indistinguishable from "everything was removed".
            raise click.UsageError("Specify --all to remove all, or --cli NAME.")
        # Likewise a --cli no generator answers to: exit 1 before the
        # records are read, so an empty machine does not exit 0 on a typo
        # that a machine with skills installed exits 1 on. A record for a
        # tool this deepctl does not know is still dropped by --all.
        if cli_name:
            from deepctl_core.skill_generator import get_all_generators

            if all(g.cli_name != cli_name for g in get_all_generators()):
                raise _unknown_cli(cli_name)
        with self._state_guard():
            self._remove_locked(remove_all, cli_name)

    def _remove_locked(self, remove_all: bool, cli_name: str | None) -> None:
        """The body of ``remove``, run with ``skills.json`` held."""
        from deepctl_core.skill_generator import (
            get_all_generators,
            mark_pending_removal,
            recorded_skill_paths,
            save_skills_state,
        )

        state = self._load_state()
        # Failed separately from "nothing installed": the records were
        # not deleted, the file is unreadable, and only one of those two
        # is fixed by deleting folders.
        installed = self._records_or_fail(
            state,
            "It will not guess which folders are its, so nothing was "
            "removed. Fix or delete that file, then remove the skill "
            "folders by hand.",
        )

        if not installed:
            _info(
                "No skills are installed according to deepctl's records. "
                "deepctl only removes folders it recorded installing, so if "
                "those records were deleted, remove the skill folders by hand."
            )
            return

        generators = {g.cli_name: g for g in get_all_generators()}

        if cli_name:
            targets = [cli_name] if cli_name in installed else []
            if not targets:
                # Same contract as the unknown-CLI path above: asking to
                # remove something that is not there is a failed command,
                # which the README documents as exit 1, not 0.
                raise click.ClickException(f"No skills installed for '{cli_name}'.")
        else:
            # _handle_remove refused a call with neither --all nor --cli.
            targets = list(installed.keys())

        total_removed = 0
        # total_removed split in two: a skill folder and a deepctl <= 0.3.0
        # file are different things, and calling five files "folders"
        # misread what had gone.
        skills_removed = 0
        legacy_removed = 0
        tools_cleaned = 0
        stranded_total = 0
        retry_total = 0
        cleaned_in_place = 0
        # A filesystem call in here can raise -- clean_legacy unlinks
        # and rewrites files without a guard. The records already
        # updated describe deletions that have happened, so they are
        # saved either way rather than thrown away with the traceback.
        try:
            for cli_key in targets:
                gen = generators.get(cli_key)
                if gen is None:
                    # No generator, so no skills root to check a path against
                    # and nothing deepctl can prove about these folders. The
                    # record is the only thing it can honestly drop.
                    _warning(
                        f"  Unknown CLI '{cli_key}': dropping its record. Delete "
                        "any folders it left behind by hand."
                    )
                    del state["installed_skills"][cli_key]
                    continue

                recorded = recorded_skill_paths(state, cli_key)
                owned = gen.owned_skill_paths(recorded)
                removed, legacy_skipped, retryable = self._remove_recorded(
                    gen, recorded
                )
                # remove_report() also lists the deepctl <= 0.3.0 artifacts it
                # cleaned up, and those do not always go away: a shared
                # context file keeps the user's own text, and the legacy
                # command directory keeps a command they added. A bare
                # "Removed" would name a path they can still see.
                deleted = [p for p in removed if not p.exists()]
                owned_set = {str(p) for p in owned}
                deleted_skills = [p for p in deleted if str(p) in owned_set]
                for p in removed:
                    if p.exists():
                        _info(f"  Removed deepctl's content from {p}")
                    else:
                        _info(f"  Removed {p}")
                for path, reason in legacy_skipped:
                    _warning(_legacy_left_in_place(gen.display_name, path, reason))
                total_removed += len(deleted)
                skills_removed += len(deleted_skills)
                legacy_removed += len(deleted) - len(deleted_skills)
                # Counted apart from total_removed, which is a count of
                # *folders that are gone*. A path deepctl only cut its own
                # content out of is still there, so it must not inflate
                # that number -- but it did happen, and the closing
                # "Nothing was removed." would contradict the line naming
                # it that was just printed.
                cleaned_in_place += len(removed) - len(deleted)
                if deleted:
                    tools_cleaned += 1

                # A recorded path deepctl can no longer claim: the folder
                # was replaced by a symlink, or the entry was hand-edited to
                # point outside the skills root. Never deleted, so say where
                # it is instead of dropping the record silently.
                # The legacy cleanup is the authority on deepctl <= 0.3.0's
                # paths and has already said what it did with each one;
                # a second verdict here contradicted it.
                handled = [
                    *gen.legacy_locations(),
                    *removed,
                    *(path for path, _ in legacy_skipped),
                ]
                for path in self._unownable(recorded, owned, handled):
                    _warning(
                        f"  {gen.display_name}: {path} is no longer deepctl's to "
                        "delete. Remove it by hand."
                    )

                # Ownership outlives a failed deletion. rmtree can lose to a
                # permission error or a read-only mount, and dropping the
                # record then would strand Deepgram's own folders: the next
                # update refuses to overwrite what it cannot prove is its,
                # and the next remove has nothing left to act on.
                #
                # The same goes for a deepctl <= 0.3.0 file the filesystem
                # would not let the cleanup read, edit or delete. The
                # cleanup only runs for a tool that is recorded, so
                # dropping the record would leave the warning's advice --
                # fix the permissions -- with no command to finish the job.
                stranded = [p for p in owned if p.exists()]
                if stranded or retryable:
                    stranded_total += len(stranded)
                    retry_total += len(retryable)
                    entry = state["installed_skills"][cli_key]
                    # Marked, so list, status and update read it as the
                    # remove it is rather than as an install.
                    mark_pending_removal(entry, stranded, retryable)
                    what = [
                        f"{len(stranded)} folder(s)" if stranded else "",
                        f"{len(retryable)} older deepctl file(s)" if retryable else "",
                    ]
                    _warning(
                        f"  {gen.display_name}: "
                        f"{' and '.join(w for w in what if w)} could not be "
                        "removed and are still recorded as deepctl's. Fix the "
                        f"permissions and run 'dg skills remove --cli {cli_key}' "
                        "again."
                    )
                else:
                    del state["installed_skills"][cli_key]
                    if not removed and not legacy_skipped:
                        _warning(f"  {gen.display_name}: nothing left to remove.")
        finally:
            save_skills_state(state)

        failed = bool(stranded_total or retry_total)
        if total_removed:
            parts = [
                f"{skills_removed} skill folder(s)" if skills_removed else "",
                f"{legacy_removed} older deepctl file(s)" if legacy_removed else "",
            ]
            summary = (
                f"Removed {' and '.join(p for p in parts if p)} "
                f"from {tools_cleaned} tool(s)."
            )
            # No success mark on a command about to exit 1: part of it
            # worked, which is information, not success.
            (_info if failed else _success)(summary)
        elif not failed and not cleaned_in_place:
            _info("Nothing was removed.")

        if failed:
            # The command did not do what was asked, and the README
            # documents 1 for a failed command. Raised after the state
            # is saved, so the retained ownership survives the failure.
            what = [
                f"{stranded_total} recorded skill folder(s)" if stranded_total else "",
                f"{retry_total} older deepctl file(s)" if retry_total else "",
            ]
            raise click.ClickException(
                f"{' and '.join(w for w in what if w)} could not be "
                "removed. They are still recorded as deepctl's, so fix the "
                "permissions and run the remove again."
            )

    @staticmethod
    def _remove_recorded(
        gen: Any, recorded: list[str]
    ) -> tuple[list[Path], list[tuple[Path, str]], list[Path]]:
        """Delete a tool's recorded folders; also what the cleanup left alone.

        ``remove_report()`` returns the folders that are gone, the
        deepctl <= 0.3.0 leftovers it found but did not touch, and which
        of those a retry can finish once the permissions are fixed.
        """
        report: RemoveReport = gen.remove_report(recorded)
        # Core reports (path, reason). A path is never split on ": ",
        # which a Windows drive letter would also match.
        skipped = [(Path(path), str(reason)) for path, reason in report.legacy_skipped]
        retryable = [Path(path) for path in report.legacy_retryable]
        return list(report.removed), skipped, retryable

    @staticmethod
    def _unownable(
        recorded: list[str], owned: list[Path], handled: Iterable[Path] = ()
    ) -> list[Path]:
        """Recorded paths still on disk that deepctl may no longer touch.

        ``owned`` has already dropped them: a symlink standing where a
        skill folder was, or an entry pointing outside the skills root.
        Reported rather than deleted, because deleting either one is how
        deepctl would destroy something that is not its.

        Both sides are compared after ``expanduser()``, the same form
        :meth:`SkillGenerator.owned_skill_paths` keeps, so a record
        written as ``~/.claude/skills/api`` is not mistaken for a path
        deepctl may no longer touch. A relative entry is skipped for the
        same reason that method drops it: it names nothing deepctl can
        resolve, and resolving it against the working directory would
        point this warning at an unrelated file.

        ``handled`` are paths something else has already reported on,
        such as the legacy cleanup, so they are not reported twice.
        """
        keep = {str(p) for p in owned} | {str(Path(p).expanduser()) for p in handled}
        out = []
        seen = set(keep)
        for entry in recorded:
            path = Path(entry).expanduser()
            if not path.is_absolute() or str(path) in seen:
                continue
            seen.add(str(path))
            if path.is_symlink() or path.exists():
                out.append(path)
        return out

    def _handle_list(self) -> SkillsListResult:
        """Show installed skills with locations, versions and upstream ref."""
        state, unparsable = self._read_state()
        if unparsable:
            _warning(unparsable)
            return SkillsListResult(status="error", message=unparsable)
        installed = self._installed_records(state)

        if installed is None:
            message = self._unreadable_records(
                "There is nothing it can list. Fix or delete that "
                "file, then run 'dg skills install'."
            )
            _warning(message)
            return SkillsListResult(status="error", message=message)

        if not installed:
            message = "No skills installed. Run 'dg skills install' to get started."
            _info(message)
            return SkillsListResult(status="success", message=message)

        from deepctl_core.skill_generator import get_all_generators, is_pending_removal

        roots = {g.cli_name: g.skills_root() for g in get_all_generators()}
        rows: list[SkillsInstalledTool] = []
        for cli_key, info in installed.items():
            # A hand-edited entry can be anything; an unreadable one is
            # listed as a tool with nothing known about it rather than
            # taking the whole listing down.
            if not isinstance(info, dict):
                info = {}
            names = info.get("skills") or []
            paths = [str(p) for p in info.get("paths") or []]
            pending = is_pending_removal(info)
            if pending:
                # What is left of a remove: deepctl <= 0.3.0 files, which
                # are not skill folders under a skills directory. Their
                # own path, or the folder they share, is where they are;
                # the parent of ~/.aider.conf.yml rendered as "~/.".
                location: str | None = (
                    os.path.commonpath(paths)
                    if len(paths) > 1
                    else (paths[0] if paths else None)
                )
            elif names and roots.get(cli_key) is not None:
                # The tool's skills directory, not the first path's parent:
                # a partial record also holds deepctl <= 0.3.0 files such
                # as ~/.cursor/rules/deepctl.mdc, which sort first. A
                # record with no skill folders yet (a 0.3.x install) is
                # still where its files are, below.
                location = str(roots[cli_key])
            else:
                location = str(Path(paths[0]).parent) if paths else None
            rows.append(
                SkillsInstalledTool(
                    cli=cli_key,
                    deepctl_version=info.get("version"),
                    skills_ref=info.get("skills_ref"),
                    skills=[str(n) for n in names],
                    count=len(names) if pending else len(names) or len(paths),
                    location=location,
                    remove_pending=pending,
                )
            )

        if _renders_table():
            console.print(_list_table(rows))
        # A pending row is a remove still to finish: 'update' would put
        # back the skills the user asked to remove, so it gets the retry.
        for row in rows:
            if row.remove_pending:
                _info(
                    f"[dim]{row.cli}: a remove has not finished. Fix the "
                    f"permissions and run 'dg skills remove --cli {row.cli}'.[/dim]"
                )
        if any(not row.remove_pending for row in rows):
            _info("[dim]Run 'dg skills update' to reinstall from upstream.[/dim]")
        return SkillsListResult(installed=rows)

    def _handle_setup(self, install_all: bool = False, ref: str | None = None) -> None:
        """Interactive first-run setup: detect AI tools and install skills.

        Fetches the Deepgram skills from the deepgram/skills repo and
        installs each one, as a folder, into the skills directory the
        selected tool actually reads.
        """
        import sys

        from deepctl_core.skill_generator import (
            detect_ai_clis,
            get_all_generators,
        )

        is_tty = sys.stdout.isatty()

        self._records_or_fail(self._load_state(), _NOTHING_INSTALLED)
        # Before the prompt and "Installing Deepgram skills...", not after.
        resolve_skills_ref(ref)

        # 1. Detect AI coding tools
        detected = detect_ai_clis()

        if not detected:
            _info("No AI coding assistants detected on this system.")
            _info("Supported tools:")
            for g in get_all_generators():
                _info(f"  - {g.display_name}")
            return

        # 2. Interactive selection (or --all for CI)
        if install_all:
            selected = list(detected)
        elif is_tty and self._guided:
            status_console.print("\n[bold]Detected AI coding tools:[/bold]\n")
            for i, g in enumerate(detected, 1):
                status_console.print(f"  [green]{i}.[/green] {g.display_name}")
            status_console.print()

            try:
                raw = click.prompt(
                    "Install skills for (comma-separated numbers, all, or none)",
                    default="all",
                    err=True,
                )
            except click.Abort as exc:
                # The same rule as install's prompts: EOF is a decline.
                if not isinstance(exc.__context__, EOFError):
                    raise
                _warning(
                    "No answer was read from stdin, so nothing was installed. "
                    "Pass --all to install without prompting."
                )
                raise SystemExit(2) from None
            raw = raw.strip().lower()

            if raw in ("none", "n", "0"):
                # Declined, which is exit 2, as for install's prompts.
                _info("No skills installed.")
                raise click.Abort()

            if raw == "all":
                selected = list(detected)
            else:
                indices: set[int] = set()
                for part in raw.split(","):
                    part = part.strip()
                    if part.isdigit():
                        idx = int(part)
                        if 1 <= idx <= len(detected):
                            indices.add(idx - 1)
                selected = [detected[i] for i in sorted(indices)]

            if not selected:
                # An answer naming no tool installs nothing, which is a
                # decline: exit 2, the same as answering "none".
                choices = ", ".join(str(i) for i in range(1, len(detected) + 1))
                _info(
                    f"'{raw}' matches no detected tool, so nothing was "
                    f"installed. Choose from {choices}, all, or none."
                )
                raise click.Abort()
        else:
            # Non-TTY without --all: install for all detected
            selected = list(detected)

        # 3. Install the upstream skills for each selected tool
        _info("Installing Deepgram skills...")

        # Core fetches, then takes the lock and re-reads the records:
        # the copy above predates the prompt.
        with self._state_errors():
            report = self._install_for(
                selected,
                {},
                ref,
                load_state=lambda: self._checked_state(_NOTHING_INSTALLED),
                keep_going=True,
            )

        if report.failures:
            if report.written:
                _info(_MCP_HINT)
            raise click.ClickException(
                _failed_tools(
                    report,
                    f"Installed skills for {len(report.written)} tool(s)",
                    "No tool was set up",
                    "run the same command",
                    self._recorded_before,
                )
            )
        if report.total_written:
            _success(
                f"Setup complete - {report.total_written} skill folder(s) from "
                f"deepgram/skills@{report.ref}"
            )
            _info(_MCP_HINT)
        elif not report.unsupported:
            _info("No skills were installed.")


def _renders_table() -> bool:
    """Whether a handler prints its table: default mode and -o table."""
    return get_output_format() in ("default", "table")


def _status_table(tools: list[SkillsToolStatus]) -> Table:
    """The `dg skills status` table, for default mode and -o table/csv."""
    table = Table(title="AI Coding Assistant Status")
    table.add_column("CLI", style="cyan", no_wrap=True)
    table.add_column("Detected", style="white")
    table.add_column("Deepgram Skills", style="white")
    # Folded, never truncated: the ref and the path are what a user
    # copies out of this table, and an 80-column terminal cut both short.
    table.add_column("Skills ref", style="green", overflow="fold")
    table.add_column("Skills Directory", style="dim", overflow="fold")
    for tool in tools:
        count = tool.installed
        if tool.skills_directory is None:
            installed_cell = (
                "[yellow]remove pending[/yellow]"
                if tool.remove_pending
                else "[dim]n/a[/dim]"
            )
            root_cell = "[dim]no skills directory[/dim]"
        else:
            if count is None:
                installed_cell = "[yellow]?[/yellow]"
            elif tool.remove_pending:
                installed_cell = "[yellow]remove pending[/yellow]"
            elif count:
                installed_cell = f"[green]{count}[/green]"
            else:
                installed_cell = "[dim]No[/dim]"
            root_cell = escape(_tilde(Path(tool.skills_directory)))
        table.add_row(
            escape(tool.display_name),
            "[green]Yes[/green]" if tool.detected else "[dim]No[/dim]",
            installed_cell,
            escape(tool.skills_ref) if count and tool.skills_ref else "[dim]-[/dim]",
            root_cell,
        )
    return table


def _list_table(installed: list[SkillsInstalledTool]) -> Table:
    """The `dg skills list` table, for default mode and -o table/csv."""
    table = Table(title="Installed Skills")
    table.add_column("CLI", style="cyan", no_wrap=True)
    table.add_column("deepctl", style="green", overflow="fold")
    table.add_column("Skills ref", style="green", overflow="fold")
    table.add_column("Skills", style="white")
    table.add_column("Location", style="dim", overflow="fold")
    for tool in installed:
        table.add_row(
            escape(tool.cli),
            escape(tool.deepctl_version or "?"),
            escape(tool.skills_ref or "?"),
            "[yellow]remove pending[/yellow]"
            if tool.remove_pending
            else str(tool.count),
            escape(_tilde(Path(tool.location))) if tool.location else "?",
        )
    return table


def _plain_rows(table: Table) -> list[list[str]]:
    """``table``'s cells as plain text, row by row, for -o csv."""
    columns = [
        [Text.from_markup(c).plain if isinstance(c, str) else str(c) for c in col.cells]
        for col in table.columns
    ]
    return [list(row) for row in zip(*columns, strict=True)]


def _tilde(path: Path) -> str:
    """Render a path under the user's home as ~/... so tables stay readable."""
    try:
        relative = path.relative_to(Path.home())
    except ValueError:
        return str(path)
    # The home directory itself is "~", not "~/.".
    return "~" if relative == Path() else f"~/{relative}"


def _deepctl_version() -> str:
    """Return the installed deepctl version, or a placeholder."""
    try:
        return importlib.metadata.version("deepctl")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0"
