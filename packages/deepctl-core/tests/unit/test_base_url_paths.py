"""A base URL with a path prefix keeps it: the host is checked on the
origin, and requests go to origin + path."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
import yaml
from deepctl_core import hosts, output, transport
from deepctl_core.auth import AuthManager
from deepctl_core.client import DeepgramClient
from deepctl_core.config import Config

PROXY = "https://proxy.example:8443/dgproxy"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "deepctl_core.config._default_config_file",
        lambda: tmp_path / "user" / "config.yaml",
    )
    monkeypatch.setattr(hosts, "_reported", set())
    monkeypatch.setattr(transport, "_client", None)
    monkeypatch.setattr(output, "is_ai_agent", lambda: False)
    monkeypatch.setattr(output, "is_agentic", lambda: False)
    monkeypatch.setattr(output.stderr_console, "_width", 400)


def user(tmp_path, data) -> str:
    path = tmp_path / "user" / "config.yaml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(yaml.safe_dump(data))
    return str(path)


def recording(config):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"projects": []})

    auth = AuthManager(config)
    auth.client = httpx.Client(transport=httpx.MockTransport(handler))
    transport._set_client(httpx.Client(transport=httpx.MockTransport(handler)))
    return auth, seen


@pytest.mark.parametrize(
    "url",
    [PROXY, "HTTPS://Proxy.Example:8443/dgproxy/", "https://proxy.example:8443/dgproxy?x=1"],
)
def test_canonical_url_keeps_path(url):
    assert hosts.canonical_url(url) == PROXY


def test_env_source(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_BASE_URL", PROXY + "/")
    config = Config(config_path=user(tmp_path, {"profiles": {"default": {"api_key": "k"}}}))
    auth, seen = recording(config)
    assert auth.check_base_url() == PROXY
    auth.guard()
    assert seen == []  # guard() checks the host and the key, and sends nothing
    DeepgramClient(config, auth).get_projects()
    assert str(seen[0].url) == PROXY + "/v1/projects"


def test_allowlisted_profile_source(tmp_path):
    data = {
        "profiles": {"default": {"api_key": "k", "base_url": PROXY}},
        "api": {"allowed_hosts": ["proxy.example:8443"]},
    }
    config = Config(config_path=user(tmp_path, data))
    auth, seen = recording(config)
    auth.verify_credentials("k", "P1")
    assert str(seen[0].url) == PROXY + "/v1/projects/P1"


def test_allowlisted_flag_source_sdk_environment(tmp_path):
    data = {"profiles": {"default": {"api_key": "k"}}, "api": {"allowed_hosts": ["proxy.example"]}}
    config = Config(config_path=user(tmp_path, data), base_url=PROXY)
    auth, _ = recording(config)
    with patch.object(AuthManager, "guard"), patch("deepctl_core.client.DGClient") as sdk:
        DeepgramClient(config, auth)._create_client()
    env = sdk.call_args.kwargs["environment"]
    assert env.base == PROXY
    assert env.production == "wss://proxy.example:8443/dgproxy"


def test_listen_websocket_url_keeps_path(tmp_path, monkeypatch):
    from deepctl_cmd_listen.command import _ws_base

    monkeypatch.setenv("DEEPGRAM_BASE_URL", PROXY)
    config = Config(config_path=user(tmp_path, {"profiles": {"default": {"api_key": "k"}}}))
    client = MagicMock()
    client.auth_manager = AuthManager(config)
    assert _ws_base(client) == "wss://proxy.example:8443/dgproxy"


def test_profile_hint_quotes_the_profile_name():
    with pytest.raises(hosts.CredentialHostError) as err:
        hosts.evaluate(
            "http://127.0.0.1:8000", hosts.SOURCE_USER, profile="my profile", agentic=False
        )
    assert "--profile 'my profile' --save-base-url" in err.value.hint
