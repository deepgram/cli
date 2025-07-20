"""Models for login command."""

from typing import Optional
from pydantic import Field
from deepctl_core import BaseResult


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
