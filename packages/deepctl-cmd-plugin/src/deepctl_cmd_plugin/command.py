"""Plugin management command implementation."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import click
from deepctl_cmd_update.installation import InstallationDetector, InstallMethod
from deepctl_core.auth import AuthManager
from deepctl_core.base_group_command import BaseGroupCommand
from deepctl_core.client import DeepgramClient
from deepctl_core.config import Config
from deepctl_core.output import print_error, print_info, print_success
from rich.console import Console
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


class PluginCommand(BaseGroupCommand):
    """Plugin management command."""

    name = "plugin"
    help = "Manage deepctl plugins"

    def __init__(self) -> None:
        """Initialize plugin command."""
        super().__init__()
        self.detector = InstallationDetector()
        self._python_executable = sys.executable

        # Plugin environment paths
        self._plugin_dir = Path.home() / ".deepctl" / "plugins"
        self._plugin_venv = self._plugin_dir / "venv"
        self._plugin_state_file = self._plugin_dir / "plugins.json"

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

        Returns:
            Tuple of (success, python_executable_path)
        """
        # Create plugin directory if it doesn't exist
        self._plugin_dir.mkdir(parents=True, exist_ok=True)

        # Check if venv exists
        if not self._plugin_venv.exists():
            print_info("Creating plugin environment...")
            try:
                # Create virtual environment
                subprocess.run(
                    [sys.executable, "-m", "venv", str(self._plugin_venv)],
                    check=True,
                    capture_output=True,
                    text=True,
                )

                # Get the python executable in the venv
                if sys.platform == "win32":
                    venv_python = self._plugin_venv / "Scripts" / "python.exe"
                else:
                    venv_python = self._plugin_venv / "bin" / "python"

                # Ensure pip is installed and up to date
                subprocess.run(
                    [
                        str(venv_python),
                        "-m",
                        "pip",
                        "install",
                        "--upgrade",
                        "pip",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )

                print_success("Plugin environment created successfully")
                return True, str(venv_python)

            except subprocess.CalledProcessError as e:
                print_error(f"Failed to create plugin environment: {e}")
                return False, ""

        # Venv exists, return python executable
        if sys.platform == "win32":
            venv_python = self._plugin_venv / "Scripts" / "python.exe"
        else:
            venv_python = self._plugin_venv / "bin" / "python"

        return True, str(venv_python)

    def _get_plugin_state(self) -> dict[str, Any]:
        """Get plugin state from file.

        Returns:
            Plugin state dictionary
        """
        if self._plugin_state_file.exists():
            try:
                result: dict[str, Any] = json.loads(
                    self._plugin_state_file.read_text()
                )
                return result
            except Exception:
                return {"plugins": {}}
        return {"plugins": {}}

    def _save_plugin_state(self, state: dict[str, Any]) -> None:
        """Save plugin state to file.

        Args:
            state: Plugin state to save
        """
        self._plugin_state_file.write_text(json.dumps(state, indent=2))

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
        else:
            print_error(result.error or result.message)
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
        options = PluginInstallOptions(
            package=kwargs["package"],
            upgrade=True,
            pre=kwargs.get("pre", False),
        )
        result = self.install_plugin(config, auth_manager, client, options)

        if result.success:
            print_success(result.message)
        else:
            print_error(result.error or result.message)
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
        package = kwargs["package"]
        yes = kwargs.get("yes", False)

        if not yes and not click.confirm(
            f"Are you sure you want to remove {package}?"
        ):
            return

        result = self.remove_plugin(config, auth_manager, client, package)

        if result.success:
            print_success(result.message)
        else:
            print_error(result.error or result.message)
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
                    and not any(
                        query_lower in kw.lower() for kw in plugin.keywords
                    )
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
        table.add_column("Version", style="green")
        table.add_column("Status", style="yellow")

        for result in search_results:
            status = "Installed" if result.installed else "Available"
            if result.installed and result.installed_version:
                status = f"Installed ({result.installed_version})"

            table.add_row(
                result.plugin.name,
                result.plugin.description,
                result.plugin.version,
                status,
            )

        console.print(table)

        # Show install hint
        available_count = sum(1 for r in search_results if not r.installed)
        if available_count > 0:
            print_info(
                "\nTo install a plugin, use: deepctl plugin install <name>"
            )

    def _get_plugin_registry(self) -> list[PluginRegistryEntry]:
        """Get the plugin registry.

        For now, this returns a hardcoded list. In the future, this will
        fetch from a .well-known URL or plugin registry service.

        Returns:
            List of available plugins
        """
        # TODO: Fetch from https://deepgram.com/.well-known/deepctl-plugins.json
        return [
            PluginRegistryEntry(
                name="deepctl-plugin-example",
                description="Example plugin demonstrating the plugin system",
                version="0.1.8",
                author="Deepgram DevRel",
                url="https://github.com/deepgram/deepctl",
                keywords=["example", "demo", "plugin"],
                install_name="deepctl-plugin-example",
            ),
            PluginRegistryEntry(
                name="deepctl-plugin-tts",
                description="Text-to-speech functionality for Deepgram",
                version="0.2.0",
                author="Deepgram",
                url="https://github.com/deepgram/deepctl-plugin-tts",
                keywords=["tts", "text-to-speech", "audio", "synthesis"],
                install_name="deepctl-plugin-tts",
            ),
            PluginRegistryEntry(
                name="deepctl-plugin-analyze",
                description="Advanced audio analysis tools",
                version="0.1.0",
                author="Deepgram",
                url="https://github.com/deepgram/deepctl-plugin-analyze",
                keywords=["analyze", "audio", "analysis", "metrics"],
                install_name="deepctl-plugin-analyze",
            ),
            PluginRegistryEntry(
                name="deepctl-plugin-batch",
                description="Batch processing utilities for large-scale transcription",
                version="0.3.1",
                author="Deepgram",
                url="https://github.com/deepgram/deepctl-plugin-batch",
                keywords=["batch", "bulk", "processing", "scale"],
                install_name="deepctl-plugin-batch",
            ),
            PluginRegistryEntry(
                name="deepctl-plugin-export",
                description="Export transcriptions to various formats (SRT, VTT, JSON, etc.)",
                version="0.2.5",
                author="Deepgram Community",
                url="https://github.com/deepgram-community/deepctl-plugin-export",
                keywords=["export", "srt", "vtt", "subtitle", "captions"],
                install_name="deepctl-plugin-export",
            ),
            PluginRegistryEntry(
                name="deepctl-plugin-realtime",
                description="Enhanced real-time transcription with WebSocket support",
                version="0.4.0",
                author="Deepgram",
                url="https://github.com/deepgram/deepctl-plugin-realtime",
                keywords=["realtime", "websocket", "streaming", "live"],
                install_name="deepctl-plugin-realtime",
            ),
            PluginRegistryEntry(
                name="deepctl-plugin-translate",
                description="Translation capabilities for transcribed text",
                version="0.1.2",
                author="Deepgram Labs",
                url="https://github.com/deepgram-labs/deepctl-plugin-translate",
                keywords=["translate", "translation", "language", "i18n"],
                install_name="deepctl-plugin-translate",
            ),
        ]

    def install_plugin(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        options: PluginInstallOptions,
    ) -> PluginOperationResult:
        """Install a plugin.

        Args:
            config: CLI configuration
            auth_manager: Authentication manager
            client: Deepgram client
            options: Installation options

        Returns:
            Operation result
        """
        # Detect installation method
        install_info = self.detector.detect()

        # Determine which Python executable to use
        if install_info.method in [
            InstallMethod.SYSTEM,
            InstallMethod.UNKNOWN,
        ]:
            # For system installations, use isolated plugin environment
            print_info(
                "System installation detected, using isolated plugin environment..."
            )
            success, python_exe = self._ensure_plugin_environment()
            if not success:
                return PluginOperationResult(
                    success=False,
                    action=PluginAction.INSTALL,
                    package=options.package,
                    message="Failed to create plugin environment",
                    error="Could not create isolated environment for plugins",
                )
            target_python = python_exe
            using_plugin_env = True
        else:
            # For other installations, use the same environment
            target_python = self._python_executable
            using_plugin_env = False

        # Build pip command
        cmd = [target_python, "-m", "pip", "install"]

        # Add options
        if options.upgrade:
            cmd.append("--upgrade")
        if options.pre:
            cmd.append("--pre")
        if options.force_reinstall:
            cmd.append("--force-reinstall")
        if options.index_url:
            cmd.extend(["--index-url", options.index_url])
        if options.extra_index_url:
            cmd.extend(["--extra-index-url", options.extra_index_url])
        if options.editable:
            cmd.append("--editable")

        # Add package
        if options.git_url:
            package_spec = options.git_url
        elif options.version:
            package_spec = f"{options.package}=={options.version}"
        else:
            package_spec = options.package

        cmd.append(package_spec)

        # Execute installation
        try:
            print_info(f"Installing {options.package}...")
            subprocess.run(cmd, capture_output=True, text=True, check=True)

            # Get installed version
            installed_version = self._get_package_version(
                options.package, target_python
            )

            # Update plugin state if using plugin environment
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

        except subprocess.CalledProcessError as e:
            return PluginOperationResult(
                success=False,
                action=PluginAction.INSTALL,
                package=options.package,
                message=f"Failed to install {options.package}",
                error=e.stderr or str(e),
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

        if not plugins:
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
            row = [
                plugin.name,
                plugin.version or "Unknown",
                plugin.entry_point or "N/A",
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

                row.extend([plugin_type, commands, env])

            table.add_row(*row)

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
        plugin_found = any(
            p.name == package for p in plugins if not p.is_builtin
        )

        if not plugin_found:
            return PluginOperationResult(
                success=False,
                action=PluginAction.REMOVE,
                package=package,
                message=f"Plugin {package} is not installed",
            )

        # Detect installation method
        install_info = self.detector.detect()

        # Determine which Python executable to use
        state = self._get_plugin_state()
        if package in state.get("plugins", {}):
            # Plugin is in isolated environment
            _, python_exe = self._ensure_plugin_environment()
            target_python = python_exe
            using_plugin_env = True
        elif install_info.method in [
            InstallMethod.SYSTEM,
            InstallMethod.UNKNOWN,
        ]:
            # System installation but plugin not in plugin env - shouldn't happen
            return PluginOperationResult(
                success=False,
                action=PluginAction.REMOVE,
                package=package,
                message="Cannot remove plugin from system installation",
                error="This plugin was not installed via deepctl",
            )
        else:
            # Use same environment
            target_python = self._python_executable
            using_plugin_env = False

        # Build pip command
        cmd = [target_python, "-m", "pip", "uninstall", "-y", package]

        # Execute removal
        try:
            print_info(f"Removing {package}...")
            subprocess.run(cmd, capture_output=True, text=True, check=True)

            # Update plugin state if using plugin environment
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

        except subprocess.CalledProcessError as e:
            return PluginOperationResult(
                success=False,
                action=PluginAction.REMOVE,
                package=package,
                message=f"Failed to remove {package}",
                error=e.stderr or str(e),
            )

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

        # Also discover from plugin environment if it exists
        if self._plugin_venv.exists():
            _, plugin_python = self._ensure_plugin_environment()
            plugin_env_plugins = self._discover_from_environment(plugin_python)

            # Add only plugins not already discovered
            for plugin in plugin_env_plugins:
                plugin_key = f"{plugin.name}:{plugin.entry_point}"
                if plugin_key not in discovered_names:
                    discovered_names.add(plugin_key)
                    plugins.append(plugin)

        return plugins

    def _discover_from_environment(
        self, python_exe: str
    ) -> list[PluginPackage]:
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
                                        is_builtin=dist.name.startswith(
                                            "deepctl-cmd-"
                                        ),
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
                            if "deepctl.commands" in eps:
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
                            if "deepctl.plugins" in eps:
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
