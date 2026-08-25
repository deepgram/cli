"""Tests for models command."""

from unittest.mock import Mock, patch

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


class TestModelsOutputGating:
    """stdout stays machine-parseable in json/yaml/csv modes.

    In any non-``default`` output mode the command must not write its human
    table to the stdout ``console``; the framework serialises the returned
    result to stdout, so a stray print here would corrupt piped JSON.
    """

    @pytest.fixture
    def command(self):
        return ModelsCommand()

    @staticmethod
    def _response():
        return {
            "stt": [
                {
                    "uuid": "stt-uuid-1",
                    "name": "Nova-3",
                    "version": "1.0",
                    "language": "en",
                }
            ],
            "tts": [],
        }

    @patch("deepctl_cmd_models.command.get_output_format", return_value="json")
    @patch("deepctl_cmd_models.command.console")
    def test_json_mode_writes_nothing_to_stdout(self, mock_console, _fmt, command):
        client = Mock(spec=DeepgramClient)
        client.list_models.return_value = self._response()

        result = command.handle(
            config=Mock(spec=Config),
            auth_manager=Mock(spec=AuthManager),
            client=client,
        )

        assert isinstance(result, ModelsResult)
        assert result.count == 1
        assert result.models[0].name == "Nova-3"
        mock_console.print.assert_not_called()

    @patch("deepctl_cmd_models.command.get_output_format", return_value="default")
    @patch("deepctl_cmd_models.command.console")
    def test_default_mode_renders_table(self, mock_console, _fmt, command):
        client = Mock(spec=DeepgramClient)
        client.list_models.return_value = self._response()

        command.handle(
            config=Mock(spec=Config),
            auth_manager=Mock(spec=AuthManager),
            client=client,
        )

        assert mock_console.print.called


class TestFieldMapping:
    """The SDK/API field-name fixes: uuid_ and languages.

    The Deepgram Python SDK's generated response classes rename the API's
    `uuid` field to `uuid_`, and the API reports `languages` as a list.
    The command used to read `uuid` and `language`, so every row rendered
    with an empty ID and an empty language column.
    """

    @pytest.fixture
    def command(self):
        return ModelsCommand()

    @pytest.fixture
    def mock_config(self):
        return Mock(spec=Config)

    @pytest.fixture
    def mock_auth_manager(self):
        manager = Mock(spec=AuthManager)
        manager.get_api_key.return_value = "test-api-key"
        return manager

    @pytest.fixture
    def mock_client(self):
        return Mock(spec=DeepgramClient)

    def test_sdk_shape_uuid_underscore_and_languages_list(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """model_dump() output (uuid_, languages list) maps onto ModelInfo."""
        mock_client.list_models.return_value = {
            "stt": [
                {
                    "uuid_": "6b28e919-8427-4f32-9847-492e2efd7daf",
                    "name": "nova-3",
                    "canonical_name": "nova-3-general",
                    "architecture": "nova-3",
                    "languages": ["en", "en-US"],
                    "version": "2025-01-01.0",
                },
            ],
            "tts": [
                {
                    "uuid_": "6fe3f8e3-14d3-456c-9534-766132310608",
                    "name": "agathe",
                    "canonical_name": "aura-2-agathe-fr",
                    "architecture": "aura-2",
                    "languages": ["fr", "fr-FR"],
                    "version": "2025-10-29.0",
                },
            ],
        }

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
        )

        assert result.status == "success"
        stt = result.models[0]
        assert stt.model_id == "6b28e919-8427-4f32-9847-492e2efd7daf"
        assert stt.canonical_name == "nova-3-general"
        assert stt.architecture == "nova-3"
        assert stt.language == "en"
        assert stt.languages == ["en", "en-US"]

        tts = result.models[1]
        assert tts.model_id == "6fe3f8e3-14d3-456c-9534-766132310608"
        assert tts.canonical_name == "aura-2-agathe-fr"
        assert tts.model_type == "tts"

    def test_legacy_shape_still_maps(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """The older keys (uuid, language) keep working as fallbacks."""
        mock_client.list_models.return_value = {
            "stt": [
                {
                    "uuid": "legacy-uuid",
                    "name": "Nova-3",
                    "version": "1.0",
                    "language": "en",
                },
            ],
            "tts": [],
        }

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            type="stt",
        )

        assert result.status == "success"
        assert result.models[0].model_id == "legacy-uuid"
        assert result.models[0].language == "en"

    def test_null_category_does_not_crash(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """An explicit null for a category is treated as an empty list."""
        mock_client.list_models.return_value = {
            "stt": [
                {"uuid_": "u1", "name": "nova-3", "languages": ["en"]},
            ],
            "tts": None,
        }

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
        )

        assert result.status == "success"
        assert result.count == 1

    def test_empty_tts_prints_warning(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """Zero TTS models triggers a loud warning instead of silence.

        A catalog that silently shows only speech-to-text models led agents
        to conclude Deepgram has no text-to-speech side. The warning makes
        an empty category visible as an anomaly.
        """
        mock_client.list_models.return_value = {
            "stt": [
                {"uuid_": "u1", "name": "nova-3", "languages": ["en"]},
            ],
            "tts": [],
        }

        with patch("deepctl_cmd_models.command.status_console") as mock_status_console:
            result = command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
            )

        assert result.status == "success"
        warnings = [str(call) for call in mock_status_console.print.call_args_list]
        assert any("zero text-to-speech" in w for w in warnings)

    def test_stt_filter_suppresses_tts_warning(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """--type stt does not warn about the (unrequested) TTS category."""
        mock_client.list_models.return_value = {
            "stt": [
                {"uuid_": "u1", "name": "nova-3", "languages": ["en"]},
            ],
            "tts": [],
        }

        with patch("deepctl_cmd_models.command.status_console") as mock_status_console:
            result = command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                type="stt",
            )

        assert result.status == "success"
        warnings = [str(call) for call in mock_status_console.print.call_args_list]
        assert not any("zero text-to-speech" in w for w in warnings)


class TestDeprecationFlags:
    """Legacy models are flagged so nobody derives new names from them."""

    @pytest.fixture
    def command(self):
        return ModelsCommand()

    @pytest.fixture
    def mock_config(self):
        return Mock(spec=Config)

    @pytest.fixture
    def mock_auth_manager(self):
        manager = Mock(spec=AuthManager)
        manager.get_api_key.return_value = "test-api-key"
        return manager

    @pytest.fixture
    def mock_client(self):
        return Mock(spec=DeepgramClient)

    @pytest.mark.parametrize("legacy_name", ["conversationalai", "2-conversationalai"])
    def test_conversationalai_flagged_deprecated(
        self, command, mock_config, mock_auth_manager, mock_client, legacy_name
    ):
        mock_client.list_models.return_value = {
            "stt": [
                {"uuid_": "u1", "name": legacy_name, "languages": ["en"]},
                {"uuid_": "u2", "name": "nova-3", "languages": ["en"]},
            ],
            "tts": [],
        }

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            type="stt",
        )

        legacy = next(m for m in result.models if m.name == legacy_name)
        assert legacy.deprecated is True
        assert "nova-3" in legacy.deprecation_note
        assert "nova-3-conversational" in legacy.deprecation_note

        current = next(m for m in result.models if m.name == "nova-3")
        assert current.deprecated is False
        assert current.deprecation_note == ""
