"""Models for update command."""

from deepctl_core import BaseResult


class UpdateResult(BaseResult):
    """Result from update command."""

    success: bool  # Override to make it required
    current_version: str | None = None
    latest_version: str | None = None
    update_available: bool | None = None
    installation_method: str | None = None
