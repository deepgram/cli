"""Version checking functionality for deepctl."""

from datetime import datetime, timedelta

import httpx
from deepctl_core import Config
from packaging import version
from pydantic import BaseModel, Field


class VersionInfo(BaseModel):
    """Version information from PyPI."""

    current_version: str
    latest_version: str
    update_available: bool
    release_date: datetime | None = None
    release_notes_url: str | None = None
    check_timestamp: datetime = Field(default_factory=datetime.now)


# Mapping from config check_frequency values to timedelta
_FREQUENCY_DURATIONS: dict[str, timedelta | None] = {
    "daily": timedelta(hours=24),
    "weekly": timedelta(days=7),
    "never": None,  # Sentinel: skip checking entirely
}


class VersionChecker:
    """Handles version checking against PyPI and GitHub Releases."""

    PYPI_API_URL = "https://pypi.org/pypi/{package}/json"
    GITHUB_RELEASES_URL = "https://api.github.com/repos/deepgram/cli/releases/latest"
    PACKAGE_NAME = "deepctl"

    def __init__(self, config: Config, current_version: str = "0.0.0"):
        """Initialize version checker.

        Args:
            config: Configuration instance
            current_version: Current installed version
        """
        self.config = config
        self.current_version = current_version

    def _get_cache_duration(self) -> timedelta | None:
        """Get the cache duration based on ``check_frequency`` config.

        Returns:
            A timedelta for the cache window, or ``None`` when
            checking is disabled (``never``).
        """
        frequency: str = self.config.get("update.check_frequency", "daily")
        return _FREQUENCY_DURATIONS.get(frequency, timedelta(hours=24))

    async def check_version(
        self,
        force: bool = False,
        install_method: str | None = None,
    ) -> VersionInfo:
        """Check for newer version.

        Uses GitHub Releases API when *install_method* is ``homebrew``,
        otherwise queries PyPI.

        Args:
            force: Force check even if recently checked
            install_method: Optional installation method hint

        Returns:
            Version information including update availability
        """
        # Check if we should skip based on cache
        if not force and not self.should_check():
            # Return cached info if available
            cached_info = self._get_cached_info()
            if cached_info:
                return cached_info

        # Choose version source
        if install_method == "homebrew":
            version_info = await self._check_github_releases()
        else:
            version_info = await self._check_pypi()

        if version_info is not None:
            self._cache_info(version_info)
            return version_info

        # On error, return current version with no update
        return VersionInfo(
            current_version=self.current_version,
            latest_version=self.current_version,
            update_available=False,
        )

    async def _check_pypi(self) -> VersionInfo | None:
        """Fetch latest version from PyPI.

        Returns:
            VersionInfo or None on error
        """
        try:
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
                    upload_time = releases[0].get("upload_time_iso_8601")
                    if upload_time:
                        release_date = datetime.fromisoformat(
                            upload_time.replace("Z", "+00:00")
                        )

            # Compare versions
            current = version.parse(self.current_version)
            latest = version.parse(latest_version)
            update_available = latest > current

            return VersionInfo(
                current_version=self.current_version,
                latest_version=latest_version,
                update_available=update_available,
                release_date=release_date,
                release_notes_url=(
                    f"https://github.com/deepgram/cli/releases/tag/v{latest_version}"
                ),
            )
        except Exception:
            return None

    async def _check_github_releases(self) -> VersionInfo | None:
        """Fetch latest version from GitHub Releases.

        Returns:
            VersionInfo or None on error
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    self.GITHUB_RELEASES_URL,
                    timeout=10.0,
                    headers={"Accept": "application/vnd.github.v3+json"},
                )
                response.raise_for_status()
                data = response.json()

            # Tag name is typically "v1.2.3"
            tag_name: str = data.get("tag_name", "")
            latest_version = tag_name.lstrip("v") if tag_name else self.current_version

            release_date = None
            published_at = data.get("published_at")
            if published_at:
                release_date = datetime.fromisoformat(
                    published_at.replace("Z", "+00:00")
                )

            release_notes_url = data.get("html_url")

            current = version.parse(self.current_version)
            latest = version.parse(latest_version)
            update_available = latest > current

            return VersionInfo(
                current_version=self.current_version,
                latest_version=latest_version,
                update_available=update_available,
                release_date=release_date,
                release_notes_url=release_notes_url,
            )
        except Exception:
            # Fall back to PyPI on GitHub API failure
            return await self._check_pypi()

    def should_check(self) -> bool:
        """Determine if version check should run.

        Returns:
            True if check should run, False otherwise
        """
        # Check if updates are disabled
        if not self.config.get("update.check_enabled", True):
            return False

        # Honour check_frequency
        cache_duration = self._get_cache_duration()
        if cache_duration is None:
            # "never" — always skip
            return False

        # Check last check timestamp
        last_check = self.config.get("update.last_check")
        if last_check:
            try:
                last_check_time = datetime.fromisoformat(last_check)
                if datetime.now() - last_check_time < cache_duration:
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
                pass
        return None

    def _cache_info(self, info: VersionInfo) -> None:
        """Cache version info in config.

        Args:
            info: Version info to cache
        """
        self.config._set_config_value("update.last_check", datetime.now().isoformat())
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

    message = f"Update available: {info.current_version} → {info.latest_version}"
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
