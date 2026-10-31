"""Entry point for ``dg``, ``deepctl``, ``deepgram`` and ``python -m deepctl``.

Only ``sys`` and ``os`` are imported at module level. ``main()`` peeks at
argv first, so two paths finish without Click or ``deepctl_core``:

- ``dg --version`` prints the version constant (the same bytes Click printed
  in 0.4.x) and exits 0.
- ``dg mcp`` (stdio, no other options) answers the MCP handshake locally in
  ``deepctl.mcp._fast`` and hands the session to the proxy on the first call.

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

# The `dg mcp` arguments the fast path accepts; anything else takes the full
# path (and its Click errors).
_FAST_MCP_ARGS: "tuple[list[str], ...]" = (
    [],
    ["--transport", "stdio"],
    ["--transport=stdio"],
    ["-t", "stdio"],
)


def capture_launcher() -> "str | None":
    """Read and remove ``DEEPCTL_LAUNCHER`` (first call only); return its value."""
    global LAUNCHER, LAUNCHER_CAPTURED
    if not LAUNCHER_CAPTURED:
        LAUNCHER = os.environ.pop(LAUNCHER_ENV_VAR, None) or None
        LAUNCHER_CAPTURED = True
    return LAUNCHER


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


# Error handlers that never raise on a character the encoding lacks.
# "strict" and "surrogateescape" (a C locale) both raise.
_SAFE_ERRORS = frozenset(
    {"backslashreplace", "replace", "ignore", "namereplace", "xmlcharrefreplace"}
)


def safe_output_streams() -> None:
    """Have stdout and stderr escape what their encoding can't write.

    A redirected stream on Windows uses the ANSI code page (cp1252, say),
    which has no block art: writing the --help banner raised
    UnicodeEncodeError and ended the command. Such a stream now writes
    ``\\u2588`` instead. A UTF-8 stream is left as it is, and the encoding
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


def _print_version() -> None:
    from deepctl import __version__

    if sys.stdout is None:  # started with stdout closed (`dg --version >&-`)
        return
    try:
        # Byte-identical to 0.4.x, where Click's version_option printed this
        # with prog_name="deepctl" for every console script.
        sys.stdout.write(f"deepctl, version {__version__}\n")
        sys.stdout.flush()
    except BrokenPipeError:
        # The reader went away (`dg --version | true`): exit quietly, and
        # keep the interpreter's final flush from reporting it again.
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
            os.close(devnull)
        except (OSError, ValueError):
            pass


def main() -> None:
    """Run deepctl for ``sys.argv``."""
    from time import perf_counter

    started_at = perf_counter()
    capture_launcher()
    args = sys.argv[1:]

    if args == ["--version"]:
        _print_version()
        return

    # httpx imports its optional CLI (pygments, rich.syntax) at package import
    # and falls back to a stub on ImportError; this skips it.
    sys.modules.setdefault("httpx._main", None)  # type: ignore[arg-type]

    if args and args[0] == "mcp" and args[1:] in _FAST_MCP_ARGS:
        from deepctl.mcp._fast import serve

        sys.exit(serve())

    safe_output_streams()
    sys.excepthook = _rich_excepthook
    from deepctl._cli import run

    run(args, started_at)


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
