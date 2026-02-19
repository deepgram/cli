"""Tests for API command."""

import json
from unittest.mock import Mock, patch

import httpx
import pytest
from deepctl_cmd_api.command import DEFAULT_BASE_URL, ApiCommand
from deepctl_cmd_api.models import ApiResult
from deepctl_core import AuthManager, Config, DeepgramClient


class TestApiCommand:
    """Test cases for ApiCommand."""

    @pytest.fixture
    def command(self):
        """Create an ApiCommand instance."""
        return ApiCommand()

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
        assert command.name == "api"
        assert command.requires_auth is True
        assert command.requires_project is False
        assert command.ci_friendly is True

    def test_get_arguments(self, command):
        """Test command arguments configuration."""
        args = command.get_arguments()

        # Check positional argument
        positional = [a for a in args if not a.get("is_option", False)]
        assert len(positional) == 1
        assert positional[0]["name"] == "endpoint"

        # Check options
        option_names = []
        for arg in args:
            if arg.get("is_option", False):
                option_names.extend(arg["names"])

        assert "-X" in option_names
        assert "--method" in option_names
        assert "-f" in option_names
        assert "--field" in option_names
        assert "--input" in option_names
        assert "-H" in option_names
        assert "--header" in option_names
        assert "--jq" in option_names
        assert "--raw" in option_names

    def test_resolve_url_relative_path(self, command):
        """Test URL resolution for relative paths."""
        assert command._resolve_url("/v1/projects") == f"{DEFAULT_BASE_URL}/v1/projects"

    def test_resolve_url_relative_without_slash(self, command):
        """Test URL resolution for relative paths without leading slash."""
        assert command._resolve_url("v1/projects") == f"{DEFAULT_BASE_URL}/v1/projects"

    def test_resolve_url_absolute(self, command):
        """Test URL resolution for absolute URLs."""
        url = "https://custom.deepgram.com/v1/test"
        assert command._resolve_url(url) == url

    def test_build_headers(self, command):
        """Test header building with auth and custom headers."""
        headers = command._build_headers(
            "test-key", ["X-Custom: custom-value", "Accept: text/plain"]
        )
        assert headers["Authorization"] == "Token test-key"
        assert headers["X-Custom"] == "custom-value"
        assert headers["Accept"] == "text/plain"

    def test_build_headers_no_custom(self, command):
        """Test header building with only auth."""
        headers = command._build_headers("test-key", None)
        assert headers == {"Authorization": "Token test-key"}

    def test_build_body_from_fields(self, command):
        """Test body building from field arguments."""
        body = command._build_body(["name=Test Project", "tier=basic"], None)
        assert body == {"name": "Test Project", "tier": "basic"}

    def test_build_body_from_json_fields(self, command):
        """Test body building from JSON field arguments."""
        body = command._build_body(
            ['name=Test', 'count:=42', 'active:=true'], None
        )
        assert body == {"name": "Test", "count": 42, "active": True}

    def test_build_body_invalid_field(self, command):
        """Test body building with invalid field format."""
        with pytest.raises(ValueError, match="Invalid field format"):
            command._build_body(["invalid-no-equals"], None)

    def test_build_body_stdin(self, command):
        """Test body building from stdin."""
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = '{"key": "value"}'
            body = command._build_body(None, "-")
            assert body == {"key": "value"}

    def test_build_body_file(self, command, tmp_path):
        """Test body building from file."""
        body_file = tmp_path / "body.json"
        body_file.write_text('{"from_file": true}')
        body = command._build_body(None, str(body_file))
        assert body == {"from_file": True}

    def test_build_body_mutual_exclusion(self, command):
        """Test that fields and input are mutually exclusive."""
        with pytest.raises(ValueError, match="Cannot use both"):
            command._build_body(["name=test"], "-")

    def test_build_body_none(self, command):
        """Test body building with no input."""
        body = command._build_body(None, None)
        assert body is None

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_apply_jq_filter(self, mock_run, mock_which, command):
        """Test jq filter application."""
        mock_which.return_value = "/usr/bin/jq"
        mock_run.return_value = Mock(
            returncode=0,
            stdout='"project-1"\n',
            stderr="",
        )

        result = command._apply_jq_filter(
            '{"name": "project-1"}', ".name"
        )
        assert result == '"project-1"'

    @patch("shutil.which")
    def test_apply_jq_filter_not_installed(self, mock_which, command):
        """Test jq filter when jq is not installed."""
        mock_which.return_value = None
        with pytest.raises(RuntimeError, match="jq is not installed"):
            command._apply_jq_filter("{}", ".name")

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_apply_jq_filter_error(self, mock_run, mock_which, command):
        """Test jq filter with invalid expression."""
        mock_which.return_value = "/usr/bin/jq"
        mock_run.return_value = Mock(
            returncode=5,
            stdout="",
            stderr="jq: error: syntax error",
        )

        with pytest.raises(RuntimeError, match="jq error"):
            command._apply_jq_filter("{}", "invalid[")

    @patch("httpx.Client")
    def test_handle_success(
        self, mock_client_class, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test successful API request."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = '{"projects": []}'
        mock_response.json.return_value = {"projects": []}
        mock_response.elapsed.total_seconds.return_value = 0.150

        mock_http = Mock()
        mock_http.__enter__ = Mock(return_value=mock_http)
        mock_http.__exit__ = Mock(return_value=False)
        mock_http.request.return_value = mock_response
        mock_client_class.return_value = mock_http

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            endpoint="/v1/projects",
        )

        assert isinstance(result, ApiResult)
        assert result.status == "success"
        assert result.status_code == 200
        assert result.method == "GET"
        assert result.url == f"{DEFAULT_BASE_URL}/v1/projects"
        assert result.response_body == {"projects": []}

    @patch("httpx.Client")
    def test_handle_error_response(
        self, mock_client_class, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test handling of error HTTP responses."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.text = '{"error": "not found"}'
        mock_response.json.return_value = {"error": "not found"}
        mock_response.elapsed.total_seconds.return_value = 0.050

        mock_http = Mock()
        mock_http.__enter__ = Mock(return_value=mock_http)
        mock_http.__exit__ = Mock(return_value=False)
        mock_http.request.return_value = mock_response
        mock_client_class.return_value = mock_http

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            endpoint="/v1/nonexistent",
        )

        assert isinstance(result, ApiResult)
        assert result.status == "error"
        assert result.status_code == 404

    @patch("httpx.Client")
    def test_handle_request_error(
        self, mock_client_class, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test handling of request errors."""
        mock_http = Mock()
        mock_http.__enter__ = Mock(return_value=mock_http)
        mock_http.__exit__ = Mock(return_value=False)
        mock_http.request.side_effect = httpx.ConnectError("Connection refused")
        mock_client_class.return_value = mock_http

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            endpoint="/v1/projects",
        )

        assert isinstance(result, ApiResult)
        assert result.status == "error"
        assert "Connection refused" in result.message

    def test_handle_no_api_key(
        self, command, mock_config, mock_client
    ):
        """Test handling when no API key is available."""
        mock_auth = Mock(spec=AuthManager)
        mock_auth.get_api_key.return_value = None

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth,
            client=mock_client,
            endpoint="/v1/projects",
        )

        assert isinstance(result, ApiResult)
        assert result.status == "error"
        assert "No API key found" in result.message

    @patch("httpx.Client")
    def test_handle_post_with_fields(
        self, mock_client_class, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test POST request with field arguments."""
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.text = '{"project_id": "abc123"}'
        mock_response.json.return_value = {"project_id": "abc123"}
        mock_response.elapsed.total_seconds.return_value = 0.200

        mock_http = Mock()
        mock_http.__enter__ = Mock(return_value=mock_http)
        mock_http.__exit__ = Mock(return_value=False)
        mock_http.request.return_value = mock_response
        mock_client_class.return_value = mock_http

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            endpoint="/v1/projects",
            method="POST",
            field=("name=Test Project",),
        )

        assert result.status == "success"
        assert result.status_code == 201
        assert result.method == "POST"

        # Verify the request was made with JSON body
        call_kwargs = mock_http.request.call_args
        assert call_kwargs.kwargs.get("json") == {"name": "Test Project"} or (
            call_kwargs[1].get("json") == {"name": "Test Project"}
        )


class TestApiResult:
    """Test cases for ApiResult model."""

    def test_create_api_result(self):
        """Test creating an ApiResult."""
        result = ApiResult(
            status="success",
            method="GET",
            url="https://api.deepgram.com/v1/projects",
            status_code=200,
            response_body={"projects": []},
            elapsed_ms=150.5,
        )

        assert result.status == "success"
        assert result.method == "GET"
        assert result.url == "https://api.deepgram.com/v1/projects"
        assert result.status_code == 200
        assert result.response_body == {"projects": []}
        assert result.elapsed_ms == 150.5

    def test_api_result_defaults(self):
        """Test ApiResult with default values."""
        result = ApiResult()

        assert result.status == "success"
        assert result.method == ""
        assert result.url == ""
        assert result.status_code == 0
        assert result.response_body is None
        assert result.elapsed_ms is None

    def test_api_result_serialization(self):
        """Test ApiResult can be serialized."""
        result = ApiResult(
            status="success",
            method="POST",
            url="https://api.deepgram.com/v1/projects",
            status_code=201,
            response_body={"id": "abc"},
        )

        data = result.model_dump()
        assert data["method"] == "POST"
        assert data["status_code"] == 201
