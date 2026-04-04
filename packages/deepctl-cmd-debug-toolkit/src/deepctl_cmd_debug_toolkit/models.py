"""Models for the toolkit manifest."""

from __future__ import annotations

from pydantic import BaseModel


class ToolkitScript(BaseModel):
    """A single script entry from toolkit.json."""

    description: str
    script: str  # Relative path within the support-toolkit repo
    pass_api_key: bool = False  # Inject DEEPGRAM_API_KEY into subprocess env


class ToolkitManifest(BaseModel):
    """Parsed representation of toolkit.json."""

    version: str
    commands: dict[str, ToolkitScript]
