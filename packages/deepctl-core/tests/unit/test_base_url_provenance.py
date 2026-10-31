"""Every base_url carries its source, and credentials go only to trusted hosts.

Trusted: a regional Deepgram host, a host in the user config's
api.allowed_hosts, or (outside agent mode) a DEEPGRAM_BASE_URL from the
environment. A base_url from --base-url or the environment is never saved.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import yaml
from deepctl_core import hosts, output, transport
from deepctl_core.auth import AuthManager
from deepctl_core.base_command import BaseCommand
from deepctl_core.client import DeepgramClient
from deepctl_core.config import Config
from deepctl_core.hosts import (
    SOURCE_DEFAULT,
    SOURCE_ENV,
    SOURCE_FLAG,
    SOURCE_PROJECT,
    SOURCE_USER,
    CredentialHostError,
    evaluate,
)

FOREIGN = "https://evil.example"
_REAL_IS_AI_AGENT = output.is_ai_agent


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # On Windows tmp_path sits under the real home, so paths would show as "~\...".
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    # This test's user config is the one at the default location.
    monkeypatch.setattr(
        "deepctl_core.config._default_config_file",
        lambda: tmp_path / "user" / "config.yaml",
    )
    # In agent sessions too (location rules: test_config_location).
    monkeypatch.setattr(
        "deepctl_core.config._trust_root_location", lambda: (tmp_path, tmp_path / "user" / "config.yaml")
    )
    monkeypatch.setattr(hosts, "_reported", set())
    monkeypatch.setattr(transport, "_client", None)
    monkeypatch.setattr(output, "is_agentic", lambda: False)
    monkeypatch.setattr(output, "is_ai_agent", lambda: False)
    output._output_config.update(agentic=False, quiet=False, format="default")
    monkeypatch.setattr(output.stderr_console, "_width", 400)
    # No keyring backend: keys fall back to the config file.
    from deepctl_core import auth as auth_module

    def no_keyring(*args, **kwargs):
        raise RuntimeError("no keyring backend")

    monkeypatch.setattr(auth_module.keyring, "set_password", no_keyring)
    monkeypatch.setattr(auth_module.keyring, "get_password", lambda *a: None)
    monkeypatch.setattr(auth_module.keyring, "delete_password", no_keyring)


@pytest.fixture
def agentic(monkeypatch):
    monkeypatch.setattr(output, "is_agentic", lambda: True)
    monkeypatch.setattr(output, "is_ai_agent", lambda: True)
    output._output_config["agentic"] = True


def write_user(tmp_path, data) -> str:
    path = tmp_path / "user" / "config.yaml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(yaml.safe_dump(data))
    path.chmod(0o600)
    return str(path)


def recording_auth(config: Config) -> tuple[AuthManager, list[httpx.Request]]:
    """AuthManager whose HTTP clients (its own, and the shared one the SDK
    uses) record requests instead of sending."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"projects": []})

    auth = AuthManager(config)
    auth.client = httpx.Client(transport=httpx.MockTransport(handler))
    transport._set_client(httpx.Client(transport=httpx.MockTransport(handler)))
    return auth, seen


def send(auth: AuthManager) -> None:
    """What an authed command does: guard(), then its own request through
    the SDK. There is no separate key check."""
    auth.guard()
    DeepgramClient(auth.config, auth).get_projects()


KEYED = {"profiles": {"default": {"api_key": "sk-USERKEY"}}}


# ── Sources ──────────────────────────────────────────────────────


class TestSources:
    def test_default(self, tmp_path):
        config = Config(config_path=write_user(tmp_path, KEYED))
        assert config.base_url_source() == SOURCE_DEFAULT
        assert config.get_profile().base_url == "https://api.deepgram.com"

    def test_user_config(self, tmp_path):
        data = {"profiles": {"default": {"base_url": "https://api.eu.deepgram.com"}}}
        config = Config(config_path=write_user(tmp_path, data))
        assert config.base_url_source() == SOURCE_USER

    def test_project_file(self, tmp_path):
        (tmp_path / "deepgram.yaml").write_text(
            "profiles:\n  default:\n    base_url: https://api.au.deepgram.com\n"
        )
        config = Config(config_path=write_user(tmp_path, KEYED))
        assert config.base_url_source() == SOURCE_PROJECT

    def test_parent_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEEPGRAM_BASE_URL", "http://127.0.0.1:8080")
        config = Config(config_path=write_user(tmp_path, KEYED))
        assert config.base_url_source() == SOURCE_ENV
        assert config.base_url_override == ("http://127.0.0.1:8080", SOURCE_ENV)

    def test_flag_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEEPGRAM_BASE_URL", "http://127.0.0.1:8080")
        config = Config(config_path=write_user(tmp_path, KEYED), base_url=FOREIGN)
        assert config.get_profile().base_url == FOREIGN
        assert config.base_url_source() == SOURCE_FLAG

    def test_flag_does_not_touch_environment(self, tmp_path, monkeypatch):
        import os

        Config(config_path=write_user(tmp_path, KEYED), base_url=FOREIGN)
        assert "DEEPGRAM_BASE_URL" not in os.environ


# ── Decision matrix ──────────────────────────────────────────────


REGIONAL = "https://api.eu.deepgram.com"
LOOPBACK = "http://127.0.0.1:8000"


@pytest.mark.parametrize(
    ("url", "source", "allowed", "agent", "ok"),
    [
        (REGIONAL, SOURCE_FLAG, [], False, True),
        (REGIONAL, SOURCE_ENV, [], True, True),
        (REGIONAL, SOURCE_PROJECT, [], True, True),
        (FOREIGN, SOURCE_FLAG, [], False, False),
        (FOREIGN, SOURCE_FLAG, ["evil.example"], True, True),
        (FOREIGN, SOURCE_ENV, [], False, True),
        (FOREIGN, SOURCE_ENV, [], True, False),
        (FOREIGN, SOURCE_ENV, ["evil.example"], True, True),
        (FOREIGN, SOURCE_USER, [], False, False),
        (FOREIGN, SOURCE_USER, ["evil.example"], False, True),
        (LOOPBACK, SOURCE_FLAG, [], False, False),
        (LOOPBACK, SOURCE_ENV, [], False, True),
        (LOOPBACK, SOURCE_ENV, [], True, False),
        (LOOPBACK, SOURCE_USER, [], False, False),
        (LOOPBACK, SOURCE_USER, ["127.0.0.1"], False, True),
        (LOOPBACK, SOURCE_USER, ["127.0.0.1:8000"], False, True),
        (LOOPBACK, SOURCE_USER, ["127.0.0.1:9000"], False, False),
        ("https://localhost:8443", SOURCE_FLAG, [], False, False),
        ("https://evil.example.deepgram.com", SOURCE_FLAG, [], False, False),
        ("https://api.deepgram.com.evil.example", SOURCE_FLAG, [], False, False),
        ("https://api.deepgram.com:8443", SOURCE_FLAG, [], False, False),
        (FOREIGN, SOURCE_FLAG, ["*.example", "*"], False, False),
    ],
)
def test_decision_matrix(url, source, allowed, agent, ok):
    if ok:
        evaluate(url, source, allowed, agentic=agent)
    else:
        with pytest.raises(CredentialHostError) as err:
            evaluate(url, source, allowed, agentic=agent)
        assert err.value.code == "foreign_host"


@pytest.mark.parametrize("source", [SOURCE_FLAG, SOURCE_ENV, SOURCE_USER])
def test_insecure_non_loopback_refused(source):
    with pytest.raises(CredentialHostError) as err:
        evaluate("http://evil.example", source, ["evil.example"], agentic=False)
    assert err.value.code == "insecure_base_url"
    assert err.value.error == (
        "Refusing to use http://evil.example: API URLs must use https (plain "
        "http is allowed only for localhost, 127.0.0.0/8 and [::1])."
    )


def test_profile_error_text(tmp_path):
    with pytest.raises(CredentialHostError) as err:
        evaluate(LOOPBACK, SOURCE_USER, profile="staging", agentic=False)
    assert err.value.message_id == "E-FH-PROFILE"
    assert err.value.error == (
        'Not sending your API key to 127.0.0.1: profile "staging" points to a '
        "host that is not a Deepgram API host."
    )
    # The hint names the default user config and ends with the
    # exact command, without a trailing period.
    assert err.value.hint == (
        "If you trust 127.0.0.1, add it to api.allowed_hosts in "
        f"{tmp_path / 'user' / 'config.yaml'}. To switch back to Deepgram, run: "
        "dg --base-url https://api.deepgram.com login --profile staging "
        "--save-base-url --force-write"
    )


def test_flag_error_text(tmp_path):
    with pytest.raises(CredentialHostError) as err:
        evaluate(FOREIGN, SOURCE_FLAG, agentic=False)
    assert err.value.message_id == "E-FH-FLAG"
    assert err.value.error == (
        "Not sending your API key to evil.example: it is not a Deepgram API host."
    )
    assert err.value.hint == (
        "If you trust evil.example, add it to api.allowed_hosts in "
        f"{tmp_path / 'user' / 'config.yaml'}, or export DEEPGRAM_BASE_URL in "
        "your shell."
    )


AGENT_HINT = (
    "A person must approve evil.example in the deepctl user config. If you are "
    "an AI agent, ask the user."
)


@pytest.mark.parametrize("source", [SOURCE_FLAG, SOURCE_ENV, SOURCE_USER])
def test_agent_hint_names_no_flag_variable_or_path(source):
    with pytest.raises(CredentialHostError) as err:
        evaluate(FOREIGN, source, agentic=True)
    hint = err.value.hint
    assert hint == AGENT_HINT
    for banned in ("--", "DEEPGRAM_BASE_URL", "config.yaml"):
        assert banned not in hint


@pytest.mark.parametrize("signal", ["CI", "--agent-friendly"])
def test_agent_rendering_gets_agent_hint(signal, monkeypatch, tmp_path, capsys):
    """Output in agent mode (CI, --agent-friendly, soft signals) never teaches
    the env-var bypass, although trust follows hard markers only."""
    monkeypatch.setattr(output, "is_agentic", lambda: True)
    output._output_config["agentic"] = True
    with pytest.raises(CredentialHostError) as err:
        evaluate(FOREIGN, SOURCE_FLAG)
    assert err.value.hint == AGENT_HINT
    hosts.report(err.value)
    data = {**KEYED}
    monkeypatch.setenv("DEEPGRAM_BASE_URL", "https://proxy.example")
    config = Config(config_path=write_user(tmp_path, data))
    auth, seen = recording_auth(config)
    send(auth)  # sent: CI is not an AI-agent marker
    assert seen
    captured = capsys.readouterr()
    for text in (captured.err, captured.out):
        assert "DEEPGRAM_BASE_URL in your shell" not in text
        assert "config.yaml" not in text


def test_untrusted_text_is_sanitized():
    with pytest.raises(CredentialHostError) as err:
        evaluate("https://ev\x1b[31mil.example", SOURCE_FLAG, agentic=False)
    assert "\x1b" not in err.value.error


# ── Every path that attaches a credential ────────────────────────


class TestCredentialPaths:
    def test_command_path_refuses_flag_host(self, tmp_path, capsys):
        config = Config(config_path=write_user(tmp_path, KEYED), base_url=FOREIGN)
        auth, seen = recording_auth(config)
        with pytest.raises(CredentialHostError):
            send(auth)
        assert seen == []
        err = capsys.readouterr().err
        assert err.count("Not sending your API key to evil.example") == 1

    def test_verify_api_key_refuses(self, tmp_path):
        config = Config(config_path=write_user(tmp_path, KEYED), base_url=FOREIGN)
        auth, seen = recording_auth(config)
        with pytest.raises(CredentialHostError):
            auth.verify_api_key("sk-USERKEY")
        with pytest.raises(CredentialHostError):
            auth.verify_credentials("sk-USERKEY", "P1")
        assert seen == []

    def test_project_check_uses_the_checked_base_url(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEEPGRAM_BASE_URL", "http://127.0.0.1:8080")
        config = Config(config_path=write_user(tmp_path, KEYED))
        auth, seen = recording_auth(config)
        auth.verify_credentials("sk-USERKEY", "P1")
        assert [str(r.url) for r in seen] == ["http://127.0.0.1:8080/v1/projects/P1"]

    def test_command_path_sends_to_trusted_flag_host(self, tmp_path):
        data = {**KEYED, "api": {"allowed_hosts": ["evil.example"]}}
        config = Config(config_path=write_user(tmp_path, data), base_url=FOREIGN)
        auth, seen = recording_auth(config)
        send(auth)
        assert [r.url.host for r in seen] == ["evil.example"]
        assert seen[0].headers["Authorization"] == "Token sk-USERKEY"

    def test_sdk_environment_refused(self, tmp_path):
        config = Config(config_path=write_user(tmp_path, KEYED), base_url=FOREIGN)
        auth, _ = recording_auth(config)
        with (
            patch.object(AuthManager, "guard"),
            patch("deepctl_core.client.DGClient") as sdk,
        ):
            with pytest.raises(CredentialHostError):
                DeepgramClient(config, auth)._create_client()
        sdk.assert_not_called()

    def test_sdk_environment_uses_checked_url(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEEPGRAM_BASE_URL", "http://127.0.0.1:8080")
        config = Config(config_path=write_user(tmp_path, KEYED))
        auth, _ = recording_auth(config)
        with (
            patch.object(AuthManager, "guard"),
            patch("deepctl_core.client.DGClient") as sdk,
        ):
            DeepgramClient(config, auth)._create_client()
        env = sdk.call_args.kwargs["environment"]
        assert env.base == "http://127.0.0.1:8080"
        assert env.production == "ws://127.0.0.1:8080"

    def test_base_command_reports_once_with_code(self, tmp_path, capsys):
        class Probe(BaseCommand):
            name = "probe"
            help = "probe"
            requires_auth = True

            def handle(self, config, auth_manager, client, **kwargs):
                raise AssertionError("must not run")

        import click

        output._output_config["format"] = "json"
        config = Config(config_path=write_user(tmp_path, KEYED), base_url=FOREIGN)
        ctx = click.Context(click.Command("probe"), obj={"config": config})
        with pytest.raises(SystemExit) as exc:
            Probe().execute(ctx)
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert '"code": "foreign_host"' in captured.out
        assert '"hint":' in captured.out
        assert captured.err.count("Not sending your API key") == 1


# ── Who can approve a host ───────────────────────────────────────


class TestWhoCanApproveAHost:
    def test_project_file_allowlist_does_not_trust_flag_host(self, tmp_path):
        """A repository's deepgram.yaml can't approve its own host."""
        (tmp_path / "deepgram.yaml").write_text(
            "api:\n  allowed_hosts: [evil.example]\n"
        )
        config = Config(config_path=write_user(tmp_path, KEYED), base_url=FOREIGN)
        auth, seen = recording_auth(config)
        with pytest.raises(CredentialHostError) as err:
            send(auth)
        assert err.value.code == "foreign_host"
        assert seen == []

    def test_saved_profile_base_url_refused(self, tmp_path):
        """A loopback base_url saved by 0.4.0 gets no credentials."""
        data = {"profiles": {"default": {"api_key": "sk-U", "base_url": LOOPBACK}}}
        config = Config(config_path=write_user(tmp_path, data))
        auth, seen = recording_auth(config)
        with pytest.raises(CredentialHostError) as err:
            send(auth)
        assert err.value.message_id == "E-FH-PROFILE"
        assert seen == []

    def test_saved_profile_base_url_allowlisted(self, tmp_path):
        data = {
            "profiles": {"default": {"api_key": "sk-U", "base_url": LOOPBACK}},
            "api": {"allowed_hosts": ["127.0.0.1"]},
        }
        config = Config(config_path=write_user(tmp_path, data))
        auth, seen = recording_auth(config)
        send(auth)
        assert len(seen) == 1

    def test_agent_env_host_refused(self, tmp_path, monkeypatch, agentic):
        """In agent mode, an env base URL must be regional or allowlisted."""
        monkeypatch.setenv("DEEPGRAM_BASE_URL", FOREIGN)
        config = Config(config_path=write_user(tmp_path, KEYED))
        auth, seen = recording_auth(config)
        with pytest.raises(CredentialHostError) as err:
            send(auth)
        assert err.value.code == "foreign_host"
        assert seen == []

    def test_agent_env_host_allowlisted(self, tmp_path, monkeypatch, agentic):
        monkeypatch.setenv("DEEPGRAM_BASE_URL", FOREIGN)
        data = {**KEYED, "api": {"allowed_hosts": ["evil.example"]}}
        config = Config(config_path=write_user(tmp_path, data))
        auth, seen = recording_auth(config)
        send(auth)
        assert seen[0].headers["Authorization"] == "Token sk-USERKEY"

    def test_non_agent_loopback_env_allowed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEEPGRAM_BASE_URL", "http://127.0.0.1:8080")
        config = Config(config_path=write_user(tmp_path, KEYED))
        auth, seen = recording_auth(config)
        send(auth)
        assert seen[0].url.host == "127.0.0.1"
        assert seen[0].headers["Authorization"] == "Token sk-USERKEY"


# ── Persistence ──────────────────────────────────────────────────


class TestPersistence:
    @pytest.mark.parametrize("via", ["flag", "env"])
    def test_login_does_not_save_base_url(self, tmp_path, monkeypatch, via):
        path = write_user(tmp_path, {})
        if via == "env":
            monkeypatch.setenv("DEEPGRAM_BASE_URL", "http://127.0.0.1:8000")
            config = Config(config_path=path)
        else:
            data = {"api": {"allowed_hosts": ["127.0.0.1"]}}
            path = write_user(tmp_path, data)
            config = Config(config_path=path, base_url="http://127.0.0.1:8000")
        auth, seen = recording_auth(config)
        auth.login_with_api_key("sk-LOGIN")
        assert len(seen) == 1  # verification went to the host in use
        text = Path(path).read_text()
        assert "127.0.0.1:8000" not in text
        assert "sk-LOGIN" in text  # fail keyring -> file fallback
        monkeypatch.delenv("DEEPGRAM_BASE_URL", raising=False)
        assert Config(config_path=path).get_profile().base_url == (
            "https://api.deepgram.com"
        )

    def test_approved_base_url_is_saved(self, tmp_path):
        path = write_user(tmp_path, KEYED)
        config = Config(config_path=path, base_url="https://staging.example")
        config.approve_base_url("default", "https://staging.example")
        config.add_allowed_host("staging.example")
        config.save()
        saved = yaml.safe_load(Path(path).read_text())
        assert saved["profiles"]["default"]["base_url"] == "https://staging.example"
        assert saved["api"]["allowed_hosts"] == ["staging.example"]

    def test_existing_user_base_url_kept(self, tmp_path):
        data = {"profiles": {"default": {"api_key": "k", "base_url": REGIONAL}}}
        path = write_user(tmp_path, data)
        config = Config(config_path=path, base_url=FOREIGN)
        config.create_profile("default", api_key="k2", project_id="p")
        saved = yaml.safe_load(Path(path).read_text())
        assert saved["profiles"]["default"]["base_url"] == REGIONAL
        assert config.get_profile().base_url == FOREIGN  # this run only


# ── Hard AI-agent signals only ───────────────────────────────────


@pytest.fixture
def real_agent_detection(monkeypatch):
    """Use the real is_ai_agent() with a clean environment."""
    from deepctl_core.output import AI_AGENT_ENV_MARKERS

    for name in (*AI_AGENT_ENV_MARKERS, "OR_APP_NAME", "OR_SITE_URL", "CI", "TERM"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(output, "is_ai_agent", _REAL_IS_AI_AGENT)


class TestHardAgentSignals:
    def test_ci_and_missing_tty_are_not_agent_signals(
        self, real_agent_detection, monkeypatch
    ):
        monkeypatch.setenv("CI", "true")
        assert output.is_ai_agent() is False

    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("AI_AGENT", "1"),
            ("CLAUDECODE", "1"),
            ("CLAUDE_CODE_ENTRYPOINT", "cli"),
            ("CODEX_SANDBOX", "seatbelt"),
            ("CODEX_SANDBOX_NETWORK_DISABLED", "1"),
            ("CURSOR_AGENT", "1"),
            ("GEMINI_CLI", "1"),
            ("OPENCODE", "1"),
            ("OPENCODE_CLIENT", "cli"),
            ("CLAUDE_CODE", "1"),
            ("CODEX_CI", "1"),
            ("CODEX_THREAD_ID", "thread-1"),
            ("AUGMENT_AGENT", "1"),
            ("ANTIGRAVITY_AGENT", "1"),
            ("CURSOR_EXTENSION_HOST_ROLE", "agent-exec"),
            ("CLAUDE_CODE_IS_COWORK", "1"),
            ("COPILOT_CLI", "1"),
            ("GOOSE_TERMINAL", "1"),
            ("AGENT", "amp"),
            ("AGENT", "goose"),
            ("OR_APP_NAME", "Aider"),
            ("OR_SITE_URL", "https://aider.chat"),
        ],
    )
    def test_agent_markers(self, real_agent_detection, monkeypatch, name, value):
        monkeypatch.setenv(name, value)
        assert output.is_ai_agent() is True

    @pytest.mark.parametrize(
        ("name", "value"),
        [
            # A person's own shell in these tools sets them too.
            ("CURSOR_EXTENSION_HOST_ROLE", "user"),
            ("AGENT", "someone"),
            ("CURSOR_TRACE_ID", "abc"),
            ("REPL_ID", "abc"),
        ],
    )
    def test_not_agent_markers(self, real_agent_detection, monkeypatch, name, value):
        monkeypatch.setenv(name, value)
        assert output.is_ai_agent() is False

    @pytest.mark.parametrize(
        ("name", "value"), [("OPENCODE", "1"), ("OPENCODE_CLIENT", "cli")]
    )
    def test_opencode_env_host_refused(
        self, tmp_path, monkeypatch, real_agent_detection, name, value
    ):
        """An OpenCode session's DEEPGRAM_BASE_URL never gets the API key
        unless it is regional or approved in the user config."""
        monkeypatch.setenv(name, value)
        monkeypatch.setenv("DEEPGRAM_BASE_URL", FOREIGN)
        config = Config(config_path=write_user(tmp_path, KEYED))
        auth, seen = recording_auth(config)
        with pytest.raises(CredentialHostError) as err:
            auth.guard()
        assert err.value.code == "foreign_host"
        assert seen == []

    def test_ci_proxy_env_host_sent_with_one_warning(
        self, tmp_path, monkeypatch, real_agent_detection, capsys
    ):
        monkeypatch.setenv("CI", "true")
        monkeypatch.setenv("DEEPGRAM_BASE_URL", "https://proxy.example")
        config = Config(config_path=write_user(tmp_path, KEYED))
        auth, seen = recording_auth(config)
        send(auth)
        send(auth)
        assert [r.url.host for r in seen] == ["proxy.example", "proxy.example"]
        assert seen[0].headers["Authorization"] == "Token sk-USERKEY"
        err = capsys.readouterr().err
        assert (
            err.count(
                "Sending your API key to proxy.example (from DEEPGRAM_BASE_URL), "
                "which is not a Deepgram API host."
            )
            == 1
        )

    def test_ci_proxy_env_host_refused_for_agent(
        self, tmp_path, monkeypatch, real_agent_detection
    ):
        monkeypatch.setenv("CI", "true")
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setenv("DEEPGRAM_BASE_URL", "https://proxy.example")
        config = Config(config_path=write_user(tmp_path, KEYED))
        auth, seen = recording_auth(config)
        with pytest.raises(CredentialHostError) as err:
            send(auth)
        assert err.value.code == "foreign_host"
        assert seen == []

    def test_cron_like_env_host_allowed_with_warning(
        self, tmp_path, monkeypatch, real_agent_detection, capsys
    ):
        monkeypatch.setenv("DEEPGRAM_BASE_URL", "https://selfhosted.example")
        config = Config(config_path=write_user(tmp_path, KEYED))
        auth, seen = recording_auth(config)
        send(auth)
        assert seen[0].headers["Authorization"] == "Token sk-USERKEY"
        assert capsys.readouterr().err.count("(from DEEPGRAM_BASE_URL)") == 1

    def test_regional_or_allowlisted_env_host_no_warning(
        self, tmp_path, monkeypatch, real_agent_detection, capsys
    ):
        monkeypatch.setenv("DEEPGRAM_BASE_URL", "https://proxy.example")
        data = {**KEYED, "api": {"allowed_hosts": ["proxy.example"]}}
        config = Config(config_path=write_user(tmp_path, data))
        auth, _ = recording_auth(config)
        send(auth)
        assert "(from DEEPGRAM_BASE_URL)" not in capsys.readouterr().err


# ── --config outside the default location ────────────────────────


class TestCustomConfigFile:
    REPO_FILE = {
        "api": {"allowed_hosts": ["127.0.0.1:8000"]},
        "profiles": {"default": {"base_url": "http://127.0.0.1:8000"}},
        "tools": {"ffprobe_path": "/repo/ffprobe"},
    }

    def repo_file(self, tmp_path, data) -> str:
        path = tmp_path / "repo" / "dg.yaml"
        path.parent.mkdir(exist_ok=True)
        path.write_text(yaml.safe_dump(data))
        return str(path)

    def test_default_path_widens_trust(self, tmp_path):
        data = {
            **self.REPO_FILE,
            "profiles": {
                "default": {"api_key": "sk-U", "base_url": "http://127.0.0.1:8000"}
            },
        }
        config = Config(config_path=write_user(tmp_path, data))
        assert config.trusted_file is True
        assert config.allowed_hosts() == ["127.0.0.1:8000"]
        assert config.get_profile().base_url == "http://127.0.0.1:8000"
        assert config.get("tools.ffprobe_path") == "/repo/ffprobe"
        auth, seen = recording_auth(config)
        send(auth)
        assert seen[0].url.host == "127.0.0.1"

    def test_other_path_cannot_widen_trust(self, tmp_path, agentic, monkeypatch):
        """secr4/configflag.sh: agent mode, env key, --config repo file."""
        monkeypatch.setenv("DEEPGRAM_API_KEY", "sk-ENVSENTINEL")
        from deepctl_core import config as config_module

        monkeypatch.setattr(config_module, "_pending_notices", [])
        monkeypatch.setattr(config_module, "_seen_notices", set())
        config = Config(config_path=self.repo_file(tmp_path, self.REPO_FILE))
        assert config.trusted_file is False
        assert config.allowed_hosts() == []
        assert config.get_profile().base_url == "https://api.deepgram.com"
        assert config.get("tools.ffprobe_path") is None
        texts = [t for _, t, _, _ in config_module._pending_notices]
        hints = [h for _, _, h, _ in config_module._pending_notices]
        repo_file = tmp_path / "repo" / "dg.yaml"
        assert (
            f"Ignored api.allowed_hosts in {repo_file}: only the default user "
            "config can approve hosts for your API key."
        ) in texts
        assert (
            f"Ignored profiles.default.base_url in {repo_file}: a --config file "
            "can only select a Deepgram regional API URL (http://127.0.0.1:8000 "
            "is not one)."
        ) in texts
        assert (
            f"Ignored tools.ffprobe_path in {repo_file}: only the default user "
            "config can set tool paths."
        ) in texts
        assert not any(str(repo_file) in (h or "") for h in hints)
        auth, seen = recording_auth(config)
        send(auth)
        assert [r.url.host for r in seen] == ["api.deepgram.com"]

    def test_other_path_keeps_credentials_and_regional_url(self, tmp_path):
        data = {
            "profiles": {
                "default": {
                    "api_key": "sk-FILEKEY",
                    "base_url": "https://api.eu.deepgram.com",
                }
            }
        }
        config = Config(config_path=self.repo_file(tmp_path, data))
        assert config.get_profile().api_key == "sk-FILEKEY"
        assert config.get_profile().base_url == "https://api.eu.deepgram.com"

    def test_other_path_flag_host_still_foreign(self, tmp_path):
        config = Config(
            config_path=self.repo_file(tmp_path, self.REPO_FILE),
            base_url="http://127.0.0.1:8000",
        )
        auth, seen = recording_auth(config)
        with pytest.raises(CredentialHostError):
            send(auth)
        assert seen == []


# ── Device-login identity host ───────────────────────────────────


class TestIdentityHost:
    def test_default(self, tmp_path):
        config = Config(config_path=write_user(tmp_path, {}))
        assert hosts.check_identity_host(config) == "https://id.dx.deepgram.com"

    def test_env_loopback_allowed_with_one_warning(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("DEEPGRAM_CLI_BASE_URL", "http://127.0.0.1:9000/")
        config = Config(config_path=write_user(tmp_path, {}))
        assert hosts.check_identity_host(config) == "http://127.0.0.1:9000"
        hosts.check_identity_host(config)
        assert (
            capsys.readouterr().err.count(
                "Logging in through 127.0.0.1 (from DEEPGRAM_CLI_BASE_URL), which is not "
                "a Deepgram login host."
            )
            == 1
        )

    def test_env_http_non_loopback_refused(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEEPGRAM_CLI_BASE_URL", "http://id.evil.example")
        config = Config(config_path=write_user(tmp_path, {}))
        with pytest.raises(CredentialHostError) as err:
            hosts.check_identity_host(config)
        assert err.value.code == "insecure_base_url"
        assert "DEEPGRAM_CLI_BASE_URL" in err.value.hint

    def test_agent_env_host_refused(self, tmp_path, monkeypatch, agentic):
        monkeypatch.setenv("DEEPGRAM_CLI_BASE_URL", "https://id.evil.example")
        config = Config(config_path=write_user(tmp_path, {}))
        with pytest.raises(CredentialHostError) as err:
            hosts.check_identity_host(config)
        assert err.value.code == "foreign_host"

    def test_agent_env_host_allowlisted(self, tmp_path, monkeypatch, agentic):
        monkeypatch.setenv("DEEPGRAM_CLI_BASE_URL", "https://id.staging.example")
        data = {"api": {"allowed_hosts": ["id.staging.example"]}}
        config = Config(config_path=write_user(tmp_path, data))
        assert hosts.check_identity_host(config) == "https://id.staging.example"

    def test_device_login_refused_before_any_request(
        self, tmp_path, monkeypatch, agentic
    ):
        monkeypatch.setenv("DEEPGRAM_CLI_BASE_URL", "https://id.evil.example")
        config = Config(config_path=write_user(tmp_path, {}))
        auth, seen = recording_auth(config)
        with pytest.raises(CredentialHostError):
            auth.login_with_device_flow()
        assert seen == []

    def test_device_login_uses_checked_url(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEEPGRAM_CLI_BASE_URL", "http://127.0.0.1:9000")
        config = Config(config_path=write_user(tmp_path, {}))
        auth, seen = recording_auth(config)
        with pytest.raises(Exception):
            auth._request_device_code()
        assert str(seen[0].url) == "http://127.0.0.1:9000/device/code"


# ── Normalized URLs ────────────────────────────────────────────────


class TestNormalizedUrl:
    @pytest.mark.parametrize(
        "flag",
        [
            "HTTPS://API.DEEPGRAM.COM",
            "https://api.deepgram.com.",
            "HTTPS://Api.EU.Deepgram.Com/",
        ],
    )
    def test_checked_url_is_the_url_used(self, tmp_path, flag):
        config = Config(config_path=write_user(tmp_path, KEYED), base_url=flag)
        auth, seen = recording_auth(config)
        base = auth.check_base_url()
        assert base == base.lower()
        assert base.startswith("https://api.")
        assert not base.endswith(".")
        send(auth)
        assert seen[0].url.host.startswith("api.")
        with (
            patch.object(AuthManager, "guard"),
            patch("deepctl_core.client.DGClient") as sdk,
        ):
            DeepgramClient(config, auth)._create_client()
        env = sdk.call_args.kwargs.get("environment")
        if env is not None:
            assert env.base == base
            assert env.production == base.replace("https://", "wss://")

    def test_split_base_url_is_scheme_insensitive(self):
        from deepctl_core.client import _split_base_url

        assert _split_base_url("HTTP://localhost:8080") == (
            "http",
            "ws",
            "localhost:8080",
        )
        assert _split_base_url("HTTPS://h.example") == ("https", "wss", "h.example")

    def test_project_file_value_stored_normalized(self, tmp_path):
        (tmp_path / "deepgram.yaml").write_text(
            "profiles:\n  default:\n    base_url: HTTPS://API.EU.DEEPGRAM.COM/\n"
        )
        config = Config(config_path=write_user(tmp_path, KEYED))
        assert config.get_profile().base_url == "https://api.eu.deepgram.com"


class TestAllowlistEntries:
    @pytest.mark.parametrize(
        "entry",
        [
            "proxy.example",
            ["proxy.example"],
            ["https://proxy.example"],
            ["PROXY.example/"],
        ],
    )
    def test_entry_forms(self, tmp_path, entry, recwarn):
        data = {**KEYED, "api": {"allowed_hosts": entry}}
        config = Config(
            config_path=write_user(tmp_path, data), base_url="https://proxy.example"
        )
        assert config.allowed_hosts() == ["proxy.example"]
        auth, seen = recording_auth(config)
        send(auth)
        assert seen
        assert not [w for w in recwarn if "serializer" in str(w.message).lower()]

    def test_entry_with_scheme_and_port(self, tmp_path):
        data = {**KEYED, "api": {"allowed_hosts": ["http://127.0.0.1:8000"]}}
        config = Config(config_path=write_user(tmp_path, data), base_url=LOOPBACK)
        assert config.allowed_hosts() == ["127.0.0.1:8000"]
        auth, seen = recording_auth(config)
        send(auth)
        assert seen

    def test_scheme_entry_does_not_match_host_named_https(self):
        origin = hosts.parse_origin("https://https")
        assert origin is not None
        assert not hosts.is_allowlisted(origin, ["https://proxy.example"])


def test_custom_config_reads_allowlist_from_default_file(tmp_path):
    """A --config user approves hosts in the default user config."""
    write_user(tmp_path, {"api": {"allowed_hosts": ["h.example"]}})
    other = tmp_path / "dot" / "dg.yaml"
    other.parent.mkdir()
    other.write_text(yaml.safe_dump({**KEYED, "api": {"allowed_hosts": ["x.example"]}}))
    config = Config(config_path=str(other), base_url="https://h.example")
    assert config.allowed_hosts() == ["h.example"]
    auth, seen = recording_auth(config)
    send(auth)
    assert seen[0].url.host == "h.example"


def test_custom_config_hint_names_default_file(tmp_path, capsys):
    other = tmp_path / "dot" / "dg.yaml"
    other.parent.mkdir()
    other.write_text(yaml.safe_dump(KEYED))
    config = Config(config_path=str(other), base_url=FOREIGN)
    auth, _ = recording_auth(config)
    with pytest.raises(CredentialHostError) as err:
        send(auth)
    assert str(tmp_path / "user" / "config.yaml") in err.value.hint
    assert str(other) not in err.value.hint
