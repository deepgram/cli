"""Tests for models command."""

from unittest.mock import Mock

import pytest
from deepctl_cmd_models.command import ModelsCommand
from deepctl_cmd_models.models import ModelInfo, ModelsResult
from deepctl_core import AuthManager, BaseResult, Config, DeepgramClient


class TestModelsCommand:
    """Test cases for ModelsCommand."""

    @pytest.fixture
    def command(self):
        """Create a ModelsCommand instance."""
        return ModelsCommand()

    @pytest.fixture
    def mock_config(self):
        """Create a mock config."""
        return Mock(spec=Config)

    @pytest.fixture
    def mock_auth_manager(self):
        """Create a mock auth manager."""
        manager = Mock(spec=AuthManager)
        manager.get_api_key.return_value = "test-api-key"
        return manager

    @pytest.fixture
    def mock_client(self):
        """Create a mock Deepgram client."""
        return Mock(spec=DeepgramClient)

    @pytest.fixture
    def sample_models_response(self):
        """Sample response from client.list_models()."""
        return {
            "stt": [
                {
                    "uuid": "stt-uuid-1",
                    "name": "Nova-3",
                    "version": "1.0",
                    "language": "en",
                },
            ],
            "tts": [
                {
                    "uuid": "tts-uuid-1",
                    "name": "Aura-2",
                    "version": "1.0",
                    "language": "en",
                },
            ],
        }

    def test_command_properties(self, command):
        """Test command basic properties."""
        assert command.name == "models"
        assert command.requires_auth is True
        assert command.ci_friendly is True

    def test_get_arguments(self, command):
        """Test command arguments configuration."""
        args = command.get_arguments()

        option_names = []
        for arg in args:
            if arg.get("is_option", False):
                option_names.extend(arg["names"])

        assert "--type" in option_names
        assert "-t" in option_names
        assert "--include-outdated" in option_names

    def test_handle_list_models(
        self,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        sample_models_response,
    ):
        """Test listing all models returns correct result."""
        mock_client.list_models.return_value = sample_models_response

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
        )

        assert isinstance(result, ModelsResult)
        assert result.status == "success"
        assert result.count == 2
        assert len(result.models) == 2

        model_names = [m.name for m in result.models]
        assert "Nova-3" in model_names
        assert "Aura-2" in model_names

    def test_handle_filter_stt_only(
        self,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        sample_models_response,
    ):
        """Test filtering by type='stt' excludes tts models."""
        mock_client.list_models.return_value = sample_models_response

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            type="stt",
        )

        assert isinstance(result, ModelsResult)
        assert result.status == "success"
        assert result.count == 1
        assert len(result.models) == 1
        assert result.models[0].name == "Nova-3"
        assert result.models[0].model_type == "stt"

    def test_handle_filter_tts_only(
        self,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        sample_models_response,
    ):
        """Test filtering by type='tts' excludes stt models."""
        mock_client.list_models.return_value = sample_models_response

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            type="tts",
        )

        assert isinstance(result, ModelsResult)
        assert result.status == "success"
        assert result.count == 1
        assert len(result.models) == 1
        assert result.models[0].name == "Aura-2"
        assert result.models[0].model_type == "tts"

    def test_handle_no_models(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test empty response returns info status."""
        mock_client.list_models.return_value = {"stt": [], "tts": []}

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
        )

        assert isinstance(result, ModelsResult)
        assert result.status == "info"
        assert "No models found" in result.message

    def test_handle_error(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test client exception returns error status."""
        mock_client.list_models.side_effect = Exception("API connection failed")

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
        )

        assert isinstance(result, BaseResult)
        assert result.status == "error"
        assert "API connection failed" in result.message

    def test_handle_include_outdated(
        self,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        sample_models_response,
    ):
        """Test include_outdated flag is passed to client."""
        mock_client.list_models.return_value = sample_models_response

        command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            include_outdated=True,
        )

        mock_client.list_models.assert_called_once_with(include_outdated=True)


class TestModelsResult:
    """Test cases for ModelsResult model."""

    def test_create_models_result(self):
        """Test creating a ModelsResult."""
        models = [
            ModelInfo(
                model_id="uuid-1",
                name="Nova-3",
                version="1.0",
                language="en",
                model_type="stt",
            ),
        ]
        result = ModelsResult(
            status="success",
            models=models,
            count=1,
        )

        assert result.status == "success"
        assert result.count == 1
        assert len(result.models) == 1
        assert result.models[0].name == "Nova-3"

    def test_models_result_defaults(self):
        """Test ModelsResult with default values."""
        result = ModelsResult()

        assert result.status == "success"
        assert result.models == []
        assert result.count == 0

    def test_models_result_serialization(self):
        """Test ModelsResult can be serialized."""
        result = ModelsResult(
            status="success",
            models=[
                ModelInfo(
                    model_id="uuid-1",
                    name="Nova-3",
                    version="1.0",
                    language="en",
                    model_type="stt",
                ),
            ],
            count=1,
        )

        data = result.model_dump()
        assert data["count"] == 1
        assert len(data["models"]) == 1
        assert data["models"][0]["name"] == "Nova-3"
