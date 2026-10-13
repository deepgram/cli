"""Cross-platform authentication system for deepctl using dx-id OIDC provider."""

import os
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import keyring
from keyring.errors import PasswordDeleteError
from pydantic import BaseModel
from rich.progress import Progress, SpinnerColumn, TextColumn

from .client import _split_base_url
from .config import Config
from .models import ProfileInfo, ProfilesResult
from .output import stderr_console

# Diagnostics only: everything printed here is status or error chrome, so
# it goes to stderr and can never corrupt the machine-readable payload a
# command writes to stdout. See get_status_console() in output.py.
console = stderr_console

# Auth provider base URL (dx-id OIDC provider)
AUTH_BASE_URL = os.getenv("DEEPGRAM_CLI_BASE_URL", "https://id.dx.deepgram.com")
DEVICE_CODE_URL = f"{AUTH_BASE_URL}/device/code"
TOKEN_POLL_URL = f"{AUTH_BASE_URL}/device/token"

# Registered OIDC client ID for the CLI
CLIENT_ID = "deepgram-cli"

# Keyring service identifier using reverse domain notation
KEYRING_SERVICE = "com.deepgram.dx.deepctl"


class DeviceCodeResponse(BaseModel):
    """Response from device code request."""

    device_code: str
    user_code: str | None = None
    verification_uri: str
    verification_uri_complete: str | None = None
    expires_in: int
    interval: int


class TokenResponse(BaseModel):
    """Response from token request."""

    access_token: str
    project_id: str
    dg_token: str | None = None
    dg_expires_in: int | None = None


class AuthenticationError(Exception):
    """Authentication related errors."""

    pass


class AuthManager:
    """Cross-platform authentication manager."""

    def __init__(
        self,
        config: Config,
        explicit_api_key: str | None = None,
        explicit_project_id: str | None = None,
    ):
        """Initialize authentication manager.

        Args:
            config: Configuration manager instance
            explicit_api_key: Explicitly provided API key (e.g., from CLI flags)
            explicit_project_id: Explicitly provided project ID (e.g., from CLI flags)
        """
        self.config = config
        self.explicit_api_key = explicit_api_key
        self.explicit_project_id = explicit_project_id
        # Disable SSL verification for local dev (DEEPGRAM_CLI_INSECURE=1)
        verify = not bool(os.getenv("DEEPGRAM_CLI_INSECURE"))
        self.client = httpx.Client(timeout=30.0, verify=verify)

    def has_env_credentials(self) -> tuple[bool, bool]:
        """Check if environment variables are set.

        Returns:
            Tuple of (has_api_key, has_project_id)
        """
        return bool(os.getenv("DEEPGRAM_API_KEY")), bool(
            os.getenv("DEEPGRAM_PROJECT_ID")
        )

    def has_profile_credentials(
        self, profile_name: str | None = None
    ) -> tuple[bool, bool]:
        """Check if profile has stored credentials.

        Args:
            profile_name: Profile to check (uses current profile if not specified)

        Returns:
            Tuple of (has_api_key, has_project_id)
        """
        profile_name = profile_name or self.config.profile or "default"

        # Check keyring for a direct API key.
        has_api_key = False
        expired, _ = self._credential_expiry(profile_name)
        if not expired:
            try:
                has_api_key = bool(
                    keyring.get_password(KEYRING_SERVICE, f"api-key.{profile_name}")
                )
            except Exception:
                pass

            # If not in keyring, check config.
            if not has_api_key:
                profile = self.config.get_profile(profile_name)
                has_api_key = bool(profile.api_key)

        # Check for project ID in profile
        profile = self.config.get_profile(profile_name)
        has_project_id = bool(profile.project_id)

        return has_api_key, has_project_id

    def is_authenticated(self, check_profile_only: bool = False) -> bool:
        """Check if user is authenticated.

        Args:
            check_profile_only: If True, only check profile credentials, not env vars
        """
        # Check explicit credentials first (highest priority)
        if self.explicit_api_key:
            return True

        # Check profile credentials
        profile_name = self.config.profile or "default"
        has_profile_key, _ = self.has_profile_credentials(profile_name)
        if has_profile_key:
            return True

        # Check environment variables (unless checking profile only)
        return not check_profile_only and bool(os.getenv("DEEPGRAM_API_KEY"))

    def is_ci_mode(self) -> bool:
        """Check if running in CI mode (credentials from environment)."""
        # If both API key and project ID are provided via environment,
        # we're in CI mode
        return bool(os.getenv("DEEPGRAM_API_KEY") and os.getenv("DEEPGRAM_PROJECT_ID"))

    def get_api_key(self, ignore_env: bool = False) -> str | None:
        """Get API key following precedence: explicit > profile > env.

        Expired finite profile credentials are treated as absent, allowing an
        explicit key or ``DEEPGRAM_API_KEY`` to continue to work. If no other
        credential is available, raise an actionable login error.

        Args:
            ignore_env: If True, don't check environment variables
        """
        # Explicit credentials (--api-key flag, CI/CD) bypass the JWT flow.
        if self.explicit_api_key:
            return self.explicit_api_key

        profile_name = self.config.profile or "default"
        current_profile = self.config.get_profile(profile_name)

        expired, expiry_error = self._credential_expiry(profile_name)
        if not expired:
            # Profile keyring entry, followed by its config-file fallback.
            try:
                api_key = keyring.get_password(
                    KEYRING_SERVICE, f"api-key.{profile_name}"
                )
                if api_key:
                    return api_key
            except Exception:
                pass

            if current_profile.api_key:
                return current_profile.api_key
        else:
            self._delete_keyring_credential(f"api-key.{profile_name}")

        # Environment variable — lowest priority.
        if not ignore_env:
            api_key = os.getenv("DEEPGRAM_API_KEY")
            if api_key:
                return api_key

        if expiry_error:
            raise AuthenticationError(expiry_error)

        return None

    def _credential_expiry(self, profile_name: str) -> tuple[bool, str | None]:
        """Return whether a finite profile credential is unusable and why."""
        expires_at = self.config.get_profile(profile_name).dg_token_expires_at
        if not isinstance(expires_at, str):
            return False, None

        try:
            parsed_expiry = datetime.fromisoformat(expires_at)
            if parsed_expiry.tzinfo is None:
                raise ValueError
        except (TypeError, ValueError):
            return (
                True,
                "Your Deepgram credential expiry is invalid. Run `dg login` again.",
            )

        if parsed_expiry <= datetime.now(timezone.utc):
            return True, "Your Deepgram credential has expired. Run `dg login` again."

        return False, None

    def _delete_keyring_credential(self, keyring_key: str) -> None:
        """Remove a credential when present without masking keyring failures."""
        try:
            keyring.delete_password(KEYRING_SERVICE, keyring_key)
        except PasswordDeleteError:
            pass
        except Exception as exc:
            console.print(
                f"[yellow]Warning:[/yellow] Could not remove legacy credential: {exc}"
            )

    def get_project_id(self, ignore_env: bool = False) -> str | None:
        """Get project ID following precedence: explicit > profile > env.

        Args:
            ignore_env: If True, don't check environment variables
        """
        # Explicit credentials have highest priority
        if self.explicit_project_id:
            return self.explicit_project_id

        # Check profile next
        profile_name = self.config.profile or "default"
        current_profile = self.config.get_profile(profile_name)
        if current_profile.project_id:
            return current_profile.project_id

        # Environment variable has lowest priority
        if not ignore_env:
            project_id = os.getenv("DEEPGRAM_PROJECT_ID")
            if project_id:
                return project_id

        return None

    def get_credential_source(self) -> str:
        """Get a description of where credentials are coming from.

        Returns:
            Description like "explicit flags", "profile 'default'", or "environment variables"
        """
        if self.explicit_api_key or self.explicit_project_id:
            return "explicit flags"

        profile_name = self.config.profile or "default"
        has_profile_key, has_profile_project = self.has_profile_credentials(
            profile_name
        )

        if has_profile_key or has_profile_project:
            return f"profile '{profile_name}'"

        has_env_key, has_env_project = self.has_env_credentials()
        if has_env_key or has_env_project:
            return "environment variables"

        return "no credentials"

    def verify_api_key(
        self, api_key: str | None = None
    ) -> tuple[bool, str, str | None]:
        """Verify that an API key is valid by listing projects.

        Args:
            api_key: API key to verify (uses stored key if not provided)

        Returns:
            Tuple of (success, message, error_type)
        """
        if not api_key:
            api_key = self.get_api_key()

        if not api_key:
            return False, "No API key provided or stored", "auth"

        try:
            headers = {
                "Authorization": f"Token {api_key}",
                "Content-Type": "application/json",
            }

            # Verify against the configured base URL (not hardcoded prod), so a
            # custom base (e.g. staging) validates its own keys correctly.
            # Preserve the input scheme so a plaintext endpoint isn't forced
            # onto TLS.
            base = self.config.get_profile().base_url or "https://api.deepgram.com"
            rest_scheme, _, host = _split_base_url(base)
            response = self.client.get(
                f"{rest_scheme}://{host}/v1/projects",
                headers=headers,
            )

            if response.status_code == 200:
                return True, "API key verified successfully", None
            elif response.status_code == 401:
                return False, "Invalid API key - authentication failed", "auth"
            elif response.status_code == 403:
                return False, "API key lacks required permissions", "auth"
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

    def verify_credentials(
        self, api_key: str | None = None, project_id: str | None = None
    ) -> tuple[bool, str, str | None]:
        """Verify API key and optionally project ID.

        If project_id is provided, verifies both the key and project access.
        If only api_key is provided, verifies just the key.

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
        if project_id is None:
            project_id = self.get_project_id()

        # Check if we have an API key
        if not api_key:
            return False, "No API key provided or stored", "auth"

        # If no project_id, just verify the API key alone
        if not project_id:
            return self.verify_api_key(api_key)

        # Make API request to verify both credentials
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
        """Guard function to ensure a valid API key is available.

        Only validates that the API key works. Project ID validation
        is handled separately by requires_project on BaseCommand.
        """
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

        # Verify the API key is valid (does NOT require project_id)
        success, message, error_type = self.verify_api_key(api_key)
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

    def login_with_api_key(
        self, api_key: str, project_id: str | None = None, _force_write: bool = False
    ) -> None:
        """Login with API key directly (CI-friendly method).

        Args:
            api_key: Deepgram API key
            project_id: Deepgram project ID (optional — can be set later)
            force_write: Skip confirmation prompts
        """
        # Validate API key format (basic check)
        if not api_key.startswith(("sk-", "pk-")):
            console.print(
                "[red]Warning:[/red] API key format doesn't match expected pattern"
            )

        # Verify API key (and project if provided)
        console.print("[dim]Verifying credentials...[/dim]")
        success, message, error_type = self.verify_credentials(
            api_key, project_id or None
        )

        if not success:
            if error_type == "auth":
                raise AuthenticationError(message)
            elif error_type == "project":
                # Project ID is invalid, but the key might be fine — warn but
                # still store the key so the user isn't locked out.
                console.print(
                    f"[yellow]Warning:[/yellow] {message}. "
                    "API key will still be stored."
                )
                console.print(
                    "[dim]You can update the project ID later with: "
                    "deepctl login --project-id <id>[/dim]"
                )
                # Clear the bad project_id so we don't store it
                project_id = None
            else:
                raise AuthenticationError(message)
        else:
            console.print(f"[green]✓[/green] {message}")

        # Store API key in keyring for security
        profile_name = self.config.profile or "default"
        keyring_available = False

        try:
            keyring.set_password(KEYRING_SERVICE, f"api-key.{profile_name}", api_key)
            # Don't store project ID in keyring - it goes in profile config only
            console.print("[green]✓[/green] API key stored securely in system keyring")
            keyring_available = True
        except Exception as e:
            console.print(f"[yellow]Warning:[/yellow] Could not store in keyring: {e}")
            console.print("API key will be stored in config file instead")

        # Update config with non-sensitive data
        # Only store API key in config if keyring is not available
        self.config.create_profile(
            profile_name,
            api_key=api_key if not keyring_available else None,
            project_id=project_id,  # Always store project ID in config
            base_url=self.config.get_profile(profile_name).base_url,
        )

        console.print("[green]✓[/green] Successfully logged in with API key")
        console.print(f"[dim]Profile:[/dim] {profile_name}")

        if project_id:
            console.print(f"[dim]Project ID:[/dim] {project_id}")

    def login_with_device_flow(self) -> None:
        """Login using device flow (interactive method)."""
        console.print("[blue]Starting device flow authentication...[/blue]")

        try:
            # Get hostname for device identification
            hostname = os.uname().nodename if hasattr(os, "uname") else "unknown"

            # Request device code from dx-id auth provider
            device_response = self._request_device_code()

            verification_uri = (
                device_response.verification_uri_complete
                or device_response.verification_uri
            )

            # Open browser immediately and start polling
            console.print(
                "\n[bold]Hello from Deepgram![/bold] Opening browser to "
                "complete login..."
            )
            if device_response.user_code:
                console.print(f"[bold]Enter code:[/bold] {device_response.user_code}")

            try:
                webbrowser.open(verification_uri)
                console.print("[green]✓[/green] Opened browser for authentication")
            except Exception as e:
                console.print(f"[yellow]Warning:[/yellow] Could not open browser: {e}")

            console.print(
                f"[dim]If the browser didn't open, visit:[/dim] "
                f"[dim]{verification_uri}[/dim]\n"
            )

            # Poll for token (starts immediately — user may have clicked the link)
            token_response = self._poll_for_token(device_response, hostname)

            # Store token and get user info
            self._store_token(token_response)

            console.print("\n[green]Key created and stored successfully.[/green]")
            console.print("\nYou are now logged in. Happy coding!")

        except Exception as e:
            console.print(f"[red]Error during device flow:[/red] {e}")
            raise AuthenticationError(f"Device flow failed: {e}")

    def _request_device_code(self) -> DeviceCodeResponse:
        """Request device code from auth provider.

        Returns:
            DeviceCodeResponse with device code and verification URI
        """
        hostname = os.uname().nodename if hasattr(os, "uname") else "unknown"

        payload = {
            "client_id": CLIENT_ID,
            "hostname": hostname,
            "scopes": "openid profile email",
        }

        try:
            response = self.client.post(
                DEVICE_CODE_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
            )

            if response.status_code == 201:
                return DeviceCodeResponse(**response.json())
            else:
                raise AuthenticationError(
                    f"Device code request failed: {response.status_code}"
                )

        except httpx.RequestError as e:
            raise AuthenticationError(f"Network error during device code request: {e}")

    def _poll_for_token(
        self,
        device_response: DeviceCodeResponse,
        hostname: str,
    ) -> TokenResponse:
        """Poll for token using device code."""
        console.print("\n[blue]Waiting for authentication...[/blue]")

        start_time = time.time()

        query_params = {
            "device_code": device_response.device_code,
            "client_id": CLIENT_ID,
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
        """Store authentication token.

        Stores a direct finite Deepgram credential from dx-id. The credential
        expires independently of the OIDC access token and requires a new
        device authorization when it expires.
        """
        profile_name = self.config.profile or "default"

        if token_response.dg_token:
            api_key = token_response.dg_token
            expires_in = token_response.dg_expires_in or 0
            if expires_in <= 0:
                raise AuthenticationError(
                    "Deepgram sign-in did not provide a finite credential expiry"
                )
            expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=expires_in)
            ).isoformat()

            keyring_available = False
            try:
                keyring.set_password(
                    KEYRING_SERVICE, f"api-key.{profile_name}", api_key
                )
                keyring_available = True
            except Exception as e:
                console.print(
                    f"[yellow]Warning:[/yellow] Could not store in keyring: {e}"
                )
                console.print("API key will be stored in config file instead")

            for keyring_key in (f"jwt.{profile_name}", f"refresh-token.{profile_name}"):
                self._delete_keyring_credential(keyring_key)

            self.config.create_profile(
                profile_name,
                api_key=api_key if not keyring_available else None,
                project_id=token_response.project_id,
                base_url=self.config.get_profile(profile_name).base_url,
            )
            profile = self.config.get_profile(profile_name)
            profile.dg_token_expires_at = expires_at
            self.config.save()
            if keyring_available:
                console.print(
                    "[green]✓[/green] API key stored securely in system keyring"
                )
            else:
                console.print(
                    "[yellow]Warning:[/yellow] API key stored in the config file fallback"
                )
            return

        raise AuthenticationError(
            "Deepgram sign-in did not return a finite credential. Run `dg update` and `dg login` again."
        )

    def logout(self, keep_config: bool = False) -> None:
        """Logout user and clear credentials.

        Args:
            keep_config: If True, keep profile configuration (project ID, base URL)
                        and only clear credentials. If False, remove profile completely.
        """
        profile_name = self.config.profile or "default"

        # Clear all credential types from keyring.
        for keyring_key in (
            f"api-key.{profile_name}",
            f"jwt.{profile_name}",
            f"refresh-token.{profile_name}",
        ):
            try:
                keyring.delete_password(KEYRING_SERVICE, keyring_key)
            except Exception:
                pass
        console.print("[dim]Cleared credentials from system keyring[/dim]")

        if keep_config:
            # Clear sensitive data from config but keep profile
            if profile_name in self.config.list_profiles():
                profile = self.config.get_profile(profile_name)
                self.config.create_profile(
                    profile_name,
                    api_key=None,  # Clear API key
                    project_id=profile.project_id,  # Keep project ID
                    base_url=profile.base_url,  # Keep base URL
                )
        else:
            # Clear profile completely from config
            self.config.delete_profile(profile_name)

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
                # Project ID is only stored in config, not keyring
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
            current_profile=self.config.profile or self.config._config.default_profile,
        )

    def __del__(self) -> None:
        """Cleanup HTTP client."""
        if hasattr(self, "client"):
            self.client.close()
