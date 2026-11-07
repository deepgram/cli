"""Models for login command."""

from typing import Any

from deepctl_core import BaseResult
from deepctl_core.models import CodedErrorResult
from pydantic import Field


class LoginCodedError(CodedErrorResult):
    """A 0.5.0 coded error from ``dg login``, keys in the catalogue's order.

    ``next_step`` is always present (null when no command fixes it);
    ``details`` is left out when there are none. The 0.4.1 coded
    errors login also returns keep their keys.
    """

    next_step: str | None = None
    schema_version: int = 1
    details: dict[str, Any] | None = None

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        data = super().model_dump(**kwargs)
        if not data.get("details"):
            data.pop("details", None)
        return data


class LoginResult(BaseResult):
    """Return structure for `deepctl login` command."""

    profile: str
    api_key_masked: str | None = Field(
        None, description="Obfuscated key for display – e.g. ****abcd"
    )
    project_id: str | None = None
    config_path: str | None = None


class LoginPendingResult(BaseResult):
    """``dg login`` handed the device login to a person; exit 0."""

    status: str = "pending"
    verification_uri: str
    user_code: str
    expires_in: int
    next_step: str | None = None
    schema_version: int = 1


class LogoutResult(BaseResult):
    """Return structure for logout command."""

    profile: str | None = None
    profiles_count: int | None = None  # when --all is used


class WhoamiResult(BaseResult):
    """Return structure for `dg whoami` command."""

    authenticated: bool
    profile: str
    api_key_masked: str | None = None
    key_source: str
    project_id: str | None = None
    base_url: str | None = None
    credential_expires_at: str | None = None
