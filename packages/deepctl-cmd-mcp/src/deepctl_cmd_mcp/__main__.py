"""Main entry point for running MCP proxy directly via deepctl-cmd-mcp."""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from typing import Any


def signal_handler(signum: int, frame: Any) -> None:
    """Handle signals by exiting immediately."""
    print("\nMCP proxy stopped by user", file=sys.stderr)
    os._exit(0)


def main() -> None:
    """Run MCP proxy with command line arguments."""
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # No abbreviations: --base-url must be recognised as the flag it is.
    parser = argparse.ArgumentParser(
        description="Run Deepgram MCP proxy", allow_abbrev=False
    )
    parser.add_argument(
        "--transport",
        default="stdio",
        # Matched case-insensitively, like dg mcp.
        type=str.lower,
        choices=["stdio", "sse", "streamable-http"],
        help="Transport: stdio (the only one supported)",
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Ignored: only stdio is supported."
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Ignored: only stdio is supported."
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Base URL for Deepgram developer API",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("DEEPGRAM_API_KEY"),
        help="Deepgram API key",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    # Only stdio runs. "sse", which 0.4.0 served, and "streamable-http",
    # which no version served, are accepted here so a config that names
    # either gets this message instead of an argparse error. Nothing is
    # started.
    if args.transport != "stdio":
        from .messages import E_MCP_TRANSPORT, E_MCP_TRANSPORT_HINT

        text = E_MCP_TRANSPORT.format(transport=args.transport)
        print(f"Error: {text}\n  {E_MCP_TRANSPORT_HINT}", file=sys.stderr)
        sys.exit(1)

    if not args.api_key:
        print(
            "Error: No API key. Set DEEPGRAM_API_KEY or use --api-key.", file=sys.stderr
        )
        sys.exit(1)

    # Same host check as `dg mcp`: the key goes only to the Deepgram
    # developer API, a host in the user's api.allowed_hosts, or (outside
    # agent mode) a DEEPGRAM_DX_URL from the environment.
    from deepctl_core.hosts import (
        DEFAULT_DX_URL,
        DX_API_HOSTS,
        SOURCE_DEFAULT,
        SOURCE_ENV,
        SOURCE_FLAG,
        CredentialHostError,
        canonical_url,
        evaluate,
        is_allowlisted,
        is_regional,
        parse_origin,
        report,
        warn_env_host,
    )

    env_url = os.getenv("DEEPGRAM_DX_URL")
    if args.base_url is not None:
        base_url, source = args.base_url, SOURCE_FLAG
    elif env_url:
        base_url, source = env_url, SOURCE_ENV
    else:
        base_url, source = DEFAULT_DX_URL, SOURCE_DEFAULT
    origin = parse_origin(base_url)
    if origin is None or not is_regional(origin, DX_API_HOSTS):
        # Only now read the user config (for api.allowed_hosts); stdout is
        # the protocol channel, so nothing here may print to it.
        from contextlib import redirect_stdout

        from deepctl_core import Config

        try:
            with redirect_stdout(sys.stderr):
                allowed = Config().allowed_hosts()
            approved = evaluate(base_url, source, allowed, trusted_hosts=DX_API_HOSTS)
        except CredentialHostError as err:
            report(err)
            sys.exit(1)
        if source == SOURCE_ENV and not is_allowlisted(approved, allowed):
            warn_env_host(approved, "DEEPGRAM_DX_URL")

    from deepgram_mcp import run_proxy

    try:
        asyncio.run(
            run_proxy(
                transport="stdio",
                host=args.host,
                port=args.port,
                api_key=args.api_key,
                base_url=canonical_url(base_url),
                debug=args.debug,
            )
        )
    except KeyboardInterrupt:
        print("\nMCP proxy stopped by user", file=sys.stderr)
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
