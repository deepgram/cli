"""Sentry-backed telemetry client: unexpected errors only, allowlisted fields.

This version has no built-in destination, so it sends no telemetry. A
report can go only to a DEEPCTL_TELEMETRY_DSN override on a loopback
address, which exists for tests and local sinks. Without one, telemetry is
off: no hooks are installed and the SDK is never imported.

The SDK is never imported on the success path. ``init_telemetry`` only
records the enabled decision and installs stdlib exception hooks;
``report_exception`` imports and initializes the SDK when an error actually
reaches it, sends one event, and flushes within a fixed time budget.
"""

from __future__ import annotations

import os
import platform
import re
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from types import TracebackType

    from deepctl_core import Config
    from sentry_sdk.types import Event, Hint


# Build switch. Set to False to ship a build in which telemetry is inert:
# no hooks are installed, the SDK is never imported, DEEPCTL_TELEMETRY_DSN is
# ignored, and `--help` says this version of dg sends no telemetry.
TELEMETRY_BUILD_ENABLED = True

# There is no built-in destination: _dsn() returns a DEEPCTL_TELEMETRY_DSN
# override on a loopback address, or None, and with None nothing is sent.

DSN_ENV_VAR = "DEEPCTL_TELEMETRY_DSN"
DISABLE_ENV_VAR = "DEEPCTL_TELEMETRY_DISABLED"
# Cross-tool opt-outs, honored like DEEPCTL_TELEMETRY_DISABLED: the
# consoledonottrack.com convention, and the variable CI services set.
DO_NOT_TRACK_ENV_VAR = "DO_NOT_TRACK"
CI_ENV_VAR = "CI"
# DEEPCTL_TELEMETRY=log prints the report that would be sent to stderr as
# one JSON line, and sends nothing. The values in _MODE_OFF turn telemetry
# off; any other value keeps the default.
MODE_ENV_VAR = "DEEPCTL_TELEMETRY"
LOG_MODE = "log"
_MODE_OFF = frozenset({"off", "0", "false", "no", "disabled"})

# Values of the opt-out variables that leave telemetry on. Any other
# non-empty value turns it off.
_ENV_KEEPS_ON = frozenset({"0", "false", "no", "off"})

# Flush timeout once the SDK is initialized, and the cap on the whole report
# (SDK import and init included). The flush always gets at least the floor.
FLUSH_TIMEOUT_SECONDS = 0.5
REPORT_BUDGET_SECONDS = 0.6
FLUSH_FLOOR_SECONDS = 0.05


def _fresh_state() -> dict[str, Any]:
    """Per-run state, stdlib types only.

    `opt_out` names the switch that turned telemetry off ("build", "dsn"
    when there is no destination, "flag", "env", "do_not_track", "ci" or
    "config"); `config_path` is the user config file the decision was read
    from; `reported` is set once a report starts, `sent` once an event has
    been handed to the transport.
    """
    return {
        "enabled": False,
        "opt_out": None,
        "config_path": None,
        "hooks_installed": False,
        "reported": False,
        "sent": False,
        "disabled_for_run": False,
    }


_state: dict[str, Any] = _fresh_state()
_report_lock = threading.Lock()


def disable_for_run() -> None:
    """Turn telemetry off for this process only (`dg --no-telemetry`)."""
    _state["disabled_for_run"] = True
    _state["enabled"] = False


def _env_set(name: str) -> bool:
    """Whether the variable `name` turns telemetry off (any non-empty value
    except the ones in _ENV_KEEPS_ON)."""
    value = os.environ.get(name, "").strip().lower()
    return bool(value) and value not in _ENV_KEEPS_ON


def _env_opts_out() -> bool:
    if _env_set(DISABLE_ENV_VAR):
        return True
    return os.environ.get(MODE_ENV_VAR, "").strip().lower() in _MODE_OFF


def _log_mode() -> bool:
    """DEEPCTL_TELEMETRY=log: print the report instead of sending it."""
    return os.environ.get(MODE_ENV_VAR, "").strip().lower() == LOG_MODE


# Keys whose repetition in a config file makes the telemetry setting
# ambiguous; a file that repeats one counts as opted out.
_TELEMETRY_KEYS = frozenset({"telemetry", "enabled", "telemetry.enabled"})


def _default_config_path() -> Path:
    """The default user config file (the one Config uses without --config)."""
    import platformdirs

    return Path(platformdirs.user_config_dir("deepctl", "deepgram")) / "config.yaml"


_REFUSED = object()


def _file_opts_out(config_file: Path, text: object = None) -> bool:
    """Whether one user config file turns telemetry off.

    `text` is the file's content as Config read it, or _REFUSED when Config
    didn't use the file. Without it, the file is read here through core's
    config reader (read_user_config_text), with the same limits and
    refusals as a config load, and never at all when Config refused it.
    Telemetry never parses config YAML itself: core's load_config_text
    does, with every load limit.

    Telemetry stays on when the file is absent or empty, or has no
    `telemetry` setting, or sets `enabled` to the boolean `true`. A file
    that a load would refuse (not a regular file, too large, a bomb, a
    parse error, an in-place rewrite that didn't finish, untrusted in an AI
    agent session) turns it off, as does one that repeats a telemetry key,
    has a `telemetry` section that isn't a mapping, or holds any other
    `enabled` value. A top-level `telemetry.enabled` key counts too.
    """
    if text is _REFUSED:
        return True
    try:
        from deepctl_core.config import load_config_text, read_user_config_text
    except ImportError:  # an older deepctl-core: no capped reader to use
        return True
    if not isinstance(text, str):
        try:
            text = read_user_config_text(config_file)
        except FileNotFoundError:
            return False
        except Exception:
            return True
        if text is None:
            return True
    try:
        data, repeated = load_config_text(text, _TELEMETRY_KEYS)
    except Exception:
        return True
    if repeated:
        return True
    if data is None:
        return False
    if not isinstance(data, dict):
        return True
    if "telemetry.enabled" in data and data["telemetry.enabled"] is not True:
        return True
    if "telemetry" not in data:
        return False
    section = data["telemetry"]
    if not isinstance(section, dict):
        return True
    if "enabled" not in section:
        return False
    return section["enabled"] is not True


def _user_config_opts_out(config: Config) -> bool:
    """Read `telemetry.enabled` from the user's own config files only.

    Both the default config file and, with `--config`, the named file count;
    telemetry is off if either turns it off. The file Config loaded is
    parsed from the text Config kept, and counts as opted out when Config
    refused it. The merged Config is not used, because it also holds the
    project's `deepgram.yaml`.
    """
    files: dict[str, object] = {}
    explicit = getattr(config, "config_path", None)
    if explicit is not None:
        text = getattr(config, "user_config_text", None)
        if getattr(config, "user_config_refused", False) is True:
            files[str(explicit)] = _REFUSED
        else:
            files[str(explicit)] = text if isinstance(text, str) else None
    try:
        files.setdefault(str(_default_config_path()), None)
    except Exception:
        return True
    return any(_file_opts_out(Path(path), text) for path, text in files.items())


def _opt_out_source(config: Config) -> str | None:
    if not TELEMETRY_BUILD_ENABLED:
        return "build"
    if _dsn() is None:
        return "dsn"
    if _state["disabled_for_run"]:
        return "flag"
    if _env_opts_out():
        return "env"
    if _env_set(DO_NOT_TRACK_ENV_VAR):
        return "do_not_track"
    if _env_set(CI_ENV_VAR):
        return "ci"
    if _user_config_opts_out(config):
        return "config"
    return None


def is_enabled(config: Config) -> bool:
    """Whether telemetry may report an error for this invocation.

    Resolution order: the build switch, then whether there is a
    destination (a loopback DEEPCTL_TELEMETRY_DSN; without one telemetry is
    off), then `--no-telemetry` for this run, then the env overrides
    (DEEPCTL_TELEMETRY_DISABLED or DEEPCTL_TELEMETRY set to off,
    DO_NOT_TRACK, CI), then `telemetry.enabled` in the user's config file
    (default on). The project's `deepgram.yaml` is never read.
    """
    return _opt_out_source(config) is None


def init_telemetry(config: Config) -> bool:
    """Record whether telemetry is on and install the error hooks.

    Called after option parsing with the resolved Config, so `--config` and
    `--no-telemetry` are honored. Does not import the SDK. Safe to call
    more than once; the latest call wins. Returns whether telemetry is on.
    """
    opt_out = _opt_out_source(config)
    _state["opt_out"] = opt_out
    _state["enabled"] = opt_out is None
    path = getattr(config, "config_path", None)
    _state["config_path"] = str(path) if path is not None else None
    if _state["enabled"]:
        _install_hooks()
    return bool(_state["enabled"])


def _install_hooks() -> None:
    """Chain stdlib hooks for exceptions that escape `main()` or a thread."""
    if _state["hooks_installed"]:
        return
    previous_excepthook = sys.excepthook
    previous_thread_hook = threading.excepthook

    def excepthook(
        exc_type: type[BaseException],
        exc: BaseException,
        tb: TracebackType | None,
    ) -> None:
        report_exception(exc)
        previous_excepthook(exc_type, exc, tb)

    def thread_excepthook(args: threading.ExceptHookArgs) -> None:
        if args.exc_value is not None:
            report_exception(args.exc_value)
        previous_thread_hook(args)

    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook
    _state["hooks_installed"] = True


def _unexpected_error_types() -> tuple[type[BaseException], ...]:
    try:
        from deepctl_core.base_command import UnexpectedCommandError
    except ImportError:
        return ()
    return (UnexpectedCommandError,)


def _reportable(exc: BaseException | None) -> BaseException | None:
    """The exception worth reporting, or None for user errors and exits.

    Any ClickException is an expected outcome (a usage error, or a command
    reporting a problem to the user), except deepctl-core's
    UnexpectedCommandError: BaseCommand raises it `from` an error it didn't
    expect, and that cause is what gets reported.
    """
    import click

    if isinstance(exc, _unexpected_error_types()):
        exc = exc.__cause__
    if exc is None or isinstance(exc, click.ClickException):
        return None
    if isinstance(exc, (click.exceptions.Exit, click.Abort)):
        return None
    return exc if isinstance(exc, Exception) else None


def _dsn() -> str | None:
    """A DEEPCTL_TELEMETRY_DSN override on loopback, or None.

    There is no built-in DSN, so without an override there is nowhere to
    send a report. The override exists for tests and local sinks, so a
    non-loopback value is ignored rather than allowed to redirect reports.
    """
    override = os.environ.get(DSN_ENV_VAR, "").strip()
    if not override:
        return None
    try:
        import ipaddress
        from urllib.parse import urlsplit

        host = urlsplit(override).hostname or ""
        if host == "localhost" or ipaddress.ip_address(host).is_loopback:
            return override
    except ValueError:
        pass
    return None


def report_exception(exc: BaseException | None) -> bool:
    """Send one error report for `exc` if telemetry is on.

    Does nothing, and never imports the SDK, without a loopback
    DEEPCTL_TELEMETRY_DSN. Imports and initializes the SDK only here. At
    most one report per process. The flush waits at most
    FLUSH_TIMEOUT_SECONDS after init, and the whole call is held to
    REPORT_BUDGET_SECONDS (but the flush always gets FLUSH_FLOOR_SECONDS);
    there is no flush when no event was queued. With DEEPCTL_TELEMETRY=log
    the event is printed to stderr instead and nothing is sent, not even to
    the loopback DSN. Never raises an Exception; a KeyboardInterrupt during
    the flush propagates to the caller. Returns whether an event was handed
    to the SDK.
    """
    started = time.monotonic()
    with _report_lock:
        if not TELEMETRY_BUILD_ENABLED or not _state["enabled"] or _state["reported"]:
            return False
        dsn = _dsn()
        if dsn is None:
            return False
        target = _reportable(exc)
        if target is None:
            return False
        _state["reported"] = True

    try:
        import sentry_sdk

        try:
            cli_version = _read_cli_version()
        except Exception:
            cli_version = "unknown"

        if _log_mode():
            options = _sdk_options(None, cli_version)
            options["transport"] = _stderr_transport()
        else:
            options = _sdk_options(dsn, cli_version)
        sentry_sdk.init(**options)
        if sentry_sdk.capture_exception(target) is None:
            # Dropped before it was queued (a closed-stream error, or no
            # field left to send): there is nothing to wait for.
            return False
        if _log_mode():
            return True
        left = REPORT_BUDGET_SECONDS - (time.monotonic() - started)
        timeout = max(FLUSH_FLOOR_SECONDS, min(FLUSH_TIMEOUT_SECONDS, left))
        sentry_sdk.flush(timeout=timeout)
        return True
    except Exception:
        return False


def _read_cli_version() -> str:
    import importlib.metadata

    return importlib.metadata.version("deepctl")


def _stderr_transport() -> Any:
    """A transport that writes each event to stderr as one JSON line.

    Used for DEEPCTL_TELEMETRY=log. It never opens a connection, and the
    SDK gets no destination at all in that mode.
    """
    import json

    from sentry_sdk.transport import Transport

    class StderrTransport(Transport):
        def capture_envelope(self, envelope: Any) -> None:
            event = envelope.get_event()
            if event is None:
                return
            try:
                sys.stderr.write(json.dumps(event, sort_keys=True, default=str))
                sys.stderr.write("\n")
                sys.stderr.flush()
            except (OSError, ValueError, AttributeError):
                pass

    return StderrTransport()


def _sdk_options(dsn: str | None, cli_version: str) -> dict[str, Any]:
    """Explicit SDK settings: one error event per failure and nothing else.

    No integrations at all (no argv, modules, logging, breadcrumbs, and none
    of the SDK's own excepthook or threading capture: the stdlib hooks in
    `_install_hooks` are the only capture path, so the once-per-process
    rule holds). No traces, profiles, sessions or logs, and no locals,
    source lines, stack snapshots or hostname. No proxy: an empty
    `http_proxy` and `https_proxy` stop the SDK from falling back to a proxy
    from the environment or the system settings, so a report to a loopback
    DSN stays on this machine. `before_send` and `before_send_transaction`
    then rebuild whatever is left from the allowlist in `_rebuild_event`.
    """
    return {
        "dsn": dsn,
        "release": f"deepctl@{cli_version}",
        "environment": "production",
        "default_integrations": False,
        "auto_enabling_integrations": False,
        "integrations": [],
        "traces_sample_rate": 0.0,
        "profiles_sample_rate": 0.0,
        "enable_logs": False,
        "auto_session_tracking": False,
        "attach_stacktrace": False,
        "include_local_variables": False,
        "include_source_context": False,
        "max_breadcrumbs": 0,
        "send_default_pii": False,
        "send_client_reports": False,
        "server_name": "",
        "spotlight": False,
        "http_proxy": "",
        "https_proxy": "",
        "before_send": _before_send,
        "before_send_transaction": _rebuild_event,
    }


def _before_send(event: Event, hint: Hint) -> Event | None:
    """Rebuild the event, and let only the first one in the process through."""
    with _report_lock:
        if _state["sent"]:
            return None
        rebuilt = _rebuild_event(event, hint)
        if rebuilt is not None:
            _state["sent"] = True
        return rebuilt


def _is_broken_pipe(event: Event) -> bool:
    """Identify events caused by writing to a closed/broken output stream.

    When `dg mcp` runs as an MCP server, the host can close stdio while the
    CLI is still finishing up. Any residual write (an advisory notification, a
    console error message) then raises ``BrokenPipeError`` — or rich's
    ``ValueError: I/O operation on closed file`` when the underlying stream is
    already closed. Neither is an actionable CLI bug, so drop them rather than
    page the DX team.
    """
    for exc in (event.get("exception") or {}).get("values") or []:
        exc_type = exc.get("type") or ""
        if exc_type == "BrokenPipeError":
            return True
        if exc_type == "ValueError" and "closed file" in (exc.get("value") or ""):
            return True
    return False


# Top-level packages whose module and exception-type names may be sent: dg's
# own packages, the libraries it calls, and the standard library. Any other
# module (a plugin, even one named deepctl_*, a user script, `__main__`) is
# sent as OTHER_NAME. Keep _FIRST_PARTY in step with packages/*/src.
_FIRST_PARTY = frozenset(
    {
        "deepctl",
        "deepctl_cmd_api",
        "deepctl_cmd_billing",
        "deepctl_cmd_completion",
        "deepctl_cmd_debug",
        "deepctl_cmd_debug_audio",
        "deepctl_cmd_debug_browser",
        "deepctl_cmd_debug_network",
        "deepctl_cmd_debug_probe",
        "deepctl_cmd_debug_toolkit",
        "deepctl_cmd_ffprobe",
        "deepctl_cmd_init",
        "deepctl_cmd_keys",
        "deepctl_cmd_listen",
        "deepctl_cmd_login",
        "deepctl_cmd_mcp",
        "deepctl_cmd_members",
        "deepctl_cmd_models",
        "deepctl_cmd_plugin",
        "deepctl_cmd_projects",
        "deepctl_cmd_read",
        "deepctl_cmd_requests",
        "deepctl_cmd_skills",
        "deepctl_cmd_speak",
        "deepctl_cmd_transcribe",
        "deepctl_cmd_update",
        "deepctl_cmd_usage",
        "deepctl_core",
        "deepctl_shared_utils",
        "deepctl_telemetry",
    }
)
_KNOWN_PACKAGES = frozenset(
    {
        "anyio",
        "certifi",
        "click",
        "deepgram",
        "deepgram_mcp",
        "h11",
        "httpcore",
        "httpx",
        "keyring",
        "mcp",
        "platformdirs",
        "pydantic",
        "pydantic_core",
        "requests",
        "rich",
        "sentry_sdk",
        "typer",
        "urllib3",
        "websockets",
        "yaml",
    }
)
OTHER_NAME = "<other>"


def _known_module(module: str) -> bool:
    top = module.split(".", 1)[0]
    return (
        top in _FIRST_PARTY
        or top in _KNOWN_PACKAGES
        or top in getattr(sys, "stdlib_module_names", ())
    )


# Identifier-like tokens only. A value that doesn't match is dropped, never
# escaped or truncated, so free text can't reach the payload.
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")
_FUNCTION_RE = re.compile(r"^(<[a-z]+>|[A-Za-z_][A-Za-z0-9_.<>]{0,127})$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9_.+-]{1,64}$")
_COMMAND_RE = re.compile(r"^[A-Za-z0-9 _-]{1,200}$")
_FLAGS_RE = re.compile(r"^[A-Za-z0-9,_-]{0,1000}$")


def _match(pattern: re.Pattern[str], value: object) -> str | None:
    return value if isinstance(value, str) and pattern.match(value) else None


def _allowed_tags() -> dict[str, str]:
    """Run-level fields: command path, flag names, version, OS, arch, Python."""
    try:
        from deepctl_core.telemetry_context import command_context

        context = command_context()
    except ImportError:
        # An older deepctl-core without the context: report without cmd.*.
        context = {}
    candidates = {
        "cmd.path": _match(_COMMAND_RE, context.get("command")),
        "cmd.flags": _match(_FLAGS_RE, context.get("flags")),
        "cli.os": _match(_VERSION_RE, platform.system()),
        "cli.arch": _match(_VERSION_RE, platform.machine()),
        "cli.python": _match(_VERSION_RE, platform.python_version()),
    }
    return {key: value for key, value in candidates.items() if value is not None}


def _allowed_exception(exc: dict[str, Any]) -> dict[str, Any] | None:
    """Exception type, mechanism, and module/function/line per frame.

    Names from modules outside `_known_module` become OTHER_NAME, so a
    plugin's or script's own identifiers are never sent.
    """
    exc_type = _match(_NAME_RE, exc.get("type"))
    if exc_type is None:
        return None
    type_module = _match(_NAME_RE, exc.get("module")) or "builtins"
    if not _known_module(type_module):
        exc_type = OTHER_NAME

    frames: list[dict[str, Any]] = []
    for frame in (exc.get("stacktrace") or {}).get("frames") or []:
        kept: dict[str, Any] = {}
        module = _match(_NAME_RE, frame.get("module"))
        function = _match(_FUNCTION_RE, frame.get("function"))
        lineno = frame.get("lineno")
        if module is None or not _known_module(module):
            module = function = OTHER_NAME
        kept["module"] = module
        if function is not None:
            kept["function"] = function
        if isinstance(lineno, int) and not isinstance(lineno, bool):
            kept["lineno"] = lineno
        if kept:
            frames.append(kept)

    rebuilt: dict[str, Any] = {"type": exc_type}
    mechanism = exc.get("mechanism") or {}
    mechanism_type = _match(_NAME_RE, mechanism.get("type"))
    if mechanism_type is not None:
        rebuilt["mechanism"] = {"type": mechanism_type}
        if isinstance(mechanism.get("handled"), bool):
            rebuilt["mechanism"]["handled"] = mechanism["handled"]
    if frames:
        rebuilt["stacktrace"] = {"frames": frames}
    return rebuilt


def _rebuild_event(event: Event, _hint: Hint) -> Event | None:
    """Build a new event from allowlisted fields; everything else is dropped.

    The SDK's event is only read, never forwarded: messages, exception
    values, paths, variables, request data, contexts, extra, modules,
    breadcrumbs, user and server_name never make it into the new event.
    Events without an exception (log messages, transactions) are dropped.

    Also drops closed-stream events (see ``_is_broken_pipe``).
    """
    if _is_broken_pipe(event):
        return None

    values = [
        rebuilt
        for exc in (event.get("exception") or {}).get("values") or []
        if (rebuilt := _allowed_exception(exc)) is not None
    ]
    if not values:
        return None

    rebuilt_event: dict[str, Any] = {
        "platform": "python",
        "level": "error",
        "exception": {"values": values},
        "tags": _allowed_tags(),
    }
    for key, pattern in (
        ("event_id", re.compile(r"^[0-9a-f]{32}$")),
        ("release", re.compile(r"^deepctl@[A-Za-z0-9_.+-]{1,64}$")),
        ("environment", _NAME_RE),
    ):
        value = _match(pattern, event.get(key))
        if value is not None:
            rebuilt_event[key] = value
    timestamp = event.get("timestamp")
    if timestamp is not None:
        rebuilt_event["timestamp"] = timestamp
    sdk = event.get("sdk") or {}
    sdk_name = _match(_NAME_RE, sdk.get("name"))
    sdk_version = _match(_VERSION_RE, sdk.get("version"))
    if sdk_name and sdk_version:
        rebuilt_event["sdk"] = {"name": sdk_name, "version": sdk_version}
    return rebuilt_event  # type: ignore[return-value]
