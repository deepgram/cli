"""Unit tests for version checking."""

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from deepctl_cmd_update.version_check import (
    VersionChecker,
    VersionInfo,
    _FREQUENCY_DURATIONS,
    format_version_message,
)


class TestCheckFrequency:
    """Test check_frequency configuration values."""

    def test_daily_duration(self):
        assert _FREQUENCY_DURATIONS["daily"] == timedelta(hours=24)

    def test_weekly_duration(self):
        assert _FREQUENCY_DURATIONS["weekly"] == timedelta(days=7)

    def test_never_disables(self):
        assert _FREQUENCY_DURATIONS["never"] is None

    def test_get_cache_duration_daily(self):
        config = MagicMock()
        config.get.return_value = "daily"
        checker = VersionChecker(config, "1.0.0")
        assert checker._get_cache_duration() == timedelta(hours=24)

    def test_get_cache_duration_weekly(self):
        config = MagicMock()
        config.get.return_value = "weekly"
        checker = VersionChecker(config, "1.0.0")
        assert checker._get_cache_duration() == timedelta(days=7)

    def test_get_cache_duration_never(self):
        config = MagicMock()
        config.get.return_value = "never"
        checker = VersionChecker(config, "1.0.0")
        assert checker._get_cache_duration() is None

    def test_should_check_never_returns_false(self):
        """When frequency is 'never', should_check returns False."""
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "update.check_enabled": True,
            "update.check_frequency": "never",
        }.get(key, default)
        checker = VersionChecker(config, "1.0.0")
        assert checker.should_check() is False

    def test_should_check_weekly_within_window(self):
        """Within the weekly window, should_check returns False."""
        recent = (datetime.now() - timedelta(days=3)).isoformat()
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "update.check_enabled": True,
            "update.check_frequency": "weekly",
            "update.last_check": recent,
        }.get(key, default)
        checker = VersionChecker(config, "1.0.0")
        assert checker.should_check() is False

    def test_should_check_weekly_outside_window(self):
        """Outside the weekly window, should_check returns True."""
        old = (datetime.now() - timedelta(days=10)).isoformat()
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "update.check_enabled": True,
            "update.check_frequency": "weekly",
            "update.last_check": old,
        }.get(key, default)
        checker = VersionChecker(config, "1.0.0")
        assert checker.should_check() is True


class TestGitHubReleasesCheck:
    """Test GitHub Releases API version check."""

    @pytest.mark.asyncio
    async def test_github_releases_success(self):
        """Successful GitHub Releases API check."""
        config = MagicMock()
        checker = VersionChecker(config, "1.0.0")

        github_response = {
            "tag_name": "v2.0.0",
            "published_at": "2025-01-15T12:00:00Z",
            "html_url": "https://github.com/deepgram/cli/releases/tag/v2.0.0",
        }

        mock_response = MagicMock()
        mock_response.json.return_value = github_response
        mock_response.raise_for_status = MagicMock()

        async def mock_get(*args, **kwargs):
            return mock_response

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            result = await checker._check_github_releases()

        assert result is not None
        assert result.latest_version == "2.0.0"
        assert result.update_available is True
        assert result.release_notes_url == (
            "https://github.com/deepgram/cli/releases/tag/v2.0.0"
        )

    @pytest.mark.asyncio
    async def test_github_releases_strips_v_prefix(self):
        """Tag name 'v' prefix is stripped correctly."""
        config = MagicMock()
        checker = VersionChecker(config, "1.0.0")

        github_response = {
            "tag_name": "v1.5.3",
            "published_at": None,
            "html_url": "https://github.com/deepgram/cli/releases/tag/v1.5.3",
        }

        mock_response = MagicMock()
        mock_response.json.return_value = github_response
        mock_response.raise_for_status = MagicMock()

        async def mock_get(*args, **kwargs):
            return mock_response

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            result = await checker._check_github_releases()

        assert result is not None
        assert result.latest_version == "1.5.3"

    @pytest.mark.asyncio
    async def test_github_releases_fallback_to_pypi(self):
        """Falls back to PyPI when GitHub API fails."""
        config = MagicMock()
        checker = VersionChecker(config, "1.0.0")

        # Mock GitHub to fail, PyPI to succeed
        pypi_response = {
            "info": {"version": "1.5.0"},
            "releases": {},
        }

        call_count = 0

        async def mock_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # GitHub fails
                raise httpx.HTTPError("API rate limited")
            # PyPI succeeds
            mock_resp = MagicMock()
            mock_resp.json.return_value = pypi_response
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            result = await checker._check_github_releases()

        assert result is not None
        assert result.latest_version == "1.5.0"


class TestCheckVersionWithInstallMethod:
    """Test check_version with install_method parameter."""

    @pytest.mark.asyncio
    async def test_homebrew_uses_github(self):
        """When install_method='homebrew', GitHub is queried."""
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "update.check_enabled": True,
            "update.check_frequency": "daily",
            "update.last_check": None,
            "update.cached_version_info": None,
        }.get(key, default)

        checker = VersionChecker(config, "1.0.0")

        mock_info = VersionInfo(
            current_version="1.0.0",
            latest_version="2.0.0",
            update_available=True,
        )

        with patch.object(
            checker,
            "_check_github_releases",
            return_value=mock_info,
        ) as mock_gh:
            result = await checker.check_version(
                force=True, install_method="homebrew"
            )

        mock_gh.assert_called_once()
        assert result.latest_version == "2.0.0"

    @pytest.mark.asyncio
    async def test_pip_uses_pypi(self):
        """When install_method is not 'homebrew', PyPI is queried."""
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "update.check_enabled": True,
            "update.check_frequency": "daily",
            "update.last_check": None,
            "update.cached_version_info": None,
        }.get(key, default)

        checker = VersionChecker(config, "1.0.0")

        mock_info = VersionInfo(
            current_version="1.0.0",
            latest_version="1.5.0",
            update_available=True,
        )

        with patch.object(
            checker, "_check_pypi", return_value=mock_info
        ) as mock_pypi:
            result = await checker.check_version(
                force=True, install_method="pip"
            )

        mock_pypi.assert_called_once()
        assert result.latest_version == "1.5.0"


class TestFormatVersionMessage:
    """Test version message formatting."""

    def test_up_to_date(self):
        info = VersionInfo(
            current_version="1.0.0",
            latest_version="1.0.0",
            update_available=False,
        )
        msg = format_version_message(info)
        assert "latest version" in msg
        assert "1.0.0" in msg

    def test_update_available(self):
        info = VersionInfo(
            current_version="1.0.0",
            latest_version="2.0.0",
            update_available=True,
        )
        msg = format_version_message(info)
        assert "1.0.0" in msg
        assert "2.0.0" in msg
        assert "dg update" in msg
