"""Main entry point for running MCP server directly."""

import argparse
import os
import signal
import sys
from typing import Any

from .command import create_mcp_server


def signal_handler(signum: int, frame: Any) -> None:
    """Handle signals by exiting immediately."""
    print("\nMCP server stopped by user", file=sys.stderr)
    os._exit(0)


def main() -> None:
    """Run MCP server with command line arguments."""
    # Install signal handlers FIRST before anything else
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    parser = argparse.ArgumentParser(description="Run Deepgram MCP server")
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "sse", "streamable-http"],
        help="Transport mode",
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Port for HTTP transports"
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Host for HTTP transports"
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging"
    )

    args = parser.parse_args()

    # Create and run the server
    try:
        server = create_mcp_server()

        if args.transport == "stdio":
            server.run()
        elif args.transport == "sse":
            server.run(transport="sse", host=args.host, port=args.port)
        elif args.transport == "streamable-http":
            server.run(
                transport="streamable-http", host=args.host, port=args.port
            )
    except KeyboardInterrupt:
        print("\nMCP server stopped by user", file=sys.stderr)
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
