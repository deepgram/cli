"""deepctl_core.credentials: one resolver, 0.4.2's precedence."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml
from deepctl_core import auth as auth_module
from deepctl_core import config as config_module
from deepctl_core import credentials, hosts, output, transport
from deepctl_core.auth import AuthManager, AuthRequiredError
from deepctl_core.config import Config

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="XDG layout (Linux)"
)

KEYRING_SERVICE = auth_module.KEYRING_SERVICE


class FakeKeyring:
    """A dict-backed keyring; ``hang`` makes reads block until released."""

    def __init__(self, entries: dict[str, str] | None = None) -> None:
        self.entries = dict(entries or {})
        self.reads: list[str] = []
        self.deletes: list[str] = []
        self.hang: threading.Event | None = None

    def get_password(self, service: str, username: str) -> str | None:
        assert service == KEYRING_SERVICE
        self.reads.append(username)
        if self.hang is not None:
            self.hang.wait(10)
        return self.entries.get(username)

    def delete_password(self, service: str, username: str) -> None:
        self.deletes.append(username)
        self.entries.pop(username, None)

    def set_password(self, service: str, username: str, value: str) -> None:
        self.entries[username] = value


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "_pending_notices", [])
    monkeypatch.setattr(config_module, "_seen_notices", set())
    monkeypatch.setattr(hosts, "_reported", set())
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(config_module, "_real_home", lambda: str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    for name in output.AI_AGENT_ENV_MARKERS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(output, "is_ai_agent", lambda: False)
    monkeypatch.setattr(transport, "prog", lambda: "dg")
    return home


@pytest.fixture
def kr(monkeypatch) -> FakeKeyring:
    fake = FakeKeyring()
    monkeypatch.setattr(auth_module, "keyring", fake, raising=False)
    return fake


def write_config(home: Path, data: dict[str, Any], mode: int = 0o600) -> Path:
    directory = home / ".config" / "deepctl"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    os.chmod(path, mode)
    return path


# ── 0.4.2's get_api_key(), verbatim in substance, as the golden ─────────


def golden_042(config: Config, explicit: str | None, kr: FakeKeyring) -> str | None:
    if explicit:
        return explicit
    profile_name = config.profile or "default"
    current_profile = config.get_profile(profile_name)
    expired, _ = credentials.credential_expiry(config, profile_name)
    if not expired:
        api_key = kr.entries.get(f"api-key.{profile_name}")
        if api_key:
            return api_key
        if current_profile.api_key:
            return current_profile.api_key
    return os.getenv("DEEPGRAM_API_KEY") or None


def whoami_source_042(config: Config, key: str | None, kr: FakeKeyring) -> str:
    """`dg whoami`'s key_source rule (0.4.2), plus the flag."""
    if key is None:
        return credentials.KEY_SOURCE_NONE
    name = config.profile or "default"
    if kr.entries.get(f"api-key.{name}") == key:
        return credentials.KEY_SOURCE_KEYRING
    env = os.getenv("DEEPGRAM_API_KEY")
    if config.get_profile(name).api_key == key:
        return credentials.KEY_SOURCE_ENV if env == key else credentials.KEY_SOURCE_FILE
    return credentials.KEY_SOURCE_ENV


PROFILE_SOURCES = ("none", "--profile", "DEEPGRAM_PROFILE", "user-file default")


@pytest.mark.parametrize("profile_source", PROFILE_SOURCES)
@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize("keyring_set", [False, True])
@pytest.mark.parametrize("file_set", [False, True])
@pytest.mark.parametrize("env_set", [False, True])
def test_precedence_matches_042(
    home, kr, monkeypatch, profile_source, flag, keyring_set, file_set, env_set
):
    profile = "default" if profile_source == "none" else "work"
    data: dict[str, Any] = {"profiles": {}}
    if file_set:
        data["profiles"][profile] = {"api_key": "f" * 39 + "1"}
    if profile_source == "user-file default":
        data["default_profile"] = "work"
    write_config(home, data)
    if keyring_set:
        kr.entries[f"api-key.{profile}"] = "f" * 39 + "2"
    if env_set:
        monkeypatch.setenv("DEEPGRAM_API_KEY", "f" * 39 + "3")
    if profile_source == "DEEPGRAM_PROFILE":
        monkeypatch.setenv("DEEPGRAM_PROFILE", "work")
    config = Config(profile="work" if profile_source == "--profile" else None)
    explicit = "f" * 39 + "4" if flag else None

    resolved = AuthManager(config, explicit).resolve()

    want = golden_042(config, explicit, kr)
    assert resolved.api_key == want
    assert resolved.profile == profile
    assert resolved.profile_source == profile_source
    if flag:
        assert resolved.key_source == credentials.KEY_SOURCE_FLAG
        assert kr.reads == []  # --api-key never reads the keyring
    else:
        assert resolved.key_source == whoami_source_042(config, want, kr)


def test_keyring_read_once_per_run(home, kr, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "f" * 40)
    auth = AuthManager(Config())
    auth.guard()
    auth.get_api_key()
    auth.get_credential_source()
    auth.has_profile_credentials()
    auth.is_authenticated()
    auth.get_project_id()
    assert transport._key_source(auth) == credentials.KEY_SOURCE_ENV
    assert kr.reads == ["api-key.default"]


def test_login_forgets_the_cached_answer(home, kr, monkeypatch):
    auth = AuthManager(Config())
    assert auth.get_api_key() is None
    monkeypatch.setattr(auth, "verify_credentials", lambda *a: (True, "ok", None))
    auth.login_with_api_key("f" * 40)
    assert auth.get_api_key() == "f" * 40


def test_project_id_precedence(home, kr, monkeypatch):
    write_config(home, {"profiles": {"default": {"project_id": "p-file"}}})
    monkeypatch.setenv("DEEPGRAM_PROJECT_ID", "p-env")
    # Config merges the env value over the file's, as in 0.4.2.
    assert AuthManager(Config()).get_project_id() == "p-env"
    assert AuthManager(Config(), None, "p-flag").get_project_id() == "p-flag"
    monkeypatch.delenv("DEEPGRAM_PROJECT_ID")
    assert AuthManager(Config()).get_project_id() == "p-file"
    assert kr.reads == []  # project questions don't read the keyring


# ── Where the credentials line says they came from ─────────────────────


def test_env_key_is_reported_as_the_environments(home, kr, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "f" * 40)
    auth = AuthManager(Config())
    # 0.4.2 said "profile 'default'": Config merges the env key into the profile.
    assert auth.get_credential_source() == "environment variables"
    assert auth.has_profile_credentials() == (True, False)  # 0.4.2's answer


def test_stored_key_is_reported_as_the_profiles(home, kr, monkeypatch):
    kr.entries["api-key.default"] = "f" * 40
    monkeypatch.setenv("DEEPGRAM_API_KEY", "e" * 40)
    assert AuthManager(Config()).get_credential_source() == "profile 'default'"
    write_config(home, {"profiles": {"default": {"api_key": "d" * 40}}})
    kr.entries.clear()
    monkeypatch.delenv("DEEPGRAM_API_KEY")
    assert AuthManager(Config()).get_credential_source() == "profile 'default'"
    assert AuthManager(Config(), "c" * 40).get_credential_source() == "explicit flags"


# ── Trust cells and key expiry ────────────────────────────────


@pytest.mark.parametrize("mode", [0o644, 0o666, 0o660])
@pytest.mark.parametrize("agent", [False, True])
def test_trust_cells(home, kr, monkeypatch, mode, agent):
    monkeypatch.setattr(output, "is_ai_agent", lambda: agent)
    write_config(home, {"profiles": {"default": {"api_key": "f" * 40}}}, mode)
    resolved = AuthManager(Config()).resolve()
    writable = bool(mode & 0o022)
    if agent and writable:
        assert resolved.api_key is None
        assert resolved.untrusted_source == "~/.config/deepctl/config.yaml"
        with pytest.raises(AuthRequiredError) as err:
            AuthManager(Config()).guard()
        assert err.value.messages == transport.KEY_UNTRUSTED
    else:
        assert resolved.api_key == "f" * 40
        assert resolved.key_source == credentials.KEY_SOURCE_FILE
        assert resolved.untrusted_source is None


def test_untrusted_file_with_env_key_uses_env(home, kr, monkeypatch):
    monkeypatch.setattr(output, "is_ai_agent", lambda: True)
    write_config(home, {"profiles": {"default": {"api_key": "f" * 40}}}, 0o666)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "e" * 40)
    resolved = AuthManager(Config()).resolve()
    assert resolved.api_key == "e" * 40
    assert resolved.key_source == credentials.KEY_SOURCE_ENV


def test_sf1_untrusted_expiry_never_deletes_keyring(home, kr, monkeypatch):
    monkeypatch.setattr(output, "is_ai_agent", lambda: True)
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    write_config(
        home,
        {"profiles": {"default": {"api_key": "f" * 40, "dg_token_expires_at": past}}},
        0o666,
    )
    kr.entries["api-key.default"] = "k" * 40
    AuthManager(Config()).resolve()
    assert kr.deletes == []
    assert kr.entries["api-key.default"] == "k" * 40


def test_trusted_expiry_still_deletes_keyring(home, kr):
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    write_config(home, {"profiles": {"default": {"dg_token_expires_at": past}}})
    kr.entries["api-key.default"] = "k" * 40
    with pytest.raises(auth_module.AuthenticationError, match="expired"):
        AuthManager(Config()).get_api_key()
    assert kr.deletes == ["api-key.default"]


def test_introspection_never_prunes(home, kr, monkeypatch):
    """Whoami and login's checks stay read-only, as in 0.4.2."""
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    write_config(home, {"profiles": {"default": {"dg_token_expires_at": past}}})
    kr.entries["api-key.default"] = "k" * 40
    monkeypatch.setenv("DEEPGRAM_API_KEY", "e" * 40)
    auth = AuthManager(Config())
    assert auth.has_profile_credentials() == (False, False)
    assert auth.has_profile_credentials("default") == (False, False)
    assert auth.is_authenticated() is True
    assert kr.deletes == []
    # A command that uses the key prunes it, once (0.4.2's get_api_key).
    assert auth.get_api_key() == "e" * 40
    assert auth.get_api_key() == "e" * 40
    assert kr.deletes == ["api-key.default"]


def test_one_resolution_per_profile_spelling(home, kr):
    kr.entries["api-key.default"] = "k" * 40
    auth = AuthManager(Config())
    auth.resolve(profile="default", use_flags=False, prune=False)
    auth.resolve()
    auth.has_profile_credentials()
    assert kr.reads == ["api-key.default"]


# ── Keyring bound ───────────────────────────────────────────────────────


def test_keyring_bound_falls_back_once(home, kr, monkeypatch, capsys):
    # A 2 s bound: the first read waits it once, a second wait would show.
    monkeypatch.setattr(credentials, "KEYRING_BOUND_S", 2.0)
    monkeypatch.setattr(credentials, "_interactive", lambda: False)
    monkeypatch.setattr(output.stderr_console, "_width", 400)
    kr.entries["api-key.default"] = "k" * 40
    kr.hang = threading.Event()
    monkeypatch.setenv("DEEPGRAM_API_KEY", "e" * 40)
    try:
        started = time.monotonic()
        assert AuthManager(Config()).get_api_key() == "e" * 40
        assert time.monotonic() - started < 6.0
        # Cached for the rest of the process: no second wait, no second line.
        started = time.monotonic()
        assert AuthManager(Config()).get_api_key() == "e" * 40
        assert time.monotonic() - started < 1.0
    finally:
        kr.hang.set()
    err = capsys.readouterr().err
    assert err.count("The system keyring didn't answer within 2 s") == 1
    assert "k" * 40 not in err and "e" * 40 not in err
    assert len(kr.reads) == 1


def test_keyring_unbounded_at_a_terminal(home, kr, monkeypatch):
    monkeypatch.setattr(credentials, "_interactive", lambda: True)

    def no_threads(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("an interactive read runs in the caller's thread")

    monkeypatch.setattr(credentials.threading, "Thread", no_threads)
    kr.entries["api-key.default"] = "k" * 40
    assert AuthManager(Config()).get_api_key() == "k" * 40


def test_keyring_errors_fall_through(home, kr, monkeypatch):
    def broken(*args: Any) -> None:
        raise RuntimeError("no backend")

    monkeypatch.setattr(kr, "get_password", broken)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "e" * 40)
    assert AuthManager(Config()).get_api_key() == "e" * 40


# ── Placeholders ────────────────────────────────────────────────────────


def test_placeholder_key_counts_as_unset(home, kr, monkeypatch, capsys):
    monkeypatch.setattr(output.stderr_console, "_width", 400)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "${DEEPGRAM_API_KEY}")
    assert credentials.drop_env_placeholders() == ["DEEPGRAM_API_KEY"]
    auth = AuthManager(Config())
    with pytest.raises(AuthRequiredError):
        auth.guard()
    credentials.flush_warnings()
    err = capsys.readouterr().err
    assert err.count("ignored DEEPGRAM_API_KEY: it holds a placeholder") == 1
    assert "${DEEPGRAM_API_KEY}" not in err


def test_placeholder_merged_before_the_drop_keeps_the_file_key(home, kr, monkeypatch):
    write_config(home, {"profiles": {"default": {"api_key": "f" * 40}}})
    monkeypatch.setenv("DEEPGRAM_API_KEY", "${DEEPGRAM_API_KEY}")
    config = Config()  # e.g. `dg mcp`'s handoff builds its own Config first
    resolved = AuthManager(config).resolve()
    assert resolved.api_key == "f" * 40
    assert resolved.key_source == credentials.KEY_SOURCE_FILE


@pytest.mark.parametrize(
    "name", ["DEEPGRAM_PROFILE", "DEEPGRAM_PROJECT_ID", "DEEPGRAM_BASE_URL"]
)
def test_other_placeholders_count_as_unset(home, kr, monkeypatch, capsys, name):
    monkeypatch.setattr(output.stderr_console, "_width", 400)
    write_config(home, {"profiles": {"default": {"api_key": "f" * 40}}})
    monkeypatch.setenv(name, "${" + name + "}")
    credentials.drop_env_placeholders()
    config = Config()
    resolved = AuthManager(config).resolve()
    assert resolved.api_key == "f" * 40
    assert resolved.profile == "default"
    assert resolved.project_id is None
    assert hosts.check_credential_host(config) == hosts.DEFAULT_BASE_URL
    credentials.flush_warnings()
    credentials.flush_warnings()
    err = capsys.readouterr().err
    assert err.count(f"ignored {name}: it holds a placeholder") == 1


def test_profile_placeholder_merged_before_the_drop(home, kr, monkeypatch):
    write_config(
        home,
        {"default_profile": "work", "profiles": {"work": {"api_key": "f" * 40}}},
    )
    monkeypatch.setenv("DEEPGRAM_PROFILE", "${DEEPGRAM_PROFILE}")
    resolved = AuthManager(Config()).resolve()
    assert resolved.profile == "work"
    assert resolved.api_key == "f" * 40


# ── Env-vs-stored mismatch: implemented, off in 0.5.0 ──────────────────


def test_mismatch_check_is_off(home, kr, monkeypatch):
    kr.entries["api-key.default"] = "k" * 40
    monkeypatch.setenv("DEEPGRAM_API_KEY", "e" * 40)
    assert credentials.WARN_ENV_MISMATCH is False
    assert AuthManager(Config()).resolve().env_mismatch is False
    monkeypatch.setattr(credentials, "WARN_ENV_MISMATCH", True)
    assert AuthManager(Config()).resolve().env_mismatch is True
    assert credentials.fingerprint("k" * 40) != "k" * 40


# ── auth_required rendering ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("agentic", "hard", "hint_id", "next_step"),
    [
        (False, False, "H-AUTH-REQUIRED", "dg login"),
        (True, True, "A-AUTH-REQUIRED", "dg login"),
        (True, False, "C-AUTH-REQUIRED", None),
    ],
)
def test_auth_required_render(monkeypatch, agentic, hard, hint_id, next_step):
    monkeypatch.setattr(output, "is_ai_agent", lambda: hard)
    monkeypatch.setitem(output._output_config, "agentic", agentic)
    result = transport.render(AuthRequiredError(), command_path="listen")
    assert result.code == "auth_required"
    assert result.error == "DEEPGRAM_API_KEY is not set"
    assert result.hint == transport.STRINGS[hint_id].format(prog="dg")
    assert result.next_step == next_step
    assert result.model_dump()["schema_version"] == 1
    assert "details" not in result.model_dump()


def test_key_untrusted_render_has_no_next_step(monkeypatch):
    monkeypatch.setattr(output, "is_ai_agent", lambda: True)
    monkeypatch.setitem(output._output_config, "agentic", True)
    err = AuthRequiredError(untrusted_source="~/.config/deepctl/config.yaml")
    result = transport.render(err, command_path="listen")
    assert result.error == (
        "dg didn't use the API key in ~/.config/deepctl/config.yaml (see the "
        "warning above), and no other API key is set."
    )
    assert result.hint == (
        "A person must fix this file. If you are an AI agent, ask the user."
    )
    assert result.next_step is None


def test_catalogue_text_is_0_4_1s(monkeypatch):
    from deepctl_core import messages

    assert transport.STRINGS["E-KEY-UNTRUSTED"] == messages.E_KEY_UNTRUSTED
    assert (
        transport.STRINGS["W_CFG_UNTRUSTED_FILE_HINT"]
        == messages.W_CFG_UNTRUSTED_FILE_HINT
    )
    assert isinstance(AuthRequiredError(), auth_module.AuthenticationError)


def _run_command(monkeypatch, capsys, *, requires_project: bool = False) -> Any:
    import click
    from deepctl_core.base_command import BaseCommand
    from deepctl_core.models import BaseResult

    class NeedsAuth(BaseCommand):
        name = "listen"
        help = "test command"
        requires_auth = True

        def handle(self, config, auth_manager, client, **kwargs):  # type: ignore[no-untyped-def]
            return BaseResult(status="success", message="done")

    NeedsAuth.requires_project = requires_project
    monkeypatch.setattr(output.stderr_console, "_width", 400)
    monkeypatch.setattr("deepctl_core.output.get_output_format", lambda: "json")
    ctx = click.Context(
        click.Command("listen"), info_name="dg listen", obj={"config": Config()}
    )
    code = 0
    try:
        NeedsAuth().execute(ctx)
    except SystemExit as exc:
        code = int(exc.code or 0)
    except click.ClickException:
        code = 1
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_no_key_prints_coded_result_and_two_lines(home, kr, monkeypatch, capsys):
    from deepctl_core import base_command

    monkeypatch.setattr(output, "is_ai_agent", lambda: True)
    monkeypatch.setitem(output._output_config, "agentic", True)
    monkeypatch.setattr(base_command, "_agentic", True)
    code, out, err = _run_command(monkeypatch, capsys)
    assert code == 1
    payload = json.loads(out)
    assert payload == {
        "error": "DEEPGRAM_API_KEY is not set",
        "status": "error",
        "code": "auth_required",
        "hint": transport.STRINGS["A-AUTH-REQUIRED"].format(prog="dg"),
        "next_step": "dg login",
        "schema_version": 1,
    }
    assert err.splitlines() == [
        "ERROR: DEEPGRAM_API_KEY is not set",
        "HINT: " + transport.STRINGS["A-AUTH-REQUIRED"].format(prog="dg"),
    ]


def test_untrusted_file_prints_two_lines(home, kr, monkeypatch, capsys):
    from deepctl_core import base_command

    monkeypatch.setattr(output, "is_ai_agent", lambda: True)
    monkeypatch.setitem(output._output_config, "agentic", True)
    monkeypatch.setattr(base_command, "_agentic", True)
    write_config(home, {"profiles": {"default": {"api_key": "f" * 40}}}, 0o666)
    code, out, err = _run_command(monkeypatch, capsys)
    assert code == 1
    payload = json.loads(out)
    assert payload["code"] == "auth_required"
    assert payload["next_step"] is None
    lines = err.splitlines()
    # The warning's hint prints once per run (0.4.1), before the error.
    assert lines[-1].startswith("ERROR: dg didn't use the API key in ")
    assert lines[-2] == (
        "HINT: A person must fix this file. If you are an AI agent, ask the user."
    )
    assert err.count("HINT: ") == 1
    assert "f" * 40 not in out + err


def test_env_key_info_line(home, kr, monkeypatch, capsys):
    from deepctl_core import base_command

    monkeypatch.setitem(output._output_config, "agentic", True)
    monkeypatch.setattr(base_command, "_agentic", True)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "f" * 40)
    code, out, err = _run_command(monkeypatch, capsys)
    assert code == 0
    assert err.splitlines() == ["INFO: Using credentials from environment variables"]


def test_no_project_warning_only_when_needed(home, kr, monkeypatch, capsys):
    from deepctl_core import base_command

    monkeypatch.setitem(output._output_config, "agentic", True)
    monkeypatch.setattr(base_command, "_agentic", True)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "f" * 40)
    _, _, err = _run_command(monkeypatch, capsys, requires_project=True)
    assert "WARN: No project ID specified" in err


# ── Import cost: no keyring with --api-key, no client in a group's --help ─


def _imports(code: str, home: Path) -> set[str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("DEEPGRAM_") and k not in output.AI_AGENT_ENV_MARKERS
    }
    env.update(HOME=str(home), DEEPCTL_TELEMETRY_DISABLED="1")
    done = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return set(json.loads(done.stdout.strip().splitlines()[-1]))


def test_api_key_flag_never_imports_keyring(home):
    modules = _imports(
        """
        import json, sys
        from deepctl_core.auth import AuthManager
        from deepctl_core.config import Config
        auth = AuthManager(Config(), "f" * 40)
        auth.guard()
        assert auth.get_api_key() == "f" * 40
        auth.get_credential_source()
        print(json.dumps(sorted(sys.modules)))
        """,
        home,
    )
    assert "keyring" not in modules
    assert "httpx" not in modules


def test_group_subcommand_help_builds_no_client(home):
    modules = _imports(
        """
        import json, sys
        import click
        from deepctl_core.base_command import BaseCommand
        from deepctl_core.base_group_command import BaseGroupCommand

        class Sub(BaseCommand):
            name = "sub"
            help = "sub"
            def handle(self, config, auth_manager, client, **kwargs):
                return None

        class Group(BaseGroupCommand):
            name = "grp"
            help = "grp"
            def setup_commands(self):
                return [click.Command("sub", callback=lambda: None, help="sub")]

        @click.group()
        @click.pass_context
        def root(ctx):
            from deepctl_core.config import Config
            ctx.obj = {"config": Config()}

        root.add_command(Group().get_click_group())
        # Not click.testing: on 3.14 it imports pdb, and with it asyncio and ssl.
        try:
            root.main(["grp", "sub", "--help"], prog_name="dg")
        except SystemExit as exc:
            assert exc.code == 0, exc.code
        print(json.dumps(sorted(sys.modules)))
        """,
        home,
    )
    for name in ("httpx", "keyring", "ssl", "deepgram"):
        assert name not in modules, name
