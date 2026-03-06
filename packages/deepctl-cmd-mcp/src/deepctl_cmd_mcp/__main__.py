"""Main entry point for running MCP proxy directly."""

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

    parser = argparse.ArgumentParser(description="Run Deepgram MCP proxy")
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "sse", "streamable-http"],
        help="Transport mode",
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Port for HTTP transports"
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host for HTTP transports")
    parser.add_argument(
        "--base-url",
        default=os.getenv("DEEPGRAM_DX_URL", "http://localhost:8080"),
        help="Base URL for Deepgram developer API",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("DEEPGRAM_API_KEY"),
        help="Deepgram API key",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    if not args.api_key:
        print(
            "Error: No API key. Set DEEPGRAM_API_KEY or use --api-key.", file=sys.stderr
        )
        sys.exit(1)

    from .command import run_proxy

    try:
        asyncio.run(
            run_proxy(
                transport=args.transport,
                host=args.host,
                port=args.port,
                api_key=args.api_key,
                base_url=args.base_url,
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
