"""Config.save() writes only the user-file layer plus explicit changes,
atomically, owner-only (0600 file, 0700 directory)."""

from __future__ import annotations

import os
import stat
import sys

import pytest
import yaml
from deepctl_core import config as config_module
from deepctl_core import output
from deepctl_core.config import Config, flush_config_notices

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config_module, "_pending_notices", [])
    monkeypatch.setattr(config_module, "_seen_notices", set())
    output._output_config.update(agentic=False, quiet=False)
    monkeypatch.setattr(output.stderr_console, "_width", 400)


def mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@posix_only
def test_umask_022_still_gives_0600_and_0700(tmp_path):
    old = os.umask(0o022)
    try:
        path = tmp_path / "new" / "deepctl" / "config.yaml"
        config = Config(config_path=str(path))
        config.create_profile("default", api_key="sk-FAKE")
    finally:
        os.umask(old)
    assert mode(path) == 0o600
    assert mode(path.parent) == 0o700


@posix_only
def test_default_config_dir_created_0700(tmp_path, monkeypatch):
    monkeypatch.setattr(
        config_module.platformdirs,
        "user_config_dir",
        lambda *a: str(tmp_path / "xdg" / "deepctl"),
    )
    old = os.umask(0o022)
    try:
        Config()
    finally:
        os.umask(old)
    assert mode(tmp_path / "xdg" / "deepctl") == 0o700


@posix_only
def test_existing_0644_file_replaced_at_0600(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("output:\n  format: json\n")
    path.chmod(0o644)
    config = Config(config_path=str(path))
    config._set_config_value("output.format", "yaml")
    config.save()
    assert mode(path) == 0o600


def test_env_layer_not_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "sk_env_SECRET")
    monkeypatch.setenv("DEEPGRAM_PROJECT_ID", "env-project")
    monkeypatch.setenv("DEEPGRAM_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("DEEPGRAM_PROFILE", "envprofile")
    path = tmp_path / "config.yaml"
    config = Config(config_path=str(path))
    assert config.get_profile("default").api_key == "sk_env_SECRET"
    config._set_config_value("update.installation_method", "pip")
    config.save()
    text = path.read_text()
    for value in ("sk_env_SECRET", "env-project", "127.0.0.1", "envprofile"):
        assert value not in text
    assert yaml.safe_load(text) == {"update": {"installation_method": "pip"}}


def test_round_trip_keeps_unknown_keys_and_opt_out(tmp_path):
    data = {
        "telemetry": {"enabled": False},
        "future_section": {"x": [1, 2]},
        "profiles": {"default": {"api_key": "k", "custom": "keep"}},
        "api": {"allowed_hosts": ["staging.example"]},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    config = Config(config_path=str(path))
    config._set_config_value("update.installation_method", "pip")
    config.save()
    saved = yaml.safe_load(path.read_text())
    assert saved["telemetry"] == {"enabled": False}
    assert saved["future_section"] == {"x": [1, 2]}
    assert saved["profiles"]["default"] == {"api_key": "k", "custom": "keep"}
    assert saved["api"] == {"allowed_hosts": ["staging.example"]}
    assert saved["update"] == {"installation_method": "pip"}


def test_explicit_value_equal_to_env_is_written(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "sk-SAME")
    path = tmp_path / "config.yaml"
    config = Config(config_path=str(path))
    config.create_profile("default", api_key="sk-SAME", project_id=None)
    assert yaml.safe_load(path.read_text())["profiles"]["default"] == {
        "api_key": "sk-SAME"
    }


def test_delete_profile_removes_it(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"profiles": {"a": {"api_key": "k"}, "b": {}}}))
    config = Config(config_path=str(path))
    config.delete_profile("a")
    assert yaml.safe_load(path.read_text()) == {"profiles": {"b": {}}}


def test_reading_profiles_does_not_write_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    config = Config(config_path=str(path))
    config.get_profile("x")
    config._config.active_profile = "x"
    config.save()
    assert yaml.safe_load(path.read_text()) == {"active_profile": "x"}


def test_no_temp_files_left_behind(tmp_path):
    path = tmp_path / "config.yaml"
    config = Config(config_path=str(path))
    config._set_config_value("output.format", "yaml")
    config.save()
    config.save()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.yaml"]


def test_failed_write_keeps_old_file(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("output:\n  format: json\n")
    config = Config(config_path=str(path))
    config._set_config_value("output.format", "yaml")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(config_module.yaml, "safe_dump", boom)
    with pytest.raises(OSError):
        config.save()
    assert path.read_text() == "output:\n  format: json\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.yaml"]


@posix_only
def test_0644_with_key_fixed_on_load(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    path.write_text("profiles:\n  default:\n    api_key: sk-FAKE\n")
    path.chmod(0o644)
    Config(config_path=str(path))
    Config(config_path=str(path))
    assert mode(path) == 0o600
    flush_config_notices()
    err = capsys.readouterr().err
    assert err.count("to owner-only access (0600) because it holds an API key") == 1
    assert "sk-FAKE" not in err


@posix_only
def test_0644_without_key_left_alone(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("output:\n  format: json\n")
    path.chmod(0o644)
    Config(config_path=str(path))
    assert mode(path) == 0o644


@posix_only
def test_perm_fixed_notice_respects_quiet(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    path.write_text("profiles:\n  default:\n    api_key: sk-FAKE\n")
    path.chmod(0o640)
    Config(config_path=str(path))
    output._output_config["quiet"] = True
    flush_config_notices()
    assert capsys.readouterr().err == ""


@posix_only
def test_chmod_failure_warns_even_when_quiet(tmp_path, capsys, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("api_key: sk-FAKE\n")
    path.chmod(0o644)

    def deny(*args, **kwargs):
        raise PermissionError("not owner")

    monkeypatch.setattr(config_module.os, "chmod", deny)
    Config(config_path=str(path))
    output._output_config["quiet"] = True
    flush_config_notices()
    err = capsys.readouterr().err
    assert "holds an API key and other users can read it (mode 0644)" in err
    assert "chmod 600" in err


@posix_only
def test_symlinked_config_written_through(tmp_path):
    """Dotfile managers link config.yaml; saving keeps the link."""
    target = tmp_path / "dotfiles" / "deepctl.yaml"
    target.parent.mkdir()
    target.parent.chmod(0o755)
    target.write_text("output:\n  format: json\n")
    target.chmod(0o640)
    link = tmp_path / "cfg" / "config.yaml"
    link.parent.mkdir()
    link.symlink_to(target)
    config = Config(config_path=str(link))
    config._set_config_value("output.format", "yaml")
    config.save()
    assert link.is_symlink()
    assert yaml.safe_load(target.read_text()) == {"output": {"format": "yaml"}}
    assert mode(target) == 0o600
    assert mode(target.parent) == 0o755  # the dotfiles directory is left alone
    assert sorted(p.name for p in target.parent.iterdir()) == ["deepctl.yaml"]


@posix_only
def test_unreplaceable_file_rewritten_in_place(tmp_path, monkeypatch):
    """A single-file bind mount can't be renamed over (EBUSY)."""
    import errno as errno_module

    path = tmp_path / "config.yaml"
    path.write_text("output:\n  format: json\n")
    path.chmod(0o644)
    config = Config(config_path=str(path))
    config._set_config_value("output.format", "yaml")

    def busy(src, dst):
        raise OSError(errno_module.EBUSY, "Device or resource busy")

    monkeypatch.setattr(config_module.os, "replace", busy)
    config.save()
    assert yaml.safe_load(path.read_text()) == {"output": {"format": "yaml"}}
    assert mode(path) == 0o600
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.yaml"]


@posix_only
def test_other_replace_errors_are_raised(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    config = Config(config_path=str(path))
    config._set_config_value("output.format", "yaml")

    def denied(src, dst):
        raise PermissionError("denied")

    monkeypatch.setattr(config_module.os, "replace", denied)
    with pytest.raises(PermissionError):
        config.save()
    assert sorted(p.name for p in tmp_path.iterdir()) == []


@posix_only
def test_existing_default_dir_tightened_to_0700(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "xdg" / "deepctl"
    cfg_dir.mkdir(parents=True)
    cfg_dir.chmod(0o755)
    monkeypatch.setattr(
        config_module, "_default_config_file", lambda: cfg_dir / "config.yaml"
    )
    config = Config(config_path=str(cfg_dir / "config.yaml"))
    config._set_config_value("output.format", "yaml")
    config.save()
    assert mode(cfg_dir) == 0o700


@posix_only
def test_other_dir_not_tightened(tmp_path):
    other = tmp_path / "shared"
    other.mkdir()
    other.chmod(0o755)
    config = Config(config_path=str(other / "dg.yaml"))
    config._set_config_value("output.format", "yaml")
    config.save()
    assert mode(other) == 0o755


def test_update_profile_keeps_env_values_out(tmp_path, monkeypatch):
    """projects --set-default and logout --keep-config change one field."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "sk_env_SECRET")
    monkeypatch.setenv("DEEPGRAM_PROJECT_ID", "env-project")
    path = tmp_path / "config.yaml"
    config = Config(config_path=str(path))
    config.update_profile("default", project_id="P9")
    config.update_profile("default", api_key=None, dg_token_expires_at=None)
    text = path.read_text()
    assert "sk_env_SECRET" not in text
    assert "env-project" not in text
    assert yaml.safe_load(text) == {"profiles": {"default": {"project_id": "P9"}}}


@posix_only
@pytest.mark.skipif(
    getattr(os, "geteuid", lambda: 0)() == 0, reason="root ignores directory modes"
)
def test_unwritable_dir_rewrites_file_in_place(tmp_path):
    """A directory we can't create files in (0500, or a root-owned bind mount
    parent): the key still comes out of the file."""
    cfg_dir = tmp_path / "ro"
    cfg_dir.mkdir()
    path = cfg_dir / "config.yaml"
    path.write_text("profiles:\n  default:\n    api_key: sk-SENTINEL\n")
    path.chmod(0o600)
    cfg_dir.chmod(0o500)
    try:
        config = Config(config_path=str(path))
        config.update_profile("default", api_key=None, dg_token_expires_at=None)
        assert "sk-SENTINEL" not in path.read_text()
        assert mode(path) == 0o600
    finally:
        cfg_dir.chmod(0o700)


@posix_only
def test_unwritable_dir_fallback_simulated(tmp_path, monkeypatch):
    """Same fallback, driven by the errno (also covers running as root)."""
    import errno as errno_module

    path = tmp_path / "config.yaml"
    path.write_text("profiles:\n  default:\n    api_key: sk-SENTINEL\n")
    config = Config(config_path=str(path))
    real_open = os.open

    def no_tmp(file, flags, *args):
        if str(file).endswith(".tmp"):
            raise PermissionError(errno_module.EACCES, "Permission denied")
        return real_open(file, flags, *args)

    monkeypatch.setattr(config_module.os, "open", no_tmp)
    config.update_profile("default", api_key=None)
    assert "sk-SENTINEL" not in path.read_text()


@posix_only
def test_fchmod_failure_tolerated(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("output:\n  format: json\n")
    monkeypatch.setattr(
        config_module.os, "fchmod", lambda *a: (_ for _ in ()).throw(PermissionError())
    )
    config_module._rewrite_in_place(path, "output:\n  format: yaml\n")
    assert path.read_text() == "output:\n  format: yaml\n"


@posix_only
def test_symlinked_config_dir_not_tightened(tmp_path, monkeypatch):
    real_dir = tmp_path / "dotfiles" / "deepctl"
    real_dir.mkdir(parents=True)
    real_dir.chmod(0o755)
    link_dir = tmp_path / "xdg" / "deepctl"
    link_dir.parent.mkdir()
    link_dir.symlink_to(real_dir, target_is_directory=True)
    monkeypatch.setattr(
        config_module, "_default_config_file", lambda: link_dir / "config.yaml"
    )
    config = Config(config_path=str(link_dir / "config.yaml"))
    config._set_config_value("output.format", "yaml")
    config.save()
    assert mode(real_dir) == 0o755


def test_files_written_with_lf_only(tmp_path):
    path = tmp_path / "config.yaml"
    config = Config(config_path=str(path))
    config._set_config_value("output.format", "yaml")
    config.save()
    assert b"\r" not in path.read_bytes()


def test_unreadable_working_directory_is_not_fatal(tmp_path, monkeypatch):
    """A project file that can't be checked is treated as absent."""
    path = tmp_path / "config.yaml"
    real_exists = config_module.Path.exists

    def exists(self):
        if self.name == "deepgram.yaml":
            raise PermissionError("Permission denied")
        return real_exists(self)

    monkeypatch.setattr(config_module.Path, "exists", exists)
    config = Config(config_path=str(path))
    assert config.get("output.format") == "json"


@posix_only
def test_in_place_rewrite_refuses_hard_links(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("output:\n  format: json\n")
    os.link(path, tmp_path / "other.yaml")
    with pytest.raises(OSError):
        config_module._rewrite_in_place(path, "output:\n  format: yaml\n")
    assert path.read_text() == "output:\n  format: json\n"


@posix_only
def test_in_place_rewrite_keeps_key_out_if_mode_stays_open(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("output:\n  format: json\n")
    path.chmod(0o644)

    def deny(*args, **kwargs):
        raise PermissionError("not owner")

    monkeypatch.setattr(config_module.os, "fchmod", deny)
    with pytest.raises(PermissionError):
        config_module._rewrite_in_place(path, "api_key: sk-SENTINEL\n")
    assert "sk-SENTINEL" not in path.read_text()


OLD_CFG = "profiles:\n  default:\n    project_id: p1\ntelemetry:\n  enabled: false\n"


def _valid_old_or_new(path, new_text):
    data = yaml.safe_load(path.read_text())
    return data in (yaml.safe_load(OLD_CFG), yaml.safe_load(new_text))


@posix_only
@pytest.mark.parametrize(
    ("grow", "kill_at"), [(False, 1), (False, 2), (True, 1), (True, 2), (True, 3)]
)
def test_in_place_rewrite_survives_a_kill(tmp_path, monkeypatch, grow, kill_at):
    """Stop the rewrite at each fsync: the file is always the old or the new
    config (plus blank lines), never truncated."""
    path = tmp_path / "config.yaml"
    path.write_text(OLD_CFG)
    path.chmod(0o600)
    new_text = (
        "profiles:\n  default:\n    project_id: p1\n" + "    extra: " + "x" * 200 + "\n"
        if grow
        else "profiles: {}\n"
    )
    real_fsync = os.fsync
    calls = {"n": 0}

    class Killed(BaseException):
        pass

    def fsync(fd):
        calls["n"] += 1
        if calls["n"] == kill_at:
            raise Killed
        real_fsync(fd)

    monkeypatch.setattr(config_module.os, "fsync", fsync)
    with pytest.raises(Killed):
        config_module._rewrite_in_place(path, new_text)
    assert _valid_old_or_new(path, new_text)


@posix_only
def test_in_place_rewrite_full_disk_keeps_old_content(tmp_path, monkeypatch):
    import errno as errno_module

    path = tmp_path / "config.yaml"
    path.write_text(OLD_CFG)
    path.chmod(0o600)
    real_write = os.write

    def write(fd, data):
        real_write(fd, bytes(data[: len(data) // 2]))
        raise OSError(errno_module.ENOSPC, "No space left on device")

    monkeypatch.setattr(config_module.os, "write", write)
    with pytest.raises(OSError):
        config_module._rewrite_in_place(path, OLD_CFG + "extra: " + "y" * 500 + "\n")
    assert path.read_text() == OLD_CFG


@posix_only
def test_in_place_rewrite_shrinks_to_exact_bytes(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(OLD_CFG)
    path.chmod(0o600)
    config_module._rewrite_in_place(path, "profiles: {}\n")
    assert path.read_bytes() == b"profiles: {}\n"


# ── Unreadable configs and concurrent saves ──────────────────────────

TYPO_CFG = (
    "profiles:\n  default:\n    api_key: sk-SENTINEL\n  bad: [unclosed\n"
    "telemetry:\n  enabled: false\n"
)


def test_unparseable_config_is_never_overwritten(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(TYPO_CFG)
    config = Config(config_path=str(path))
    config._set_config_value("update.installation_method", "pip")
    with pytest.raises(OSError) as err:
        config.save()
    assert err.value.filename == str(path)
    config.save(implicit=True)  # bookkeeping writes skip quietly
    assert path.read_text() == TYPO_CFG


def test_save_errors_name_the_config_file(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    config = Config(config_path=str(path))
    config._set_config_value("output.format", "yaml")

    def boom(*args, **kwargs):
        raise PermissionError(13, "Permission denied", str(tmp_path / ".x.tmp"))

    monkeypatch.setattr(config_module, "_rewrite_in_place", boom)
    monkeypatch.setattr(config_module.os, "replace", boom)
    with pytest.raises(OSError) as err:
        config.save()
    assert err.value.filename == str(path)


@posix_only
def test_chmod_failure_on_foreign_file_gives_no_chmod_advice(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("api_key: sk-FAKE\n")
    path.chmod(0o644)
    real_stat = os.stat

    def foreign_stat(p, *a, **k):
        st = real_stat(p, *a, **k)
        if str(p) == str(path):
            fields = list(st)
            fields[4] = st.st_uid + 1  # another owner
            return os.stat_result(fields)
        return st

    def deny(*args, **kwargs):
        raise PermissionError("not owner")

    monkeypatch.setattr(config_module.os, "stat", foreign_stat)
    monkeypatch.setattr(config_module.os, "chmod", deny)
    Config(config_path=str(path))
    texts = [t for _, t, _, _ in config_module._pending_notices]
    assert any("dg can't change that file" in t for t in texts)
    assert not any("chmod 600" in t for t in texts)


def _concurrent_saver(args):
    path, i = args
    from deepctl_core.config import Config as _Config

    for k in range(15):
        c = _Config(config_path=path)
        c.update_profile("default", project_id=f"p{i}-{k}" + "x" * ((i * 7 + k) % 40))


@posix_only
@pytest.mark.parametrize("in_place", [False, True])
def test_concurrent_saves_keep_keys_and_opt_out(tmp_path, monkeypatch, in_place):
    """8 dg processes saving at once: the file stays valid, with the key,
    the opt-out and the allowlist kept."""
    import multiprocessing as mp

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    path = cfg_dir / "config.yaml"
    path.write_text(
        "profiles:\n  default:\n    api_key: sk-SENTINEL\n    project_id: p0\n"
        "telemetry:\n  enabled: false\napi:\n  allowed_hosts:\n  - proxy.example\n"
    )
    path.chmod(0o600)
    if in_place:
        if getattr(os, "geteuid", lambda: 0)() == 0:
            pytest.skip("root ignores directory modes")
        cfg_dir.chmod(0o500)
    # 120 serialized saves: on a loaded host one process can wait past the
    # 5 s lock bound and get "another process is writing" (by design). That
    # isn't what this test checks; the forked workers inherit a longer bound.
    monkeypatch.setattr(config_module, "_LOCK_TIMEOUT", 120.0)
    try:
        with mp.get_context("fork").Pool(8) as pool:
            pool.map(_concurrent_saver, [(str(path), i) for i in range(8)])
    finally:
        cfg_dir.chmod(0o700)
    data = yaml.safe_load(path.read_text())
    assert data["profiles"]["default"]["api_key"] == "sk-SENTINEL"
    assert data["telemetry"] == {"enabled": False}
    assert data["api"] == {"allowed_hosts": ["proxy.example"]}
    assert sorted(p.name for p in cfg_dir.iterdir()) == ["config.yaml"]


@posix_only
def test_load_during_in_place_rewrite_sees_old_or_new(tmp_path, monkeypatch):
    """A config load while another dg rewrites the file in place (a bind
    mount, a read-only directory) reads the old or the new YAML, never half
    of each: the writer is held halfway through its write while the load
    runs. Without the read lock the load parsed a mix of both files (or
    failed and fell back to defaults, telemetry opt-out included)."""
    import threading

    monkeypatch.setattr(output, "is_ai_agent", lambda: False, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config_module, "_LOCK_TIMEOUT", 60.0)
    path = tmp_path / "config.yaml"
    old = (
        "profiles:\n  default:\n    api_key: sk-SENTINEL\n"
        "    project_id: p-" + "a" * 60 + "\n"
        "telemetry:\n  enabled: false\napi:\n  allowed_hosts:\n  - proxy.example\n"
    )
    new = old.replace("p-" + "a" * 60, "p-b")
    path.write_text(old)
    path.chmod(0o600)

    halfway = threading.Event()
    resume = threading.Event()
    real_write_all = config_module._write_all

    def paused_write_all(fd, data, offset):
        if offset == 0 and not halfway.is_set():
            cut = len(new) // 2
            real_write_all(fd, data[:cut], 0)
            halfway.set()
            assert resume.wait(60)
            real_write_all(fd, data[cut:], cut)
        else:
            real_write_all(fd, data, offset)

    monkeypatch.setattr(config_module, "_write_all", paused_write_all)
    writer = threading.Thread(target=config_module._rewrite_in_place, args=(path, new))
    writer.start()
    assert halfway.wait(60)
    loaded = {}

    def load():
        loaded["config"] = Config(config_path=str(path))

    reader = threading.Thread(target=load)
    reader.start()
    # Unlocked, the load finishes while the writer is paused (the mixed
    # file); locked, it waits for the writer. Either way, then resume it.
    reader.join(2.0)
    resume.set()
    writer.join(60)
    reader.join(60)
    c = loaded["config"]
    assert not c._load_failed
    assert c._user_data == yaml.safe_load(new)
    assert c.get("telemetry.enabled") is False
    assert yaml.safe_load(path.read_text()) == yaml.safe_load(new)


_BUSY_TEXT = (
    "profiles:\n  default:\n    api_key: sk-SENTINEL\ntelemetry:\n  enabled: false\n"
)


def _hold_rewrite_lock(path) -> int:
    """Hold the lock an in-place rewrite holds, from another open file."""
    import fcntl

    fd = os.open(path, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


@posix_only
def test_load_while_rewrite_lock_stays_taken_uses_nothing(tmp_path, monkeypatch):
    """Another dg holds the rewrite lock for the whole wait: the load uses
    nothing from the file, telemetry is off for the run, and a save is
    refused with the file kept as it was."""
    monkeypatch.setattr(output, "is_ai_agent", lambda: False, raising=False)
    monkeypatch.setattr(config_module, "_LOCK_TIMEOUT", 0.2)
    path = tmp_path / "config.yaml"
    path.write_text(_BUSY_TEXT)
    path.chmod(0o600)
    fd = _hold_rewrite_lock(path)
    try:
        c = Config(config_path=str(path))
        assert c._load_failed and c._load_busy
        assert c._user_data == {}
        assert c.get("telemetry.enabled") is False
        assert c.get_profile().api_key is None
        assert any(
            "another process is writing it" in str(n)
            for n in config_module._pending_notices
        )
        c._set_config_value("output.format", "yaml")
        with pytest.raises(OSError) as err:
            c.save()
        assert "another process is writing this config" in str(err.value)
        c.save(implicit=True)  # skipped quietly
    finally:
        os.close(fd)
    assert path.read_text() == _BUSY_TEXT


@posix_only
def test_save_gives_up_while_rewrite_lock_stays_taken(tmp_path, monkeypatch):
    """The save's own read waits for the rewrite lock too, and reports the
    other process when it stays taken."""
    monkeypatch.setattr(output, "is_ai_agent", lambda: False, raising=False)
    monkeypatch.setattr(config_module, "_LOCK_TIMEOUT", 0.2)
    path = tmp_path / "config.yaml"
    path.write_text(_BUSY_TEXT)
    path.chmod(0o600)
    c = Config(config_path=str(path))
    assert not c._load_failed
    c._set_config_value("output.format", "yaml")
    fd = _hold_rewrite_lock(path)
    try:
        with pytest.raises(OSError) as err:
            c.save()
        assert "another process is writing this config" in str(err.value)
    finally:
        os.close(fd)
    assert path.read_text() == _BUSY_TEXT


@posix_only
@pytest.mark.parametrize("no_lock", ["ENOLCK", "EOPNOTSUPP", "no_fcntl"])
def test_load_without_working_flock_reads_unlocked(tmp_path, monkeypatch, no_lock):
    """Where flock doesn't exist (Windows) or doesn't work (some network
    file systems), the read goes ahead unlocked, as the rewrite does."""
    import errno
    import fcntl

    monkeypatch.setattr(output, "is_ai_agent", lambda: False, raising=False)
    if no_lock == "no_fcntl":
        monkeypatch.setitem(sys.modules, "fcntl", None)
    else:
        real = fcntl.flock

        def flock(fd, op):
            if op & fcntl.LOCK_SH:
                raise OSError(getattr(errno, no_lock), "no locks")
            return real(fd, op)

        monkeypatch.setattr(fcntl, "flock", flock)
    path = tmp_path / "config.yaml"
    path.write_text(_BUSY_TEXT)
    path.chmod(0o600)
    c = Config(config_path=str(path))
    assert not c._load_failed
    assert c.get("telemetry.enabled") is False
    assert c.get_profile().api_key == "sk-SENTINEL"


@posix_only
def test_read_lock_is_shared_and_waits_for_a_rewrite(tmp_path, monkeypatch):
    """Config reads take a shared lock: while one load holds it, another
    reader takes it too, and a rewrite's exclusive lock can't. An
    exclusive holder (a rewrite) keeps every load waiting."""
    import fcntl
    import threading

    monkeypatch.setattr(output, "is_ai_agent", lambda: False, raising=False)
    path = tmp_path / "config.yaml"
    path.write_text(_BUSY_TEXT)
    path.chmod(0o600)
    seen = []
    real_lock = config_module._flock_shared_bounded

    def probe(fd):
        locked = real_lock(fd)
        other = os.open(path, os.O_RDWR)
        try:
            fcntl.flock(other, fcntl.LOCK_SH | fcntl.LOCK_NB)  # a second reader
            seen.append("shared")
            fcntl.flock(other, fcntl.LOCK_UN)
            try:
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)  # a rewrite
                seen.append("exclusive")
            except BlockingIOError:
                pass
        finally:
            os.close(other)
        return locked

    monkeypatch.setattr(config_module, "_flock_shared_bounded", probe)
    c = Config(config_path=str(path))
    assert not c._load_failed
    assert seen and set(seen) == {"shared"}, seen

    # Another reader already holds it shared: a load doesn't wait.
    monkeypatch.setattr(config_module, "_flock_shared_bounded", real_lock)
    monkeypatch.setattr(config_module, "_LOCK_TIMEOUT", 2.0)
    held = os.open(path, os.O_RDONLY)
    try:
        fcntl.flock(held, fcntl.LOCK_SH)
        c = Config(config_path=str(path))
        assert not c._load_failed and not c._load_busy
    finally:
        os.close(held)

    # An exclusive holder blocks two loads at once; both use nothing.
    monkeypatch.setattr(config_module, "_LOCK_TIMEOUT", 0.3)
    loads = []
    held = _hold_rewrite_lock(path)
    try:
        threads = [
            threading.Thread(
                target=lambda: loads.append(Config(config_path=str(path)))
            )
            for _ in range(2)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
    finally:
        os.close(held)
    assert len(loads) == 2
    assert all(c._load_busy and c.get("telemetry.enabled") is False for c in loads)


@posix_only
def test_read_lock_is_held_only_for_the_read(tmp_path):
    """The shared lock is dropped once the bytes are read, so an open
    config never holds up a rewrite."""
    import fcntl

    path = tmp_path / "config.yaml"
    path.write_text(_BUSY_TEXT)
    with config_module._open_config(path) as reader:
        assert reader.load()["telemetry"] == {"enabled": False}
        fd = os.open(path, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)


@posix_only
def test_concurrent_in_place_writers_never_interleave(tmp_path, monkeypatch):
    """With the fsync window widened, two writers on one file always leave
    exactly one of them."""
    import threading
    import time

    path = tmp_path / "cfg.yaml"
    real_fsync = os.fsync

    def slow_fsync(fd):
        real_fsync(fd)
        time.sleep(0.01)

    monkeypatch.setattr(config_module.os, "fsync", slow_fsync)
    a = yaml.safe_dump({"api_key": "sk-LONG" + "x" * 300, "profiles": {"default": {"project_id": "A"}}})
    b = yaml.safe_dump({"profiles": {"default": {"project_id": "B"}}})
    for _ in range(10):
        path.write_text("profiles: {}\n")
        path.chmod(0o600)
        threads = [
            threading.Thread(target=config_module._rewrite_in_place, args=(path, t))
            for t in (a, b)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        data = path.read_bytes()
        assert b"\x00" not in data
        assert data in (a.encode(), b.encode())


def _run_in_child(target, *args):
    import multiprocessing as mp
    from pathlib import Path

    if "fork" in mp.get_all_start_methods():
        proc = mp.get_context("fork").Process(target=target, args=args)
        proc.start()
    else:
        # Windows: spawn imports this module by name, so the child needs
        # its package root on sys.path (pytest's importlib mode skips that).
        root = str(Path(__file__).parents[__name__.count(".")])
        sys.path.insert(0, root)
        try:
            proc = mp.get_context("spawn").Process(target=target, args=args)
            proc.start()
        finally:
            sys.path.remove(root)
    proc.join(30)
    assert proc.exitcode == 0


def _edit_opt_out(path):
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data["telemetry"] = {"enabled": False}
    data["future_section"] = {"kept": True}
    with open(path, "w", encoding="utf-8") as f:
        f.write(yaml.safe_dump(data))


def _save_other_profile(path):
    from deepctl_core.config import Config as _Config

    _Config(config_path=path).update_profile("other", project_id="pB")


@posix_only
def test_save_keeps_what_another_process_saved_after_load(tmp_path):
    """A loads, B saves the opt-out and a profile, A saves; the
    file has A's change on top of B's, not A's startup snapshot."""
    path = tmp_path / "config.yaml"
    path.write_text("profiles:\n  default:\n    api_key: sk-FAKE\n    project_id: p0\n")
    path.chmod(0o600)
    a = Config(config_path=str(path))
    _run_in_child(_edit_opt_out, str(path))
    _run_in_child(_save_other_profile, str(path))
    a.update_profile("default", project_id="pA")
    data = yaml.safe_load(path.read_text())
    assert data["telemetry"] == {"enabled": False}
    assert data["future_section"] == {"kept": True}
    assert data["profiles"]["default"] == {"api_key": "sk-FAKE", "project_id": "pA"}
    assert data["profiles"]["other"]["project_id"] == "pB"
    assert mode(path) == 0o600


def test_save_refuses_a_file_broken_since_load(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("output:\n  format: json\n")
    config = Config(config_path=str(path))
    path.write_text(TYPO_CFG)
    config._set_config_value("output.format", "yaml")
    with pytest.raises(OSError) as err:
        config.save()
    assert err.value.filename == str(path)
    config.save(implicit=True)
    assert path.read_text() == TYPO_CFG


def _hold_flock(target, ready, release):
    import fcntl

    fd = os.open(target, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    ready.set()
    release.wait(60)


@posix_only
@pytest.mark.parametrize("held", ["directory", "file"])
def test_save_gives_up_on_a_lock_held_by_another_process(tmp_path, monkeypatch, held):
    """A stuck lock holder can't hang dg. An explicit save
    fails naming the file after about 5 s; an implicit one skips."""
    import errno as errno_module
    import multiprocessing as mp
    import time

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    path = cfg_dir / "config.yaml"
    original = "telemetry:\n  enabled: false\n"
    path.write_text(original)
    path.chmod(0o600)
    if held == "file":
        # The in-place path (a bind mount) takes the file's own lock.
        def busy(src, dst):
            raise OSError(errno_module.EBUSY, "Device or resource busy")

        monkeypatch.setattr(config_module.os, "replace", busy)
    ctx = mp.get_context("fork")
    ready, release = ctx.Event(), ctx.Event()
    target = cfg_dir if held == "directory" else path
    holder = ctx.Process(target=_hold_flock, args=(str(target), ready, release))
    holder.start()
    try:
        assert ready.wait(10)
        # A held file lock is the in-place rewrite's: the load waits on it
        # and uses nothing from the file, and the save is then refused.
        start = time.monotonic()
        config = Config(config_path=str(path))
        assert config._load_busy is (held == "file")
        config._set_config_value("output.format", "yaml")
        with pytest.raises(OSError) as err:
            config.save()
        elapsed = time.monotonic() - start
        assert err.value.errno == errno_module.EWOULDBLOCK
        assert err.value.filename == str(path)
        assert "another process is writing this config, so nothing was saved" in str(
            err.value
        )
        assert 4.0 <= elapsed < 6.5
        monkeypatch.setattr(config_module, "_LOCK_TIMEOUT", 0.2)
        config.save(implicit=True)  # returns without writing
        assert path.read_text() == original
        assert sorted(p.name for p in cfg_dir.iterdir()) == ["config.yaml"]
    finally:
        release.set()
        holder.join(10)
    if held == "file":
        # That load used nothing from the file: load it again to save.
        config = Config(config_path=str(path))
        config._set_config_value("output.format", "yaml")
    config.save()  # the lock is free again
    assert yaml.safe_load(path.read_text())["output"]["format"] == "yaml"


@posix_only
def test_save_goes_ahead_where_flock_does_not_work(tmp_path, monkeypatch):
    """e.g. NFS without lock support: save writes unlocked."""
    import errno as errno_module
    import fcntl

    def no_locks(fd, op):
        raise OSError(errno_module.ENOLCK, "No locks available")

    monkeypatch.setattr(fcntl, "flock", no_locks)
    path = tmp_path / "config.yaml"
    config = Config(config_path=str(path))
    config._set_config_value("output.format", "yaml")
    config.save()
    assert yaml.safe_load(path.read_text())["output"]["format"] == "yaml"


# ── Empty sections, non-regular targets ──────────────────────────


def _create_profile_b(path):
    from deepctl_core.config import Config as _Config

    _Config(config_path=path).create_profile("b", api_key="kb")


@pytest.mark.parametrize("start", ["telemetry:\n  enabled: false\nfoo_unknown: 1\n", "{}\n"])
def test_first_logins_from_a_file_with_no_profiles_keep_each_other(tmp_path, start):
    """Two first-time logins; the second to save
    keeps the profile and key the first one wrote."""
    path = tmp_path / "config.yaml"
    path.write_text(start)
    path.chmod(0o600)
    a = Config(config_path=str(path))
    _run_in_child(_create_profile_b, str(path))
    a.create_profile("a", api_key="ka")
    data = yaml.safe_load(path.read_text())
    assert data["profiles"]["a"]["api_key"] == "ka"
    assert data["profiles"]["b"]["api_key"] == "kb"
    if "foo_unknown" in start:
        assert data["telemetry"] == {"enabled": False}
        assert data["foo_unknown"] == 1


def test_reading_a_profile_then_saving_keeps_profiles_added_since(tmp_path):
    """Reading a profile with get_profile(), then an unrelated save."""
    path = tmp_path / "config.yaml"
    path.write_text("telemetry:\n  enabled: true\n")
    a = Config(config_path=str(path))
    a.get_profile("default")
    _run_in_child(_create_profile_b, str(path))
    a._set_config_value("output.format", "yaml")
    a.save()
    data = yaml.safe_load(path.read_text())
    assert data["profiles"]["b"]["api_key"] == "kb"
    assert data["output"]["format"] == "yaml"


def test_first_logins_in_real_processes_keep_each_other(tmp_path):
    """A loads and waits at a prompt, B logs in and
    finishes, then A finishes."""
    import subprocess
    import time

    path = tmp_path / "config.yaml"
    path.write_text("{}\n")
    path.chmod(0o600)
    go = tmp_path / "go"
    loaded = tmp_path / "loaded"
    code = (
        "import os, time\n"
        "from deepctl_core.config import Config\n"
        f"a = Config(config_path={str(path)!r})\n"
        f"open({str(loaded)!r}, 'w').close()\n"
        f"while not os.path.exists({str(go)!r}):\n"
        "    time.sleep(0.02)\n"
        "a.create_profile('a', api_key='ka')\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", code])
    try:
        deadline = time.monotonic() + 30
        while not loaded.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        subprocess.run(
            [
                sys.executable,
                "-c",
                "from deepctl_core.config import Config; "
                f"Config(config_path={str(path)!r}).create_profile('b', api_key='kb')",
            ],
            check=True,
            timeout=60,
        )
        go.write_text("1")
        assert proc.wait(60) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
    data = yaml.safe_load(path.read_text())
    assert data["profiles"]["a"]["api_key"] == "ka"
    assert data["profiles"]["b"]["api_key"] == "kb"


def test_emptied_section_is_still_removed(tmp_path):
    """A save keeps another process's section only if it gained keys; one
    this process emptied on purpose still goes."""
    path = tmp_path / "config.yaml"
    path.write_text("profiles:\n  old:\n    project_id: p\n")
    config = Config(config_path=str(path))
    config.delete_profile("old")
    assert "old" not in (yaml.safe_load(path.read_text()).get("profiles") or {})


NOT_REGULAR = "this config file isn't a regular file, so nothing was saved"


@posix_only
def test_config_linked_to_dev_null_is_never_replaced(tmp_path):
    """Persistence disabled with a /dev/null link."""
    import errno as errno_module

    path = tmp_path / "config.yaml"
    path.symlink_to("/dev/null")
    config = Config(config_path=str(path))
    config._set_config_value("output.format", "yaml")
    with pytest.raises(OSError) as err:
        config.save()
    assert err.value.errno == errno_module.EINVAL
    assert err.value.filename == str(path)
    assert NOT_REGULAR in str(err.value)
    config.save(implicit=True)
    assert stat.S_ISCHR(os.stat("/dev/null").st_mode)
    assert path.is_symlink()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.yaml"]


@posix_only
@pytest.mark.timeout(20)
def test_fifo_config_is_never_replaced(tmp_path):
    """Refused when the save reads it (the rename path)."""
    import errno as errno_module

    path = tmp_path / "config.yaml"
    path.write_text("output:\n  format: json\n")
    config = Config(config_path=str(path))
    path.unlink()
    os.mkfifo(path)
    config._set_config_value("output.format", "yaml")
    with pytest.raises(OSError) as err:
        config.save()
    assert err.value.errno == errno_module.EINVAL
    assert NOT_REGULAR in str(err.value)
    config.save(implicit=True)
    assert stat.S_ISFIFO(os.lstat(path).st_mode)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.yaml"]


@posix_only
@pytest.mark.timeout(20)
def test_fifo_appearing_before_in_place_rewrite_is_refused(tmp_path, monkeypatch):
    """The in-place path itself refuses a target that became a FIFO after
    the read, with the usual refusal sentence."""
    import errno as errno_module

    path = tmp_path / "config.yaml"
    path.write_text("output:\n  format: json\n")
    config = Config(config_path=str(path))

    def busy_then_fifo(src, dst):
        os.unlink(dst)
        os.mkfifo(dst)
        raise OSError(errno_module.EBUSY, "Device or resource busy")

    monkeypatch.setattr(config_module.os, "replace", busy_then_fifo)
    config._set_config_value("output.format", "yaml")
    with pytest.raises(OSError) as err:
        config.save()
    assert err.value.errno == errno_module.EINVAL
    assert str(err.value) == f"[Errno 22] {NOT_REGULAR}: {str(path)!r}"
    assert stat.S_ISFIFO(os.lstat(path).st_mode)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.yaml"]


@posix_only
@pytest.mark.timeout(20)
def test_in_place_rewrite_refuses_a_fifo(tmp_path):
    path = tmp_path / "pipe"
    os.mkfifo(path)
    import errno as errno_module

    with pytest.raises(OSError) as err:
        config_module._rewrite_in_place(path, "output:\n  format: yaml\n")
    assert isinstance(err.value, config_module._NotRegularFile)
    assert err.value.errno == errno_module.EINVAL
    assert NOT_REGULAR in str(err.value)
    assert stat.S_ISFIFO(os.lstat(path).st_mode)


@posix_only
def test_check_saveable_refuses_a_dev_null_link(tmp_path):
    import errno as errno_module

    path = tmp_path / "config.yaml"
    path.symlink_to("/dev/null")
    with pytest.raises(OSError) as err:
        Config(config_path=str(path)).check_saveable()
    assert err.value.errno == errno_module.EINVAL
    assert NOT_REGULAR in str(err.value)
    Config(config_path=str(tmp_path / "missing.yaml")).check_saveable()
    regular = tmp_path / "regular.yaml"
    regular.write_text("{}\n")
    Config(config_path=str(regular)).check_saveable()


@posix_only
def test_login_with_dev_null_link_stores_nothing_in_the_keyring(tmp_path, monkeypatch):
    """The save can't happen, so the key isn't put in the
    keyring before the error."""
    from deepctl_core import auth as auth_module
    from deepctl_core.auth import AuthManager

    stored = []
    monkeypatch.setattr(
        auth_module.keyring, "set_password", lambda *a: stored.append(a)
    )
    monkeypatch.setattr(auth_module.keyring, "get_password", lambda *a: None)
    path = tmp_path / "config.yaml"
    path.symlink_to("/dev/null")
    auth = AuthManager(Config(config_path=str(path)))
    monkeypatch.setattr(
        auth, "verify_credentials", lambda *a, **k: (True, "Credentials verified", None)
    )
    with pytest.raises(OSError) as err:
        auth.login_with_api_key("sk-FAKE0000", "proj-fake", _force_write=True)
    assert NOT_REGULAR in str(err.value)
    assert stored == []
    assert stat.S_ISCHR(os.stat("/dev/null").st_mode)
