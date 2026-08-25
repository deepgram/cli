"""Plugin manager for deepctl command discovery and loading."""

import json
import sys
from importlib import metadata
from pathlib import Path
from typing import Any, cast

import click
from rich.console import Console

from .base_command import BaseCommand
from .base_group_command import BaseGroupCommand
from .models import ErrorResult, PluginInfo
from .output import print_warning
from .plugin_env import (
    PLUGIN_VENV,
    get_plugin_state,
    get_venv_python_version,
    get_venv_site_packages,
)
from .timing import TimingContext

console = Console()


class PluginManager:
    """Manager for loading and organizing CLI plugins/commands."""

    def __init__(self) -> None:
        """Initialize plugin manager."""
        self.loaded_plugins: dict[str, Any] = {}
        self.command_classes: dict[str, type[Any]] = {}
        self._loaded_entry_point_values: set[str] = set()

    def load_plugins(self, cli_group: click.Group) -> None:
        """Load all plugins into the CLI group.

        Args:
            cli_group: Main CLI group to add commands to
        """
        # Load built-in commands
        with TimingContext("builtin_commands_loading"):
            self._load_builtin_commands(cli_group)

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

            for entry_point in command_entry_points:
                with TimingContext(f"load_command_{entry_point.name}"):
                    try:
                        # Load the command class
                        command_class = entry_point.load()

                        # Create instance
                        command_instance = command_class()

                        # Create Click command
                        click_command = self._create_click_command(command_instance)

                        # Add to CLI group
                        cli_group.add_command(click_command)

                        # Store reference and track for dedup
                        self.command_classes[entry_point.name] = command_class
                        self._loaded_entry_point_values.add(entry_point.value)

                    except Exception as e:
                        console.print(
                            f"[red]Error loading command {entry_point.name}:[/red] {e}"
                        )

        except Exception as e:
            console.print(f"[red]Error loading built-in commands:[/red] {e}")

    def _load_external_plugins(self, cli_group: click.Group) -> None:
        """Load external plugins from the plugins entry point group."""
        try:
            # Load plugins from entry points
            entry_points = metadata.entry_points()
            for entry_point in entry_points.select(group="deepctl.plugins"):
                try:
                    # Load the plugin class
                    plugin_class = entry_point.load()

                    # Create instance
                    plugin_instance = plugin_class()

                    # Create Click command
                    click_command = self._create_click_command(plugin_instance)

                    # Add to CLI group
                    cli_group.add_command(click_command)

                    # Store reference and track for dedup
                    self.loaded_plugins[entry_point.name] = plugin_instance
                    self._loaded_entry_point_values.add(entry_point.value)

                except Exception as e:
                    console.print(
                        f"[red]Error loading plugin {entry_point.name}:[/red] {e}"
                    )

        except Exception as e:
            console.print(f"[red]Error loading external plugins:[/red] {e}")

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
        """
        # Fast bail — nothing to do if the venv doesn't exist
        if not PLUGIN_VENV.exists():
            return

        # Check if there are any plugins tracked in state
        state = get_plugin_state()
        if not state.get("plugins"):
            return

        site_packages = get_venv_site_packages()
        if site_packages is None:
            return

        self._warn_if_plugin_venv_python_mismatch()

        site_packages_str = str(site_packages)

        # Append to sys.path so modules inside the plugin venv can be imported.
        # Using append (not insert) guarantees core deps resolve from main env.
        if site_packages_str not in sys.path:
            sys.path.append(site_packages_str)

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
                            plugin_class = ep.load()
                            plugin_instance = plugin_class()
                            click_command = self._create_click_command(plugin_instance)
                            cli_group.add_command(click_command)

                            self.loaded_plugins[ep.name] = plugin_instance
                            self._loaded_entry_point_values.add(ep.value)
                        except Exception as e:
                            console.print(
                                f"[red]Error loading plugin venv entry "
                                f"{ep.name}:[/red] {e}"
                            )

        except Exception as e:
            console.print(f"[red]Error loading plugins from plugin venv:[/red] {e}")

    def _warn_if_plugin_venv_python_mismatch(self) -> None:
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
        venv_version = get_venv_python_version()
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
            f"  rm -rf {PLUGIN_VENV} && dg plugin install <your-plugin>"
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

        for subcommand_group in subcommand_groups:
            try:
                entry_points = metadata.entry_points()
                for entry_point in entry_points.select(group=subcommand_group):
                    try:
                        # Load the subcommand class
                        subcommand_class = entry_point.load()

                        # Create instance
                        subcommand_instance = subcommand_class()

                        # Create Click command for the subcommand
                        click_subcommand = self._create_click_command(
                            subcommand_instance
                        )

                        # Add to the group
                        group.add_command(click_subcommand)

                        # Store reference in the group instance
                        if hasattr(group_instance, "add_subcommand"):
                            group_instance.add_subcommand(
                                entry_point.name, subcommand_class
                            )

                    except Exception as e:
                        console.print(
                            f"[red]Error loading subcommand "
                            f"{entry_point.name} for "
                            f"{group_instance.name}:[/red] {e}"
                        )

            except Exception as e:
                console.print(
                    f"[red]Error loading subcommands from {subcommand_group}:[/red] {e}"
                )

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
