"""Models for update command."""

from typing import Optional

from deepctl_core import BaseResult


class UpdateResult(BaseResult):
    """Result from update command."""

    success: bool  # Override to make it required
    current_version: Optional[str] = None
    latest_version: Optional[str] = None
    update_available: Optional[bool] = None
    installation_method: Optional[str] = None
