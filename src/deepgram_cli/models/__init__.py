from __future__ import annotations

from typing import Optional, Dict, Any, List

from pydantic import BaseModel, Field


class BaseResult(BaseModel):
    """Common base for all command result payloads.

    Having a shared superclass lets the output layer detect any
    result produced by commands even if we add new ones later.
    """

    status: str = Field(default="success",
                        description="Outcome marker/messages key")
    message: Optional[str] = None


class LoginResult(BaseResult):
    """Return structure for `deepctl login` command."""

    profile: str
    api_key_masked: Optional[str] = Field(
        None, description="Obfuscated key for display – e.g. ****abcd")
    project_id: Optional[str] = None
    config_path: Optional[str] = None


class LogoutResult(BaseResult):
    """Return structure for logout command."""

    profile: Optional[str] = None
    profiles_count: Optional[int] = None  # when --all is used


class ProfileInfo(BaseModel):
    api_key: Optional[str]
    project_id: Optional[str]
    base_url: str


class ProfilesResult(BaseResult):
    """List of profiles with optional current indicator."""

    current_profile: Optional[str] = None
    profiles: Dict[str, ProfileInfo] = Field(default_factory=dict)


class PluginInfo(BaseModel):
    name: str
    help: str
    short_help: Optional[str]
    type: str  # builtin | external
    module: str


class PluginListResult(BaseResult):
    plugins: List[PluginInfo] = Field(default_factory=list)

# ----------------------------
# Projects / Usage / Transcribe
# ----------------------------


class ProjectInfo(BaseModel):
    project_id: str
    name: str
    company: Optional[str] = None


class ProjectsResult(BaseResult):
    projects: List[ProjectInfo] = Field(default_factory=list)
    count: int = 0


class TranscribeResult(BaseResult):
    source: str
    model: str
    language: str
    transcript: str
    saved_to: Optional[str] = None
    full_result: Dict[str, Any]


class UsageBucket(BaseModel):
    start: str  # ISO date
    end: str
    hours: float


class UsageResult(BaseResult):
    project_id: str
    buckets: List[UsageBucket] = Field(default_factory=list)
    total_hours: float = 0.0


class ProjectList(BaseResult):
    projects: List[ProjectInfo] = Field(default_factory=list)
    count: int = 0


class FileInfo(BaseModel):
    """File information for validation results."""

    path: str
    name: Optional[str] = None
    extension: Optional[str] = None
    size_bytes: Optional[int] = None
    size_mb: Optional[float] = None
    modified: Optional[float] = None
    readable: Optional[bool] = None
    exists: bool = False
    is_file: Optional[bool] = None
    is_audio: Optional[bool] = None
    error: Optional[str] = None


class ErrorResult(BaseModel):
    """Generic error result for any command failures."""

    error: str
    status: str = "error"
