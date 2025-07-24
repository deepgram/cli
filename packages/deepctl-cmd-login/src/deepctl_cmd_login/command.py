"""Login command for deepctl."""

from typing import Any

from deepctl_core import (
    AuthenticationError,
    AuthManager,
    BaseCommand,
    Config,
    DeepgramClient,
    ProfileInfo,
    ProfilesResult,
)
from rich.console import Console
from rich.prompt import Prompt

from .models import LoginResult, LogoutResult

console = Console()


class LoginCommand(BaseCommand):
    """Login command for authenticating with Deepgram."""

    name = "login"
    help = "Log in to Deepgram with browser-based authentication or API key"
    short_help = "Log in to Deepgram"

    # Login doesn't require existing auth
    requires_auth = False
    requires_project = False
    ci_friendly = True

    def get_arguments(self) -> list[dict[str, Any]]:
        """Get command arguments and options."""
        return [
            {
                "names": ["--api-key", "-k"],
                "help": "Configure the CLI with your Deepgram API key",
                "type": str,
                "required": False,
                "is_option": True,
            },
            {
                "names": ["--project-id", "-p"],
                "help": "Configure the CLI with your Deepgram project ID",
                "type": str,
                "required": False,
                "is_option": True,
            },
            {
                "names": ["--force-write", "-f"],
                "help": (
                    "Don't prompt for confirmation when providing "
                    "credentials"
                ),
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--profile"],
                "help": "Profile name to use for storing credentials",
                "type": str,
                "required": False,
                "is_option": True,
            },
        ]

    def handle(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> Any:
        """Handle login command."""
        api_key = kwargs.get("api_key")
        project_id = kwargs.get("project_id")
        force_write = kwargs.get("force_write", False)
        profile = kwargs.get("profile")

        # Set profile if provided
        if profile:
            config.profile = profile

        current_profile = config.profile or "default"

        # Check for environment variables
        has_env_key, has_env_project = auth_manager.has_env_credentials()

        if has_env_key and not force_write:
            console.print(
                "[yellow]Environment variable DEEPGRAM_API_KEY is set.[/yellow]"
            )
            if has_env_project:
                console.print(
                    "[yellow]Environment variable DEEPGRAM_PROJECT_ID is also set.[/yellow]\n"
                    "[green]Commands will work using these environment variables.[/green]"
                )
            else:
                console.print(
                    "[yellow]Note: DEEPGRAM_PROJECT_ID is not set. "
                    "You'll need to set it or provide it via --project-id flag.[/yellow]"
                )

            console.print(
                "\n[dim]Logging in will create a profile that takes precedence "
                "over environment variables.[/dim]"
            )

            if not self.confirm(
                "Do you still want to login with a profile?", default=False
            ):
                return LoginResult(
                    status="cancelled",
                    message="Login cancelled - using environment variables",
                    profile=current_profile,
                    api_key_masked=None,
                )

        # Check if already logged into this profile
        has_profile_key, has_profile_project = (
            auth_manager.has_profile_credentials(current_profile)
        )

        if has_profile_key and not force_write:
            console.print(
                f"[yellow]Already logged in to profile:[/yellow] {current_profile}"
            )

            # Ask if they want to re-login to this profile
            if self.confirm(
                f"Do you want to re-login to profile '{current_profile}'?",
                default=False,
            ):
                # Continue with login
                pass
            else:
                # Ask if they want to login with another profile
                if self.confirm(
                    "Do you want to login with another profile?", default=False
                ):
                    # Prompt for new profile name
                    new_profile = Prompt.ask(
                        "Enter profile name", default=f"{current_profile}-2"
                    )
                    config.profile = new_profile
                    current_profile = new_profile
                else:
                    return LoginResult(
                        status="cancelled",
                        message="Login cancelled by user",
                        profile=current_profile,
                        api_key_masked=None,
                    )

        # Determine authentication method
        if api_key:
            return self._cli_auth(
                config, auth_manager, str(api_key), project_id, force_write
            )
        else:
            return self._web_auth(config, auth_manager, force_write)

    def _cli_auth(
        self,
        config: Config,
        auth_manager: AuthManager,
        api_key: str,
        project_id: str | None,
        force_write: bool,
    ) -> LoginResult:
        """Handle CLI authentication with API key."""
        console.print("[blue]Configuring CLI with API key...[/blue]")

        # Validate API key format
        if not api_key.startswith(("sk-", "pk-")):
            console.print(
                "[yellow]Warning:[/yellow] API key format doesn't match "
                "expected pattern"
            )
            if not force_write and not self.confirm(
                "Continue anyway?", default=False
            ):
                return LoginResult(
                    status="cancelled",
                    message="Login cancelled by user",
                    profile=config.profile or "default",
                    api_key_masked=None,
                )

        # Validate project ID is provided with API key
        if not project_id:
            console.print("[yellow]Warning:[/yellow] Project ID not provided")
            console.print(
                "You can set it later with: "
                "deepctl login --project-id <project_id>"
            )
            console.print("Or use environment variable: DEEPGRAM_PROJECT_ID")

        # No need to check for existing config - we handle that in the main flow

        try:
            # Store credentials (verification happens inside
            # login_with_api_key)
            auth_manager.login_with_api_key(
                api_key, project_id or "", force_write
            )

            profile_name = config.profile or "default"

            # Update the active profile in config
            config._config.active_profile = profile_name
            config.save()

            return LoginResult(
                status="success",
                message="Successfully logged in with API key",
                profile=profile_name,
                api_key_masked=f"****{api_key[-4:]}",
                project_id=project_id,
                config_path=str(config.config_path),
            )

        except AuthenticationError as e:
            console.print(f"[red]Authentication failed:[/red] {e}")
            return LoginResult(
                status="error",
                message=str(e),
                profile=config.profile or "default",
                api_key_masked=None,
            )

        except Exception as e:
            console.print(f"[red]Error during CLI authentication:[/red] {e}")
            return LoginResult(
                status="error",
                message=str(e),
                profile=config.profile or "default",
                api_key_masked=None,
            )

    def _web_auth(
        self, config: Config, auth_manager: AuthManager, force_write: bool
    ) -> LoginResult:
        """Handle web authentication with device flow."""
        console.print("[blue]Starting web authentication...[/blue]")

        # No need to check for existing config - we handle that in the main flow

        try:
            # Start device flow
            auth_manager.login_with_device_flow()

            profile_name = config.profile or "default"

            # Update the active profile in config
            config._config.active_profile = profile_name
            config.save()

            # Retrieve the stored credentials
            api_key = auth_manager.get_api_key()
            project_id = auth_manager.get_project_id()

            # Mask the API key for display
            api_key_masked = None
            if api_key:
                api_key_masked = f"****{api_key[-4:]}"

            return LoginResult(
                status="success",
                message="Successfully logged in via web authentication",
                profile=profile_name,
                api_key_masked=api_key_masked,
                project_id=project_id,
                config_path=str(config.config_path),
            )

        except AuthenticationError as e:
            console.print(f"[red]Authentication failed:[/red] {e}")
            return LoginResult(
                status="error",
                message=str(e),
                profile=config.profile or "default",
                api_key_masked=None,
            )

        except KeyboardInterrupt:
            console.print(
                "\n[yellow]Authentication cancelled by user[/yellow]"
            )
            return LoginResult(
                status="cancelled",
                message="Login cancelled by user",
                profile=config.profile or "default",
                api_key_masked=None,
            )

        except Exception as e:
            console.print(f"[red]Error during web authentication:[/red] {e}")
            return LoginResult(
                status="error",
                message=str(e),
                profile=config.profile or "default",
                api_key_masked=None,
            )


class LogoutCommand(BaseCommand):
    """Logout command for clearing authentication."""

    name = "logout"
    help = "Log out and clear stored credentials"
    short_help = "Log out of Deepgram"

    # Logout doesn't require existing auth
    requires_auth = False
    requires_project = False
    ci_friendly = True

    def get_arguments(self) -> list[dict[str, Any]]:
        """Get command arguments and options."""
        return [
            {
                "names": ["--profile"],
                "help": "Profile to logout from (default: current profile)",
                "type": str,
                "required": False,
                "is_option": True,
            },
            {
                "names": ["--all"],
                "help": "Logout from all profiles",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--keep-config"],
                "help": "Keep profile configuration (project ID, base URL) and only clear credentials",
                "is_flag": True,
                "is_option": True,
            },
        ]

    def handle(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> Any:
        """Handle logout command."""
        profile = kwargs.get("profile")
        logout_all = kwargs.get("all", False)
        keep_config = kwargs.get("keep_config", False)

        try:
            if logout_all:
                # Logout from all profiles
                profiles = config.list_profiles()
                for profile_name in profiles:
                    config.profile = profile_name
                    auth_manager_for_profile = AuthManager(config)
                    auth_manager_for_profile.logout(keep_config=keep_config)

                # Clear active profile since we logged out from all
                config._config.active_profile = None
                config.save()

                console.print(
                    f"[green]✓[/green] Successfully logged out from all "
                    f"profiles ({len(profiles)} profiles)"
                )
                return LogoutResult(
                    status="success",
                    message="Logged out from all profiles",
                    profiles_count=len(profiles),
                )

            else:
                # Logout from specific profile
                if profile:
                    config.profile = profile

                profile_name = config.profile or "default"

                # Check if profile exists (not just if authenticated)
                if profile_name not in config.list_profiles():
                    console.print(
                        f"[yellow]Profile '{profile_name}' does not exist[/yellow]"
                    )
                    return LogoutResult(
                        status="info",
                        message=f"Profile '{profile_name}' does not exist",
                    )

                auth_manager.logout(keep_config=keep_config)

                # Clear active profile if we logged out from the current active profile
                if config._config.active_profile == profile_name:
                    config._config.active_profile = None
                    config.save()

                return LogoutResult(
                    status="success",
                    message=(
                        f"Successfully logged out from profile: {profile_name}"
                    ),
                    profile=profile_name,
                )

        except Exception as e:
            console.print(f"[red]Error during logout:[/red] {e}")
            return LogoutResult(status="error", message=str(e))


class ProfilesCommand(BaseCommand):
    """Command to manage authentication profiles."""

    name = "profiles"
    help = "Manage authentication profiles"
    short_help = "Manage profiles"

    # Profiles command doesn't require auth
    requires_auth = False
    requires_project = False
    ci_friendly = True

    def get_arguments(self) -> list[dict[str, Any]]:
        """Get command arguments and options."""
        return [
            {
                "names": ["--list", "-l"],
                "help": "List all profiles",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--current"],
                "help": "Show current profile",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--switch"],
                "help": "Switch to a different profile",
                "type": str,
                "required": False,
                "is_option": True,
            },
        ]

    def handle(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> Any:
        """Handle profiles command."""
        list_profiles = kwargs.get("list", False)
        show_current = kwargs.get("current", False)
        switch_profile = kwargs.get("switch")

        if list_profiles:
            profiles_result = auth_manager.list_profiles()
            profiles = profiles_result.profiles
            if not profiles:
                console.print("[yellow]No profiles found[/yellow]")
                return ProfilesResult(
                    status="info", message="No profiles found", profiles={}
                )

            console.print("[blue]Available profiles:[/blue]")
            for name, info in profiles.items():
                current_marker = (
                    " (current)"
                    if name == (config.profile or "default")
                    else ""
                )
                console.print(f"  • {name}{current_marker}")
                console.print(f"    API Key: {info.api_key or 'Not set'}")
                console.print(
                    f"    Project ID: {info.project_id or 'Not set'}"
                )
                console.print(f"    Base URL: {info.base_url}")
                console.print()
            return profiles_result

        elif show_current:
            current_profile = config.profile or "default"
            profile_info = auth_manager.list_profiles().profiles.get(
                current_profile,
                ProfileInfo(api_key=None, project_id=None, base_url=""),
            )

            console.print(f"[blue]Current profile:[/blue] {current_profile}")
            console.print(
                f"[dim]API Key:[/dim] {profile_info.api_key or 'Not set'}"
            )
            console.print(
                f"[dim]Project ID:[/dim] "
                f"{profile_info.project_id or 'Not set'}"
            )
            console.print(
                f"[dim]Base URL:[/dim] {profile_info.base_url or 'Not set'}"
            )

            return ProfilesResult(
                status="success",
                current_profile=current_profile,
                profiles={current_profile: profile_info},
            )

        elif switch_profile:
            profile_names = config.list_profiles()

            if switch_profile not in profile_names:
                console.print(
                    f"[red]Profile '{switch_profile}' not found[/red]"
                )
                return ProfilesResult(
                    status="error",
                    message=f"Profile '{switch_profile}' not found",
                )

            # Require re-authentication when switching profiles
            console.print(
                f"[blue]Switching to profile:[/blue] {switch_profile}"
            )
            console.print(
                "[dim]Re-authentication required to confirm access to credentials[/dim]"
            )

            # Try to get API key from keyring to verify access
            try:
                import keyring
                from deepctl_core.auth import KEYRING_SERVICE

                api_key = keyring.get_password(
                    KEYRING_SERVICE, f"api-key.{switch_profile}"
                )

                if not api_key:
                    # Check if API key is in config instead
                    profile_config = config.get_profile(switch_profile)
                    if not profile_config.api_key:
                        console.print(
                            f"[red]No credentials found for profile '{switch_profile}'[/red]"
                        )
                        console.print(
                            "[yellow]Please login to this profile first:[/yellow] "
                            f"deepctl login --profile {switch_profile}"
                        )
                        return ProfilesResult(
                            status="error",
                            message=f"No credentials found for profile '{switch_profile}'",
                        )

                # Update active profile in config
                config._config.active_profile = switch_profile
                config.save()

                console.print(
                    f"[green]✓[/green] Switched to profile: {switch_profile}"
                )

                # Log the project ID being used
                project_id = config.get_profile(switch_profile).project_id
                if project_id:
                    console.print(f"[dim]Using project ID:[/dim] {project_id}")

                return ProfilesResult(
                    status="success",
                    message=f"Switched to profile: {switch_profile}",
                    current_profile=switch_profile,
                )

            except Exception as e:
                console.print(f"[red]Error accessing credentials:[/red] {e}")
                return ProfilesResult(
                    status="error",
                    message=f"Could not access credentials: {e}",
                )

        else:
            # Default behavior - show current profile
            return self.handle(config, auth_manager, client, current=True)
