"""Main entry point for running gnosis module directly."""

from .gnosis import main

if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
