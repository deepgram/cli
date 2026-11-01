"""Commands that save the user config print the refusal's hint once when an
AI agent session can't save over a file others can change."""

from __future__ import annotations

import sys

import pytest
import yaml
from deepctl_core import AuthManager, Config, DeepgramClient, hosts, output
from deepctl_core import config as config_module

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")

FILE_HINT = "A person must fix this file. If you are an AI agent, ask the user."
REFUSAL = (
    "in an AI agent session, dg can't save this config because the file can be "
    "changed by other users, so nothing was saved."
)


@pytest.fixture
def writable_config(tmp_path, monkeypatch):
    path = tmp_path / "user" / "config.yaml"
    path.parent.mkdir()
    path.write_text(
        yaml.safe_dump(
            {
                "profiles": {
                    "default": {"api_key": "sk-PLANTED", "project_id": "p0"},
                    "work": {"project_id": "p1"},
                }
            }
        )
    )
    path.chmod(0o666)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config_module, "_default_config_file", lambda: path)
    monkeypatch.setattr(config_module, "_trust_root_location", lambda: (tmp_path, path))
    monkeypatch.setattr(config_module, "_pending_notices", [])
    monkeypatch.setattr(config_module, "_seen_notices", set())
    monkeypatch.setattr(hosts, "_reported", set())
    monkeypatch.setattr(output, "is_ai_agent", lambda: True)
    monkeypatch.setattr(output, "is_agentic", lambda: True)
    output._output_config.update(agentic=True, quiet=False, format="json")
    monkeypatch.setattr(output.stderr_console, "_width", 400)
    from deepctl_core import auth as auth_module

    deleted: list[str] = []
    monkeypatch.setattr(auth_module.keyring, "get_password", lambda *a: None)
    monkeypatch.setattr(auth_module.keyring, "set_password", lambda *a: None)
    monkeypatch.setattr(
        auth_module.keyring, "delete_password", lambda service, key: deleted.append(key)
    )
    monkeypatch.setenv("DEEPGRAM_API_KEY", "sk-ENVKEY")
    yield path, deleted
    output._output_config.update(agentic=False, format="json")


def _run(path, capsys, handler):
    before = path.read_bytes()
    config = Config(config_path=str(path))
    auth = AuthManager(config)
    config_module.flush_config_notices()
    result = handler(config, auth)
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert path.read_bytes() == before
    assert (path.stat().st_mode & 0o777) == 0o666
    assert result.status == "error"
    assert REFUSAL in str(result.message)
    assert text.count(f"HINT: {FILE_HINT}") == 1, text
    return text


@pytest.mark.parametrize("kwargs", [{}, {"keep_config": True}, {"all": True}])
def test_logout_refusal_hint_and_keyring_untouched(writable_config, capsys, kwargs):
    from deepctl_cmd_login.command import LogoutCommand

    path, deleted = writable_config
    _run(
        path,
        capsys,
        lambda c, a: LogoutCommand().handle(c, a, DeepgramClient(c, a), **kwargs),
    )
    assert deleted == []


def test_profiles_switch_refusal_hint(writable_config, capsys, monkeypatch):
    from deepctl_cmd_login.command import ProfilesCommand
    from deepctl_core import auth as auth_module

    monkeypatch.setattr(
        auth_module.keyring,
        "get_password",
        lambda service, key: "sk-WORK" if key == "api-key.work" else None,
    )
    path, _ = writable_config
    _run(
        path,
        capsys,
        lambda c, a: ProfilesCommand().handle(c, a, DeepgramClient(c, a), switch="work"),
    )


def test_projects_set_default_refusal_hint(writable_config, capsys, monkeypatch):
    from deepctl_cmd_projects import command as projects_module

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def get_project(self, project_id):
            return {"project_id": project_id}

    monkeypatch.setattr(projects_module, "DeepgramClient", _Client)
    path, _ = writable_config
    _run(
        path,
        capsys,
        lambda c, a: projects_module.ProjectsCommand().handle(
            c, a, None, set_default="proj-other"
        ),
    )
