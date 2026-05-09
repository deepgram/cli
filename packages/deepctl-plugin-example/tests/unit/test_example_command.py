"""Tests for the example plugin command."""

from unittest.mock import MagicMock, Mock

import pytest
from click.testing import CliRunner
from deepctl_core import AuthManager, Config, DeepgramClient
from deepctl_plugin_example import ExampleCommand


@pytest.fixture
def example_command():
    """Create example command instance."""
    return ExampleCommand()


@pytest.fixture
def mock_config():
    """Create mock config."""
    config = Mock(spec=Config)
    config.get.return_value = "json"
    return config


@pytest.fixture
def mock_auth_manager():
    """Create mock auth manager."""
    return Mock(spec=AuthManager)


@pytest.fixture
def mock_client():
    """Create mock Deepgram client."""
    return Mock(spec=DeepgramClient)


class TestExampleCommand:
    """Test cases for ExampleCommand."""

    def test_command_metadata(self, example_command):
        """Test command has correct metadata."""
        assert example_command.name == "example"
        assert (
            example_command.help
            == "Example plugin command demonstrating the plugin system"
        )
        assert example_command.short_help == "Example plugin command"
        assert example_command.requires_auth is False
        assert example_command.requires_project is False

    def test_get_arguments(self, example_command):
        """Test command arguments are properly defined."""
        args = example_command.get_arguments()
        assert len(args) == 3

        # Check greeting argument
        greeting_arg = next(
            arg for arg in args if "--greeting" in arg["names"]
        )
        assert greeting_arg["help"] == "Custom greeting message"
        assert greeting_arg["type"] == str
        assert greeting_arg["default"] == "Hello"

        # Check name argument
        name_arg = next(arg for arg in args if "--name" in arg["names"])
        assert name_arg["help"] == "Name to greet"
        assert name_arg["type"] == str
        assert name_arg["default"] == "World"

        # Check show-info flag
        info_arg = next(arg for arg in args if "--show-info" in arg["names"])
        assert info_arg["help"] == "Show plugin system information"
        assert info_arg["is_flag"] is True

    def test_handle_default(
        self, example_command, mock_config, mock_auth_manager, mock_client
    ):
        """Test handle with default arguments."""
        result = example_command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
        )

        assert result.message == "Hello, World!"
        assert result.plugin == "deepctl-plugin-example"
        assert result.version == "0.1.0"
        assert result.greeting == "Hello"
        assert result.name == "World"

    def test_handle_custom_greeting(
        self, example_command, mock_config, mock_auth_manager, mock_client
    ):
        """Test handle with custom greeting and name."""
        result = example_command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            greeting="Howdy",
            name="Partner",
        )

        assert result.message == "Howdy, Partner!"
        assert result.plugin == "deepctl-plugin-example"
        assert result.version == "0.1.0"
        assert result.greeting == "Howdy"
        assert result.name == "Partner"

    def test_handle_show_info(
        self, example_command, mock_config, mock_auth_manager, mock_client
    ):
        """Test handle with show_info flag."""
        # This test ensures the method runs without error
        # show_info returns None to avoid additional output
        result = example_command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            show_info=True,
        )

        assert result is None  # show_info returns None
