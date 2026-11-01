"""Core models for deepgram-core package."""

from pydantic import BaseModel, Field

__all__ = [
    "BaseModel",
    "BaseResult",
    "CodedErrorResult",
    "ErrorResult",
    "PluginInfo",
    "ProfileInfo",
    "ProfilesResult",
]


class BaseResult(BaseModel):
    """Common base for all command result payloads."""

    status: str = Field(default="success", description="Outcome marker/messages key")
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


class CodedErrorResult(ErrorResult):
    """Error result that also carries a machine-readable code and a hint.

    A subclass rather than optional fields on ErrorResult, so payloads of
    every other error keep their existing keys.
    """

    code: str
    hint: str
