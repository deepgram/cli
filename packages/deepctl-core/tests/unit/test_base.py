"""Unit tests for BaseCommand class."""

from typing import Any, Dict, List
from unittest.mock import MagicMock, Mock, patch

import click
import pytest
from click.testing import CliRunner
from deepctl_core import AuthManager, BaseCommand, Config, DeepgramClient


class TestBaseCommand:
    """Test suite for BaseCommand class."""

    @pytest.fixture
    def mock_command_class(self):
        """Create a mock command class for testing."""

        class MockCommand(BaseCommand):
            name = "test"
            help = "Test command"

            def handle(
                self,
                config: Config,
                auth_manager: AuthManager,
                client: DeepgramClient,
                **kwargs,
            ) -> Any:
                """Mock handle method."""
                return {"result": "success"}

        return MockCommand

    @pytest.fixture
    def mock_context(self):
        """Create a mock Click context."""
        ctx = Mock(spec=click.Context)
        ctx.obj = {"config": Config()}
        return ctx

    @pytest.mark.unit
    def test_constructor_requires_name(self):
        """Test that constructor requires a name."""
        with pytest.raises(ValueError, match="Command must have a name"):

            class NoNameCommand(BaseCommand):
                help = "Test command"

                def handle(
                    self,
                    config: Config,
                    auth_manager: AuthManager,
                    client: DeepgramClient,
                    **kwargs,
                ) -> Any:
                    pass

            NoNameCommand()

    @pytest.mark.unit
    def test_constructor_requires_help(self):
        """Test that constructor requires help text."""
        with pytest.raises(ValueError, match="Command must have help text"):

            class NoHelpCommand(BaseCommand):
                name = "test"

                def handle(
                    self,
                    config: Config,
                    auth_manager: AuthManager,
                    client: DeepgramClient,
                    **kwargs,
                ) -> Any:
                    pass

            NoHelpCommand()

    @pytest.mark.unit
    def test_constructor_with_valid_metadata(self, mock_command_class):
        """Test that constructor works with valid metadata."""
        command = mock_command_class()
        assert command.name == "test"
        assert command.help == "Test command"
        assert command.short_help is None
        assert command.requires_auth is False
        assert command.requires_project is False
        assert command.ci_friendly is True

    @pytest.mark.unit
    @patch("deepctl_core.base_command.AuthManager")
    @patch("deepctl_core.base_command.DeepgramClient")
    def test_execute_successful(
        self,
        mock_client_class,
        mock_auth_class,
        mock_command_class,
        mock_context,
    ):
        """Test successful command execution."""
        # Setup mocks
        mock_auth_instance = Mock()
        mock_client_instance = Mock()
        mock_auth_class.return_value = mock_auth_instance
        mock_client_class.return_value = mock_client_instance

        # Create and execute command
        command = mock_command_class()
        with patch.object(command, "output_result") as mock_output:
            command.execute(mock_context, test_arg="value")

        # Verify AuthManager and DeepgramClient were created
        mock_auth_class.assert_called_once_with(mock_context.obj["config"], None, None)
        mock_client_class.assert_called_once_with(
            mock_context.obj["config"], mock_auth_instance
        )

        # Verify guard was not called (requires_auth is False by default)
        mock_auth_instance.guard.assert_not_called()

        # Verify get_project_id was not called (requires_project is False by default)
        mock_auth_instance.get_project_id.assert_not_called()

        # Verify output_result was called with the result
        mock_output.assert_called_once_with(
            {"result": "success"}, mock_context.obj["config"]
        )

    @pytest.mark.unit
    @patch("deepctl_core.base_command.Config")
    @patch("deepctl_core.base_command.AuthManager")
    @patch("deepctl_core.base_command.DeepgramClient")
    def test_execute_without_config_in_context(
        self,
        mock_client_class,
        mock_auth_class,
        mock_config_class,
        mock_command_class,
    ):
        """Test command execution when config is not in context."""
        # Setup mocks
        mock_config_instance = Mock()
        mock_auth_instance = Mock()
        mock_client_instance = Mock()
        mock_config_class.return_value = mock_config_instance
        mock_auth_class.return_value = mock_auth_instance
        mock_client_class.return_value = mock_client_instance

        # Create context without config
        ctx = Mock(spec=click.Context)
        ctx.obj = {}

        # Create and execute command
        command = mock_command_class()
        with patch.object(command, "output_result") as mock_output:
            command.execute(ctx, test_arg="value")

        # Verify Config was created
        mock_config_class.assert_called_once()

        # Verify AuthManager and DeepgramClient were created with new config
        mock_auth_class.assert_called_once_with(mock_config_instance, None, None)
        mock_client_class.assert_called_once_with(
            mock_config_instance, mock_auth_instance
        )

    @pytest.mark.unit
    @patch("deepctl_core.base_command.AuthManager")
    @patch("deepctl_core.base_command.DeepgramClient")
    def test_execute_with_auth_required_success(
        self, mock_client_class, mock_auth_class, mock_context
    ):
        """Test command execution with authentication required and successful auth."""
        # Setup mocks
        mock_auth_instance = Mock()
        mock_client_instance = Mock()
        mock_auth_class.return_value = mock_auth_instance
        mock_client_class.return_value = mock_client_instance

        # Create command that requires auth
        class AuthCommand(BaseCommand):
            name = "auth_test"
            help = "Test auth command"
            requires_auth = True

            def handle(
                self,
                config: Config,
                auth_manager: AuthManager,
                client: DeepgramClient,
                **kwargs,
            ) -> Any:
                return {"authenticated": True}

        command = AuthCommand()
        with patch.object(command, "output_result") as mock_output:
            command.execute(mock_context)

        # Verify auth guard was called
        mock_auth_instance.guard.assert_called_once()

        # Verify output was called with result
        mock_output.assert_called_once_with(
            {"authenticated": True}, mock_context.obj["config"]
        )

    @pytest.mark.unit
    @patch("deepctl_core.base_command.AuthManager")
    @patch("deepctl_core.base_command.DeepgramClient")
    @patch("deepctl_core.base_command.console")
    def test_execute_with_auth_required_failure(
        self, mock_console, mock_client_class, mock_auth_class, mock_context
    ):
        """Test command execution with authentication required and failed auth."""
        # Setup mocks
        mock_auth_instance = Mock()
        mock_auth_instance.guard.side_effect = Exception("Authentication failed")
        mock_auth_class.return_value = mock_auth_instance

        # Create command that requires auth
        class AuthCommand(BaseCommand):
            name = "auth_test"
            help = "Test auth command"
            requires_auth = True

            def handle(
                self,
                config: Config,
                auth_manager: AuthManager,
                client: DeepgramClient,
                **kwargs,
            ) -> Any:
                return {"authenticated": True}

        command = AuthCommand()

        # Execute should raise SystemExit (guard already printed the error)
        with pytest.raises(SystemExit):
            command.execute(mock_context)

    @pytest.mark.unit
    @patch("deepctl_core.base_command.AuthManager")
    @patch("deepctl_core.base_command.DeepgramClient")
    def test_execute_with_project_required_success(
        self, mock_client_class, mock_auth_class, mock_context
    ):
        """Test command execution with project required and project ID available."""
        # Setup mocks
        mock_auth_instance = Mock()
        mock_auth_instance.get_project_id.return_value = "test-project-id"
        mock_client_instance = Mock()
        mock_auth_class.return_value = mock_auth_instance
        mock_client_class.return_value = mock_client_instance

        # Create command that requires project
        class ProjectCommand(BaseCommand):
            name = "project_test"
            help = "Test project command"
            requires_project = True

            def handle(
                self,
                config: Config,
                auth_manager: AuthManager,
                client: DeepgramClient,
                **kwargs,
            ) -> Any:
                return {"project": auth_manager.get_project_id()}

        command = ProjectCommand()
        with patch.object(command, "output_result") as mock_output:
            command.execute(mock_context)

        # Verify get_project_id was called twice (once in execute, once in handle)
        assert mock_auth_instance.get_project_id.call_count == 2

        # Verify output was called with result
        mock_output.assert_called_once_with(
            {"project": "test-project-id"}, mock_context.obj["config"]
        )

    @pytest.mark.unit
    @patch("deepctl_core.base_command.AuthManager")
    @patch("deepctl_core.base_command.DeepgramClient")
    @patch("deepctl_core.base_command.print_error")
    def test_execute_with_project_required_failure(
        self, mock_print_error, mock_client_class, mock_auth_class, mock_context
    ):
        """Test command execution with project required but no project ID."""
        # Setup mocks
        mock_auth_instance = Mock()
        mock_auth_instance.get_project_id.return_value = None
        mock_auth_class.return_value = mock_auth_instance

        # Create command that requires project
        class ProjectCommand(BaseCommand):
            name = "project_test"
            help = "Test project command"
            requires_project = True

            def handle(
                self,
                config: Config,
                auth_manager: AuthManager,
                client: DeepgramClient,
                **kwargs,
            ) -> Any:
                return {"project": "should not reach here"}

        command = ProjectCommand()

        # Execute should raise ClickException
        with pytest.raises(click.ClickException, match="Project ID required"):
            command.execute(mock_context)

        # Verify error was printed to stderr via print_error
        mock_print_error.assert_called_once_with(
            "Project ID is required for this command. "
            "Set DEEPGRAM_PROJECT_ID or configure via profile."
        )

    @pytest.mark.unit
    @patch("deepctl_core.base_command.AuthManager")
    @patch("deepctl_core.base_command.DeepgramClient")
    @patch("deepctl_core.base_command.stderr_console")
    def test_execute_keyboard_interrupt(
        self,
        mock_stderr_console,
        mock_client_class,
        mock_auth_class,
        mock_command_class,
        mock_context,
    ):
        """Test command execution when KeyboardInterrupt is raised."""
        # Setup mocks
        mock_auth_instance = Mock()
        mock_client_instance = Mock()
        mock_auth_class.return_value = mock_auth_instance
        mock_client_class.return_value = mock_client_instance

        # Create command that raises KeyboardInterrupt
        class InterruptCommand(BaseCommand):
            name = "interrupt_test"
            help = "Test interrupt command"

            def handle(
                self,
                config: Config,
                auth_manager: AuthManager,
                client: DeepgramClient,
                **kwargs,
            ) -> Any:
                raise KeyboardInterrupt()

        command = InterruptCommand()

        # Execute should raise click.Abort
        with pytest.raises(click.Abort):
            command.execute(mock_context)

        # Verify cancellation message was printed to stderr
        mock_stderr_console.print.assert_called_once_with(
            "\n[yellow]Command cancelled by user[/yellow]"
        )

    @pytest.mark.unit
    @patch("deepctl_core.base_command.AuthManager")
    @patch("deepctl_core.base_command.DeepgramClient")
    @patch("deepctl_core.base_command.print_error")
    def test_execute_general_exception(
        self,
        mock_print_error,
        mock_client_class,
        mock_auth_class,
        mock_command_class,
        mock_context,
    ):
        """Test command execution when a general exception is raised."""
        # Setup mocks
        mock_auth_instance = Mock()
        mock_client_instance = Mock()
        mock_auth_class.return_value = mock_auth_instance
        mock_client_class.return_value = mock_client_instance

        # Create command that raises an exception
        class ErrorCommand(BaseCommand):
            name = "error_test"
            help = "Test error command"

            def handle(
                self,
                config: Config,
                auth_manager: AuthManager,
                client: DeepgramClient,
                **kwargs,
            ) -> Any:
                raise RuntimeError("Something went wrong")

        command = ErrorCommand()

        # Execute should raise ClickException
        with pytest.raises(click.ClickException, match="Something went wrong"):
            command.execute(mock_context)

        # Verify error was printed to stderr via print_error
        mock_print_error.assert_called_once_with("Command failed: Something went wrong")

    @pytest.mark.unit
    @patch("deepctl_core.base_command.AuthManager")
    @patch("deepctl_core.base_command.DeepgramClient")
    @patch("deepctl_core.base_command.stderr_console")
    @patch("deepctl_core.base_command.print_error")
    def test_execute_general_exception_verbose(
        self,
        mock_print_error,
        mock_stderr_console,
        mock_client_class,
        mock_auth_class,
        mock_command_class,
    ):
        """Test command execution with exception in verbose mode."""
        # Setup mocks with verbose config
        config = Mock(spec=Config)
        config.get.return_value = True  # output.verbose returns True
        ctx = Mock(spec=click.Context)
        ctx.obj = {"config": config}

        mock_auth_instance = Mock()
        mock_client_instance = Mock()
        mock_auth_class.return_value = mock_auth_instance
        mock_client_class.return_value = mock_client_instance

        # Create command that raises an exception
        class ErrorCommand(BaseCommand):
            name = "error_test"
            help = "Test error command"

            def handle(
                self,
                config: Config,
                auth_manager: AuthManager,
                client: DeepgramClient,
                **kwargs,
            ) -> Any:
                raise RuntimeError("Something went wrong")

        command = ErrorCommand()

        # Execute should raise ClickException
        with pytest.raises(click.ClickException, match="Something went wrong"):
            command.execute(ctx)

        # Verify error and traceback went to stderr
        mock_print_error.assert_called_once_with("Command failed: Something went wrong")
        mock_stderr_console.print_exception.assert_called_once()

    @pytest.mark.unit
    def test_output_result_none(self, mock_command_class):
        """Test output_result with None result."""
        command = mock_command_class()
        config = Mock(spec=Config)

        # Should return without output
        command.output_result(None, config)

        # No assertions needed - just verify it doesn't crash

    @pytest.mark.unit
    @patch("deepctl_core.base_command.console")
    def test_output_result_json_dict(self, mock_console, mock_command_class):
        """Test output_result with JSON format and dict result."""
        command = mock_command_class()
        config = Mock(spec=Config)
        config.get.return_value = "json"  # output.format returns json

        result = {"key": "value", "number": 42}
        command.output_result(result, config)

        # Verify print_json was called with JSON string
        import json

        mock_console.print_json.assert_called_once_with(json.dumps(result, indent=2))

    @pytest.mark.unit
    @patch("deepctl_core.base_command.console")
    def test_output_result_json_list(self, mock_console, mock_command_class):
        """Test output_result with JSON format and list result."""
        command = mock_command_class()
        config = Mock(spec=Config)
        config.get.return_value = "json"

        result = [{"id": 1}, {"id": 2}]
        command.output_result(result, config)

        import json

        mock_console.print_json.assert_called_once_with(json.dumps(result, indent=2))

    @pytest.mark.unit
    @patch("deepctl_core.base_command.console")
    def test_output_result_json_string(self, mock_console, mock_command_class):
        """Test output_result with JSON format and string result."""
        command = mock_command_class()
        config = Mock(spec=Config)
        config.get.return_value = "json"

        result = "simple string"
        command.output_result(result, config)

        # String results should be wrapped in object
        import json

        mock_console.print.assert_called_once()
        actual_output = mock_console.print.call_args[0][0]
        expected_data = {"result": "simple string"}
        assert json.loads(actual_output) == expected_data

    @pytest.mark.unit
    def test_output_result_pydantic_model(self, mock_command_class):
        """Test output_result with Pydantic model."""
        from pydantic import BaseModel

        class TestModel(BaseModel):
            name: str
            value: int

        command = mock_command_class()
        config = Mock(spec=Config)
        config.get.return_value = "json"

        result = TestModel(name="test", value=123)

        with patch.object(command, "_output_json") as mock_output_json:
            command.output_result(result, config)

            # Verify model was converted to dict
            mock_output_json.assert_called_once_with({"name": "test", "value": 123})

    @pytest.mark.unit
    def test_output_result_list_of_pydantic_models(self, mock_command_class):
        """Test output_result with list of Pydantic models."""
        from pydantic import BaseModel

        class TestModel(BaseModel):
            name: str
            value: int

        command = mock_command_class()
        config = Mock(spec=Config)
        config.get.return_value = "json"

        result = [
            TestModel(name="test1", value=1),
            TestModel(name="test2", value=2),
        ]

        with patch.object(command, "_output_json") as mock_output_json:
            command.output_result(result, config)

            # Verify models were converted to dicts
            mock_output_json.assert_called_once_with(
                [{"name": "test1", "value": 1}, {"name": "test2", "value": 2}]
            )

    @pytest.mark.unit
    @patch("deepctl_core.base_command.console")
    @patch(
        "deepctl_core.output._output_config",
        {"format": "yaml", "quiet": False, "verbose": False, "color": True},
    )
    def test_output_result_yaml(self, mock_console, mock_command_class):
        """Test output_result with YAML format."""
        command = mock_command_class()
        config = Mock(spec=Config)

        result = {"key": "value", "nested": {"inner": "data"}}
        command.output_result(result, config)

        # Verify YAML output
        import yaml

        expected_yaml = yaml.dump(result, default_flow_style=False)
        mock_console.print.assert_called_once_with(expected_yaml)

    @pytest.mark.unit
    @patch("deepctl_core.base_command.console")
    @patch(
        "deepctl_core.output._output_config",
        {"format": "table", "quiet": False, "verbose": False, "color": True},
    )
    def test_output_result_table_list_of_dicts(self, mock_console, mock_command_class):
        """Test output_result with table format and list of dicts."""
        command = mock_command_class()
        config = Mock(spec=Config)

        result = [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]
        command.output_result(result, config)

        # Verify table was created and printed
        mock_console.print.assert_called_once()
        # Check that Table was created
        assert any("Table" in str(call) for call in mock_console.print.call_args_list)

    @pytest.mark.unit
    @patch("deepctl_core.base_command.console")
    @patch(
        "deepctl_core.output._output_config",
        {"format": "table", "quiet": False, "verbose": False, "color": True},
    )
    def test_output_result_table_dict(self, mock_console, mock_command_class):
        """Test output_result with table format and dict."""
        command = mock_command_class()
        config = Mock(spec=Config)

        result = {"name": "Alice", "age": 30, "city": "NYC"}
        command.output_result(result, config)

        # Verify table was created and printed
        mock_console.print.assert_called_once()

    @pytest.mark.unit
    @patch("deepctl_core.base_command.console")
    @patch(
        "deepctl_core.output._output_config",
        {"format": "csv", "quiet": False, "verbose": False, "color": True},
    )
    def test_output_result_csv_list_of_dicts(self, mock_console, mock_command_class):
        """Test output_result with CSV format and list of dicts."""
        command = mock_command_class()
        config = Mock(spec=Config)

        result = [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]
        command.output_result(result, config)

        # Verify CSV output
        mock_console.print.assert_called_once()
        output = mock_console.print.call_args[0][0]
        assert "name,age" in output
        assert "Alice,30" in output
        assert "Bob,25" in output

    @pytest.mark.unit
    @patch("deepctl_core.base_command.console")
    @patch(
        "deepctl_core.output._output_config",
        {"format": "csv", "quiet": False, "verbose": False, "color": True},
    )
    def test_output_result_csv_dict(self, mock_console, mock_command_class):
        """Test output_result with CSV format and dict."""
        command = mock_command_class()
        config = Mock(spec=Config)

        result = {"name": "Alice", "age": 30}
        command.output_result(result, config)

        # Verify CSV output with key-value format
        mock_console.print.assert_called_once()
        output = mock_console.print.call_args[0][0]
        assert "Key,Value" in output
        assert "name,Alice" in output
        assert "age,30" in output

    @pytest.mark.unit
    @patch("deepctl_core.base_command.console")
    @patch(
        "deepctl_core.output._output_config",
        {"format": "unknown", "quiet": False, "verbose": False, "color": True},
    )
    def test_output_result_unknown_format(self, mock_console, mock_command_class):
        """Test output_result with unknown format falls back to JSON."""
        command = mock_command_class()
        config = Mock(spec=Config)

        result = {"key": "value"}

        with patch.object(command, "_output_json") as mock_output_json:
            command.output_result(result, config)

        # Verify error message and fallback to JSON
        mock_console.print.assert_called_once_with(
            "[red]Unknown output format:[/red] unknown"
        )
        mock_output_json.assert_called_once_with(result)

    @pytest.mark.unit
    def test_confirm_ci_mode_returns_default(self, mock_command_class):
        """Test confirm in CI mode returns default value."""

        # Create command with ci_friendly=False (CI mode)
        class CICommand(BaseCommand):
            name = "ci_test"
            help = "Test CI command"
            ci_friendly = False

            def handle(
                self,
                config: Config,
                auth_manager: AuthManager,
                client: DeepgramClient,
                **kwargs,
            ) -> Any:
                pass

        command = CICommand()

        # Test with default=True
        assert command.confirm("Continue?", default=True) is True

        # Test with default=False
        assert command.confirm("Continue?", default=False) is False

    @pytest.mark.unit
    @patch("deepctl_core.base_command._agentic", False)
    @patch("click.confirm")
    def test_confirm_interactive_mode_success(
        self, mock_click_confirm, mock_command_class
    ):
        """Test confirm in interactive mode when user confirms."""
        command = mock_command_class()
        mock_click_confirm.return_value = True

        result = command.confirm("Continue?", default=False)

        assert result is True
        mock_click_confirm.assert_called_once_with("Continue?", default=False)

    @pytest.mark.unit
    @patch("deepctl_core.base_command._agentic", False)
    @patch("click.confirm")
    def test_confirm_interactive_mode_declined(
        self, mock_click_confirm, mock_command_class
    ):
        """Test confirm in interactive mode when user declines."""
        command = mock_command_class()
        mock_click_confirm.return_value = False

        result = command.confirm("Continue?", default=True)

        assert result is False
        mock_click_confirm.assert_called_once_with("Continue?", default=True)

    @pytest.mark.unit
    @patch("deepctl_core.base_command._agentic", False)
    @patch("click.confirm")
    def test_confirm_interactive_mode_abort(
        self, mock_click_confirm, mock_command_class
    ):
        """Test confirm in interactive mode when user aborts (Ctrl+C)."""
        command = mock_command_class()
        mock_click_confirm.side_effect = click.Abort()

        result = command.confirm("Continue?", default=True)

        assert result is False
        mock_click_confirm.assert_called_once_with("Continue?", default=True)

    @pytest.mark.unit
    def test_prompt_ci_mode_with_default(self, mock_command_class):
        """Test prompt in CI mode returns default when provided."""

        # Create command with ci_friendly=False (CI mode)
        class CICommand(BaseCommand):
            name = "ci_test"
            help = "Test CI command"
            ci_friendly = False

            def handle(
                self,
                config: Config,
                auth_manager: AuthManager,
                client: DeepgramClient,
                **kwargs,
            ) -> Any:
                pass

        command = CICommand()

        # Test with default value
        result = command.prompt("Enter value:", default="default_value")
        assert result == "default_value"

    @pytest.mark.unit
    @patch("click.prompt")
    def test_prompt_ci_mode_without_default(self, mock_click_prompt):
        """Test prompt in CI mode still prompts when no default."""

        # Create command with ci_friendly=False (CI mode)
        class CICommand(BaseCommand):
            name = "ci_test"
            help = "Test CI command"
            ci_friendly = False

            def handle(
                self,
                config: Config,
                auth_manager: AuthManager,
                client: DeepgramClient,
                **kwargs,
            ) -> Any:
                pass

        command = CICommand()
        mock_click_prompt.return_value = "user_input"

        # Test without default value - should still prompt
        result = command.prompt("Enter value:")
        assert result == "user_input"
        mock_click_prompt.assert_called_once_with(
            "Enter value:", default=None, hide_input=False
        )

    @pytest.mark.unit
    @patch("deepctl_core.base_command._agentic", False)
    @patch("click.prompt")
    def test_prompt_interactive_mode(self, mock_click_prompt, mock_command_class):
        """Test prompt in interactive mode."""
        command = mock_command_class()
        mock_click_prompt.return_value = "user_input"

        result = command.prompt("Enter value:", default="default")

        assert result == "user_input"
        mock_click_prompt.assert_called_once_with(
            "Enter value:", default="default", hide_input=False
        )

    @pytest.mark.unit
    @patch("click.prompt")
    def test_prompt_with_hidden_input(self, mock_click_prompt, mock_command_class):
        """Test prompt with hidden input (password mode)."""
        command = mock_command_class()
        mock_click_prompt.return_value = "secret_password"

        result = command.prompt("Enter password:", hide_input=True)

        assert result == "secret_password"
        mock_click_prompt.assert_called_once_with(
            "Enter password:", default=None, hide_input=True
        )

    @pytest.mark.unit
    @patch("click.prompt")
    def test_prompt_abort_raises_exception(self, mock_click_prompt, mock_command_class):
        """Test prompt raises ClickException when user aborts."""
        command = mock_command_class()
        mock_click_prompt.side_effect = click.Abort()

        with pytest.raises(click.ClickException, match="User cancelled input"):
            command.prompt("Enter value:")

    @pytest.mark.unit
    def test_validate_file_path_valid_file(self, mock_command_class, tmp_path):
        """Test validate_file_path with a valid file."""
        command = mock_command_class()

        # Create a temporary file
        test_file = tmp_path / "test.txt"
        test_file.write_text("test content")

        assert command.validate_file_path(str(test_file)) is True

    @pytest.mark.unit
    def test_validate_file_path_nonexistent(self, mock_command_class):
        """Test validate_file_path with non-existent path."""
        command = mock_command_class()

        assert command.validate_file_path("/non/existent/file.txt") is False

    @pytest.mark.unit
    def test_validate_file_path_directory(self, mock_command_class, tmp_path):
        """Test validate_file_path with a directory path."""
        command = mock_command_class()

        # Create a temporary directory
        test_dir = tmp_path / "test_dir"
        test_dir.mkdir()

        assert command.validate_file_path(str(test_dir)) is False

    @pytest.mark.unit
    def test_validate_file_path_empty(self, mock_command_class):
        """Test validate_file_path with empty path."""
        command = mock_command_class()

        assert command.validate_file_path("") is False

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "url,expected",
        [
            # Valid URLs
            ("http://example.com", True),
            ("https://example.com", True),
            ("http://example.com/path", True),
            ("https://example.com/path/to/resource", True),
            ("http://example.com:8080", True),
            ("https://example.com:443/path", True),
            ("http://localhost", True),
            ("http://localhost:8000", True),
            ("http://192.168.1.1", True),
            ("https://192.168.1.1:8443/api", True),
            ("http://sub.domain.example.com", True),
            ("https://sub.domain.example.co.uk", True),
            # Invalid URLs
            ("example.com", False),  # Missing protocol
            ("ftp://example.com", False),  # Wrong protocol
            ("http://", False),  # Missing domain
            ("http:///path", False),  # Missing domain
            ("http://example", False),  # Missing TLD
            ("http://example..com", False),  # Double dot
            # Port number (regex doesn't validate range)
            ("http://example.com:999999", True),
            ("", False),  # Empty string
            ("not a url", False),  # Plain text
            ("http://exam ple.com", False),  # Space in domain
        ],
    )
    def test_validate_url(self, mock_command_class, url, expected):
        """Test validate_url with various URL formats."""
        command = mock_command_class()
        assert command.validate_url(url) is expected

    @pytest.mark.unit
    def test_get_arguments_default(self, mock_command_class):
        """Test get_arguments returns empty list by default."""
        command = mock_command_class()
        assert command.get_arguments() == []

    @pytest.mark.unit
    def test_get_arguments_override(self):
        """Test get_arguments can be overridden in subclasses."""

        class CommandWithArgs(BaseCommand):
            name = "args_test"
            help = "Test command with arguments"

            def handle(
                self,
                config: Config,
                auth_manager: AuthManager,
                client: DeepgramClient,
                **kwargs,
            ) -> Any:
                pass

            def get_arguments(self) -> list[dict[str, Any]]:
                return [
                    {"name": "--option1", "type": str, "help": "First option"},
                    {
                        "name": "--option2",
                        "type": int,
                        "default": 42,
                        "help": "Second option",
                    },
                ]

        command = CommandWithArgs()
        args = command.get_arguments()

        assert len(args) == 2
        assert args[0]["name"] == "--option1"
        assert args[0]["type"] == str
        assert args[1]["name"] == "--option2"
        assert args[1]["default"] == 42


class TestIsGuided:
    """Verify is_guided() distinguishes bare invocations from scripted ones."""

    @pytest.fixture
    def command(self):
        class MockCommand(BaseCommand):
            name = "test"
            help = "Test command"

            def handle(self, *args, **kwargs):
                return None

        return MockCommand()

    @staticmethod
    def _ctx_with_sources(sources):
        ctx = Mock(spec=click.Context)
        ctx.command = Mock()
        ctx.command.params = [_param(n) for n in sources.keys()]

        def get_source(name):
            src_name = sources.get(name)
            if src_name is None:
                return None
            src = Mock()
            src.name = src_name
            return src

        ctx.get_parameter_source = get_source
        return ctx

    @pytest.mark.unit
    def test_bare_invocation_is_guided(self, command):
        ctx = self._ctx_with_sources(
            {"foo": "DEFAULT", "bar": "DEFAULT", "baz": "DEFAULT_MAP"}
        )
        with patch("deepctl_core.base_command._agentic", False):
            assert command.is_guided(ctx) is True

    @pytest.mark.unit
    def test_any_commandline_arg_breaks_guided(self, command):
        ctx = self._ctx_with_sources(
            {"foo": "DEFAULT", "bar": "COMMANDLINE", "baz": "DEFAULT"}
        )
        with patch("deepctl_core.base_command._agentic", False):
            assert command.is_guided(ctx) is False

    @pytest.mark.unit
    def test_env_var_breaks_guided(self, command):
        ctx = self._ctx_with_sources(
            {"foo": "DEFAULT", "bar": "ENVIRONMENT"}
        )
        with patch("deepctl_core.base_command._agentic", False):
            assert command.is_guided(ctx) is False

    @pytest.mark.unit
    def test_agentic_short_circuits_to_false(self, command):
        ctx = self._ctx_with_sources({"foo": "DEFAULT", "bar": "DEFAULT"})
        with patch("deepctl_core.base_command._agentic", True):
            assert command.is_guided(ctx) is False


class TestTelemetryTagging:
    """Verify _tag_telemetry_start and _tag_telemetry_status emit usage signal."""

    @pytest.fixture
    def command(self):
        class MockCommand(BaseCommand):
            name = "test"
            help = "Test command"

            def handle(self, *args, **kwargs):
                return None

        return MockCommand()

    @staticmethod
    def _ctx_with_flags(
        used_flags, defaulted_flags, output="json", path="deepctl listen"
    ):
        ctx = Mock(spec=click.Context)
        ctx.command_path = path
        ctx.params = {"output": output}
        ctx.command = Mock()
        ctx.command.params = [
            *(_param(n) for n in used_flags),
            *(_param(n) for n in defaulted_flags),
        ]
        used = set(used_flags)

        def get_source(name):
            src = Mock()
            src.name = "COMMANDLINE" if name in used else "DEFAULT"
            return src

        ctx.get_parameter_source = get_source
        return ctx

    @pytest.mark.unit
    def test_start_renames_transaction_to_command_path(self, command):
        ctx = self._ctx_with_flags(["diarize"], ["model"])
        scope = Mock()
        scope.transaction = Mock()
        with patch("sentry_sdk.get_current_scope", return_value=scope):
            command._tag_telemetry_start(ctx)
        assert scope.transaction.name == "deepctl listen"

    @pytest.mark.unit
    def test_start_records_only_user_provided_flags(self, command):
        ctx = self._ctx_with_flags(["diarize", "summarize"], ["model", "language"])
        scope = Mock()
        scope.transaction = Mock()
        with patch("sentry_sdk.get_current_scope", return_value=scope):
            command._tag_telemetry_start(ctx)
        scope.set_tag.assert_any_call("cmd.flags", "diarize,summarize")
        scope.set_tag.assert_any_call("cmd.output_format", "json")

    @pytest.mark.unit
    def test_start_no_flags_records_none_sentinel(self, command):
        ctx = self._ctx_with_flags([], [], output=None, path="deepctl whoami")
        scope = Mock()
        scope.transaction = Mock()
        with patch("sentry_sdk.get_current_scope", return_value=scope):
            command._tag_telemetry_start(ctx)
        scope.set_tag.assert_any_call("cmd.flags", "(none)")
        scope.set_tag.assert_any_call("cmd.output_format", "default")

    @pytest.mark.unit
    def test_start_swallows_exceptions(self, command):
        ctx = self._ctx_with_flags(["diarize"], [])
        with patch("sentry_sdk.get_current_scope", side_effect=RuntimeError("boom")):
            command._tag_telemetry_start(ctx)

    @pytest.mark.unit
    def test_status_sets_cmd_status(self, command):
        scope = Mock()
        with patch("sentry_sdk.get_current_scope", return_value=scope):
            command._tag_telemetry_status("ok")
        scope.set_tag.assert_called_with("cmd.status", "ok")

    @pytest.mark.unit
    @pytest.mark.parametrize("status", ["ok", "error", "cancelled", "partial"])
    def test_status_passes_through_status_string(self, command, status):
        scope = Mock()
        with patch("sentry_sdk.get_current_scope", return_value=scope):
            command._tag_telemetry_status(status)
        scope.set_tag.assert_called_with("cmd.status", status)

    @pytest.mark.unit
    def test_status_swallows_exceptions(self, command):
        with patch("sentry_sdk.get_current_scope", side_effect=RuntimeError("boom")):
            command._tag_telemetry_status("error")


def _param(name):
    p = Mock()
    p.name = name
    return p
