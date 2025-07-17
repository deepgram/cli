"""Login command for deepctl authentication."""

from typing import Any, List, Dict, Optional

import click
from rich.console import Console

from .base import BaseCommand
from ..core.config import Config
from ..core.auth import AuthManager, AuthenticationError
from ..core.client import DeepgramClient

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
    
    def get_arguments(self) -> List[Dict[str, Any]]:
        """Get command arguments and options."""
        return [
            {
                "names": ["--api-key", "-k"],
                "help": "Configure the CLI with your Deepgram API key",
                "type": str,
                "required": False,
                "is_option": True
            },
            {
                "names": ["--project-id", "-p"],
                "help": "Configure the CLI with your Deepgram project ID",
                "type": str,
                "required": False,
                "is_option": True
            },
            {
                "names": ["--force-write", "-f"],
                "help": "Don't prompt for confirmation when providing credentials",
                "is_flag": True,
                "is_option": True
            },
            {
                "names": ["--profile"],
                "help": "Profile name to use for storing credentials",
                "type": str,
                "required": False,
                "is_option": True
            }
        ]
    
    def handle(
        self, 
        config: Config, 
        auth_manager: AuthManager, 
        client: DeepgramClient, 
        **kwargs
    ) -> Any:
        """Handle login command."""
        api_key = kwargs.get("api_key")
        project_id = kwargs.get("project_id")
        force_write = kwargs.get("force_write", False)
        profile = kwargs.get("profile")
        
        # Set profile if provided
        if profile:
            config.profile = profile
        
        # Check if user is already logged in
        if auth_manager.is_authenticated() and not force_write:
            current_profile = config.get_profile()
            console.print(f"[yellow]Already logged in to profile:[/yellow] {config.profile or 'default'}")
            
            if not self.confirm("Do you want to login again?", default=False):
                return {"status": "cancelled", "message": "Login cancelled by user"}
        
        # Determine authentication method
        if api_key:
            return self._cli_auth(config, auth_manager, api_key, project_id, force_write)
        else:
            return self._web_auth(config, auth_manager, force_write)
    
    def _cli_auth(
        self, 
        config: Config, 
        auth_manager: AuthManager, 
        api_key: str, 
        project_id: Optional[str], 
        force_write: bool
    ) -> Dict[str, Any]:
        """Handle CLI authentication with API key."""
        console.print("[blue]Configuring CLI with API key...[/blue]")
        
        # Validate API key format
        if not api_key.startswith(("sk-", "pk-")):
            console.print("[yellow]Warning:[/yellow] API key format doesn't match expected pattern")
            if not force_write and not self.confirm("Continue anyway?", default=False):
                return {"status": "cancelled", "message": "Login cancelled by user"}
        
        # Validate project ID is provided with API key
        if not project_id:
            console.print("[yellow]Warning:[/yellow] Project ID not provided")
            console.print("You can set it later with: deepctl login --project-id <project_id>")
            console.print("Or use environment variable: DEEPGRAM_PROJECT_ID")
        
        # Check if config file exists and prompt for overwrite
        if not force_write:
            if config.config_path.exists():
                console.print(f"[yellow]Configuration file already exists:[/yellow] {config.config_path}")
                if not self.confirm("Overwrite existing configuration?", default=False):
                    return {"status": "cancelled", "message": "Login cancelled by user"}
            else:
                if not self.confirm("Do you want to write these credentials to config?", default=True):
                    return {"status": "cancelled", "message": "Login cancelled by user"}
        
        try:
            # Test API key validity
            console.print("[dim]Testing API key...[/dim]")
            
            # Create temporary client to test
            temp_client = DeepgramClient(config, auth_manager)
            temp_client._client = None  # Reset client to force recreation
            
            # Temporarily set API key for testing
            old_api_key = config.get_profile().api_key
            config.get_profile().api_key = api_key
            
            # Test the key
            if not temp_client.validate_api_key(api_key):
                # Restore old key
                config.get_profile().api_key = old_api_key
                console.print("[red]✗[/red] API key validation failed")
                return {"status": "error", "message": "Invalid API key"}
            
            # Restore old key (will be set properly below)
            config.get_profile().api_key = old_api_key
            
            console.print("[green]✓[/green] API key validated successfully")
            
            # Store credentials
            auth_manager.login_with_api_key(api_key, project_id, force_write)
            
            profile_name = config.profile or "default"
            return {
                "status": "success",
                "message": f"Successfully logged in with API key",
                "profile": profile_name,
                "api_key": f"****{api_key[-4:]}",
                "project_id": project_id,
                "config_path": str(config.config_path)
            }
            
        except AuthenticationError as e:
            console.print(f"[red]Authentication failed:[/red] {e}")
            return {"status": "error", "message": str(e)}
        
        except Exception as e:
            console.print(f"[red]Error during CLI authentication:[/red] {e}")
            return {"status": "error", "message": str(e)}
    
    def _web_auth(
        self, 
        config: Config, 
        auth_manager: AuthManager, 
        force_write: bool
    ) -> Dict[str, Any]:
        """Handle web authentication with device flow."""
        console.print("[blue]Starting web authentication...[/blue]")
        
        # Check if config file exists and prompt for overwrite
        if not force_write:
            if config.config_path.exists():
                console.print(f"[yellow]Configuration file already exists:[/yellow] {config.config_path}")
                if not self.confirm("Overwrite existing configuration?", default=False):
                    return {"status": "cancelled", "message": "Login cancelled by user"}
        
        try:
            # Start device flow
            auth_manager.login_with_device_flow()
            
            profile_name = config.profile or "default"
            return {
                "status": "success",
                "message": "Successfully logged in via web authentication",
                "profile": profile_name,
                "config_path": str(config.config_path)
            }
            
        except AuthenticationError as e:
            console.print(f"[red]Authentication failed:[/red] {e}")
            return {"status": "error", "message": str(e)}
        
        except KeyboardInterrupt:
            console.print("\n[yellow]Authentication cancelled by user[/yellow]")
            return {"status": "cancelled", "message": "Login cancelled by user"}
        
        except Exception as e:
            console.print(f"[red]Error during web authentication:[/red] {e}")
            return {"status": "error", "message": str(e)}


class LogoutCommand(BaseCommand):
    """Logout command for clearing authentication."""
    
    name = "logout"
    help = "Log out and clear stored credentials"
    short_help = "Log out of Deepgram"
    
    # Logout doesn't require existing auth
    requires_auth = False
    requires_project = False
    ci_friendly = True
    
    def get_arguments(self) -> List[Dict[str, Any]]:
        """Get command arguments and options."""
        return [
            {
                "names": ["--profile"],
                "help": "Profile to logout from (default: current profile)",
                "type": str,
                "required": False,
                "is_option": True
            },
            {
                "names": ["--all"],
                "help": "Logout from all profiles",
                "is_flag": True,
                "is_option": True
            }
        ]
    
    def handle(
        self, 
        config: Config, 
        auth_manager: AuthManager, 
        client: DeepgramClient, 
        **kwargs
    ) -> Any:
        """Handle logout command."""
        profile = kwargs.get("profile")
        logout_all = kwargs.get("all", False)
        
        try:
            if logout_all:
                # Logout from all profiles
                profiles = config.list_profiles()
                for profile_name in profiles:
                    config.profile = profile_name
                    auth_manager_for_profile = AuthManager(config)
                    auth_manager_for_profile.logout()
                
                console.print(f"[green]✓[/green] Successfully logged out from all profiles ({len(profiles)} profiles)")
                return {
                    "status": "success",
                    "message": f"Logged out from all profiles",
                    "profiles_count": len(profiles)
                }
            
            else:
                # Logout from specific profile
                if profile:
                    config.profile = profile
                
                if not auth_manager.is_authenticated():
                    console.print("[yellow]Not currently logged in[/yellow]")
                    return {"status": "info", "message": "Not currently logged in"}
                
                auth_manager.logout()
                
                profile_name = config.profile or "default"
                return {
                    "status": "success",
                    "message": f"Successfully logged out from profile: {profile_name}",
                    "profile": profile_name
                }
                
        except Exception as e:
            console.print(f"[red]Error during logout:[/red] {e}")
            return {"status": "error", "message": str(e)}


class ProfilesCommand(BaseCommand):
    """Command to manage authentication profiles."""
    
    name = "profiles"
    help = "Manage authentication profiles"
    short_help = "Manage profiles"
    
    # Profiles command doesn't require auth
    requires_auth = False
    requires_project = False
    ci_friendly = True
    
    def get_arguments(self) -> List[Dict[str, Any]]:
        """Get command arguments and options."""
        return [
            {
                "names": ["--list", "-l"],
                "help": "List all profiles",
                "is_flag": True,
                "is_option": True
            },
            {
                "names": ["--current"],
                "help": "Show current profile",
                "is_flag": True,
                "is_option": True
            },
            {
                "names": ["--switch"],
                "help": "Switch to a different profile",
                "type": str,
                "required": False,
                "is_option": True
            }
        ]
    
    def handle(
        self, 
        config: Config, 
        auth_manager: AuthManager, 
        client: DeepgramClient, 
        **kwargs
    ) -> Any:
        """Handle profiles command."""
        list_profiles = kwargs.get("list", False)
        show_current = kwargs.get("current", False)
        switch_profile = kwargs.get("switch")
        
        if list_profiles:
            profiles = auth_manager.list_profiles()
            
            if not profiles:
                console.print("[yellow]No profiles found[/yellow]")
                return {"status": "info", "message": "No profiles found"}
            
            console.print("[blue]Available profiles:[/blue]")
            for name, info in profiles.items():
                current_marker = " (current)" if name == (config.profile or "default") else ""
                console.print(f"  • {name}{current_marker}")
                console.print(f"    API Key: {info['api_key'] or 'Not set'}")
                console.print(f"    Project ID: {info['project_id'] or 'Not set'}")
                console.print(f"    Base URL: {info['base_url']}")
                console.print()
            
            return {"status": "success", "profiles": profiles}
        
        elif show_current:
            current_profile = config.profile or "default"
            profile_info = auth_manager.list_profiles().get(current_profile, {})
            
            console.print(f"[blue]Current profile:[/blue] {current_profile}")
            console.print(f"[dim]API Key:[/dim] {profile_info.get('api_key', 'Not set')}")
            console.print(f"[dim]Project ID:[/dim] {profile_info.get('project_id', 'Not set')}")
            console.print(f"[dim]Base URL:[/dim] {profile_info.get('base_url', 'Not set')}")
            
            return {
                "status": "success", 
                "current_profile": current_profile,
                "profile_info": profile_info
            }
        
        elif switch_profile:
            profiles = config.list_profiles()
            
            if switch_profile not in profiles:
                console.print(f"[red]Profile '{switch_profile}' not found[/red]")
                return {"status": "error", "message": f"Profile '{switch_profile}' not found"}
            
            # Update default profile in config
            config._config.default_profile = switch_profile
            config.save()
            
            console.print(f"[green]✓[/green] Switched to profile: {switch_profile}")
            return {
                "status": "success",
                "message": f"Switched to profile: {switch_profile}",
                "profile": switch_profile
            }
        
        else:
            # Default behavior - show current profile
            return self.handle(config, auth_manager, client, current=True) 