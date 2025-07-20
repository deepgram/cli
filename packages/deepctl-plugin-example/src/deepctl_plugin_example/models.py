"""Data models for the example plugin."""

from pydantic import BaseModel
from typing import Optional


class ExampleResult(BaseModel):
    """Result model for the example command."""

    message: str
    plugin: str
    version: str
    greeting: Optional[str] = None
    name: Optional[str] = None
