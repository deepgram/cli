"""In an AI agent session, only the user config at the location derived
from the real home directory (not HOME or XDG_CONFIG_HOME) can widen
trust, and only as a plain file: no symlink on the way down from home,
a regular file owned by the user with one link and no group or other
write bit. Outside agent mode, XDG_CONFIG_HOME and symlinks are honored."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml
from deepctl_core import config as config_module
from deepctl_core import hosts, output
from deepctl_core import messages as msg
from deepctl_core.config import Config

_REAL_HOME = config_module._real_home

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX layout")

# Where the user config lives under the home directory (platformdirs).
_CFG = (
    Path("Library/Application Support") if sys.platform == "darwin" else Path(".config")
)
# This process's open file descriptors.
_FD_DIR = "/dev/fd" if sys.platform == "darwin" else "/proc/self/fd"

TRUST_CONFIG = {
    "api": {"allowed_hosts": ["127.0.0.1:8000"]},
    "profiles": {"default": {"base_url": "http://127.0.0.1:8000"}},
    "tools": {"ffprobe_path": "/repo/tools/ffprobe"},
}


@pytest.fixture(autouse=True)
def real_home(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "_pending_notices", [])
    monkeypatch.setattr(config_module, "_seen_notices", set())
    monkeypatch.setattr(hosts, "_reported", set())
    home = tmp_path / "home"
    home.mkdir()
    # The account's home directory as the OS reports it.
    monkeypatch.setattr(config_module, "_real_home", lambda: str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return home


def agent(monkeypatch, on: bool) -> None:
    monkeypatch.setattr(output, "is_ai_agent", lambda: on)


def plant(directory) -> None:
    """A config file as dg writes it (0700 dir, 0600 file), whatever the
    umask: a group-writable file is untrusted, by design."""
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    (directory / "config.yaml").write_text(yaml.safe_dump(TRUST_CONFIG))
    (directory / "config.yaml").chmod(0o600)


def notices() -> list[str]:
    return [text for _, text, _, _ in config_module._pending_notices]


def ids() -> list[str]:
    return [i for i, _, _, _ in config_module._pending_notices]


def assert_trusted(config: Config) -> None:
    assert config.trusted_file is True
    assert config.allowed_hosts() == ["127.0.0.1:8000"]
    assert config.get_profile().base_url == "http://127.0.0.1:8000"
    assert config.get("tools.ffprobe_path") == "/repo/tools/ffprobe"
    assert not [i for i in ids() if i == "W-CFG-UNTRUSTED"]


def assert_filtered(config: Config) -> None:
    assert config.trusted_file is False
    assert config.allowed_hosts() == []
    assert config.get_profile().base_url == "https://api.deepgram.com"
    assert config.get("tools.ffprobe_path") is None
    lines = [t for i, t in zip(ids(), notices()) if i == "W-CFG-UNTRUSTED"]
    # One line per ignored key, absolute path, no "--config" wording.
    assert len(lines) == 3, lines
    for line in lines:
        assert "~" not in line
        assert "--config" not in line
    assert any(line.startswith("Ignored api.allowed_hosts in /") for line in lines)
    with pytest.raises(hosts.CredentialHostError):
        hosts.check_credential_host(Config(base_url="http://127.0.0.1:8000"))


def test_agent_real_config_honored_with_cwd_home(real_home, monkeypatch):
    agent(monkeypatch, True)
    monkeypatch.chdir(real_home)
    plant(real_home / _CFG / "deepctl")
    assert_trusted(Config())


def test_agent_xdg_under_home_filtered(real_home, monkeypatch):
    agent(monkeypatch, True)
    repo = real_home / "cloned-repo"
    monkeypatch.chdir(real_home)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(repo / ".dg"))
    plant(repo / ".dg" / "deepctl")
    assert_filtered(Config())


def test_agent_fake_home_filtered(real_home, tmp_path, monkeypatch):
    agent(monkeypatch, True)
    fake = tmp_path / "repo" / "fakehome"
    monkeypatch.setenv("HOME", str(fake))
    plant(fake / _CFG / "deepctl")
    assert_filtered(Config())


def test_non_agent_xdg_honored(real_home, tmp_path, monkeypatch):
    agent(monkeypatch, False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    plant(tmp_path / "xdg" / "deepctl")
    assert_trusted(Config())


def test_real_default_ignores_env(real_home, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "elsewhere"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert config_module._real_default_config_file() == (
        real_home / _CFG / "deepctl" / "config.yaml"
    )


def test_real_home_comes_from_the_os(monkeypatch):
    import pwd

    monkeypatch.setenv("HOME", "/repo/fakehome")
    assert _REAL_HOME() == pwd.getpwuid(os.getuid()).pw_dir


# ── A link flipped while the config is read ──────────────────────


def test_flipping_link_never_trusts_the_other_file(real_home, tmp_path, monkeypatch):
    """The agent's XDG path flips between the real config and its own file;
    the trust decision and the content always come from one open file."""
    import threading

    agent(monkeypatch, True)
    monkeypatch.chdir(real_home)
    plant(real_home / _CFG / "deepctl")
    real_cfg = real_home / _CFG / "deepctl" / "config.yaml"
    real_cfg.write_text(yaml.safe_dump({"profiles": {"default": {"api_key": "k"}}}))
    evil = tmp_path / "repo" / "evil.yaml"
    evil.parent.mkdir()
    evil.write_text(yaml.safe_dump(TRUST_CONFIG))
    xdg = tmp_path / "repo" / ".dg"
    (xdg / "deepctl").mkdir(parents=True)
    link = xdg / "deepctl" / "config.yaml"
    link.symlink_to(real_cfg)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

    stop = threading.Event()

    def flip():
        tmp = str(link) + ".swp"
        while not stop.is_set():
            for target in (real_cfg, evil):
                try:
                    os.unlink(tmp)
                except FileNotFoundError:
                    pass
                os.symlink(target, tmp)
                os.replace(tmp, link)

    thread = threading.Thread(target=flip, daemon=True)
    thread.start()
    try:
        for _ in range(300):
            config = Config()
            assert config.allowed_hosts() == []
            assert config.get_profile().base_url == "https://api.deepgram.com"
            assert config.get("tools.ffprobe_path") is None
    finally:
        stop.set()
        thread.join()


def test_agent_untrusted_text_and_hint(real_home, tmp_path, monkeypatch):
    agent(monkeypatch, True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "x"))
    plant(tmp_path / "x" / "deepctl")
    Config()
    path = tmp_path / "x" / "deepctl" / "config.yaml"
    pending = [(t, h) for i, t, h, _ in config_module._pending_notices if i == "W-CFG-UNTRUSTED"]
    assert (
        f"Ignored api.allowed_hosts in {path}: in an AI agent session, only the "
        "deepctl user config at its standard location in your home directory can "
        "set this.",
        "A person must set this in the deepctl user config. If you are an AI "
        "agent, ask the user.",
    ) in pending


@pytest.mark.parametrize("content", [None, {"profiles": {"default": {"api_key": "k"}}}])
def test_agent_no_notice_when_nothing_ignored(real_home, tmp_path, monkeypatch, content):
    agent(monkeypatch, True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "x"))
    if content is not None:
        (tmp_path / "x" / "deepctl").mkdir(parents=True)
        (tmp_path / "x" / "deepctl" / "config.yaml").write_text(yaml.safe_dump(content))
    Config()
    assert config_module._pending_notices == [] or "W-CFG-UNTRUSTED" not in ids()


# ── Strict trust root ─────────────────────────────────────────────


def link_config_to(real_home, target):
    cfg_dir = real_home / _CFG / "deepctl"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.yaml").symlink_to(target)


def test_dotfiles_symlinked_config_filtered_in_agent_mode(real_home, monkeypatch):
    agent(monkeypatch, True)
    plant(real_home / "dotfiles")
    link_config_to(real_home, real_home / "dotfiles" / "config.yaml")
    monkeypatch.chdir(real_home)
    assert_filtered(Config())


def test_dotfiles_symlinked_config_honored_outside_agent_mode(real_home, monkeypatch):
    agent(monkeypatch, False)
    plant(real_home / "dotfiles")
    link_config_to(real_home, real_home / "dotfiles" / "config.yaml")
    assert_trusted(Config())


def test_repointed_link_hop_filtered(real_home, tmp_path, monkeypatch):
    """~/.config/deepctl/config.yaml -> /repo/dotfiles/x -> /tmp/evil."""
    agent(monkeypatch, True)
    plant(tmp_path / "elsewhere")
    repo = tmp_path / "repo" / "dotfiles"
    repo.mkdir(parents=True)
    (repo / "deepctl.yaml").symlink_to(tmp_path / "elsewhere" / "config.yaml")
    link_config_to(real_home, repo / "deepctl.yaml")
    monkeypatch.chdir(tmp_path / "repo")
    assert_filtered(Config())


def test_symlinked_config_folder_filtered(real_home, tmp_path, monkeypatch):
    """~/.config/deepctl -> /repo/dotfiles/deepctl."""
    agent(monkeypatch, True)
    plant(tmp_path / "repo" / "dotfiles" / "deepctl")
    (real_home / _CFG).mkdir(parents=True)
    (real_home / _CFG / "deepctl").symlink_to(
        tmp_path / "repo" / "dotfiles" / "deepctl", target_is_directory=True
    )
    assert_filtered(Config())


def test_symlinked_dot_config_filtered(real_home, tmp_path, monkeypatch):
    agent(monkeypatch, True)
    plant(tmp_path / "x" / "deepctl")
    (real_home / _CFG).parent.mkdir(parents=True, exist_ok=True)
    (real_home / _CFG).symlink_to(tmp_path / "x", target_is_directory=True)
    assert_filtered(Config())


def test_hardlinked_config_filtered(real_home, tmp_path, monkeypatch):
    """One inode shared with a repo copy the agent can edit."""
    agent(monkeypatch, True)
    plant(real_home / _CFG / "deepctl")
    (tmp_path / "repo").mkdir()
    os.link(real_home / _CFG / "deepctl" / "config.yaml", tmp_path / "repo" / "dg.yaml")
    assert_filtered(Config())


def test_group_writable_config_filtered(real_home, monkeypatch):
    agent(monkeypatch, True)
    plant(real_home / _CFG / "deepctl")
    (real_home / _CFG / "deepctl" / "config.yaml").chmod(0o664)
    assert_filtered(Config())


@pytest.mark.timeout(10)
def test_fifo_does_not_hang(real_home, tmp_path, monkeypatch):
    agent(monkeypatch, True)
    fifo_dir = tmp_path / "x" / "deepctl"
    fifo_dir.mkdir(parents=True)
    os.mkfifo(fifo_dir / "config.yaml")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "x"))
    config = Config()
    assert config.allowed_hosts() == []


def test_hop_flipping_race_never_trusts(real_home, tmp_path, monkeypatch):
    """The in-repo hop flips between a benign home file and an evil file;
    a symlinked config is never trusted, so nothing leaks."""
    import threading

    agent(monkeypatch, True)
    benign = real_home / "benign.yaml"
    benign.write_text(yaml.safe_dump({"profiles": {"default": {"api_key": "k"}}}))
    evil = tmp_path / "repo" / "evil.yaml"
    evil.parent.mkdir()
    evil.write_text(yaml.safe_dump(TRUST_CONFIG))
    hop = tmp_path / "repo" / "hop.yaml"
    hop.symlink_to(benign)
    link_config_to(real_home, hop)
    stop = threading.Event()

    def flip():
        tmp = str(hop) + ".swp"
        while not stop.is_set():
            for target in (benign, evil):
                try:
                    os.unlink(tmp)
                except FileNotFoundError:
                    pass
                os.symlink(target, tmp)
                os.replace(tmp, hop)

    thread = threading.Thread(target=flip, daemon=True)
    thread.start()
    try:
        for _ in range(150):
            config = Config()
            assert config.allowed_hosts() == []
            assert config.get("tools.ffprobe_path") is None
    finally:
        stop.set()
        thread.join()


def test_untrusted_root_not_used_for_custom_config_allowlist(
    real_home, tmp_path, monkeypatch
):
    """--config elsewhere reads api.allowed_hosts from the trust root only
    when the root passes the checks."""
    agent(monkeypatch, True)
    plant(tmp_path / "dot")
    link_config_to(real_home, tmp_path / "dot" / "config.yaml")
    other = tmp_path / "other.yaml"
    other.write_text("{}\n")
    assert Config(config_path=str(other)).allowed_hosts() == []
    (real_home / _CFG / "deepctl" / "config.yaml").unlink()
    plant(real_home / _CFG / "deepctl")
    assert Config(config_path=str(other)).allowed_hosts() == ["127.0.0.1:8000"]


def test_inode_zero_is_never_the_same_file():
    """Some Windows file systems report index 0 for every file."""
    import types

    a = types.SimpleNamespace(st_dev=1, st_ino=0)
    assert config_module._same_inode(a, a) is False


# ── Reasons, single open, first-run tightening, fallbacks ───────────


def reasons() -> list[str]:
    return [t for i, t in zip(ids(), notices()) if i == "W-CFG-UNTRUSTED"]


@pytest.mark.parametrize(
    ("setup", "why"),
    [
        ("symlink", "is a symlink or is reached through one"),
        ("hardlink", "has more than one hard link"),
        ("writable", "can be changed by other users"),
    ],
)
def test_untrusted_real_config_names_the_cause(real_home, tmp_path, monkeypatch, setup, why):
    agent(monkeypatch, True)
    cfg = real_home / _CFG / "deepctl" / "config.yaml"
    if setup == "symlink":
        plant(real_home / "dotfiles")
        link_config_to(real_home, real_home / "dotfiles" / "config.yaml")
    else:
        plant(cfg.parent)
        if setup == "hardlink":
            os.link(cfg, tmp_path / "copy.yaml")
        else:
            cfg.chmod(0o664)
    assert_filtered(Config())
    lines = reasons()
    assert lines
    assert all(line.endswith(f"because the file {why}.") for line in lines), lines
    assert all("standard location" not in line for line in lines)
    hints = [h for i, _, h, _ in config_module._pending_notices if i == "W-CFG-UNTRUSTED"]
    assert set(hints) == {
        "A person must fix this file. If you are an AI agent, ask the user."
    }


def plant_with_key(real_home, mode):
    cfg = real_home / _CFG / "deepctl" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(yaml.safe_dump({**TRUST_CONFIG, "profiles": {"default": {
        "api_key": "k", "base_url": "http://127.0.0.1:8000"}}}))
    cfg.chmod(mode)
    return cfg


W_PERM_AGENT_TAIL = (
    "holds an API key and other users can change it (mode {octal}), so in an AI "
    "agent session dg won't use that key or change the file's permissions."
)
FILE_HINT = "A person must fix this file. If you are an AI agent, ask the user."


@pytest.mark.parametrize("mode", [0o664, 0o666, 0o660, 0o646])
def test_writable_config_with_key_left_alone_and_untrusted_in_agent_mode(
    real_home, monkeypatch, mode
):
    """Content others could have written is never
    made trusted by tightening it, and its API key isn't used."""
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    agent(monkeypatch, True)
    cfg = plant_with_key(real_home, mode)
    before = cfg.read_text()
    config = Config()
    assert (cfg.stat().st_mode & 0o777) == mode
    assert cfg.read_text() == before
    assert config.trusted_file is False
    assert config.allowed_hosts() == []
    assert config.get("tools.ffprobe_path") is None
    assert config.get_profile().api_key is None
    assert "I-PERM-FIXED" not in ids()
    perm = [(t, h) for i, t, h, _ in config_module._pending_notices if i == "W-PERM-AGENT"]
    assert perm == [
        (f"{msg.display_path(cfg)} {W_PERM_AGENT_TAIL.format(octal=f'{mode:04o}')}",
         FILE_HINT)
    ]
    lines = reasons()
    assert len(lines) == 3, lines
    assert all(line.endswith("because the file can be changed by other users.")
               for line in lines)
    assert config.get_profile().base_url == "https://api.deepgram.com"
    with pytest.raises(hosts.CredentialHostError):
        hosts.check_credential_host(Config(base_url="http://127.0.0.1:8000"))


def test_writable_key_file_warning_ignores_quiet_and_prints_once(
    real_home, monkeypatch, capsys
):
    agent(monkeypatch, True)
    plant_with_key(real_home, 0o666)
    output._output_config.update(agentic=True, quiet=True)
    try:
        Config()
        Config()
        config_module.flush_config_notices()
    finally:
        output._output_config.update(agentic=False, quiet=False)
    err = capsys.readouterr().err.replace("\n", " ")
    assert err.count("other users can change it (mode 0666)") == 1
    assert err.count(FILE_HINT) >= 1


def test_writable_key_file_env_key_still_used_in_agent_mode(real_home, monkeypatch):
    agent(monkeypatch, True)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "env-key")
    plant_with_key(real_home, 0o666)
    assert Config().get_profile().api_key == "env-key"


def test_readable_key_file_gives_no_agent_key_warning(real_home, monkeypatch):
    agent(monkeypatch, True)
    plant_with_key(real_home, 0o644)
    config = Config()
    assert config.get_profile().api_key == "k"
    assert "W-PERM-AGENT" not in ids()


def test_readable_config_with_key_tightened_and_trusted_in_agent_mode(
    real_home, monkeypatch
):
    agent(monkeypatch, True)
    cfg = plant_with_key(real_home, 0o644)
    config = Config()
    assert (cfg.stat().st_mode & 0o777) == 0o600
    assert "I-PERM-FIXED" in ids()
    assert config.trusted_file is True
    assert config.allowed_hosts() == ["127.0.0.1:8000"]


@pytest.mark.parametrize("mode", [0o644, 0o666])
def test_config_with_key_tightened_outside_agent_mode(real_home, monkeypatch, mode):
    agent(monkeypatch, False)
    cfg = plant_with_key(real_home, mode)
    config = Config()
    assert (cfg.stat().st_mode & 0o777) == 0o600
    assert config.get_profile().api_key == "k"
    assert "W-PERM-AGENT" not in ids()
    assert "I-PERM-FIXED" in ids()
    assert config.trusted_file is True
    assert config.allowed_hosts() == ["127.0.0.1:8000"]


def test_dir_fd_unavailable_falls_back_to_path_walk(real_home, monkeypatch):
    agent(monkeypatch, True)
    monkeypatch.setattr(config_module.os, "supports_dir_fd", set())
    plant(real_home / _CFG / "deepctl")
    assert_trusted(Config())


def test_dir_fd_unavailable_still_refuses_symlinks(real_home, monkeypatch):
    agent(monkeypatch, True)
    monkeypatch.setattr(config_module.os, "supports_dir_fd", set())
    plant(real_home / "dotfiles")
    link_config_to(real_home, real_home / "dotfiles" / "config.yaml")
    assert_filtered(Config())


@pytest.mark.parametrize("replaces", [1, 2, 4])
def test_replace_between_open_and_checks_never_untrusts(real_home, monkeypatch, replaces):
    """A dg save replacing the file after it was opened and before it is
    checked, forced on every load: the open copy has no
    links left, and the load looks again instead of distrusting it."""
    agent(monkeypatch, True)
    plant(real_home / _CFG / "deepctl")
    cfg = real_home / _CFG / "deepctl" / "config.yaml"
    text = cfg.read_text()
    real_checked_parse = config_module._checked_parse
    budget = {"left": 0, "done": 0}

    def replace_then_check(fd):
        if budget["left"]:
            budget["left"] -= 1
            tmp = cfg.with_name(".c.tmp")
            tmp.write_text(text)
            tmp.chmod(0o600)
            os.replace(tmp, cfg)
            budget["done"] += 1
        return real_checked_parse(fd)

    monkeypatch.setattr(config_module, "_checked_parse", replace_then_check)
    for _ in range(20):
        budget["left"] = replaces
        c = Config()
        assert budget["left"] == 0  # the race was really hit, every time
        assert_trusted(c)
    assert budget["done"] == 20 * replaces


def test_concurrent_replace_never_untrusts_a_normal_config(real_home, monkeypatch):
    """Another dg save replacing the file while it is read,
    from a real second thread at unforced times. At most two replaces land in
    each load (a load looks up to five times), whatever the host load: the saver
    gets a budget per load and is idle before the next load starts."""
    import threading
    import time

    agent(monkeypatch, True)
    plant(real_home / _CFG / "deepctl")
    cfg = real_home / _CFG / "deepctl" / "config.yaml"
    text = cfg.read_text()
    stop = threading.Event()
    armed = threading.Event()
    replacing = threading.Lock()
    budget = {"left": 0, "done": 0}

    def saver():
        n = 0
        while not stop.is_set():
            if not armed.wait(0.05):
                continue
            with replacing:
                if not armed.is_set() or budget["left"] <= 0:
                    continue
                budget["left"] -= 1
                tmp = cfg.with_name(f".c.{n}.tmp")
                tmp.write_text(text)
                tmp.chmod(0o600)
                os.replace(tmp, cfg)
                budget["done"] += 1
                n += 1
            time.sleep(0.001)

    thread = threading.Thread(target=saver, daemon=True)
    thread.start()
    try:
        results = []
        for _ in range(200):
            with replacing:
                budget["left"] = 2
                armed.set()
            c = Config()
            with replacing:  # no replace in flight into the next load
                armed.clear()
                budget["left"] = 0
            if not c.trusted_file:
                results.append(
                    (config_module._check_trust_root()[2], str(c.config_path))
                )
        untrusted = len(results)
    finally:
        stop.set()
        armed.set()
        thread.join()
    assert untrusted == 0, results[:5]
    assert budget["done"] > 0  # the saver really ran alongside the loads


def _legacy(real_home, target_text, symlink_to=None):
    old_dir = real_home / _CFG / "deepgram"
    old_dir.mkdir(parents=True)
    if symlink_to is not None:
        symlink_to.write_text(target_text)
        (old_dir / "config.yaml").symlink_to(symlink_to)
    else:
        (old_dir / "config.yaml").write_text(target_text)


def test_legacy_migration_skipped_in_agent_sessions(real_home, tmp_path, monkeypatch):
    """A stow-linked legacy file never becomes the
    trusted config through migration in an agent session."""
    agent(monkeypatch, True)
    _legacy(real_home, yaml.safe_dump(TRUST_CONFIG), symlink_to=tmp_path / "legacy.yaml")
    config = Config()
    assert not (real_home / _CFG / "deepctl" / "config.yaml").exists()
    assert config.allowed_hosts() == []


def test_legacy_migration_writes_a_private_copy(real_home, tmp_path, monkeypatch):
    agent(monkeypatch, False)
    _legacy(real_home, yaml.safe_dump(TRUST_CONFIG), symlink_to=tmp_path / "legacy.yaml")
    (tmp_path / "legacy.yaml").chmod(0o644)
    Config()
    new = real_home / _CFG / "deepctl" / "config.yaml"
    assert not new.is_symlink()
    assert (new.stat().st_mode & 0o777) == 0o600
    assert yaml.safe_load(new.read_text()) == TRUST_CONFIG


# ── No saves over a file others control ──────────────────────────

SAVE_REFUSED = (
    "in an AI agent session, dg can't save this config because the file {why}, "
    "so nothing was saved."
)


def _no_keyring(monkeypatch):
    from deepctl_core import auth as auth_module

    def fail(*args, **kwargs):
        raise RuntimeError("no keyring backend")

    monkeypatch.setattr(auth_module.keyring, "set_password", fail)
    monkeypatch.setattr(auth_module.keyring, "get_password", lambda *a: None)
    monkeypatch.setattr(auth_module.keyring, "delete_password", fail)


@pytest.mark.parametrize("mode", [0o666, 0o660, 0o646])
@pytest.mark.parametrize("with_key", [True, False])
def test_agent_never_saves_over_a_writable_config(real_home, monkeypatch, mode, with_key):
    """No save in an AI agent session rewrites a file
    others can change; it stays byte-identical and untrusted."""
    import errno

    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    agent(monkeypatch, True)
    cfg = real_home / _CFG / "deepctl" / "config.yaml"
    if with_key:
        plant_with_key(real_home, mode)
    else:
        plant(cfg.parent)
        cfg.chmod(mode)
    before = cfg.read_bytes()
    config = Config()
    config._set_config_value("update.installation_method", "pip")
    config.save(implicit=True)  # the install hook, the update cache
    with pytest.raises(OSError) as err:
        config.update_profile("default", project_id="p-agent")  # dg projects <id>
    assert isinstance(err.value, config_module.ConfigSaveRefused)
    assert err.value.errno == errno.EPERM
    assert err.value.filename == str(cfg)
    assert err.value.strerror == SAVE_REFUSED.format(why="can be changed by other users")
    assert err.value.hint == FILE_HINT
    with pytest.raises(config_module.ConfigSaveRefused):
        config.check_saveable()
    assert cfg.read_bytes() == before
    assert (cfg.stat().st_mode & 0o777) == mode
    again = Config()
    assert again.trusted_file is False
    assert again.allowed_hosts() == []
    assert again.get_profile().api_key is None


def test_agent_login_onto_a_writable_config_is_refused(real_home, monkeypatch):
    from deepctl_core.auth import AuthManager

    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    _no_keyring(monkeypatch)
    agent(monkeypatch, True)
    cfg = plant_with_key(real_home, 0o666)
    before = cfg.read_bytes()
    auth = AuthManager(Config())
    monkeypatch.setattr(
        auth, "verify_credentials", lambda *a, **k: (True, "Credentials verified", None)
    )
    with pytest.raises(config_module.ConfigSaveRefused) as err:
        auth.login_with_api_key("sk-NEWKEY0000", "proj-fake", _force_write=True)
    assert err.value.strerror == SAVE_REFUSED.format(why="can be changed by other users")
    assert cfg.read_bytes() == before
    assert (cfg.stat().st_mode & 0o777) == 0o666


def test_agent_saves_an_own_0644_config_as_before(real_home, monkeypatch):
    agent(monkeypatch, True)
    cfg = plant_with_key(real_home, 0o644)
    config = Config()
    config.update_profile("default", project_id="p2")
    assert (cfg.stat().st_mode & 0o777) == 0o600
    assert yaml.safe_load(cfg.read_text())["profiles"]["default"]["project_id"] == "p2"


@pytest.mark.parametrize("mode", [0o666, 0o664])
def test_writable_config_saved_outside_agent_mode(real_home, monkeypatch, mode):
    agent(monkeypatch, False)
    cfg = real_home / _CFG / "deepctl" / "config.yaml"
    plant(cfg.parent)
    cfg.chmod(mode)
    config = Config()
    config.update_profile("default", project_id="p3")
    assert (cfg.stat().st_mode & 0o777) == 0o600
    assert yaml.safe_load(cfg.read_text())["profiles"]["default"]["project_id"] == "p3"


def test_agent_foreign_owned_key_file_key_unused_and_not_saved(real_home, monkeypatch):
    """A key file another user owns: its key isn't used and it isn't saved."""
    import errno

    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    agent(monkeypatch, True)
    cfg = plant_with_key(real_home, 0o600)
    before = cfg.read_bytes()
    real_euid = os.geteuid()
    monkeypatch.setattr(config_module.os, "geteuid", lambda: real_euid + 1)
    config = Config()
    assert config.get_profile().api_key is None
    assert config.api_key_untrusted is True
    owner = [
        (t, h)
        for i, t, h, _ in config_module._pending_notices
        if i == "W-CFG-UNTRUSTED" and t.startswith("Ignored profiles.default.api_key")
    ]
    assert owner == [
        (
            f"Ignored profiles.default.api_key in {cfg}: in an AI agent session, the "
            "deepctl user config can't set this because the file is owned by another "
            "user.",
            FILE_HINT,
        )
    ]
    with pytest.raises(config_module.ConfigSaveRefused) as err:
        config.update_profile("default", project_id="p4")
    assert err.value.errno == errno.EPERM
    assert err.value.strerror == SAVE_REFUSED.format(why="is owned by another user")
    assert cfg.read_bytes() == before


def test_missing_key_after_untrusted_file_key_says_so(real_home, monkeypatch, capsys):
    """The error says neither 'not set in the configuration file' nor dg login."""
    from deepctl_core.auth import AuthenticationError, AuthManager

    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    _no_keyring(monkeypatch)
    agent(monkeypatch, True)
    plant_with_key(real_home, 0o666)
    output._output_config.update(agentic=True)
    try:
        with pytest.raises(AuthenticationError) as err:
            AuthManager(Config()).guard()
    finally:
        output._output_config.update(agentic=False)
    cfg = msg.display_path(real_home / _CFG / "deepctl" / "config.yaml")
    expected = (
        f"dg didn't use the API key in {cfg} (see the warning "
        "above), and no other API key is set."
    )
    assert str(err.value) == expected
    # guard() raises the coded auth_required (E-KEY-UNTRUSTED);
    # BaseCommand prints its two lines (test_credentials.py covers that).
    from deepctl_core import transport

    assert isinstance(err.value, transport.TransportError)
    assert err.value.code == "auth_required"
    output._output_config.update(agentic=True)
    try:
        result = transport.render(err.value, command_path="listen")
    finally:
        output._output_config.update(agentic=False)
    assert result.error == expected
    assert result.hint == FILE_HINT
    assert result.next_step is None
    text = f"{result.error} {result.hint}"
    assert "not set in the configuration file" not in text
    assert "deepctl login" not in text
    assert "dg login" not in text


def test_missing_key_outside_untrusted_case_unchanged(real_home, monkeypatch, capsys):
    from deepctl_core.auth import AuthenticationError, AuthManager

    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    _no_keyring(monkeypatch)
    agent(monkeypatch, True)
    plant(real_home / _CFG / "deepctl")
    with pytest.raises(AuthenticationError, match="DEEPGRAM_API_KEY is not set"):
        AuthManager(Config()).guard()


# ── Credential metadata, one hint per run, owner without W-PERM ───────────


class _DictKeyring:
    def __init__(self, entries):
        self.entries = dict(entries)

    def get_password(self, service, key):
        return self.entries.get(key)

    def set_password(self, service, key, value):
        self.entries[key] = value

    def delete_password(self, service, key):
        self.entries.pop(key, None)


def _dict_keyring(monkeypatch, entries):
    from deepctl_core import auth as auth_module

    kr = _DictKeyring(entries)
    for name in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(auth_module.keyring, name, getattr(kr, name))
    return kr


EXPIRED = "2000-01-01T00:00:00+00:00"


@pytest.mark.parametrize("expiry", [EXPIRED, "garbage"])
def test_agent_writable_keyless_file_expiry_never_deletes_keyring_key(
    real_home, monkeypatch, expiry
):
    """A keyless 0666 file's dg_token_expires_at isn't used."""
    from deepctl_core.auth import AuthManager

    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    kr = _dict_keyring(monkeypatch, {"api-key.default": "sk-PERSONKEY"})
    agent(monkeypatch, True)
    cfg = real_home / _CFG / "deepctl" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(yaml.safe_dump({"profiles": {"default": {"dg_token_expires_at": expiry}}}))
    cfg.chmod(0o666)
    config = Config()
    assert config._key_untrusted is True
    assert config.api_key_untrusted is False  # no key in the file
    assert config.get_profile().dg_token_expires_at is None
    assert AuthManager(config).get_api_key() == "sk-PERSONKEY"
    assert kr.entries == {"api-key.default": "sk-PERSONKEY"}
    assert (cfg.stat().st_mode & 0o777) == 0o666
    lines = [(t, h) for i, t, h, _ in config_module._pending_notices if i == "W-CFG-UNTRUSTED"]
    assert (
        f"Ignored profiles.default.dg_token_expires_at in {cfg}: in an AI agent session, "
        "the deepctl user config can't set this because the file can be changed by "
        "other users.",
        FILE_HINT,
    ) in lines
    assert "W-PERM-AGENT" not in ids()


def test_trusted_file_expiry_still_deletes_keyring_key(real_home, monkeypatch):
    """Control: the person's own 0600 file keeps today's expiry behaviour."""
    from deepctl_core.auth import AuthenticationError, AuthManager

    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    kr = _dict_keyring(monkeypatch, {"api-key.default": "sk-PERSONKEY"})
    agent(monkeypatch, True)
    cfg = real_home / _CFG / "deepctl" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(yaml.safe_dump({"profiles": {"default": {"dg_token_expires_at": EXPIRED}}}))
    cfg.chmod(0o600)
    with pytest.raises(AuthenticationError):
        AuthManager(Config()).get_api_key()
    assert kr.entries == {}


def test_agent_foreign_owned_key_file_gets_no_perm_line(real_home, monkeypatch):
    """Owner lines only; nothing names DEEPGRAM_API_KEY."""
    agent(monkeypatch, True)
    cfg = plant_with_key(real_home, 0o644)
    real_euid = os.geteuid()
    chmods = []
    monkeypatch.setattr(config_module.os, "geteuid", lambda: real_euid + 1)
    monkeypatch.setattr(config_module.os, "chmod", lambda *a, **k: chmods.append(a))
    config = Config()
    assert config.get_profile().api_key is None
    assert chmods == []
    assert not [i for i in ids() if i.startswith(("W-PERM", "I-PERM"))]
    assert not [t for t in notices() if "DEEPGRAM_API_KEY" in t]
    assert any(
        t.startswith("Ignored profiles.default.api_key") and t.endswith("owned by another user.")
        for t in notices()
    )
    assert (cfg.stat().st_mode & 0o777) == 0o644


def test_foreign_owned_key_file_outside_agent_mode_unchanged(real_home, monkeypatch):
    agent(monkeypatch, False)
    plant_with_key(real_home, 0o644)
    real_euid = os.geteuid()
    real_uid = os.getuid()
    monkeypatch.setattr(config_module.os, "geteuid", lambda: real_euid + 1)
    monkeypatch.setattr(config_module.os, "getuid", lambda: real_uid + 1)

    def deny(*args, **kwargs):
        raise PermissionError("not owner")

    monkeypatch.setattr(config_module.os, "chmod", deny)
    config = Config()
    assert config.get_profile().api_key == "k"
    texts = [t for i, t, _, _ in config_module._pending_notices if i == "W-PERM-FAILED"]
    assert len(texts) == 1 and "set DEEPGRAM_API_KEY instead" in texts[0]


def _render_guard_error(manager, error) -> None:
    """The two stderr lines BaseCommand prints for guard()'s coded error."""
    from deepctl_core import transport
    from rich.markup import escape

    result = transport.render(error, command_path="projects", auth_manager=manager)
    output.print_error(escape(result.error))
    output.print_hint(escape(result.hint))


def _run_projects_guard(capsys):
    """Config load + notices + guard, as a `dg projects` run renders them."""
    from deepctl_core.auth import AuthenticationError, AuthManager

    output._output_config.update(agentic=True)
    try:
        config = Config()
        config_module.flush_config_notices()
        manager = AuthManager(config)
        with pytest.raises(AuthenticationError) as raised:
            manager.guard()
        _render_guard_error(manager, raised.value)
    finally:
        output._output_config.update(agentic=False)
    return capsys.readouterr().err


@pytest.mark.parametrize("case", ["own_0666_hosts", "foreign_0644", "foreign_0666_two"])
def test_untrusted_file_hint_prints_once_per_run(real_home, monkeypatch, capsys, case):
    """Warnings, one hint, then the error."""
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    _no_keyring(monkeypatch)
    agent(monkeypatch, True)
    cfg = real_home / _CFG / "deepctl" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    profiles = {"default": {"api_key": "k"}}
    if case == "foreign_0666_two":
        profiles["work"] = {"api_key": "k2"}
    cfg.write_text(
        yaml.safe_dump({"profiles": profiles, "api": {"allowed_hosts": ["evil.example"]}})
    )
    cfg.chmod(0o644 if case == "foreign_0644" else 0o666)
    if case.startswith("foreign"):
        real_euid = os.geteuid()
        monkeypatch.setattr(config_module.os, "geteuid", lambda: real_euid + 1)
    err = _run_projects_guard(capsys)
    lines = [line for line in err.splitlines() if line.strip()]
    hints = [i for i, line in enumerate(lines) if line.startswith("HINT: ")]
    assert len(hints) == 1, err
    assert lines[hints[0]] == f"HINT: {FILE_HINT}"
    error = next(i for i, line in enumerate(lines) if line.startswith("ERROR: "))
    assert all(line.startswith("WARN: ") for line in lines[: hints[0]]), err
    assert hints[0] == error - 1, err


def test_untrusted_hint_follows_the_error_when_nothing_warned(real_home, monkeypatch, capsys):
    from deepctl_core.auth import AuthenticationError, AuthManager

    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    _no_keyring(monkeypatch)
    agent(monkeypatch, True)
    plant_with_key(real_home, 0o666)
    output._output_config.update(agentic=True)
    try:
        manager = AuthManager(Config())
        with pytest.raises(AuthenticationError) as raised:
            manager.guard()
        _render_guard_error(manager, raised.value)
    finally:
        output._output_config.update(agentic=False)
    lines = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
    assert lines[-1] == f"HINT: {FILE_HINT}"
    assert lines[-2].startswith("ERROR: ")


# ── Keyless untrusted files, unreadable real configs ─────────────


@pytest.mark.parametrize("owner", ["own_0666", "foreign_0644"])
def test_keyless_untrusted_file_gives_the_usual_missing_key_error(
    real_home, monkeypatch, capsys, owner
):
    """No key in the file, so no E_KEY_UNTRUSTED."""
    from deepctl_core.auth import AuthenticationError, AuthManager

    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    _no_keyring(monkeypatch)
    agent(monkeypatch, True)
    cfg = real_home / _CFG / "deepctl" / "config.yaml"
    plant(cfg.parent)
    if owner == "own_0666":
        cfg.chmod(0o666)
    else:
        cfg.chmod(0o644)
        real_euid = os.geteuid()
        monkeypatch.setattr(config_module.os, "geteuid", lambda: real_euid + 1)
    config = Config()
    assert config._key_untrusted is True
    assert config.api_key_untrusted is False
    with pytest.raises(AuthenticationError, match="DEEPGRAM_API_KEY is not set"):
        AuthManager(config).guard()
    captured = capsys.readouterr()
    assert "didn't use the API key" not in captured.out + captured.err


def test_broken_real_config_never_drops_a_custom_config(real_home, tmp_path, monkeypatch):
    """--config in an agent session while the real config is unparseable."""
    agent(monkeypatch, True)
    real = real_home / _CFG / "deepctl" / "config.yaml"
    real.parent.mkdir(parents=True)
    real.write_text("profiles:\n  default:\n    api_key: x project_id: [oops\n")
    real.chmod(0o600)
    custom = tmp_path / "custom.yaml"
    custom.write_text(yaml.safe_dump({"profiles": {"default": {"project_id": "p-custom"}}}))
    custom.chmod(0o600)
    config = Config(config_path=str(custom))
    assert config._load_failed is False
    assert config.get_profile().project_id == "p-custom"
    assert not [t for i, t, _, _ in config_module._pending_notices if i == "W-CFG-LOAD"]


def test_first_leaves_stops_early():
    import yaml as _yaml

    bomb = "a: &a {x: 1, y: 2}\nb: &b {p: *a, q: *a, r: *a}\nc: {s: *b, t: *b, u: *b}\n"
    data = _yaml.safe_load(bomb)
    assert config_module._first_leaves(data["c"], ("api",), 4) == [
        "api.s.p.x", "api.s.p.y", "api.s.q.x", "api.s.q.y",
    ]
    assert config_module._first_leaves({}, ("api",), 4) == ["api"]
    assert config_module._first_leaves(5, ("api",), 4) == ["api"]


@pytest.mark.parametrize("shape", ["directory", "deep"])
def test_unreadable_real_config_never_drops_a_custom_config(
    real_home, tmp_path, monkeypatch, shape
):
    """A real config that is a directory or nests too deeply."""
    agent(monkeypatch, True)
    real = real_home / _CFG / "deepctl" / "config.yaml"
    real.parent.mkdir(parents=True)
    if shape == "directory":
        real.mkdir()
    else:
        real.write_text("a: " + "[" * 5000 + "]" * 5000 + "\n")
        real.chmod(0o600)
    custom = tmp_path / "custom.yaml"
    custom.write_text(yaml.safe_dump({"profiles": {"default": {"project_id": "p-custom"}}}))
    custom.chmod(0o600)
    before = len(os.listdir(_FD_DIR))
    config = Config(config_path=str(custom))
    assert config._load_failed is False
    assert config.get_profile().project_id == "p-custom"
    assert config.allowed_hosts() == []
    assert not [t for i, t, _, _ in config_module._pending_notices if i == "W-CFG-LOAD"]
    import gc

    gc.collect()
    assert len(os.listdir(_FD_DIR)) <= before  # no fd left open


@pytest.mark.parametrize(
    "kind", ["fifo_root", "device_link_root", "fifo_custom", "dir_custom", "device_custom"]
)
def test_agent_non_regular_config_warns_like_outside_agent_mode(
    real_home, tmp_path, monkeypatch, kind
):
    """W-CFG-LOAD once, and an explicit save says why."""
    agent(monkeypatch, True)
    if kind == "fifo_root":
        path = real_home / _CFG / "deepctl" / "config.yaml"
        path.parent.mkdir(parents=True)
        os.mkfifo(path)
    elif kind == "device_link_root":
        path = real_home / _CFG / "deepctl" / "config.yaml"
        path.parent.mkdir(parents=True)
        path.symlink_to("/dev/null")
    elif kind == "fifo_custom":
        path = tmp_path / "c.yaml"
        os.mkfifo(path)
    elif kind == "dir_custom":
        path = tmp_path / "c.yaml"
        path.mkdir()
    else:
        path = Path("/dev/null")
    config = Config() if kind.endswith("_root") else Config(config_path=str(path))
    loads = [t for i, t, _, _ in config_module._pending_notices if i == "W-CFG-LOAD"]
    assert len(loads) == 1, loads
    assert loads[0].endswith(": not a regular file")
    assert config._load_failed is True
    config._set_config_value("output.format", "yaml")
    with pytest.raises(OSError) as err:
        config.save()
    assert "this config file isn't a regular file, so nothing was saved" in str(err.value)


@pytest.mark.parametrize("on", [False, True])
def test_default_config_never_trusted_while_rewrite_lock_stays_taken(
    real_home, monkeypatch, on
):
    """Another dg holds the default config's rewrite lock for the whole
    wait: nothing from the file is used, it isn't trusted, and telemetry is
    off for the run."""
    import fcntl

    agent(monkeypatch, on)
    plant(real_home / _CFG / "deepctl")
    monkeypatch.setattr(config_module, "_LOCK_TIMEOUT", 0.2)
    fd = os.open(real_home / _CFG / "deepctl" / "config.yaml", os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        config = Config()
    finally:
        os.close(fd)
    assert config._load_failed is True
    assert config.trusted_file is False
    assert config.allowed_hosts() == []
    assert config.get("tools.ffprobe_path") is None
    assert config.get("telemetry.enabled") is False
    assert "W-CFG-LOAD" in ids()
