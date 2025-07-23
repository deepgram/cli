"""Data models for the example plugin."""

from pydantic import BaseModel


class ExampleResult(BaseModel):
    """Result model for the example command."""

    message: str
    plugin: str
    version: str
    greeting: str | None = None
    name: str | None = None
