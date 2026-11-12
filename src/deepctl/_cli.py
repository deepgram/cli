"""The Click CLI behind ``dg``: the root group, command loading and ``run()``.

``deepctl.main`` imports this module only on the full path. ``dg --version``
and the ``dg mcp`` handshake never load it (or Click, or ``deepctl_core``).

Commands load lazily. The root group is a ``LazyGroup``: it lists the
built-in commands from the manifest (``deepctl._manifest``) and imports a
command's module only when that command runs or shows its own help. Plugins
are discovered only for a name that isn't built in, for root ``--help`` and
for ``dg plugin list``. ``DEEPCTL_EAGER_LOAD=1`` (0.5.x only) loads every
command up front, as 0.4.x did.

``dg <cmd> --help`` and ``dg <group> <sub> --help`` for a built-in leaf
render from the command's help spec (``deepctl._help_spec``) without
importing its module, and the root callback then only scans the config
files (``Config(scan_only=True)``): see ``LazyGroup._help_command``.
"""

from __future__ import annotations

import os
import sys
import time
from typing import TYPE_CHECKING, Any, cast

import click
from deepctl_core import (
    TimingContext,
    enable_timing,
    print_timing_summary,
    setup_output,
)
from deepctl_core.output import stderr_console

from deepctl import _manifest

if TYPE_CHECKING:
    from collections.abc import Callable

    from deepctl_core import Config
    from deepctl_core.plugin_manager import PluginManager

# 0.5.x only: load every command at startup, as 0.4.x did (removed in 0.6.0).
EAGER_LOAD_ENV_VAR = "DEEPCTL_EAGER_LOAD"
# The program name shown in usage lines (the launcher and `python -m deepctl`
# set it; `--version` ignores it).
PROG_NAME_ENV_VAR = "DEEPCTL_PROG_NAME"
# Set in the root context's obj when a --help run found no readable config
# (an unreadable HOME): a group's callback then leaves its subcommand to
# show its help.
HELP_WITHOUT_CONFIG = "help_without_config"

E_DEPENDENCY = "{prog} {command_path} needs {dependency}, which isn't installed."
H_DEPENDENCY_REINSTALL = "Reinstall {prog} with the tool you installed it with."
A_DEPENDENCY = (
    "Tell the user {prog} needs {dependency}. Suggested fix: {dependency_install_hint}"
)
# Agent-mode usage errors print ``ERROR: {message}`` and then this hint.
A_USAGE = "Run {prog} {command_path} --help to see valid options."


def eager_load_requested() -> bool:
    """``DEEPCTL_EAGER_LOAD=1``: load every command up front (0.5.x only)."""
    return os.environ.get(EAGER_LOAD_ENV_VAR, "") == "1"


class DependencyMissingError(click.ClickException):
    """A command's module couldn't be imported: ``dependency_missing``.

    The catalogue entry: exit 1, ``next_step`` null and
    ``details.dependency`` naming what is missing (the command's
    distribution when its own package is gone).
    """

    exit_code = 1

    def __init__(self, prog: str, command_path: str, dependency: str) -> None:
        self.prog = prog
        self.command_path = command_path
        self.dependency = dependency
        super().__init__(
            E_DEPENDENCY.format(
                prog=prog, command_path=command_path, dependency=dependency
            )
        )

    def payload(self, hint: str) -> dict[str, Any]:
        """The coded error object, keys in the catalogue's order."""
        return {
            "error": self.message,
            "status": "error",
            "code": "dependency_missing",
            "hint": hint,
            "next_step": None,
            "schema_version": 1,
            "details": {"dependency": self.dependency},
        }

    def show(self, file: Any = None) -> None:  # noqa: ARG002 (stderr/stdout)
        from deepctl_core import output

        install_hint = H_DEPENDENCY_REINSTALL.format(prog=self.prog)
        if output.is_agentic() or output.agent_mode():
            hint = A_DEPENDENCY.format(
                prog=self.prog,
                dependency=self.dependency,
                dependency_install_hint=install_hint,
            )
        else:
            hint = install_hint
        from rich.markup import escape

        # The program name comes from argv or DEEPCTL_PROG_NAME: text, not markup.
        output.print_error(escape(self.message))
        output.print_hint(escape(hint))
        if output.get_output_format() in ("json", "yaml"):
            payload = self.payload(hint)
            if output.get_output_format() == "yaml":
                import yaml

                text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
            else:
                import json

                text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
            try:
                sys.stdout.write(text)
                sys.stdout.flush()
            except (OSError, ValueError):
                pass


def _missing_dependency(error: ImportError, entry: dict[str, Any]) -> str:
    """What to name in ``dependency_missing``: the command's distribution when
    its own package is what's missing, else the module that failed."""
    missing = (error.name or "").split(".")[0]
    own = str(entry["module"]).split(".")[0]
    if not missing or missing == own:
        return str(entry["dist"])
    return missing


def _setup_output_from(ctx: click.Context) -> None:
    """Apply the root output options before the root callback has run
    (commands are resolved before it), so an error renders as asked."""
    params = ctx.find_root().params
    setup_output(
        format_type=params.get("output") or "default",
        quiet=bool(params.get("quiet")),
        verbose=bool(params.get("verbose")),
    )


class _Listed(click.Command):
    """A command as root ``--help`` lists it, built from the manifest without
    importing it. Only the attributes ``format_commands`` reads are set."""

    def __init__(self, name: str, entry: dict[str, Any]) -> None:
        short_help = entry.get("summary")
        if short_help and entry.get("summary_more"):
            # A cached plugin row: its paragraphs, as Click keeps them.
            short_help = "\n\n".join([short_help, *entry["summary_more"]])
        super().__init__(
            name,
            short_help=short_help,
            help=entry.get("help"),
            hidden=bool(entry.get("hidden", False)),
            deprecated=entry.get("deprecated", False),
        )


class _MissingCommand(click.Command):
    """A built-in subcommand whose module can't be imported. Hidden, as a
    missing command was in 0.4.x; running it reports ``dependency_missing``."""

    def __init__(self, name: str, error: DependencyMissingError) -> None:
        super().__init__(name, hidden=True, add_help_option=False)
        self.error = error

    def make_context(
        self,
        info_name: str | None,
        args: list[str],
        parent: click.Context | None = None,
        **extra: Any,
    ) -> click.Context:
        del info_name, args, extra  # nothing to parse: it can't run
        if parent is not None:
            _setup_output_from(parent)
        raise self.error


def _nothing_to_discover() -> bool:
    """True when no plugin can be installed: every ``deepctl.commands`` entry
    point is a built-in's own, there is no ``deepctl.plugins`` entry point
    and no ``plugins.json`` for a plugin venv. Then discovery has nothing to
    load, and ``deepctl_core`` needn't be imported to find that out."""
    from importlib import metadata

    from deepctl import _cache

    builtins = _manifest.builtin_command_modules()
    entry_points = metadata.entry_points()
    for entry_point in entry_points.select(group="deepctl.commands"):
        if (
            builtins.get(entry_point.name)
            != entry_point.value.partition(":")[0].strip()
        ):
            return False
    if any(True for _ in entry_points.select(group="deepctl.plugins")):
        return False
    return not any(
        path.endswith("plugins.json") and os.path.lexists(path)
        for path in _cache.plugin_state_paths()
    )


def _only_builtin_subcommands(group: str) -> bool:
    """Whether every ``deepctl.subcommands.<group>`` and
    ``deepctl.subplugins.<group>`` entry point is a built-in subcommand's
    own (same name, module and class as in the manifest).

    A yes is remembered for the plugin stamp (``_cache.plugin_stamp``, the
    one the discovery cache uses): reading every distribution's entry
    points costs far more than the rest of the help.
    """
    from deepctl import _cache

    try:
        stamp: str | None = _cache.plugin_stamp()
    except Exception:
        stamp = None
    if stamp is not None and _cache.read_builtin_only(stamp, group):
        return True
    if not _scan_builtin_only(group):
        return False
    if stamp is not None:
        _cache.write_builtin_only(stamp, group)
    return True


def _scan_builtin_only(group: str) -> bool:
    from importlib import metadata

    builtins = {
        name: f"{entry['module']}:{entry['attr']}"
        for name, entry in _manifest.subcommands(group).items()
    }
    entry_points = metadata.entry_points()
    for kind in ("subcommands", "subplugins"):
        for entry_point in entry_points.select(group=f"deepctl.{kind}.{group}"):
            value = entry_point.value.replace(" ", "")
            if kind == "subplugins" or builtins.get(entry_point.name) != value:
                return False
    return True


class LazyGroup(click.Group):
    """The root group: built-in commands come from the manifest and load on
    first use; plugins are discovered only when needed.

    Discovery (``PluginManager.discover_plugins``) runs for a name that
    isn't built in, for root ``--help``, for ``dg plugin list`` and for a
    built-in name listed in ``plugins.allow_override``. Every 0.4.1 plugin
    rule applies unchanged there: reserved built-in names,
    ``W-PLUGIN-BADNAME``, ``plugins.allow_override`` and the trust root.
    Built-ins are never loaded through an entry point: their module comes
    from the manifest.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.eager = False
        self._manager: PluginManager | None = None
        self._discovered = False
        # Plugin commands from the discovery cache: listed, not loaded.
        self._cached_plugins: dict[str, dict[str, Any]] = {}
        # Root help as rendered, and the program name it used (help cache).
        self.rendered_help: tuple[str, str] | None = None
        # (name, command) when this run shows a built-in's help from its spec.
        self.help_from_spec: tuple[str, click.Command] | None = None

    def _plugin_manager(self) -> PluginManager:
        if self._manager is None:
            from deepctl_core.plugin_manager import PluginManager

            self._manager = PluginManager()
        return self._manager

    def discover(self) -> None:
        """Register plugin commands (once per process)."""
        if self._discovered or self.eager:
            return
        self._discovered = True
        with TimingContext("plugin_discovery"):
            try:
                self._plugin_manager().discover_plugins(self)
            except OSError as exc:
                from deepctl_core.plugin_manager import warn_plugins_unavailable

                warn_plugins_unavailable(exc)

    def _overridden(self, name: str) -> bool:
        """Whether ``plugins.allow_override`` lists this built-in name."""
        from deepctl_core._plugin_trust import allow_override_names

        return name in allow_override_names()

    def _load_builtin(self, ctx: click.Context, name: str) -> click.Command | None:
        entry = _manifest.top_level()[name]
        try:
            with TimingContext(f"load_command_{name}"):
                self._plugin_manager().load_builtin(self, name, entry)
        except ImportError as exc:
            _setup_output_from(ctx)
            raise DependencyMissingError(
                ctx.find_root().info_name or "dg",
                name,
                _missing_dependency(exc, entry),
            ) from exc
        command = self.commands.get(name)
        if isinstance(command, click.Group):
            self._fill_subcommands(ctx, name, command)
        return command

    def _fill_subcommands(
        self, ctx: click.Context, group_name: str, group: click.Group
    ) -> None:
        """Load the group's built-in subcommands its entry point scan missed,
        from the manifest; one that can't be imported reports
        ``dependency_missing`` when it's used."""
        from deepctl_core.plugin_manager import _load_error

        prog = ctx.find_root().info_name or "dg"
        for name, entry in _manifest.subcommands(group_name).items():
            if name in group.commands:
                continue
            try:
                self._plugin_manager().load_builtin_subcommand(
                    group, group_name, name, entry
                )
            except ImportError as exc:
                group.add_command(
                    _MissingCommand(
                        name,
                        DependencyMissingError(
                            prog,
                            f"{group_name} {name}",
                            _missing_dependency(exc, entry),
                        ),
                    )
                )
            except Exception as exc:
                _load_error("subcommand", f"{name} for {group_name}", exc)

    def _help_command(
        self, ctx: click.Context, args: list[str]
    ) -> click.Command | None:
        """The command to resolve when ``args`` (what follows the root
        options) asks only for a built-in leaf's help, built from its help
        spec without importing it; ``None`` takes the normal path.

        Only for exactly ``<cmd> --help`` or ``<group> <sub> --help``, a
        leaf with a spec, not listed (nor its group) in
        ``plugins.allow_override``, outside ``DEEPCTL_EAGER_LOAD``. For a
        subcommand, every entry point the group would scan must be the
        built-in's own: the normal path imports and vets those, and a
        plugin's would print there. Group help always takes the normal
        path, which lists plugin subcommands.
        """
        if self.eager or ctx.parent is not None or ctx.resilient_parsing:
            return None
        if eager_load_requested() or args[-1:] != ["--help"]:
            return None
        path = args[:-1]
        if len(path) not in (1, 2) or path[0] not in _manifest.top_level():
            return None
        if path[0] in self.commands:
            return None
        if len(path) == 2 and path[1] not in _manifest.subcommands(path[0]):
            return None
        if self._overridden(path[0]) or self._overridden(" ".join(path)):
            return None
        from deepctl import _help_spec

        spec = _manifest.help_spec(path[0])
        if spec is None:
            return None
        if len(path) == 1:
            return None if spec.get("group") else _help_spec.from_spec(spec)
        sub = _manifest.help_spec(" ".join(path))
        if sub is None or sub.get("group") or not spec.get("group"):
            return None
        if not _only_builtin_subcommands(path[0]):
            return None
        group = cast("click.Group", _help_spec.from_spec(spec))
        group.add_command(_help_spec.from_spec(sub), name=path[1])
        return group

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        if self.help_from_spec is not None and self.help_from_spec[0] == cmd_name:
            return self.help_from_spec[1]
        command = self.commands.get(cmd_name)
        if command is not None or self.eager:
            return command
        if cmd_name in _manifest.top_level():
            if self._overridden(cmd_name):
                self.discover()
                command = self.commands.get(cmd_name)
                if command is not None:
                    return command
            return self._load_builtin(ctx, cmd_name)
        self.discover()
        return self.commands.get(cmd_name)

    def list_commands(self, ctx: click.Context) -> list[str]:
        if self.eager:
            return super().list_commands(ctx)
        if ctx.resilient_parsing:
            # Shell completion offers plugin commands too, as 0.4.x did.
            self._discover_for_listing()
        return sorted(
            set(self.commands) | set(_manifest.top_level()) | set(self._cached_plugins)
        )

    def _discover_for_listing(self) -> None:
        """Discovery for root ``--help``, through the discovery cache.

        A cache hit lists the plugins without importing them (or
        ``deepctl_core``). On a miss, a quick entry point scan skips the
        full discovery when no plugin is installed at all.
        """
        if self._discovered:
            return
        from deepctl import _cache

        reserved = frozenset(_manifest.top_level()) | frozenset(_manifest.aliases())
        try:
            stamp: str | None = _cache.plugin_stamp()
        except Exception:
            stamp = None
        if stamp is not None:
            cached = _cache.read_discovery(stamp, reserved)
            if cached is not None:
                self._cached_plugins = {c["name"]: c for c in cached}
                return
        before = dict(self.commands)
        watch = _cache.StderrWatch(sys.stderr)
        sys.stderr = watch
        try:
            if _nothing_to_discover():
                self._discovered = True
            else:
                self.discover()
        finally:
            if sys.stderr is watch:
                sys.stderr = watch.stream
        added = {
            name: command
            for name, command in self.commands.items()
            if before.get(name) is not command
        }
        replaces_builtin = any(name in _manifest.top_level() for name in added)
        if stamp is not None and not watch.written and not replaces_builtin:
            rows = []
            for name, command in sorted(added.items()):
                row = _cache.listing_row(name, command)
                if row is None:
                    break  # one row the cache can't reproduce: keep nothing
                rows.append(row)
            else:
                _cache.write_discovery(stamp, rows, reserved)

    def get_help(self, ctx: click.Context) -> str:
        text = super().get_help(ctx)
        if ctx.parent is None:
            self.rendered_help = (text, ctx.info_name or "dg")
        return text

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        if ctx.parent is None:
            self.help_from_spec = None
        if args:
            command = self._help_command(ctx, args)
            if command is not None:
                self.help_from_spec = (args[0], command)
                return super().resolve_command(ctx, args)
            args = self._expand_command_word(ctx, args)
        if args[:2] == ["plugin", "list"]:
            # 0.4.x loaded (and vetted) every plugin before `plugin list`.
            self.discover()
        return super().resolve_command(ctx, args)

    def _expand_command_word(self, ctx: click.Context, args: list[str]) -> list[str]:
        """Rewrite a hyphenated command word to its command path.

        Only the command word changes; the rest of argv is the command's own
        as in 0.4.x. A hidden alias (``debug-audio``) always means its built-in,
        unless ``plugins.allow_override`` lists the alias. Any other
        ``<group>-<x>`` runs ``<group> <x>`` when ``<group>`` is a built-in
        group and ``<x>`` is one of its subcommands, a plugin's included
        (0.4.1 parity); otherwise argv is left for unknown-name discovery.
        """
        word = args[0]
        if word in _manifest.top_level():
            return args
        target = _manifest.aliases().get(word)
        if target is not None:
            if self._overridden(word):
                # The plugin allowed to replace the alias runs, if one did
                # register it; otherwise the built-in does, as for names.
                self.discover()
                if word in self.commands:
                    return args
            return [*target, *args[1:]]
        prefix, _, rest = word.partition("-")
        if rest and prefix in _manifest.groups():
            try:
                group = self.get_command(ctx, prefix)
            except DependencyMissingError:
                # The group can't load: leave the word to plugin discovery.
                return args
            if isinstance(group, click.Group) and group.get_command(ctx, rest):
                return [prefix, rest, *args[1:]]
        return args

    def format_commands(
        self, ctx: click.Context, formatter: click.HelpFormatter
    ) -> None:
        if self.eager:
            super().format_commands(ctx, formatter)
            return
        self._discover_for_listing()
        # Built-ins always win over anything the discovery cache holds.
        listed = {**self._cached_plugins, **_manifest.top_level()}
        rows: list[tuple[str, click.Command]] = []
        for name in self.list_commands(ctx):
            command = self.commands.get(name) or _Listed(name, listed[name])
            if not command.hidden:
                rows.append((name, command))
        if rows:
            limit = formatter.width - 6 - max(len(name) for name, _ in rows)
            with formatter.section("Commands"):
                formatter.write_dl(
                    [(name, cmd.get_short_help_str(limit)) for name, cmd in rows]
                )


# Diagnostics go to stderr so stdout carries only the requested payload -- a
# crash or usage error must not corrupt `dg -o json ...` for a script piping
# stdout into jq. Same console the command layer uses via print_error(), so
# root-level and command-level diagnostics format identically (including the
# no-color handling for agentic/CI callers).
console = stderr_console


def _record_install_method_cb(
    ctx: click.Context, param: click.Parameter, value: str | None
) -> None:
    """Write the installation method to config. Called by install scripts."""
    if not value or ctx.resilient_parsing:
        return
    from deepctl_core import Config

    try:
        config = Config()
        config._set_config_value("update.installation_method", value)
        config.save(implicit=True)
    except OSError as exc:
        # A failed write must not look like success to the install script.
        console.print(f"[red]Error:[/red] could not save the config file: {exc}")
        # sys.exit, not ctx.exit: main() runs Click with standalone_mode=False,
        # where ctx.exit(1) would only become an ignored return value.
        sys.exit(1)
    except Exception:
        pass
    ctx.exit()


def _root_flag_opts() -> set[str]:
    """Root options that take no value: flags, plus `--help`."""
    opts = {"--help"}
    for param in cli.params:
        if isinstance(param, click.Option) and param.is_flag:
            opts.update(param.opts)
            opts.update(param.secondary_opts)
    return opts


def _takes_next_token(arg: str, flag_opts: set[str]) -> bool:
    """Whether root option `arg` takes the following token as its value.

    Mirrors click: flags take nothing, `--opt=value` and `-Xvalue` carry
    their value, and a short cluster such as `-qv` or `-qpprod` is read
    letter by letter until the first option that takes a value.
    """
    if arg in flag_opts or "=" in arg:
        return False
    if arg.startswith("--") or len(arg) <= 2:
        return True
    letters = arg[1:]
    for index, letter in enumerate(letters):
        if f"-{letter}" in flag_opts:
            continue
        # The first option that takes a value: anything after it is the
        # attached value; nothing after it means the next token is.
        return index == len(letters) - 1
    return False


def _root_option_tokens(args: list[str]) -> list[str]:
    """The option tokens before the subcommand (values skipped)."""
    flag_opts = _root_flag_opts()
    tokens: list[str] = []
    i = 0
    while i < len(args) and args[i].startswith("-"):
        arg = args[i]
        tokens.append(arg)
        i += 1
        if _takes_next_token(arg, flag_opts):
            i += 1  # skip the option's value
    return tokens


def _full_output_cb(ctx: click.Context, _param: click.Parameter, value: bool) -> None:
    """``--full-output``: shared with every subcommand through ``ctx.meta``."""
    if value:
        from deepctl_core.result_store import FULL_OUTPUT_META

        ctx.meta[FULL_OUTPUT_META] = True


def _no_telemetry_cb(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    """Turn telemetry off for this run. Eager, so root `--help` reflects it."""
    if not value or ctx.resilient_parsing:
        return
    try:
        from deepctl_telemetry import disable_for_run

        disable_for_run()
    except Exception:
        pass


def _init_telemetry(config: Config) -> None:
    """Decide telemetry from the resolved Config. Never loads the SDK."""
    try:
        from deepctl_telemetry import init_telemetry

        init_telemetry(config)
    except Exception:
        pass


def _report_error(exc: BaseException) -> None:
    """Send an error report if telemetry is on (the SDK loads only here).

    A Ctrl-C while the report is sent is still a user cancellation: exit 2.
    """
    try:
        from deepctl_telemetry import report_exception

        report_exception(exc)
    except KeyboardInterrupt:
        _safe_console_print("\n[yellow]Stopped sending the error report.[/yellow]")
        sys.exit(2)
    except Exception:
        pass


# The arguments run() was called with (Click clears them from the root
# context before the root callback runs).
_run_args: list[str] | None = None


def _help_requested() -> bool:
    """Whether this run asks for help (and so needs no readable config)."""
    args = sys.argv[1:] if _run_args is None else _run_args
    return "--help" in args


def _argv_config_path(args: list[str]) -> str | None:
    """`--config` from the root arguments, only for the notice on root `--help`.

    Root `--help` is eager and fires before the root callback resolves
    `--config`; every other path uses the parsed value. Accepts
    `--config X`, `--config=X`, `-c X`, `-cX` and clusters such as `-qcX`.
    When the option repeats, the last value wins, as in click.
    """
    flag_opts = _root_flag_opts()
    found: str | None = None
    i = 0
    while i < len(args) and args[i].startswith("-"):
        arg = args[i]
        i += 1
        if arg.startswith("--config="):
            found = arg.split("=", 1)[1]
        elif arg == "--config":
            found = args[i] if i < len(args) else None
        elif not arg.startswith("--"):
            for index, letter in enumerate(arg[1:]):
                if f"-{letter}" in flag_opts:
                    continue
                if letter == "c":
                    rest = arg[index + 2 :]
                    found = rest or (args[i] if i < len(args) else None)
                break
        if _takes_next_token(arg, flag_opts):
            i += 1
    return found


def _show_version(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    """``--version`` past the argv peek: the line ``deepctl.main`` prints."""
    del param
    if not value or ctx.resilient_parsing:
        return
    from deepctl.main import version_line

    click.echo(version_line(), color=ctx.color)
    ctx.exit()


# Create CLI group
@click.group(name="deepctl", cls=LazyGroup)
@click.option(
    "--version",
    is_flag=True,
    expose_value=False,
    is_eager=True,
    callback=_show_version,
    help="Show the version and exit.",
)
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=True),
    help="Path to configuration file",
)
@click.option(
    "--profile",
    "-p",
    help="Configuration profile to use",
)
@click.option(
    "--base-url",
    help=(
        "Override the API base URL (e.g. https://api.eu.deepgram.com). "
        "Hosts other than Deepgram API hosts must be listed in "
        "api.allowed_hosts. "
        "REST and WebSocket endpoints are derived from this host. "
        "Also settable via the DEEPGRAM_BASE_URL environment variable."
    ),
)
@click.option(
    "--api-key",
    help=(
        "Deepgram API key to use, overriding login/profile/env credentials. "
        "Intended for testing and CI."
    ),
)
@click.option(
    "--output",
    "-o",
    type=click.Choice(["json", "yaml", "table", "csv"], case_sensitive=False),
    help="Output format",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Suppress non-essential output",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Enable verbose output",
)
@click.option(
    "--timing",
    is_flag=True,
    help="Show performance timing information",
)
@click.option(
    "--timing-detailed",
    is_flag=True,
    help="Show detailed performance timing information",
)
@click.option(
    "--no-telemetry",
    is_flag=True,
    is_eager=True,
    expose_value=False,
    callback=_no_telemetry_cb,
    help="No effect: this version of dg sends no telemetry",
)
@click.option(
    "--non-interactive",
    is_flag=True,
    is_eager=True,
    expose_value=False,
    help="Skip interactive prompts; use defaults for any optional features.",
)
@click.option(
    "--full-output",
    is_flag=True,
    expose_value=False,
    callback=_full_output_cb,
    help=(
        "In an AI agent session, print a large result in full instead of "
        "saving it to a file and printing a summary."
    ),
)
@click.option(
    "--record-install-method",
    is_eager=True,
    expose_value=False,
    hidden=True,
    callback=_record_install_method_cb,
    metavar="METHOD",
    help="Internal: record the installation method used by the install script",
)
@click.pass_context
def cli(
    ctx: click.Context,
    config: str | None,
    profile: str | None,
    base_url: str | None,
    api_key: str | None,
    output: str | None,
    quiet: bool,
    verbose: bool,
    timing: bool,
    timing_detailed: bool,
) -> None:
    """\b
    ████████████████
    ██████████████████
    ████████████████████
    █████████████████████
    ███████      ████████
    ███████       ███████
                 ████████
          ███████████████
        ████████████████
      ████████████████
    ████████████████

    deepctl — Official Deepgram CLI
    STT · TTS · Audio Intelligence"""

    # Enable timing if requested
    if timing or timing_detailed:
        enable_timing()

    with TimingContext("cli_initialization"):
        # Initialize configuration. A --base-url flag overrides the
        # configured/env base URL for this run only; Config records that it
        # came from the command line, so it is never saved and gets
        # credentials only if the host is trusted.
        ctx.ensure_object(dict)
        # An unexpanded ${...} in a DEEPGRAM_* variable counts as unset
        # (W-ENV-PLACEHOLDER, printed once the output mode is known).
        from deepctl_core.credentials import drop_env_placeholders

        drop_env_placeholders()

        from deepctl_core import Config

        # Help from a spec runs no command: only the files' checks and
        # notices are needed, not the settings (no pydantic).
        scan_only = isinstance(ctx.command, LazyGroup) and (
            ctx.command.help_from_spec is not None
        )
        try:
            ctx.obj["config"] = Config(
                config_path=config,
                profile=profile,
                base_url=base_url,
                scan_only=scan_only,
            )
        except OSError:
            # An unreadable HOME must not block `dg <cmd> --help`.
            # Anything that runs still fails, on its own Config().
            if not _help_requested():
                raise
            ctx.obj["config"] = None
            ctx.obj[HELP_WITHOUT_CONFIG] = True
        # Global --api-key passthrough (highest-precedence explicit credential).
        ctx.obj["api_key"] = api_key
        ctx.obj["timing"] = timing or timing_detailed
        ctx.obj["timing_detailed"] = timing_detailed

        # Setup output formatting
        setup_output(
            format_type=output or "default",
            quiet=quiet,
            verbose=verbose,
        )

        # Telemetry is decided here, after option parsing, so --config and
        # --no-telemetry are honored.
        if ctx.obj["config"] is not None:
            _init_telemetry(ctx.obj["config"])


_commands_loaded = False


def load_commands() -> None:
    """Load every command into ``cli`` up front, once, as 0.4.x did.

    For ``DEEPCTL_EAGER_LOAD=1`` and for importers that read ``cli.commands``
    (``get_cli()``). Runs inside ``run()``'s error handling, so an
    unreadable HOME or plugin cache degrades to one ``WARN:`` line instead
    of a traceback.
    """
    global _commands_loaded
    if _commands_loaded:
        return
    _commands_loaded = True
    from deepctl_core import PluginManager

    cli.eager = True
    with TimingContext("plugin_loading"):
        try:
            PluginManager().load_plugins(cli)
        except OSError as exc:
            from deepctl_core.plugin_manager import warn_plugins_unavailable

            warn_plugins_unavailable(exc)


def get_cli() -> LazyGroup:
    """The root group with every command loaded (for importers and tests)."""
    load_commands()
    return cli


def _command_names(ctx: click.Context | None) -> tuple[str, str]:
    """``(root, command)`` for ``ctx``: the program name as typed (or
    ``DEEPCTL_PROG_NAME``) and the subcommand path after it ("" at the root)."""
    if ctx is None:
        return prog_name("dg") or "dg", ""
    root = ctx.find_root().info_name or prog_name("dg") or "dg"
    path = ctx.command_path
    command = path[len(root) :].strip() if path.startswith(root) else ""
    return root, command


def _usage_hint(ctx: click.Context | None) -> str:
    """``A-USAGE`` filled for ``ctx``, with no double space at the root."""
    root, command = _command_names(ctx)
    return " ".join(A_USAGE.format(prog=root, command_path=command).split())


def _global_option_hint(
    message: str, ctx: click.Context | None = None, *, markup: bool = True
) -> str | None:
    """Build a placement hint when a 'No such option' names a global option.

    Global options belong before the subcommand (``dg -o json models``).
    When one is placed after the subcommand instead (``dg models --profile
    staging``), Click reports "No such option" with no explanation, which
    reads as if the option does not exist at all. This looks the failing
    option up on the root group and, when it is a global one, returns a
    hint showing the working placement, with the root and subcommand names
    taken from ``ctx``.
    """
    import re

    match = re.search(r"[Nn]o such option:?\s*'?(--?[A-Za-z][\w-]*)'?", message)
    if not match:
        return None
    token = match.group(1)

    for param in cli.params:
        opts = list(getattr(param, "opts", [])) + list(
            getattr(param, "secondary_opts", [])
        )
        if token in opts:
            takes_value = not getattr(param, "is_flag", False)
            usage = f"{token} <value>" if takes_value else token
            root, command = _command_names(ctx)
            text = (
                f"{token} is a global option, so it goes before the command: "
                f"{root} {usage} {command or '<command>'} …"
            )
            if not markup:
                return text
            from rich.markup import escape

            return f"[yellow]Hint: {escape(text)}[/yellow]"
    return None


def _safe_console_print(message: str) -> None:
    """Print a diagnostic to stderr, tolerating a closed/broken stream.

    When `dg` runs as an MCP server, the host can close stdio before the
    process finishes. A write to the closed stream raises ``BrokenPipeError``
    (or rich's ``ValueError: I/O operation on closed file``); there is nowhere
    left to report the message, so swallow it rather than crash on exit.
    """
    try:
        console.print(message)
    except (BrokenPipeError, OSError, ValueError):
        pass


def _agent_lines(error: str, hint: str) -> None:
    """Agent mode: exactly two prefixed lines (ERROR:, HINT:) on stderr."""
    try:
        click.echo(f"ERROR: {error}\nHINT: {hint}", err=True, color=False)
    except (OSError, ValueError):
        pass


def _print_cancelled() -> None:
    """The line for Ctrl-C/Ctrl-D (exit 2): ``cancelled`` in the catalogue."""
    from deepctl_core import messages as msg
    from deepctl_core.output import is_agentic

    if is_agentic():
        _agent_lines(msg.E_CANCELLED, msg.A_CANCELLED)
        return
    _safe_console_print(f"\n[yellow]{msg.E_CANCELLED}[/yellow]")


def _print_internal_error(exc: BaseException) -> None:
    """The catch-all for an exception nothing else handled (exit 1)."""
    from deepctl_core import messages as msg
    from deepctl_core.output import is_agentic

    message = msg.E_INTERNAL.format(exception_message=str(exc) or type(exc).__name__)
    if is_agentic():
        _agent_lines(message, msg.A_INTERNAL)
        return
    from rich.markup import escape

    # The exception text can hold anything: text, not markup.
    _safe_console_print(f"[red]Error: {escape(message)}[/red]")
    _safe_console_print(f"  [dim]{escape(msg.H_INTERNAL)}[/dim]")


def _print_usage(ctx: click.Context | None) -> None:
    """Click's usage line and "Try ... --help" before a usage error, so the
    program name a user typed (or ``DEEPCTL_PROG_NAME``) shows up.

    People only: in agent mode ``run()`` prints its two prefixed lines and
    exits before this is called.
    """
    if ctx is None:
        return
    try:
        lines = ctx.get_usage()
        if ctx.command.get_help_option(ctx) is not None:
            lines += (
                f"\nTry '{ctx.command_path} {ctx.help_option_names[0]}' for help.\n"
            )
        click.echo(lines, err=True, color=ctx.color)
    except (OSError, ValueError):
        pass


def _best_effort(notice: Callable[..., object], **kwargs: Any) -> None:
    """Run a startup check or notice; an unreadable cache skips it."""
    try:
        notice(**kwargs)
    except OSError:
        pass


def _help_cache_requested() -> bool:
    """``DEEPCTL_HELP_CACHE=1`` from the launcher (``deepctl.main`` removes
    it from the environment at startup), and not an AI agent session."""
    main_module = sys.modules.get("deepctl.main")
    requested = bool(getattr(main_module, "HELP_CACHE", False))
    if not requested:
        return False
    from deepctl_core.output import is_ai_agent

    return not is_ai_agent()


def _save_root_help(before: dict[str, Any] | None) -> None:
    """Write the help cache entry for the root help this run printed."""
    if cli.rendered_help is None:
        return
    import codecs

    try:
        encoding = codecs.lookup(getattr(sys.stdout, "encoding", "") or "").name
    except LookupError:
        return
    if encoding != "utf-8":
        return
    from deepctl import _cache

    text, prog = cli.rendered_help
    try:
        tty = sys.stdout.isatty()
    except (OSError, ValueError):
        return
    # As click.echo wrote it: styles kept only on a terminal.
    shown = text if tty else click.unstyle(text)
    _cache.write_help(shown + "\n", prog, _cache.help_width(), tty, before)


def _start_checks(quiet: bool) -> list[Callable[[], object]]:
    """Start the background update, plugin-update and AI-CLI checks; return
    the functions that print what they found."""
    pending: list[Callable[[], object]] = []
    try:
        from deepctl_cmd_update.startup_check import (
            check_and_notify,
            print_pending_notification,
        )

        _best_effort(check_and_notify, quiet=quiet)
        pending.append(print_pending_notification)
    except ImportError:
        pass

    try:
        from deepctl_cmd_update.plugin_update_check import (
            check_plugins_and_notify,
            print_pending_plugin_notifications,
        )

        _best_effort(check_plugins_and_notify, quiet=quiet)
        pending.append(print_pending_plugin_notifications)
    except ImportError:
        pass

    try:
        from deepctl_cmd_skills.startup_check import (
            check_and_notify as skills_check_and_notify,
        )
        from deepctl_cmd_skills.startup_check import (
            print_pending_notification as print_pending_skills_notification,
        )

        _best_effort(skills_check_and_notify, quiet=quiet)
        pending.append(print_pending_skills_notification)
    except ImportError:
        pass
    return pending


def _agent_mode_notice(quiet: bool) -> None:
    """Tell a person at a terminal, once per run, that an AI-agent marker in
    their environment put dg in agent mode (``deepctl_core.agents``)."""
    if quiet:
        return
    from deepctl_core import agents, output

    try:
        line = agents.mode_notice("{prog}")
        if line is None:
            return
        from rich.markup import escape

        output.print_info(
            escape(line.replace("{prog}", output.invoked_prog())), stderr=True
        )
    except (OSError, ValueError):
        pass


def _record_startup(started_at: float) -> None:
    """Add process startup (from ``main()``'s first line) to ``--timing``."""
    from deepctl_core.timing import TimingEntry, get_timing_collector

    entry = TimingEntry(name="startup_imports", start_time=started_at)
    entry.finish()
    get_timing_collector().entries[entry.name] = entry


class _HelpConfig:
    """What the telemetry notice reads for root ``--help``: the config file
    path, with the text left for it to read. Root ``--help`` runs before the
    root callback builds a ``Config``, and loading one there would cost the
    pydantic import that ``dg --help`` otherwise never pays."""

    def __init__(self, config_path: str | None) -> None:
        from pathlib import Path

        self.config_path = Path(config_path) if config_path else None
        self.user_config_text = None


def _is_help_run(args: list[str]) -> bool:
    """Whether this run only prints help: ``--help`` among the options
    (before any ``--``), or the ``help`` command (``dg help <topic>``)."""
    words = args[: args.index("--")] if "--" in args else args
    if "--help" in words:
        return True
    flag_opts = _root_flag_opts()
    i = 0
    while i < len(args) and args[i].startswith("-") and args[i] != "--":
        i += 2 if _takes_next_token(args[i], flag_opts) else 1
    return i < len(args) and args[i] == "help"


def prog_name(default: str | None = None) -> str | None:
    """The program name for usage lines: ``DEEPCTL_PROG_NAME`` when it is
    a plain program name (``deepctl.main.prog_name_from_env``), else
    ``default`` (``None`` lets Click use ``argv[0]``)."""
    from deepctl.main import prog_name_from_env

    return prog_name_from_env() or default


def run(
    args: list[str],
    started_at: float | None = None,
    prog: str | None = None,
) -> None:
    """Run the CLI for ``args`` (argv without the program name).

    ``started_at`` is ``time.perf_counter()`` from the first line of
    ``deepctl.main.main()``, so ``--timing`` includes the imports. ``prog``
    is the program name to show when ``DEEPCTL_PROG_NAME`` isn't set
    (``python -m deepctl`` passes ``dg``).
    """
    global _run_args
    if started_at is None:
        started_at = time.perf_counter()
    _run_args = list(args)
    try:
        # Check if timing was requested and enable it early
        timing_requested = "--timing" in args or "--timing-detailed" in args
        detailed_timing = "--timing-detailed" in args
        quiet_requested = "--quiet" in args or "-q" in args

        if timing_requested:
            enable_timing()
            _record_startup(started_at)

        if eager_load_requested():
            load_commands()

        # Install the --help telemetry footer. Telemetry itself is decided in
        # the root callback, after option parsing. `--no-telemetry` is also
        # applied here so it holds even after an eager `--help` before it.
        try:
            from deepctl_telemetry import disable_for_run, install_help_notice

            if "--no-telemetry" in _root_option_tokens(args):
                disable_for_run()
            install_help_notice(
                lambda: cast("Config", _HelpConfig(_argv_config_path(args)))
            )
        except Exception:
            pass

        # Background update and AI-CLI checks (non-blocking). Not for help,
        # which makes no network connection and prints only the help.
        pending = [] if _is_help_run(args) else _start_checks(quiet_requested)
        if not _is_help_run(args):
            _agent_mode_notice(quiet_requested)

        # Root `dg --help` is saved for the native launcher when it asks
        # (DEEPCTL_HELP_CACHE=1), outside an AI agent, unless the run writes
        # anything to stderr.
        stderr_watch = None
        key_before: dict[str, Any] | None = None
        if args == ["--help"] and not cli.eager and _help_cache_requested():
            from deepctl._cache import StderrWatch, key_inputs

            # The key's inputs before rendering: if any changes while the
            # help is rendered, no entry is written.
            key_before = key_inputs()
            stderr_watch = StderrWatch(sys.stderr)
            sys.stderr = stderr_watch

        with TimingContext("total_execution"):
            exit_code = 0
            with TimingContext("cli_execution"):
                try:
                    cli(
                        args=list(args),
                        prog_name=prog_name(prog),
                        standalone_mode=False,
                    )
                except SystemExit as exc:
                    # Click calls sys.exit() even in non-standalone mode, so we
                    # cannot let this propagate — the notifications and timing
                    # summary below still need to run. But swallowing the code
                    # outright made every failure exit 0, which is what broke
                    # `if dg -o json keys; then ...` in scripts and CI. Carry it
                    # and re-raise at the end instead.
                    exit_code = exc.code if isinstance(exc.code, int) else 0

        if stderr_watch is not None:
            if sys.stderr is stderr_watch:
                sys.stderr = stderr_watch.stream
            if not exit_code and not stderr_watch.written:
                _save_root_help(key_before)

        # Print update notifications if available (before timing summary)
        for print_pending in pending:
            _best_effort(print_pending)

        # Print timing summary if timing was enabled
        if timing_requested:
            print_timing_summary(detailed_timing)

        if exit_code:
            sys.exit(exit_code)

    except (KeyboardInterrupt, click.exceptions.Abort):
        # Click wraps a Ctrl-C/Ctrl-D raised inside cli() in Abort when
        # standalone_mode=False, so a mid-command interrupt arrives here as
        # Abort, not KeyboardInterrupt. Both are user cancellation: exit 2.
        _print_cancelled()
        sys.exit(2)
    except click.UsageError as e:
        # Keep the published exit-code contract (1 = error), but explain the
        # one failure mode that reads as a lie: a *global* option placed
        # after the subcommand, where Click says "No such option" about an
        # option that very much exists.
        message = e.format_message()
        from deepctl_core.output import is_agentic

        if is_agentic():
            # Agent mode: exactly two prefixed lines (ERROR:, HINT:) and no
            # Usage/Try lines. A global option after the command gets its
            # placement hint in place of the generic one.
            hint = _global_option_hint(message, e.ctx, markup=False)
            lines = [f"ERROR: {message}", f"HINT: {hint or _usage_hint(e.ctx)}"]
            try:
                click.echo("\n".join(lines), err=True, color=False)
            except (OSError, ValueError):
                pass
            sys.exit(1)
        _print_usage(e.ctx)
        from rich.markup import escape

        # The message quotes what was typed: text, not markup.
        _safe_console_print(f"[red]Error: {escape(message)}[/red]")
        hint = _global_option_hint(message, e.ctx)
        if hint:
            _safe_console_print(hint)
        sys.exit(1)
    except DependencyMissingError as e:
        # The install is incomplete; nothing unexpected to report.
        try:
            e.show()
        except (OSError, ValueError):
            pass
        sys.exit(e.exit_code)
    except Exception as e:
        _print_internal_error(e)
        _report_error(e)
        sys.exit(1)  # 1 = error; 2 is reserved for user interrupt
