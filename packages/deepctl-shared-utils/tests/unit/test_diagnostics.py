"""Tests for shared diagnostic console output."""

from io import StringIO

from deepctl_shared_utils.diagnostics import create_diagnostic_console


def test_ci_diagnostic_console_uses_plain_text_in_a_tty(monkeypatch):
    monkeypatch.setenv("CI", "true")
    output = StringIO()

    console = create_diagnostic_console(file=output, force_terminal=True)
    console.print("[red]Error:[/red] File not found")

    assert output.getvalue() == "Error: File not found\n"
    assert "\x1b" not in output.getvalue()
