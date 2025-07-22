"""Main entry point for running gnosis module directly."""

import sys

from .gnosis import main

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
