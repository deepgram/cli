"""Hand a stdio MCP session from ``dg mcp``'s fast path to the proxy.

``deepctl.mcp._fast`` answers ``initialize`` and ``tools/list`` itself, then
calls ``serve_after_handshake`` on the first request it can't answer. This
replays the client's ``initialize`` to the local MCP server in
``deepctl_cmd_mcp.proxy`` (dropping that second reply), then feeds it the
pending request and the rest of stdin.

Contract (see ``deepctl.mcp._fast``): returns the exit code when the session
ends; raises an ``Exception`` whose message the client sees only when it
could not start, before reading stdin or writing stdout.
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from typing import TYPE_CHECKING, Any, BinaryIO

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# The id of the replayed initialize; its reply is ours, not the client's.
_REPLAY_ID = "deepctl-fast-path-initialize"


def _report_error(exc: BaseException) -> None:
    """Errors-only telemetry, as `dg mcp` reports proxy failures."""
    try:
        from deepctl_telemetry import report_exception

        report_exception(exc)
    except Exception:
        pass


class _ReplayThenStdin:
    """Async lines: the replayed handshake and pending request, then stdin."""

    def __init__(self, lines: list[str], stdin: BinaryIO) -> None:
        self._lines = lines
        self._stdin = stdin
        # Set once the server starts reading: from then on a failure is
        # reported on stderr, not as a start failure.
        self.started = False

    def __aiter__(self) -> AsyncIterator[str]:
        self.started = True
        return self._lines_then_stdin()

    async def _lines_then_stdin(self) -> AsyncIterator[str]:
        import anyio

        for line in self._lines:
            yield line + "\n"
        text = io.TextIOWrapper(self._stdin, encoding="utf-8", errors="replace")
        async for line in anyio.wrap_file(text):
            yield line


class _DropReplayReply:
    """stdout for the proxy server, minus its reply to the replayed initialize."""

    def __init__(self, stdout: BinaryIO) -> None:
        self._stdout = stdout
        self._dropped = False

    async def write(self, data: str) -> None:
        if not self._dropped:
            try:
                reply_id = json.loads(data).get("id")
            except (ValueError, AttributeError):
                reply_id = None
            if reply_id == _REPLAY_ID:
                self._dropped = True
                return
        self._stdout.write(data.encode("utf-8"))

    async def flush(self) -> None:
        self._stdout.flush()


# The client sees these texts (as a tool error) and stderr gets them after
# "ERROR: dg mcp: ". A local cause (no key, unreadable config) never reads
# as Deepgram being down.
CANT_START = "dg mcp can't start the documentation search: {reason}"
CANT_REACH = "dg mcp couldn't reach Deepgram's documentation search: {reason}"
NO_KEY = (
    "No API key is set. Run dg login or set DEEPGRAM_API_KEY, then restart "
    "the MCP server."
)


def _api_key() -> str:
    """The key `dg mcp` would use (flag-less: profile, keyring or env).

    Raises RuntimeError with the client-facing text when there is none.
    """
    from deepctl_core import AuthManager, Config
    from deepctl_core import messages as msg
    from deepctl_core.config import flush_config_notices

    try:
        # stdout is the protocol channel: config notices go to stderr, and
        # now, so they come before any reply or ERROR line.
        with redirect_stdout(sys.stderr):
            config = Config()
            flush_config_notices()
            try:
                from deepctl_telemetry import init_telemetry

                init_telemetry(config)
            except Exception:
                pass
            api_key = AuthManager(config).get_api_key()
    except Exception as exc:
        raise RuntimeError(CANT_START.format(reason=_reason(exc))) from exc
    if api_key:
        return api_key
    if getattr(config, "api_key_untrusted", False) is True:
        # The config file's key was left unused (W-PERM-AGENT /
        # W-CFG-UNTRUSTED): send the agent to a person, never to dg login.
        raise RuntimeError(
            msg.E_KEY_UNTRUSTED.format(user_config=msg.display_path(config.config_path))
            + " "
            + msg.W_CFG_UNTRUSTED_FILE_HINT
        )
    raise RuntimeError(CANT_START.format(reason=NO_KEY))


def serve_after_handshake(
    initialize_params: dict[str, Any],
    pending_message: dict[str, Any],
    stdin: BinaryIO,
    stdout: BinaryIO,
) -> int:
    """Serve the rest of a stdio session through the local MCP server."""
    import asyncio

    from deepctl_core.hosts import canonical_url

    from .proxy import DEFAULT_BASE_URL, serve

    api_key = _api_key()
    replay = [
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": _REPLAY_ID,
                "method": "initialize",
                "params": initialize_params,
            }
        ),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps(pending_message),
    ]
    lines = _ReplayThenStdin(replay, stdin)
    try:
        asyncio.run(
            serve(
                lines,
                _DropReplayReply(stdout),
                base_url=canonical_url(DEFAULT_BASE_URL),
                api_key=lambda: api_key,
            )
        )
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        _report_error(exc)
        reason = _reason(exc)
        if not lines.started:
            raise RuntimeError(CANT_REACH.format(reason=reason)) from exc
        print(f"ERROR: dg mcp: {reason}", file=sys.stderr)
        return 1
    return 0


def _reason(exc: BaseException) -> str:
    """One line: the innermost cause of an (exception-group) failure."""
    # ExceptionGroup is 3.11+; anyio raises its backport on 3.10.
    while getattr(exc, "exceptions", None):
        exc = exc.exceptions[0]  # type: ignore[attr-defined]
    text = str(exc).strip().splitlines()
    return text[0] if text else type(exc).__name__
