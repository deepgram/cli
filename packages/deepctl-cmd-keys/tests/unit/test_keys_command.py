"""Tests for keys command."""

from unittest.mock import Mock, patch

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
        mock_client.list_keys.assert_called_once_with(project_id=None, status=None)

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
        mock_client.get_key.assert_called_once_with("key-id", project_id=None)

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
        mock_client.delete_key.assert_called_once_with("key-id", project_id=None)

    def test_handle_error(self, command, mock_config, mock_auth_manager, mock_client):
        """Test client raises exception, returns error."""
        mock_client.list_keys.side_effect = Exception("API connection failed")

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
        )

        assert result.status == "error"
        assert "API connection failed" in result.message


class TestKeysOutputFormat:
    """`-o json` must leave stdout parseable (#98).

    `keys` was the one account command missed when the other seven were fixed,
    so `dg -o json keys | jq` still failed on "Fetching API keys..." arriving
    ahead of the JSON.
    """

    @pytest.fixture
    def command(self):
        return KeysCommand()

    @staticmethod
    def _keys_response():
        return {
            "api_keys": [
                {
                    "api_key": {
                        "api_key_id": "key1",
                        "comment": "ci-runner",
                        "scopes": ["member"],
                        "created": "2026-01-01",
                        "expiration_date": "",
                    }
                }
            ]
        }

    @patch("deepctl_cmd_keys.command.get_output_format", return_value="default")
    @patch("deepctl_cmd_keys.command.status_console")
    @patch("deepctl_cmd_keys.command.console")
    def test_default_mode_table_on_stdout_chrome_on_stderr(
        self, mock_console, mock_status_console, _fmt, command
    ):
        client = Mock(spec=DeepgramClient)
        client.list_keys.return_value = self._keys_response()

        command._list_keys(client, None, None)

        stderr_text = " ".join(
            str(c.args[0]) for c in mock_status_console.print.call_args_list if c.args
        )
        # The table rides stdout for humans...
        mock_console.print.assert_called()
        # ...while progress chrome only ever goes to stderr.
        assert "Fetching API keys" in stderr_text

    @patch("deepctl_cmd_keys.command.get_output_format", return_value="json")
    @patch("deepctl_cmd_keys.command.status_console")
    @patch("deepctl_cmd_keys.command.console")
    def test_json_mode_renders_no_human_output(
        self, mock_console, mock_status_console, _fmt, command
    ):
        client = Mock(spec=DeepgramClient)
        client.list_keys.return_value = self._keys_response()

        result = command._list_keys(client, None, None)

        # Nothing human-facing on stdout — the framework serialises the result.
        mock_console.print.assert_not_called()
        assert result.count == 1
        assert result.keys[0].key_id == "key1"

    @patch("deepctl_cmd_keys.command.get_output_format", return_value="json")
    @patch("deepctl_cmd_keys.command.status_console")
    @patch("deepctl_cmd_keys.command.console")
    def test_json_mode_created_key_only_in_result(
        self, mock_console, mock_status_console, _fmt, command
    ):
        """The secret must reach the caller via the result, not stray prints."""
        client = Mock(spec=DeepgramClient)
        client.create_key.return_value = {
            "api_key_id": "new-id",
            "key": "secret-value",
        }

        result = command._create_key(client, None, comment="ci", scopes="member")

        mock_console.print.assert_not_called()
        assert result.created_key.key == "secret-value"
        assert result.created_key.key_id == "new-id"

    @patch("deepctl_cmd_keys.command.get_output_format", return_value="json")
    @patch("deepctl_cmd_keys.command.status_console")
    @patch("deepctl_cmd_keys.command.console")
    def test_json_mode_show_key_renders_nothing(
        self, mock_console, mock_status_console, _fmt, command
    ):
        client = Mock(spec=DeepgramClient)
        client.get_key.return_value = {
            "api_key": {"api_key_id": "key1", "comment": "c", "scopes": ["member"]}
        }

        result = command._show_key(client, "key1", None)

        mock_console.print.assert_not_called()
        assert result.keys[0].key_id == "key1"


class TestKeysDryRun:
    """`--dry-run` must reach its own code, not a TypeError.

    `handle` read `project_id` and `dry_run` off kwargs with `.get()` and then
    forwarded `**kwargs` alongside them, so every argument arrived twice and
    `--create --dry-run` failed with "got multiple values for argument" before
    the dry-run body ran.
    """

    @pytest.fixture
    def command(self):
        return KeysCommand()

    @pytest.fixture
    def mock_client(self):
        return Mock(spec=DeepgramClient)

    @patch("deepctl_cmd_keys.command.get_output_format", return_value="json")
    @patch("deepctl_cmd_keys.command.status_console")
    @patch("deepctl_cmd_keys.command.console")
    def test_create_dry_run_reports_dry_run_and_calls_nothing(
        self, mock_console, _status, _fmt, command, mock_client
    ):
        result = command.handle(
            Mock(spec=Config),
            Mock(spec=AuthManager),
            mock_client,
            create=True,
            dry_run=True,
            project_id=None,
            comment="probe",
            scopes="member",
            ttl=3600,
            tags="a,b",
        )

        assert result.status == "dry_run"
        mock_client.create_key.assert_not_called()
        mock_console.print.assert_not_called()

    @patch("deepctl_cmd_keys.command.get_output_format", return_value="default")
    @patch("deepctl_cmd_keys.command.status_console")
    @patch("deepctl_cmd_keys.command.console")
    def test_create_dry_run_renders_summary_for_humans(
        self, mock_console, _status, _fmt, command, mock_client
    ):
        result = command.handle(
            Mock(spec=Config),
            Mock(spec=AuthManager),
            mock_client,
            create=True,
            dry_run=True,
            project_id=None,
            comment="probe",
            scopes="member",
        )

        assert result.status == "dry_run"
        printed = " ".join(
            str(c.args[0]) for c in mock_console.print.call_args_list if c.args
        )
        assert "Dry run" in printed
        assert "probe" in printed

    @patch("deepctl_cmd_keys.command.get_output_format", return_value="json")
    @patch("deepctl_cmd_keys.command.status_console")
    @patch("deepctl_cmd_keys.command.console")
    def test_delete_dry_run_reports_dry_run_and_calls_nothing(
        self, mock_console, _status, _fmt, command, mock_client
    ):
        result = command.handle(
            Mock(spec=Config),
            Mock(spec=AuthManager),
            mock_client,
            delete="key-id",
            dry_run=True,
            project_id=None,
        )

        assert result.status == "dry_run"
        assert "key-id" in result.message
        mock_client.delete_key.assert_not_called()
        mock_console.print.assert_not_called()


class TestKeysDeleteConfirmation:
    """`--delete` without `--yes` must not silently no-op.

    `BaseCommand.confirm` returns its default whenever any parameter came from
    the command line, and `--delete KEY_ID` is itself such a parameter — so the
    prompt never appeared and the command reported "Cancelled by user" without
    asking anyone and without deleting anything.
    """

    @pytest.fixture
    def command(self):
        return KeysCommand()

    @pytest.fixture
    def mock_client(self):
        return Mock(spec=DeepgramClient)

    @patch("deepctl_cmd_keys.command.status_console")
    @patch("deepctl_cmd_keys.command.is_agentic", return_value=True)
    def test_non_interactive_without_yes_errors_and_deletes_nothing(
        self, _agentic, _status, command, mock_client
    ):
        result = command._delete_key(mock_client, "key-id", None, yes=False)

        assert result.status == "error"
        assert "--yes" in result.message
        mock_client.delete_key.assert_not_called()

    @patch("deepctl_cmd_keys.command.status_console")
    @patch("deepctl_cmd_keys.command.is_agentic", return_value=False)
    def test_interactive_declined_cancels_and_deletes_nothing(
        self, _agentic, _status, command, mock_client
    ):
        with (
            patch("deepctl_cmd_keys.command.sys.stdin.isatty", return_value=True),
            patch("click.confirm", return_value=False) as mock_confirm,
        ):
            result = command._delete_key(mock_client, "key-id", None, yes=False)

        assert result.status == "cancelled"
        mock_client.delete_key.assert_not_called()
        # The prompt goes to stderr, so stdout stays parseable under -o json.
        assert mock_confirm.call_args.kwargs["err"] is True

    @patch("deepctl_cmd_keys.command.status_console")
    @patch("deepctl_cmd_keys.command.is_agentic", return_value=False)
    def test_interactive_accepted_deletes(
        self, _agentic, _status, command, mock_client
    ):
        with (
            patch("deepctl_cmd_keys.command.sys.stdin.isatty", return_value=True),
            patch("click.confirm", return_value=True),
        ):
            result = command._delete_key(mock_client, "key-id", None, yes=False)

        assert result.status == "success"
        mock_client.delete_key.assert_called_once_with("key-id", project_id=None)

    @patch("deepctl_cmd_keys.command.status_console")
    def test_yes_skips_confirmation_entirely(self, _status, command, mock_client):
        with patch("click.confirm", side_effect=AssertionError("prompted anyway")):
            result = command._delete_key(mock_client, "key-id", None, yes=True)

        assert result.status == "success"
        mock_client.delete_key.assert_called_once_with("key-id", project_id=None)


class TestKeysErrorPath:
    """Errors are chrome, not payload: stderr, and a non-zero exit code."""

    @pytest.fixture
    def command(self):
        return KeysCommand()

    @patch("deepctl_cmd_keys.command.get_output_format", return_value="json")
    @patch("deepctl_cmd_keys.command.status_console")
    @patch("deepctl_cmd_keys.command.console")
    def test_error_goes_to_stderr_and_maps_to_exit_1(
        self, mock_console, mock_status_console, _fmt, command
    ):
        client = Mock(spec=DeepgramClient)
        client.list_keys.side_effect = Exception("API connection failed")

        result = command.handle(
            Mock(spec=Config), Mock(spec=AuthManager), client, project_id=None
        )

        assert result.status == "error"
        mock_console.print.assert_not_called()
        stderr_text = " ".join(
            str(c.args[0]) for c in mock_status_console.print.call_args_list if c.args
        )
        assert "API connection failed" in stderr_text
        assert command.exit_code_for(result) == 1


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
