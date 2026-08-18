"""Tests for requests command."""

from unittest.mock import Mock, patch

import pytest
from deepctl_cmd_requests.command import RequestsCommand
from deepctl_cmd_requests.models import (
    RequestDetailResult,
    RequestInfo,
    RequestsResult,
)
from deepctl_core import AuthManager, BaseResult, Config, DeepgramClient


class TestRequestsCommand:
    """Test cases for RequestsCommand."""

    @pytest.fixture
    def command(self):
        """Create a RequestsCommand instance."""
        return RequestsCommand()

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
        assert command.name == "requests"
        assert command.requires_auth is True
        assert command.requires_project is True

    def test_get_arguments(self, command):
        """Test command arguments configuration."""
        args = command.get_arguments()

        # All arguments should be options
        option_names = []
        for arg in args:
            assert arg.get("is_option", False) is True
            option_names.extend(arg["names"])

        assert "--show" in option_names
        assert "-s" in option_names
        assert "--status" in option_names
        assert "--endpoint" in option_names
        assert "--method" in option_names
        assert "--limit" in option_names
        assert "--page" in option_names
        assert "--start-date" in option_names
        assert "--end-date" in option_names
        assert "--last-week" in option_names
        assert "--last-day" in option_names
        assert "--project-id" in option_names
        assert "-p" in option_names

    def test_handle_list_requests(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test default behavior lists requests."""
        mock_client.list_requests.return_value = {
            "requests": [
                {
                    "request_id": "req-1",
                    "created": "2024-01-01T00:00:00Z",
                    "path": "/v1/listen",
                    "method": "sync",
                    "response": {"code": 200},
                    "duration": 1.5,
                }
            ]
        }

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
        )

        assert isinstance(result, RequestsResult)
        assert result.status == "success"
        assert result.count == 1
        assert len(result.requests) == 1
        assert result.requests[0].request_id == "req-1"
        assert result.requests[0].path == "/v1/listen"
        assert result.requests[0].method == "sync"
        assert result.requests[0].status == "succeeded"
        assert result.requests[0].duration == 1.5
        mock_client.list_requests.assert_called_once_with(
            project_id=None,
            start=None,
            end=None,
            limit=10,
            page=None,
            status=None,
            endpoint=None,
            method=None,
        )

    def test_handle_list_empty(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test empty requests list returns info status."""
        mock_client.list_requests.return_value = {"requests": []}

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
        )

        assert isinstance(result, RequestsResult)
        assert result.status == "info"
        assert result.message == "No requests found"

    def test_handle_show_request(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test showing details for a specific request."""
        mock_client.get_request.return_value = {
            "request_id": "req-1",
            "created": "2024-01-01",
            "path": "/v1/listen",
        }

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            show="req-1",
        )

        assert isinstance(result, BaseResult)
        assert result.status == "success"
        mock_client.get_request.assert_called_once_with(
            "req-1", project_id=None
        )

    def test_handle_filter_by_status(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test filtering requests by status."""
        mock_client.list_requests.return_value = {
            "requests": [
                {
                    "request_id": "req-fail",
                    "created": "2024-01-01T00:00:00Z",
                    "path": "/v1/listen",
                    "method": "sync",
                    "response": {"code": 500},
                    "duration": 0.3,
                }
            ]
        }

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            status="failed",
        )

        assert isinstance(result, RequestsResult)
        assert result.status == "success"
        mock_client.list_requests.assert_called_once_with(
            project_id=None,
            start=None,
            end=None,
            limit=10,
            page=None,
            status="failed",
            endpoint=None,
            method=None,
        )

    @patch("deepctl_cmd_requests.command.datetime")
    def test_handle_last_week(
        self, mock_datetime, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test last_week flag computes start/end datetime."""
        from datetime import datetime, timedelta

        fake_now = datetime(2024, 6, 15, 12, 0, 0)
        mock_datetime.now.return_value = fake_now
        mock_datetime.side_effect = lambda *a, **kw: datetime(*a, **kw)

        mock_client.list_requests.return_value = {"requests": []}

        command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            last_week=True,
        )

        call_kwargs = mock_client.list_requests.call_args[1]
        assert call_kwargs["start"] == fake_now - timedelta(days=7)
        assert call_kwargs["end"] == fake_now

    def test_handle_error(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test client raises exception, returns error."""
        mock_client.list_requests.side_effect = Exception("API connection failed")

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
        )

        assert result.status == "error"
        assert "API connection failed" in result.message


class TestRequestsOutputGating:
    """stdout stays machine-parseable in json/yaml/csv modes.

    In any non-``default`` output mode the command must not write its human
    table/detail to the stdout ``console`` — the framework serialises the
    returned result to stdout, so a stray print here would prepend
    non-parseable text to piped JSON. Status chrome uses ``status_console``
    (stderr) and is intentionally unaffected.
    """

    @pytest.fixture
    def command(self):
        return RequestsCommand()

    def _handle_list(self, command, client):
        return command.handle(
            config=Mock(spec=Config),
            auth_manager=Mock(spec=AuthManager),
            client=client,
        )

    @staticmethod
    def _list_response():
        return {
            "requests": [
                {
                    "request_id": "req-1",
                    "created": "2024-01-01T00:00:00Z",
                    "path": "/v1/listen",
                    "method": "sync",
                    "response": {"code": 200},
                    "duration": 1.5,
                }
            ]
        }

    @patch("deepctl_cmd_requests.command.get_output_format", return_value="json")
    @patch("deepctl_cmd_requests.command.console")
    def test_list_json_mode_writes_nothing_to_stdout(
        self, mock_console, _fmt, command
    ):
        client = Mock(spec=DeepgramClient)
        client.list_requests.return_value = self._list_response()

        result = self._handle_list(command, client)

        # Result still fully populated for the framework to serialise.
        assert isinstance(result, RequestsResult)
        assert result.count == 1
        assert result.requests[0].request_id == "req-1"
        # Nothing written to the stdout console.
        mock_console.print.assert_not_called()

    @patch("deepctl_cmd_requests.command.get_output_format", return_value="default")
    @patch("deepctl_cmd_requests.command.console")
    def test_list_default_mode_renders_table(self, mock_console, _fmt, command):
        client = Mock(spec=DeepgramClient)
        client.list_requests.return_value = self._list_response()

        self._handle_list(command, client)

        assert mock_console.print.called

    @patch("deepctl_cmd_requests.command.get_output_format", return_value="json")
    @patch("deepctl_cmd_requests.command.console")
    def test_show_json_mode_returns_detail_only(self, mock_console, _fmt, command):
        client = Mock(spec=DeepgramClient)
        client.get_request.return_value = {
            "request_id": "req-1",
            "path": "/v1/listen",
        }

        result = command.handle(
            config=Mock(spec=Config),
            auth_manager=Mock(spec=AuthManager),
            client=client,
            show="req-1",
        )

        assert isinstance(result, RequestDetailResult)
        assert result.detail == {"request_id": "req-1", "path": "/v1/listen"}
        # Dedicated model: no empty requests/count noise beside the detail.
        dumped = result.model_dump()
        assert "requests" not in dumped
        assert "count" not in dumped
        mock_console.print.assert_not_called()


class TestRequestsModels:
    """Test cases for requests models."""

    def test_request_info_defaults(self):
        """Test RequestInfo with default values."""
        info = RequestInfo()
        assert info.request_id == ""
        assert info.created == ""
        assert info.path == ""
        assert info.method == ""
        assert info.status == ""
        assert info.duration == 0.0

    def test_request_info_with_values(self):
        """Test RequestInfo with provided values."""
        info = RequestInfo(
            request_id="req-abc",
            created="2024-01-01T00:00:00Z",
            path="/v1/listen",
            method="sync",
            status="succeeded",
            duration=2.5,
        )
        assert info.request_id == "req-abc"
        assert info.created == "2024-01-01T00:00:00Z"
        assert info.path == "/v1/listen"
        assert info.method == "sync"
        assert info.status == "succeeded"
        assert info.duration == 2.5

    def test_requests_result_defaults(self):
        """Test RequestsResult with default values."""
        result = RequestsResult()
        assert result.requests == []
        assert result.count == 0

    def test_requests_result_serialization(self):
        """Test RequestsResult can be serialized."""
        result = RequestsResult(
            status="success",
            requests=[
                RequestInfo(request_id="r1", path="/v1/listen", duration=1.0)
            ],
            count=1,
        )
        data = result.model_dump()
        assert data["count"] == 1
        assert len(data["requests"]) == 1
        assert data["requests"][0]["request_id"] == "r1"
        assert data["requests"][0]["path"] == "/v1/listen"
        assert data["requests"][0]["duration"] == 1.0
