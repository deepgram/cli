"""Login command for deepctl."""

import dataclasses
import os
import shlex
import shutil
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO
from urllib.parse import urlsplit

import click
from deepctl_core import (
    AuthenticationError,
    AuthManager,
    BaseCommand,
    CodedErrorResult,
    Config,
    CredentialHostError,
    DeepgramClient,
    ProfileInfo,
    ProfilesResult,
    get_output_format,
    is_agentic,
    is_ai_agent,
)
from deepctl_core import messages as msg
from deepctl_core.auth import DeviceCodeUnreadable
from deepctl_core.config import ConfigSaveRefused
from deepctl_core.hosts import (
    SOURCE_ENV,
    SOURCE_FLAG,
    canonical_url,
    check_identity_host,
    evaluate,
    host_entry,
    is_allowlisted,
    is_loopback,
    is_regional,
    parse_origin,
    report,
    user_config_display,
)
from deepctl_core.login_state import (
    MIN_POLL_INTERVAL,
    LoadedPending,
    PendingLogin,
    load_pending,
    remove_pending,
    save_pending,
    state_dir,
    unwritable_dir,
)
from deepctl_core.output import (
    _output_config,
    print_error,
    print_error_hint,
    print_hint,
    print_info,
    print_security_warning,
    print_warning,
    stderr_console,
)
from rich.console import Console
from rich.markup import escape
from rich.prompt import Prompt

if TYPE_CHECKING:
    from deepctl_core.auth import DeviceCodeResponse

from .models import (
    LoginCodedError,
    LoginPendingResult,
    LoginResult,
    LogoutResult,
    WhoamiResult,
)

console = Console()
# Status chrome of the login flows: stderr, never the payload's stdout.
status = stderr_console

_PROG_NAMES = ("dg", "deepgram", "deepctl")
_YES_FLAG = "--yes"
# Options whose value is a key.
_SECRET_OPTIONS = ("--api-key", "-k")


def _interactive_terminal() -> bool:
    """A person can answer a prompt: stdin and stderr are both terminals."""
    return sys.stdin.isatty() and sys.stderr.isatty()


def _prog() -> str | None:
    """``{prog}``: the running executable, as a command.

    The bare name when ``which(name)`` is this same file; else the console
    script's absolute path, quoted. ``None`` when neither applies (``python
    -m``, an unknown launcher), which makes ``next_step`` null.
    """
    argv0 = sys.argv[0] if sys.argv else ""
    name = os.path.basename(argv0)
    if os.name == "nt" and name.lower().endswith(".exe"):
        name = name[:-4]
    if name not in _PROG_NAMES:
        return None
    found = shutil.which(name)
    running = shutil.which(argv0) or argv0
    try:
        if found and os.path.samefile(found, running):
            return name
    except OSError:
        pass
    if not os.path.isfile(running):
        return None
    path = os.path.abspath(running)
    if os.name == "nt":
        return None if shlex.quote(path) != path else path
    return shlex.quote(path)


def _secret_in(argv: list[str]) -> bool:
    """True when ``argv`` holds a key or a URL with userinfo or a query."""
    after_option = False
    for token in argv:
        if after_option:
            return True
        if token in _SECRET_OPTIONS:
            after_option = True
            continue
        if token.startswith("--api-key=") or (
            token.startswith("-k") and not token.startswith("--") and len(token) > 2
        ):
            return True
        if "://" in token:
            try:
                parts = urlsplit(token)
            except ValueError:
                return True
            if "@" in parts.netloc or parts.query:
                return True
    return False


def _rerun_with_yes(prog: str | None) -> str | None:
    """``{prog} {argv} --yes`` as a runnable command, or None."""
    argv = sys.argv[1:]
    if prog is None or _secret_in(argv):
        return None
    tokens = [*argv, _YES_FLAG]
    if any(token.startswith("=") for token in tokens):
        return None  # zsh's EQUALS option would expand it
    if os.name == "nt" and any(shlex.quote(token) != token for token in tokens):
        return None
    step = f"{prog} {shlex.join(tokens)}"
    return step if shlex.join(shlex.split(step)) == step else None


def _hard_signal() -> bool:
    """Hard agent mode: an AI agent marked the environment, or ``DG_AGENT=1``
    says one runs dg (``DG_AGENT=0`` turns it off)."""
    from deepctl_core.agents import override

    forced = override()
    if forced is not None:
        return forced == "1"
    return is_ai_agent()


def _hint_for(
    human: str,
    agent: str,
    next_step: str | None,
    *,
    agent_null: str | None = None,
    automation_null: str | None = None,
) -> str:
    """The hint for this run's mode, null-aware."""
    if _hard_signal():
        return agent_null if next_step is None and agent_null else agent
    if _output_config["agentic"]:
        if next_step is None:
            return automation_null or agent_null or agent
        return agent
    return human


def _coded_error(
    error: str,
    code: str,
    hint: str,
    next_step: str | None,
    details: dict[str, Any] | None = None,
) -> LoginCodedError:
    """Report a coded error on stderr and return its payload."""
    print_error(escape(error))
    print_hint(escape(hint))
    return LoginCodedError(
        error=error, code=code, hint=hint, next_step=next_step, details=details
    )


# Mode A stderr grammar: every line a caller reads starts with
# one of these. Anything else a login flow prints is status chrome.
_MODE_A_PREFIXES = (
    "ERROR: ",
    "WARN: ",
    "INFO: ",
    "HINT: ",
    "OK: ",
    "DEPRECATED: ",
)


class _ModeAStderr:
    """``sys.stderr`` in mode A: each write that starts a line is kept or
    dropped as a whole. With ``verbose``, chrome is kept as ``INFO:`` lines
    instead of dropped."""

    def __init__(self, stream: TextIO, verbose: bool = False) -> None:
        self._stream = stream
        self._verbose = verbose
        self._keep = True
        self._line_start = True

    def write(self, text: str) -> int:
        if not text:
            return 0
        if self._line_start:
            first = text.lstrip("\n").split("\n", 1)[0]
            prefixed = first.startswith(_MODE_A_PREFIXES)
            self._keep = bool(first.strip()) and (prefixed or self._verbose)
            if self._keep and not prefixed:
                lines = [line for line in text.strip("\n").split("\n") if line.strip()]
                text = "".join(f"INFO: {line.strip()}\n" for line in lines)
        if self._keep:
            self._stream.write(text)
        self._line_start = text.endswith("\n")
        return len(text)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


@contextmanager
def _without_chrome() -> Iterator[None]:
    """In mode A, drop status chrome from stderr (``INFO:`` lines with
    --verbose)."""
    if not _output_config["agentic"]:
        yield
        return
    real = sys.stderr
    sys.stderr = _ModeAStderr(real, verbose=bool(_output_config["verbose"]))
    try:
        yield
    finally:
        sys.stderr = real


def _shown_prog(prog: str | None) -> str:
    return prog or _PROG_NAMES[0]


def _state_unwritable(directory: Path) -> LoginCodedError:
    shown = _shown_prog(_prog())
    return _coded_error(
        msg.E_STATE_UNWRITABLE.format(prog=shown, dir=msg.display_path(directory)),
        msg.CODE_STATE_UNWRITABLE,
        _hint_for(
            msg.H_STATE_UNWRITABLE.format(prog=shown),
            msg.A_STATE_UNWRITABLE.format(prog=shown),
            None,
        ),
        None,
        {"dir": str(directory)},
    )


def _preflight(config: Config) -> Any | None:
    """Stop before any request when the login couldn't be saved.

    In order: the state or credential directory isn't writable
    (``E-STATE-UNWRITABLE``); then, in an AI agent session, the user config
    is one dg won't save over (``E-CFG-SAVE-UNTRUSTED``). Both
    come before the confirmations, so an agent is never told to pass --yes
    for a save that would fail.
    """
    config_path = Path(config.config_path)
    for directory, file in (
        (state_dir(), None),
        (config_path.parent, config_path),
    ):
        bad = unwritable_dir(directory, file)
        if bad is not None:
            return _state_unwritable(bad)
    try:
        config.check_saveable()
    except ConfigSaveRefused as e:
        shown = _shown_prog(_prog())
        return _coded_error(
            msg.E_CFG_SAVE_UNTRUSTED.format(
                prog=shown,
                path=msg.display_path(config_path),
                why=msg.W_CFG_UNTRUSTED_WHY[e.why],
            ),
            msg.CODE_STATE_UNWRITABLE,
            msg.W_CFG_UNTRUSTED_FILE_HINT,
            None,
            {"reason": "untrusted_config", "path": str(config_path)},
        )
    except OSError as e:
        # A save that can't happen for another reason (not a regular file,
        # content that wouldn't load back): stop before sending anything.
        print_error(escape(f"Login failed: {e}"))
        print_error_hint(e)
        return LoginResult(
            status="error",
            message=str(e),
            profile=config.profile or "default",
            api_key_masked=None,
        )
    return None


def _device_code_unreadable(host: str) -> LoginCodedError:
    """The identity provider's device-code answer couldn't be read."""
    shown = _shown_prog(_prog())
    values = {"host": msg.display(host), "prog": shown}
    return _coded_error(
        msg.E_NETWORK_UNREADABLE.format(**values),
        msg.CODE_NETWORK_UNAVAILABLE,
        _hint_for(
            msg.H_NETWORK_UNREADABLE.format(**values),
            msg.A_NETWORK_UNREADABLE,
            None,
        ),
        None,
        {"host": msg.display(host), "reason": "unreadable response"},
    )


def _confirmation_required(error: str, next_step: str | None, hint: str) -> Any:
    return _coded_error(
        error,
        msg.CODE_CONFIRMATION_REQUIRED,
        hint,
        next_step,
        {"flag": _YES_FLAG},
    )


class LoginCommand(BaseCommand):
    """Login command for authenticating with Deepgram."""

    name = "login"
    help = "Log in to Deepgram with browser-based authentication or API key"
    short_help = "Log in to Deepgram"

    # Set by --save-base-url once a person confirmed it.
    _saved_origin: str | None = None
    _added_host: str | None = None

    # Login doesn't require existing auth
    requires_auth = False
    requires_project = False
    ci_friendly = True

    examples = [
        "dg login",
        "dg login --no-browser",
        "dg login --complete",
        "dg login --api-key YOUR_API_KEY",
        "dg login --api-key YOUR_KEY --project-id YOUR_PROJECT",
        "dg login --profile staging --api-key YOUR_KEY",
        "dg --base-url https://api.eu.deepgram.com login --save-base-url",
    ]
    agent_help = (
        "Log in to Deepgram. In an AI agent session, dg login prints a link "
        "and code and exits: give both to the user, then run dg login "
        "--complete (add --wait to keep checking). Never put an API key on "
        "the command line. Profiles keep separate API keys and project IDs."
    )

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
                "names": ["--yes", "-y"],
                "help": (
                    "Save without asking, even if DEEPGRAM_API_KEY is set or the "
                    "profile already has a saved login (--save-base-url still asks)"
                ),
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--force-write", "-f"],
                "help": "Same as --yes",
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
            {
                "names": ["--no-browser"],
                "help": (
                    "Print the login link and code instead of opening a browser, "
                    "then exit; finish with --complete, or add --wait to wait here"
                ),
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--complete"],
                "help": (
                    "Finish a pending browser login: check once whether it was "
                    "approved, and save the key if it was (add --wait to keep "
                    "checking)"
                ),
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--wait"],
                "help": (
                    "Wait until the browser login is approved (the default in a "
                    "terminal), instead of exiting with the link and code; with "
                    "--complete, keep checking"
                ),
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--save-base-url"],
                "help": (
                    "Save the --base-url URL as this profile's API URL "
                    "(asks for confirmation in a terminal)"
                ),
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
        """Handle login command."""
        with _without_chrome():
            return self._login(config, auth_manager, **kwargs)

    def _login(self, config: Config, auth_manager: AuthManager, **kwargs: Any) -> Any:
        api_key = kwargs.get("api_key")
        project_id = kwargs.get("project_id")
        force_write = bool(kwargs.get("yes") or kwargs.get("force_write"))
        profile = kwargs.get("profile")
        save_base_url = kwargs.get("save_base_url", False)
        complete = bool(kwargs.get("complete"))
        wait = bool(kwargs.get("wait"))
        no_browser = bool(kwargs.get("no_browser"))

        # Set profile if provided
        if profile:
            config.profile = profile

        current_profile = config.profile or "default"

        if complete:
            if api_key:
                raise click.UsageError("--complete can't be used with --api-key.")
            if save_base_url:
                raise click.UsageError("--complete can't be used with --save-base-url.")
            return self._complete(config, auth_manager, wait=wait)

        # Nothing is asked or sent when the login couldn't be saved.
        stop = _preflight(config)
        if stop is not None:
            return stop

        # Decide where the key may go before doing anything else. A base URL
        # from --base-url or DEEPGRAM_BASE_URL is used for this login only;
        # --save-base-url, confirmed by a person, is the one way to save it.
        if save_base_url:
            stop = self._confirm_save_base_url(config, current_profile)
            if stop is not None:
                return stop
            if not api_key:
                has_key, _ = auth_manager.has_profile_credentials(current_profile)
                if has_key:
                    # The confirmed URL is the change; keep the stored key.
                    return self._save_base_url_for_existing_key(
                        config, auth_manager, current_profile
                    )
        else:
            try:
                auth_manager.check_base_url()
            except CredentialHostError as e:
                return CodedErrorResult(error=e.error, code=e.code, hint=e.hint)

        # Check for environment variables
        has_env_key, has_env_project = auth_manager.has_env_credentials()

        if has_env_key and not force_write:
            if not self._can_ask():
                # Nobody can answer: say which flag does.
                prog = _prog()
                next_step = None if api_key else _rerun_with_yes(prog)
                return _confirmation_required(
                    msg.E_CONFIRM_ENVKEY.format(prog=prog or _PROG_NAMES[0]),
                    next_step,
                    _hint_for(
                        msg.H_CONFIRM_YES,
                        msg.A_CONFIRM_ENVKEY,
                        next_step,
                        agent_null=msg.A_CONFIRM_ENVKEY_NULL.format(
                            prog=prog or _PROG_NAMES[0]
                        ),
                        automation_null=msg.C_CONFIRM_ENVKEY_NULL,
                    ),
                )
            status.print(
                "[yellow]Environment variable DEEPGRAM_API_KEY is set.[/yellow]"
            )
            if has_env_project:
                status.print(
                    "[yellow]Environment variable DEEPGRAM_PROJECT_ID is also set.[/yellow]\n"
                    "[green]Commands will work using these environment variables.[/green]"
                )
            else:
                status.print(
                    "[yellow]Note: DEEPGRAM_PROJECT_ID is not set. "
                    "You'll need to set it or provide it via --project-id flag.[/yellow]"
                )

            status.print(
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
        has_profile_key, _has_profile_project = auth_manager.has_profile_credentials(
            current_profile
        )

        if has_profile_key and not force_write:
            if not self._can_ask():
                next_step = _rerun_with_yes(_prog())
                return _confirmation_required(
                    msg.E_CONFIRM_RELOGIN.format(profile=msg.display(current_profile)),
                    next_step,
                    _hint_for(
                        msg.H_CONFIRM_YES,
                        msg.A_CONFIRM_YES,
                        next_step,
                        agent_null=msg.A_CONFIRM_YES_NULL,
                        automation_null=msg.C_CONFIRM_YES_NULL,
                    ),
                )
            status.print(
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
        result: LoginResult | CodedErrorResult
        if api_key:
            result = self._cli_auth(
                config, auth_manager, str(api_key), project_id, force_write
            )
        else:
            result = self._web_auth(
                config, auth_manager, wait=wait, no_browser=no_browser
            )

        if result.status == "success":
            self._report_base_url(config, current_profile)

        # Post-login: offer skills setup if login succeeded and no skills installed
        if result.status == "success":
            self._maybe_prompt_skills_setup()

        return result

    def _pending_mode(self, *, wait: bool, no_browser: bool) -> bool:
        """Return the link and code at once instead of waiting:
        under a hard agent signal, with --no-browser, or when neither stdin
        nor stdout is a terminal. --wait always waits."""
        if wait:
            return False
        if no_browser or _hard_signal():
            return True
        return not sys.stdin.isatty() and not sys.stdout.isatty()

    def _can_ask(self) -> bool:
        """A person can answer login's confirmations here."""
        return self.can_prompt() and _interactive_terminal() and not is_ai_agent()

    def _confirm_save_base_url(self, config: Config, profile: str) -> Any | None:
        """Ask a person to approve saving the base URL.

        Returns a result to stop with, or ``None`` to continue with the URL
        approved for saving.
        """
        override = config.base_url_override
        if override is None or override[1] not in (SOURCE_FLAG, SOURCE_ENV):
            raise click.UsageError(msg.E_SAVE_NOURL)
        url, source = override

        agent = is_agentic() or is_ai_agent()
        if agent or not _interactive_terminal():
            hint = msg.E_SAVE_NOTTY_AGENT_HINT if agent else msg.E_SAVE_NOTTY_HINT
            print_error(msg.E_SAVE_NOTTY)
            print_hint(hint)
            return CodedErrorResult(
                error=msg.E_SAVE_NOTTY,
                code=msg.CODE_CONFIRMATION_REQUIRED,
                hint=hint,
            )

        # Only the default user config can approve hosts.
        if not config.trusted_file:
            raise click.UsageError(
                msg.E_SAVE_CUSTOM_CONFIG.format(user_config=user_config_display())
            )

        # Only https, or http to a loopback host, can be saved.
        origin = parse_origin(url)
        if origin is None:
            raise click.UsageError(msg.E_SAVE_NOURL)
        if not origin.secure and not is_loopback(origin.host):
            try:
                evaluate(url, source, profile=profile, agentic=False)
            except CredentialHostError as e:
                report(e)
                return CodedErrorResult(error=e.error, code=e.code, hint=e.hint)

        host = msg.display(origin.host)
        shown_origin = msg.display(origin.origin)
        needs_allowlist = not is_regional(origin) and not is_allowlisted(
            origin, config.allowed_hosts()
        )
        if needs_allowlist:
            click.echo(msg.P_SAVE_BASEURL_FOREIGN.format(host=host), err=True)
        try:
            confirmed = click.confirm(
                msg.P_SAVE_BASEURL.format(
                    origin=shown_origin, profile=msg.display(profile)
                ),
                default=False,
                err=True,
            )
        except click.Abort:
            confirmed = False
        if not confirmed:
            print_error(msg.E_SAVE_DECLINED.format(host=host))
            return LoginResult(
                status="cancelled",
                message=msg.E_SAVE_DECLINED.format(host=host),
                profile=profile,
                api_key_masked=None,
            )

        config.approve_base_url(profile, canonical_url(url))
        if needs_allowlist:
            self._added_host = host_entry(origin)
            config.add_allowed_host(self._added_host)
        self._saved_origin = shown_origin
        return None

    def _save_base_url_for_existing_key(
        self, config: Config, auth_manager: AuthManager, profile: str
    ) -> Any:
        """Save the approved base URL for a profile that already has a key.

        The stored key is checked against the approved URL first; nothing
        is saved if that fails.
        """
        api_key = auth_manager.get_api_key(ignore_env=True)
        success, message, _ = auth_manager.verify_api_key(api_key)
        if not success:
            print_error(escape(message))
            return LoginResult(
                status="error",
                message=message,
                profile=profile,
                api_key_masked=None,
            )
        config.save()
        self._report_base_url(config, profile)
        return LoginResult(
            status="success",
            message=msg.S_SAVE_BASEURL.format(
                origin=self._saved_origin, profile=msg.display(profile)
            ),
            profile=profile,
            api_key_masked=f"****{api_key[-4:]}" if api_key else None,
            config_path=str(config.config_path),
        )

    def _report_base_url(self, config: Config, profile: str) -> None:
        """Tell the user whether the base URL used for login was saved."""
        if _output_config["quiet"]:
            return
        if self._saved_origin:
            text = msg.S_SAVE_BASEURL.format(
                origin=self._saved_origin, profile=msg.display(profile)
            )
            if self._added_host:
                text += msg.S_SAVE_BASEURL_ADDED.format(
                    host=msg.display(self._added_host)
                )
            print_info(escape(text), stderr=True)
            return
        unsaved = config.unsaved_base_url(profile)
        if not isinstance(unsaved, tuple):
            return
        used = parse_origin(unsaved[0])
        was = parse_origin(unsaved[1])
        saved = unsaved[1]
        if used is None or (was is not None and used.origin == was.origin):
            return
        print_info(
            escape(
                msg.I_LOGIN_NOT_SAVED.format(
                    origin=msg.display(used.origin),
                    profile=msg.display(profile),
                    saved_origin=msg.display(was.origin if was else saved),
                )
            ),
            stderr=True,
        )
        print_hint(
            escape(msg.I_LOGIN_NOT_SAVED_HINT.format(origin=msg.display(used.origin)))
        )

    def _maybe_prompt_skills_setup(self) -> None:
        """After login, offer interactive skills setup for detected AI tools."""
        import sys

        if not sys.stdout.isatty() or not getattr(self, "_guided", True):
            return  # Non-interactive — skip

        try:
            from deepctl_core.skill_generator import (
                detect_ai_clis,
                get_skills_state,
            )

            state = get_skills_state()
            if state.get("installed_skills"):
                return  # Skills already installed

            detected = detect_ai_clis()
            if not detected:
                return  # No AI tools found

            console.print(
                "\n[cyan]AI coding tools detected:[/cyan]"
                "\n[dim]Deepgram skills teach AI assistants how to use "
                "our APIs, docs, and this CLI.[/dim]\n"
            )

            # Show numbered list with all detected pre-selected
            for i, gen in enumerate(detected, 1):
                console.print(f"  [green]{i}.[/green] {gen.display_name}")
            console.print()

            raw = Prompt.ask(
                "Install skills for (comma-separated numbers, [bold]all[/bold], or [bold]none[/bold])",
                default="all",
            )
            raw = raw.strip().lower()

            if raw in ("none", "n", "0", ""):
                console.print("[dim]You can run 'dg skills setup' later.[/dim]")
                return

            if raw == "all":
                selected = list(detected)
            else:
                indices: set[int] = set()
                for part in raw.split(","):
                    part = part.strip()
                    if part.isdigit():
                        idx = int(part)
                        if 1 <= idx <= len(detected):
                            indices.add(idx - 1)
                selected = [detected[i] for i in sorted(indices)]

            if not selected:
                console.print("[dim]No tools selected.[/dim]")
                return

            # The path 'dg skills install' uses (B3): one fetch, every tool
            # preflighted, each tool recorded as it lands.
            from deepctl_core import skill_generator as sg
            from deepctl_core.skill_bundle import resolve_skills_ref

            console.print("\n[blue]Installing Deepgram skills...[/blue]")
            for gen in selected:
                if gen.skills_root() is None:
                    print_warning(escape(sg._msg("E15", gen)), stderr=True)
            ref, count = resolve_skills_ref(), 0
            for gen, paths, leftover in sg.install_for([(g, ref) for g in selected]):
                count += len(paths)
                line = f"{gen.display_name} → {gen.skills_root()} ({len(paths)} skills)"
                console.print(f"  [green]✓[/green] {escape(line)}")
                if leftover:
                    print_warning(escape(sg._msg("E12", staging=leftover)), stderr=True)
            if count:
                console.print(
                    "\n[green]Skills installed![/green] "
                    "[dim]Run 'dg skills update' to get newer skills.[/dim]"
                )
        except Exception as exc:  # Never fails the login, and never silent (B3).
            from deepctl_core.skill_generator import warn_install_failure

            warn_install_failure(
                "Skills setup did not finish",
                exc,
                "run 'dg skills install' to try again",
            )

    def _cli_auth(
        self,
        config: Config,
        auth_manager: AuthManager,
        api_key: str,
        project_id: str | None,
        force_write: bool,
    ) -> LoginResult:
        """Handle CLI authentication with API key."""
        status.print("[blue]Configuring CLI with API key...[/blue]")

        # The key's format isn't checked: Deepgram keys have no fixed prefix,
        # and login_with_api_key verifies the key with the API.

        # Validate project ID is provided with API key
        if not project_id:
            status.print("[yellow]Warning:[/yellow] Project ID not provided")
            status.print(
                "You can set it later with: "
                f"{_shown_prog(_prog())} login --project-id <project_id>"
            )
            status.print("Or use environment variable: DEEPGRAM_PROJECT_ID")

        # No need to check for existing config - we handle that in the main flow

        try:
            # Store credentials (verification happens inside
            # login_with_api_key)
            auth_manager.login_with_api_key(api_key, project_id or None, force_write)

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
            print_error(escape(f"Login failed: {e}"))
            return LoginResult(
                status="error",
                message=str(e),
                profile=config.profile or "default",
                api_key_masked=None,
            )

        except Exception as e:
            print_error(escape(f"Login failed: {e}"))
            print_error_hint(e)
            return LoginResult(
                status="error",
                message=str(e),
                profile=config.profile or "default",
                api_key_masked=None,
            )

    def _web_auth(
        self,
        config: Config,
        auth_manager: AuthManager,
        *,
        wait: bool = False,
        no_browser: bool = False,
    ) -> Any:
        """Handle web authentication with device flow."""
        # The identity host mints the key: check it before anything else, and
        # keep a refusal off stdout apart from the structured result.
        try:
            identity_url = check_identity_host(config)
        except CredentialHostError as e:
            return CodedErrorResult(error=e.error, code=e.code, hint=e.hint)

        if self._pending_mode(wait=wait, no_browser=no_browser):
            return self._start_pending(config, auth_manager, identity_url)

        status.print("[blue]Starting web authentication...[/blue]")

        try:
            open_browser = not (no_browser or _hard_signal())
            if _output_config["agentic"]:
                # Mode A: the link and code as an INFO line; chrome is dropped.
                device = auth_manager._request_device_code(identity_url)
                self._announce(device)
                if open_browser:
                    _open_browser(device)
                token = auth_manager._poll_for_token(device, identity_url)
                auth_manager._store_token(token)
            else:
                auth_manager.login_with_device_flow(open_browser=open_browser)

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

        except DeviceCodeUnreadable as e:
            return _device_code_unreadable(e.host)

        except AuthenticationError as e:
            print_error(escape(f"Login failed: {e}"))
            return LoginResult(
                status="error",
                message=str(e),
                profile=config.profile or "default",
                api_key_masked=None,
            )

        except KeyboardInterrupt:
            status.print("\n[yellow]Authentication cancelled by user[/yellow]")
            return LoginResult(
                status="cancelled",
                message="Login cancelled by user",
                profile=config.profile or "default",
                api_key_masked=None,
            )

        except Exception as e:
            print_error(escape(f"Error during web authentication: {e}"))
            print_error_hint(e)
            return LoginResult(
                status="error",
                message=str(e),
                profile=config.profile or "default",
                api_key_masked=None,
            )

    def _announce(self, device: "DeviceCodeResponse") -> None:
        """Print the link and code as an INFO line (stderr)."""
        print_info(
            escape(
                msg.I_LOGIN_PENDING.format(
                    verification_uri=msg.display(device.verification_uri),
                    user_code=msg.display(device.user_code or ""),
                )
            ),
            stderr=True,
        )

    def _start_pending(
        self, config: Config, auth_manager: AuthManager, identity_url: str
    ) -> Any:
        """Get a device code, save it for --complete, and return the link and
        code at once (exit 0)."""
        profile_name = config.profile or "default"
        try:
            device = auth_manager._request_device_code(identity_url)
            if not device.user_code:
                raise AuthenticationError(
                    "the login service didn't return a code. Try again in a moment."
                )
        except DeviceCodeUnreadable as e:
            return _device_code_unreadable(e.host)
        except AuthenticationError as e:
            print_error(escape(f"Login failed: {e}"))
            return LoginResult(
                status="error",
                message=str(e),
                profile=profile_name,
                api_key_masked=None,
            )
        pending = PendingLogin(
            device_code=device.device_code,
            user_code=device.user_code,
            verification_uri=device.verification_uri,
            interval=max(MIN_POLL_INTERVAL, device.interval),
            expires_at=time.time() + device.expires_in,
            host=identity_url,
            profile=profile_name,
        )
        try:
            save_pending(pending)
        except OSError:
            return _state_unwritable(state_dir())

        prog = _prog()
        shown = _shown_prog(prog)
        next_step = f"{prog} login --complete" if prog else None
        self._announce(device)
        hint = (
            msg.A_LOGIN_PENDING
            if _hard_signal() or _output_config["agentic"]
            else msg.H_LOGIN_PENDING
        )
        print_hint(escape(hint.format(prog=shown)))
        return LoginPendingResult(
            message=msg.M_LOGIN_PENDING,
            verification_uri=device.verification_uri,
            user_code=device.user_code,
            expires_in=device.expires_in,
            next_step=next_step,
        )

    def _complete(
        self, config: Config, auth_manager: AuthManager, *, wait: bool
    ) -> Any:
        """``dg login --complete``: poll once (or, with --wait, until done)
        for the saved device code, and save the key once it is approved."""
        import httpx
        from deepctl_core import transport

        stop = _preflight(config)
        if stop is not None:
            return stop
        prog = _prog()
        shown = _shown_prog(prog)
        loaded = load_pending(agent=is_ai_agent())
        if loaded.ignored_why is not None:
            print_security_warning(
                escape(
                    msg.W_LOGIN_PENDING_UNTRUSTED.format(
                        path=msg.display_path(loaded.path),
                        prog=shown,
                        why=msg.W_CFG_UNTRUSTED_WHY[loaded.ignored_why],
                    )
                )
            )
        pending = loaded.pending
        if pending is None:
            return self._auth_expired(prog)
        try:
            identity_url = check_identity_host(config)
        except CredentialHostError as e:
            return CodedErrorResult(error=e.error, code=e.code, hint=e.hint)
        if canonical_url(pending.host) != identity_url:
            # Issued by another login host: never send its code elsewhere.
            return self._auth_expired(prog)

        explicit = getattr(config, "_explicit_profile", None)
        if explicit and explicit != pending.profile:
            # The pending login was confirmed for its own profile.
            raise click.UsageError(
                f'The pending login is for profile "{msg.display(pending.profile)}". '
                "Run --complete without --profile, or start a new login for "
                f'"{msg.display(explicit)}" with dg login --profile.'
            )
        config.profile = pending.profile
        interval = pending.interval
        while True:
            if pending.expired():
                remove_pending(loaded)
                return self._auth_expired(prog)
            try:
                poll = auth_manager.poll_device_token(pending.device_code, identity_url)
            except httpx.RequestError as e:
                # The coded network error every other request gets.
                mapped = transport.map_exception(e, (pending.device_code,))
                if mapped is not None:
                    raise mapped from e
                text = f"Network error while completing the login: {e}"
                print_error(escape(text))
                return LoginResult(
                    status="error",
                    message=text,
                    profile=pending.profile,
                    api_key_masked=None,
                )
            if poll.state == "token" and poll.token is not None:
                return self._finish(config, auth_manager, loaded, poll.token)
            if poll.state == "unavailable" and not wait:
                text = (
                    f"Couldn't complete the login: {poll.detail}. "
                    f"Run {shown} login to start again."
                )
                print_error(escape(text))
                return LoginResult(
                    status="error",
                    message=text,
                    profile=pending.profile,
                    api_key_masked=None,
                )
            if poll.state in ("pending", "slow_down", "unavailable"):
                if poll.state in ("slow_down", "unavailable"):
                    interval = max(interval + 5, poll.retry_after or 0)
                    pending = dataclasses.replace(pending, interval=interval)
                    try:
                        save_pending(pending)
                        loaded = load_pending(agent=is_ai_agent())
                    except OSError:
                        pass  # the next --complete waits the old interval
                if wait:
                    time.sleep(
                        max(0.0, min(interval, pending.expires_at - time.time()))
                    )
                    continue
                return self._auth_pending(prog, pending)
            if poll.state == "denied":
                remove_pending(loaded)
                return _coded_error(
                    msg.E_AUTH_DENIED,
                    msg.CODE_AUTH_DENIED,
                    _hint_for(
                        msg.H_AUTH_DENIED.format(prog=shown),
                        msg.A_AUTH_DENIED,
                        None,
                    ),
                    None,
                )
            if poll.state == "expired":
                remove_pending(loaded)
                return self._auth_expired(prog)
            text = (
                f"Couldn't complete the login: {poll.detail}. "
                f"Run {shown} login to start again."
            )
            print_error(escape(text))
            return LoginResult(
                status="error",
                message=text,
                profile=pending.profile,
                api_key_masked=None,
            )

    def _finish(
        self,
        config: Config,
        auth_manager: AuthManager,
        loaded: LoadedPending,
        token: Any,
    ) -> Any:
        """Save the approved key (keyring only once the save can happen)."""
        profile_name = config.profile or "default"
        try:
            auth_manager._store_token(token)
            config._config.active_profile = profile_name
            config.save()
        except Exception as e:
            print_error(escape(f"Login failed: {e}"))
            print_error_hint(e)
            return LoginResult(
                status="error",
                message=str(e),
                profile=profile_name,
                api_key_masked=None,
            )
        finally:
            # The code is used up either way.
            remove_pending(loaded)
        key = token.dg_token or ""
        return LoginResult(
            status="success",
            message="Successfully logged in via web authentication",
            profile=profile_name,
            api_key_masked=f"****{key[-4:]}" if key else None,
            project_id=token.project_id or token.pid,
            config_path=str(config.config_path),
        )

    def _auth_expired(self, prog: str | None) -> LoginCodedError:
        shown = _shown_prog(prog)
        return _coded_error(
            msg.E_AUTH_EXPIRED,
            msg.CODE_AUTH_EXPIRED,
            _hint_for(
                msg.H_AUTH_EXPIRED.format(prog=shown),
                msg.A_AUTH_EXPIRED.format(prog=shown),
                f"{prog} login" if prog else None,
            ),
            f"{prog} login" if prog else None,
        )

    def _auth_pending(self, prog: str | None, pending: PendingLogin) -> LoginCodedError:
        shown = _shown_prog(prog)
        values = {
            "prog": shown,
            "retry_after": pending.interval,
            "verification_uri": msg.display(pending.verification_uri),
            "user_code": msg.display(pending.user_code),
        }
        next_step = f"{prog} login --complete" if prog else None
        return _coded_error(
            msg.E_AUTH_PENDING,
            msg.CODE_AUTH_PENDING,
            _hint_for(
                msg.H_AUTH_PENDING.format(**values),
                msg.A_AUTH_PENDING.format(**values),
                next_step,
            ),
            next_step,
            {
                "retry_after": pending.interval,
                "verification_uri": pending.verification_uri,
                "user_code": pending.user_code,
            },
        )


def _open_browser(device: "DeviceCodeResponse") -> None:
    import webbrowser

    try:
        webbrowser.open(device.verification_uri_complete or device.verification_uri)
    except Exception as e:
        print_warning(escape(f"Could not open a browser: {e}"), stderr=True)


class LogoutCommand(BaseCommand):
    """Logout command for clearing authentication."""

    name = "logout"
    help = "Log out and clear stored credentials"
    short_help = "Log out of Deepgram"

    # Logout doesn't require existing auth
    requires_auth = False

    examples = [
        "dg logout",
        "dg logout --profile staging",
    ]
    agent_help = (
        "Clear stored Deepgram credentials. Removes the API key from the "
        "system keyring for the active or specified profile."
    )
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
                    message=(f"Successfully logged out from profile: {profile_name}"),
                    profile=profile_name,
                )

        except Exception as e:
            console.print(f"[red]Error during logout:[/red] {e}")
            print_error_hint(e)
            return LogoutResult(status="error", message=str(e))


class ProfilesCommand(BaseCommand):
    """Command to manage authentication profiles."""

    name = "profiles"
    help = "Manage authentication profiles"
    short_help = "Manage profiles"

    # Profiles command doesn't require auth
    requires_auth = False

    examples = [
        "dg profiles --list",
        "dg profiles --current",
        "dg profiles --switch staging",
    ]
    agent_help = (
        "Manage named authentication profiles. Each profile stores a "
        "separate API key and project ID, useful for switching between "
        "development, staging, and production environments."
    )
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
                    " (current)" if name == (config.profile or "default") else ""
                )
                console.print(f"  • {name}{current_marker}")
                console.print(f"    API Key: {info.api_key or 'Not set'}")
                console.print(f"    Project ID: {info.project_id or 'Not set'}")
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
            console.print(f"[dim]API Key:[/dim] {profile_info.api_key or 'Not set'}")
            console.print(
                f"[dim]Project ID:[/dim] {profile_info.project_id or 'Not set'}"
            )
            console.print(f"[dim]Base URL:[/dim] {profile_info.base_url or 'Not set'}")

            return ProfilesResult(
                status="success",
                current_profile=current_profile,
                profiles={current_profile: profile_info},
            )

        elif switch_profile:
            profile_names = config.list_profiles()

            if switch_profile not in profile_names:
                console.print(f"[red]Profile '{switch_profile}' not found[/red]")
                return ProfilesResult(
                    status="error",
                    message=f"Profile '{switch_profile}' not found",
                )

            # Require re-authentication when switching profiles
            console.print(f"[blue]Switching to profile:[/blue] {switch_profile}")
            console.print(
                "[dim]Re-authentication required to confirm access to credentials[/dim]"
            )

            # Try to get API key from keyring to verify access
            try:
                from deepctl_core import credentials

                # Bounded off a terminal (2 s); no answer counts as no entry.
                api_key = credentials.keyring_get(f"api-key.{switch_profile}")

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

                console.print(f"[green]✓[/green] Switched to profile: {switch_profile}")

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
                print_error_hint(e)
                return ProfilesResult(
                    status="error",
                    message=f"Could not access credentials: {e}",
                )

        else:
            # Default behavior - show current profile
            return self.handle(config, auth_manager, client, current=True)


class WhoamiCommand(BaseCommand):
    """Show current authentication status."""

    name = "whoami"
    help = "Show current authentication status"
    short_help = "Show auth status"

    requires_auth = False
    requires_project = False
    ci_friendly = True

    examples = [
        "dg whoami",
        "dg whoami --profile staging",
    ]
    agent_help = (
        "Show current authentication state: which profile is active, "
        "masked API key with its storage source (system keyring, config "
        "file, or environment variable), and configured project ID."
    )

    def get_arguments(self) -> list[dict[str, Any]]:
        """Get command arguments."""
        return [
            {
                "names": ["--profile"],
                "help": "Profile to inspect (default: active profile)",
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
        """Handle whoami command."""
        if kwargs.get("profile"):
            config.profile = kwargs["profile"]

        profile_name = config.profile or "default"
        profile_cfg = config.get_profile(profile_name)

        # The key and its exact storage source, from the one resolver (its
        # key_source uses these same words): one bounded keyring read, and an
        # expired finite profile credential doesn't mask the environment's.
        # Like before, --api-key isn't a storage source here. Read-only, as in
        # 0.4.2: an expired credential's keyring entry stays (prune=False).
        resolved = auth_manager.resolve(
            profile=profile_name, use_flags=False, prune=False
        )
        api_key = resolved.api_key
        key_source = resolved.key_source
        has_profile_key = resolved.profile_has_key

        authenticated = auth_manager.is_authenticated()
        masked: str | None = None
        if api_key:
            masked = f"{api_key[:4]}****{api_key[-4:]}" if len(api_key) > 8 else "****"

        project_id = auth_manager.get_project_id()
        from deepctl_core.hosts import DEFAULT_BASE_URL

        base_url = profile_cfg.base_url or DEFAULT_BASE_URL

        # Render the human block only in default mode. For json/yaml/csv the
        # framework serialises the returned result to stdout, so printing the
        # block here would corrupt that output for piping (the #97 pattern).
        if get_output_format() == "default":
            if not authenticated:
                console.print(
                    "[yellow]Not logged in.[/yellow] Run 'dg login' to authenticate."
                )
            else:
                console.print("[green]✓[/green] Authenticated")
                console.print(f"  Profile:    {profile_name}")
                console.print(f"  API Key:    {masked}  [dim]({key_source})[/dim]")
                console.print(
                    f"  Project ID: {project_id}"
                    if project_id
                    else "  Project ID: [dim]not set[/dim]"
                )
                if base_url != DEFAULT_BASE_URL:
                    console.print(f"  Base URL:   {base_url}")
                if profile_cfg.dg_token_expires_at and has_profile_key:
                    console.print(f"  Expires:    {profile_cfg.dg_token_expires_at}")

        return WhoamiResult(
            authenticated=authenticated,
            profile=profile_name,
            api_key_masked=masked,
            key_source=key_source,
            project_id=project_id,
            base_url=base_url,
            credential_expires_at=(
                profile_cfg.dg_token_expires_at if has_profile_key else None
            ),
        )
