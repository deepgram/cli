"""Tests for the init command."""

import os
import shutil
import stat
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from deepctl_cmd_init import lifecycle
from deepctl_cmd_init.command import InitCommand
from deepctl_cmd_init.lifecycle import check_prereqs, inject_env
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
from deepctl_core import AuthenticationError, AuthManager, Config, DeepgramClient

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


needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )


def _repo(tmp_path, files=None, track=()):
    """A cloned-template stand-in: a git repo with ``files`` committed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    for name, data in (files or {}).items():
        (repo / name).write_bytes(data.encode() if isinstance(data, str) else data)
    for name in track:
        _git(repo, "add", "-f", name)
    if track:
        _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "t")
    return repo


def _status_lists_env(repo) -> bool:
    out = _git(repo, "status", "--porcelain", "--untracked-files=all").stdout
    return any(line[3:] == ".env" for line in out.splitlines())


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
class TestEnvFileHardening:
    """.env is 0600 and kept out of git."""

    def test_new_env_is_owner_only_whatever_the_umask(self, tmp_path):
        old = os.umask(0o000)
        try:
            inject_env(tmp_path, "sk-test-key")
        finally:
            os.umask(old)
        assert stat.S_IMODE((tmp_path / ".env").stat().st_mode) == 0o600

    @pytest.mark.parametrize(
        "existing", ["OTHER_VAR=value\n", "DEEPGRAM_API_KEY=$DG\n", "A=1"]
    )
    def test_existing_env_is_narrowed_to_owner_only(self, tmp_path, existing):
        env_file = tmp_path / ".env"
        env_file.write_text(existing)
        env_file.chmod(0o644)
        inject_env(tmp_path, "sk-test-key")
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
        assert "DEEPGRAM_API_KEY=sk-test-key\n" in env_file.read_text()

    def test_append_keeps_existing_lines(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("A=1")
        inject_env(tmp_path, "sk-test-key")
        assert env_file.read_text() == "A=1\nDEEPGRAM_API_KEY=sk-test-key\n"

    def test_env_with_key_already_set_is_narrowed(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("DEEPGRAM_API_KEY=existing-key\n")
        env_file.chmod(0o644)
        inject_env(tmp_path, "sk-new-key")
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
        assert ".env" in (tmp_path / ".gitignore").read_text().splitlines()

    def test_env_symlink_is_not_written_through(self, tmp_path, capsys):
        outside = tmp_path / "outside.txt"
        outside.write_text("")
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".env").symlink_to(outside)
        inject_env(repo, "sk-test-key")
        assert outside.read_text() == ""
        err = capsys.readouterr().err
        assert "Not writing your API key to .env: it is a symbolic link." in err
        assert "add DEEPGRAM_API_KEY there yourself" in err

    def test_gitignore_symlink_is_not_written_through(self, tmp_path, capsys):
        outside = tmp_path / "outside.txt"
        outside.write_text("x\n")
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".gitignore").symlink_to(outside)
        inject_env(repo, "sk-test-key")
        assert outside.read_text() == "x\n"
        assert stat.S_IMODE((repo / ".env").stat().st_mode) == 0o600
        assert "Did not add .env to .gitignore: it is a symbolic link." in (
            capsys.readouterr().err
        )


@needs_git
@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
class TestEnvKeptOutOfGit:
    """Decided by git itself (check-ignore / ls-files), not by matching lines."""

    def test_plain_repo_gets_env_ignored(self, tmp_path, capsys):
        repo = _repo(tmp_path)
        inject_env(repo, "sk-test-key")
        assert (repo / ".gitignore").read_text() == ".env\n"
        assert _git(repo, "check-ignore", "-q", ".env").returncode == 0
        assert not _status_lists_env(repo)
        assert "Added .env to .gitignore to keep your API key out of git." in (
            capsys.readouterr().err
        )

    @pytest.mark.parametrize(
        "gitignore", [".env*\n!.env\n", ".env\n!.env\n", "!.env\n"]
    )
    def test_negated_env_is_overridden_by_a_last_line(self, tmp_path, gitignore):
        repo = _repo(tmp_path, {".gitignore": gitignore}, track=[".gitignore"])
        inject_env(repo, "sk-test-key")
        assert (repo / ".gitignore").read_text() == gitignore + ".env\n"
        assert _git(repo, "check-ignore", "-q", ".env").returncode == 0
        assert not _status_lists_env(repo)

    @pytest.mark.parametrize("pattern", [".env*", "/.env", "*.env", ".env"])
    def test_already_ignored_gitignore_left_alone(self, tmp_path, pattern, capsys):
        gitignore = f"node_modules\n{pattern}\n"
        repo = _repo(tmp_path, {".gitignore": gitignore}, track=[".gitignore"])
        inject_env(repo, "sk-test-key")
        assert (repo / ".gitignore").read_text() == gitignore
        assert not _status_lists_env(repo)
        assert "Added .env" not in capsys.readouterr().err

    def test_crlf_gitignore_gets_a_crlf_line(self, tmp_path):
        repo = _repo(tmp_path, {".gitignore": b"node_modules\r\n"}, track=[".gitignore"])
        inject_env(repo, "sk-test-key")
        assert (repo / ".gitignore").read_bytes() == b"node_modules\r\n.env\r\n"
        assert _git(repo, "check-ignore", "-q", ".env").returncode == 0

    def test_missing_trailing_newline(self, tmp_path):
        repo = _repo(tmp_path, {".gitignore": "node_modules"}, track=[".gitignore"])
        inject_env(repo, "sk-test-key")
        assert (repo / ".gitignore").read_text() == "node_modules\n.env\n"
        assert _git(repo, "check-ignore", "-q", ".env").returncode == 0

    def test_tracked_env_warns_and_claims_nothing(self, tmp_path, capsys):
        repo = _repo(
            tmp_path, {".env": "DEEPGRAM_API_KEY=$DEEPGRAM_API_KEY\n"}, track=[".env"]
        )
        inject_env(repo, "sk-test-key")
        err = capsys.readouterr().err
        assert (
            "This template tracks .env in git, so a commit would include your API key."
            in err
        )
        assert "Before you commit, run: git rm --cached .env" in err
        assert "Added .env to .gitignore" not in err
        assert _git(repo, "ls-files", ".env").stdout.strip() == ".env"  # index untouched
        assert not (repo / ".gitignore").exists()  # no line that wouldn't help
        assert stat.S_IMODE((repo / ".env").stat().st_mode) == 0o600

    def test_tracked_env_leaves_a_tracked_gitignore_alone(self, tmp_path):
        repo = _repo(
            tmp_path,
            {".gitignore": "node_modules\n", ".env": "DEEPGRAM_API_KEY=$X\n"},
            track=[".gitignore", ".env"],
        )
        inject_env(repo, "sk-test-key")
        assert (repo / ".gitignore").read_text() == "node_modules\n"
        status = _git(repo, "status", "--porcelain").stdout.splitlines()
        assert " M .gitignore" not in status

    def test_inherited_git_dir_is_ignored(self, tmp_path, monkeypatch):
        """An exported GIT_DIR/GIT_WORK_TREE must not point the check at
        another repository."""
        other = tmp_path / "other"
        other.mkdir()
        _git(other, "init", "-q")
        (other / ".gitignore").write_text(".env\n")
        monkeypatch.setenv("GIT_DIR", str(other / ".git"))
        monkeypatch.setenv("GIT_WORK_TREE", str(other))
        monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "-C", str(repo), "init", "-q"],
            check=True,
            env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        )
        inject_env(repo, "sk-test-key")
        assert (repo / ".gitignore").read_text() == ".env\n"
        out = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
            env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        ).stdout
        assert "?? .env" not in out.splitlines()

    def test_without_git_falls_back_to_pattern_check(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lifecycle.shutil, "which", lambda name: None)
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("node_modules")
        inject_env(tmp_path, "sk-test-key")
        inject_env(tmp_path, "sk-test-key")
        assert gitignore.read_text() == "node_modules\n.env\n"


class TestCloneTemplate:
    def test_url_is_passed_after_end_of_options(self, tmp_path):
        with patch.object(lifecycle.subprocess, "run") as run, patch.object(
            lifecycle.shutil, "which", return_value="/usr/bin/git"
        ):
            run.return_value = MagicMock(returncode=0)
            lifecycle.clone_template("--upload-pack=touch /tmp/pwned", tmp_path / "d")
        argv = run.call_args.args[0]
        clone = argv.index("clone")
        assert argv[clone : clone + 3] == ["clone", "--", "--upload-pack=touch /tmp/pwned"]

    def test_option_like_url_is_not_run_as_an_option(self, tmp_path):
        """With real git: an option-like URL fails as a repository name."""
        if shutil.which("git") is None:
            pytest.skip("git not installed")
        marker = tmp_path / "pwned"
        with pytest.raises(RuntimeError, match="git clone failed"):
            lifecycle.clone_template(f"--upload-pack=touch {marker}", tmp_path / "d")
        assert not marker.exists()


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
        mock_lifecycle.check_prereqs.return_value = []
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
    @patch("deepctl_cmd_init.command.lifecycle")
    def test_expired_credential_warning_is_visible(
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
        mock_lifecycle.check_prereqs.return_value = []
        mock_lifecycle.clone_template.return_value = None
        mock_auth_manager.get_api_key.side_effect = AuthenticationError(
            "Your Deepgram credential has expired. Run `dg login` again."
        )

        with patch.object(init_command, "confirm", return_value=True), patch(
            "deepctl_cmd_init.command.console.print"
        ) as print_console:
            result = init_command.handle(
                config=mock_config,
                auth_manager=mock_auth_manager,
                client=mock_client,
                template="node-transcription",
                dir=str(tmp_path / "my-app"),
                search=None,
                list=False,
                install=False,
                start=False,
                no_install=True,
                no_start=True,
            )

        assert result.status == "success"
        assert any(
            "Run `dg login` again" in str(call)
            for call in print_console.call_args_list
        )

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
        mock_lifecycle.check_prereqs.return_value = []
        mock_lifecycle.clone_template.return_value = None
        mock_auth_manager.get_api_key.return_value = "sk-my-key"

        target = str(tmp_path / "app")

        with patch("deepctl_cmd_init.command._agentic", False):
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


class TestCheckPrereqs:
    """Tests for lifecycle.check_prereqs() — prerequisite tool detection."""

    def test_all_tools_present_returns_empty_list(self):
        with patch("shutil.which", return_value="/usr/bin/tool"):
            assert check_prereqs() == []

    def test_single_missing_tool_returned(self):
        def fake_which(cmd: str) -> str | None:
            return None if cmd == "git" else "/usr/bin/tool"

        with patch("shutil.which", side_effect=fake_which):
            missing = check_prereqs()

        assert len(missing) == 1
        key, name, hint = missing[0]
        assert key == "git"
        assert "git" in name.lower()
        assert hint  # install hint is non-empty

    def test_multiple_missing_tools_all_returned(self):
        def fake_which(cmd: str) -> str | None:
            return None if cmd in ("node", "npm", "curl") else "/usr/bin/tool"

        with patch("shutil.which", side_effect=fake_which):
            missing = check_prereqs()

        keys = [m[0] for m in missing]
        assert "node" in keys
        assert "npm" in keys
        assert "curl" in keys

    def test_all_tools_missing_returns_full_list(self):
        with patch("shutil.which", return_value=None):
            missing = check_prereqs()

        from deepctl_cmd_init.lifecycle import PREREQS

        assert len(missing) == len(PREREQS)

    def test_missing_entry_structure(self):
        """Each entry is a (key, display_name, install_hint) triple."""
        with patch("shutil.which", return_value=None):
            missing = check_prereqs()

        for entry in missing:
            assert len(entry) == 3
            key, name, hint = entry
            assert isinstance(key, str) and key
            assert isinstance(name, str) and name
            assert isinstance(hint, str) and hint
