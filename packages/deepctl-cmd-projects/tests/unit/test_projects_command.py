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


class TestSetDefaultValidation:
    """--set-default warns and asks when the key can't see the project, as
    in 0.4.x; any other request failure stops the command."""

    @staticmethod
    def _run(code):
        from deepctl_core.transport import TransportError

        command = ProjectsCommand()
        config = Mock(spec=Config)
        config.profile = "default"
        client = Mock()
        client.get_project.side_effect = TransportError(
            code, {"path": "/v1/projects/p9", "http_status": 503}, {}
        )
        with patch(
            "deepctl_cmd_projects.command.DeepgramClient", return_value=client
        ), patch.object(command, "confirm", return_value=False) as confirm:
            result = command._set_default_project(config, Mock(), "p9")
        return result, confirm, config

    @pytest.mark.parametrize("code", ["not_found", "auth_forbidden"])
    def test_unseen_project_warns_and_confirms(self, code):
        result, confirm, config = self._run(code)
        confirm.assert_called_once()
        assert result.status == "cancelled"
        config.update_profile.assert_not_called()

    def test_other_failures_stop(self):
        from deepctl_core.transport import TransportError

        with pytest.raises(TransportError):
            self._run("server_error")


def test_create_without_an_id_says_how_to_find_it(monkeypatch):
    monkeypatch.setattr("deepctl_core.transport.prog", lambda: "dg")
    warnings: list[str] = []
    monkeypatch.setattr(
        "deepctl_cmd_projects.command.print_warning",
        lambda text, **kwargs: warnings.append(text),
    )
    client = Mock(spec=DeepgramClient)
    client.create_project.return_value = {"name": "Acme"}
    result = ProjectsCommand().handle(
        config=Mock(spec=Config),
        auth_manager=Mock(spec=AuthManager),
        client=client,
        create="Acme",
    )
    assert result.status == "warning"
    assert warnings == [
        "Deepgram created the project but didn't return its ID. Run dg projects "
        "--list to find it."
    ]
