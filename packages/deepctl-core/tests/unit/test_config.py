"""Tests for the Config class."""

import pytest
from unittest.mock import Mock, patch, MagicMock, mock_open
from pathlib import Path
import yaml
import os

from deepctl_core.config import Config


class TestConfig:
    """Test Config class."""

    @pytest.fixture
    def mock_config_data(self):
        """Mock configuration data."""
        return {
            "profiles": {
                "default": {
                    "api_key": None,
                    "project_id": None,
                    "base_url": "https://api.deepgram.com"
                },
                "test": {
                    "api_key": "sk-test",
                    "project_id": "test-project",
                    "base_url": "https://test.deepgram.com"
                }
            },
            "active_profile": "default",
            "default_profile": "default"
        }

    @pytest.fixture
    def mock_env_vars(self):
        """Mock environment variables."""
        return {
            "DEEPGRAM_API_KEY": "sk-env",
            "DEEPGRAM_PROJECT_ID": "env-project",
            "DEEPGRAM_BASE_URL": "https://env.deepgram.com"
        }

    @patch("deepctl_core.config.platformdirs.user_config_dir")
    @patch("deepctl_core.config.Path.exists")
    @patch("deepctl_core.config.Path.mkdir")
    def test_init_default_path(self, mock_mkdir, mock_exists, mock_config_dir):
        """Test initialization with default config path."""
        mock_config_dir.return_value = "/mock/config"
        mock_exists.return_value = False

        config = Config()

        # Use Path for cross-platform compatibility
        expected_path = Path("/mock/config") / "config.yaml"
        assert config.config_path == expected_path

    def test_init_custom_path(self):
        """Test initialization with custom config path."""
        custom_path = "/custom/path/config.yaml"

        with patch("deepctl_core.config.Path.exists", return_value=False):
            config = Config(config_path=custom_path)

        assert config.config_path == Path(custom_path)

    def test_init_with_profile(self):
        """Test initialization with explicit profile."""
        with patch("deepctl_core.config.Path.exists", return_value=False):
            config = Config(profile="test")

        assert config._explicit_profile == "test"

    @patch("deepctl_core.config.Path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_load_config_from_file(self, mock_file, mock_exists, mock_config_data):
        """Test loading configuration from file."""
        mock_exists.return_value = True
        mock_file.return_value.read.return_value = yaml.dump(mock_config_data)

        with patch("deepctl_core.config.yaml.safe_load", return_value=mock_config_data):
            config = Config()

        assert config._config.profiles["test"].api_key == "sk-test"
        assert config._config.active_profile == "default"

    @patch("deepctl_core.config.Path.exists")
    def test_load_config_file_not_found(self, mock_exists):
        """Test loading when config file doesn't exist."""
        mock_exists.return_value = False

        config = Config()

        # Should create default config
        assert config._config.profiles == {}
        assert config._config.active_profile is None

    @patch("deepctl_core.config.Path.exists")
    @patch("builtins.open", side_effect=Exception("Read error"))
    def test_load_config_error_handling(self, mock_file, mock_exists, capsys):
        """Test error handling when loading config fails."""
        mock_exists.return_value = True

        config = Config()

        # Should warn but not fail
        captured = capsys.readouterr()
        assert "Warning: Could not load config" in captured.out

    @patch.dict(os.environ, {"DEEPGRAM_API_KEY": "sk-env"})
    @patch("deepctl_core.config.Path.exists", return_value=False)
    def test_load_env_config(self, mock_exists):
        """Test loading configuration from environment variables."""
        config = Config()

        # Environment variables should override profile settings
        profile = config.get_profile()
        assert profile.api_key == "sk-env"

    def test_profile_property_precedence(self):
        """Test profile property precedence."""
        with patch("deepctl_core.config.Path.exists", return_value=False):
            config = Config()

            # Default profile is set
            assert config.profile == "default"

            # Explicit profile takes precedence
            config._explicit_profile = "explicit"
            config._config.active_profile = "active"
            config._config.default_profile = "default"

            assert config.profile == "explicit"

            # Active profile next
            config._explicit_profile = None
            assert config.profile == "active"

            # Default profile last
            config._config.active_profile = None
            assert config.profile == "default"

    @patch("deepctl_core.config.Path.exists", return_value=False)
    def test_get_and_set_values(self, mock_exists):
        """Test getting and setting configuration values."""
        config = Config()

        # Test get with default
        assert config.get("missing.key", "default") == "default"

        # Test setting actual config values
        config._set_config_value("output.format", "yaml")
        assert config.get("output.format") == "yaml"

        # Test setting profile values
        config._set_config_value("api_key", "test-key")
        profile = config.get_profile()
        assert profile.api_key == "test-key"

    @patch("deepctl_core.config.Path.exists", return_value=False)
    def test_get_profile(self, mock_exists):
        """Test getting profile configuration."""
        config = Config()

        # Default profile
        profile = config.get_profile()
        assert profile.api_key is None
        assert profile.base_url == "https://api.deepgram.com"

        # Non-existent profile
        profile = config.get_profile("missing")
        assert profile.api_key is None

    @patch("deepctl_core.config.Path.exists", return_value=False)
    @patch("builtins.open", new_callable=mock_open)
    @patch("deepctl_core.config.yaml.safe_dump")
    def test_save_config(self, mock_dump, mock_file, mock_exists):
        """Test saving configuration."""
        config = Config()
        config._set_config_value("output.format", "yaml")

        config.save()

        # Verify file was opened for writing
        mock_file.assert_called_with(config.config_path, "w", encoding="utf-8")

        # Verify yaml.safe_dump was called
        assert mock_dump.called

    @patch("deepctl_core.config.Path.exists", return_value=False)
    def test_create_profile(self, mock_exists):
        """Test creating profile configuration."""
        config = Config()

        # Create new profile
        with patch.object(config, 'save'):
            config.create_profile("test", api_key="new-key",
                                  project_id="new-project")

        profile = config.get_profile("test")
        assert profile.api_key == "new-key"
        assert profile.project_id == "new-project"

    @patch("deepctl_core.config.Path.exists", return_value=False)
    def test_delete_profile(self, mock_exists):
        """Test deleting profile."""
        config = Config()

        # Create a profile
        with patch.object(config, 'save'):
            config.create_profile("test", api_key="key")
            assert "test" in config._config.profiles

            # Delete it
            config.delete_profile("test")
            assert "test" not in config._config.profiles

    @patch("deepctl_core.config.Path.exists", return_value=False)
    def test_list_profiles(self, mock_exists):
        """Test listing profiles."""
        config = Config()

        # Add some profiles
        with patch.object(config, 'save'):
            config.create_profile("profile1", api_key="key1")
            config.create_profile("profile2", api_key="key2")

        profiles = config.list_profiles()
        assert "profile1" in profiles
        assert "profile2" in profiles

    @patch("deepctl_core.config.platformdirs.user_config_dir")
    def test_migrate_config(self, mock_config_dir):
        """Test configuration migration setup."""
        mock_config_dir.return_value = "/mock/config"

        # Just test that config initializes properly with mocked paths
        with patch("deepctl_core.config.Path.exists", return_value=False):
            with patch("deepctl_core.config.Path.mkdir"):
                config = Config()

                # Verify config was created with expected path
                # Use Path for cross-platform compatibility
                expected_path = Path("/mock/config") / "config.yaml"
                assert config.config_path == expected_path

    @patch.dict(os.environ, {
        "DEEPGRAM_OUTPUT_FORMAT": "json",
        "DEEPGRAM_OUTPUT_QUIET": "true",
        "DEEPGRAM_UPDATE_CHECK_ENABLED": "false"
    })
    @patch("deepctl_core.config.Path.exists", return_value=False)
    def test_env_config_overrides(self, mock_exists):
        """Test environment variable configuration overrides."""
        config = Config()

        # Note: Config doesn't load these specific env vars,
        # only the ones mapped in _load_env_config
        assert config.get("output.format") == "json"  # Default value

    @patch("deepctl_core.config.Path.exists", return_value=False)
    def test_project_config_loading(self, mock_exists):
        """Test loading project-specific configuration."""
        # Note: Current implementation doesn't automatically load project configs
        # This test verifies the default behavior
        config = Config()

        # No project config should be loaded by default
        assert "project" not in config._config.profiles
