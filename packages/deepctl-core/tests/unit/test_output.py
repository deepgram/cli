"""Tests for the output utilities."""

import json
import sys
from unittest.mock import MagicMock, Mock, patch

import pytest
import yaml
from deepctl_core.output import (
    MACHINE_FORMATS,
    OutputFormatter,
    get_console,
    get_status_console,
    is_agentic,
    print_error,
    print_info,
    print_output,
    print_success,
    print_warning,
    setup_output,
)


class TestIsAgentic:
    """Cover every signal that flips the CLI into non-interactive mode."""

    @pytest.fixture
    def baseline(self, monkeypatch):
        """Interactive baseline: clean argv, real-looking TTY env, no AI hints."""
        monkeypatch.setattr("sys.argv", ["dg", "listen", "foo.wav"])
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)
        for var in (
            "CI",
            "CLAUDECODE",
            "CLAUDE_CODE_ENTRYPOINT",
            "CODEX_SANDBOX",
            "CODEX_SANDBOX_NETWORK_DISABLED",
            "OR_APP_NAME",
            "OR_SITE_URL",
            "NO_COLOR",
        ):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("TERM", "xterm-256color")

    def test_baseline_is_interactive(self, baseline):
        assert is_agentic() is False

    @pytest.mark.parametrize(
        "argv",
        [
            ["dg", "--non-interactive", "listen", "foo.wav"],
            ["dg", "listen", "--non-interactive", "foo.wav"],
            ["dg", "listen", "foo.wav", "--non-interactive"],
        ],
    )
    def test_non_interactive_flag_anywhere_in_argv(
        self, baseline, monkeypatch, argv
    ):
        monkeypatch.setattr("sys.argv", argv)
        assert is_agentic() is True

    @pytest.mark.parametrize(
        "argv",
        [
            ["dg", "--agent-friendly", "listen", "foo.wav"],
            ["dg", "listen", "--agent-friendly", "foo.wav"],
        ],
    )
    def test_agent_friendly_flag_anywhere_in_argv(
        self, baseline, monkeypatch, argv
    ):
        monkeypatch.setattr("sys.argv", argv)
        assert is_agentic() is True

    @pytest.mark.parametrize("ci_value", ["1", "true"])
    def test_ci_env_var_is_hard_signal(self, baseline, monkeypatch, ci_value):
        monkeypatch.setenv("CI", ci_value)
        assert is_agentic() is True

    @pytest.mark.parametrize(
        "ci_value",
        ["false", "0", "", "yes", "TRUE"],
    )
    def test_other_ci_values_do_not_count(
        self, baseline, monkeypatch, ci_value
    ):
        monkeypatch.setenv("CI", ci_value)
        assert is_agentic() is False

    @pytest.mark.parametrize(
        "env_var",
        [
            "CLAUDECODE",
            "CLAUDE_CODE_ENTRYPOINT",
            "CODEX_SANDBOX",
            "CODEX_SANDBOX_NETWORK_DISABLED",
        ],
    )
    def test_ai_tool_env_var_is_hard_signal(
        self, baseline, monkeypatch, env_var
    ):
        monkeypatch.setenv(env_var, "1")
        assert is_agentic() is True

    def test_aider_via_or_app_name(self, baseline, monkeypatch):
        monkeypatch.setenv("OR_APP_NAME", "Aider")
        assert is_agentic() is True

    def test_aider_via_or_site_url(self, baseline, monkeypatch):
        monkeypatch.setenv("OR_SITE_URL", "https://aider.example.com")
        assert is_agentic() is True

    def test_one_soft_signal_not_enough(self, baseline, monkeypatch):
        monkeypatch.setattr("sys.stdout.isatty", lambda: False)
        assert is_agentic() is False

    def test_two_soft_signals_not_enough(self, baseline, monkeypatch):
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("sys.stdout.isatty", lambda: False)
        assert is_agentic() is False

    def test_three_soft_signals_trip_threshold(self, baseline, monkeypatch):
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("sys.stdout.isatty", lambda: False)
        monkeypatch.setenv("NO_COLOR", "1")
        assert is_agentic() is True

    def test_dumb_term_counts_as_soft_signal(self, baseline, monkeypatch):
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("sys.stdout.isatty", lambda: False)
        monkeypatch.setenv("TERM", "dumb")
        assert is_agentic() is True

    def test_unset_term_counts_as_soft_signal(self, baseline, monkeypatch):
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("sys.stdout.isatty", lambda: False)
        monkeypatch.delenv("TERM", raising=False)
        assert is_agentic() is True


class TestOutputFormatter:
    """Test OutputFormatter class."""

    def test_init_default_format(self):
        """Test initialization with default format."""
        formatter = OutputFormatter()
        assert formatter.format_type == "json"

    def test_init_custom_format(self):
        """Test initialization with custom format."""
        formatter = OutputFormatter("yaml")
        assert formatter.format_type == "yaml"

    def test_format_json_dict(self):
        """Test formatting dictionary as JSON."""
        formatter = OutputFormatter("json")
        data = {"name": "test", "value": 123}
        result = formatter.format(data)

        # Should produce valid JSON
        parsed = json.loads(result)
        assert parsed == data

    def test_format_json_list(self):
        """Test formatting list as JSON."""
        formatter = OutputFormatter("json")
        data = [{"id": 1}, {"id": 2}]
        result = formatter.format(data)

        parsed = json.loads(result)
        assert parsed == data

    def test_format_json_string(self):
        """Test formatting string as JSON."""
        formatter = OutputFormatter("json")
        data = "test string"
        result = formatter.format(data)

        # String gets wrapped in {"result": ...}
        parsed = json.loads(result)
        assert parsed == {"result": "test string"}

    def test_format_yaml_dict(self):
        """Test formatting dictionary as YAML."""
        formatter = OutputFormatter("yaml")
        data = {"name": "test", "value": 123}
        result = formatter.format(data)

        # Should produce valid YAML
        parsed = yaml.safe_load(result)
        assert parsed == data

    def test_format_yaml_list(self):
        """Test formatting list as YAML."""
        formatter = OutputFormatter("yaml")
        data = [{"id": 1}, {"id": 2}]
        result = formatter.format(data)

        parsed = yaml.safe_load(result)
        assert parsed == data

    def test_format_table_list_of_dicts(self):
        """Test formatting list of dicts as table."""
        formatter = OutputFormatter("table")
        data = [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]
        result = formatter.format(data)

        # Should contain headers and data (headers are capitalized)
        assert "Name" in result
        assert "Age" in result
        assert "Alice" in result
        assert "Bob" in result
        assert "30" in result
        assert "25" in result

    def test_format_table_single_dict(self):
        """Test formatting single dict as table."""
        formatter = OutputFormatter("table")
        data = {"name": "Alice", "age": 30}
        result = formatter.format(data)

        # Should show key-value pairs
        assert "Key" in result
        assert "Value" in result
        assert "name" in result or "Name" in result
        assert "Alice" in result
        assert "age" in result or "Age" in result
        assert "30" in result

    def test_format_table_empty_list(self):
        """Test formatting empty list as table."""
        formatter = OutputFormatter("table")
        data = []
        result = formatter.format(data)

        # Empty list returns string representation
        assert result == "[]"

    def test_format_table_non_dict_list(self):
        """Test formatting non-dict list as table."""
        formatter = OutputFormatter("table")
        data = ["item1", "item2", "item3"]
        result = formatter.format(data)

        # Should show as list with Value column
        assert "Value" in result
        assert "item1" in result
        assert "item2" in result
        assert "item3" in result

    def test_format_csv_list_of_dicts(self):
        """Test formatting list of dicts as CSV."""
        formatter = OutputFormatter("csv")
        data = [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]
        result = formatter.format(data)

        lines = result.strip().split("\n")
        # Remove carriage returns if present
        lines = [line.rstrip("\r") for line in lines]
        assert lines[0] == "name,age"
        assert lines[1] == "Alice,30"
        assert lines[2] == "Bob,25"

    def test_format_csv_single_dict(self):
        """Test formatting single dict as CSV."""
        formatter = OutputFormatter("csv")
        data = {"name": "Alice", "age": 30}
        result = formatter.format(data)

        lines = result.strip().split("\n")
        # Remove carriage returns if present
        lines = [line.rstrip("\r") for line in lines]
        # Single dict shows as Key,Value format
        assert lines[0] == "Key,Value"
        assert "name,Alice" in lines[1] or "Alice,name" in lines[1]

    def test_format_csv_with_special_chars(self):
        """Test CSV formatting with special characters."""
        formatter = OutputFormatter("csv")
        data = [{"name": "Alice, Jr.", "note": 'Has "quotes"'}]
        result = formatter.format(data)

        # Values with special chars should be quoted
        assert '"Alice, Jr."' in result
        assert '"Has ""quotes"""' in result

    def test_format_unsupported_type(self):
        """Test formatting with unsupported format type."""
        formatter = OutputFormatter("unknown")
        data = {"test": "data"}
        result = formatter.format(data)

        # Falls back to JSON
        parsed = json.loads(result)
        assert parsed == data


class TestSetupOutput:
    """Test setup_output function."""

    @patch("deepctl_core.output._output_config")
    @patch("deepctl_core.output.console")
    def test_setup_output_default(self, mock_console, mock_config):
        """Test setup with default values."""
        setup_output()

        # Should update config
        mock_config.update.assert_called_once_with(
            {"format": "json", "quiet": False, "verbose": False}
        )

        # Should update console
        assert mock_console.quiet == False

    @patch("deepctl_core.output._output_config")
    @patch("deepctl_core.output.console")
    def test_setup_output_with_params(self, mock_console, mock_config):
        """Test setup with custom parameters."""
        setup_output(format_type="yaml", quiet=True, verbose=True)

        mock_config.update.assert_called_once_with(
            {"format": "yaml", "quiet": True, "verbose": True}
        )

        assert mock_console.quiet == True


class TestPrintFunctions:
    """Test print utility functions."""

    @patch("deepctl_core.output.console")
    @patch("deepctl_core.output._output_config", {"quiet": False, "agentic": False})
    def test_print_success(self, mock_console):
        """Test printing success message."""
        print_success("Operation completed")

        mock_console.print.assert_called_once_with(
            "[green]✓[/green] Operation completed"
        )

    @patch("deepctl_core.output.console")
    @patch("deepctl_core.output._output_config", {"quiet": True, "agentic": False})
    def test_print_success_quiet(self, mock_console):
        """Test printing success message in quiet mode."""
        print_success("Operation completed")

        # Should not print in quiet mode
        mock_console.print.assert_not_called()

    @patch("deepctl_core.output.stderr_console")
    @patch("deepctl_core.output._output_config", {"quiet": False, "agentic": False})
    def test_print_error(self, mock_console):
        """Test printing error message."""
        print_error("Something went wrong")

        mock_console.print.assert_called_once_with(
            "[red]✗[/red] Something went wrong"
        )

    @patch("deepctl_core.output.stderr_console")
    @patch("deepctl_core.output._output_config", {"quiet": False, "agentic": True})
    def test_print_error_agentic(self, mock_console):
        """Test printing error message in agentic mode."""
        print_error("Something went wrong")

        mock_console.print.assert_called_once_with(
            "ERROR: Something went wrong"
        )

    @patch("deepctl_core.output.console")
    @patch("deepctl_core.output._output_config", {"quiet": False, "agentic": False})
    def test_print_warning(self, mock_console):
        """Test printing warning message."""
        print_warning("Be careful")

        mock_console.print.assert_called_once_with(
            "[yellow]⚠[/yellow] Be careful"
        )

    @patch("deepctl_core.output.console")
    @patch("deepctl_core.output._output_config", {"quiet": False, "agentic": False})
    def test_print_info(self, mock_console):
        """Test printing info message."""
        print_info("FYI")

        mock_console.print.assert_called_once_with("[blue]ℹ[/blue] FYI")


class TestPrintOutput:
    """Test print_output function."""

    @patch("deepctl_core.output.stdout_console")
    @patch(
        "deepctl_core.output._output_config",
        {"quiet": False, "format": "json"},
    )
    def test_print_output_json_dict(self, mock_console):
        """Test printing dict as JSON."""
        data = {"test": "value"}
        print_output(data)

        # Should use Rich's JSON display
        mock_console.print.assert_called_once()

    @patch("deepctl_core.output.stdout_console")
    @patch(
        "deepctl_core.output._output_config", {"quiet": True, "format": "json"}
    )
    def test_print_output_quiet(self, mock_console):
        """Test printing in quiet mode."""
        print_output({"test": "value"})

        # Should not print in quiet mode
        mock_console.print.assert_not_called()

    @patch("deepctl_core.output.stdout_console")
    @patch(
        "deepctl_core.output._output_config",
        {"quiet": False, "format": "yaml"},
    )
    def test_print_output_yaml(self, mock_console):
        """Test printing as YAML with syntax highlighting."""
        data = {"test": "value"}
        print_output(data)

        # Should use syntax highlighting
        mock_console.print.assert_called_once()

    @patch("deepctl_core.output.stdout_console")
    @patch(
        "deepctl_core.output._output_config",
        {"quiet": False, "format": "table"},
    )
    def test_print_output_table(self, mock_console):
        """Test printing as table."""
        data = [{"name": "Alice", "age": 30}]
        print_output(data)

        # Table formatting uses capture which results in multiple print calls
        assert mock_console.print.call_count >= 1

    @patch("deepctl_core.output.stdout_console")
    @patch(
        "deepctl_core.output._output_config", {"quiet": False, "format": "csv"}
    )
    def test_print_output_csv(self, mock_console):
        """Test printing as CSV."""
        data = [{"name": "Alice", "age": 30}]
        print_output(data)

        # CSV should be printed directly
        mock_console.print.assert_called_once()


class TestGetConsole:
    """Test get_console function."""

    def test_get_console(self):
        """Test getting console instance."""
        console = get_console()

        # Should return Rich Console instance
        assert console is not None
        assert hasattr(console, "print")


class TestStatusConsoleRouting:
    """Status output must yield stdout to machine-readable payloads.

    Regression tests for the `-o json` pollution bug: commands printed status
    lines and tables to stdout, so `dg -o json projects | jq` received
    "Fetching projects..." ahead of the JSON and failed to parse.
    """

    @pytest.mark.parametrize("fmt", sorted(MACHINE_FORMATS))
    def test_status_console_writes_to_stderr_for_machine_formats(self, fmt):
        with patch(
            "deepctl_core.output._output_config",
            {"format": fmt, "quiet": False, "agentic": False},
        ):
            assert get_status_console().file is sys.stderr

    @pytest.mark.parametrize("fmt", ["default", "table"])
    def test_status_console_writes_to_stdout_for_human_formats(self, fmt):
        with patch(
            "deepctl_core.output._output_config",
            {"format": fmt, "quiet": False, "agentic": False},
        ):
            assert get_status_console().file is sys.stdout

    def test_machine_formats_membership(self):
        # `table` is a human rendering and must keep stdout
        assert "table" not in MACHINE_FORMATS
        assert "default" not in MACHINE_FORMATS
        assert {"json", "yaml", "csv"} == set(MACHINE_FORMATS)

    def test_payload_console_is_not_the_status_console(self):
        """The payload must never be diverted along with status output."""
        from deepctl_core.output import stdout_console

        assert stdout_console is not get_status_console()
        with patch(
            "deepctl_core.output._output_config",
            {"format": "json", "quiet": False, "agentic": False},
        ):
            # status steps aside, payload keeps stdout
            assert get_status_console().file is sys.stderr
            assert stdout_console.file is sys.stdout
