"""Unit tests for the Gnosis client module."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from deepctl_cmd_mcp.gnosis import GnosisClient, GnosisRequest, GnosisResponse


class TestGnosisClient:
    """Test suite for GnosisClient."""

    def test_init_with_api_key(self):
        """Test client initialization with API key."""
        client = GnosisClient(api_key="test-key")
        assert client.api_key == "test-key"
        assert client.base_url == "https://gnosis.deepgram.com"
        assert client.timeout == 30.0
        assert client.debug is False

    def test_init_with_env_var(self):
        """Test client initialization with environment variable."""
        with patch.dict("os.environ", {"DEEPGRAM_API_KEY": "env-key"}):
            client = GnosisClient()
            assert client.api_key == "env-key"

    def test_init_without_api_key_raises(self):
        """Test that initialization without API key raises ValueError."""
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="API key is required"):
                GnosisClient()

    def test_init_with_custom_params(self):
        """Test client initialization with custom parameters."""
        client = GnosisClient(
            api_key="test-key",
            base_url="https://custom.gnosis.com",
            timeout=60.0,
            debug=True,
        )
        assert client.base_url == "https://custom.gnosis.com"
        assert client.timeout == 60.0
        assert client.debug is True

    @pytest.mark.asyncio
    async def test_call_success(self):
        """Test successful API call."""
        client = GnosisClient(api_key="test-key")

        # Mock the HTTP response
        mock_response = Mock()
        mock_response.json.return_value = {
            "choices": [
                {"message": {"content": "Test response"}}
            ]
        }
        mock_response.raise_for_status = Mock()

        with patch("httpx.AsyncClient.post", return_value=mock_response):
            result = await client.call([{"role": "user", "content": "Test"}])
            assert result == "Test response"

    @pytest.mark.asyncio
    async def test_call_http_error(self):
        """Test API call with HTTP error."""
        client = GnosisClient(api_key="test-key")

        # Mock HTTP error response
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401 Unauthorized", request=Mock(), response=mock_response
        )

        with patch("httpx.AsyncClient.post", return_value=mock_response):
            result = await client.call([{"role": "user", "content": "Test"}])
            assert "HTTP Error 401" in result
            assert "Unauthorized" in result

    @pytest.mark.asyncio
    async def test_call_no_response(self):
        """Test API call with no response content."""
        client = GnosisClient(api_key="test-key")

        # Mock empty response
        mock_response = Mock()
        mock_response.json.return_value = {"choices": []}
        mock_response.raise_for_status = Mock()

        with patch("httpx.AsyncClient.post", return_value=mock_response):
            result = await client.call([{"role": "user", "content": "Test"}])
            assert result == "No response from Deepgram AI"

    @pytest.mark.asyncio
    async def test_ask_question(self):
        """Test ask_question method."""
        client = GnosisClient(api_key="test-key")

        # Mock the call method
        with patch.object(client, "call", return_value="Answer") as mock_call:
            result = await client.ask_question("What is Deepgram?")
            assert result == "Answer"

            # Check the call was made with correct messages
            mock_call.assert_called_once()
            messages = mock_call.call_args[0][0]
            assert len(messages) == 1
            assert messages[0] == {"role": "user",
                                   "content": "What is Deepgram?"}

    @pytest.mark.asyncio
    async def test_ask_question_with_system_prompt(self):
        """Test ask_question with system prompt."""
        client = GnosisClient(api_key="test-key")

        with patch.object(client, "call", return_value="Answer") as mock_call:
            result = await client.ask_question(
                "Explain API",
                system_prompt="You are a technical expert"
            )
            assert result == "Answer"

            # Check messages include system prompt
            messages = mock_call.call_args[0][0]
            assert len(messages) == 2
            assert messages[0] == {"role": "system",
                                   "content": "You are a technical expert"}
            assert messages[1] == {"role": "user", "content": "Explain API"}

    @pytest.mark.asyncio
    async def test_chat(self):
        """Test chat method."""
        client = GnosisClient(api_key="test-key")

        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"}
        ]

        with patch.object(client, "call", return_value="I'm doing well!") as mock_call:
            result = await client.chat(messages)
            assert result == "I'm doing well!"

            # Check messages were passed correctly
            called_messages = mock_call.call_args[0][0]
            assert called_messages == messages

    @pytest.mark.asyncio
    async def test_chat_with_system_prompt(self):
        """Test chat with system prompt."""
        client = GnosisClient(api_key="test-key")

        messages = [{"role": "user", "content": "Hello"}]

        with patch.object(client, "call", return_value="Response") as mock_call:
            result = await client.chat(messages, system_prompt="Be friendly")
            assert result == "Response"

            # Check system prompt was prepended
            called_messages = mock_call.call_args[0][0]
            assert len(called_messages) == 2
            assert called_messages[0] == {
                "role": "system", "content": "Be friendly"}
            assert called_messages[1] == {"role": "user", "content": "Hello"}


class TestGnosisModels:
    """Test suite for Gnosis Pydantic models."""

    def test_gnosis_request_defaults(self):
        """Test GnosisRequest with default values."""
        request = GnosisRequest(messages=[{"role": "user", "content": "Test"}])
        assert request.model == "deepgram"
        assert request.temperature == 0.7
        assert request.max_tokens is None

    def test_gnosis_request_custom(self):
        """Test GnosisRequest with custom values."""
        request = GnosisRequest(
            messages=[{"role": "user", "content": "Test"}],
            model="custom-model",
            temperature=0.9,
            max_tokens=100
        )
        assert request.model == "custom-model"
        assert request.temperature == 0.9
        assert request.max_tokens == 100

    def test_gnosis_response_parsing(self):
        """Test GnosisResponse parsing."""
        response_data = {
            "choices": [
                {"message": {"content": "Response text"}}
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            "model": "deepgram",
            "id": "response-123",
            "created": 1234567890
        }
        response = GnosisResponse(**response_data)
        assert len(response.choices) == 1
        assert response.choices[0]["message"]["content"] == "Response text"
        assert response.usage["prompt_tokens"] == 10
        assert response.model == "deepgram"


@pytest.mark.asyncio
class TestGnosisMain:
    """Test suite for the main CLI function."""

    async def test_main_help(self):
        """Test main function shows help when no arguments."""
        with patch("sys.argv", ["gnosis.py"]):
            # The argparse module will call sys.exit when printing help
            # We just need to verify that it attempts to exit with code 1
            with patch("sys.exit", side_effect=SystemExit(1)) as mock_exit:
                with pytest.raises(SystemExit):
                    from deepctl_cmd_mcp.gnosis import main
                    await main()
                # Verify sys.exit was called (at least once)
                assert mock_exit.called

    async def test_main_single_question(self):
        """Test main function with single question."""
        with patch("sys.argv", ["gnosis.py", "What is Deepgram?"]):
            with patch.dict("os.environ", {"DEEPGRAM_API_KEY": "test-key"}):
                mock_client = Mock()
                mock_client.ask_question = AsyncMock(
                    return_value="Deepgram is...")

                with patch("deepctl_cmd_mcp.gnosis.GnosisClient", return_value=mock_client):
                    with patch("builtins.print") as mock_print:
                        from deepctl_cmd_mcp.gnosis import main
                        await main()
                        mock_print.assert_called_with("Deepgram is...")

    async def test_main_json_output(self):
        """Test main function with JSON output."""
        with patch("sys.argv", ["gnosis.py", "Test", "--json"]):
            with patch.dict("os.environ", {"DEEPGRAM_API_KEY": "test-key"}):
                mock_client = Mock()
                mock_client.ask_question = AsyncMock(return_value="Response")

                with patch("deepctl_cmd_mcp.gnosis.GnosisClient", return_value=mock_client):
                    with patch("builtins.print") as mock_print:
                        from deepctl_cmd_mcp.gnosis import main
                        await main()

                        # Check that JSON was printed
                        printed = mock_print.call_args[0][0]
                        parsed = json.loads(printed)
                        assert parsed["question"] == "Test"
                        assert parsed["response"] == "Response"
