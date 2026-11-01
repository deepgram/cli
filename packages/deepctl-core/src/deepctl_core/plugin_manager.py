"""Plugin manager for deepctl command discovery and loading."""

import json
import os
import re
import sys
from collections.abc import Collection, Mapping
from importlib import metadata
from pathlib import Path
from typing import Any, cast

import click

from . import messages as _msg
from . import output as _output
from ._plugin_trust import (
    agent_plugin_venv,
    allow_override_names,
    contained_in,
    is_ai_agent,
    trusted_user_config_file,
)
from .base_command import BaseCommand
from .base_group_command import BaseGroupCommand
from .models import ErrorResult, PluginInfo
from .output import print_warning, stderr_console
from .plugin_env import (
    PLUGIN_VENV,
    get_plugin_state,
    get_venv_python_version,
    get_venv_site_packages,
    plugin_dir_problem,
    read_plugin_state_file,
    skipped_plugin_venvs,
)
from .safe_text import printable as _printable
from .timing import TimingContext

# Load errors are diagnostics: they go to stderr so a broken plugin can't
# corrupt `dg ... -o json` payloads on stdout.
console = stderr_console

# Built-in command names and the exact module each one may be loaded from.
# A command is built-in only when its entry point's module is the one
# listed here: never by a ``deepctl_cmd_`` prefix or a distribution name, since
# any wheel can ship such a package or a dist-info named like ours. Every
# listed name is reserved: a command with that name from any other module is
# skipped, even when the built-in itself is missing.
BUILTIN_COMMAND_MODULES: Mapping[str, str] = {
    "api": "deepctl_cmd_api.command",
    "billing": "deepctl_cmd_billing.command",
    "completion": "deepctl_cmd_completion.command",
    "debug": "deepctl_cmd_debug.command",
    "ffprobe": "deepctl_cmd_ffprobe.command",
    "init": "deepctl_cmd_init.command",
    "keys": "deepctl_cmd_keys.command",
    "listen": "deepctl_cmd_listen.command",
    "login": "deepctl_cmd_login.command",
    "logout": "deepctl_cmd_login.command",
    "mcp": "deepctl_cmd_mcp.command",
    "members": "deepctl_cmd_members.command",
    "models": "deepctl_cmd_models.command",
    "plugin": "deepctl_cmd_plugin.command",
    "profiles": "deepctl_cmd_login.command",
    "projects": "deepctl_cmd_projects.command",
    "read": "deepctl_cmd_read.command",
    "requests": "deepctl_cmd_requests.command",
    "skills": "deepctl_cmd_skills.command",
    "speak": "deepctl_cmd_speak.command",
    "transcribe": "deepctl_cmd_transcribe.command",
    "update": "deepctl_cmd_update.command",
    "usage": "deepctl_cmd_usage.command",
    "whoami": "deepctl_cmd_login.command",
}

# Built-in subcommands per built-in group, with the same rule.
BUILTIN_SUBCOMMAND_MODULES: Mapping[str, Mapping[str, str]] = {
    "debug": {
        "audio": "deepctl_cmd_debug_audio.command",
        "browser": "deepctl_cmd_debug_browser.command",
        "network": "deepctl_cmd_debug_network.command",
        "probe": "deepctl_cmd_debug_probe.command",
        "toolkit": "deepctl_cmd_debug_toolkit.command",
    },
}


# Messages.
W_PLUGIN_SHADOW = (
    "Ignored the plugin command '{command}' from {label}: plugins can't "
    "replace built-in commands."
)
W_PLUGIN_SHADOW_HINT = (
    "If you trust this plugin, add '{command}' to plugins.allow_override in "
    "{user_config} to use its version."
)
A_PLUGIN_SHADOW = (
    "dg runs its built-in '{command}' instead. If you are an AI agent, tell the "
    "user about this plugin and don't change dg's settings to allow it."
)
I_PLUGIN_OVERRIDE = (
    "Using '{command}' from {label} instead of the built-in, as "
    "plugins.allow_override in {user_config} allows."
)
I_PLUGIN_OVERRIDE_AGENT = (
    "Using '{command}' from {label} instead of the built-in, as the deepctl "
    "user config allows."
)
W_PLUGIN_VENV_SKIPPED = (
    "Ignored the plugins in a plugin environment: when an AI agent runs dg, "
    "plugins load only from your account's home directory, not through a "
    "symlink or from inside the working directory."
)
A_PLUGIN_VENV_SKIPPED = (
    "If you are an AI agent, tell the user about this and don't move, copy or "
    "link plugin files to change it."
)
# {why} is a messages.W_CFG_UNTRUSTED_WHY value (owner or writable).
W_PLUGIN_VENV_UNTRUSTED = (
    "Ignored the plugins in your plugin environment: in an AI agent session, "
    "dg can't load plugins from it because the directory {path} {why}."
)
A_PLUGIN_VENV_UNTRUSTED = (
    "A person must fix this directory. If you are an AI agent, ask the user."
)
W_PLUGIN_DIR_OPEN = (
    "The plugin directory {path} {why}, so someone else could change the "
    "plugins dg runs."
)
H_PLUGIN_DIR_OPEN = "dg makes it private the next time you install a plugin."
W_PLUGIN_BADNAME = (
    "Ignored the plugin command {command} from {label}: plugin command names "
    "must start with a lowercase letter and use only lowercase a-z, 0-9 and "
    "hyphens, up to 64 characters."
)
A_PLUGIN_BADNAME = (
    "If you are an AI agent, tell the user about this plugin and don't edit "
    "its files to change the name."
)

# The only command and subcommand names a plugin may register: lowercase
# ASCII letters, digits and hyphens, starting with a letter. Any other name is
# refused, so no plugin command can look like a built-in in ``--help``.
_VALID_PLUGIN_NAME = re.compile(r"[a-z][a-z0-9-]{0,63}")


def _agent_mode() -> bool:
    """Output is rendered for agents (``WARN:``/``HINT:`` prefixes)."""
    return bool(_output._output_config.get("agentic"))


def _agent_hints() -> bool:
    """Choose the hint written for agents: agentic output or a hard marker."""
    return _output.is_agentic() or is_ai_agent()


def _security_warning(message: str) -> None:
    """A warning that ``--quiet`` doesn't silence."""
    if _agent_mode():
        stderr_console.print(f"WARN: {message}", soft_wrap=True, emoji=False)
    else:
        stderr_console.print(
            f"[yellow]⚠[/yellow] {message}", soft_wrap=True, emoji=False
        )


def _hint(message: str) -> None:
    """The hint line that follows a warning, on its own line."""
    if _agent_mode():
        stderr_console.print(f"HINT: {message}", soft_wrap=True, emoji=False)
    else:
        stderr_console.print(f"  [dim]{message}[/dim]", soft_wrap=True, emoji=False)


def _notice(message: str) -> None:
    """An info line on stderr; ``--quiet`` silences it."""
    if _output._output_config.get("quiet"):
        return
    if _agent_mode():
        stderr_console.print(f"INFO: {message}", soft_wrap=True, emoji=False)
    else:
        stderr_console.print(f"[blue]ℹ[/blue] {message}", soft_wrap=True, emoji=False)


def _load_error(what: str, name: Any, error: BaseException) -> None:
    """A plugin load error, with the plugin's text shown literally."""
    label = f"{what} {_printable(str(name))}" if name else what
    console.print(
        f"[red]Error loading {label}:[/red] {_printable(str(error))}",
        soft_wrap=True,
        emoji=False,
    )


def _display_path(path: Path) -> str:
    """``path`` with the home directory shown as ``~``."""
    text = str(path)
    home = os.path.expanduser("~")
    if home not in ("", "~") and text.startswith(home.rstrip(os.sep) + os.sep):
        return "~" + text[len(home.rstrip(os.sep)) :]
    return text


def _command_name(obj: Any) -> str:
    """A command's ``name``, which must be a plain ``str``."""
    name = getattr(obj, "name", None)
    if type(name) is not str or not name:
        raise TypeError("command name must be a non-empty str")
    return name


def _entry_point_module(entry_point: Any) -> str:
    """The module an entry point imports (``module:attr`` -> ``module``)."""
    value = getattr(entry_point, "value", None)
    if not isinstance(value, str):
        return ""
    return value.partition(":")[0].strip()


def _distribution_label(entry_point: Any) -> str:
    """``name version (module)`` for the distribution behind an entry point.

    Any dist-info can claim any name, so the module path
    is always shown next to it.
    """
    module = _entry_point_module(entry_point) or "unknown module"
    dist = getattr(entry_point, "dist", None)
    try:
        name = dist.metadata["Name"] if dist is not None else None
        version = dist.version if dist is not None else None
    except Exception:
        name = version = None
    if not isinstance(name, str) or not name:
        return _printable(module)
    label = f"{name} {version}" if isinstance(version, str) and version else name
    return f"{_printable(label)} ({_printable(module)})"


class PluginManager:
    """Manager for loading and organizing CLI plugins/commands."""

    def __init__(self) -> None:
        """Initialize plugin manager."""
        self.loaded_plugins: dict[str, Any] = {}
        self.command_classes: dict[str, type[Any]] = {}
        self._loaded_entry_point_values: set[str] = set()
        self._allow_override: frozenset[str] | None = None
        self._announced: set[str] = set()

    def _overrides(self) -> frozenset[str]:
        """``plugins.allow_override`` from the trusted user config, read once."""
        if self._allow_override is None:
            self._allow_override = allow_override_names()
        return self._allow_override

    def _admit(
        self,
        name: str,
        entry_point: Any,
        builtin_modules: Mapping[str, str],
        reserved: Collection[str],
        path_prefix: str = "",
    ) -> str | None:
        """Decide how a loaded command may be registered.

        Returns ``"builtin"`` when the name maps to this entry point's exact
        module, ``"plugin"`` for any other free, valid name, ``"override"``
        for a reserved name the user listed in ``plugins.allow_override``, and
        ``None`` when the command must be skipped: its name isn't a valid
        plugin command name, or it would replace a built-in.
        """
        expected = builtin_modules.get(name)
        if expected is not None and _entry_point_module(entry_point) == expected:
            return "builtin"
        taken = set(builtin_modules) | set(reserved)
        command_path = f"{path_prefix}{name}"
        if _VALID_PLUGIN_NAME.fullmatch(name) is None:
            self._warn_bad_name(ascii(f"{path_prefix}{name}"), entry_point)
            return None
        if name not in taken:
            return "plugin"
        if command_path in self._overrides():
            return "override"
        self._warn_shadow(command_path, entry_point)
        return None

    def _refused_by_entry_point_name(
        self,
        entry_point: Any,
        builtin_modules: Mapping[str, str],
        reserved: Collection[str],
        path_prefix: str = "",
    ) -> bool:
        """Whether an entry point is refused by its own name, before loading.

        An entry point named like a built-in or reserved command must not be
        imported unless ``_admit`` would register it under that name. Only a
        taken name is checked here: an entry point's name isn't always its
        command's name (``my_tool`` may register ``my-tool``), so the full
        check still runs on the loaded command.
        """
        name = getattr(entry_point, "name", None)
        if type(name) is not str or _VALID_PLUGIN_NAME.fullmatch(name) is None:
            return False
        if name not in builtin_modules and name not in reserved:
            return False
        kind = self._admit(name, entry_point, builtin_modules, reserved, path_prefix)
        return kind is None

    def _warn_bad_name(self, shown_name: str, entry_point: Any) -> None:
        """Print W-PLUGIN-BADNAME once per name. ``shown_name`` is already
        ``ascii()``-escaped."""
        key = f"badname:{shown_name}"
        if key in self._announced:
            return
        self._announced.add(key)
        _security_warning(
            W_PLUGIN_BADNAME.format(
                command=_printable(shown_name),
                label=_distribution_label(entry_point),
            )
        )
        if _agent_hints():
            _hint(A_PLUGIN_BADNAME)

    @staticmethod
    def _announce_on_invoke(
        command: click.Command, command_path: str, entry_point: Any
    ) -> None:
        """Print I-PLUGIN-OVERRIDE when ``command`` runs.

        At load time the output options (``--quiet``) aren't parsed yet, so
        the notice is printed from the command's own invocation instead, and
        only for the command that runs.
        """
        label = _distribution_label(entry_point)
        invoke = command.invoke

        def invoke_with_notice(ctx: click.Context) -> Any:
            config_file = trusted_user_config_file()
            template = (
                I_PLUGIN_OVERRIDE_AGENT
                if _agent_hints() or config_file is None
                else I_PLUGIN_OVERRIDE
            )
            _notice(
                template.format(
                    command=_printable(command_path),
                    label=label,
                    user_config=_printable(_display_path(config_file))
                    if config_file is not None
                    else "",
                )
            )
            return invoke(ctx)

        command.invoke = invoke_with_notice  # type: ignore[method-assign]

    def _warn_shadow(self, command_path: str, entry_point: Any) -> None:
        """Print W-PLUGIN-SHADOW and its hint, once per command path.

        The agent hint (A-PLUGIN-SHADOW) is used for agentic output or a hard
        AI-agent signal and names no setting or file; the other hint is for
        people.
        """
        key = f"skip:{command_path}"
        if key in self._announced:
            return
        self._announced.add(key)
        command = _printable(command_path)
        _security_warning(
            W_PLUGIN_SHADOW.format(
                command=command, label=_distribution_label(entry_point)
            )
        )
        if _agent_hints():
            _hint(A_PLUGIN_SHADOW.format(command=command))
            return
        config_file = trusted_user_config_file()
        if config_file is not None:
            _hint(
                W_PLUGIN_SHADOW_HINT.format(
                    command=command,
                    user_config=_printable(_display_path(config_file)),
                )
            )

    def load_plugins(self, cli_group: click.Group) -> None:
        """Load all plugins into the CLI group.

        Args:
            cli_group: Main CLI group to add commands to
        """
        # Load built-in commands
        with TimingContext("builtin_commands_loading"):
            self._load_builtin_commands(cli_group)
            self._restore_hidden_builtins(cli_group)

        # Load external plugins from the current environment
        with TimingContext("external_plugins_loading"):
            self._load_external_plugins(cli_group)

        # Bridge plugins installed in the isolated plugin venv
        with TimingContext("plugin_venv_loading"):
            self._load_plugin_venv_entries(cli_group)

    def _load_builtin_commands(self, cli_group: click.Group) -> None:
        """Load built-in commands from the commands entry point group."""
        try:
            # Load built-in commands from entry points
            with TimingContext("discover_entry_points"):
                entry_points = metadata.entry_points()
                command_entry_points = list(
                    entry_points.select(group="deepctl.commands")
                )

            reserved = self._existing_names(cli_group)
            for entry_point in command_entry_points:
                with TimingContext(f"load_command_{entry_point.name}"):
                    try:
                        self._register(cli_group, entry_point, reserved)
                    except Exception as e:
                        _load_error("command", entry_point.name, e)

        except Exception as e:
            _load_error("built-in commands", "", e)

    @staticmethod
    def _hidden_builtin_entry_points(
        group_name: str, wanted: Mapping[str, str]
    ) -> list[Any]:
        """Entry points for the ``wanted`` built-ins from every distribution.

        ``metadata.entry_points()`` keeps one distribution per name, so a
        stray dist-info named like a first-party package can hide the real
        one's entry points. This scan sees every dist-info and keeps only the
        entry points whose module is the built-in's exact module.
        """
        found: list[Any] = []
        for dist in metadata.distributions():
            try:
                entry_points = list(dist.entry_points)
            except Exception:
                continue  # one malformed dist-info doesn't stop the scan
            for ep in entry_points:
                try:
                    if (
                        ep.group == group_name
                        and ep.name in wanted
                        and _entry_point_module(ep) == wanted[ep.name]
                    ):
                        found.append(ep)
                except Exception:
                    continue
        return found

    def _restore_hidden_builtins(self, cli_group: click.Group) -> None:
        """Load any built-in command the entry point scan didn't find."""
        present = self._existing_names(cli_group)
        missing = {
            name: module
            for name, module in BUILTIN_COMMAND_MODULES.items()
            if name not in present
        }
        if not missing:
            return
        try:
            for ep in self._hidden_builtin_entry_points("deepctl.commands", missing):
                if ep.name in self._existing_names(cli_group):
                    continue
                try:
                    self._register(cli_group, ep, frozenset())
                except Exception as e:
                    _load_error("command", ep.name, e)
        except Exception as e:
            _load_error("built-in commands", "", e)

    @staticmethod
    def _existing_names(group: click.Group) -> frozenset[str]:
        """Names a group already holds before entry points are loaded."""
        commands = getattr(group, "commands", None)
        return frozenset(commands) if isinstance(commands, dict) else frozenset()

    def _register(
        self, cli_group: click.Group, entry_point: Any, reserved: Collection[str]
    ) -> None:
        """Load one top-level entry point and add it unless it would replace
        a built-in command (``BUILTIN_COMMAND_MODULES``)."""
        if self._refused_by_entry_point_name(
            entry_point, BUILTIN_COMMAND_MODULES, reserved
        ):
            return
        command_class = entry_point.load()
        instance = command_class()
        name = _command_name(instance)
        kind = self._admit(name, entry_point, BUILTIN_COMMAND_MODULES, reserved)
        if kind is None:
            return
        click_command = self._create_click_command(instance)
        # Click registers by the command's own name, which a plugin's group
        # can set to anything: check that name too, and register under it.
        checked = _command_name(click_command)
        if checked != name:
            kind = self._admit(checked, entry_point, BUILTIN_COMMAND_MODULES, reserved)
            if kind is None:
                return
        if kind == "override":
            self._announce_on_invoke(click_command, checked, entry_point)
        cli_group.add_command(click_command, name=checked)
        if kind == "builtin":
            self.command_classes[entry_point.name] = command_class
        else:
            self.loaded_plugins[entry_point.name] = instance
        self._loaded_entry_point_values.add(entry_point.value)

    def _load_external_plugins(self, cli_group: click.Group) -> None:
        """Load external plugins from the plugins entry point group."""
        try:
            # Load plugins from entry points
            entry_points = metadata.entry_points()
            reserved = self._existing_names(cli_group)
            for entry_point in entry_points.select(group="deepctl.plugins"):
                try:
                    self._register(cli_group, entry_point, reserved)
                except Exception as e:
                    _load_error("plugin", entry_point.name, e)

        except Exception as e:
            _load_error("external plugins", "", e)

    def _load_plugin_venv_entries(self, cli_group: click.Group) -> None:
        """Load plugins from the isolated plugin venv (``~/.deepctl/plugins/venv``).

        This bridges plugins that were installed via ``deepctl plugin install``
        into the CLI.  It only activates when:
          1. The plugin venv directory exists.
          2. ``plugins.json`` lists at least one plugin.
          3. A valid ``site-packages`` path can be resolved.

        The venv's ``site-packages`` is **appended** (not prepended) to
        ``sys.path`` so that core packages always resolve from the main
        environment first.

        Under an AI-agent signal the venv is the one under the account's real
        home directory, never one found through ``$HOME``, and it is skipped
        when it resolves through a symlink or inside the working directory
        (see ``agent_plugin_venv``).
        """
        venv = PLUGIN_VENV
        state_file = None
        if is_ai_agent():
            agent_venv = agent_plugin_venv()
            problem = plugin_dir_problem(agent_venv) if agent_venv else None
            if agent_venv is not None and problem is not None:
                # Fail closed: nothing from this environment loads.
                state = read_plugin_state_file(agent_venv.parent / "plugins.json")
                if state["plugins"]:
                    self._warn_venv_untrusted(*problem)
                return
            if skipped_plugin_venvs():
                self._warn_venv_skipped()
            if agent_venv is None:
                return
            venv, state_file = agent_venv, agent_venv.parent / "plugins.json"

        # Fast bail — nothing to do if the venv doesn't exist
        if not venv.exists():
            return

        # Check if there are any plugins tracked in state
        state = get_plugin_state(state_file)
        if not state.get("plugins"):
            return

        if state_file is None:
            problem = plugin_dir_problem(venv)
            if problem is not None:
                self._warn_dir_open(*problem)

        site_packages = get_venv_site_packages(venv)
        if site_packages is None:
            return
        if state_file is not None and not contained_in(site_packages, venv):
            self._warn_venv_skipped()
            return

        self._warn_if_plugin_venv_python_mismatch(venv)

        site_packages_str = str(site_packages)

        # Append to sys.path so modules inside the plugin venv can be imported.
        # Using append (not insert) guarantees core deps resolve from main env.
        if site_packages_str not in sys.path:
            sys.path.append(site_packages_str)

        reserved = self._existing_names(cli_group)
        try:
            # Scan only the plugin venv for distributions
            for dist in metadata.distributions(path=[site_packages_str]):
                ep_groups_to_check = ["deepctl.plugins"]

                # Also check for subplugin groups
                for ep in dist.entry_points:
                    if (
                        ep.group
                        and ep.group.startswith("deepctl.subplugins.")
                        and ep.group not in ep_groups_to_check
                    ):
                        ep_groups_to_check.append(ep.group)

                for group_name in ep_groups_to_check:
                    for ep in dist.entry_points:
                        if ep.group != group_name:
                            continue

                        # Dedup: skip if already loaded from main environment
                        if ep.value in self._loaded_entry_point_values:
                            continue

                        try:
                            # Venv entries are added at the top level (both
                            # groups), so they get the top-level name check.
                            self._register(cli_group, ep, reserved)
                        except Exception as e:
                            _load_error("plugin venv entry", ep.name, e)

        except Exception as e:
            _load_error("plugins from plugin venv", "", e)

    def _warn_venv_skipped(self) -> None:
        """W-PLUGIN-VENV-SKIPPED and its hint, once. Names no setting or path."""
        if "venv-skipped" not in self._announced:
            self._announced.add("venv-skipped")
            _security_warning(W_PLUGIN_VENV_SKIPPED)
            _hint(A_PLUGIN_VENV_SKIPPED)

    def _warn_venv_untrusted(self, directory: Path, why: str) -> None:
        """An AI agent run refuses a plugin environment others can change."""
        if "venv-untrusted" not in self._announced:
            self._announced.add("venv-untrusted")
            _security_warning(
                W_PLUGIN_VENV_UNTRUSTED.format(
                    path=_printable(_display_path(directory)),
                    why=_msg.W_CFG_UNTRUSTED_WHY[why],
                )
            )
            _hint(A_PLUGIN_VENV_UNTRUSTED)

    def _warn_dir_open(self, directory: Path, why: str) -> None:
        """A person's plugin environment others can change: it still loads."""
        if "dir-open" not in self._announced:
            self._announced.add("dir-open")
            _security_warning(
                W_PLUGIN_DIR_OPEN.format(
                    path=_printable(_display_path(directory)),
                    why=_msg.W_CFG_UNTRUSTED_WHY[why],
                )
            )
            if why == "writable":
                _hint(H_PLUGIN_DIR_OPEN)

    def _warn_if_plugin_venv_python_mismatch(self, venv: Path | None = None) -> None:
        """Surface a one-line warning when the plugin venv's Python differs from ours.

        Triggered when the underlying interpreter is bumped (e.g. ``brew upgrade
        python@3.13`` rebuilding deepctl against a newer Python) without the user
        recreating ``~/.deepctl/plugins/venv/``. Pure-Python plugins keep loading
        via :data:`sys.path` bridging; only C-extension plugins fail to load,
        and they fail with a confusing low-level ImportError. This warning gives
        users a clear remediation before they hit that error.

        Silent when the venv version can't be determined (no ``pyvenv.cfg``,
        unparseable cfg, etc.) — there's no useful guidance to give.
        """
        venv = PLUGIN_VENV if venv is None else venv
        venv_version = get_venv_python_version(venv)
        if venv_version is None:
            return

        running = (sys.version_info.major, sys.version_info.minor)
        if venv_version == running:
            return

        venv_str = f"{venv_version[0]}.{venv_version[1]}"
        running_str = f"{running[0]}.{running[1]}"
        print_warning(
            f"Plugin environment was built with Python {venv_str} but you're "
            f"running Python {running_str}. C-extension plugins may fail to "
            f"load. To rebuild:\n"
            f"  rm -rf {venv} && dg plugin install <your-plugin>"
        )

    def _create_click_command(self, command_instance: Any) -> click.Command:
        """Create a Click command from a BaseCommand instance.

        Args:
            command_instance: Instance of BaseCommand

        Returns:
            Click command object
        """
        # Check if this is a group command
        if isinstance(command_instance, BaseGroupCommand) or getattr(
            command_instance, "is_group", False
        ):
            # Create a Click Group for group commands
            return self._create_click_group(command_instance)

        # Create the command function
        def command_func(**kwargs: Any) -> Any:
            # Pass CLI context and arguments to the command
            ctx = click.get_current_context()
            return command_instance.execute(ctx, **kwargs)

        # Set function name and docstring
        command_func.__name__ = command_instance.name.replace("-", "_")
        command_func.__doc__ = command_instance.help

        # Build help text with examples (+ AI hint when in agentic context)
        help_text = self._build_help_text(command_instance)

        # Create base command
        cmd = click.Command(
            name=command_instance.name,
            callback=command_func,
            help=help_text,
            short_help=command_instance.short_help or command_instance.help,
            hidden=command_instance.hidden,
        )

        # Add arguments and options
        cmd = self._add_command_arguments(cmd, command_instance)

        # Add --agent-friendly as an eager option so it fires before argument
        # validation (like --help does), allowing AI agents to query metadata
        # without supplying required positional arguments.
        manager_ref = self

        def _agent_friendly_callback(
            ctx: click.Context, _param: click.Parameter, value: bool
        ) -> None:
            if value:
                manager_ref._output_command_ai_metadata(command_instance)
                ctx.exit()

        cmd = click.option(
            "--agent-friendly",
            is_flag=True,
            is_eager=True,
            expose_value=False,
            help="Output machine-readable JSON metadata for this command and exit.",
            callback=_agent_friendly_callback,
        )(cmd)

        cmd = click.option(
            "--non-interactive",
            is_flag=True,
            expose_value=False,
            help="Skip interactive prompts; use defaults for any optional features.",
        )(cmd)

        # Accept the global output options after the subcommand as well.
        cmd = self._add_global_passthrough_options(cmd)

        return cmd

    def _add_global_passthrough_options(self, cmd: click.Command) -> click.Command:
        """Let the global output options work after the subcommand too.

        The root group defines -o/--output, -q/--quiet and -v/--verbose, but
        Click only parses group options that appear *before* the subcommand
        name — so ``dg models -o json`` used to fail with "No such option
        '-o'" while ``dg -o json models`` worked, and the error gave no hint.
        These pass-through copies apply the same effect from the subcommand
        position. A copy is only added when the command does not already
        define the option name itself (for example, ``dg speak --output``
        names an audio file and keeps its own meaning).
        """
        from .output import update_output

        taken: set[str] = set()
        for param in cmd.params:
            taken.update(param.opts)
            taken.update(param.secondary_opts)

        def _apply_format(
            _ctx: click.Context, _param: click.Parameter, value: str | None
        ) -> None:
            if value is not None:
                update_output(format_type=value)

        def _apply_quiet(
            _ctx: click.Context, _param: click.Parameter, value: bool
        ) -> None:
            if value:
                update_output(quiet=True)

        def _apply_verbose(
            _ctx: click.Context, _param: click.Parameter, value: bool
        ) -> None:
            if value:
                update_output(verbose=True)

        if not {"--output", "-o"} & taken:
            cmd = click.option(
                "--output",
                "-o",
                type=click.Choice(
                    ["json", "yaml", "table", "csv"], case_sensitive=False
                ),
                expose_value=False,
                callback=_apply_format,
                help="Output format (same as the global -o before the command).",
            )(cmd)

        if not {"--quiet", "-q"} & taken:
            cmd = click.option(
                "--quiet",
                "-q",
                is_flag=True,
                expose_value=False,
                callback=_apply_quiet,
                help="Suppress non-essential output.",
            )(cmd)

        if not {"--verbose", "-v"} & taken:
            cmd = click.option(
                "--verbose",
                "-v",
                is_flag=True,
                expose_value=False,
                callback=_apply_verbose,
                help="Enable verbose output.",
            )(cmd)

        return cmd

    def _build_help_text(self, instance: Any) -> str:
        """Build help text with examples appended.

        Args:
            instance: BaseCommand instance

        Returns:
            Help text string, optionally with examples section
        """
        help_text: str = instance.help
        examples = getattr(instance, "examples", [])
        if examples:
            help_text += "\n\nExamples:\n"
            for ex in examples:
                help_text += f"  {ex}\n"
        help_text += "\n\nFor agents, use --agent-friendly."
        return help_text

    def _output_command_ai_metadata(self, instance: Any) -> None:
        """Output AI-friendly JSON metadata for a command to stdout."""

        def _type_name(t: Any) -> str:
            return t.__name__ if hasattr(t, "__name__") else str(t)

        arguments = (
            instance.get_arguments() if hasattr(instance, "get_arguments") else []
        )
        metadata_doc = {
            "command": instance.name,
            "description": instance.help,
            "agent_hints": getattr(instance, "agent_help", "") or instance.help,
            "examples": list(getattr(instance, "examples", [])),
            "requires_auth": bool(getattr(instance, "requires_auth", False)),
            "requires_project": bool(getattr(instance, "requires_project", False)),
            "non_interactive": True,
            "output_formats": ["json", "yaml", "table", "csv"],
            "parameters": [
                {
                    "name": arg.get("name")
                    or (arg.get("names", [""])[0] if arg.get("names") else ""),
                    "help": arg.get("help", ""),
                    "required": bool(
                        arg.get("required", not arg.get("is_option", False))
                    ),
                    "type": "bool"
                    if arg.get("is_flag")
                    else _type_name(arg.get("type", str)),
                    "is_option": bool(arg.get("is_option", False)),
                    "is_flag": bool(arg.get("is_flag", False)),
                    "default": arg.get("default"),
                }
                for arg in arguments
            ],
        }
        click.echo(json.dumps(metadata_doc, indent=2, default=str))

    def _create_click_group(self, group_instance: BaseGroupCommand) -> click.Group:
        """Create a Click group from a BaseGroupCommand instance.

        Args:
            group_instance: Instance of BaseGroupCommand

        Returns:
            Click group object
        """
        # Use the group's own method to create the Click group
        if hasattr(group_instance, "get_click_group"):
            group = group_instance.get_click_group()
        else:
            # Fallback for basic group creation
            def group_func(**kwargs: Any) -> Any:
                ctx = click.get_current_context()
                return group_instance.execute(ctx, **kwargs)

            group_func.__name__ = group_instance.name.replace("-", "_")
            group_func.__doc__ = group_instance.help

            group = click.Group(
                name=group_instance.name,
                callback=group_func,
                help=group_instance.help,
                short_help=group_instance.short_help or group_instance.help,
                invoke_without_command=getattr(
                    group_instance, "invoke_without_command", False
                ),
            )

            # Add arguments and options to the group
            group = cast(
                "click.Group",
                self._add_command_arguments(group, group_instance),
            )

        # Load subcommands for this group
        self._load_subcommands_for_group(group, group_instance)

        return group

    def _load_subcommands_for_group(
        self, group: click.Group, group_instance: BaseGroupCommand
    ) -> None:
        """Load subcommands for a group command.

        Args:
            group: Click Group to add subcommands to
            group_instance: Instance of BaseGroupCommand
        """
        # Load both built-in subcommands and plugin subcommands
        subcommand_groups = [
            # Built-in subcommands
            f"deepctl.subcommands.{group_instance.name}",
            f"deepctl.subplugins.{group_instance.name}",  # Plugin subcommands
        ]

        builtin_modules = BUILTIN_SUBCOMMAND_MODULES.get(group_instance.name, {})
        reserved = self._existing_names(group)
        prefix = f"{group_instance.name} "

        for subcommand_group in subcommand_groups:
            try:
                entry_points = metadata.entry_points()
                for entry_point in entry_points.select(group=subcommand_group):
                    try:
                        if self._refused_by_entry_point_name(
                            entry_point, builtin_modules, reserved, prefix
                        ):
                            continue

                        # Load the subcommand class
                        subcommand_class = entry_point.load()

                        # Create instance
                        subcommand_instance = subcommand_class()

                        # Built-in subcommand names are reserved too
                        name = _command_name(subcommand_instance)
                        kind = self._admit(
                            name, entry_point, builtin_modules, reserved, prefix
                        )
                        if kind is None:
                            continue

                        # Create Click command for the subcommand
                        click_subcommand = self._create_click_command(
                            subcommand_instance
                        )

                        # Click registers by the command's own name, which a
                        # plugin's group can set to anything: check that name
                        # too, and register under it.
                        checked = _command_name(click_subcommand)
                        if checked != name:
                            kind = self._admit(
                                checked,
                                entry_point,
                                builtin_modules,
                                reserved,
                                prefix,
                            )
                            if kind is None:
                                continue
                        if kind == "override":
                            self._announce_on_invoke(
                                click_subcommand, f"{prefix}{checked}", entry_point
                            )

                        # Add to the group
                        group.add_command(click_subcommand, name=checked)

                        # Store reference in the group instance
                        if hasattr(group_instance, "add_subcommand"):
                            group_instance.add_subcommand(
                                entry_point.name, subcommand_class
                            )

                    except Exception as e:
                        _load_error(
                            "subcommand",
                            f"{entry_point.name} for {group_instance.name}",
                            e,
                        )

            except Exception as e:
                _load_error("subcommands from", subcommand_group, e)

        missing = {
            name: module
            for name, module in builtin_modules.items()
            if name not in self._existing_names(group)
        }
        if missing:
            self._restore_hidden_subcommands(group, group_instance, missing)

    def _restore_hidden_subcommands(
        self,
        group: click.Group,
        group_instance: BaseGroupCommand,
        missing: Mapping[str, str],
    ) -> None:
        """Load built-in subcommands the entry point scan didn't find."""
        try:
            entry_points = self._hidden_builtin_entry_points(
                f"deepctl.subcommands.{group_instance.name}", missing
            )
            for ep in entry_points:
                if ep.name in self._existing_names(group):
                    continue
                try:
                    subcommand_class = ep.load()
                    instance = subcommand_class()
                    if _command_name(instance) != ep.name:
                        continue
                    click_subcommand = self._create_click_command(instance)
                    if _command_name(click_subcommand) != ep.name:
                        continue
                    group.add_command(click_subcommand, name=ep.name)
                    if hasattr(group_instance, "add_subcommand"):
                        group_instance.add_subcommand(ep.name, subcommand_class)
                except Exception as e:
                    _load_error("subcommand", f"{ep.name} for {group_instance.name}", e)
        except Exception as e:
            _load_error("built-in subcommands", "", e)

    def _add_command_arguments(
        self, cmd: click.Command, command_instance: Any
    ) -> click.Command:
        """Add arguments and options to a Click command.

        Args:
            cmd: Click command
            command_instance: BaseCommand instance

        Returns:
            Updated Click command
        """
        # Get arguments from command instance
        if hasattr(command_instance, "get_arguments"):
            arguments = command_instance.get_arguments()

            # Add arguments in reverse order (Click applies decorators
            # in reverse)
            for arg in reversed(arguments):
                if arg.get("is_option", False):
                    # Add as option
                    is_flag = arg.get("is_flag", False)
                    # Flags default to False (not None) so Click passes the
                    # right type. Also skip `type` for flags — Click manages
                    # its own bool type for flags; passing type=str converts
                    # the False default to the string "False" which is truthy.
                    default_fallback = False if is_flag else None
                    option_kwargs: dict[str, Any] = {
                        "default": arg.get("default", default_fallback),
                        "help": arg.get("help", ""),
                        "required": arg.get("required", False),
                        "is_flag": is_flag,
                        "multiple": arg.get("multiple", False),
                    }
                    if not is_flag:
                        option_kwargs["type"] = arg.get("type", str)
                    cmd = click.option(*arg.get("names", []), **option_kwargs)(cmd)
                else:
                    # Add as argument
                    cmd = click.argument(
                        arg.get("name", ""),
                        type=arg.get("type", str),
                        required=arg.get("required", True),
                        nargs=arg.get("nargs", 1),
                    )(cmd)

        return cmd

    def get_command_list(self) -> list[str]:
        """Get list of loaded command names.

        Returns:
            List of command names
        """
        return list(self.command_classes.keys()) + list(self.loaded_plugins.keys())

    def get_command_info(self, command_name: str) -> PluginInfo | ErrorResult:
        """Get information about a specific command.

        Args:
            command_name: Name of the command

        Returns:
            Command information
        """
        if command_name in self.command_classes:
            cmd_class = self.command_classes[command_name]
            instance = cmd_class()
            return PluginInfo(
                name=instance.name,
                help=instance.help,
                short_help=instance.short_help,
                type="builtin",
                module=cmd_class.__module__,
            )

        elif command_name in self.loaded_plugins:
            instance = self.loaded_plugins[command_name]
            return PluginInfo(
                name=instance.name,
                help=instance.help,
                short_help=instance.short_help,
                type="external",
                module=instance.__class__.__module__,
            )

        else:
            return ErrorResult(error=f"Command '{command_name}' not found")

    def reload_plugins(self, cli_group: click.Group) -> None:
        """Reload all plugins.

        Args:
            cli_group: Main CLI group
        """
        # Clear existing plugins
        self.loaded_plugins.clear()
        self.command_classes.clear()

        # Remove commands from CLI group
        cli_group.commands.clear()

        # Reload plugins
        self.load_plugins(cli_group)

    def validate_plugin(self, plugin_class: type[Any]) -> bool:
        """Validate that a plugin class is properly implemented.

        Args:
            plugin_class: Plugin class to validate

        Returns:
            True if valid, False otherwise
        """
        try:
            # Check if it's a subclass of BaseCommand
            if not issubclass(plugin_class, BaseCommand):
                return False

            # Check required attributes
            instance = plugin_class()

            required_attrs = ["name", "help"]
            for attr in required_attrs:
                if not hasattr(instance, attr) or not getattr(instance, attr):
                    return False

            # Check if execute method exists
            return hasattr(instance, "execute")

        except Exception:
            return False

    def discover_plugin_directories(self) -> list[Path]:
        """Discover directories that might contain plugins.

        Returns:
            List of plugin directories
        """
        plugin_dirs = []

        # Built-in plugins directory
        builtin_plugins = Path(__file__).parent.parent / "plugins"
        if builtin_plugins.exists():
            plugin_dirs.append(builtin_plugins)

        # User plugins directory
        from .config import Config

        config = Config()
        user_plugins = config.config_dir / "plugins"
        if user_plugins.exists():
            plugin_dirs.append(user_plugins)

        return plugin_dirs
