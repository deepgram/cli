"""Entry point for ``dg``, ``deepctl``, ``deepgram`` and ``python -m deepctl``.

Only ``sys`` and ``os`` are imported at module level. ``main()`` peeks at
argv first, so two paths finish without Click or ``deepctl_core``:

- ``dg --version`` prints the version line (the same bytes as the Click
  path) and exits 0.
- ``dg mcp`` (stdio, no other options) answers the MCP handshake locally in
  ``deepctl.mcp._fast`` and hands the session to the proxy on the first call.

Every ``dg mcp`` run except a help request first moves fd 1 to stderr
(``deepctl.mcp._fast.isolate_stdout``), so only the MCP server can write
to the client's stdout, whatever runs after it.

Everything else imports ``deepctl._cli`` and runs the Click CLI as before.
Names that used to live here (``cli``, ``load_commands`` and the helpers)
resolve from ``deepctl._cli`` through ``__getattr__``; ``cli`` comes back with
its commands loaded, as it did when this module loaded them at import. That
covers reads only: to patch them, patch ``deepctl._cli.<name>``.
"""

import os
import sys

# typing is not imported at run time (it costs more than this whole path);
# type checkers treat a name called TYPE_CHECKING as true.
TYPE_CHECKING = False
if TYPE_CHECKING:
    from types import TracebackType
    from typing import Any

LAUNCHER_ENV_VAR = "DEEPCTL_LAUNCHER"

# DEEPCTL_LAUNCHER as main() found it (set by the @deepgram/cli npm package),
# or None. main() removes it from os.environ first thing, so nothing deepctl
# starts inherits it; deepctl_core.launcher reads it from here.
LAUNCHER: "str | None" = None
LAUNCHER_CAPTURED = False

HELP_CACHE_ENV_VAR = "DEEPCTL_HELP_CACHE"
# Whether the launcher asked for a help-cache entry (DEEPCTL_HELP_CACHE=1).
# main() removes the variable first thing, so nothing dg starts inherits it.
HELP_CACHE = False

# The `dg mcp` arguments the fast path accepts; anything else takes the full
# path (and its Click errors).
_FAST_MCP_ARGS: "tuple[list[str], ...]" = (
    [],
    ["--transport", "stdio"],
    ["--transport=stdio"],
    ["-t", "stdio"],
)


# Root options that take a value (``dg --profile staging mcp``). Must match
# the root group's non-flag options; tests/unit/test_main.py checks it.
_ROOT_VALUE_OPTIONS = frozenset(
    {
        "--config",
        "-c",
        "--profile",
        "-p",
        "--base-url",
        "--api-key",
        "--output",
        "-o",
        "--record-install-method",
    }
)
# Arguments that make `dg mcp` print help or metadata to stdout instead of
# serving.
_MCP_HELP_ARGS = frozenset({"--help", "-h", "--agent-friendly"})


def _is_mcp_server_run(args: "list[str]") -> bool:
    """True when argv runs ``dg mcp`` as a server (not for help)."""
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            i += 1
            break
        if not arg.startswith("-") or arg == "-":
            break
        if arg in _MCP_HELP_ARGS or arg == "--version":
            return False
        i += 2 if arg in _ROOT_VALUE_OPTIONS else 1
    if i >= len(args) or args[i] != "mcp":
        return False
    return not any(arg in _MCP_HELP_ARGS for arg in args[i + 1 :])


def capture_launcher() -> "str | None":
    """Read and remove ``DEEPCTL_LAUNCHER`` (first call only); return its value."""
    global LAUNCHER, LAUNCHER_CAPTURED
    if not LAUNCHER_CAPTURED:
        LAUNCHER = os.environ.pop(LAUNCHER_ENV_VAR, None) or None
        LAUNCHER_CAPTURED = True
    return LAUNCHER


PROG_NAME_ENV_VAR = "DEEPCTL_PROG_NAME"
_PROG_FIRST = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
)
_PROG_REST = _PROG_FIRST | frozenset("._-")


def prog_name_from_env() -> "str | None":
    """``DEEPCTL_PROG_NAME`` without surrounding whitespace, when it is a
    plain program name: an ASCII letter or digit, then ASCII letters,
    digits, ``.``, ``_`` or ``-``, at most 64 characters. Anything else (a
    path, a control character, a space, a shell metacharacter) is ignored,
    as if unset: dg prints this name in usage lines, ``--version`` and
    notices."""
    name = os.environ.get(PROG_NAME_ENV_VAR, "").strip()
    if (
        0 < len(name) <= 64
        and name[0] in _PROG_FIRST
        and all(char in _PROG_REST for char in name)
    ):
        return name
    return None


def _rich_excepthook(
    exc_type: "type[BaseException]",
    exc: BaseException,
    tb: "TracebackType | None",
) -> None:
    """Show an uncaught exception as a rich traceback, without frame locals.

    Installed by main() on the full path. Rich loads only when an exception
    actually reaches the interpreter's top level.
    """
    try:
        from rich.traceback import install

        install(show_locals=False)
    except Exception:
        sys.__excepthook__(exc_type, exc, tb)
        return
    sys.excepthook(exc_type, exc, tb)


def version_line() -> str:
    """``<name>, version <version>``: ``DEEPCTL_PROG_NAME`` when a launcher
    sets it to a plain program name, else the name the user typed (``dg``,
    ``deepgram`` or ``deepctl``), else ``deepctl`` (``python -m deepctl``,
    a renamed script). The Click path prints the same line."""
    from deepctl import __version__

    name = prog_name_from_env()
    if not name:
        name = os.path.basename(sys.argv[0]) if sys.argv and sys.argv[0] else ""
        if os.name == "nt" and name.lower().endswith(".exe"):
            name = name[:-4]
        if name not in ("dg", "deepgram", "deepctl"):
            name = "deepctl"
    return f"{name}, version {__version__}"


# Error handlers that never raise on a character the encoding lacks.
# "strict" and "surrogateescape" (a C locale) both raise.
_SAFE_ERRORS = frozenset(
    {"backslashreplace", "replace", "ignore", "namereplace", "xmlcharrefreplace"}
)


def safe_output_streams() -> None:
    """Have stdout and stderr escape what their encoding can't write.

    A redirected stream on Windows uses the ANSI code page (cp1252, say),
    which has no block art or check mark: writing one raised
    UnicodeEncodeError and ended the command. Such a stream now writes
    ``\\u2713`` instead. A UTF-8 stream is left as it is, and the encoding
    never changes, so a real console keeps what Python chose for it.
    """
    import codecs
    import io

    for stream in (sys.stdout, sys.stderr):
        if not isinstance(stream, io.TextIOWrapper):
            continue  # None, or a stream that can't be reconfigured
        try:
            encoding = codecs.lookup(stream.encoding or "").name
            if encoding == "utf-8" or stream.errors in _SAFE_ERRORS:
                continue
            stream.reconfigure(errors="backslashreplace")
        except (LookupError, OSError, ValueError):
            continue  # an unknown encoding, or a closed stream


def _write_cancelled() -> None:
    """run()'s cancel lines, written without rich or Click."""
    text = "\nCancelled.\n"
    try:
        from deepctl_core import messages as msg
        from deepctl_core.agents import is_agentic

        if is_agentic():
            text = f"ERROR: {msg.E_CANCELLED}\nHINT: {msg.A_CANCELLED}\n"
        else:
            text = f"\n{msg.E_CANCELLED}\n"
    except (Exception, KeyboardInterrupt):  # half-loaded: keep the default
        pass
    try:
        sys.stderr.write(text)
        sys.stderr.flush()
    except (AttributeError, OSError, ValueError):
        pass


def _print_version() -> None:
    if sys.stdout is None:  # started with stdout closed (`dg --version >&-`)
        return
    try:
        sys.stdout.write(version_line() + "\n")
        sys.stdout.flush()
    except OSError as exc:
        if not isinstance(exc, BrokenPipeError):
            # Only Windows reports a reader that went away as EINVAL; on
            # other systems any other error is real. deepctl_core is
            # imported only there: this path is stdlib only.
            if sys.platform != "win32":
                raise
            from deepctl_core.signals import reader_gone

            if not reader_gone(exc, sys.stdout):
                raise
        # The reader went away (`dg --version | true`): exit quietly, and
        # keep the interpreter's final flush from reporting it again.
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
            os.close(devnull)
        except (OSError, ValueError):
            pass


def main(prog: "str | None" = None) -> None:
    """Run deepctl for ``sys.argv``.

    ``prog`` is the program name for usage lines when ``DEEPCTL_PROG_NAME``
    isn't set; the console scripts leave it to ``argv[0]``.
    """
    from time import perf_counter

    started_at = perf_counter()
    capture_launcher()
    global HELP_CACHE
    HELP_CACHE = os.environ.pop(HELP_CACHE_ENV_VAR, None) == "1"
    args = sys.argv[1:]

    if args == ["--version"]:
        _print_version()
        return

    # httpx imports its optional CLI (pygments, rich.syntax) at package import
    # and falls back to a stub on ImportError; this skips it.
    sys.modules.setdefault("httpx._main", None)  # type: ignore[arg-type]

    if _is_mcp_server_run(args):
        from deepctl.mcp._fast import isolate_stdout, serve

        isolate_stdout()
        if args[0] == "mcp" and args[1:] in _FAST_MCP_ARGS:
            sys.exit(serve())

    safe_output_streams()
    sys.excepthook = _rich_excepthook
    try:
        from deepctl_core.signals import install_interrupt_handler

        install_interrupt_handler()
        from deepctl._cli import run
    except KeyboardInterrupt:
        # Ctrl-C while the CLI is still loading: cancelled, exit 2, the same
        # lines run() prints (rich may not be loaded yet), and no traceback.
        _write_cancelled()
        sys.exit(2)

    run(args, started_at, prog)


# Only at run time: type checkers then flag a misspelled name.
if not TYPE_CHECKING:

    def __getattr__(name: str) -> "Any":
        """Resolve the names that lived here before 0.5.0 from ``deepctl._cli``."""
        if name.startswith("__"):
            raise AttributeError(name)
        from deepctl import _cli

        if name == "cli":
            return _cli.get_cli()
        try:
            return getattr(_cli, name)
        except AttributeError:
            raise AttributeError(
                f"module {__name__!r} has no attribute {name!r}"
            ) from None


if __name__ == "__main__":
    main()
