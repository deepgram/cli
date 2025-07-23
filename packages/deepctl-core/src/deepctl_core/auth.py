"""Cross-platform authentication system for deepctl based on the Go CLI
implementation."""

import os
import random
import string
import time
import webbrowser
from urllib.parse import urlencode

import httpx
import keyring
from pydantic import BaseModel
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from .config import Config
from .models import ProfileInfo, ProfilesResult

console = Console()

# Constants from Go implementation
COMMUNITY_BASE_URL = os.getenv(
    "DEEPGRAM_CLI_BASE_URL", "https://community.deepgram.com"
)
DEVICE_CODE_URL = f"{COMMUNITY_BASE_URL}/api/auth/device/code"
TOKEN_POLL_URL = f"{COMMUNITY_BASE_URL}/api/auth/device/token"

# Keyring service identifier using reverse domain notation
KEYRING_SERVICE = "com.deepgram.dx.deepctl"


class DeviceCodeResponse(BaseModel):
    """Response from device code request."""

    device_code: str
    user_code: str | None = None  # Not used in current implementation
    verification_uri: str
    expires_in: int
    interval: int


class TokenResponse(BaseModel):
    """Response from token request."""

    access_token: str
    project_id: str
    token_type: str | None = None
    expires_in: int | None = None
    scope: str | None = None

    # The access_token returned is the actual Deepgram API key
    @property
    def api_key(self) -> str:
        """Get the API key from the response."""
        # The community server returns the actual API key in the
        # access_token field
        return self.access_token


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
        # Disable SSL verification for local development
        verify = not COMMUNITY_BASE_URL.startswith("https://community-local")
        self.client = httpx.Client(timeout=30.0, verify=verify)

    def is_authenticated(self) -> bool:
        """Check if user is authenticated."""
        # Check for API key in environment
        if os.getenv("DEEPGRAM_API_KEY"):
            return True

        # Check keyring
        try:
            profile_name = self.config.profile or "default"
            api_key = keyring.get_password(
                KEYRING_SERVICE, f"api-key.{profile_name}"
            )
            if api_key:
                return True
        except Exception:
            pass

        # Check for API key in config (backward compatibility)
        current_profile = self.config.get_profile()
        return bool(current_profile.api_key)

    def is_ci_mode(self) -> bool:
        """Check if running in CI mode (credentials from environment)."""
        # If both API key and project ID are provided via environment,
        # we're in CI mode
        return bool(
            os.getenv("DEEPGRAM_API_KEY") and os.getenv("DEEPGRAM_PROJECT_ID")
        )

    def get_api_key(self) -> str | None:
        """Get API key from keyring, then environment, then config."""
        # Environment variable takes precedence (CI-friendly)
        api_key = os.getenv("DEEPGRAM_API_KEY")
        if api_key:
            return api_key

        # Check keyring next
        try:
            api_key = keyring.get_password(
                KEYRING_SERVICE, f"api-key.{self.config.profile or 'default'}"
            )
            if api_key:
                return api_key
        except Exception:
            pass  # Keyring not available or error

        # Fall back to config file (for backward compatibility)
        current_profile = self.config.get_profile()
        if current_profile.api_key:
            return current_profile.api_key

        return None

    def get_project_id(self) -> str | None:
        """Get project ID from environment or config."""
        # Environment variable takes precedence (CI-friendly)
        project_id = os.getenv("DEEPGRAM_PROJECT_ID")
        if project_id:
            return project_id

        # Check keyring for project ID (if stored there)
        try:
            project_id = keyring.get_password(
                KEYRING_SERVICE,
                f"project-id.{self.config.profile or 'default'}",
            )
            if project_id:
                return project_id
        except Exception:
            pass

        # Check current profile config
        current_profile = self.config.get_profile()
        if current_profile.project_id:
            return current_profile.project_id

        return None

    def verify_credentials(
        self, api_key: str | None = None, project_id: str | None = None
    ) -> tuple[bool, str, str | None]:
        """Verify API key and project ID by making a request to the
        Deepgram API.

        Args:
            api_key: API key to verify (uses stored key if not provided)
            project_id: Project ID to verify (uses stored ID if not provided)

        Returns:
            Tuple of (success, message, error_type)
            - success: True if credentials are valid
            - message: Human-readable message about the result
            - error_type: 'auth' for API key issues, 'project' for project ID
              issues, None if successful
        """
        # Use provided credentials or get from storage
        if not api_key:
            api_key = self.get_api_key()
        if not project_id:
            project_id = self.get_project_id()

        # Check if we have required credentials
        if not api_key:
            return False, "No API key provided or stored", "auth"

        if not project_id:
            return False, "No project ID provided or stored", "project"

        # Make API request to verify credentials
        try:
            headers = {
                "Authorization": f"Token {api_key}",
                "Content-Type": "application/json",
            }

            # Make request to get project details
            response = self.client.get(
                f"https://api.deepgram.com/v1/projects/{project_id}",
                headers=headers,
            )

            if response.status_code == 200:
                return True, "Credentials verified successfully", None
            elif response.status_code == 401:
                return False, "Invalid API key - authentication failed", "auth"
            elif response.status_code == 403:
                return (
                    False,
                    "API key is valid but lacks permission for this project",
                    "auth",
                )
            elif response.status_code == 404:
                return False, f"Project ID '{project_id}' not found", "project"
            else:
                return (
                    False,
                    f"Unexpected error: HTTP {response.status_code}",
                    "unknown",
                )

        except httpx.RequestError as e:
            return False, f"Network error during verification: {e}", "network"
        except Exception as e:
            return (
                False,
                f"Unexpected error during verification: {e}",
                "unknown",
            )

    def guard(self) -> None:
        """Guard function to ensure authentication (replicated from Go
        implementation)."""
        api_key = self.get_api_key()

        if not api_key:
            console.print(
                "[red]Error:[/red] DEEPGRAM_API_KEY is not set in the "
                "configuration file "
                f"({self.config.config_path}) or environment variable.\n"
            )
            console.print(
                "[yellow]Run[/yellow] [bold]deepctl login[/bold] "
                "[yellow]to configure the CLI with your Deepgram "
                "account.[/yellow]\n"
            )
            raise AuthenticationError("DEEPGRAM_API_KEY is not set")

        # Verify credentials before proceeding
        success, message, error_type = self.verify_credentials()
        if not success:
            console.print(f"[red]Error:[/red] {message}")

            if error_type == "auth":
                console.print(
                    "[yellow]Your API key may have expired or been "
                    "revoked.[/yellow]\n"
                    "[yellow]Run[/yellow] [bold]deepctl login[/bold] "
                    "[yellow]to re-authenticate.[/yellow]"
                )
                raise AuthenticationError(message)
            elif error_type == "project":
                console.print(
                    "[yellow]The project may have been deleted or you may "
                    "need to specify a different project.[/yellow]\n"
                    "[yellow]Run[/yellow] [bold]deepctl login --project-id "
                    "<project_id>[/bold] [yellow]to set a valid project "
                    "ID.[/yellow]"
                )
                raise AuthenticationError(message)
            else:
                raise AuthenticationError(message)

    def login_with_api_key(
        self, api_key: str, project_id: str, _force_write: bool = False
    ) -> None:
        """Login with API key directly (CI-friendly method).

        Args:
            api_key: Deepgram API key
            project_id: Deepgram project ID
            force_write: Skip confirmation prompts
        """
        # Validate API key format (basic check)
        if not api_key.startswith(("sk-", "pk-")):
            console.print(
                "[red]Warning:[/red] API key format doesn't match expected "
                "pattern"
            )

        # Verify credentials before storing
        console.print("[dim]Verifying credentials...[/dim]")
        success, message, error_type = self.verify_credentials(
            api_key, project_id
        )

        if not success:
            console.print(f"[red]Error:[/red] {message}")
            if error_type == "auth":
                raise AuthenticationError(
                    f"API key verification failed: {message}"
                )
            elif error_type == "project":
                raise AuthenticationError(
                    f"Project ID verification failed: {message}"
                )
            else:
                raise AuthenticationError(
                    f"Credential verification failed: {message}"
                )

        console.print(f"[green]✓[/green] {message}")

        # Store API key in keyring for security
        profile_name = self.config.profile or "default"
        keyring_available = False

        try:
            keyring.set_password(
                KEYRING_SERVICE, f"api-key.{profile_name}", api_key
            )
            if project_id:
                keyring.set_password(
                    KEYRING_SERVICE, f"project-id.{profile_name}", project_id
                )
            console.print(
                "[green]✓[/green] Credentials stored securely in system "
                "keyring"
            )
            keyring_available = True
        except Exception as e:
            console.print(
                f"[yellow]Warning:[/yellow] Could not store in keyring: {e}"
            )
            console.print("Credentials will be stored in config file instead")

        # Update config with non-sensitive data
        # Only store API key in config if keyring is not available
        self.config.create_profile(
            profile_name,
            api_key=api_key if not keyring_available else None,
            project_id=project_id,
            base_url=self.config.get_profile(profile_name).base_url,
        )

        console.print("[green]✓[/green] Successfully logged in with API key")
        console.print(f"[dim]Profile:[/dim] {profile_name}")

        if project_id:
            console.print(f"[dim]Project ID:[/dim] {project_id}")

    def _generate_client_id(self, length: int = 40) -> str:
        """Generate a random client ID for device flow.

        Args:
            length: Length of the client ID

        Returns:
            Random URL-safe string
        """
        # URL-friendly characters matching Go implementation
        url_friendly_chars = string.ascii_letters + string.digits + "-._~"
        return "".join(
            random.choice(url_friendly_chars) for _ in range(length)
        )

    def login_with_device_flow(self) -> None:
        """Login using device flow (interactive method)."""
        console.print("[blue]Starting device flow authentication...[/blue]")

        # Check if already authenticated
        if self.is_authenticated():
            console.print("[yellow]You're already logged in.[/yellow]")
            if (
                not console.input("Do you want to login again? [y/N]: ")
                .lower()
                .startswith("y")
            ):
                return

        try:
            # Get hostname for device identification
            hostname = (
                os.uname().nodename if hasattr(os, "uname") else "unknown"
            )

            # Request device code (returns device code response and client_id)
            device_response, client_id = self._request_device_code()

            # Build verification URI with query parameters like Go
            # implementation
            query_params = {
                "device_code": device_response.device_code,
                "client_id": client_id,
                "hostname": hostname,
            }
            verification_uri = (
                f"{device_response.verification_uri}?{urlencode(query_params)}"
            )

            # Display prompt message like Go implementation
            console.print(
                "\n[bold]Hello from Deepgram![/bold] Press Enter to open "
                "browser and login automatically."
            )
            console.print(
                f"[dim]Here is your login link in case browser did not "
                f"open:[/dim] [dim]{verification_uri}[/dim]\n"
            )

            # Wait for Enter key
            console.input()

            # Open browser
            try:
                webbrowser.open(verification_uri)
                console.print(
                    "[green]✓[/green] Opened browser for authentication"
                )
            except Exception as e:
                console.print(
                    f"[yellow]Warning:[/yellow] Could not open browser: {e}"
                )
                console.print(
                    "Please manually navigate to the verification URL above"
                )

            # Poll for token
            token_response = self._poll_for_token(
                device_response, client_id, hostname
            )

            # Store token and get user info
            self._store_token(token_response)

            console.print(
                "\n[green]Key created and stored successfully.[/green]"
            )
            console.print("\nYou are now logged in. Happy coding!")

        except Exception as e:
            console.print(f"[red]Error during device flow:[/red] {e}")
            raise AuthenticationError(f"Device flow failed: {e}")

    def _request_device_code(self) -> tuple[DeviceCodeResponse, str]:
        """Request device code from community site.

        Returns:
            Tuple of (DeviceCodeResponse, client_id)
        """
        # Get hostname info (like Go implementation)
        hostname = os.uname().nodename if hasattr(os, "uname") else "unknown"

        # Generate random client ID like Go implementation
        client_id = self._generate_client_id(40)

        payload = {
            "client_id": client_id,
            "hostname": hostname,
            # Full scopes needed for CLI
            "scopes": ["admin"],
        }

        try:
            response = self.client.post(
                DEVICE_CODE_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
            )

            if response.status_code == 201:
                return DeviceCodeResponse(**response.json()), client_id
            else:
                raise AuthenticationError(
                    f"Device code request failed: {response.status_code}"
                )

        except httpx.RequestError as e:
            raise AuthenticationError(
                f"Network error during device code request: {e}"
            )

    def _poll_for_token(
        self,
        device_response: DeviceCodeResponse,
        client_id: str,
        hostname: str,
    ) -> TokenResponse:
        """Poll for token using device code."""
        console.print("\n[blue]Waiting for authentication...[/blue]")

        start_time = time.time()

        # Build query parameters like Go implementation
        query_params = {
            "device_code": device_response.device_code,
            "client_id": client_id,
            "hostname": hostname,
        }

        poll_url = f"{TOKEN_POLL_URL}?{urlencode(query_params)}"

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            progress.add_task("Waiting for authentication...", total=None)

            while time.time() - start_time < device_response.expires_in:
                try:
                    # Use GET request like Go implementation
                    response = self.client.get(poll_url)

                    if response.status_code == 201:
                        response_data = response.json()
                        return TokenResponse(**response_data)
                    elif response.status_code == 404:
                        # Still pending - this is the expected status code from
                        # Go implementation
                        time.sleep(device_response.interval)
                        continue
                    else:
                        error_data = response.json()
                        raise AuthenticationError(
                            f"Token request failed: "
                            f"{error_data.get('error', 'Unknown error')}"
                        )

                except httpx.RequestError as e:
                    console.print(f"[red]Network error:[/red] {e}")
                    time.sleep(device_response.interval)
                    continue

        raise AuthenticationError("Authentication timed out")

    def _store_token(self, token_response: TokenResponse) -> None:
        """Store authentication token."""
        # The access_token from community site is already a Deepgram API key
        api_key = token_response.access_token
        project_id = token_response.project_id

        profile_name = self.config.profile or "default"
        keyring_available = False

        try:
            keyring.set_password(
                KEYRING_SERVICE, f"api-key.{profile_name}", api_key
            )
            if project_id:
                keyring.set_password(
                    KEYRING_SERVICE, f"project-id.{profile_name}", project_id
                )
            console.print(
                "[green]✓[/green] Credentials stored securely in system "
                "keyring"
            )
            keyring_available = True
        except Exception as e:
            console.print(
                f"[yellow]Warning:[/yellow] Could not store in keyring: {e}"
            )
            console.print("Credentials will be stored in config file instead")

        # Update config - only store API key if keyring is not available
        self.config.create_profile(
            profile_name,
            api_key=api_key if not keyring_available else None,
            project_id=project_id,
        )

    def logout(self) -> None:
        """Logout user and clear credentials."""
        profile_name = self.config.profile or "default"

        # Clear keyring
        try:
            keyring.delete_password(KEYRING_SERVICE, f"api-key.{profile_name}")
            keyring.delete_password(
                KEYRING_SERVICE, f"project-id.{profile_name}"
            )
            console.print("[dim]Cleared credentials from system keyring[/dim]")
        except Exception:
            pass  # Ignore errors if not stored

        # Clear sensitive data from config but keep profile
        if profile_name in self.config.list_profiles():
            profile = self.config.get_profile(profile_name)
            self.config.create_profile(
                profile_name,
                api_key=None,  # Clear API key
                project_id=profile.project_id,  # Keep project ID
                base_url=profile.base_url,  # Keep base URL
            )

        console.print("[green]✓[/green] Successfully logged out")

    def list_profiles(self) -> ProfilesResult:
        """Return all profiles wrapped in ProfilesResult model."""
        profiles: dict[str, ProfileInfo] = {}

        for profile_name in self.config.list_profiles():
            profile = self.config.get_profile(profile_name)

            # Try to get API key from keyring first
            api_key = None
            project_id = profile.project_id

            try:
                api_key = keyring.get_password(
                    KEYRING_SERVICE, f"api-key.{profile_name}"
                )
                # Also check if project_id is in keyring
                keyring_project_id = keyring.get_password(
                    KEYRING_SERVICE, f"project-id.{profile_name}"
                )
                if keyring_project_id:
                    project_id = keyring_project_id
            except Exception:
                # Fall back to config
                api_key = profile.api_key

            masked_key = None
            if api_key:
                masked_key = "****" + api_key[-4:]

            profiles[profile_name] = ProfileInfo(
                api_key=masked_key,
                project_id=project_id,
                base_url=profile.base_url,
            )

        return ProfilesResult(
            profiles=profiles,
            current_profile=self.config.profile
            or self.config._config.default_profile,
        )

    def __del__(self) -> None:
        """Cleanup HTTP client."""
        if hasattr(self, "client"):
            self.client.close()
