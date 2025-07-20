"""Example plugin package for deepctl."""

from .command import ExampleCommand
from .models import ExampleResult

__all__ = ["ExampleCommand", "ExampleResult"]
