"""Version checking functionality for deepctl."""

from datetime import datetime, timedelta

import httpx
from packaging import version
from pydantic import BaseModel, Field

from deepctl_core import Config


class VersionInfo(BaseModel):
    """Version information from PyPI."""

    current_version: str
    latest_version: str
    update_available: bool
    release_date: datetime | None = None
    release_notes_url: str | None = None
    check_timestamp: datetime = Field(default_factory=datetime.now)


class VersionChecker:
    """Handles version checking against PyPI."""

    PYPI_API_URL = "https://pypi.org/pypi/{package}/json"
    CACHE_DURATION = timedelta(hours=24)
    PACKAGE_NAME = "deepctl"

    def __init__(self, config: Config, current_version: str = "0.1.5"):
        """Initialize version checker.

        Args:
            config: Configuration instance
            current_version: Current installed version
        """
        self.config = config
        self.current_version = current_version

    async def check_version(self, force: bool = False) -> VersionInfo:
        """Check for newer version on PyPI.

        Args:
            force: Force check even if recently checked

        Returns:
            Version information including update availability
        """
        # Check if we should skip based on cache
        if not force and not self.should_check():
            # Return cached info if available
            cached_info = self._get_cached_info()
            if cached_info:
                return cached_info

        try:
            # Fetch latest version from PyPI
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    self.PYPI_API_URL.format(package=self.PACKAGE_NAME),
                    timeout=10.0,
                )
                response.raise_for_status()
                data = response.json()

            # Extract version info
            latest_version = data["info"]["version"]

            # Get release date for latest version
            release_date = None
            if latest_version in data["releases"]:
                releases = data["releases"][latest_version]
                if releases:
                    # Get upload time of first file
                    upload_time = releases[0].get("upload_time_iso_8601")
                    if upload_time:
                        release_date = datetime.fromisoformat(
                            upload_time.replace("Z", "+00:00")
                        )

            # Compare versions
            current = version.parse(self.current_version)
            latest = version.parse(latest_version)
            update_available = latest > current

            # Create version info
            version_info = VersionInfo(
                current_version=self.current_version,
                latest_version=latest_version,
                update_available=update_available,
                release_date=release_date,
                release_notes_url=f"https://github.com/deepgram/cli/releases/tag/v{latest_version}",
            )

            # Cache the result
            self._cache_info(version_info)

            return version_info

        except Exception:
            # On error, return current version with no update
            return VersionInfo(
                current_version=self.current_version,
                latest_version=self.current_version,
                update_available=False,
            )

    def should_check(self) -> bool:
        """Determine if version check should run.

        Returns:
            True if check should run, False otherwise
        """
        # Check if updates are disabled
        if not self.config.get("update.check_enabled", True):
            return False

        # Check last check timestamp
        last_check = self.config.get("update.last_check")
        if last_check:
            try:
                last_check_time = datetime.fromisoformat(last_check)
                if datetime.now() - last_check_time < self.CACHE_DURATION:
                    return False
            except ValueError:
                # Invalid timestamp, proceed with check
                pass

        return True

    def _get_cached_info(self) -> VersionInfo | None:
        """Get cached version info from config.

        Returns:
            Cached version info or None
        """
        cached_data = self.config.get("update.cached_version_info")
        if cached_data:
            try:
                return VersionInfo(**cached_data)
            except Exception:
                # Invalid cached data
                pass
        return None

    def _cache_info(self, info: VersionInfo) -> None:
        """Cache version info in config.

        Args:
            info: Version info to cache
        """
        # Update config with version info
        self.config._set_config_value(
            "update.last_check", datetime.now().isoformat()
        )
        self.config._set_config_value(
            "update.cached_version_info", info.model_dump(mode="json")
        )
        self.config.save()


def format_version_message(info: VersionInfo) -> str:
    """Format a user-friendly version message.

    Args:
        info: Version information

    Returns:
        Formatted message string
    """
    if not info.update_available:
        return f"You are using the latest version ({info.current_version})"

    message = (
        f"Update available: {info.current_version} → {info.latest_version}"
    )
    if info.release_date:
        days_old = (datetime.now() - info.release_date).days
        if days_old == 0:
            message += " (released today)"
        elif days_old == 1:
            message += " (released yesterday)"
        else:
            message += f" (released {days_old} days ago)"

    message += "\nRun 'deepctl update' to upgrade"
    return message
