"""Which stream each console writes to, pinned.

Regression cover for #104: `deepctl_core` modules declared bare `Console()`
instances bound to stdout and printed diagnostics through them, so
`dg -o json <cmd>` on an auth failure wrote English prose to stdout and left
stderr empty -- unparseable for the CI step that redirects stdout and parses
it. `get_status_console()` warns about exactly this in its own docstring; the
tests below turn that warning into a gate.

The distinction these tests protect is *what the console carries*, not the
module it lives in:

- diagnostics (errors, status, progress, timing chrome) -> stderr, always
- the payload (JSON/YAML/table/CSV a command produces) -> stdout, always
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from deepctl_core import output

CORE_SRC = Path(output.__file__).parent

# Modules whose module-level `console` carries diagnostics only.
DIAGNOSTIC_MODULES = [
    "auth",
    "client",
    "timing",
    "base_group_command",
    "plugin_manager",
]


class TestConsoleBindings:
    """Every module-level console must be one of the two shared instances."""

    @pytest.mark.parametrize("module_name", DIAGNOSTIC_MODULES)
    def test_diagnostic_console_is_the_shared_stderr_console(
        self, module_name: str
    ) -> None:
        import importlib

        module = importlib.import_module(f"deepctl_core.{module_name}")

        assert module.console is output.stderr_console
        assert module.console.stderr is True

    def test_base_command_console_is_the_shared_stdout_console(self) -> None:
        """The payload console stays on stdout -- deliberately.

        Tables, JSON and raw text written by `_output_*` are the
        machine-readable result and belong on stdout. The requirement is that
        it is the *shared* instance, so it honours the agentic no-color and
        highlight settings a bare Console() would silently miss.
        """
        from deepctl_core import base_command

        assert base_command.console is output.console
        assert base_command.console.stderr is False


class TestNoBareConsoleInCore:
    """A bare `Console()` in deepctl_core is how #104 happened."""

    def test_no_module_declares_a_bare_console(self) -> None:
        offenders: list[str] = []

        for path in sorted(CORE_SRC.glob("*.py")):
            # encoding is explicit because read_text() otherwise uses the
            # locale codec -- cp1252 on Windows, which cannot decode the
            # emoji in timing.py and fails the whole matrix.
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name != "Console":
                    continue
                # output.py is where the two shared instances are built.
                if path.name == "output.py":
                    continue
                offenders.append(f"{path.name}:{node.lineno}")

        assert not offenders, (
            "bare Console() in deepctl_core reintroduces the #104 stdout "
            "pollution -- import `console` or `stderr_console` from .output "
            f"instead. Found at: {', '.join(offenders)}"
        )


class TestAuthFailurePayload:
    """The #104 reproduction, as a test."""

    def _run_guard_failure(self, capsys, output_format: str):
        """Drive a requires_auth command whose guard() raises."""
        from unittest.mock import MagicMock, patch

        import click
        from deepctl_core.auth import AuthenticationError
        from deepctl_core.base_command import BaseCommand
        from deepctl_core.config import Config

        class NeedsAuth(BaseCommand):
            name = "needs-auth"
            help = "test command"
            requires_auth = True

            def handle(self, config, auth_manager, client, **kwargs):  # type: ignore[no-untyped-def]
                raise AssertionError("handle must not run when guard() fails")

        command = NeedsAuth()
        ctx = MagicMock(spec=click.Context)
        ctx.obj = {"config": Config()}

        auth_manager = MagicMock()
        auth_manager.guard.side_effect = AuthenticationError(
            "Invalid API key - authentication failed"
        )

        with (
            patch(
                "deepctl_core.base_command.AuthManager", return_value=auth_manager
            ),
            patch("deepctl_core.base_command.DeepgramClient"),
            patch(
                "deepctl_core.output.get_output_format", return_value=output_format
            ),
        ):
            with pytest.raises(SystemExit) as exc:
                command.execute(ctx)

        assert exc.value.code == 1
        return capsys.readouterr()

    def test_json_failure_writes_parseable_payload_to_stdout(self, capsys) -> None:
        captured = self._run_guard_failure(capsys, "json")

        payload = json.loads(captured.out)
        assert payload["status"] == "error"
        assert "Invalid API key" in payload["error"]

    def test_default_mode_writes_nothing_to_stdout(self, capsys) -> None:
        """Human mode must not gain a duplicate of the stderr diagnosis."""
        captured = self._run_guard_failure(capsys, "default")

        assert captured.out == ""
