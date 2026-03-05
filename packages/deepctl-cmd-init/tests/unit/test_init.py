"""Tests for the init command."""

from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from deepctl_cmd_init.command import InitCommand
from deepctl_cmd_init.lifecycle import inject_env
from deepctl_cmd_init.models import (
    InitResult,
    LifecycleStep,
    TemplateDetail,
    TemplateInfo,
    TemplateListResponse,
    TemplateStats,
    TomlConfig,
)
from deepctl_cmd_init.templates_api import filter_templates
from deepctl_core import AuthManager, Config, DeepgramClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_config():
    config = Mock(spec=Config)
    config.get.return_value = None
    return config


@pytest.fixture
def mock_auth_manager():
    auth = Mock(spec=AuthManager)
    auth.get_api_key.return_value = None
    auth.is_authenticated.return_value = False
    return auth


@pytest.fixture
def mock_client():
    return Mock(spec=DeepgramClient)


@pytest.fixture
def init_command():
    return InitCommand()


@pytest.fixture
def sample_templates():
    return [
        TemplateInfo(
            name="node-transcription",
            title="Node Transcription",
            description="Get started with Node",
            language="javascript",
            framework="node",
            category="transcription",
        ),
        TemplateInfo(
            name="flask-transcription",
            title="Flask Transcription",
            description="Get started with Flask",
            language="python",
            framework="flask",
            category="transcription",
        ),
        TemplateInfo(
            name="next-live",
            title="Next.js Live Transcription",
            description="Live transcription with Next.js",
            language="typescript",
            framework="next",
            category="live-transcription",
        ),
    ]


@pytest.fixture
def sample_detail():
    return TemplateDetail(
        name="node-transcription",
        title="Node Transcription",
        description="Get started with Node",
        language="javascript",
        framework="node",
        category="transcription",
        sdk="node",
        tags=["node", "transcription"],
        links={"github": "https://github.com/deepgram-starters/node-transcription"},
        stats=TemplateStats(stars=10, forks=5),
        config=TomlConfig(
            install=LifecycleStep(command="npm install"),
            start=LifecycleStep(command="npm start"),
        ),
    )


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestModels:
    def test_template_info_parsing(self):
        data = {
            "name": "test-app",
            "title": "Test App",
            "description": "A test app",
            "language": "python",
            "framework": None,
            "category": "transcription",
        }
        info = TemplateInfo.model_validate(data)
        assert info.name == "test-app"
        assert info.framework is None

    def test_template_list_response_parsing(self):
        data = {
            "total": 44,
            "page": 1,
            "limit": 10,
            "totalPages": 5,
            "items": [
                {
                    "name": "test",
                    "title": "Test",
                    "description": "desc",
                    "language": "python",
                }
            ],
        }
        resp = TemplateListResponse.model_validate(data)
        assert resp.total == 44
        assert resp.total_pages == 5
        assert len(resp.items) == 1

    def test_template_detail_with_config(self, sample_detail):
        assert sample_detail.config is not None
        assert sample_detail.config.install is not None
        assert sample_detail.config.install.command == "npm install"

    def test_init_result_defaults(self):
        result = InitResult()
        assert result.status == "success"
        assert result.installed is False
        assert result.started is False


# ---------------------------------------------------------------------------
# Filter tests
# ---------------------------------------------------------------------------


class TestFilterTemplates:
    def test_filter_by_language(self, sample_templates):
        result = filter_templates(sample_templates, "python")
        assert len(result) == 1
        assert result[0].name == "flask-transcription"

    def test_filter_by_framework(self, sample_templates):
        result = filter_templates(sample_templates, "flask")
        assert len(result) == 1

    def test_filter_by_name(self, sample_templates):
        result = filter_templates(sample_templates, "next-live")
        assert len(result) == 1

    def test_filter_case_insensitive(self, sample_templates):
        result = filter_templates(sample_templates, "PYTHON")
        assert len(result) == 1

    def test_filter_no_match(self, sample_templates):
        result = filter_templates(sample_templates, "rust")
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestInjectEnv:
    def test_creates_env_file(self, tmp_path):
        inject_env(tmp_path, "sk-test-key")
        env_file = tmp_path / ".env"
        assert env_file.exists()
        assert "DEEPGRAM_API_KEY=sk-test-key" in env_file.read_text()

    def test_appends_to_existing_env(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("OTHER_VAR=value\n")
        inject_env(tmp_path, "sk-test-key")
        content = env_file.read_text()
        assert "OTHER_VAR=value" in content
        assert "DEEPGRAM_API_KEY=sk-test-key" in content

    def test_skips_if_key_already_set(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("DEEPGRAM_API_KEY=existing-key\n")
        inject_env(tmp_path, "sk-new-key")
        content = env_file.read_text()
        assert content == "DEEPGRAM_API_KEY=existing-key\n"


# ---------------------------------------------------------------------------
# Command tests
# ---------------------------------------------------------------------------


class TestInitCommand:
    def test_command_metadata(self, init_command):
        assert init_command.name == "init"
        assert init_command.requires_auth is False
        assert init_command.requires_project is False

    def test_get_arguments(self, init_command):
        args = init_command.get_arguments()
        names = []
        for a in args:
            if "name" in a:
                names.append(a["name"])
            else:
                names.extend(a["names"])
        assert "template" in names
        assert "--list" in names
        assert "--search" in names
        assert "--install" in names

    @patch("deepctl_cmd_init.command.templates_api")
    def test_list_mode(
        self,
        mock_api,
        init_command,
        mock_config,
        mock_auth_manager,
        mock_client,
        sample_templates,
    ):
        mock_api.list_templates.return_value = TemplateListResponse(
            total=3, items=sample_templates
        )

        result = init_command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            list=True,
            search=None,
            template=None,
            dir=None,
            install=False,
            start=False,
            no_install=False,
            no_start=False,
        )

        assert isinstance(result, list)
        assert len(result) == 3
        mock_api.list_templates.assert_called_once_with(search=None)

    @patch("deepctl_cmd_init.command.templates_api")
    def test_list_mode_with_search(
        self, mock_api, init_command, mock_config, mock_auth_manager, mock_client
    ):
        mock_api.list_templates.return_value = TemplateListResponse(
            total=1,
            items=[
                TemplateInfo(
                    name="flask-transcription",
                    title="Flask Transcription",
                    description="Get started with Flask",
                    language="python",
                    framework="flask",
                    category="transcription",
                )
            ],
        )

        result = init_command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            list=True,
            search="python",
            template=None,
            dir=None,
            install=False,
            start=False,
            no_install=False,
            no_start=False,
        )

        assert isinstance(result, list)
        assert len(result) == 1
        mock_api.list_templates.assert_called_once_with(search="python")

    @patch("deepctl_cmd_init.command.templates_api")
    @patch("deepctl_cmd_init.command.lifecycle")
    def test_direct_clone(
        self,
        mock_lifecycle,
        mock_api,
        init_command,
        mock_config,
        mock_auth_manager,
        mock_client,
        sample_detail,
        tmp_path,
    ):
        mock_api.get_template.return_value = sample_detail
        mock_lifecycle.clone_template.return_value = None
        mock_lifecycle.run_lifecycle_step.return_value = True

        target = str(tmp_path / "my-app")

        with patch.object(init_command, "confirm", return_value=True):
            result = init_command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                template="node-transcription",
                dir=target,
                search=None,
                list=False,
                install=True,
                start=False,
                no_install=False,
                no_start=True,
            )

        assert isinstance(result, InitResult)
        assert result.status == "success"
        assert result.template == "node-transcription"
        mock_api.get_template.assert_called_once_with("node-transcription")
        mock_lifecycle.clone_template.assert_called_once()
        mock_lifecycle.run_lifecycle_step.assert_called_once()

    @patch("deepctl_cmd_init.command.templates_api")
    def test_template_not_found(
        self, mock_api, init_command, mock_config, mock_auth_manager, mock_client
    ):
        mock_api.get_template.side_effect = Exception("Not found")

        result = init_command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            template="nonexistent",
            dir=None,
            search=None,
            list=False,
            install=False,
            start=False,
            no_install=False,
            no_start=False,
        )

        assert isinstance(result, InitResult)
        assert result.status == "error"

    @patch("deepctl_cmd_init.command.templates_api")
    @patch("deepctl_cmd_init.command.lifecycle")
    def test_api_key_injection(
        self,
        mock_lifecycle,
        mock_api,
        init_command,
        mock_config,
        mock_auth_manager,
        mock_client,
        sample_detail,
        tmp_path,
    ):
        mock_api.get_template.return_value = sample_detail
        mock_lifecycle.clone_template.return_value = None
        mock_auth_manager.get_api_key.return_value = "sk-my-key"

        target = str(tmp_path / "app")

        with patch.object(init_command, "confirm", return_value=False):
            result = init_command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                template="node-transcription",
                dir=target,
                search=None,
                list=False,
                install=False,
                start=False,
                no_install=True,
                no_start=True,
            )

        # Clone cancelled because confirm returned False
        assert result.status == "cancelled"
