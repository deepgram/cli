"""Cross-platform authentication system for deepctl based on the Go CLI implementation."""

import json
import os
import time
import webbrowser
from typing import Optional, Dict, Any
from urllib.parse import urljoin

import httpx
import keyring
from pydantic import BaseModel
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from .config import Config
from ..models import ProfileInfo, ProfilesResult

console = Console()

# Constants from Go implementation
COMMUNITY_BASE_URL = "https://community-local.deepgram.com:3000"
DEVICE_FLOW_URL = f"{COMMUNITY_BASE_URL}/api/auth/cli/device"
LOGIN_URL = f"{COMMUNITY_BASE_URL}/auth/login/cli/"


class DeviceCodeResponse(BaseModel):
    """Response from device code request."""
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


class TokenResponse(BaseModel):
    """Response from token request."""
    access_token: str
    token_type: str
    expires_in: int
    scope: str


class AuthenticationError(Exception):
    """Authentication related errors."""
    pass


class AuthManager:
    """Cross-platform authentication manager."""

    def __init__(self, config: Config):
        """Initialize authentication manager.

        Args:
            config: Configuration manager instance
        """
        self.config = config
        self.client = httpx.Client(timeout=30.0)

    def is_authenticated(self) -> bool:
        """Check if user is authenticated."""
        # Check for API key in config
        current_profile = self.config.get_profile()
        if current_profile.api_key:
            return True

        # Check for API key in environment
        if os.getenv("DEEPGRAM_API_KEY"):
            return True

        return False

    def get_api_key(self) -> Optional[str]:
        """Get API key from config or environment."""
        # Environment variable takes precedence (CI-friendly)
        api_key = os.getenv("DEEPGRAM_API_KEY")
        if api_key:
            return api_key

        # Check current profile
        current_profile = self.config.get_profile()
        if current_profile.api_key:
            return current_profile.api_key

        return None

    def get_project_id(self) -> Optional[str]:
        """Get project ID from config or environment."""
        # Environment variable takes precedence (CI-friendly)
        project_id = os.getenv("DEEPGRAM_PROJECT_ID")
        if project_id:
            return project_id

        # Check current profile
        current_profile = self.config.get_profile()
        if current_profile.project_id:
            return current_profile.project_id

        return None

    def guard(self) -> None:
        """Guard function to ensure authentication (replicated from Go implementation)."""
        api_key = self.get_api_key()

        if not api_key:
            console.print(
                "[red]Error:[/red] DEEPGRAM_API_KEY is not set in the configuration file "
                f"({self.config.config_path}) or environment variable.\n"
            )
            console.print(
                "[yellow]Run[/yellow] [bold]deepctl login[/bold] [yellow]to configure the CLI with your Deepgram account.[/yellow]\n"
            )
            raise AuthenticationError("DEEPGRAM_API_KEY is not set")

    def login_with_api_key(self, api_key: str, project_id: str, force_write: bool = False) -> None:
        """Login with API key directly (CI-friendly method).

        Args:
            api_key: Deepgram API key
            project_id: Deepgram project ID
            force_write: Skip confirmation prompts
        """
        # Validate API key format (basic check)
        if not api_key.startswith(("sk-", "pk-")):
            console.print(
                "[red]Warning:[/red] API key format doesn't match expected pattern")

        # Store in keyring for security (with fallback)
        try:
            keyring.set_password("deepgram", "api_key", api_key)
            if project_id:
                keyring.set_password("deepgram", "project_id", project_id)
            console.print(
                "[green]✓[/green] Credentials stored securely in system keyring")
        except Exception as e:
            console.print(
                f"[yellow]Warning:[/yellow] Could not store in keyring: {e}")
            console.print("Falling back to config file storage")

        # Update config
        profile_name = self.config.profile or "default"
        self.config.create_profile(
            profile_name,
            api_key=api_key,
            project_id=project_id
        )

        console.print(f"[green]✓[/green] Successfully logged in with API key")
        console.print(f"[dim]Profile:[/dim] {profile_name}")

        if project_id:
            console.print(f"[dim]Project ID:[/dim] {project_id}")

    def login_with_device_flow(self) -> None:
        """Login using device flow (interactive method)."""
        console.print("[blue]Starting device flow authentication...[/blue]")

        # Check if already authenticated
        if self.is_authenticated():
            console.print("[yellow]You're already logged in.[/yellow]")
            if not console.input("Do you want to login again? [y/N]: ").lower().startswith('y'):
                return

        try:
            # Request device code
            device_response = self._request_device_code()

            # Display user code and open browser
            console.print(
                f"\n[bold]User Code:[/bold] {device_response.user_code}")
            console.print(
                f"[dim]Verification URL:[/dim] {device_response.verification_uri}")
            console.print(
                f"[dim]Expires in:[/dim] {device_response.expires_in} seconds")

            # Open browser automatically
            try:
                webbrowser.open(device_response.verification_uri)
                console.print(
                    "[green]✓[/green] Opened browser for authentication")
            except Exception as e:
                console.print(
                    f"[yellow]Warning:[/yellow] Could not open browser: {e}")
                console.print(
                    "Please manually navigate to the verification URL")

            # Poll for token
            token_response = self._poll_for_token(device_response)

            # Store token and get user info
            self._store_token(token_response)

            console.print(
                "[green]✓[/green] Successfully logged in via device flow")

        except Exception as e:
            console.print(f"[red]Error during device flow:[/red] {e}")
            raise AuthenticationError(f"Device flow failed: {e}")

    def _request_device_code(self) -> DeviceCodeResponse:
        """Request device code from community site."""
        # Get process and hostname info (like Go implementation)
        ppid = os.getpid()  # Use current process ID
        hostname = os.uname().nodename if hasattr(os, 'uname') else 'unknown'

        payload = {
            "id": ppid,
            "hostname": hostname,
            "scopes": ["usage:write"]  # Same scopes as Go implementation
        }

        try:
            response = self.client.post(
                f"{COMMUNITY_BASE_URL}/api/auth/device/code",
                json=payload,
                headers={"Content-Type": "application/json"}
            )

            if response.status_code == 201:
                return DeviceCodeResponse(**response.json())
            else:
                raise AuthenticationError(
                    f"Device code request failed: {response.status_code}")

        except httpx.RequestError as e:
            raise AuthenticationError(
                f"Network error during device code request: {e}")

    def _poll_for_token(self, device_response: DeviceCodeResponse) -> TokenResponse:
        """Poll for token using device code."""
        console.print("\n[blue]Waiting for authentication...[/blue]")

        start_time = time.time()

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True
        ) as progress:
            task = progress.add_task(
                "Waiting for authentication...", total=None)

            while time.time() - start_time < device_response.expires_in:
                try:
                    response = self.client.post(
                        f"{COMMUNITY_BASE_URL}/api/auth/device/token",
                        json={"device_code": device_response.device_code},
                        headers={"Content-Type": "application/json"}
                    )

                    if response.status_code == 200:
                        return TokenResponse(**response.json())
                    elif response.status_code == 202:
                        # Still pending
                        time.sleep(device_response.interval)
                        continue
                    else:
                        raise AuthenticationError(
                            f"Token request failed: {response.status_code}")

                except httpx.RequestError as e:
                    console.print(f"[red]Network error:[/red] {e}")
                    time.sleep(device_response.interval)
                    continue

        raise AuthenticationError("Authentication timed out")

    def _store_token(self, token_response: TokenResponse) -> None:
        """Store authentication token."""
        # For now, we'll need to extract the API key from the token
        # This would typically involve making another API call to get user info
        # For the prototype, we'll use a placeholder
        api_key = "sk-placeholder-from-device-flow"

        try:
            keyring.set_password("deepgram", "access_token",
                                 token_response.access_token)
            keyring.set_password("deepgram", "api_key", api_key)
            console.print(
                "[green]✓[/green] Token stored securely in system keyring")
        except Exception as e:
            console.print(
                f"[yellow]Warning:[/yellow] Could not store in keyring: {e}")

        # Update config
        profile_name = self.config.profile or "default"
        self.config.create_profile(
            profile_name,
            api_key=api_key
        )

    def logout(self) -> None:
        """Logout user and clear credentials."""
        # Clear keyring
        try:
            keyring.delete_password("deepgram", "api_key")
            keyring.delete_password("deepgram", "project_id")
            keyring.delete_password("deepgram", "access_token")
        except Exception:
            pass  # Ignore errors if not stored

        # Clear config
        profile_name = self.config.profile or "default"
        if profile_name in self.config.list_profiles():
            self.config.delete_profile(profile_name)

        console.print("[green]✓[/green] Successfully logged out")

    def list_profiles(self) -> ProfilesResult:
        """Return all profiles wrapped in ProfilesResult model."""
        profiles: Dict[str, ProfileInfo] = {}

        for profile_name in self.config.list_profiles():
            profile = self.config.get_profile(profile_name)
            masked_key = None
            if profile.api_key:
                masked_key = "****" + profile.api_key[-4:]

            profiles[profile_name] = ProfileInfo(
                api_key=masked_key,
                project_id=profile.project_id,
                base_url=profile.base_url,
            )

        return ProfilesResult(
            profiles=profiles,
            current_profile=self.config.profile or self.config._config.default_profile,
        )

    def __del__(self):
        """Cleanup HTTP client."""
        if hasattr(self, 'client'):
            self.client.close()
