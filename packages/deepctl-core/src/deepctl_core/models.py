"""Core models for deepgram-core package."""

from pydantic import BaseModel, Field


class BaseResult(BaseModel):
    """Common base for all command result payloads."""

    status: str = Field(
        default="success", description="Outcome marker/messages key"
    )
    message: str | None = None


class ProfileInfo(BaseModel):
    """Profile information."""

    api_key: str | None
    project_id: str | None
    base_url: str


class ProfilesResult(BaseResult):
    """List of profiles with optional current indicator."""

    current_profile: str | None = None
    profiles: dict[str, ProfileInfo] = Field(default_factory=dict)


class PluginInfo(BaseModel):
    """Plugin information."""

    name: str
    help: str
    short_help: str | None
    type: str  # builtin | external
    module: str


class ErrorResult(BaseModel):
    """Generic error result for any command failures."""

    error: str
    status: str = "error"
