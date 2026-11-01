"""Plugin management command implementation."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rich.text import Text

    from .strategies import PluginInstallStrategy

import click
from deepctl_cmd_update.installation import InstallationDetector, InstallMethod
from deepctl_core import messages as msg
from deepctl_core._plugin_trust import real_home_plugin_venv
from deepctl_core.auth import AuthManager
from deepctl_core.base_group_command import BaseGroupCommand
from deepctl_core.client import DeepgramClient
from deepctl_core.config import Config
from deepctl_core.output import (
    is_ai_agent,
    print_error,
    print_hint,
    print_info,
    print_success,
    print_warning,
)
from deepctl_core.plugin_env import (
    find_system_python,
    get_plugin_state,
    get_venv_python,
    is_frozen,
    plugin_paths,
    read_plugin_state_file,
    save_plugin_state,
    skipped_plugin_venvs,
)
from deepctl_core.safe_text import literal
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from .models import (
    PluginAction,
    PluginInstallOptions,
    PluginOperationResult,
    PluginPackage,
    PluginRegistryEntry,
    PluginSearchResult,
)

console = Console()

E_PLUGIN_ENV_UNAVAILABLE = (
    "Can't use the plugin environment: when an AI agent runs dg, plugins load "
    "only from your account's home directory, not through a symlink or from "
    "inside the working directory."
)
A_PLUGIN_ENV_UNAVAILABLE = (
    "A person must run this command in their own terminal. If you are an AI "
    "agent, ask the user."
)


def _print_env_unavailable_hint() -> None:
    """The agent hint after E-PLUGIN-ENV-UNAVAILABLE."""
    print_hint(A_PLUGIN_ENV_UNAVAILABLE)


def _refuse_in_agent_session(action: str) -> None:
    """Installed plugins run in every later dg command: an AI agent session
    never installs, updates or removes them. Scripts and CI without an agent
    marker still can."""
    if is_ai_agent():
        print_error(msg.E_PLUGIN_AGENT.format(action=action))
        print_hint(msg.E_PERSON_ONLY_AGENT_HINT)
        raise SystemExit(1)


class PluginCommand(BaseGroupCommand):
    """Plugin management command."""

    name = "plugin"
    help = "Manage deepctl plugins"

    examples = [
        "dg plugin search example",
        "dg plugin install deepctl-plugin-example",
        "dg plugin list",
        "dg plugin update deepctl-plugin-example",
        "dg plugin remove deepctl-plugin-example",
    ]
    agent_help = (
        "Install, update, remove, search, and list deepctl plugins. "
        "Plugins extend deepctl with additional commands. Uses an isolated "
        "virtual environment for plugin dependencies."
    )

    def __init__(self) -> None:
        """Initialize plugin command."""
        super().__init__()
        self.detector = InstallationDetector()
        self._python_executable = sys.executable

        # Shared plugin environment paths. None when this run must not use
        # the plugin environment (see plugin_env.plugin_paths).
        paths = plugin_paths()
        self._plugin_paths = paths
        self._plugin_dir = paths.dir if paths else None
        self._plugin_venv = paths.venv if paths else None
        self._plugin_state_file = paths.state_file if paths else None

    def execute(self, ctx: click.Context, **kwargs: Any) -> None:
        """Execute plugin group command.

        Args:
            ctx: Click context
            **kwargs: Additional arguments
        """
        # Get config from context
        config = ctx.obj.get("config") if ctx.obj else None
        if not config:
            # If no config in context, create one
            config = Config()

        # Create auth manager and client
        auth_manager = AuthManager(config)
        client = DeepgramClient(config, auth_manager)

        # Store in context for subcommands
        ctx.obj = ctx.obj or {}
        ctx.obj["config"] = config
        ctx.obj["auth_manager"] = auth_manager
        ctx.obj["client"] = client

        # Continue with normal group execution
        super().execute(ctx, **kwargs)

    def setup_commands(self) -> list[click.Command]:
        """Set up plugin management commands.

        Returns:
            List of subcommands
        """

        # Create a wrapper for subcommands to handle context
        def context_wrapper(func: Any) -> Any:
            """Wrap subcommand to provide config and auth."""

            @click.pass_context
            def wrapper(ctx: click.Context, /, **kwargs: Any) -> Any:
                # Try to get from parent context first
                if ctx.parent and ctx.parent.obj:
                    config = ctx.parent.obj.get("config")
                    auth_manager = ctx.parent.obj.get("auth_manager")
                    client = ctx.parent.obj.get("client")
                    if config and auth_manager and client:
                        return func(config, auth_manager, client, **kwargs)

                # Fallback - look for deepctl_context
                current_ctx: click.Context | None = ctx
                while current_ctx:
                    if hasattr(current_ctx, "deepctl_context"):
                        config = current_ctx.deepctl_context.get("config")
                        if config:
                            auth_manager = AuthManager(config)
                            client = DeepgramClient(config, auth_manager)
                            return func(config, auth_manager, client, **kwargs)
                    current_ctx = current_ctx.parent

                # Final fallback
                config = Config()
                auth_manager = AuthManager(config)
                client = DeepgramClient(config, auth_manager)
                return func(config, auth_manager, client, **kwargs)

            wrapper.__name__ = func.__name__
            wrapper.__doc__ = func.__doc__
            return wrapper

        # Define all subcommands
        return [
            self._create_install_command(context_wrapper),
            self._create_list_command(context_wrapper),
            self._create_update_command(context_wrapper),
            self._create_remove_command(context_wrapper),
            self._create_search_command(context_wrapper),
        ]

    def _ensure_plugin_environment(self) -> tuple[bool, str]:
        """Ensure plugin environment exists.

        In frozen (PyInstaller) builds, ``sys.executable`` is the binary
        itself and cannot create venvs.  We detect this via
        :func:`is_frozen` and fall back to a real system Python found
        on PATH.

        Returns:
            Tuple of (success, python_executable_path)
        """
        if self._plugin_dir is None or self._plugin_venv is None:
            return False, ""

        # Create plugin directory if it doesn't exist
        self._plugin_dir.mkdir(parents=True, exist_ok=True)

        # Check if venv exists
        if not self._plugin_venv.exists():
            print_info("Creating plugin environment...")

            # Determine which Python to use for venv creation
            if is_frozen():
                creator_python = find_system_python()
                if creator_python is None:
                    print_error(
                        "Cannot create plugin environment: no suitable "
                        "Python >= 3.10 found on your system.  "
                        "Please install Python 3.10+ and try again."
                    )
                    return False, ""
                print_info(f"Using system Python: {creator_python}")
            else:
                creator_python = sys.executable

            try:
                # Create virtual environment
                subprocess.run(
                    [creator_python, "-m", "venv", str(self._plugin_venv)],
                    check=True,
                    capture_output=True,
                    text=True,
                )

                # Get the python executable in the venv
                venv_python = get_venv_python(self._plugin_venv)
                if venv_python is None:
                    if sys.platform == "win32":
                        venv_python_path = self._plugin_venv / "Scripts" / "python.exe"
                    else:
                        venv_python_path = self._plugin_venv / "bin" / "python"
                    venv_python = str(venv_python_path)

                # Ensure pip is installed and up to date
                subprocess.run(
                    [venv_python, "-m", "pip", "install", "--upgrade", "pip"],
                    check=True,
                    capture_output=True,
                    text=True,
                )

                # Install deepctl-core so plugins can import BaseCommand etc.
                self._install_core_into_venv(venv_python)

                print_success("Plugin environment created successfully")
                return True, venv_python

            except subprocess.CalledProcessError as e:
                print_error(f"Failed to create plugin environment: {e}")
                return False, ""

        # Venv exists — return its python executable
        venv_python = get_venv_python(self._plugin_venv)
        if venv_python is None:
            if sys.platform == "win32":
                venv_python = str(self._plugin_venv / "Scripts" / "python.exe")
            else:
                venv_python = str(self._plugin_venv / "bin" / "python")

        return True, venv_python

    def _install_core_into_venv(self, venv_python: str) -> None:
        """Install the current version of deepctl-core into the plugin venv.

        This ensures plugins can ``from deepctl_core import BaseCommand``
        even when running from an isolated environment.
        """
        try:
            from importlib.metadata import version as pkg_version

            core_version = pkg_version("deepctl-core")
            package_spec = f"deepctl-core=={core_version}"
        except Exception:
            package_spec = "deepctl-core"

        try:
            subprocess.run(
                [venv_python, "-m", "pip", "install", package_spec],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError:
            # Non-fatal — plugins may still work if they don't import
            # from deepctl_core directly.
            print_warning(
                "Could not install deepctl-core into plugin environment. "
                "Some plugins may not load correctly."
            )

    def _get_plugin_state(self) -> dict[str, Any]:
        """Get plugin state from file.

        Returns:
            Plugin state dictionary
        """
        if self._plugin_state_file is None:
            return {"plugins": {}}
        return get_plugin_state(self._plugin_state_file)

    def _save_plugin_state(self, state: dict[str, Any]) -> None:
        """Save plugin state to file.

        Args:
            state: Plugin state to save
        """
        if self._plugin_paths is None:
            raise RuntimeError(E_PLUGIN_ENV_UNAVAILABLE)
        save_plugin_state(state, self._plugin_paths)

    def _create_install_command(self, context_wrapper: Any) -> click.Command:
        """Create the install subcommand."""

        @click.command(name="install", help="Install a plugin")
        @click.argument("package")
        @click.option(
            "--version",
            "-v",
            help="Specific version to install",
        )
        @click.option(
            "--upgrade",
            "-U",
            is_flag=True,
            help="Upgrade if already installed",
        )
        @click.option(
            "--pre",
            is_flag=True,
            help="Include pre-release versions",
        )
        @click.option(
            "--force-reinstall",
            is_flag=True,
            help="Force reinstallation",
        )
        @click.option(
            "--index-url",
            help="Base URL of Python package index",
        )
        @click.option(
            "--extra-index-url",
            help="Extra URLs of package indexes",
        )
        @click.option(
            "--git",
            "git_url",
            help="Install from git repository URL",
        )
        @click.option(
            "--editable",
            "-e",
            is_flag=True,
            help="Install in editable mode",
        )
        def install_cmd(**kwargs: Any) -> None:
            """Install a plugin handler."""
            pass  # Handler is wrapped by context_wrapper

        # Apply the context wrapper
        install_cmd.callback = context_wrapper(
            lambda config, auth_manager, client, **kwargs: self._handle_install(
                config, auth_manager, client, **kwargs
            )
        )

        return install_cmd

    def _handle_install(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> None:
        """Handle install command."""
        _refuse_in_agent_session("install")
        # Extract package argument
        package = kwargs.pop("package", "")

        # Check if package is a git URL
        if package.startswith("git+") or (
            (package.startswith("http://") or package.startswith("https://"))
            and ".git" in package
        ):
            # This is a git URL
            kwargs["git_url"] = package
            # Extract package name from URL for tracking
            # e.g., git+https://github.com/user/deepctl-plugin-foo.git -> deepctl-plugin-foo
            if "/" in package:
                potential_name = package.split("/")[-1]
                if potential_name.endswith(".git"):
                    potential_name = potential_name[:-4]
                kwargs["package"] = potential_name
            else:
                kwargs["package"] = package
        else:
            kwargs["package"] = package

        options = PluginInstallOptions(**kwargs)
        result = self.install_plugin(config, auth_manager, client, options)

        if result.success:
            print_success(result.message)
            self._maybe_update_skills()
        else:
            print_error(result.error or result.message)
            if result.error == E_PLUGIN_ENV_UNAVAILABLE:
                _print_env_unavailable_hint()
            raise click.ClickException(result.error or result.message)

    def _create_list_command(self, context_wrapper: Any) -> click.Command:
        """Create the list subcommand."""

        @click.command(name="list", help="List installed plugins")
        @click.option(
            "--verbose",
            "-v",
            is_flag=True,
            help="Show detailed information",
        )
        def list_cmd(**kwargs: Any) -> None:
            """List plugins handler."""
            pass  # Handler is wrapped by context_wrapper

        # Apply the context wrapper
        list_cmd.callback = context_wrapper(
            lambda config, auth_manager, client, **kwargs: self.list_plugins(
                config, auth_manager, client, **kwargs
            )
        )

        return list_cmd

    def _create_update_command(self, context_wrapper: Any) -> click.Command:
        """Create the update subcommand."""

        @click.command(name="update", help="Update a plugin")
        @click.argument("package")
        @click.option(
            "--pre",
            is_flag=True,
            help="Include pre-release versions",
        )
        def update_cmd(**kwargs: Any) -> None:
            """Update plugin handler."""
            pass  # Handler is wrapped by context_wrapper

        # Apply the context wrapper
        update_cmd.callback = context_wrapper(
            lambda config, auth_manager, client, **kwargs: self._handle_update(
                config, auth_manager, client, **kwargs
            )
        )

        return update_cmd

    def _handle_update(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> None:
        """Handle update command."""
        _refuse_in_agent_session("update")
        options = PluginInstallOptions(
            package=kwargs["package"],
            upgrade=True,
            pre=kwargs.get("pre", False),
        )
        result = self.install_plugin(config, auth_manager, client, options)

        if result.success:
            print_success(result.message)
            self._maybe_update_skills()
        else:
            print_error(result.error or result.message)
            if result.error == E_PLUGIN_ENV_UNAVAILABLE:
                _print_env_unavailable_hint()
            raise click.ClickException(result.error or result.message)

    def _create_remove_command(self, context_wrapper: Any) -> click.Command:
        """Create the remove subcommand."""

        @click.command(name="remove", help="Remove a plugin")
        @click.argument("package")
        @click.option(
            "--yes",
            "-y",
            is_flag=True,
            help="Skip confirmation",
        )
        def remove_cmd(**kwargs: Any) -> None:
            """Remove plugin handler."""
            pass  # Handler is wrapped by context_wrapper

        # Apply the context wrapper
        remove_cmd.callback = context_wrapper(
            lambda config, auth_manager, client, **kwargs: self._handle_remove(
                config, auth_manager, client, **kwargs
            )
        )

        return remove_cmd

    def _handle_remove(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> None:
        """Handle remove command."""
        _refuse_in_agent_session("remove")
        package = kwargs["package"]
        yes = kwargs.get("yes", False)

        # err=True: the prompt is interaction, not output. Keeping it off
        # stdout is what lets `dg plugin remove ... -o json` stay parseable,
        # and matches the sibling prompt in deepctl-cmd-keys.
        if not yes and not click.confirm(
            f"Are you sure you want to remove {package}?", err=True
        ):
            # Abort rather than return: this is a group subcommand, so there is
            # no result for BaseCommand.EXIT_CODES to map, and a bare return
            # exits 0 -- indistinguishable from a successful removal. main.py
            # turns Abort into the documented exit 2 for user cancellation.
            raise click.Abort()

        result = self.remove_plugin(config, auth_manager, client, package)

        if result.success:
            print_success(result.message)
            self._maybe_update_skills()
        else:
            print_error(result.error or result.message)
            if result.error == E_PLUGIN_ENV_UNAVAILABLE:
                _print_env_unavailable_hint()
            raise click.ClickException(result.error or result.message)

    def _create_search_command(self, context_wrapper: Any) -> click.Command:
        """Create the search subcommand."""

        @click.command(name="search", help="Search for available plugins")
        @click.argument("query", required=False)
        @click.option(
            "--installed",
            is_flag=True,
            help="Show only installed plugins",
        )
        @click.option(
            "--available",
            is_flag=True,
            help="Show only available (not installed) plugins",
        )
        def search_cmd(**kwargs: Any) -> None:
            """Search plugins handler."""
            pass  # Handler is wrapped by context_wrapper

        # Apply the context wrapper
        search_cmd.callback = context_wrapper(
            lambda config, auth_manager, client, **kwargs: self._handle_search(
                config, auth_manager, client, **kwargs
            )
        )

        return search_cmd

    def _handle_search(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> None:
        """Handle search command."""
        query = kwargs.get("query", "")
        show_installed = kwargs.get("installed", False)
        show_available = kwargs.get("available", False)

        # Get available plugins from registry
        registry_plugins = self._get_plugin_registry()

        # Get installed plugins
        installed_plugins = {p.name: p for p in self._discover_plugins()}

        # Filter plugins based on query
        search_results = []
        for plugin in registry_plugins:
            # Check if plugin matches query
            if query:
                query_lower = query.lower()
                if (
                    query_lower not in plugin.name.lower()
                    and query_lower not in plugin.description.lower()
                    and not any(query_lower in kw.lower() for kw in plugin.keywords)
                ):
                    continue

            # Check installed status
            is_installed = (
                plugin.install_name is not None
                and plugin.install_name in installed_plugins
            ) or plugin.name in installed_plugins
            installed_version = None
            if is_installed:
                installed_plugin = (
                    installed_plugins.get(plugin.install_name)
                    if plugin.install_name
                    else None
                ) or installed_plugins.get(plugin.name)
                if installed_plugin:
                    installed_version = installed_plugin.version

            # Apply filters
            if show_installed and not is_installed:
                continue
            if show_available and is_installed:
                continue

            search_results.append(
                PluginSearchResult(
                    plugin=plugin,
                    installed=is_installed,
                    installed_version=installed_version,
                )
            )

        # Display results
        if not search_results:
            if query:
                print_info(f"No plugins found matching '{query}'")
            else:
                print_info("No plugins found")
            return

        # Create table
        table = Table(title="Available Plugins")
        table.add_column("Name", style="cyan", no_wrap=True)
        table.add_column("Description", style="white")
        table.add_column("Status", style="yellow")

        for result in search_results:
            status: str | Text = "Installed" if result.installed else "Available"
            if result.installed and result.installed_version:
                status = literal(f"Installed ({result.installed_version})")

            table.add_row(
                result.plugin.name,
                result.plugin.description,
                status,
            )

        console.print(table)

        # Show install hint
        available_count = sum(1 for r in search_results if not r.installed)
        if available_count > 0:
            print_info("To install one, run: dg plugin install <name>", stderr=True)

    def _get_plugin_registry(self) -> list[PluginRegistryEntry]:
        """Plugins Deepgram publishes, from a static list in this package.

        Returns:
            List of available plugins
        """
        return [
            PluginRegistryEntry(
                name="deepctl-plugin-example",
                description="Example plugin demonstrating the plugin system",
                author="Deepgram",
                url="https://github.com/deepgram/cli/tree/main/packages/deepctl-plugin-example",
                keywords=["example", "demo", "plugin"],
                install_name="deepctl-plugin-example",
            ),
        ]

    def _needs_isolated_venv(self, method: InstallMethod) -> bool:
        """Check if this installation method needs the isolated plugin venv."""
        return method in (
            InstallMethod.HOMEBREW,
            InstallMethod.SYSTEM,
            InstallMethod.UNKNOWN,
        )

    def _get_strategy(
        self, method: InstallMethod
    ) -> tuple[PluginInstallStrategy, bool]:
        """Get the correct installation strategy and whether it uses the plugin venv.

        Returns:
            Tuple of (strategy, uses_plugin_venv).
        """
        from .strategies import get_strategy

        if self._needs_isolated_venv(method):
            if self._plugin_paths is None:
                raise RuntimeError(E_PLUGIN_ENV_UNAVAILABLE)
            print_info(
                f"{method.value.title()} installation detected, "
                "using isolated plugin environment..."
            )
            success, python_exe = self._ensure_plugin_environment()
            if not success:
                raise RuntimeError("Could not create isolated environment for plugins")
            return get_strategy(method, venv_python=python_exe), True

        return get_strategy(method), False

    def install_plugin(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        options: PluginInstallOptions,
    ) -> PluginOperationResult:
        """Install a plugin.

        Detects the installation method and delegates to the appropriate
        strategy (pip, pipx inject, uv tool --with, isolated venv, etc.).

        Args:
            config: CLI configuration
            auth_manager: Authentication manager
            client: Deepgram client
            options: Installation options

        Returns:
            Operation result
        """
        install_info = self.detector.detect()

        try:
            strategy, using_plugin_env = self._get_strategy(install_info.method)
        except RuntimeError as exc:
            return PluginOperationResult(
                success=False,
                action=PluginAction.INSTALL,
                package=options.package,
                message="Failed to create plugin environment",
                error=str(exc),
            )

        print_info(f"Installing {options.package}...")
        ok, output = strategy.install(options)

        if not ok:
            return PluginOperationResult(
                success=False,
                action=PluginAction.INSTALL,
                package=options.package,
                message=f"Failed to install {options.package}",
                error=output,
            )

        # Get installed version
        target_python = (
            get_venv_python(self._plugin_venv)
            if using_plugin_env
            else self._python_executable
        )
        installed_version = self._get_package_version(options.package, target_python)

        # Track state for isolated venv installs
        if using_plugin_env:
            state = self._get_plugin_state()
            state["plugins"][options.package] = {
                "version": installed_version,
                "source": "plugin_env",
            }
            self._save_plugin_state(state)

        return PluginOperationResult(
            success=True,
            action=PluginAction.INSTALL,
            package=options.package,
            message=f"Successfully installed {options.package}",
            installed_version=installed_version,
        )

    def list_plugins(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        verbose: bool = False,
    ) -> None:
        """List installed plugins.

        Args:
            config: CLI configuration
            auth_manager: Authentication manager
            client: Deepgram client
            verbose: Show detailed information
        """
        # Get all installed packages that provide deepctl plugins
        plugins = self._discover_plugins()
        skipped = self._skipped_plugins()

        if not plugins and not skipped:
            print_info("No plugins installed")
            return

        # Create table
        table = Table(title="Installed Plugins")
        table.add_column("Name", style="cyan", no_wrap=True)
        table.add_column("Version", style="green")
        table.add_column("Entry Point", style="dim")

        if verbose:
            table.add_column("Type", style="yellow")
            table.add_column("Commands", style="magenta")
            table.add_column("Environment", style="blue")

        for plugin in plugins:
            row: list[str | Text] = [
                literal(plugin.name),
                literal(plugin.version or "Unknown"),
                literal(plugin.entry_point or "N/A"),
            ]

            if verbose:
                plugin_type = "Built-in" if plugin.is_builtin else "External"
                commands = self._get_plugin_commands(plugin.name)

                # Check if this is from plugin environment
                state = self._get_plugin_state()
                env = (
                    "Plugin Env"
                    if plugin.name in state.get("plugins", {})
                    else "Main Env"
                )

                row.extend([plugin_type, literal(commands), env])

            table.add_row(*row)

        for name, version in skipped:
            skipped_row: list[str | Text] = [literal(name), literal(version), "Skipped"]
            if verbose:
                skipped_row.extend(["External", "", "Plugin Env"])
            table.add_row(*skipped_row)

        console.print(table)

        if verbose:
            # Show installation info
            install_info = self.detector.detect()
            print_info(f"\nInstallation method: {install_info.method.value}")
            if install_info.method == InstallMethod.SYSTEM:
                print_info(
                    "Using isolated plugin environment at: ~/.deepctl/plugins/venv"
                )

    def update_plugin(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        package: str,
        pre: bool = False,
    ) -> PluginOperationResult:
        """Update a plugin.

        Args:
            config: CLI configuration
            auth_manager: Authentication manager
            client: Deepgram client
            package: Package name to update
            pre: Include pre-release versions

        Returns:
            Operation result
        """
        options = PluginInstallOptions(
            package=package,
            upgrade=True,
            pre=pre,
        )
        return self.install_plugin(config, auth_manager, client, options)

    def remove_plugin(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        package: str,
    ) -> PluginOperationResult:
        """Remove a plugin.

        Detects the installation method and delegates to the appropriate
        strategy for uninstallation.

        Args:
            config: CLI configuration
            auth_manager: Authentication manager
            client: Deepgram client
            package: Package name to remove

        Returns:
            Operation result
        """
        # Check if plugin is installed
        plugins = self._discover_plugins()
        plugin_found = any(p.name == package for p in plugins if not p.is_builtin)

        if not plugin_found:
            if any(name == package for name, _version in self._skipped_plugins()):
                return PluginOperationResult(
                    success=False,
                    action=PluginAction.REMOVE,
                    package=package,
                    message=f"Failed to remove {package}",
                    error=E_PLUGIN_ENV_UNAVAILABLE,
                )
            return PluginOperationResult(
                success=False,
                action=PluginAction.REMOVE,
                package=package,
                message=f"Plugin {package} is not installed",
            )

        # Detect installation method
        install_info = self.detector.detect()

        # Check if the plugin lives in the isolated venv
        state = self._get_plugin_state()
        using_plugin_env = package in state.get("plugins", {})

        if using_plugin_env:
            # Force isolated-venv strategy for this plugin
            from .strategies import IsolatedVenvStrategy

            ok_env, python_exe = self._ensure_plugin_environment()
            if not ok_env:
                return PluginOperationResult(
                    success=False,
                    action=PluginAction.REMOVE,
                    package=package,
                    message=f"Failed to remove {package}",
                    error=E_PLUGIN_ENV_UNAVAILABLE,
                )
            strategy: PluginInstallStrategy = IsolatedVenvStrategy(python_exe)
        elif self._needs_isolated_venv(install_info.method):
            return PluginOperationResult(
                success=False,
                action=PluginAction.REMOVE,
                package=package,
                message="Cannot remove plugin from system installation",
                error="This plugin was not installed via deepctl",
            )
        else:
            try:
                strategy, _ = self._get_strategy(install_info.method)
            except RuntimeError as exc:
                return PluginOperationResult(
                    success=False,
                    action=PluginAction.REMOVE,
                    package=package,
                    message=f"Failed to remove {package}",
                    error=str(exc),
                )

        print_info(f"Removing {package}...")
        ok, output = strategy.uninstall(package)

        if not ok:
            return PluginOperationResult(
                success=False,
                action=PluginAction.REMOVE,
                package=package,
                message=f"Failed to remove {package}",
                error=output,
            )

        # Clean up plugin state for isolated venv installs
        if using_plugin_env:
            state = self._get_plugin_state()
            state["plugins"].pop(package, None)
            self._save_plugin_state(state)

        return PluginOperationResult(
            success=True,
            action=PluginAction.REMOVE,
            package=package,
            message=f"Successfully removed {package}",
        )

    def _skipped_plugins(self) -> list[tuple[str, str]]:
        """Plugins recorded in the real-home plugin venv when this run skips it.

        Only that venv's ``plugins.json`` is read (defensively); a skipped
        venv found through ``$HOME`` is never read, and nothing in any
        skipped venv runs.
        """
        rows: list[tuple[str, str]] = []
        trusted = real_home_plugin_venv()
        if trusted is None:
            return rows
        for venv in skipped_plugin_venvs():
            if venv != trusted:
                continue
            state = read_plugin_state_file(venv.parent / "plugins.json")
            entries = state.get("plugins")
            if not isinstance(entries, dict):
                continue
            for name, info in entries.items():
                version = info.get("version") if isinstance(info, dict) else None
                rows.append(
                    (str(name), version if isinstance(version, str) else "Unknown")
                )
        return rows

    def _discover_plugins(self) -> list[PluginPackage]:
        """Discover all installed plugins.

        Returns:
            List of discovered plugins
        """
        plugins = []
        discovered_names = set()

        # Discover from main environment
        main_env_plugins = self._discover_from_environment(sys.executable)
        for plugin in main_env_plugins:
            # Use a combination of name and entry point to identify unique plugins
            plugin_key = f"{plugin.name}:{plugin.entry_point}"
            if plugin_key not in discovered_names:
                discovered_names.add(plugin_key)
                plugins.append(plugin)

        # Also discover from plugin environment if it exists (never from a
        # venv this run doesn't use; see plugin_env.plugin_paths)
        if self._plugin_venv is not None and self._plugin_venv.exists():
            ok_env, plugin_python = self._ensure_plugin_environment()
            plugin_env_plugins = (
                self._discover_from_environment(plugin_python) if ok_env else []
            )

            # Add only plugins not already discovered
            for plugin in plugin_env_plugins:
                plugin_key = f"{plugin.name}:{plugin.entry_point}"
                if plugin_key not in discovered_names:
                    discovered_names.add(plugin_key)
                    plugins.append(plugin)

        return plugins

    def _discover_from_environment(self, python_exe: str) -> list[PluginPackage]:
        """Discover plugins from a specific Python environment.

        Args:
            python_exe: Path to Python executable

        Returns:
            List of discovered plugins
        """
        plugins = []

        # Get entry points from the specified environment
        try:
            # Use a simpler approach - run importlib.metadata directly
            if python_exe == sys.executable:
                # For the current environment, use importlib directly
                import importlib.metadata as metadata

                for dist in metadata.distributions():
                    if dist.metadata:
                        eps = dist.entry_points
                        if hasattr(eps, "select"):
                            # Newer versions
                            for ep in eps.select(group="deepctl.commands"):
                                plugins.append(
                                    PluginPackage(
                                        name=dist.name,
                                        version=dist.version,
                                        entry_point=f"{ep.name}={ep.value}",
                                        is_builtin=dist.name.startswith("deepctl-cmd-"),
                                    )
                                )
                            # Also check for external plugins
                            for ep in eps.select(group="deepctl.plugins"):
                                plugins.append(
                                    PluginPackage(
                                        name=dist.name,
                                        version=dist.version,
                                        entry_point=f"{ep.name}={ep.value}",
                                        is_builtin=False,
                                    )
                                )
                        else:
                            # Older versions
                            if "deepctl.commands" in eps:  # type: ignore[comparison-overlap]
                                for ep in eps["deepctl.commands"]:
                                    plugins.append(
                                        PluginPackage(
                                            name=dist.name,
                                            version=dist.version,
                                            entry_point=f"{ep.name}={ep.value}",
                                            is_builtin=dist.name.startswith(
                                                "deepctl-cmd-"
                                            ),
                                        )
                                    )
                            # Also check for external plugins
                            if "deepctl.plugins" in eps:  # type: ignore[comparison-overlap]
                                for ep in eps["deepctl.plugins"]:
                                    plugins.append(
                                        PluginPackage(
                                            name=dist.name,
                                            version=dist.version,
                                            entry_point=f"{ep.name}={ep.value}",
                                            is_builtin=False,
                                        )
                                    )
            else:
                # For other environments, try subprocess
                code = """import json;from importlib import metadata;plugins=[];
for dist in metadata.distributions():
    if dist.metadata:
        eps = dist.entry_points
        if hasattr(eps, 'select'):
            for group in ['deepctl.commands', 'deepctl.plugins']:
                for ep in eps.select(group=group):
                    plugins.append({
                        "name": dist.name,
                        "version": dist.version,
                        "entry_point": f"{ep.name}={ep.value}",
                        "is_builtin": dist.name.startswith("deepctl-cmd-") if group == "deepctl.commands" else False
                    })
        else:
            for group in ['deepctl.commands', 'deepctl.plugins']:
                if group in eps:
                    for ep in eps[group]:
                        plugins.append({
                            "name": dist.name,
                            "version": dist.version,
                            "entry_point": f"{ep.name}={ep.value}",
                            "is_builtin": dist.name.startswith("deepctl-cmd-") if group == "deepctl.commands" else False
                        })
print(json.dumps(plugins))"""

                result = subprocess.run(
                    [python_exe, "-c", code],
                    capture_output=True,
                    text=True,
                    check=True,
                )

                plugin_data = json.loads(result.stdout)
                for data in plugin_data:
                    plugins.append(PluginPackage(**data))

        except Exception as e:
            # Silently ignore subprocess failures for plugin environments
            if python_exe != sys.executable:
                pass
            else:
                print_info(f"Warning: Could not discover plugins: {e}")

        return plugins

    def _get_package_version(
        self, package: str, python_exe: str | None = None
    ) -> str | None:
        """Get installed package version.

        Args:
            package: Package name
            python_exe: Python executable to use (defaults to current)

        Returns:
            Package version or None
        """
        if python_exe is None:
            python_exe = self._python_executable

        try:
            code = f"""
import importlib.metadata
try:
    print(importlib.metadata.version("{package}"))
except:
    print("")
"""
            result = subprocess.run(
                [python_exe, "-c", code],
                capture_output=True,
                text=True,
                check=True,
            )
            version = result.stdout.strip()
            return version if version else None
        except Exception:
            return None

    def _get_plugin_commands(self, plugin_name: str) -> str:
        """Get commands provided by a plugin.

        Args:
            plugin_name: Plugin package name

        Returns:
            Comma-separated list of commands
        """
        # This is a simplified version - in reality you'd introspect
        # the entry points to find actual command names
        return "varies"

    def _maybe_update_skills(self) -> None:
        """Refresh the recorded skills the way 'dg skills update' does (B3).

        Never changes the plugin command's exit code; a failure prints a
        warning on stderr.
        """
        from deepctl_core import skill_generator as sg

        try:
            state = sg.get_skills_state()
            names = {*state.get("skill_folders", {}), *state["installed_skills"]}
            if not state.get("auto_update", True):
                return
            plan = [
                (g, sg._ref_for(g.cli_name, state))
                for g in sg.get_all_generators()
                if g.cli_name in names and g.skills_root() is not None
            ]
            if not plan:  # None recorded, or hint-only only: records untouched.
                return
            for _gen, _paths, leftover in sg.install_for(plan, since=state):
                if leftover:
                    print_warning(escape(sg._msg("E12", staging=leftover)), stderr=True)
        except Exception as exc:  # Non-fatal: the plugin operation succeeded.
            sg.warn_install_failure(
                "AI assistant skills were not updated",
                exc,
                "run 'dg skills update' to try again",
            )
