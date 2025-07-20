"""Core models for deepgram-core package."""

from typing import Optional, Dict, List
from pydantic import BaseModel, Field


class BaseResult(BaseModel):
    """Common base for all command result payloads."""
    status: str = Field(default="success",
                        description="Outcome marker/messages key")
    message: Optional[str] = None


class ProfileInfo(BaseModel):
    """Profile information."""
    api_key: Optional[str]
    project_id: Optional[str]
    base_url: str


class ProfilesResult(BaseResult):
    """List of profiles with optional current indicator."""
    current_profile: Optional[str] = None
    profiles: Dict[str, ProfileInfo] = Field(default_factory=dict)


class PluginInfo(BaseModel):
    """Plugin information."""
    name: str
    help: str
    short_help: Optional[str]
    type: str  # builtin | external
    module: str


class ErrorResult(BaseModel):
    """Generic error result for any command failures."""
    error: str
    status: str = "error"
