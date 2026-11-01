"""Hand a stdio MCP session from ``dg mcp``'s fast path to the proxy.

``deepctl.mcp._fast`` answers ``initialize`` and ``tools/list`` itself, then
calls ``serve_after_handshake`` on the first request it can't answer. This
replays the client's ``initialize`` to the local MCP server in
``deepctl_cmd_mcp.proxy`` (dropping that second reply), then feeds it the
pending request and the rest of stdin. Nothing connects to Deepgram until a
tool call, and a remote failure is that call's tool error.

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

from .proxy import CANT_START, NO_KEY, LineWriter, reason, report_error

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# The id of the replayed initialize; its reply is ours, not the client's.
_REPLAY_ID = "deepctl-fast-path-initialize"


class _ReplayThenStdin:
    """Async lines: the replayed handshake and pending request, then stdin."""

    def __init__(self, lines: list[str], stdin: BinaryIO) -> None:
        self._lines = lines
        self._stdin = stdin

    def __aiter__(self) -> AsyncIterator[str]:
        return self._lines_then_stdin()

    async def _lines_then_stdin(self) -> AsyncIterator[str]:
        import anyio

        for line in self._lines:
            yield line + "\n"
        text = io.TextIOWrapper(self._stdin, encoding="utf-8", errors="replace")
        async for line in anyio.wrap_file(text):
            yield line


def _api_key() -> str:
    """The key `dg mcp` would use (flag-less: profile, keyring or env).

    Raises RuntimeError with the client-facing text when there is none.
    """
    from deepctl_core import AuthManager, Config
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
            return key_or_error(config, AuthManager(config))
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(CANT_START.format(reason=reason(exc))) from exc


def key_or_error(config: Any, auth_manager: Any) -> str:
    """The API key, or RuntimeError with the complete text for the client."""
    from deepctl_core import messages as msg

    try:
        with redirect_stdout(sys.stderr):
            api_key = auth_manager.get_api_key()
    except Exception as exc:
        raise RuntimeError(CANT_START.format(reason=reason(exc))) from exc
    if api_key:
        return str(api_key)
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
    """Serve the rest of a stdio session with the documentation search."""
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
    try:
        asyncio.run(
            serve(
                _ReplayThenStdin(replay, stdin),
                LineWriter(stdout, drop_id=_REPLAY_ID),
                base_url=canonical_url(DEFAULT_BASE_URL),
                api_key=lambda: api_key,
            )
        )
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        report_error(exc)
        print(f"ERROR: dg mcp: {reason(exc)}", file=sys.stderr)
        return 1
    return 0
