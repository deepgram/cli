"""Global pytest configuration and fixtures."""

from deepgram_cli.core.client import DeepgramClient
from deepgram_cli.core.auth import AuthManager
from deepgram_cli.core.config import Config
import os
import sys
from pathlib import Path
from typing import Any, Dict, Generator
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner
from pydantic import BaseModel

# Add src to Python path for imports
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))


# ----------------------------
# Test Environment Setup
# ----------------------------

@pytest.fixture(autouse=True)
def test_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Set up isolated test environment for each test."""
    # Create temporary directories
    config_dir = tmp_path / ".deepgram"
    config_dir.mkdir(exist_ok=True)

    # Set environment variables
    monkeypatch.setenv("DEEPGRAM_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("DEEPGRAM_DISABLE_ANALYTICS", "true")
    monkeypatch.setenv("DEEPGRAM_TEST_MODE", "true")

    # Ensure we don't accidentally use real credentials
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)

    yield


# ----------------------------
# Configuration Fixtures
# ----------------------------

@pytest.fixture
def config_data() -> Dict[str, Any]:
    """Sample configuration data."""
    return {
        "version": "1.0",
        "profiles": {
            "default": {
                "api_key": "test_api_key_12345",
                "project_id": "test_project_id",
                "base_url": "https://api.deepgram.com/v1"
            },
            "staging": {
                "api_key": "staging_api_key_67890",
                "project_id": "staging_project_id",
                "base_url": "https://api.staging.deepgram.com/v1"
            }
        },
        "current_profile": "default",
        "output": {
            "format": "json",
            "pretty": True
        }
    }


@pytest.fixture
def mock_config(tmp_path: Path, config_data: Dict[str, Any]) -> Config:
    """Create a mock Config instance."""
    config_path = tmp_path / ".deepgram" / "config.yaml"
    config = Config(config_path=str(config_path))

    # Populate with test data
    for key, value in config_data.items():
        config.set(key, value)

    return config


# ----------------------------
# Auth & Client Fixtures
# ----------------------------

@pytest.fixture
def mock_auth_manager(mock_config: Config) -> Mock:
    """Create a mock AuthManager."""
    auth_manager = Mock(spec=AuthManager)
    auth_manager.config = mock_config
    auth_manager.get_api_key.return_value = "test_api_key_12345"
    auth_manager.get_project_id.return_value = "test_project_id"
    auth_manager.is_authenticated.return_value = True
    auth_manager.validate_credentials.return_value = True

    return auth_manager


@pytest.fixture
def mock_deepgram_client(mock_config: Config, mock_auth_manager: Mock) -> Mock:
    """Create a mock DeepgramClient."""
    client = Mock(spec=DeepgramClient)
    client.config = mock_config
    client.auth_manager = mock_auth_manager

    return client


# ----------------------------
# CLI Testing Fixtures
# ----------------------------

@pytest.fixture
def cli_runner() -> CliRunner:
    """Create a Click CLI test runner."""
    return CliRunner(mix_stderr=False)


@pytest.fixture
def isolated_cli_runner(tmp_path: Path) -> CliRunner:
    """Create an isolated Click CLI test runner with temporary directory."""
    return CliRunner(env={"HOME": str(tmp_path)}, mix_stderr=False)


# ----------------------------
# HTTP/API Mocking Fixtures
# ----------------------------

@pytest.fixture
def mock_httpx_client():
    """Mock httpx client for API calls."""
    with patch("httpx.Client") as mock_client_class:
        mock_client = Mock()
        mock_client_class.return_value = mock_client

        # Default successful response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "success"}
        mock_response.raise_for_status = Mock()

        mock_client.request.return_value = mock_response
        mock_client.get.return_value = mock_response
        mock_client.post.return_value = mock_response

        yield mock_client


# ----------------------------
# Sample Data Fixtures
# ----------------------------

@pytest.fixture
def sample_audio_file(tmp_path: Path) -> Path:
    """Create a sample audio file for testing."""
    audio_file = tmp_path / "test_audio.mp3"
    audio_file.write_bytes(b"fake audio content")
    return audio_file


@pytest.fixture
def sample_transcript_response() -> Dict[str, Any]:
    """Sample Deepgram transcription response."""
    return {
        "metadata": {
            "transaction_key": "test_transaction_123",
            "request_id": "test_request_456",
            "sha256": "test_sha256",
            "created": "2024-01-01T00:00:00.000Z",
            "duration": 10.5,
            "channels": 1,
            "models": ["nova-2"],
            "model_info": {
                "nova-2": {
                    "name": "general",
                    "version": "2024-01-01.0",
                    "arch": "nova-2"
                }
            }
        },
        "results": {
            "channels": [
                {
                    "alternatives": [
                        {
                            "transcript": "This is a test transcription.",
                            "confidence": 0.98,
                            "words": [
                                {
                                    "word": "This",
                                    "start": 0.0,
                                    "end": 0.2,
                                    "confidence": 0.99
                                }
                            ]
                        }
                    ]
                }
            ]
        }
    }


# ----------------------------
# Validation & Testing Helpers
# ----------------------------

@pytest.fixture
def assert_pydantic_model():
    """Helper to assert Pydantic model validation."""
    def _assert_model(model_class: type[BaseModel], data: Dict[str, Any],
                      expected_fields: Dict[str, Any] = None):
        """Assert that a Pydantic model validates correctly."""
        instance = model_class(**data)

        if expected_fields:
            for field, expected_value in expected_fields.items():
                assert getattr(instance, field) == expected_value

        # Ensure model can be serialized
        assert instance.model_dump()
        assert instance.model_dump_json()

        return instance

    return _assert_model


# ----------------------------
# Performance Testing Fixtures
# ----------------------------

@pytest.fixture
def benchmark_timer():
    """Simple benchmark timer for performance testing."""
    import time

    class Timer:
        def __init__(self):
            self.times = []

        def __enter__(self):
            self.start = time.perf_counter()
            return self

        def __exit__(self, *args):
            self.end = time.perf_counter()
            self.times.append(self.end - self.start)

        @property
        def last(self):
            return self.times[-1] if self.times else 0

        @property
        def average(self):
            return sum(self.times) / len(self.times) if self.times else 0

    return Timer()


# ----------------------------
# Async Testing Support
# ----------------------------

@pytest.fixture
async def async_mock_client():
    """Mock async client for testing async operations."""
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = Mock()
        mock_client_class.return_value = mock_client

        # Mock async methods
        async def mock_request(*args, **kwargs):
            return Mock(status_code=200, json=lambda: {"status": "success"})

        mock_client.request = mock_request
        mock_client.get = mock_request
        mock_client.post = mock_request

        yield mock_client


# ----------------------------
# Cleanup Fixtures
# ----------------------------

@pytest.fixture(autouse=True)
def cleanup_keyring():
    """Clean up keyring after each test."""
    yield

    with patch("keyring.delete_password") as mock_delete:
        mock_delete.return_value = None
