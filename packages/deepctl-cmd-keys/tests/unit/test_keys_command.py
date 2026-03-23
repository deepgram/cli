"""Tests for keys command."""

from unittest.mock import Mock

import pytest
from deepctl_cmd_keys.command import KeysCommand
from deepctl_cmd_keys.models import KeyCreatedInfo, KeyInfo, KeysResult
from deepctl_core import AuthManager, BaseResult, Config, DeepgramClient


class TestKeysCommand:
    """Test cases for KeysCommand."""

    @pytest.fixture
    def command(self):
        """Create a KeysCommand instance."""
        return KeysCommand()

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

    def test_command_properties(self, command):
        """Test command basic properties."""
        assert command.name == "keys"
        assert command.requires_auth is True
        assert command.requires_project is True
        assert command.ci_friendly is True

    def test_get_arguments(self, command):
        """Test command arguments configuration."""
        args = command.get_arguments()

        # All arguments should be options
        option_names = []
        for arg in args:
            assert arg.get("is_option", False) is True
            option_names.extend(arg["names"])

        assert "--list" in option_names
        assert "-l" in option_names
        assert "--create" in option_names
        assert "-c" in option_names
        assert "--show" in option_names
        assert "-s" in option_names
        assert "--delete" in option_names
        assert "-d" in option_names
        assert "--comment" in option_names
        assert "--scopes" in option_names
        assert "--expiration-date" in option_names
        assert "--ttl" in option_names
        assert "--tags" in option_names
        assert "--status" in option_names
        assert "--project-id" in option_names
        assert "-p" in option_names

    def test_handle_list_keys(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test default behavior lists keys."""
        mock_client.list_keys.return_value = {
            "api_keys": [
                {
                    "api_key": {
                        "api_key_id": "key1",
                        "comment": "test",
                        "scopes": ["member"],
                        "created": "2024-01-01",
                        "expiration_date": "",
                        "tags": [],
                    }
                }
            ]
        }

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
        )

        assert isinstance(result, KeysResult)
        assert result.status == "success"
        assert result.count == 1
        assert len(result.keys) == 1
        assert result.keys[0].key_id == "key1"
        assert result.keys[0].comment == "test"
        assert result.keys[0].scopes == ["member"]
        mock_client.list_keys.assert_called_once_with(
            project_id=None, status=None
        )

    def test_handle_list_keys_empty(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test empty api_keys list returns info status."""
        mock_client.list_keys.return_value = {"api_keys": []}

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
        )

        assert isinstance(result, KeysResult)
        assert result.status == "info"
        assert result.message == "No keys found"

    def test_handle_create_key(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test creating an API key."""
        mock_client.create_key.return_value = {
            "api_key_id": "new-key",
            "key": "sk-xxx",
        }

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            create=True,
            comment="staging",
            scopes="member",
        )

        assert isinstance(result, KeysResult)
        assert result.status == "success"
        assert result.message == "Key created"
        assert result.created_key is not None
        assert result.created_key.key_id == "new-key"
        assert result.created_key.key == "sk-xxx"
        assert result.created_key.comment == "staging"
        assert result.created_key.scopes == ["member"]
        mock_client.create_key.assert_called_once_with(
            project_id=None,
            comment="staging",
            scopes=["member"],
            expiration_date=None,
            time_to_live=None,
            tags=None,
        )

    def test_handle_show_key(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test showing details for a specific key."""
        mock_client.get_key.return_value = {
            "api_key": {
                "api_key_id": "key-id",
                "comment": "production key",
                "scopes": ["admin"],
                "created": "2024-01-15",
                "expiration_date": "2025-01-15",
                "tags": ["prod"],
            }
        }

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            show="key-id",
        )

        assert isinstance(result, KeysResult)
        assert result.status == "success"
        assert result.count == 1
        assert len(result.keys) == 1
        assert result.keys[0].key_id == "key-id"
        assert result.keys[0].comment == "production key"
        assert result.keys[0].scopes == ["admin"]
        assert result.keys[0].tags == ["prod"]
        mock_client.get_key.assert_called_once_with(
            "key-id", project_id=None
        )

    def test_handle_delete_key(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test deleting an API key."""
        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            delete="key-id",
            yes=True,
        )

        assert isinstance(result, BaseResult)
        assert result.status == "success"
        assert "key-id" in result.message
        assert "deleted" in result.message.lower()
        mock_client.delete_key.assert_called_once_with(
            "key-id", project_id=None
        )

    def test_handle_error(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test client raises exception, returns error."""
        mock_client.list_keys.side_effect = Exception("API connection failed")

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
        )

        assert result.status == "error"
        assert "API connection failed" in result.message


class TestKeysModels:
    """Test cases for keys models."""

    def test_key_info_defaults(self):
        """Test KeyInfo with default values."""
        info = KeyInfo()
        assert info.key_id == ""
        assert info.comment == ""
        assert info.scopes == []
        assert info.created == ""
        assert info.expiration_date == ""
        assert info.tags == []

    def test_key_info_with_values(self):
        """Test KeyInfo with provided values."""
        info = KeyInfo(
            key_id="abc123",
            comment="test key",
            scopes=["member", "admin"],
            created="2024-01-01",
            expiration_date="2025-01-01",
            tags=["staging"],
        )
        assert info.key_id == "abc123"
        assert info.comment == "test key"
        assert info.scopes == ["member", "admin"]
        assert info.tags == ["staging"]

    def test_key_created_info(self):
        """Test KeyCreatedInfo includes key field."""
        created = KeyCreatedInfo(
            key_id="new-key",
            key="sk-secret",
            comment="new",
            scopes=["member"],
        )
        assert created.key_id == "new-key"
        assert created.key == "sk-secret"
        assert isinstance(created, KeyInfo)

    def test_keys_result_defaults(self):
        """Test KeysResult with default values."""
        result = KeysResult()
        assert result.keys == []
        assert result.count == 0
        assert result.created_key is None

    def test_keys_result_serialization(self):
        """Test KeysResult can be serialized."""
        result = KeysResult(
            status="success",
            keys=[KeyInfo(key_id="k1", comment="first")],
            count=1,
        )
        data = result.model_dump()
        assert data["count"] == 1
        assert len(data["keys"]) == 1
        assert data["keys"][0]["key_id"] == "k1"
