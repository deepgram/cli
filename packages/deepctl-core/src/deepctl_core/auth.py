"""Cross-platform authentication system for deepctl using dx-id OIDC provider."""

import os
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, TypeVar, cast
from urllib.parse import urlsplit

from pydantic import BaseModel, field_validator

from . import credentials, hosts, output, transport
from . import messages as msg
from .client import _split_base_url
from .config import Config
from .hosts import check_credential_host, check_identity_host
from .login_state import MIN_POLL_INTERVAL
from .models import ProfileInfo, ProfilesResult
from .output import stderr_console

if TYPE_CHECKING:
    # Bound at run time by _load_modules() below, not by these imports.
    import httpx  # noqa: TC004
    import keyring  # noqa: TC004

# httpx and keyring load when they're first used, not when this module is
# imported (every command module imports it) or an AuthManager is built
# (every command builds one). __getattr__ binds them as module globals on
# first access, so `deepctl_core.auth.keyring` is the real module and tests
# can patch it as before. The keyring is read through
# deepctl_core.credentials, which bounds the read off a terminal.
_LAZY_MODULES = ("httpx", "keyring")

_F = TypeVar("_F", bound=Callable[..., Any])


def _bind_module(name: str) -> Any:
    import importlib

    module = importlib.import_module(name)
    globals()[name] = module
    return module


# Only at run time: type checkers see the TYPE_CHECKING imports above.
if not TYPE_CHECKING:

    def __getattr__(name: str) -> Any:
        if name not in _LAZY_MODULES:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        return _bind_module(name)


def _load_modules(*names: str) -> None:
    for name in names or _LAZY_MODULES:
        if name not in globals():
            _bind_module(name)


# Diagnostics only: everything printed here is status or error chrome, so
# it goes to stderr and can never corrupt the machine-readable payload a
# command writes to stdout. See get_status_console() in output.py.
console = stderr_console

# Auth provider (dx-id OIDC) base URL: hosts.DEFAULT_IDENTITY_URL, or
# DEEPGRAM_CLI_BASE_URL, resolved per login through check_identity_host().

# Registered OIDC client ID for the CLI
CLIENT_ID = "deepgram-cli"

# Keyring service identifier using reverse domain notation
KEYRING_SERVICE = "com.deepgram.dx.deepctl"

# Sent where the identity provider asks for a device name: a fixed value,
# never this machine's hostname.
DEVICE_NAME = "deepctl"
DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


def _https_or_loopback(url: str | None) -> str | None:
    """``url`` when it is https, or http to a loopback host; else ValueError."""
    if url is None:
        return None
    origin = hosts.parse_origin(url)
    if origin is None or not (origin.secure or hosts.is_loopback(origin.host)):
        raise ValueError("the login link must use https")
    return url


class DeviceCodeResponse(BaseModel):
    """Response from device code request."""

    device_code: str
    user_code: str | None = None
    verification_uri: str
    verification_uri_complete: str | None = None
    expires_in: int
    interval: int = 5

    _check_uris = field_validator("verification_uri", "verification_uri_complete")(
        _https_or_loopback
    )


class TokenResponse(BaseModel):
    """Response from token request (``pid`` is the RFC 8628 path's name for
    the project ID)."""

    access_token: str
    project_id: str | None = None
    pid: str | None = None
    dg_token: str | None = None
    dg_expires_in: int | None = None


@dataclass(frozen=True)
class DevicePoll:
    """One answer from the token endpoint.

    ``state`` is ``token`` (``token`` is set), ``pending``, ``slow_down``,
    ``denied``, ``expired`` or ``error`` (``detail`` says why).
    """

    state: str
    token: TokenResponse | None = None
    detail: str = ""
    retry_after: int | None = None


def _retry_after(response: "httpx.Response") -> int | None:
    """The server's Retry-After in whole seconds (delta form only), or None."""
    value = response.headers.get("Retry-After")
    if not isinstance(value, str) or not value.strip().isdigit():
        return None
    value = value.strip()
    return min(int(value), 3600)


def _insecure_allowed() -> bool:
    """Whether DEEPGRAM_CLI_INSECURE may turn off TLS verification.

    Only ``1`` or ``true`` count (``0``, ``false`` or anything else keeps
    verification on), and never in an AI agent session. Even then it only
    applies to localhost, 127.0.0.1 and [::1]: see ``_insecure_mounts``.
    """
    value = os.getenv("DEEPGRAM_CLI_INSECURE", "").strip().lower()
    if value not in ("1", "true"):
        return False
    if output.is_ai_agent():
        if hosts._first_time(("W-INSECURE-AGENT", "")):
            output.print_security_warning(msg.W_INSECURE_AGENT)
        return False
    return True


#: Hosts DEEPGRAM_CLI_INSECURE applies to: local dev servers only. Other
#: 127.0.0.0/8 addresses keep verification (mounts match exact hosts).
_INSECURE_HOSTS = ("localhost", "127.0.0.1", "::1")


def _insecure_mounts() -> "dict[str, httpx.BaseTransport]":
    """Unverified transports for the loopback hosts only.

    Host-specific mounts take precedence over the scheme-wide proxy mounts
    httpx builds from HTTPS_PROXY/ALL_PROXY/NO_PROXY, so every other host
    keeps both certificate verification and the proxy settings.
    """
    _load_modules("httpx")
    unverified = httpx.HTTPTransport(verify=False)
    return {
        f"all://{'[::1]' if host == '::1' else host}": unverified
        for host in _INSECURE_HOSTS
    }


def _warn_if_verified(request: "httpx.Request") -> None:
    """Say once per host that DEEPGRAM_CLI_INSECURE did not apply to it."""
    from rich.markup import escape

    host = request.url.host
    if host in _INSECURE_HOSTS:
        return
    if hosts._first_time(("W-INSECURE-REMOTE", host)):
        output.print_security_warning(
            escape(msg.W_INSECURE_REMOTE.format(host=msg.display(host)))
        )
        output.print_hint(escape(msg.W_INSECURE_REMOTE_HINT))


def escape(text: str) -> str:
    """rich.markup.escape, imported on first use (no rich at import)."""
    from rich.markup import escape as rich_escape

    return rich_escape(text)


class AuthenticationError(Exception):
    """Authentication related errors."""

    pass


class DeviceCodeUnreadable(AuthenticationError):
    """The device-code answer from ``host`` couldn't be read (not a JSON
    object with the expected fields): ``network_unavailable``."""

    def __init__(self, host: str) -> None:
        super().__init__(f"unreadable device code response from {host}")
        self.host = host


def _forgets_resolved(method: _F) -> _F:
    """Forget cached credential answers once ``method`` has written the keyring
    or a profile (also when it fails part-way), so later questions see the
    new state."""
    import functools

    @functools.wraps(method)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return method(self, *args, **kwargs)
        finally:
            self._forget_resolved()

    return cast("_F", wrapper)


class AuthRequiredError(transport.TransportError, AuthenticationError):
    """``auth_required``: no API key is set (``E-AUTH-REQUIRED``), or the only
    one was in a user config file this AI agent session doesn't trust
    (``E-KEY-UNTRUSTED``).

    A :class:`~deepctl_core.transport.TransportError`, so ``BaseCommand``
    renders the coded result, and an :class:`AuthenticationError`, so code
    that caught ``guard()``'s error before 0.5.0 still does.
    """

    def __init__(self, untrusted_source: str | None = None) -> None:
        if untrusted_source is None:
            super().__init__("auth_required", {}, {})
        else:
            super().__init__(
                "auth_required",
                {"user_config": untrusted_source},
                {},
                transport.KEY_UNTRUSTED,
            )


class _NoFlags:
    """A credentials context without ``--api-key`` and ``--project-id``."""

    explicit_api_key = None
    explicit_project_id = None

    def __init__(self, config: Config) -> None:
        self.config = config


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
        # One credential resolution per question set (deepctl_core.credentials);
        # forgotten whenever this manager writes the keyring or a profile.
        self._resolved: dict[tuple[Any, ...], credentials.Resolved] = {}
        # Keyring entries this manager already removed (expired credentials).
        self._pruned: set[str] = set()
        self._client: httpx.Client | None = None

    @property
    def client(self) -> "httpx.Client":
        """The HTTP client for login's device flow and key verification.

        Built on first use, so no other command makes a second client; API
        requests use the shared one (``deepctl_core.transport``).
        """
        if self._client is None:
            _load_modules("httpx")
            # DEEPGRAM_CLI_INSECURE=1 skips TLS verification for localhost,
            # 127.0.0.1 and [::1] (local dev), never for any other host and
            # never in an AI agent session: the agent may have set it. Proxy
            # settings from the environment still apply to every other host.
            if _insecure_allowed():
                self._client = httpx.Client(
                    timeout=30.0,
                    verify=True,
                    mounts=_insecure_mounts(),
                    event_hooks={"request": [_warn_if_verified]},
                )
            else:
                self._client = httpx.Client(timeout=30.0, verify=True)
        return self._client

    @client.setter
    def client(self, value: "httpx.Client | None") -> None:
        self._client = value

    @client.deleter
    def client(self) -> None:
        self._client = None

    def resolve(
        self,
        *,
        profile: str | None = None,
        ignore_env: bool = False,
        need_key: bool = True,
        use_flags: bool = True,
        prune: bool = True,
    ) -> credentials.Resolved:
        """This run's credentials and their sources (``credentials.resolve``).

        ``use_flags=False`` answers as if no ``--api-key`` or
        ``--project-id`` had been given (the stored-profile questions).
        ``prune=False`` keeps it read-only (introspection: whoami, login's
        checks); answers are cached either way, so one run resolves once.
        """
        ctx: Any = self if use_flags else _NoFlags(self.config)
        name = profile or credentials.profile_name(self.config)[0]
        stored = self.config.get_profile(name)
        state = (
            name,
            ignore_env,
            ctx.explicit_api_key,
            ctx.explicit_project_id,
            stored.api_key,
            stored.project_id,
            stored.dg_token_expires_at,
        )
        # A full answer also answers the project-only question.
        found = self._resolved.get((True, *state))
        if found is None and not need_key:
            found = self._resolved.get((False, *state))
        if found is None:
            found = credentials.resolve(
                ctx,
                profile=profile,
                ignore_env=ignore_env,
                need_key=need_key,
                prune=False,
            )
            self._resolved[(need_key, *state)] = found
        # Project-only questions (need_key=False) never touch the keyring.
        if (
            prune
            and need_key
            and found.prune_target
            and found.prune_target not in self._pruned
        ):
            self._pruned.add(found.prune_target)
            credentials.prune_expired(found)
        return found

    def _forget_resolved(self) -> None:
        self._resolved.clear()
        self._pruned.clear()

    def check_base_url(self) -> str:
        """Return the base URL credentials may be sent to.

        Raises ``hosts.CredentialHostError`` (already reported on stderr)
        when the effective host isn't trusted; nothing has been sent.
        """
        return check_credential_host(self.config)

    def has_env_credentials(self) -> tuple[bool, bool]:
        """Check if environment variables are set.

        Returns:
            Tuple of (has_api_key, has_project_id)
        """
        return bool(credentials.env_value("DEEPGRAM_API_KEY")), bool(
            credentials.env_value("DEEPGRAM_PROJECT_ID")
        )

    def has_profile_credentials(
        self, profile_name: str | None = None
    ) -> tuple[bool, bool]:
        """Check if profile has stored credentials.

        As in 0.4.1, a profile key is a usable keyring entry or a key in the
        profile as the config holds it (which includes ``DEEPGRAM_API_KEY``).

        Args:
            profile_name: Profile to check (uses current profile if not specified)

        Returns:
            Tuple of (has_api_key, has_project_id)
        """
        resolved = self.resolve(profile=profile_name, use_flags=False, prune=False)
        return resolved.profile_has_key, resolved.profile_has_project

    def is_authenticated(self, check_profile_only: bool = False) -> bool:
        """Check if user is authenticated.

        Args:
            check_profile_only: If True, only check profile credentials, not env vars
        """
        if self.explicit_api_key:
            return True
        resolved = self.resolve(use_flags=False, prune=False)
        if resolved.profile_has_key:
            return True
        return not check_profile_only and bool(
            credentials.env_value("DEEPGRAM_API_KEY")
        )

    def is_ci_mode(self) -> bool:
        """Check if running in CI mode (credentials from environment)."""
        # If both API key and project ID are provided via environment,
        # we're in CI mode
        return bool(
            credentials.env_value("DEEPGRAM_API_KEY")
            and credentials.env_value("DEEPGRAM_PROJECT_ID")
        )

    def get_api_key(self, ignore_env: bool = False) -> str | None:
        """Get API key following precedence: explicit > profile > env.

        Expired finite profile credentials are treated as absent, allowing an
        explicit key or ``DEEPGRAM_API_KEY`` to continue to work. If no other
        credential is available, raise an actionable login error.

        Args:
            ignore_env: If True, don't check environment variables
        """
        resolved = self.resolve(ignore_env=ignore_env)
        if resolved.api_key:
            return resolved.api_key
        if resolved.expiry_error:
            raise AuthenticationError(resolved.expiry_error)
        return None

    def _credential_expiry(self, profile_name: str) -> tuple[bool, str | None]:
        """Return whether a finite profile credential is unusable and why."""
        return credentials.credential_expiry(self.config, profile_name)

    def _delete_keyring_credential(self, keyring_key: str) -> None:
        """Remove a credential when present without masking keyring failures."""
        credentials.keyring_delete(keyring_key)

    def get_project_id(self, ignore_env: bool = False) -> str | None:
        """Get project ID following precedence: explicit > profile > env.

        Args:
            ignore_env: If True, don't check environment variables
        """
        return self.resolve(ignore_env=ignore_env, need_key=False).project_id

    def get_credential_source(self) -> str:
        """Get a description of where credentials are coming from.

        Returns:
            Description like "explicit flags", "profile 'default'", or "environment variables"
        """
        if self.explicit_api_key or self.explicit_project_id:
            return "explicit flags"

        # The real source: a profile key equal to DEEPGRAM_API_KEY came from
        # the environment (Config merges it into the profile).
        resolved = self.resolve()
        if resolved.key_source in (
            credentials.KEY_SOURCE_KEYRING,
            credentials.KEY_SOURCE_FILE,
        ) or (resolved.project_source == "profile"):
            return f"profile '{resolved.profile}'"
        if (
            resolved.key_source == credentials.KEY_SOURCE_ENV
            or resolved.project_source == "env"
        ):
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
        _load_modules("httpx")
        if not api_key:
            api_key = self.get_api_key()

        if not api_key:
            return False, "No API key provided or stored", "auth"

        # Verify against the configured base URL (not hardcoded prod), so a
        # custom base (e.g. staging) validates its own keys correctly -- but
        # only once that host is trusted with the key.
        base = self.check_base_url()

        try:
            headers = {
                **transport.request_headers(api_key),
                "Content-Type": "application/json",
            }

            # Preserve the input scheme so a plaintext endpoint isn't forced
            # onto TLS.
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
        _load_modules("httpx")
        # Use provided credentials or get from storage
        if not api_key:
            api_key = self.get_api_key()
        if project_id is None:
            project_id = self.get_project_id()

        # Check if we have an API key
        if not api_key:
            return False, "No API key provided or stored", "auth"

        base = self.check_base_url()

        # If no project_id, just verify the API key alone
        if not project_id:
            return self.verify_api_key(api_key)

        # Make API request to verify both credentials
        try:
            headers = {
                **transport.request_headers(api_key),
                "Content-Type": "application/json",
            }

            # Project details from the same (checked) base URL as the key
            rest_scheme, _, host = _split_base_url(base)
            response = self.client.get(
                f"{rest_scheme}://{host}/v1/projects/{project_id}",
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
        """Check that an API key is available, without sending it anywhere.

        Refuses first, before reading any key, when the base URL isn't
        trusted with credentials. With no key, raises :class:`AuthRequiredError`
        (``auth_required``), which ``BaseCommand`` renders; the key itself
        is checked by the command's own request: a 401 or 403 there becomes
        ``auth_invalid`` or ``auth_forbidden`` (see ``deepctl_core.transport``).
        Project ID validation is handled separately by requires_project on
        BaseCommand.
        """
        self.check_base_url()
        # Raises the expiry error when a stored credential expired and
        # nothing else is set, as before.
        if self.get_api_key():
            return
        resolved = self.resolve()
        if resolved.untrusted_source:
            # The file's key was left unused (W-PERM-AGENT / W-CFG-UNTRUSTED):
            # say so, and send the agent to a person, not to dg login.
            raise AuthRequiredError(untrusted_source=resolved.untrusted_source)
        raise AuthRequiredError()

    @_forgets_resolved
    def login_with_api_key(
        self, api_key: str, project_id: str | None = None, _force_write: bool = False
    ) -> None:
        """Login with API key directly (CI-friendly method).

        Args:
            api_key: Deepgram API key
            project_id: Deepgram project ID (optional — can be set later)
            force_write: Skip confirmation prompts
        """
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
                output.print_warning(
                    escape(f"{message}. API key will still be stored."), stderr=True
                )
                console.print(
                    "[dim]You can set the project ID later with: "
                    f"{transport.prog() or 'dg'} login --project-id <id>[/dim]"
                )
                # Clear the bad project_id so we don't store it
                project_id = None
            else:
                raise AuthenticationError(message)
        else:
            console.print(f"[green]✓[/green] {message}")

        # Store API key in keyring for security
        _load_modules("keyring")
        profile_name = self.config.profile or "default"
        keyring_available = False
        # Nothing goes to the keyring when the profile can't be saved.
        self.config.check_saveable()

        try:
            keyring.set_password(KEYRING_SERVICE, f"api-key.{profile_name}", api_key)
            # Don't store project ID in keyring - it goes in profile config only
            console.print("[green]✓[/green] API key stored securely in system keyring")
            keyring_available = True
        except Exception as e:
            output.print_warning(
                escape(
                    f"Could not store in keyring: {e}. "
                    "The API key will be stored in the config file instead."
                ),
                stderr=True,
            )

        # Update config with non-sensitive data
        # Only store API key in config if keyring is not available
        self.config.create_profile(
            profile_name,
            api_key=api_key if not keyring_available else None,
            project_id=project_id,  # Always store project ID in config
        )

        console.print("[green]✓[/green] Successfully logged in with API key")
        console.print(f"[dim]Profile:[/dim] {profile_name}")

        if project_id:
            console.print(f"[dim]Project ID:[/dim] {project_id}")

    def login_with_device_flow(self, open_browser: bool = True) -> None:
        """Login using device flow (interactive method)."""
        console.print("[blue]Starting device flow authentication...[/blue]")

        # The token exchange mints the API key: check the identity host first.
        identity_url = check_identity_host(self.config)

        try:
            # Request device code from dx-id auth provider
            device_response = self._request_device_code(identity_url)

            verification_uri = (
                device_response.verification_uri_complete
                or device_response.verification_uri
            )

            # Open browser immediately and start polling
            if open_browser:
                console.print(
                    "\n[bold]Hello from Deepgram![/bold] Opening browser to "
                    "complete login..."
                )
            else:
                console.print(
                    "\n[bold]Hello from Deepgram![/bold] Open the link below to "
                    "complete login."
                )
            output.print_info(
                escape(
                    msg.I_LOGIN_PENDING.format(
                        verification_uri=msg.display(device_response.verification_uri),
                        user_code=msg.display(device_response.user_code or ""),
                    )
                ),
                stderr=True,
            )

            if open_browser:
                try:
                    webbrowser.open(verification_uri)
                    console.print("[green]✓[/green] Opened browser for authentication")
                except Exception as e:
                    console.print(
                        f"[yellow]Warning:[/yellow] Could not open browser: {e}"
                    )

            # Poll for token (starts immediately — user may have clicked the link)
            token_response = self._poll_for_token(device_response, identity_url)

            # Store token and get user info
            self._store_token(token_response)

            console.print("\n[green]Key created and stored successfully.[/green]")
            console.print("\nYou are now logged in. Happy coding!")

        except DeviceCodeUnreadable:
            raise
        except Exception as e:
            console.print(f"[red]Error during device flow:[/red] {e}")
            raise AuthenticationError(f"Device flow failed: {e}")

    def _request_device_code(
        self, identity_url: str | None = None
    ) -> DeviceCodeResponse:
        """Request device code from auth provider.

        Returns:
            DeviceCodeResponse with device code and verification URI
        """
        _load_modules("httpx")
        payload = {
            "client_id": CLIENT_ID,
            "hostname": DEVICE_NAME,
            "scopes": "openid profile email",
        }

        try:
            base = identity_url or check_identity_host(self.config)
            response = self.client.post(
                f"{base}/device/code",
                json=payload,
                headers={"Content-Type": "application/json"},
            )

            if response.status_code in (200, 201):
                host = urlsplit(base).hostname or base
                try:
                    body = response.json()
                except ValueError:
                    raise DeviceCodeUnreadable(host) from None
                if not isinstance(body, dict):
                    raise DeviceCodeUnreadable(host)
                try:
                    return DeviceCodeResponse(**body)
                except (ValueError, TypeError):  # incl. pydantic ValidationError
                    raise DeviceCodeUnreadable(host) from None
            else:
                raise AuthenticationError(
                    f"Device code request failed: {response.status_code}"
                )

        except httpx.RequestError as e:
            raise AuthenticationError(f"Network error during device code request: {e}")

    def poll_device_token(
        self, device_code: str, identity_url: str | None = None
    ) -> DevicePoll:
        """Ask the token endpoint once whether ``device_code`` was approved.

        A POST (RFC 8628 §3.4), so the device code never lands in a URL,
        and no device name is sent. Network errors are raised
        (``httpx.RequestError``).
        """
        _load_modules("httpx")
        base = identity_url or check_identity_host(self.config)
        response = self.client.post(
            f"{base}/token",
            data={
                "grant_type": DEVICE_CODE_GRANT,
                "device_code": device_code,
                "client_id": CLIENT_ID,
            },
        )
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            body = {}
        if response.status_code in (200, 201):
            try:
                return DevicePoll("token", token=TokenResponse(**body))
            except ValueError:
                return DevicePoll("error", detail="unusable token response")
        if response.status_code == 429:
            return DevicePoll("slow_down", retry_after=_retry_after(response))
        error = body.get("error")
        if response.status_code >= 500 or error == "temporarily_unavailable":
            # A transient server problem: back off and ask again.
            return DevicePoll(
                "unavailable",
                detail=f"HTTP {response.status_code}",
                retry_after=_retry_after(response),
            )
        state = {
            "authorization_pending": "pending",
            "slow_down": "slow_down",
            "access_denied": "denied",
            "expired_token": "expired",
        }.get(error if isinstance(error, str) else "")
        if state is not None:
            return DevicePoll(state)
        reason = error if isinstance(error, str) else "unknown error"
        return DevicePoll(
            "error", detail=f"HTTP {response.status_code}: {msg.display(reason)[:80]}"
        )

    def _poll_for_token(
        self,
        device_response: DeviceCodeResponse,
        identity_url: str | None = None,
    ) -> TokenResponse:
        """Poll for token using device code until approved, denied or expired."""
        _load_modules("httpx")
        console.print("\n[blue]Waiting for authentication...[/blue]")

        start_time = time.time()
        interval = max(MIN_POLL_INTERVAL, device_response.interval)
        base = identity_url or check_identity_host(self.config)

        from rich.progress import Progress, SpinnerColumn, TextColumn

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            progress.add_task("Waiting for authentication...", total=None)

            while time.time() - start_time < device_response.expires_in:
                try:
                    poll = self.poll_device_token(device_response.device_code, base)
                except httpx.RequestError as e:
                    console.print(f"[red]Network error:[/red] {e}")
                    time.sleep(interval)
                    continue
                if poll.state == "token" and poll.token is not None:
                    return poll.token
                if poll.state == "pending":
                    time.sleep(interval)
                    continue
                if poll.state in ("slow_down", "unavailable"):
                    interval = max(interval + 5, poll.retry_after or 0)
                    left = device_response.expires_in - (time.time() - start_time)
                    time.sleep(max(0.0, min(interval, left)))
                    continue
                if poll.state == "denied":
                    raise AuthenticationError(msg.E_AUTH_DENIED)
                if poll.state == "expired":
                    break
                raise AuthenticationError(str(poll.detail))

        raise AuthenticationError("Authentication timed out")

    @_forgets_resolved
    def _store_token(self, token_response: TokenResponse) -> None:
        """Store authentication token.

        Stores a direct finite Deepgram credential from dx-id. The credential
        expires independently of the OIDC access token and requires a new
        device authorization when it expires.
        """
        _load_modules("keyring")
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
            # Nothing goes to the keyring when the profile can't be saved.
            self.config.check_saveable()
            try:
                keyring.set_password(
                    KEYRING_SERVICE, f"api-key.{profile_name}", api_key
                )
                keyring_available = True
            except Exception as e:
                output.print_warning(
                    escape(
                        f"Could not store in keyring: {e}. "
                        "The API key will be stored in the config file instead."
                    ),
                    stderr=True,
                )

            for keyring_key in (f"jwt.{profile_name}", f"refresh-token.{profile_name}"):
                self._delete_keyring_credential(keyring_key)

            self.config.create_profile(
                profile_name,
                api_key=api_key if not keyring_available else None,
                project_id=token_response.project_id or token_response.pid,
            )
            profile = self.config.get_profile(profile_name)
            profile.dg_token_expires_at = expires_at
            self.config.save()
            if keyring_available:
                console.print(
                    "[green]✓[/green] API key stored securely in system keyring"
                )
            return

        raise AuthenticationError(
            "Deepgram sign-in did not return a finite credential. Run `dg update` and `dg login` again."
        )

    @_forgets_resolved
    def logout(self, keep_config: bool = False) -> None:
        """Logout user and clear credentials.

        Args:
            keep_config: If True, keep profile configuration (project ID, base URL)
                        and only clear credentials. If False, remove profile completely.
        """
        _load_modules("keyring")
        profile_name = self.config.profile or "default"
        # Nothing is cleared from the keyring when the profile can't be saved.
        self.config.check_saveable()

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
                # Only the credential changes; project ID and base URL stay.
                self.config.update_profile(
                    profile_name, api_key=None, dg_token_expires_at=None
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

            # The keyring's key when it has one, else the config file's.
            # Project ID is only stored in config, not keyring.
            project_id = profile.project_id
            try:
                # Bounded off a terminal; a timeout falls back like an error.
                api_key = credentials.keyring_read(f"api-key.{profile_name}")
            except Exception:
                api_key = None
            if not api_key:
                # No entry, as with a key saved only in the config file
                # while a keyring backend (Windows Credential Manager, say)
                # is present.
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
        client = self.__dict__.get("_client")
        if client is not None:
            client.close()
