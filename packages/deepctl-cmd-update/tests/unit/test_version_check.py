"""Unit tests for version checking."""

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from deepctl_cmd_update.version_check import (
    _FREQUENCY_DURATIONS,
    VersionChecker,
    VersionInfo,
    format_version_message,
)


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    """dg's cache directory, pointed at a fresh temp dir (not created yet)."""
    from deepctl import _cache

    root = tmp_path / "cache" / "deepctl"
    monkeypatch.setattr(_cache, "cache_root", lambda: str(root))
    return root


@pytest.fixture(autouse=True)
def _isolated_cache(cache_dir):
    """Every test here gets its own empty cache directory."""
    from deepctl_cmd_update import check_cache

    assert check_cache.usable()
    return cache_dir


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


class TestCacheFile:
    """The last answer lives in the cache file, never in the user config."""

    @staticmethod
    def _config(values=None):
        config = MagicMock()
        values = {"update.check_enabled": True, **(values or {})}
        config.get.side_effect = lambda key, default=None: values.get(key, default)
        return config

    @pytest.mark.asyncio
    async def test_answer_goes_to_the_cache_file(self, cache_dir):
        from deepctl_cmd_update import check_cache

        config = self._config()
        checker = VersionChecker(config, "1.0.0")
        info = VersionInfo(
            current_version="1.0.0", latest_version="2.0.0", update_available=True
        )
        with patch.object(checker, "_check_pypi", AsyncMock(return_value=info)):
            await checker.check_version(force=True)

        state = check_cache.read(check_cache.UPDATE_FILE)
        assert state is not None
        assert (state["ok"], state["latest"], state["source"]) == (
            True,
            "2.0.0",
            "pypi",
        )
        config.save.assert_not_called()
        config._set_config_value.assert_not_called()

    @pytest.mark.asyncio
    async def test_github_answer_names_its_source(self):
        from deepctl_cmd_update import check_cache

        checker = VersionChecker(self._config(), "1.0.0")
        response = MagicMock()
        response.json.return_value = {"tag_name": "v2.0.0", "html_url": "u"}
        client = AsyncMock()
        client.get.return_value = response
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        with patch("httpx.AsyncClient", return_value=client):
            await checker.check_version(force=True, install_method="homebrew")
        state = check_cache.read(check_cache.UPDATE_FILE)
        assert state is not None and state["source"] == "github"

    def test_cache_file_wins_over_legacy_keys(self):
        from deepctl_cmd_update import check_cache

        check_cache.write(check_cache.UPDATE_FILE, ok=True, latest="3.0.0", source="pypi")
        config = self._config(
            {
                "update.last_check": "2000-01-01T00:00:00+00:00",
                "update.cached_version_info": {
                    "current_version": "1.0.0",
                    "latest_version": "9.9.9",
                    "update_available": True,
                },
            }
        )
        checker = VersionChecker(config, "1.0.0")
        assert checker.should_check() is False
        info = checker._get_cached_info()
        assert info is not None
        assert (info.latest_version, info.update_available) == ("3.0.0", True)

    def test_failed_check_has_no_cached_answer(self):
        from deepctl_cmd_update import check_cache

        check_cache.write(check_cache.UPDATE_FILE, ok=False, latest=None, source="pypi")
        checker = VersionChecker(self._config(), "1.0.0")
        assert checker.should_check() is False
        assert checker._get_cached_info() is None

    def test_legacy_keys_read_when_no_cache_file(self):
        config = self._config(
            {
                "update.cached_version_info": {
                    "current_version": "1.0.0",
                    "latest_version": "1.5.0",
                    "update_available": True,
                },
            }
        )
        info = VersionChecker(config, "1.0.0")._get_cached_info()
        assert info is not None and info.latest_version == "1.5.0"
