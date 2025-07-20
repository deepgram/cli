"""Global pytest configuration and fixtures for main CLI tests."""

import os
import sys
from pathlib import Path
from typing import Generator

import pytest
from click.testing import CliRunner

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
    monkeypatch.delenv("DEEPGRAM_PROJECT_ID", raising=False)

    yield


# ----------------------------
# CLI Testing Fixtures
# ----------------------------

@pytest.fixture
def cli_runner() -> CliRunner:
    """Create a Click CLI test runner."""
    return CliRunner(mix_stderr=False)


@pytest.fixture
def isolated_cli_runner(cli_runner: CliRunner) -> CliRunner:
    """Create an isolated Click CLI test runner."""
    with cli_runner.isolated_filesystem():
        yield cli_runner
