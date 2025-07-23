"""Models for login command."""

from deepctl_core import BaseResult
from pydantic import Field


class LoginResult(BaseResult):
    """Return structure for `deepctl login` command."""

    profile: str
    api_key_masked: str | None = Field(
        None, description="Obfuscated key for display – e.g. ****abcd"
    )
    project_id: str | None = None
    config_path: str | None = None


class LogoutResult(BaseResult):
    """Return structure for logout command."""

    profile: str | None = None
    profiles_count: int | None = None  # when --all is used
