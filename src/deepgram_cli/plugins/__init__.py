"""Plugin system for deepctl."""

from pathlib import Path

# Plugin discovery paths
PLUGIN_DIRS = [
    Path(__file__).parent / "examples",  # Built-in example plugins
]

__all__ = ["PLUGIN_DIRS"] 