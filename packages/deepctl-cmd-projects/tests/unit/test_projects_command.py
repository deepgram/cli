"""Tests for projects command."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from deepctl_cmd_projects.command import ProjectsCommand
from deepctl_cmd_projects.models import ProjectsResult
from deepctl_core import AuthManager, Config, DeepgramClient


class TestProjectsOutputGating:
    """stdout stays machine-parseable in json/yaml/csv modes.

    ``projects --list`` prints a human list line-by-line. In any non-``default``
    output mode none of it may reach the stdout ``console`` — the framework
    serialises the returned ``ProjectsResult`` to stdout, so a stray print here
    would prepend non-parseable text to piped JSON. Status chrome uses
    ``status_console`` (stderr) and is intentionally unaffected.
    """

    @pytest.fixture
    def command(self):
        return ProjectsCommand()

    @staticmethod
    def _response():
        return {
            "projects": [
                {
                    "project_id": "proj-1",
                    "name": "Acme",
                    "company": "Acme Inc",
                }
            ]
        }

    @patch("deepctl_cmd_projects.command.get_output_format", return_value="json")
    @patch("deepctl_cmd_projects.command.console")
    def test_list_json_mode_writes_nothing_to_stdout(
        self, mock_console, _fmt, command
    ):
        client = Mock(spec=DeepgramClient)
        client.get_projects.return_value = self._response()

        result = command.handle(
            config=Mock(spec=Config),
            auth_manager=Mock(spec=AuthManager),
            client=client,
            list=True,
        )

        # Result still fully populated for the framework to serialise.
        assert isinstance(result, ProjectsResult)
        assert result.count == 1
        assert result.projects[0].project_id == "proj-1"
        mock_console.print.assert_not_called()

    @patch("deepctl_cmd_projects.command.get_output_format", return_value="default")
    @patch("deepctl_cmd_projects.command.console")
    def test_list_default_mode_prints_list(self, mock_console, _fmt, command):
        client = Mock(spec=DeepgramClient)
        client.get_projects.return_value = self._response()

        command.handle(
            config=Mock(spec=Config),
            auth_manager=Mock(spec=AuthManager),
            client=client,
            list=True,
        )

        assert mock_console.print.called
