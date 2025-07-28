"""Tests for the output utilities."""

import pytest
from unittest.mock import Mock, patch, MagicMock
import json
import yaml

from deepctl_core.output import (
    OutputFormatter,
    setup_output,
    print_output,
    print_success,
    print_error,
    print_warning,
    print_info,
    get_console,
)


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
        data = [
            {"name": "Alice", "age": 30},
            {"name": "Bob", "age": 25}
        ]
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
        data = [
            {"name": "Alice", "age": 30},
            {"name": "Bob", "age": 25}
        ]
        result = formatter.format(data)

        lines = result.strip().split('\n')
        # Remove carriage returns if present
        lines = [line.rstrip('\r') for line in lines]
        assert lines[0] == "name,age"
        assert lines[1] == "Alice,30"
        assert lines[2] == "Bob,25"

    def test_format_csv_single_dict(self):
        """Test formatting single dict as CSV."""
        formatter = OutputFormatter("csv")
        data = {"name": "Alice", "age": 30}
        result = formatter.format(data)

        lines = result.strip().split('\n')
        # Remove carriage returns if present
        lines = [line.rstrip('\r') for line in lines]
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

    @patch('deepctl_core.output._output_config')
    @patch('deepctl_core.output.console')
    def test_setup_output_default(self, mock_console, mock_config):
        """Test setup with default values."""
        setup_output()

        # Should update config
        mock_config.update.assert_called_once_with({
            'format': 'json',
            'quiet': False,
            'verbose': False
        })

        # Should update console
        assert mock_console.quiet == False

    @patch('deepctl_core.output._output_config')
    @patch('deepctl_core.output.console')
    def test_setup_output_with_params(self, mock_console, mock_config):
        """Test setup with custom parameters."""
        setup_output(format_type='yaml', quiet=True, verbose=True)

        mock_config.update.assert_called_once_with({
            'format': 'yaml',
            'quiet': True,
            'verbose': True
        })

        assert mock_console.quiet == True


class TestPrintFunctions:
    """Test print utility functions."""

    @patch('deepctl_core.output.console')
    @patch('deepctl_core.output._output_config', {'quiet': False})
    def test_print_success(self, mock_console):
        """Test printing success message."""
        print_success("Operation completed")

        mock_console.print.assert_called_once_with(
            "[green]✓[/green] Operation completed"
        )

    @patch('deepctl_core.output.console')
    @patch('deepctl_core.output._output_config', {'quiet': True})
    def test_print_success_quiet(self, mock_console):
        """Test printing success message in quiet mode."""
        print_success("Operation completed")

        # Should not print in quiet mode
        mock_console.print.assert_not_called()

    @patch('deepctl_core.output.stderr_console')
    def test_print_error(self, mock_console):
        """Test printing error message."""
        print_error("Something went wrong")

        mock_console.print.assert_called_once_with(
            "[red]✗[/red] Something went wrong"
        )

    @patch('deepctl_core.output.console')
    @patch('deepctl_core.output._output_config', {'quiet': False})
    def test_print_warning(self, mock_console):
        """Test printing warning message."""
        print_warning("Be careful")

        mock_console.print.assert_called_once_with(
            "[yellow]⚠[/yellow] Be careful"
        )

    @patch('deepctl_core.output.console')
    @patch('deepctl_core.output._output_config', {'quiet': False})
    def test_print_info(self, mock_console):
        """Test printing info message."""
        print_info("FYI")

        mock_console.print.assert_called_once_with(
            "[blue]ℹ[/blue] FYI"
        )


class TestPrintOutput:
    """Test print_output function."""

    @patch('deepctl_core.output.console')
    @patch('deepctl_core.output._output_config', {'quiet': False, 'format': 'json'})
    def test_print_output_json_dict(self, mock_console):
        """Test printing dict as JSON."""
        data = {"test": "value"}
        print_output(data)

        # Should use Rich's JSON display
        mock_console.print.assert_called_once()

    @patch('deepctl_core.output.console')
    @patch('deepctl_core.output._output_config', {'quiet': True, 'format': 'json'})
    def test_print_output_quiet(self, mock_console):
        """Test printing in quiet mode."""
        print_output({"test": "value"})

        # Should not print in quiet mode
        mock_console.print.assert_not_called()

    @patch('deepctl_core.output.console')
    @patch('deepctl_core.output._output_config', {'quiet': False, 'format': 'yaml'})
    def test_print_output_yaml(self, mock_console):
        """Test printing as YAML with syntax highlighting."""
        data = {"test": "value"}
        print_output(data)

        # Should use syntax highlighting
        mock_console.print.assert_called_once()

    @patch('deepctl_core.output.console')
    @patch('deepctl_core.output._output_config', {'quiet': False, 'format': 'table'})
    def test_print_output_table(self, mock_console):
        """Test printing as table."""
        data = [{"name": "Alice", "age": 30}]
        print_output(data)

        # Table formatting uses capture which results in multiple print calls
        assert mock_console.print.call_count >= 1

    @patch('deepctl_core.output.console')
    @patch('deepctl_core.output._output_config', {'quiet': False, 'format': 'csv'})
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
        assert hasattr(console, 'print')
