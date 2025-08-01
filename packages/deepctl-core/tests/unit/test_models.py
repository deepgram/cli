"""Tests for the core models."""

import pytest
from pydantic import ValidationError

from deepctl_core.models import (
    BaseResult,
    ErrorResult,
    PluginInfo,
    ProfileInfo,
    ProfilesResult,
)


class TestBaseResult:
    """Test BaseResult model."""

    def test_base_result_default(self):
        """Test BaseResult with default values."""
        result = BaseResult()

        assert result.status == "success"
        assert result.message is None

    def test_base_result_with_values(self):
        """Test BaseResult with custom values."""
        result = BaseResult(status="error", message="Something failed")

        assert result.status == "error"
        assert result.message == "Something failed"

    def test_base_result_dict_export(self):
        """Test exporting BaseResult to dict."""
        result = BaseResult(status="success", message="Done")
        data = result.model_dump()

        assert data["status"] == "success"
        assert data["message"] == "Done"

    def test_base_result_json_export(self):
        """Test exporting BaseResult to JSON."""
        result = BaseResult(status="success")
        json_str = result.model_dump_json()

        assert '"status":"success"' in json_str

    def test_base_result_exclude_none(self):
        """Test excluding None values when exporting."""
        result = BaseResult(status="success", message=None)
        data = result.model_dump(exclude_none=True)

        assert "status" in data
        assert "message" not in data


class TestErrorResult:
    """Test ErrorResult model."""

    def test_error_result_minimal(self):
        """Test ErrorResult with minimal data."""
        result = ErrorResult(error="Not found")

        assert result.status == "error"
        assert result.error == "Not found"

    def test_error_result_custom_status(self):
        """Test ErrorResult with custom status."""
        result = ErrorResult(
            error="Connection failed", status="critical_error"
        )

        assert result.status == "critical_error"
        assert result.error == "Connection failed"

    def test_error_result_validation(self):
        """Test ErrorResult validation."""
        # Should require error field
        with pytest.raises(ValidationError):
            ErrorResult()

    def test_error_result_from_exception(self):
        """Test creating ErrorResult from exception."""
        try:
            raise ValueError("Test error")
        except ValueError as e:
            result = ErrorResult(error=str(e))

        assert result.error == "Test error"
        assert result.status == "error"


class TestPluginInfo:
    """Test PluginInfo model."""

    def test_plugin_info_minimal(self):
        """Test PluginInfo with minimal data."""
        plugin = PluginInfo(
            name="test-plugin",
            help="Test plugin for unit tests",
            short_help=None,
            type="external",
            module="test_plugin",
        )

        assert plugin.name == "test-plugin"
        assert plugin.help == "Test plugin for unit tests"
        assert plugin.short_help is None
        assert plugin.type == "external"
        assert plugin.module == "test_plugin"

    def test_plugin_info_full(self):
        """Test PluginInfo with all fields."""
        plugin = PluginInfo(
            name="awesome-plugin",
            help="An awesome plugin for Deepgram CLI",
            short_help="Awesome plugin",
            type="builtin",
            module="deepctl_cmd_awesome",
        )

        assert plugin.name == "awesome-plugin"
        assert plugin.help == "An awesome plugin for Deepgram CLI"
        assert plugin.short_help == "Awesome plugin"
        assert plugin.type == "builtin"
        assert plugin.module == "deepctl_cmd_awesome"

    def test_plugin_info_validation(self):
        """Test PluginInfo validation."""
        # Should require all mandatory fields
        with pytest.raises(ValidationError):
            PluginInfo(name="plugin")

        with pytest.raises(ValidationError):
            PluginInfo(name="plugin", help="Help", type="external")

        with pytest.raises(ValidationError):
            PluginInfo(name="plugin", help="Help", module="mod")

    def test_plugin_info_type_values(self):
        """Test PluginInfo type field values."""
        # Test builtin type
        plugin1 = PluginInfo(
            name="builtin-cmd",
            help="Builtin command",
            short_help="Builtin",
            type="builtin",
            module="deepctl_cmd_builtin",
        )
        assert plugin1.type == "builtin"

        # Test external type
        plugin2 = PluginInfo(
            name="external-cmd",
            help="External command",
            short_help="External",
            type="external",
            module="external_cmd",
        )
        assert plugin2.type == "external"


class TestProfileInfo:
    """Test ProfileInfo model."""

    def test_profile_info_minimal(self):
        """Test ProfileInfo with minimal data."""
        profile = ProfileInfo(
            api_key=None, project_id=None, base_url="https://api.deepgram.com"
        )

        assert profile.api_key is None
        assert profile.project_id is None
        assert profile.base_url == "https://api.deepgram.com"

    def test_profile_info_full(self):
        """Test ProfileInfo with all fields."""
        profile = ProfileInfo(
            api_key="sk-production",
            project_id="proj-123",
            base_url="https://api.deepgram.com",
        )

        assert profile.api_key == "sk-production"
        assert profile.project_id == "proj-123"
        assert profile.base_url == "https://api.deepgram.com"

    def test_profile_info_validation(self):
        """Test ProfileInfo validation."""
        # Should require all fields (even if None)
        with pytest.raises(ValidationError):
            ProfileInfo()

        with pytest.raises(ValidationError):
            ProfileInfo(api_key="key", project_id="proj")

        with pytest.raises(ValidationError):
            ProfileInfo(base_url="https://api.deepgram.com")

    def test_profile_info_custom_base_url(self):
        """Test ProfileInfo with custom base URL."""
        profile = ProfileInfo(
            api_key="sk-test",
            project_id="test-proj",
            base_url="https://custom.deepgram.com",
        )

        assert profile.base_url == "https://custom.deepgram.com"


class TestProfilesResult:
    """Test ProfilesResult model."""

    def test_profiles_result_empty(self):
        """Test ProfilesResult with no profiles."""
        result = ProfilesResult()

        assert result.status == "success"
        assert result.profiles == {}
        assert result.current_profile is None

    def test_profiles_result_with_profiles(self):
        """Test ProfilesResult with multiple profiles."""
        profiles = {
            "default": ProfileInfo(
                api_key="sk-default",
                project_id="proj-default",
                base_url="https://api.deepgram.com",
            ),
            "staging": ProfileInfo(
                api_key="sk-staging",
                project_id="proj-staging",
                base_url="https://staging.deepgram.com",
            ),
            "production": ProfileInfo(
                api_key="sk-prod",
                project_id="proj-prod",
                base_url="https://api.deepgram.com",
            ),
        }

        result = ProfilesResult(profiles=profiles, current_profile="default")

        assert result.status == "success"
        assert len(result.profiles) == 3
        assert result.current_profile == "default"
        assert result.profiles["staging"].api_key == "sk-staging"

    def test_profiles_result_default_factory(self):
        """Test ProfilesResult profiles default factory."""
        result1 = ProfilesResult()
        result2 = ProfilesResult()

        # Each instance should have its own dict
        result1.profiles["test"] = ProfileInfo(
            api_key="key",
            project_id="proj",
            base_url="https://api.deepgram.com",
        )

        assert "test" in result1.profiles
        assert "test" not in result2.profiles

    def test_profiles_result_serialization(self):
        """Test ProfilesResult serialization."""
        profiles = {
            "default": ProfileInfo(
                api_key="sk-default",
                project_id="proj-default",
                base_url="https://api.deepgram.com",
            ),
            "dev": ProfileInfo(
                api_key=None,
                project_id=None,
                base_url="https://dev.deepgram.com",
            ),
        }

        result = ProfilesResult(profiles=profiles, current_profile="default")
        data = result.model_dump()

        assert data["status"] == "success"
        assert len(data["profiles"]) == 2
        assert data["profiles"]["default"]["api_key"] == "sk-default"
        assert data["profiles"]["dev"]["api_key"] is None
        assert data["current_profile"] == "default"
