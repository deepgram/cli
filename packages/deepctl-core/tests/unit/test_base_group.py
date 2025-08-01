"""Unit tests for BaseGroupCommand class."""

from typing import Any, Dict, List
from unittest.mock import Mock, patch, MagicMock

import click
import pytest
from click.testing import CliRunner

from deepctl_core import (
    BaseCommand,
    BaseGroupCommand,
    AuthManager,
    DeepgramClient,
    Config,
)


class TestBaseGroupCommand:
    """Test suite for BaseGroupCommand class."""

    @pytest.fixture
    def mock_group_command_class(self):
        """Create a mock group command class for testing."""

        class MockGroupCommand(BaseGroupCommand):
            name = "testgroup"
            help = "Test group command"

            def handle_group(
                self,
                config: Config,
                auth_manager: AuthManager,
                client: DeepgramClient,
                **kwargs,
            ) -> Any:
                """Mock handle_group method."""
                return {"group_result": "success"}

        return MockGroupCommand

    @pytest.fixture
    def mock_subcommand_class(self):
        """Create a mock subcommand class for testing."""

        class MockSubCommand(BaseCommand):
            name = "subtest"
            help = "Test subcommand"

            def handle(
                self,
                config: Config,
                auth_manager: AuthManager,
                client: DeepgramClient,
                **kwargs,
            ) -> Any:
                """Mock handle method."""
                return {"subcommand_result": "success"}

        return MockSubCommand

    @pytest.fixture
    def mock_context(self):
        """Create a mock Click context."""
        ctx = Mock(spec=click.Context)
        ctx.obj = {"config": Config()}
        ctx.invoked_subcommand = None
        return ctx

    @pytest.mark.unit
    def test_constructor_initializes_group_attributes(
        self, mock_group_command_class
    ):
        """Test that constructor initializes group-specific attributes."""
        command = mock_group_command_class()

        assert command.is_group is True
        assert command.subcommands == {}
        assert command.invoke_without_command is False

    @pytest.mark.unit
    def test_add_subcommand(
        self, mock_group_command_class, mock_subcommand_class
    ):
        """Test adding subcommands to a group."""
        group = mock_group_command_class()
        subcommand = mock_subcommand_class

        group.add_subcommand("subtest", subcommand)

        assert "subtest" in group.subcommands
        assert group.subcommands["subtest"] == subcommand

    @pytest.mark.unit
    def test_get_subcommands(
        self, mock_group_command_class, mock_subcommand_class
    ):
        """Test getting all subcommands from a group."""
        group = mock_group_command_class()

        # Add multiple subcommands
        group.add_subcommand("sub1", mock_subcommand_class)
        group.add_subcommand("sub2", mock_subcommand_class)

        subcommands = group.get_subcommands()

        assert len(subcommands) == 2
        assert "sub1" in subcommands
        assert "sub2" in subcommands

    @pytest.mark.unit
    def test_handle_without_subcommand_shows_help(
        self, mock_group_command_class, mock_context
    ):
        """Test that handle shows help when no subcommand is invoked."""
        group = mock_group_command_class()

        # Mock click.echo to capture help output
        with (
            patch("deepctl_core.base_group_command.click.echo") as mock_echo,
            patch(
                "deepctl_core.base_group_command.click.get_current_context",
                return_value=mock_context,
            ),
        ):

            mock_context.get_help.return_value = "Mock help text"

            result = group.handle(
                config=Mock(spec=Config),
                auth_manager=Mock(spec=AuthManager),
                client=Mock(spec=DeepgramClient),
            )

            # Verify help was shown
            mock_echo.assert_called_once_with("Mock help text")
            assert result is None

    @pytest.mark.unit
    def test_handle_with_invoke_without_command(
        self, mock_group_command_class, mock_context
    ):
        """Test handle when invoke_without_command is True."""

        class InvokeWithoutCommandGroup(BaseGroupCommand):
            name = "testgroup"
            help = "Test group"
            invoke_without_command = True

            def handle_group(
                self,
                config: Config,
                auth_manager: AuthManager,
                client: DeepgramClient,
                **kwargs,
            ) -> Any:
                return {"handled": True}

        group = InvokeWithoutCommandGroup()

        # Verify the attribute is set correctly
        assert group.invoke_without_command is True

        with patch(
            "deepctl_core.base_group_command.click.get_current_context",
            return_value=mock_context,
        ):
            result = group.handle(
                config=Mock(spec=Config),
                auth_manager=Mock(spec=AuthManager),
                client=Mock(spec=DeepgramClient),
            )

            assert result == {"handled": True}

    @pytest.mark.unit
    def test_handle_with_subcommand_invoked(
        self, mock_group_command_class, mock_context
    ):
        """Test handle when a subcommand is invoked."""
        group = mock_group_command_class()
        mock_context.invoked_subcommand = "subtest"

        with patch(
            "deepctl_core.base_group_command.click.get_current_context",
            return_value=mock_context,
        ):
            result = group.handle(
                config=Mock(spec=Config),
                auth_manager=Mock(spec=AuthManager),
                client=Mock(spec=DeepgramClient),
            )

            # Should call handle_group which returns success
            assert result == {"group_result": "success"}

    @pytest.mark.unit
    def test_get_click_group(self, mock_group_command_class):
        """Test creation of Click Group object."""
        group = mock_group_command_class()

        click_group = group.get_click_group()

        assert isinstance(click_group, click.Group)
        assert click_group.name == "testgroup"
        assert click_group.help == "Test group command"
        assert click_group.invoke_without_command is False

    @pytest.mark.unit
    def test_get_click_group_with_arguments(self):
        """Test Click Group creation with arguments."""

        class GroupWithArgs(BaseGroupCommand):
            name = "testgroup"
            help = "Test group"

            def get_arguments(self) -> List[Dict[str, Any]]:
                return [
                    {
                        "names": ["--verbose", "-v"],
                        "help": "Verbose output",
                        "is_flag": True,
                        "is_option": True,
                    }
                ]

            def handle_group(
                self,
                config: Config,
                auth_manager: AuthManager,
                client: DeepgramClient,
                **kwargs,
            ) -> Any:
                return {"verbose": kwargs.get("verbose", False)}

        group = GroupWithArgs()
        click_group = group.get_click_group()

        # Check that the group has the verbose option
        assert len(click_group.params) == 1
        assert click_group.params[0].name == "verbose"

    @pytest.mark.unit
    def test_group_callback_execution(self, mock_group_command_class):
        """Test that the group callback properly executes."""
        group = mock_group_command_class()
        click_group = group.get_click_group()

        # Create a test context
        runner = CliRunner()

        with patch.object(group, "execute") as mock_execute:
            mock_execute.return_value = {"executed": True}

            # Get the callback function
            callback = click_group.callback

            # Create a context and invoke the callback
            ctx = click.Context(click_group)
            with ctx:
                result = callback()

            # Verify execute was called
            mock_execute.assert_called_once()

    @pytest.mark.unit
    def test_handle_group_default_implementation(self):
        """Test that default handle_group does nothing."""

        class MinimalGroup(BaseGroupCommand):
            name = "minimal"
            help = "Minimal group"
            # Don't override handle_group

        group = MinimalGroup()

        # Default implementation should return None
        result = group.handle_group(
            config=Mock(spec=Config),
            auth_manager=Mock(spec=AuthManager),
            client=Mock(spec=DeepgramClient),
        )

        assert result is None

    @pytest.mark.unit
    def test_subcommand_inheritance(self, mock_group_command_class):
        """Test that group commands inherit from BaseCommand properly."""
        group = mock_group_command_class()

        # Should have all BaseCommand attributes
        assert hasattr(group, "name")
        assert hasattr(group, "help")
        assert hasattr(group, "execute")
        assert hasattr(group, "requires_auth")
        assert hasattr(group, "requires_project")

        # Plus group-specific attributes
        assert hasattr(group, "is_group")
        assert hasattr(group, "subcommands")
        assert hasattr(group, "add_subcommand")
        assert hasattr(group, "get_subcommands")
        assert hasattr(group, "get_click_group")

    @pytest.mark.unit
    def test_group_with_custom_short_help(self):
        """Test group with custom short_help."""

        class GroupWithShortHelp(BaseGroupCommand):
            name = "testgroup"
            help = "This is a long help text for the test group command"
            short_help = "Test group"

            def handle_group(
                self,
                config: Config,
                auth_manager: AuthManager,
                client: DeepgramClient,
                **kwargs,
            ) -> Any:
                pass

        group = GroupWithShortHelp()
        click_group = group.get_click_group()

        assert click_group.short_help == "Test group"

    @pytest.mark.unit
    def test_nested_context_handling(
        self, mock_group_command_class, mock_context
    ):
        """Test that context is properly handled in nested scenarios."""
        group = mock_group_command_class()

        # Set up nested context
        parent_ctx = Mock(spec=click.Context)
        mock_context.parent = parent_ctx

        with patch(
            "deepctl_core.base_group_command.click.get_current_context",
            return_value=mock_context,
        ):
            # Execute the group
            group.handle(
                config=Mock(spec=Config),
                auth_manager=Mock(spec=AuthManager),
                client=Mock(spec=DeepgramClient),
            )

            # Context should be accessible during execution
            assert mock_context.parent == parent_ctx

    @pytest.mark.unit
    def test_group_command_with_both_options_and_arguments(self):
        """Test group command with both options and arguments."""

        class ComplexGroup(BaseGroupCommand):
            name = "complex"
            help = "Complex group"

            def get_arguments(self) -> List[Dict[str, Any]]:
                return [
                    {
                        "name": "arg1",
                        "type": str,
                        "required": True,
                        "is_option": False,
                    },
                    {
                        "names": ["--flag", "-f"],
                        "help": "A flag option",
                        "is_flag": True,
                        "is_option": True,
                    },
                ]

            def handle_group(
                self,
                config: Config,
                auth_manager: AuthManager,
                client: DeepgramClient,
                **kwargs,
            ) -> Any:
                return kwargs

        group = ComplexGroup()
        click_group = group.get_click_group()

        # Should have both argument and option
        assert len(click_group.params) == 2

        # Find the argument and option
        arg_param = next(
            p for p in click_group.params if isinstance(p, click.Argument)
        )
        opt_param = next(
            p for p in click_group.params if isinstance(p, click.Option)
        )

        assert arg_param.name == "arg1"
        assert opt_param.name == "flag"
