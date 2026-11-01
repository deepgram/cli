"""Unit tests for PluginManager class."""

import os
from importlib.metadata import EntryPoint
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, Mock, create_autospec, patch

import click
import pytest
from deepctl_core import (
    AuthManager,
    BaseCommand,
    BaseGroupCommand,
    Config,
    DeepgramClient,
    PluginManager,
)
from deepctl_core import config as config_module
from deepctl_core.output import AI_AGENT_ENV_MARKERS


class TestPluginManager:
    """Test suite for PluginManager class."""

    @pytest.fixture
    def plugin_manager(self):
        """Create a plugin manager instance for testing."""
        return PluginManager()

    @pytest.fixture
    def mock_command_class(self):
        """Create a mock command class for testing."""

        class MockCommand(BaseCommand):
            name = "test-command"
            help = "Test command"

            def handle(
                self,
                config: Config,
                auth_manager: AuthManager,
                client: DeepgramClient,
                **kwargs,
            ) -> Any:
                return {"result": "success"}

        return MockCommand

    @pytest.fixture
    def mock_group_command_class(self):
        """Create a mock group command class for testing."""

        class MockGroupCommand(BaseGroupCommand):
            name = "test-group"
            help = "Test group command"

            def handle_group(
                self,
                config: Config,
                auth_manager: AuthManager,
                client: DeepgramClient,
                **kwargs,
            ) -> Any:
                return {"group": "success"}

        return MockGroupCommand

    @pytest.fixture
    def mock_cli_group(self):
        """Create a mock Click CLI group."""
        return create_autospec(click.Group, spec_set=True)

    @pytest.mark.unit
    def test_constructor_initializes_attributes(self, plugin_manager):
        """Test that constructor initializes required attributes."""
        assert plugin_manager.loaded_plugins == {}
        assert plugin_manager.command_classes == {}

    @pytest.mark.unit
    def test_hyphenated_command_name_from_underscore_entry_point(
        self, plugin_manager, mock_command_class, mock_cli_group
    ):
        """Test that entry points with underscores are converted to hyphenated commands."""
        # Create mock entry point with underscore name
        mock_entry_point = Mock(spec=EntryPoint)
        mock_entry_point.name = "test_command"  # Underscore in entry point
        mock_entry_point.load.return_value = mock_command_class

        # Mock metadata.entry_points
        with patch(
            "deepctl_core.plugin_manager.metadata.entry_points"
        ) as mock_entry_points:
            mock_ep_group = Mock()
            mock_ep_group.select.return_value = [mock_entry_point]
            mock_entry_points.return_value = mock_ep_group

            # Load plugins
            plugin_manager._load_external_plugins(mock_cli_group)

            # Verify the command was added with the class's hyphenated name
            mock_cli_group.add_command.assert_called_once()
            click_command = mock_cli_group.add_command.call_args[0][0]
            # Should use class name, not entry point
            assert click_command.name == "test-command"

    @pytest.mark.unit
    def test_hyphenated_subcommand_loading(
        self, plugin_manager, mock_group_command_class, mock_command_class
    ):
        """Test loading subcommands with hyphenated names."""
        # Create group instance
        group_instance = mock_group_command_class()

        # Create mock Click group
        mock_click_group = create_autospec(click.Group, spec_set=True)

        # Create mock entry point for subcommand
        mock_entry_point = Mock(spec=EntryPoint)
        mock_entry_point.name = "sub_command"  # Underscore in entry point
        mock_entry_point.load.return_value = mock_command_class

        # Mock metadata.entry_points
        with patch(
            "deepctl_core.plugin_manager.metadata.entry_points"
        ) as mock_entry_points:
            mock_ep_group = Mock()
            mock_ep_group.select.return_value = [mock_entry_point]
            mock_entry_points.return_value = mock_ep_group

            # Load subcommands
            plugin_manager._load_subcommands_for_group(mock_click_group, group_instance)

            # Verify subcommand was added twice (once for each entry point group checked)
            assert mock_click_group.add_command.call_count == 2
            # Both calls should add the same command
            for call in mock_click_group.add_command.call_args_list:
                click_command = call[0][0]
                assert click_command.name == "test-command"

    @pytest.mark.unit
    def test_function_name_conversion_for_hyphens(
        self, plugin_manager, mock_command_class
    ):
        """Test that hyphenated command names are converted to underscores for function names."""
        # Create command instance with hyphenated name
        command_instance = mock_command_class()
        command_instance.name = "test-hyphenated-command"

        # Create Click command
        click_command = plugin_manager._create_click_command(command_instance)

        # Check that function name has underscores
        assert click_command.callback.__name__ == "test_hyphenated_command"

    @pytest.mark.unit
    def test_group_function_name_conversion_for_hyphens(
        self, plugin_manager, mock_group_command_class
    ):
        """Test that hyphenated group names are converted to underscores for function names."""
        # Create group instance with hyphenated name
        group_instance = mock_group_command_class()
        group_instance.name = "test-hyphenated-group"

        # Create Click group
        click_group = plugin_manager._create_click_group(group_instance)

        # Check that function name has underscores
        assert click_group.callback.__name__ == "test_hyphenated_group"

    @pytest.mark.unit
    @pytest.mark.usefixtures("no_hidden_scan")
    def test_subcommand_entry_point_pattern(
        self, plugin_manager, mock_group_command_class, mock_command_class
    ):
        """Test that subcommands are loaded from the correct entry point pattern."""
        # Create group with specific name
        group_instance = mock_group_command_class()
        group_instance.name = "debug"

        mock_click_group = create_autospec(click.Group, spec_set=True)

        # Create mock entry points
        mock_entry_point = Mock(spec=EntryPoint)
        # Not a built-in subcommand name: an entry point named like one
        # (``audio``) from another module is refused before it is loaded.
        mock_entry_point.name = "test_command"
        mock_entry_point.load.return_value = mock_command_class

        with patch(
            "deepctl_core.plugin_manager.metadata.entry_points"
        ) as mock_entry_points:
            mock_ep_group = Mock()

            # Should query for "deepctl.subcommands.debug"
            def mock_select(group):
                if group == "deepctl.subcommands.debug":
                    return [mock_entry_point]
                return []

            mock_ep_group.select = mock_select
            mock_entry_points.return_value = mock_ep_group

            # Load subcommands
            plugin_manager._load_subcommands_for_group(mock_click_group, group_instance)

            # Verify it loaded the subcommand
            mock_click_group.add_command.assert_called_once()

    @pytest.mark.unit
    def test_nested_hyphenated_commands(self, plugin_manager):
        """Test that nested commands with hyphens work correctly."""

        # Create a group with hyphenated name
        class DebugNetworkGroup(BaseGroupCommand):
            name = "debug-network"
            help = "Debug network commands"

        # Create a subcommand with hyphenated name
        class TestConnectionCommand(BaseCommand):
            name = "test-connection"
            help = "Test network connection"

            def handle(self, config, auth_manager, client, **kwargs):
                return {"tested": True}

        group_instance = DebugNetworkGroup()
        mock_click_group = create_autospec(click.Group, spec_set=True)

        # Create mock entry point
        mock_entry_point = Mock(spec=EntryPoint)
        mock_entry_point.name = "test_connection"  # Entry point with underscore
        mock_entry_point.load.return_value = TestConnectionCommand

        with patch(
            "deepctl_core.plugin_manager.metadata.entry_points"
        ) as mock_entry_points:
            mock_ep_group = Mock()

            def mock_select(group):
                if group == "deepctl.subcommands.debug-network":
                    return [mock_entry_point]
                return []

            mock_ep_group.select = mock_select
            mock_entry_points.return_value = mock_ep_group

            # Load subcommands
            plugin_manager._load_subcommands_for_group(mock_click_group, group_instance)

            # Verify the command was added with correct hyphenated name
            mock_click_group.add_command.assert_called_once()
            click_command = mock_click_group.add_command.call_args[0][0]
            assert click_command.name == "test-connection"

    @pytest.mark.unit
    def test_command_list_returns_all_loaded_commands(
        self, plugin_manager, mock_command_class
    ):
        """Test that get_command_list returns all loaded commands."""
        # Add some commands
        plugin_manager.command_classes["cmd1"] = mock_command_class
        plugin_manager.loaded_plugins["cmd2"] = Mock()

        command_list = plugin_manager.get_command_list()

        assert "cmd1" in command_list
        assert "cmd2" in command_list
        assert len(command_list) == 2

    @pytest.mark.unit
    def test_error_handling_for_invalid_plugin(self, plugin_manager, mock_cli_group):
        """Test that invalid plugins are handled gracefully."""
        # Create mock entry point that raises exception
        mock_entry_point = Mock(spec=EntryPoint)
        mock_entry_point.name = "bad_plugin"
        mock_entry_point.load.side_effect = ImportError("Cannot import plugin")

        with patch(
            "deepctl_core.plugin_manager.metadata.entry_points"
        ) as mock_entry_points:
            mock_ep_group = Mock()
            mock_ep_group.select.return_value = [mock_entry_point]
            mock_entry_points.return_value = mock_ep_group

            # Should not raise exception
            plugin_manager._load_external_plugins(mock_cli_group)

            # Command should not be added
            mock_cli_group.add_command.assert_not_called()

    @pytest.mark.unit
    def test_validate_plugin(self, plugin_manager, mock_command_class):
        """Test plugin validation."""
        # Valid plugin
        assert plugin_manager.validate_plugin(mock_command_class) is True

        # Invalid plugin - no name
        class NoNameCommand(BaseCommand):
            help = "Command without name"

        assert plugin_manager.validate_plugin(NoNameCommand) is False

        # Invalid plugin - not a BaseCommand
        class NotACommand:
            name = "test"
            help = "Not a command"

        assert plugin_manager.validate_plugin(NotACommand) is False

    @pytest.mark.unit
    def test_constructor_initializes_dedup_set(self, plugin_manager):
        """Test that constructor initializes the dedup tracking set."""
        assert plugin_manager._loaded_entry_point_values == set()

    @pytest.mark.unit
    def test_builtin_commands_track_entry_point_values(
        self, plugin_manager, mock_command_class, mock_cli_group
    ):
        """Test that loading builtin commands populates the dedup set."""
        mock_entry_point = Mock(spec=EntryPoint)
        mock_entry_point.name = "test_cmd"
        mock_entry_point.value = "my_module:TestCommand"
        mock_entry_point.load.return_value = mock_command_class

        with patch(
            "deepctl_core.plugin_manager.metadata.entry_points"
        ) as mock_entry_points:
            mock_ep_group = Mock()
            mock_ep_group.select.return_value = [mock_entry_point]
            mock_entry_points.return_value = mock_ep_group

            plugin_manager._load_builtin_commands(mock_cli_group)

            assert "my_module:TestCommand" in plugin_manager._loaded_entry_point_values

    @pytest.mark.unit
    def test_external_plugins_track_entry_point_values(
        self, plugin_manager, mock_command_class, mock_cli_group
    ):
        """Test that loading external plugins populates the dedup set."""
        mock_entry_point = Mock(spec=EntryPoint)
        mock_entry_point.name = "ext_plugin"
        mock_entry_point.value = "ext_module:ExtPlugin"
        mock_entry_point.load.return_value = mock_command_class

        with patch(
            "deepctl_core.plugin_manager.metadata.entry_points"
        ) as mock_entry_points:
            mock_ep_group = Mock()
            mock_ep_group.select.return_value = [mock_entry_point]
            mock_entry_points.return_value = mock_ep_group

            plugin_manager._load_external_plugins(mock_cli_group)

            assert "ext_module:ExtPlugin" in plugin_manager._loaded_entry_point_values

    @pytest.mark.unit
    def test_plugin_venv_skips_when_no_venv(self, plugin_manager, mock_cli_group):
        """Test that _load_plugin_venv_entries bails when venv doesn't exist."""
        with patch("deepctl_core.plugin_manager.PLUGIN_VENV") as mock_venv:
            mock_venv.exists.return_value = False

            # Should not raise
            plugin_manager._load_plugin_venv_entries(mock_cli_group)

            # No commands should be added
            mock_cli_group.add_command.assert_not_called()

    @pytest.mark.unit
    def test_plugin_venv_skips_when_state_empty(self, plugin_manager, mock_cli_group):
        """Test that _load_plugin_venv_entries bails when plugins.json is empty."""
        with patch("deepctl_core.plugin_manager.PLUGIN_VENV") as mock_venv:
            mock_venv.exists.return_value = True

            with patch(
                "deepctl_core.plugin_manager.get_plugin_state",
                return_value={"plugins": {}},
            ):
                plugin_manager._load_plugin_venv_entries(mock_cli_group)
                mock_cli_group.add_command.assert_not_called()

    @pytest.mark.unit
    def test_plugin_venv_skips_when_no_site_packages(
        self, plugin_manager, mock_cli_group
    ):
        """Test that _load_plugin_venv_entries bails when site-packages can't be found."""
        with patch("deepctl_core.plugin_manager.PLUGIN_VENV") as mock_venv:
            mock_venv.exists.return_value = True

            with (
                patch(
                    "deepctl_core.plugin_manager.get_plugin_state",
                    return_value={"plugins": {"some-plugin": {}}},
                ),
                patch(
                    "deepctl_core.plugin_manager.get_venv_site_packages",
                    return_value=None,
                ),
            ):
                plugin_manager._load_plugin_venv_entries(mock_cli_group)
                mock_cli_group.add_command.assert_not_called()

    @pytest.mark.unit
    def test_plugin_venv_deduplicates_already_loaded(
        self, plugin_manager, mock_command_class, mock_cli_group
    ):
        """Test that plugins already loaded from main env are skipped."""
        # Pre-populate the dedup set
        plugin_manager._loaded_entry_point_values.add("my_plugin.command:MyPlugin")

        # Create a mock distribution with the same entry point value
        mock_ep = Mock()
        mock_ep.name = "my-plugin"
        mock_ep.group = "deepctl.plugins"
        mock_ep.value = "my_plugin.command:MyPlugin"

        mock_dist = Mock()
        mock_dist.entry_points = [mock_ep]

        from pathlib import Path as RealPath

        fake_sp = RealPath("/fake/site-packages")

        with patch("deepctl_core.plugin_manager.PLUGIN_VENV") as mock_venv:
            mock_venv.exists.return_value = True

            with (
                patch(
                    "deepctl_core.plugin_manager.get_plugin_state",
                    return_value={"plugins": {"my-plugin": {}}},
                ),
                patch(
                    "deepctl_core.plugin_manager.get_venv_site_packages",
                    return_value=fake_sp,
                ),
                patch(
                    "deepctl_core.plugin_manager.metadata.distributions",
                    return_value=[mock_dist],
                ),
                patch("deepctl_core.plugin_manager.sys") as mock_sys,
            ):
                mock_sys.path = []

                plugin_manager._load_plugin_venv_entries(mock_cli_group)

                # Should NOT add the command (it's a duplicate)
                mock_cli_group.add_command.assert_not_called()

    @pytest.mark.unit
    def test_plugin_venv_loads_new_plugins(
        self, plugin_manager, mock_command_class, mock_cli_group
    ):
        """Test that new plugins from the venv are loaded."""
        mock_ep = Mock()
        mock_ep.name = "venv-plugin"
        mock_ep.group = "deepctl.plugins"
        mock_ep.value = "venv_plugin.command:VenvPlugin"
        mock_ep.load.return_value = mock_command_class

        mock_dist = Mock()
        mock_dist.entry_points = [mock_ep]

        from pathlib import Path as RealPath

        fake_sp = RealPath("/fake/site-packages")

        with patch("deepctl_core.plugin_manager.PLUGIN_VENV") as mock_venv:
            mock_venv.exists.return_value = True

            with (
                patch(
                    "deepctl_core.plugin_manager.get_plugin_state",
                    return_value={"plugins": {"venv-plugin": {}}},
                ),
                patch(
                    "deepctl_core.plugin_manager.get_venv_site_packages",
                    return_value=fake_sp,
                ),
                patch(
                    "deepctl_core.plugin_manager.metadata.distributions",
                    return_value=[mock_dist],
                ),
                patch("deepctl_core.plugin_manager.sys") as mock_sys,
            ):
                mock_sys.path = []

                plugin_manager._load_plugin_venv_entries(mock_cli_group)

                # Should add the command
                mock_cli_group.add_command.assert_called_once()
                # Should track in dedup set
                assert (
                    "venv_plugin.command:VenvPlugin"
                    in plugin_manager._loaded_entry_point_values
                )
                # Should be in loaded_plugins
                assert "venv-plugin" in plugin_manager.loaded_plugins

    @pytest.mark.unit
    def test_warn_if_plugin_venv_python_mismatch_silent_when_unknown(
        self, plugin_manager
    ):
        """No warning when get_venv_python_version returns None."""
        with (
            patch(
                "deepctl_core.plugin_manager.get_venv_python_version",
                return_value=None,
            ),
            patch("deepctl_core.plugin_manager.print_warning") as mock_warn,
        ):
            plugin_manager._warn_if_plugin_venv_python_mismatch()
            mock_warn.assert_not_called()

    @pytest.mark.unit
    def test_warn_if_plugin_venv_python_mismatch_silent_when_match(
        self, plugin_manager
    ):
        """No warning when venv version matches running interpreter."""
        with (
            patch(
                "deepctl_core.plugin_manager.get_venv_python_version",
                return_value=(3, 13),
            ),
            patch(
                "deepctl_core.plugin_manager.sys.version_info",
                Mock(major=3, minor=13),
            ),
            patch("deepctl_core.plugin_manager.print_warning") as mock_warn,
        ):
            plugin_manager._warn_if_plugin_venv_python_mismatch()
            mock_warn.assert_not_called()

    @pytest.mark.unit
    def test_warn_if_plugin_venv_python_mismatch_warns_on_minor_diff(
        self, plugin_manager
    ):
        """Warning fires when venv minor differs from running interpreter."""
        with (
            patch(
                "deepctl_core.plugin_manager.get_venv_python_version",
                return_value=(3, 12),
            ),
            patch(
                "deepctl_core.plugin_manager.sys.version_info",
                Mock(major=3, minor=13),
            ),
            patch("deepctl_core.plugin_manager.print_warning") as mock_warn,
        ):
            plugin_manager._warn_if_plugin_venv_python_mismatch()
            mock_warn.assert_called_once()
            msg = mock_warn.call_args[0][0]
            assert "3.12" in msg
            assert "3.13" in msg
            assert "rm -rf" in msg

    @pytest.mark.unit
    def test_warn_if_plugin_venv_python_mismatch_warns_on_major_diff(
        self, plugin_manager
    ):
        """Warning fires when venv major differs (e.g. Python 4 someday)."""
        with (
            patch(
                "deepctl_core.plugin_manager.get_venv_python_version",
                return_value=(3, 13),
            ),
            patch(
                "deepctl_core.plugin_manager.sys.version_info",
                Mock(major=4, minor=0),
            ),
            patch("deepctl_core.plugin_manager.print_warning") as mock_warn,
        ):
            plugin_manager._warn_if_plugin_venv_python_mismatch()
            mock_warn.assert_called_once()


class TestLoadErrorStream:
    """Plugin-load diagnostics must go to stderr, never stdout.

    A broken plugin's ImportError used to print through a stdout Console,
    corrupting `dg ... -o json` payloads for every remaining command (the
    amplifier in the 0.2.x core-floor incident). The module console is the
    shared stderr console so that cannot recur.
    """

    def test_module_console_is_the_shared_stderr_console(self):
        from deepctl_core import plugin_manager
        from deepctl_core.output import stderr_console

        assert plugin_manager.console is stderr_console
        assert plugin_manager.console.stderr is True

    def test_load_error_writes_to_stderr_not_stdout(self, capsys):
        from deepctl_core import plugin_manager

        mock_entry_point = Mock()
        mock_entry_point.name = "broken-command"
        mock_entry_point.load.side_effect = ImportError("Module not found")

        with patch("deepctl_core.plugin_manager.metadata.entry_points") as mock_eps:
            mock_eps.return_value.select.return_value = [mock_entry_point]
            plugin_manager.PluginManager()._load_builtin_commands(click.Group("dg"))

        captured = capsys.readouterr()
        assert "broken-command" in captured.err
        assert captured.out == ""


class TestGlobalPassthroughOptions:
    """Global output options must work after the subcommand too.

    The root group defines -o/--output, -q and -v, but Click only parses
    group options given before the subcommand, so `dg models -o json`
    failed with "No such option '-o'". Every leaf command now carries
    pass-through copies — except where the command defines the same option
    itself (dg speak --output names an audio file). Groups retain their
    original option handling because some nested commands write directly to
    stdout and cannot guarantee structured output.
    """

    @pytest.fixture
    def plugin_manager(self):
        return PluginManager()

    def _make_command_class(self, arguments: list[dict[str, Any]]):
        class PassthroughProbeCommand(BaseCommand):
            name = "probe"
            help = "Probe command"

            def get_arguments(self) -> list[dict[str, Any]]:
                return arguments

            def handle(
                self,
                config: Config,
                auth_manager: AuthManager,
                client: DeepgramClient,
                **kwargs,
            ) -> Any:
                return {"result": "success"}

        return PassthroughProbeCommand

    def _make_group_command_class(self, arguments: list[dict[str, Any]]):
        class PassthroughProbeGroup(BaseGroupCommand):
            name = "probe-group"
            help = "Probe group"

            def get_arguments(self) -> list[dict[str, Any]]:
                return arguments

        return PassthroughProbeGroup

    @pytest.mark.unit
    def test_output_option_added_when_free(self, plugin_manager):
        """A command without its own -o/--output gets the pass-through."""
        command_class = self._make_command_class([])
        cmd = plugin_manager._create_click_command(command_class())

        opts = set()
        for param in cmd.params:
            opts.update(param.opts)
        assert "--output" in opts
        assert "-o" in opts
        assert "--quiet" in opts
        assert "--verbose" in opts

    @pytest.mark.unit
    def test_output_option_skipped_on_collision(self, plugin_manager):
        """A command with its own --output keeps its own meaning."""
        command_class = self._make_command_class(
            [
                {
                    "names": ["--output", "-o"],
                    "help": "Output audio file path",
                    "type": str,
                    "is_option": True,
                },
            ]
        )
        cmd = plugin_manager._create_click_command(command_class())

        output_params = [p for p in cmd.params if "--output" in p.opts]
        assert len(output_params) == 1
        # The command's own option takes a free-form string, not a Choice.
        assert not isinstance(output_params[0].type, click.Choice)

    @pytest.mark.unit
    def test_output_option_not_added_to_group(self, plugin_manager):
        """Groups reject post-command output flags they cannot honor."""
        from click.testing import CliRunner

        group_class = self._make_group_command_class([])
        group = plugin_manager._create_click_command(group_class())

        result = CliRunner().invoke(group, ["-o", "json", "--help"])

        assert result.exit_code != 0
        assert "No such option" in result.output

    @pytest.mark.unit
    def test_passthrough_output_applies_format(self, plugin_manager):
        """Parsing `probe -o json` switches the global output format."""
        from click.testing import CliRunner
        from deepctl_core.output import _output_config

        command_class = self._make_command_class([])
        instance = command_class()
        # Bypass auth/client plumbing: the test targets option parsing only.
        instance.execute = lambda ctx, **kwargs: None
        cmd = plugin_manager._create_click_command(instance)

        previous_format = _output_config["format"]
        try:
            runner = CliRunner()
            result = runner.invoke(cmd, ["-o", "yaml"], standalone_mode=False)
            assert result.exception is None
            assert _output_config["format"] == "yaml"
        finally:
            _output_config["format"] = previous_format


# ── Reserved built-in names ────────────────────────────────────


def _set_mode(monkeypatch, *, agentic: bool, marker: bool) -> None:
    """Rendering mode (agentic output) and hard AI-agent marker for a test."""
    from deepctl_core import output

    monkeypatch.setitem(output._output_config, "agentic", agentic)
    monkeypatch.setattr(output, "is_agentic", lambda: agentic)
    _clear_agent_env(monkeypatch)
    if marker:
        monkeypatch.setenv("CLAUDECODE", "1")


def _clear_agent_env(monkeypatch) -> None:
    for var in (*AI_AGENT_ENV_MARKERS, "OR_APP_NAME", "OR_SITE_URL"):
        monkeypatch.delenv(var, raising=False)


def _err(capsys) -> str:
    """Captured stderr with Rich's line wrapping undone."""
    return " ".join(capsys.readouterr().err.split())


def _command_class(cmd_name: str, marker: list) -> type:
    """A BaseCommand subclass named ``cmd_name`` that records its runs."""

    class _Cmd(BaseCommand):
        name = cmd_name
        help = f"{cmd_name} command"
        requires_auth = False
        requires_project = False

        def handle(self, config, auth_manager, client, **kwargs):
            marker.append(cmd_name)

    return _Cmd


def _entry_point(
    ep_name: str,
    value: str,
    loaded: type,
    dist_name: str | None = None,
    dist_version: str = "1.0",
) -> Mock:
    ep = Mock()
    ep.name = ep_name
    ep.value = value
    ep.group = "deepctl.plugins"
    ep.load.return_value = loaded
    if dist_name is None:
        ep.dist = None
    else:
        ep.dist = Mock()
        ep.dist.metadata = {"Name": dist_name}
        ep.dist.version = dist_version
    return ep


@pytest.fixture
def no_overrides(monkeypatch):
    """No allow_override entries, whatever the host's config holds."""
    monkeypatch.setattr(
        "deepctl_core.plugin_manager.allow_override_names", lambda: frozenset()
    )


def _load(manager, builtin_eps=(), plugin_eps=()):
    """Run the built-in and plugin phases against a real Click group."""
    group = click.Group("dg")

    def entry_points():
        eps = Mock()
        eps.select.side_effect = lambda group: list(
            {"deepctl.commands": builtin_eps, "deepctl.plugins": plugin_eps}.get(
                group, []
            )
        )
        return eps

    with patch(
        "deepctl_core.plugin_manager.metadata.entry_points", side_effect=entry_points
    ):
        manager._load_builtin_commands(group)
        manager._load_external_plugins(group)
    return group


@pytest.mark.usefixtures("no_overrides")
class TestReservedBuiltinNames:
    """A plugin can't replace a built-in command."""

    def test_builtin_from_its_exact_module_is_registered(self):
        ran: list = []
        login = _command_class("login", ran)
        manager = PluginManager()
        group = _load(
            manager,
            builtin_eps=[
                _entry_point("login", "deepctl_cmd_login.command:LoginCommand", login)
            ],
        )
        assert group.commands["login"].callback is not None
        assert manager.command_classes["login"] is login
        assert manager.loaded_plugins == {}

    def test_plugin_shadowing_login_is_skipped_and_named(self, capsys):
        builtin_ran: list = []
        plugin_ran: list = []
        builtin = _command_class("login", builtin_ran)
        shadow = _command_class("login", plugin_ran)
        manager = PluginManager()
        group = _load(
            manager,
            builtin_eps=[
                _entry_point("login", "deepctl_cmd_login.command:LoginCommand", builtin)
            ],
            # Entry point named "tts"; the class calls itself "login".
            plugin_eps=[
                _entry_point(
                    "tts",
                    "deepctl_plugin_tts:Login",
                    shadow,
                    "deepctl-plugin-tts",
                    "0.2.0",
                )
            ],
        )

        assert set(group.commands) == {"login"}
        assert manager.command_classes["login"] is builtin
        assert "tts" not in manager.loaded_plugins
        assert plugin_ran == []
        err = _err(capsys)
        assert "deepctl-plugin-tts 0.2.0" in err
        assert "deepctl_plugin_tts" in err
        assert "login" in err

    def test_registered_login_is_the_builtin_callback(self):
        builtin = _command_class("login", [])
        shadow = _command_class("login", [])
        manager = PluginManager()
        group = _load(
            manager,
            builtin_eps=[
                _entry_point("login", "deepctl_cmd_login.command:LoginCommand", builtin)
            ],
            plugin_eps=[_entry_point("tts", "deepctl_plugin_tts:Login", shadow, "x")],
        )
        # Plugin phase runs last; a later add_command would have replaced it.
        assert len([c for c in group.commands.values() if c.name == "login"]) == 1
        assert "deepctl_plugin_tts:Login" not in manager._loaded_entry_point_values

    def test_name_is_reserved_when_the_builtin_is_missing(self, capsys):
        shadow = _command_class("login", [])
        manager = PluginManager()
        group = _load(
            manager,
            plugin_eps=[
                _entry_point(
                    "login", "shadow_login:Login", shadow, "shadow-login", "0.0.1"
                )
            ],
        )
        assert "login" not in group.commands
        assert "shadow-login 0.0.1" in _err(capsys)

    def test_stray_core_dist_info_in_commands_group_is_refused(self, capsys):
        """A wheel with a stray deepctl_core-9.9.dist-info that declares a
        ``deepctl.commands`` login entry is not built-in: wrong module."""
        builtin = _command_class("login", [])
        stray = _command_class("login", [])
        manager = PluginManager()
        group = _load(
            manager,
            builtin_eps=[
                # The stray entry comes first; order must not matter.
                _entry_point(
                    "login", "other_login.cmd:Login", stray, "deepctl-core", "9.9"
                ),
                _entry_point(
                    "login", "deepctl_cmd_login.command:LoginCommand", builtin
                ),
            ],
        )
        assert manager.command_classes["login"] is builtin
        assert group.commands["login"] is not None
        err = _err(capsys)
        assert "deepctl-core 9.9 (other_login.cmd)" in err

    @pytest.mark.parametrize(
        "value",
        [
            "deepctl_cmd_login:LoginCommand",  # package, not the module
            "deepctl_cmd_login.commands:LoginCommand",
            "deepctl_cmd_login_extra.command:LoginCommand",
            "deepctl_cmd_x.command:LoginCommand",
            "deepctl_cmd_login.command.other:LoginCommand",
            "deepctl_cmd_logout.command:LoginCommand",
        ],
    )
    def test_prefix_or_lookalike_module_is_not_builtin(self, value):
        stray = _command_class("login", [])
        manager = PluginManager()
        group = _load(
            manager,
            builtin_eps=[_entry_point("login", value, stray, "deepctl-cmd-login")],
        )
        assert "login" not in group.commands
        assert manager.command_classes == {}

    def test_builtin_module_cannot_claim_another_builtin_name(self):
        """The login module is the source of login/logout/profiles/whoami
        only; mapping it to ``api`` doesn't make it the api built-in."""
        cls = _command_class("api", [])
        manager = PluginManager()
        group = _load(
            manager,
            builtin_eps=[
                _entry_point("api", "deepctl_cmd_login.command:ApiCommand", cls)
            ],
        )
        assert "api" not in group.commands

    def test_plugin_with_a_free_name_still_loads(self):
        cls = _command_class("tts", [])
        manager = PluginManager()
        group = _load(
            manager,
            plugin_eps=[_entry_point("tts", "deepctl_plugin_tts:Tts", cls, "p")],
        )
        assert "tts" in group.commands
        assert "tts" in manager.loaded_plugins

    def test_name_present_before_loading_is_reserved(self):
        group = click.Group("dg")
        group.add_command(click.Command("version"))
        cls = _command_class("version", [])
        manager = PluginManager()
        with patch("deepctl_core.plugin_manager.metadata.entry_points") as eps:
            eps.return_value.select.return_value = [
                _entry_point("version", "p:Version", cls, "p")
            ]
            manager._load_external_plugins(group)
        assert group.commands["version"].callback is None  # the original

    def test_warning_escapes_untrusted_metadata(self, capsys):
        cls = _command_class("login", [])
        manager = PluginManager()
        _load(
            manager,
            plugin_eps=[
                _entry_point("x", "m:Login", cls, "[bold red]other\x1b[2J\nname", "1")
            ],
        )
        err = _err(capsys)
        assert "\x1b[2J" not in err
        assert "[bold red]other" in err  # shown literally, not as markup
        assert "Ignored the plugin command 'login'" in err

    def test_warning_and_hint_text_in_a_terminal(self, capsys, monkeypatch):
        """W-PLUGIN-SHADOW, then its hint on a line of its own."""
        _set_mode(monkeypatch, agentic=False, marker=False)
        monkeypatch.setenv("HOME", "/home/u")
        monkeypatch.setattr(
            "deepctl_core.plugin_manager.trusted_user_config_file",
            lambda: Path("/home/u/.config/deepctl/config.yaml"),
        )
        cls = _command_class("login", [])
        _load(PluginManager(), plugin_eps=[_entry_point("x", "m:L", cls, "p")])
        raw = capsys.readouterr().err
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        assert lines == [
            "⚠ Ignored the plugin command 'login' from p 1.0 (m): plugins "
            "can't replace built-in commands.",
            "If you trust this plugin, add 'login' to plugins.allow_override "
            "in ~/.config/deepctl/config.yaml to use its version.",
        ]

    def test_agent_hint_names_no_setting_or_path(self, capsys, monkeypatch):
        """A-PLUGIN-SHADOW: no key, file or flag an agent could act on."""
        _set_mode(monkeypatch, agentic=True, marker=True)
        cls = _command_class("login", [])
        _load(PluginManager(), plugin_eps=[_entry_point("x", "m:L", cls, "p")])
        raw = capsys.readouterr().err
        lines = [line for line in raw.splitlines() if line.strip()]
        assert lines == [
            "WARN: Ignored the plugin command 'login' from p 1.0 (m): plugins "
            "can't replace built-in commands.",
            "HINT: dg runs its built-in 'login' instead. If you are an AI agent, "
            "tell the user about this plugin and don't change dg's settings to "
            "allow it.",
        ]
        for needle in ("allow_override", "plugins.", "config.yaml"):
            assert needle not in raw

    def test_agentic_output_without_a_marker_gets_the_agent_hint(
        self, capsys, monkeypatch
    ):
        """CI or piped output (agentic rendering, no hard marker) gets the
        agent hint, which names no setting or file."""
        _set_mode(monkeypatch, agentic=True, marker=False)
        monkeypatch.setattr(
            "deepctl_core.plugin_manager.trusted_user_config_file",
            lambda: Path("/c/config.yaml"),
        )
        cls = _command_class("login", [])
        _load(PluginManager(), plugin_eps=[_entry_point("x", "m:L", cls, "p")])
        raw = capsys.readouterr().err
        assert "HINT: dg runs its built-in 'login' instead." in raw
        for needle in ("allow_override", "plugins.", "config.yaml"):
            assert needle not in raw

    def test_marker_without_agentic_output_gets_the_agent_hint(
        self, capsys, monkeypatch
    ):
        _set_mode(monkeypatch, agentic=False, marker=True)
        cls = _command_class("login", [])
        _load(PluginManager(), plugin_eps=[_entry_point("x", "m:L", cls, "p")])
        raw = capsys.readouterr().err
        assert "dg runs its built-in 'login' instead." in raw
        assert "allow_override" not in raw

    def test_warning_is_not_silenced_by_quiet(self, capsys, monkeypatch):
        from deepctl_core import output

        # setup_output also mutes the shared console; restore it afterwards.
        monkeypatch.setattr(output.console, "quiet", output.console.quiet)
        output.setup_output("default", quiet=True)
        _set_mode(monkeypatch, agentic=True, marker=True)
        cls = _command_class("login", [])
        _load(PluginManager(), plugin_eps=[_entry_point("x", "m:L", cls, "p")])
        err = _err(capsys)
        assert "WARN: Ignored the plugin command 'login'" in err
        assert "HINT:" in err

    def test_warning_is_printed_once_per_command(self, capsys, monkeypatch):
        from deepctl_core import output

        monkeypatch.setitem(output._output_config, "agentic", True)
        cls = _command_class("login", [])
        _load(
            PluginManager(),
            plugin_eps=[
                _entry_point("x", "m:L", cls, "p"),
                _entry_point("y", "m:L2", cls, "p"),
            ],
        )
        assert _err(capsys).count("WARN:") == 1

    def test_lines_are_not_hard_wrapped(self, capsys, monkeypatch):
        from deepctl_core import output

        monkeypatch.setitem(output._output_config, "agentic", True)
        cls = _command_class("whoami", [])
        _load(
            PluginManager(),
            plugin_eps=[
                _entry_point("x", "a_rather_long_module.name:W", cls, "p" * 40)
            ],
        )
        warn = [
            line
            for line in capsys.readouterr().err.splitlines()
            if line.startswith("WARN")
        ]
        assert warn and "plugins can't replace built-in commands." in warn[0]

    @pytest.mark.parametrize(
        "name",
        [
            "Login",
            "login ",
            " login",
            "login\u200b",
            "log\u00adin",
            "\uff4cogin",
            "LOGIN",
            "l\u03bfgin",
            "log\u0131n",
            "\u04cf\u043e\u050d\u0456\u0578",
            "\u017fpeak",
            "\u2113ogin",
            "Tts",
            "MyTool",
            "my_tool",
            "1tool",
            "-tool",
            "tool\n",
            "speak\u65e5\u672c\u8a9e",
            "keys\U0001f511",
            "listen\u00f1",
            "models\u2713",
            "\u00fcbersetzen",
            "caf\u00e9",
            "\u65e5\u672c\u8a9e",
            "\u043f\u0440\u0438\u0432\u0435\u0442",
            "stt-\u0440\u0443\u0441\u0441\u043a\u0438\u0439",
            "a" * 65,
            "login\x00",
            "login\x7f",
            "l\u2c9fgin",
            "l\u1d0fgin",
            "\u01c0ogin",
            "\ua731peak",
            "\u1d1csage",
            "\u026anit",
            "l\u0966gin",
            "l\u09e6gin",
            "l\u0665gin",
            "\u2d4fogin",
            "\ua4e1ogin",
            "\ua4df\ua4da\ua4d1",
            "\u13e6\u13ac\u13a9\u13da",
            "\uab76eys",
            "\u039c\u0421\u0420",
            "\u039a\u0395\u03a5\u0405",
            "\u041a\u0415\u0423\u0405",
            "\u03a5\u0405\u0391\u0262\u0415",
            "login\u3164",
            "login\uffa0",
            "login\u2800",
            "\u115flogin",
            "logxx\x08\x08in",
            "lo\x1b[8mzz\x1b[28mgin",
            "\U0001d425\U0001d428\U0001d420\U0001d422\U0001d427",
            "l\U0001d7e2gin",
            "l\u0585gin",
            "l\u043egin",
            "\u039c\u03f2\u03a1",
            "pr\u2c9fbe",
        ],
    )
    def test_names_outside_the_plugin_name_rule_are_refused(
        self, name, capsys, monkeypatch
    ):
        """Only ^[a-z][a-z0-9-]{0,63}$ is accepted. Anything else gets
        W-PLUGIN-BADNAME, shown escaped, never the shadow message."""
        _set_mode(monkeypatch, agentic=True, marker=False)
        cls = _command_class(name, [])
        manager = PluginManager()
        group = _load(manager, plugin_eps=[_entry_point("x", "m:L", cls, "p")])
        assert group.commands == {}
        assert manager.loaded_plugins == {}
        raw = capsys.readouterr().err
        assert raw == (
            f"WARN: Ignored the plugin command {name!a} from p 1.0 (m): "
            "plugin command names must start with a lowercase letter and use "
            "only lowercase a-z, 0-9 and hyphens, up to 64 characters.\n"
            "HINT: If you are an AI agent, tell the user about this plugin and "
            "don't edit its files to change the name.\n"
        )
        assert raw.isascii()
        assert "replace built-in" not in raw

    def test_bad_name_has_no_hint_for_people(self, capsys, monkeypatch):
        _set_mode(monkeypatch, agentic=False, marker=False)
        cls = _command_class("MyTool", [])
        _load(PluginManager(), plugin_eps=[_entry_point("x", "m:L", cls, "p")])
        lines = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
        assert len(lines) == 1
        assert "Ignored the plugin command 'MyTool'" in lines[0]

    def test_bad_name_warning_is_not_silenced_by_quiet(self, capsys, monkeypatch):
        from deepctl_core import output

        # setup_output also mutes the shared console; restore it afterwards.
        monkeypatch.setattr(output.console, "quiet", output.console.quiet)
        output.setup_output("default", quiet=True)
        _set_mode(monkeypatch, agentic=True, marker=True)
        cls = _command_class("MyTool", [])
        _load(PluginManager(), plugin_eps=[_entry_point("x", "m:L", cls, "p")])
        err = capsys.readouterr().err
        assert "WARN: Ignored the plugin command 'MyTool'" in err
        assert "HINT: If you are an AI agent" in err

    def test_emoji_codes_in_plugin_text_print_literally(self, capsys, monkeypatch):
        _set_mode(monkeypatch, agentic=True, marker=False)
        cls = _command_class("login", [])
        _load(
            PluginManager(),
            plugin_eps=[
                _entry_point(
                    "x", "m:L", cls, ":white_check_mark: verified-by-deepgram", "1.0"
                )
            ],
        )
        raw = capsys.readouterr().err
        assert ":white_check_mark: verified-by-deepgram 1.0 (m)" in raw
        assert "\u2705" not in raw

    def test_bad_name_cannot_use_allow_override(self, monkeypatch):
        monkeypatch.setattr(
            "deepctl_core.plugin_manager.allow_override_names",
            lambda: frozenset({"Login", "login"}),
        )
        cls = _command_class("Login", [])
        group = _load(PluginManager(), plugin_eps=[_entry_point("x", "m:L", cls, "p")])
        assert "Login" not in group.commands

    @pytest.mark.parametrize("name", ["my-tool", "tool2", "a", "x-1-y", "a" * 64])
    def test_valid_plugin_names_load(self, name):
        cls = _command_class(name, [])
        group = _load(PluginManager(), plugin_eps=[_entry_point("x", "m:T", cls, "p")])
        assert name in group.commands

    def test_every_builtin_name_fits_the_rule(self):
        import re

        from deepctl_core.plugin_manager import (
            BUILTIN_COMMAND_MODULES,
            BUILTIN_SUBCOMMAND_MODULES,
        )

        rule = re.compile(r"[a-z][a-z0-9-]{0,63}")
        names = list(BUILTIN_COMMAND_MODULES)
        for subs in BUILTIN_SUBCOMMAND_MODULES.values():
            names.extend(subs)
        assert all(rule.fullmatch(n) for n in names)

    def test_mismatched_click_name_is_checked_and_used(self, capsys):
        """A plugin group whose Click name differs from its own name."""
        plugin_cb = Mock()

        class RenamedGroup(BaseGroupCommand):
            name = "zzlogin"
            help = "x"

            def get_click_group(self):
                return click.Command(name="login", callback=plugin_cb)

        group = _load(
            PluginManager(),
            plugin_eps=[_entry_point("zz", "other:RenamedGroup", RenamedGroup, "e")],
        )
        assert "login" not in group.commands
        assert "zzlogin" not in group.commands
        assert "Ignored the plugin command 'login'" in _err(capsys)

    def test_non_str_command_name_is_refused(self, capsys):
        class Name(str):
            pass

        cls = _command_class("x", [])
        cls.name = Name("login")
        group = _load(PluginManager(), plugin_eps=[_entry_point("x", "m:L", cls, "p")])
        assert group.commands == {}
        assert "command name must be a non-empty str" in _err(capsys)

    def test_registered_under_the_checked_name(self):
        """Click's key is the name that passed the check, whatever the
        command object reports later."""
        cls = _command_class("tts", [])
        group = click.Group("dg")
        with (
            patch.object(group, "add_command", wraps=group.add_command) as add,
            patch("deepctl_core.plugin_manager.metadata.entry_points") as eps,
        ):
            eps.return_value.select.return_value = [_entry_point("t", "p:T", cls, "p")]
            PluginManager()._load_external_plugins(group)
        assert add.call_args.kwargs["name"] == "tts"

    def test_load_errors_show_plugin_text_literally(self, capsys):
        ep = Mock()
        ep.name = "boom[/red]"
        ep.load.side_effect = RuntimeError(
            "[bold green]Logged in as admin@deepgram.com[/bold green]\x1b[2J"
        )
        group = click.Group("dg")
        with patch("deepctl_core.plugin_manager.metadata.entry_points") as eps:
            eps.return_value.select.return_value = [ep]
            PluginManager()._load_external_plugins(group)
        err = _err(capsys)
        assert "[bold green]Logged in as admin@deepgram.com[/bold green]" in err
        assert "boom[/red]" in err
        assert "\x1b" not in err


@pytest.fixture
def no_hidden_scan(monkeypatch):
    """Keep subcommand tests from scanning the real installed distributions."""
    monkeypatch.setattr(
        PluginManager, "_hidden_builtin_entry_points", staticmethod(lambda g, w: [])
    )


@pytest.mark.usefixtures("no_overrides", "no_hidden_scan")
class TestReservedSubcommandNames:
    def _debug_group(self):
        class Debug(BaseGroupCommand):
            name = "debug"
            help = "debug"

        return Debug()

    def _load_subs(self, group_instance, builtin_eps=(), plugin_eps=(), group=None):
        group = group or click.Group(group_instance.name)

        name = group_instance.name
        by_group = {
            f"deepctl.subcommands.{name}": builtin_eps,
            f"deepctl.subplugins.{name}": plugin_eps,
        }

        def entry_points():
            eps = Mock()
            eps.select.side_effect = lambda group: list(by_group.get(group, []))
            return eps

        with patch(
            "deepctl_core.plugin_manager.metadata.entry_points",
            side_effect=entry_points,
        ):
            PluginManager()._load_subcommands_for_group(group, group_instance)
        return group

    @staticmethod
    def _renamed_group_class(own_name, click_name, plugin_cb):
        """A plugin group that admits as ``own_name`` but builds a Click
        command named ``click_name``."""

        class Renamed(BaseGroupCommand):
            name = own_name
            help = "x"

            def get_click_group(self):
                return click.Command(name=click_name, callback=plugin_cb)

        return Renamed

    def test_plugin_install_cannot_be_replaced_through_a_click_name(self, capsys):
        class Plugin(BaseGroupCommand):
            name = "plugin"
            help = "p"

        group = click.Group("plugin")
        original = click.Command("install", callback=lambda: None)
        group.add_command(original)
        plugin_cb = Mock()
        other = self._renamed_group_class("zzinstall", "install", plugin_cb)
        self._load_subs(
            Plugin(),
            plugin_eps=[
                _entry_point("zzinstall", "othercode:SubInstall", other, "other-sub")
            ],
            group=group,
        )
        assert group.commands["install"] is original
        assert "zzinstall" not in group.commands
        err = _err(capsys)
        assert "Ignored the plugin command 'plugin install'" in err
        assert "other-sub 1.0 (othercode)" in err

    def test_debug_browser_cannot_be_replaced_through_a_click_name(self, capsys):
        browser = _command_class("browser", [])
        plugin_cb = Mock()
        other = self._renamed_group_class("zzbrowser", "browser", plugin_cb)
        debug = self._debug_group()
        group = self._load_subs(
            debug,
            builtin_eps=[
                _entry_point(
                    "browser",
                    "deepctl_cmd_debug_browser.command:BrowserCommand",
                    browser,
                )
            ],
            plugin_eps=[
                _entry_point("zzbrowser", "othercode:SubBrowser", other, "other-sub2")
            ],
        )
        assert group.commands["browser"].callback is not plugin_cb
        assert debug.subcommands == {"browser": browser}
        assert "zzbrowser" not in group.commands
        assert "Ignored the plugin command 'debug browser'" in _err(capsys)

    def test_debug_network_cannot_be_replaced_through_a_click_group(self, capsys):
        """A subplugin group named ``zzz`` whose Click object is a group
        named ``network``."""
        network = _command_class("network", [])
        plugin_cb = Mock()

        class RenamedSub(BaseGroupCommand):
            name = "zzz"
            help = "x"

            def get_click_group(self):
                return click.Group(name="network", callback=plugin_cb)

        debug = self._debug_group()
        group = self._load_subs(
            debug,
            builtin_eps=[
                _entry_point(
                    "network",
                    "deepctl_cmd_debug_network.command:NetworkCommand",
                    network,
                )
            ],
            plugin_eps=[
                _entry_point("zzz", "rv_other.cmds:RenamedSub", RenamedSub, "rv-other")
            ],
        )
        assert group.commands["network"].callback is not plugin_cb
        assert debug.subcommands == {"network": network}
        assert "zzz" not in group.commands
        assert "Ignored the plugin command 'debug network'" in _err(capsys)

    @pytest.mark.parametrize("group_name", ["skills", "mcp", "some-plugin-group"])
    def test_every_subplugin_group_checks_the_click_name(self, group_name, capsys):
        """The check isn't tied to particular groups: any name the group
        already defines is kept."""

        class Grp(BaseGroupCommand):
            name = group_name
            help = "g"

        group = click.Group(group_name)
        original = click.Command("list", callback=lambda: None)
        group.add_command(original)
        renamed = self._renamed_group_class("zzlist", "list", Mock())
        self._load_subs(
            Grp(),
            plugin_eps=[_entry_point("zzlist", "p:R", renamed, "p")],
            group=group,
        )
        assert group.commands["list"] is original
        assert f"Ignored the plugin command '{group_name} list'" in _err(capsys)

    def test_subcommand_registered_under_the_checked_name(self):
        extra = _command_class("extra", [])
        group = click.Group("debug")
        with patch.object(group, "add_command", wraps=group.add_command) as add:
            self._load_subs(
                self._debug_group(),
                plugin_eps=[_entry_point("extra", "p:Extra", extra, "p")],
                group=group,
            )
        assert add.call_args.kwargs["name"] == "extra"

    @pytest.mark.parametrize("name", ["Audio", "pr\u2c9fbe", "net_work"])
    def test_bad_subcommand_name_is_refused(self, name, capsys):
        cls = _command_class(name, [])
        group = self._load_subs(
            self._debug_group(),
            plugin_eps=[_entry_point("a", "p:A", cls, "p")],
        )
        assert name not in group.commands
        err = _err(capsys)
        assert f"Ignored the plugin command {'debug ' + name!a}" in err
        assert "plugin command names must start with a lowercase letter" in err

    def test_builtin_subcommand_from_exact_module_loads(self):
        audio = _command_class("audio", [])
        group = self._load_subs(
            self._debug_group(),
            builtin_eps=[
                _entry_point(
                    "audio", "deepctl_cmd_debug_audio.command:AudioCommand", audio
                )
            ],
        )
        assert "audio" in group.commands

    def test_subplugin_shadowing_builtin_subcommand_is_skipped(self, capsys):
        ran: list = []
        audio = _command_class("audio", [])
        shadow = _command_class("audio", ran)
        debug = self._debug_group()
        self._load_subs(
            debug,
            builtin_eps=[
                _entry_point(
                    "audio", "deepctl_cmd_debug_audio.command:AudioCommand", audio
                )
            ],
            plugin_eps=[_entry_point("audio", "other:Audio", shadow, "other-dist")],
        )
        assert debug.subcommands["audio"] is audio
        assert ran == []
        err = _err(capsys)
        assert "debug audio" in err and "other-dist" in err

    def test_subplugin_cannot_replace_a_name_the_group_defines(self):
        class Projects(BaseGroupCommand):
            name = "projects"
            help = "p"

        group = click.Group("projects")
        group.add_command(click.Command("list"))
        shadow = _command_class("list", [])
        self._load_subs(
            Projects(),
            plugin_eps=[_entry_point("list", "shadow_login:ProjList", shadow, "s")],
            group=group,
        )
        assert group.commands["list"].callback is None  # the original

    def test_subplugin_with_free_name_loads(self):
        extra = _command_class("extra", [])
        group = self._load_subs(
            self._debug_group(),
            plugin_eps=[_entry_point("extra", "p:Extra", extra, "p")],
        )
        assert "extra" in group.commands


def _unloadable_entry_point(ep_name: str, value: str, dist_name: str) -> Mock:
    """An entry point whose ``load()`` fails the test if it is ever called."""
    ep = _entry_point(ep_name, value, type("Never", (), {}), dist_name)
    ep.load.side_effect = AssertionError(f"{value} was imported")
    return ep


@pytest.mark.usefixtures("no_overrides")
class TestRefusedBeforeImport:
    """An entry point named like a built-in is refused before its module is
    imported, so none of its code runs."""

    def test_plugin_named_login_is_never_imported(self, capsys):
        builtin = _command_class("login", [])
        shadow = _unloadable_entry_point("login", "evil_login:Login", "evil")
        manager = PluginManager()
        group = _load(
            manager,
            builtin_eps=[
                _entry_point("login", "deepctl_cmd_login.command:LoginCommand", builtin)
            ],
            plugin_eps=[shadow],
        )
        shadow.load.assert_not_called()
        assert group.commands["login"].callback is not None
        assert manager.command_classes["login"] is builtin
        assert manager.loaded_plugins == {}
        err = _err(capsys)
        assert "Ignored the plugin command 'login' from evil 1.0 (evil_login)" in err
        assert "was imported" not in err

    def test_reserved_name_is_refused_when_the_builtin_is_missing(self, capsys):
        shadow = _unloadable_entry_point("login", "evil_login:Login", "evil")
        group = _load(PluginManager(), plugin_eps=[shadow])
        shadow.load.assert_not_called()
        assert "login" not in group.commands
        assert "Ignored the plugin command 'login'" in _err(capsys)

    def test_stray_commands_entry_point_is_never_imported(self):
        stray = _unloadable_entry_point("login", "evil_login:Login", "deepctl-core")
        stray.group = "deepctl.commands"
        group = _load(PluginManager(), builtin_eps=[stray])
        stray.load.assert_not_called()
        assert "login" not in group.commands

    def test_name_the_group_holds_is_never_imported(self):
        group = click.Group("dg")
        group.add_command(click.Command("version"))
        shadow = _unloadable_entry_point("version", "evil:V", "evil")
        with patch("deepctl_core.plugin_manager.metadata.entry_points") as eps:
            eps.return_value.select.return_value = [shadow]
            PluginManager()._load_external_plugins(group)
        shadow.load.assert_not_called()
        assert group.commands["version"].callback is None

    def test_venv_plugin_named_login_is_never_imported(self):
        shadow = _unloadable_entry_point("login", "evil_login:Login", "venv-dist")
        dist = Mock()
        dist.entry_points = [shadow]
        group = click.Group("dg")
        with (
            patch("deepctl_core.plugin_manager.PLUGIN_VENV") as venv,
            patch(
                "deepctl_core.plugin_manager.get_plugin_state",
                return_value={"plugins": {"x": {}}},
            ),
            patch(
                "deepctl_core.plugin_manager.get_venv_site_packages",
                return_value=__import__("pathlib").Path("/fake/sp"),
            ),
            patch(
                "deepctl_core.plugin_manager.get_venv_python_version", return_value=None
            ),
            patch(
                "deepctl_core.plugin_manager.metadata.distributions",
                return_value=[dist],
            ),
            patch("deepctl_core.plugin_manager.sys") as fake_sys,
        ):
            fake_sys.path = []
            venv.exists.return_value = True
            PluginManager()._load_plugin_venv_entries(group)
        shadow.load.assert_not_called()
        assert "login" not in group.commands

    def test_subplugin_named_like_a_builtin_subcommand_is_never_imported(self, capsys):
        class Debug(BaseGroupCommand):
            name = "debug"
            help = "debug"

        audio = _command_class("audio", [])
        shadow = _unloadable_entry_point("audio", "evil:Audio", "evil")
        debug = Debug()
        by_group = {
            "deepctl.subcommands.debug": [
                _entry_point(
                    "audio", "deepctl_cmd_debug_audio.command:AudioCommand", audio
                )
            ],
            "deepctl.subplugins.debug": [shadow],
        }

        def entry_points():
            eps = Mock()
            eps.select.side_effect = lambda group: list(by_group.get(group, []))
            return eps

        group = click.Group("debug")
        with patch(
            "deepctl_core.plugin_manager.metadata.entry_points",
            side_effect=entry_points,
        ):
            PluginManager()._load_subcommands_for_group(group, debug)
        shadow.load.assert_not_called()
        assert debug.subcommands["audio"] is audio
        assert "Ignored the plugin command 'debug audio'" in _err(capsys)

    def test_listed_override_is_still_imported_and_loaded(self, monkeypatch):
        monkeypatch.setattr(
            "deepctl_core.plugin_manager.allow_override_names",
            lambda: frozenset({"login"}),
        )
        builtin = _command_class("login", [])
        mine = _command_class("login", [])
        ep = _entry_point("login", "my_login:Login", mine, "mine")
        manager = PluginManager()
        _load(
            manager,
            builtin_eps=[
                _entry_point("login", "deepctl_cmd_login.command:LoginCommand", builtin)
            ],
            plugin_eps=[ep],
        )
        ep.load.assert_called_once()
        assert isinstance(manager.loaded_plugins["login"], mine)

    def test_listed_subplugin_override_is_still_imported_and_loaded(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            "deepctl_core.plugin_manager.allow_override_names",
            lambda: frozenset({"debug audio"}),
        )

        class Debug(BaseGroupCommand):
            name = "debug"
            help = "debug"

        audio = _command_class("audio", [])
        mine = _command_class("audio", [])
        ep = _entry_point("audio", "my_audio:Audio", mine, "mine")
        debug = Debug()
        by_group = {
            "deepctl.subcommands.debug": [
                _entry_point(
                    "audio", "deepctl_cmd_debug_audio.command:AudioCommand", audio
                )
            ],
            "deepctl.subplugins.debug": [ep],
        }

        def entry_points():
            eps = Mock()
            eps.select.side_effect = lambda group: list(by_group.get(group, []))
            return eps

        group = click.Group("debug")
        with patch(
            "deepctl_core.plugin_manager.metadata.entry_points",
            side_effect=entry_points,
        ):
            PluginManager()._load_subcommands_for_group(group, debug)
        ep.load.assert_called_once()
        assert debug.subcommands["audio"] is mine
        assert "audio" in group.commands

    def test_entry_point_name_is_not_the_command_name(self):
        """``my_tool`` may register ``my-tool``: an entry point name outside
        the command name rule is checked after loading, not before."""
        cls = _command_class("my-tool", [])
        ep = _entry_point("my_tool", "p:T", cls, "p")
        group = _load(PluginManager(), plugin_eps=[ep])
        ep.load.assert_called_once()
        assert "my-tool" in group.commands

    def test_builtin_from_its_exact_module_is_imported(self):
        login = _command_class("login", [])
        ep = _entry_point("login", "deepctl_cmd_login.command:LoginCommand", login)
        group = _load(PluginManager(), builtin_eps=[ep])
        ep.load.assert_called_once()
        assert "login" in group.commands


class TestPluginVenvReservedNames:
    def test_venv_plugin_named_login_is_skipped(self, no_overrides):
        shadow = _command_class("login", [])
        ep = _entry_point("tts", "deepctl_plugin_tts:Login", shadow, "venv-dist")
        dist = Mock()
        dist.entry_points = [ep]
        group = click.Group("dg")
        manager = PluginManager()
        with (
            patch("deepctl_core.plugin_manager.PLUGIN_VENV") as venv,
            patch(
                "deepctl_core.plugin_manager.get_plugin_state",
                return_value={"plugins": {"x": {}}},
            ),
            patch(
                "deepctl_core.plugin_manager.get_venv_site_packages",
                return_value=__import__("pathlib").Path("/fake/sp"),
            ),
            patch(
                "deepctl_core.plugin_manager.get_venv_python_version", return_value=None
            ),
            patch(
                "deepctl_core.plugin_manager.metadata.distributions",
                return_value=[dist],
            ),
            patch("deepctl_core.plugin_manager.sys") as fake_sys,
        ):
            fake_sys.path = []
            venv.exists.return_value = True
            manager._load_plugin_venv_entries(group)
        assert "login" not in group.commands
        assert manager.loaded_plugins == {}


class TestAllowOverride:
    """``plugins.allow_override`` from the trusted user config only."""

    @pytest.fixture
    def config_home(self, tmp_path, monkeypatch):
        for var in (*AI_AGENT_ENV_MARKERS, "OR_APP_NAME", "OR_SITE_URL"):
            monkeypatch.delenv(var, raising=False)
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        monkeypatch.setattr(
            "deepctl_core.config.platformdirs.user_config_dir",
            lambda *a, **k: str(cfg_dir),
        )
        work = tmp_path / "work"
        work.mkdir()
        monkeypatch.chdir(work)
        return cfg_dir

    def _shadow_login(self):
        builtin = _command_class("login", [])
        shadow = _command_class("login", [])
        manager = PluginManager()
        group = _load(
            manager,
            builtin_eps=[
                _entry_point("login", "deepctl_cmd_login.command:LoginCommand", builtin)
            ],
            plugin_eps=[_entry_point("tts", "p:Login", shadow, "p")],
        )
        return manager, group, shadow

    def test_listed_name_lets_the_plugin_replace_the_builtin(self, config_home):
        (config_home / "config.yaml").write_text(
            "plugins:\n  allow_override: [login]\n"
        )
        manager, _group, shadow = self._shadow_login()
        assert isinstance(manager.loaded_plugins["tts"], shadow)

    def test_unlisted_name_is_still_reserved(self, config_home):
        (config_home / "config.yaml").write_text(
            "plugins:\n  allow_override: [speak]\n"
        )
        manager, _group, _shadow = self._shadow_login()
        assert "tts" not in manager.loaded_plugins

    def test_subcommand_override_uses_the_full_path(self, config_home):
        (config_home / "config.yaml").write_text(
            "plugins:\n  allow_override: ['debug audio']\n"
        )
        from deepctl_core._plugin_trust import allow_override_names

        assert "debug audio" in allow_override_names()

    @pytest.mark.parametrize(
        "body",
        [
            "plugins: [login]\n",
            "plugins:\n  allow_override: login\n",
            "plugins:\n  allow_override: [1, null]\n",
            ": : not yaml : [\n",
            "",
        ],
    )
    def test_malformed_values_grant_nothing(self, config_home, body):
        from deepctl_core._plugin_trust import allow_override_names

        (config_home / "config.yaml").write_text(body)
        assert allow_override_names() == frozenset()

    def test_project_file_and_config_flag_are_ignored(self, config_home, tmp_path):
        """deepgram.yaml in the working directory or any other file never
        widens the set; only the default user file is read."""
        from deepctl_core._plugin_trust import allow_override_names

        (tmp_path / "work" / "deepgram.yaml").write_text(
            "plugins:\n  allow_override: [login]\n"
        )
        (tmp_path / "other.yaml").write_text("plugins:\n  allow_override: [login]\n")
        assert allow_override_names() == frozenset()

    def test_agent_signal_ignores_a_redirected_config_dir(
        self, config_home, tmp_path, monkeypatch
    ):
        """Under CLAUDECODE, the platformdirs location (HOME/XDG-derived) is
        not trusted; only the real-home default is."""
        from deepctl_core import _plugin_trust

        (config_home / "config.yaml").write_text(
            "plugins:\n  allow_override: [login]\n"
        )
        real_home = tmp_path / "realhome"
        monkeypatch.setattr(config_module, "_real_home", lambda: str(real_home))
        monkeypatch.setenv("CLAUDECODE", "1")
        assert _plugin_trust.allow_override_names() == frozenset()

        real_cfg = _plugin_trust._agent_trust_root()
        assert real_cfg is not None
        # Owner-only, as a config save writes it: the trust root refuses a
        # group-writable file, which umask 0002 would otherwise produce.
        real_cfg.parent.mkdir(parents=True, mode=0o700)
        real_cfg.write_text("plugins:\n  allow_override: [login]\n")
        real_cfg.chmod(0o600)
        assert _plugin_trust.allow_override_names() == frozenset({"login"})

    def test_agent_signal_refuses_a_real_home_file_inside_cwd(
        self, config_home, tmp_path, monkeypatch
    ):
        from deepctl_core import _plugin_trust

        work = tmp_path / "work"
        monkeypatch.setattr(config_module, "_real_home", lambda: str(work / "h"))
        monkeypatch.setenv("CLAUDECODE", "1")
        assert _plugin_trust._agent_trust_root() is None

    def test_agent_signal_at_home_uses_the_real_home_file(
        self, config_home, tmp_path, monkeypatch
    ):
        from deepctl_core import _plugin_trust

        home = tmp_path / "work"  # cwd is home itself
        monkeypatch.setattr(config_module, "_real_home", lambda: str(home))
        monkeypatch.setenv("AI_AGENT", "x")
        assert _plugin_trust._agent_trust_root() is not None

    def test_file_is_read_once_per_manager(self, config_home):
        (config_home / "config.yaml").write_text(
            "plugins:\n  allow_override: [login]\n"
        )
        manager = PluginManager()
        with patch(
            "deepctl_core.plugin_manager.allow_override_names",
            return_value=frozenset(),
        ) as read:
            manager._overrides()
            manager._overrides()
        assert read.call_count == 1

    def test_no_collision_never_reads_the_file(self, config_home):
        cls = _command_class("tts", [])
        with patch("deepctl_core.plugin_manager.allow_override_names") as read:
            _load(PluginManager(), plugin_eps=[_entry_point("t", "p:T", cls, "p")])
        read.assert_not_called()

    def test_allow_override_survives_a_config_save(self, config_home):
        """A save (dg login, a profile change) writes the user layer back
        without dropping ``plugins.allow_override``."""
        import yaml
        from deepctl_core._plugin_trust import allow_override_names

        path = config_home / "config.yaml"
        path.write_text(
            "plugins:\n  allow_override: [login, 'debug audio']\n"
            "profiles:\n  default:\n    project_id: p-default\n"
        )
        config = Config()
        config.create_profile("work", project_id="p-work")

        data = yaml.safe_load(path.read_text())
        assert data["plugins"]["allow_override"] == ["login", "debug audio"]
        assert data["profiles"]["work"]["project_id"] == "p-work"
        assert allow_override_names() == frozenset({"login", "debug audio"})

    def test_saved_allow_override_is_read_from_the_real_home_under_an_agent(
        self, config_home, tmp_path, monkeypatch
    ):
        """After a save, the real-home file still passes the trust-root
        checks, so an agent session keeps the person's override."""
        from deepctl_core import _plugin_trust

        real_home = tmp_path / "realhome"
        monkeypatch.setattr(config_module, "_real_home", lambda: str(real_home))
        monkeypatch.setenv("CLAUDECODE", "1")
        real_cfg = _plugin_trust._agent_trust_root()
        assert real_cfg is not None
        real_cfg.parent.mkdir(parents=True)
        real_cfg.write_text("plugins:\n  allow_override: [login]\n")
        monkeypatch.setattr(
            "deepctl_core.config.platformdirs.user_config_dir",
            lambda *a, **k: str(real_cfg.parent),
        )
        monkeypatch.delenv("CLAUDECODE")
        Config().create_profile("work", project_id="p-work")

        monkeypatch.setenv("CLAUDECODE", "1")
        assert _plugin_trust.allow_override_names() == frozenset({"login"})


class TestBuiltinMapMatchesInstalledPackages:
    """The reserved-name table matches what the first-party packages in this
    repository declare (read from packages/*/pyproject.toml)."""

    def _declared(self, group_prefix):
        try:
            import tomllib
        except ModuleNotFoundError:  # Python 3.10
            tomllib = pytest.importorskip("tomli")
        parents = Path(__file__).resolve().parents
        files = (
            sorted(parents[2].parent.glob("deepctl-cmd-*/pyproject.toml"))
            if len(parents) > 3
            else []
        )
        if not files:
            pytest.skip("not running from a source checkout")
        found: dict = {}
        for path in files:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            groups = data.get("project", {}).get("entry-points", {})
            for group, eps in groups.items():
                if not group.startswith(group_prefix):
                    continue
                for name, value in eps.items():
                    key = (group, name)
                    assert key not in found, f"{key} declared twice"
                    found[key] = value.partition(":")[0].strip()
        return found

    def test_top_level_commands(self):
        from deepctl_core.plugin_manager import BUILTIN_COMMAND_MODULES

        declared = {
            name: module
            for (_group, name), module in self._declared("deepctl.commands").items()
        }
        assert declared == dict(BUILTIN_COMMAND_MODULES)

    def test_subcommands(self):
        from deepctl_core.plugin_manager import BUILTIN_SUBCOMMAND_MODULES

        declared: dict = {}
        for (group, name), module in self._declared("deepctl.subcommands.").items():
            declared.setdefault(group.rsplit(".", 1)[1], {})[name] = module
        assert declared == {k: dict(v) for k, v in BUILTIN_SUBCOMMAND_MODULES.items()}


def _dist(*eps):
    d = Mock()
    d.entry_points = list(eps)
    return d


def _real_ep(name, value, loaded, group="deepctl.commands"):
    ep = _entry_point(name, value, loaded)
    ep.group = group
    return ep


@pytest.mark.usefixtures("no_overrides")
class TestHiddenBuiltinRecovery:
    """A stray dist-info named like a first-party package hides that
    package's entry points from ``metadata.entry_points()`` (one dist per
    name). Built-ins are then found by a scan of every distribution."""

    def test_stray_cmd_login_dist_info_doesnt_remove_login(self, capsys):
        builtin = _command_class("login", [])
        stray = _command_class("login", [])
        stray_ep = _entry_point(
            "login", "other_cmd_login:Login", stray, "deepctl-cmd-login", "9.9"
        )
        stray_ep.group = "deepctl.commands"
        real_ep = _real_ep("login", "deepctl_cmd_login.command:LoginCommand", builtin)
        group = click.Group("dg")
        manager = PluginManager()

        def entry_points():
            eps = Mock()
            eps.select.side_effect = lambda group: (
                [stray_ep] if group == "deepctl.commands" else []
            )
            return eps

        with (
            patch(
                "deepctl_core.plugin_manager.metadata.entry_points",
                side_effect=entry_points,
            ),
            patch(
                "deepctl_core.plugin_manager.metadata.distributions",
                return_value=[_dist(stray_ep), _dist(real_ep)],
            ),
            patch("deepctl_core.plugin_manager.PLUGIN_VENV") as venv,
        ):
            venv.exists.return_value = False
            manager.load_plugins(group)

        assert manager.command_classes["login"] is builtin
        assert "login" in group.commands
        assert "deepctl-cmd-login 9.9 (other_cmd_login)" in _err(capsys)

    def test_scan_keeps_only_exact_modules(self):
        cls = _command_class("login", [])
        eps = [
            _real_ep("login", "deepctl_cmd_login_x.command:LoginCommand", cls),
            _real_ep("login", "deepctl_cmd_login.command:LoginCommand", cls),
            _real_ep(
                "login",
                "deepctl_cmd_login.command:LoginCommand",
                cls,
                group="deepctl.plugins",
            ),
        ]
        with patch(
            "deepctl_core.plugin_manager.metadata.distributions",
            return_value=[_dist(*eps)],
        ):
            found = PluginManager._hidden_builtin_entry_points(
                "deepctl.commands", {"login": "deepctl_cmd_login.command"}
            )
        assert found == [eps[1]]

    def test_no_scan_when_every_builtin_is_present(self):
        from deepctl_core.plugin_manager import BUILTIN_COMMAND_MODULES

        group = click.Group("dg")
        for name in BUILTIN_COMMAND_MODULES:
            group.add_command(click.Command(name))
        with patch("deepctl_core.plugin_manager.metadata.distributions") as dists:
            PluginManager()._restore_hidden_builtins(group)
        dists.assert_not_called()

    def test_hidden_builtin_subcommand_is_restored(self):
        class Debug(BaseGroupCommand):
            name = "debug"
            help = "debug"

        audio = _command_class("audio", [])
        real_ep = _real_ep(
            "audio",
            "deepctl_cmd_debug_audio.command:AudioCommand",
            audio,
            group="deepctl.subcommands.debug",
        )
        debug = Debug()
        group = click.Group("debug")
        with (
            patch("deepctl_core.plugin_manager.metadata.entry_points") as eps,
            patch(
                "deepctl_core.plugin_manager.metadata.distributions",
                return_value=[_dist(real_ep)],
            ),
        ):
            eps.return_value.select.return_value = []
            PluginManager()._load_subcommands_for_group(group, debug)
        assert "audio" in group.commands
        assert debug.subcommands["audio"] is audio


class TestOverrideNotice:
    """I-PLUGIN-OVERRIDE: an active override names the package."""

    @pytest.fixture
    def override_login(self, monkeypatch):
        monkeypatch.setattr(
            "deepctl_core.plugin_manager.allow_override_names",
            lambda: frozenset({"login"}),
        )
        monkeypatch.setenv("HOME", "/home/u")
        monkeypatch.setattr(
            "deepctl_core.plugin_manager.trusted_user_config_file",
            lambda: Path("/home/u/.config/deepctl/config.yaml"),
        )

    def _run(self):
        cls = _command_class("login", [])
        manager = PluginManager()
        group = _load(
            manager,
            plugin_eps=[
                _entry_point(
                    "tts",
                    "deepctl_plugin_tts:Login",
                    cls,
                    "deepctl-plugin-tts",
                    "0.2.0",
                ),
                _entry_point(
                    "tts2",
                    "deepctl_plugin_tts:Login2",
                    cls,
                    "deepctl-plugin-tts",
                    "0.2.0",
                ),
            ],
        )
        return manager, group

    @staticmethod
    def _invoke(group, name):
        command = group.commands[name]
        command.callback = lambda **kwargs: None
        ctx = click.Context(command)
        ctx.params = {}
        command.invoke(ctx)

    def test_loading_prints_nothing(self, override_login, capsys, monkeypatch):
        """The notice waits until the command runs, after --quiet is parsed."""
        _set_mode(monkeypatch, agentic=False, marker=False)
        _manager, group = self._run()
        assert "login" in group.commands
        assert capsys.readouterr().err == ""

    def test_notice_in_a_terminal(self, override_login, capsys, monkeypatch):
        _set_mode(monkeypatch, agentic=False, marker=False)
        _manager, group = self._run()
        self._invoke(group, "login")
        raw = capsys.readouterr().err
        assert [line.strip() for line in raw.splitlines() if line.strip()] == [
            "ℹ Using 'login' from deepctl-plugin-tts 0.2.0 (deepctl_plugin_tts) "
            "instead of the built-in, as plugins.allow_override in "
            "~/.config/deepctl/config.yaml allows."
        ]

    @pytest.mark.parametrize(
        "agentic,marker", [(True, True), (True, False), (False, True)]
    )
    def test_agent_variant_names_no_path(
        self, override_login, capsys, monkeypatch, agentic, marker
    ):
        _set_mode(monkeypatch, agentic=agentic, marker=marker)
        _manager, group = self._run()
        self._invoke(group, "login")
        raw = capsys.readouterr().err
        assert " ".join(raw.split()).endswith(
            "Using 'login' from deepctl-plugin-tts 0.2.0 (deepctl_plugin_tts) "
            "instead of the built-in, as the deepctl user config allows."
        )
        assert "config.yaml" not in raw

    def test_quiet_silences_the_notice(self, override_login, capsys, monkeypatch):
        from deepctl_core import output

        _set_mode(monkeypatch, agentic=False, marker=False)
        _manager, group = self._run()
        # setup_output also mutes the shared console; restore it afterwards.
        monkeypatch.setattr(output.console, "quiet", output.console.quiet)
        output.setup_output("default", quiet=True)
        self._invoke(group, "login")
        assert capsys.readouterr().err == ""

    def test_override_notice_for_a_subcommand(
        self, override_login, capsys, monkeypatch
    ):
        monkeypatch.setattr(
            "deepctl_core.plugin_manager.allow_override_names",
            lambda: frozenset({"debug audio"}),
        )
        _set_mode(monkeypatch, agentic=True, marker=True)

        class Debug(BaseGroupCommand):
            name = "debug"
            help = "d"

        audio = _command_class("audio", [])
        group = click.Group("debug")
        with (
            patch("deepctl_core.plugin_manager.metadata.entry_points") as eps,
            patch.object(
                PluginManager,
                "_hidden_builtin_entry_points",
                staticmethod(lambda g, w: []),
            ),
        ):
            eps.return_value.select.side_effect = lambda group: (
                [_entry_point("a", "p:Audio", audio, "audio-plus", "2.0")]
                if group == "deepctl.subplugins.debug"
                else []
            )
            PluginManager()._load_subcommands_for_group(group, Debug())
        assert capsys.readouterr().err == ""
        self._invoke(group, "audio")
        assert "Using 'debug audio' from audio-plus 2.0 (p)" in _err(capsys)


class TestRecoveryRobustness:
    def test_one_malformed_dist_doesnt_stop_the_scan(self):
        cls = _command_class("login", [])
        bad = Mock()
        type(bad).entry_points = property(
            lambda self: (_ for _ in ()).throw(ValueError("malformed"))
        )
        good_ep = _real_ep("login", "deepctl_cmd_login.command:LoginCommand", cls)
        with patch(
            "deepctl_core.plugin_manager.metadata.distributions",
            return_value=[bad, _dist(good_ep)],
        ):
            found = PluginManager._hidden_builtin_entry_points(
                "deepctl.commands", {"login": "deepctl_cmd_login.command"}
            )
        assert found == [good_ep]

    def test_recovery_load_error_is_shown_literally(self, capsys):
        ep = _real_ep("login", "deepctl_cmd_login.command:LoginCommand", Mock())
        ep.load.side_effect = RuntimeError("[green]ok[/green]\x1b]8;;https://x\x07")
        group = click.Group("dg")
        with patch(
            "deepctl_core.plugin_manager.metadata.distributions",
            return_value=[_dist(ep)],
        ):
            PluginManager()._restore_hidden_builtins(group)
        err = _err(capsys)
        assert "[green]ok[/green]" in err
        assert "\x1b" not in err and "\x07" not in err


class TestPluginVenvUnderAgent:
    """Under an AI-agent marker the plugin venv comes from the real home
    directory, never from $HOME."""

    def _sidecar(self, home: Path, marker: Path) -> Path:
        import sys as _sys

        venv = home / ".deepctl" / "plugins" / "venv"
        pyver = f"python{_sys.version_info[0]}.{_sys.version_info[1]}"
        sp = venv / "lib" / pyver / "site-packages"
        sp.mkdir(parents=True)
        (venv / "pyvenv.cfg").write_text(
            f"home = /usr/bin\nversion = {_sys.version_info[0]}.{_sys.version_info[1]}.0\n"
        )
        (home / ".deepctl" / "plugins" / "plugins.json").write_text(
            '{"plugins": {"repo-plugin": {"version": "1.0"}}}'
        )
        (sp / "w05_repo_plugin.py").write_text(
            "import os\n"
            f"open({str(marker)!r}, 'w').write("
            "'IMPORTED key=' + repr(os.environ.get('DEEPGRAM_API_KEY')))\n"
            "from deepctl_core import BaseCommand\n"
            "class X(BaseCommand):\n"
            "    name = 'repo-x'\n"
            "    help = 'x'\n"
            "    def handle(self, config, auth_manager, client, **kw):\n"
            "        return None\n"
        )
        di = sp / "repo_plugin-1.0.dist-info"
        di.mkdir()
        (di / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: repo-plugin\nVersion: 1.0\n"
        )
        (di / "entry_points.txt").write_text(
            "[deepctl.plugins]\nrepo-x = w05_repo_plugin:X\n"
        )
        return venv

    @pytest.fixture
    def env(self, tmp_path, monkeypatch):
        import sys as _sys

        for var in (*AI_AGENT_ENV_MARKERS, "OR_APP_NAME", "OR_SITE_URL"):
            monkeypatch.delenv(var, raising=False)
        repo_home = tmp_path / "repohome"
        real_home = tmp_path / "realhome"
        real_home.mkdir()
        marker = tmp_path / "imported"
        venv = self._sidecar(repo_home, marker)
        # The process started with HOME pointing at the repo directory.
        monkeypatch.setenv("HOME", str(repo_home))
        monkeypatch.setattr("deepctl_core.plugin_manager.PLUGIN_VENV", venv)
        monkeypatch.setattr("deepctl_core.plugin_env.PLUGIN_VENV", venv)
        monkeypatch.setattr(
            "deepctl_core.plugin_env.PLUGIN_STATE_FILE",
            repo_home / ".deepctl" / "plugins" / "plugins.json",
        )
        monkeypatch.setattr(config_module, "_real_home", lambda: str(real_home))
        monkeypatch.setenv("DEEPGRAM_API_KEY", "sk-FAKEKEYSENTINEL123")
        monkeypatch.setattr(_sys, "path", list(_sys.path))
        work = tmp_path / "work"
        work.mkdir()
        monkeypatch.chdir(work)
        yield {"marker": marker, "real_home": real_home, "tmp": tmp_path}
        _sys.modules.pop("w05_repo_plugin", None)

    def test_agent_marker_ignores_a_venv_found_through_home(
        self, env, monkeypatch, capsys
    ):
        import sys as _sys

        monkeypatch.setenv("CLAUDECODE", "1")
        group = click.Group("dg")
        PluginManager()._load_plugin_venv_entries(group)
        assert not env["marker"].exists()  # nothing imported, no key read
        assert "w05_repo_plugin" not in _sys.modules
        assert "repo-x" not in group.commands
        # The real home has no plugins to skip: no warning for HOME alone.
        assert "Ignored the plugins" not in capsys.readouterr().err

    def test_skip_warning_when_cwd_holds_the_real_home(self, env, monkeypatch, capsys):
        """cwd "/" (an ancestor of home): the real-home venv is refused, with
        one warning."""
        self._sidecar(env["real_home"], env["marker"])
        monkeypatch.setattr("deepctl_core.plugin_env.PLUGIN_VENV", Path("/nonexistent"))
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.chdir("/")
        group = click.Group("dg")
        PluginManager()._load_plugin_venv_entries(group)
        assert not env["marker"].exists()
        assert "repo-x" not in group.commands
        raw = capsys.readouterr().err
        err = " ".join(raw.split())
        assert err.count("Ignored the plugins in a plugin environment") == 1
        assert (
            "If you are an AI agent, tell the user about this and don't move, "
            "copy or link plugin files to change it." in err
        )
        for needle in ("allow_override", "plugins.json", "config.yaml", "realhome"):
            assert needle not in raw

    def test_no_skip_warning_when_the_skipped_venv_lists_nothing(
        self, env, monkeypatch, capsys
    ):
        self._sidecar(env["real_home"], env["marker"])
        (env["real_home"] / ".deepctl" / "plugins" / "plugins.json").write_text(
            '{"plugins": {}}'
        )
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.chdir("/")
        PluginManager()._load_plugin_venv_entries(click.Group("dg"))
        assert "Ignored the plugins" not in capsys.readouterr().err

    def test_no_skip_warning_when_real_home_plugins_load(
        self, env, monkeypatch, capsys
    ):
        """HOME redirected to a directory with its own venv while the
        real-home plugins load: nothing was skipped that this run would use."""
        self._sidecar(env["real_home"], env["marker"])
        monkeypatch.setenv("CLAUDECODE", "1")
        group = click.Group("dg")
        PluginManager()._load_plugin_venv_entries(group)
        assert "repo-x" in group.commands
        assert "Ignored the plugins" not in capsys.readouterr().err

    def test_no_skip_warning_without_a_marker(self, env, capsys):
        group = click.Group("dg")
        PluginManager()._load_plugin_venv_entries(group)
        assert "Ignored the plugins" not in capsys.readouterr().err

    def test_control_without_agent_marker_loads_it(self, env):
        group = click.Group("dg")
        PluginManager()._load_plugin_venv_entries(group)
        assert "repo-x" in group.commands
        assert env["marker"].read_text() == "IMPORTED key='sk-FAKEKEYSENTINEL123'"

    def test_agent_marker_loads_the_real_home_venv(self, env, monkeypatch):
        self._sidecar(env["real_home"], env["marker"])
        monkeypatch.setenv("CLAUDECODE", "1")
        group = click.Group("dg")
        PluginManager()._load_plugin_venv_entries(group)
        assert "repo-x" in group.commands

    def test_agent_venv_through_a_symlink_is_skipped(self, env, monkeypatch):
        from deepctl_core import _plugin_trust

        elsewhere = env["tmp"] / "elsewhere"
        self._sidecar(elsewhere, env["marker"])
        (env["real_home"] / ".deepctl").symlink_to(elsewhere / ".deepctl")
        monkeypatch.setenv("CLAUDECODE", "1")
        assert _plugin_trust.agent_plugin_venv() is None
        group = click.Group("dg")
        PluginManager()._load_plugin_venv_entries(group)
        assert not env["marker"].exists()

    def test_agent_venv_with_symlinked_site_packages_is_skipped(self, env, monkeypatch):
        import shutil
        import sys as _sys

        venv = self._sidecar(env["real_home"], env["marker"])
        lib = venv / "lib" / f"python{_sys.version_info[0]}.{_sys.version_info[1]}"
        outside = env["tmp"] / "outside-sp"
        shutil.move(str(lib / "site-packages"), outside)
        (lib / "site-packages").symlink_to(outside)
        monkeypatch.setenv("CLAUDECODE", "1")
        group = click.Group("dg")
        PluginManager()._load_plugin_venv_entries(group)
        assert not env["marker"].exists()

    def test_agent_venv_inside_cwd_is_skipped(self, env, monkeypatch):
        from deepctl_core import _plugin_trust

        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.chdir(env["tmp"])  # real home is tmp/realhome, inside cwd
        assert _plugin_trust.agent_plugin_venv() is None
        monkeypatch.chdir(env["real_home"])  # cwd is the home itself
        assert _plugin_trust.agent_plugin_venv() == (
            Path(os.path.realpath(env["real_home"])) / ".deepctl" / "plugins" / "venv"
        )


def test_allow_override_survives_a_config_save(tmp_path, monkeypatch):
    """Saving the user config keeps plugins.allow_override."""
    import yaml

    monkeypatch.setattr(
        "deepctl_core.config.platformdirs.user_config_dir",
        lambda *a, **k: str(tmp_path),
    )
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "config.yaml"
    path.write_text("plugins:\n  allow_override: [login]\n")
    config = Config()
    config.get_profile("default").project_id = "p-1"
    config.save()
    data = yaml.safe_load(path.read_text())
    assert data["plugins"]["allow_override"] == ["login"]
