"""Telemetry reads the config opt-out only through core's config reader.

A file Config refused (too large, a YAML bomb, a rewrite that didn't
finish, untrusted) counts as opted out and is never read again; the default
file, when --config names another one, is read with the same limits.
"""

from __future__ import annotations

import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any

import pytest
from deepctl_core import config as config_module
from deepctl_core import output
from deepctl_core.config import Config
from deepctl_telemetry import client, is_enabled

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX locks")

OPT_OUT = "telemetry:\n  enabled: false\n"
NO_OPT_OUT = "output:\n  format: yaml\n"


def merge_bomb(levels: int = 23) -> str:
    """About 820 bytes; each level doubles what a plain SafeLoader builds."""
    lines = ["l0: &l0 {k0: 1}"]
    for i in range(1, levels + 1):
        lines.append(f"l{i}: &l{i} {{<<: [*l{i - 1}, *l{i - 1}], k{i}: 1}}")
    return "\n".join(lines) + "\n"


def big_file(mib: int = 8) -> str:
    """``mib`` MiB of distinct keys and no telemetry setting."""
    lines = []
    size = i = 0
    while size < mib << 20:
        line = f"key_{i}: {i}\n"
        lines.append(line)
        size += len(line)
        i += 1
    return "".join(lines)


CONTENT = {"merge_bomb": merge_bomb, "8mib": big_file}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Telemetry on in this process (env switch cleared), no SDK to send
    with, no agent session; returns the default config path (absent)."""
    monkeypatch.delenv(client.DISABLE_ENV_VAR, raising=False)
    monkeypatch.delenv(client.CI_ENV_VAR, raising=False)
    monkeypatch.setattr(client, "_state", client._fresh_state())
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)
    monkeypatch.setattr(output, "is_ai_agent", lambda: False, raising=False)
    monkeypatch.chdir(tmp_path)
    default = tmp_path / "default" / "config.yaml"
    default.parent.mkdir()
    monkeypatch.setattr(client, "_default_config_path", lambda: default)
    return default


def _custom(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "custom.yaml"
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


def _cost(fn: Any) -> tuple[Any, float, int]:
    """fn()'s result, its CPU seconds, and its peak traced allocation."""
    start = time.process_time()
    result = fn()
    cpu = time.process_time() - start
    tracemalloc.start()
    try:
        fn()
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    return result, cpu, peak


@pytest.mark.parametrize("kind", sorted(CONTENT))
@pytest.mark.parametrize("where", ["config", "default"])
def test_refused_file_is_opted_out_quickly(
    tmp_path: Path, _env: Path, kind: str, where: str
) -> None:
    """The 820-byte merge bomb and an 8 MiB file, as --config or as the
    default file: under 1 s of CPU, bounded memory, and opted out."""
    text = CONTENT[kind]()
    if where == "config":
        path = _custom(tmp_path, text)
    else:
        _env.write_text(text, encoding="utf-8")
        path = _custom(tmp_path, NO_OPT_OUT)

    def run() -> str | None:
        config = Config(config_path=str(path))
        assert config.user_config_refused is (where == "config")
        return client._opt_out_source(config)

    source, cpu, peak = _cost(run)
    assert source == "config"
    assert cpu < 1.0, cpu
    assert peak < 32 << 20, peak


def test_refused_file_is_never_read_again(tmp_path: Path) -> None:
    path = _custom(tmp_path, merge_bomb())
    config = Config(config_path=str(path))
    assert config.user_config_refused is True
    path.write_text(NO_OPT_OUT, encoding="utf-8")  # now valid: still not read
    assert is_enabled(config) is False


@pytest.mark.parametrize("where", ["config", "default"])
def test_valid_opt_out_is_honoured(tmp_path: Path, _env: Path, where: str) -> None:
    if where == "config":
        path = _custom(tmp_path, OPT_OUT)
    else:
        _env.write_text(OPT_OUT, encoding="utf-8")
        path = _custom(tmp_path, NO_OPT_OUT)
    assert is_enabled(Config(config_path=str(path))) is False


@pytest.mark.parametrize("default", [None, NO_OPT_OUT, "telemetry:\n  enabled: true\n"])
def test_valid_files_without_opt_out_keep_it_on(
    tmp_path: Path, _env: Path, default: str | None
) -> None:
    if default is not None:
        _env.write_text(default, encoding="utf-8")
    path = _custom(tmp_path, NO_OPT_OUT)
    assert is_enabled(Config(config_path=str(path))) is True
    assert is_enabled(Config(config_path=str(_env))) is True


@pytest.mark.parametrize("text", [OPT_OUT + "telemetry: {}\n", "- a\n", "a: [\n"])
def test_default_file_a_load_would_refuse_turns_it_off(
    tmp_path: Path, _env: Path, text: str
) -> None:
    """Repeated telemetry key, not a mapping, a parse error."""
    _env.write_text(text, encoding="utf-8")
    assert is_enabled(Config(config_path=str(_custom(tmp_path, NO_OPT_OUT)))) is False


@posix_only
@pytest.mark.parametrize("where", ["config", "default"])
def test_rewrite_lock_that_stays_taken_is_opted_out(
    tmp_path: Path, _env: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    """Another dg rewriting the file in place for the whole wait."""
    import fcntl
    import os

    monkeypatch.setattr(config_module, "_LOCK_TIMEOUT", 0.2)
    _env.write_text(NO_OPT_OUT, encoding="utf-8")
    path = _custom(tmp_path, NO_OPT_OUT)
    held = path if where == "config" else _env
    fd = os.open(held, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        assert is_enabled(Config(config_path=str(path))) is False
    finally:
        os.close(fd)
    assert is_enabled(Config(config_path=str(path))) is True


# Where the user config lives under the home directory (platformdirs).
_CFG = (
    Path("Library/Application Support") if sys.platform == "darwin" else Path(".config")
)


@pytest.fixture
def real_home_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An AI agent session whose default config is the real-home one."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(config_module, "_real_home", lambda: str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(output, "is_ai_agent", lambda: True)
    directory = home / _CFG / "deepctl"
    directory.mkdir(parents=True)
    directory.chmod(0o700)
    default = directory / "config.yaml"
    monkeypatch.setattr(client, "_default_config_path", lambda: default)
    return default


@posix_only
@pytest.mark.parametrize("mode", [0o600, 0o666])
def test_agent_default_file_opt_out_follows_trust(
    tmp_path: Path, real_home_config: Path, mode: int
) -> None:
    """An untrusted default file (others can change it) plus a separate
    --config counts as opted out; a trusted one without an opt-out doesn't."""
    real_home_config.write_text(NO_OPT_OUT, encoding="utf-8")
    real_home_config.chmod(mode)
    path = _custom(tmp_path, NO_OPT_OUT)
    assert is_enabled(Config(config_path=str(path))) is (mode == 0o600)


@posix_only
@pytest.mark.parametrize("mode", [0o600, 0o666])
def test_agent_loaded_default_file_opt_out_follows_trust(
    real_home_config: Path, mode: int
) -> None:
    real_home_config.write_text(NO_OPT_OUT, encoding="utf-8")
    real_home_config.chmod(mode)
    config = Config()
    assert config.user_config_refused is (mode != 0o600)
    assert is_enabled(config) is (mode == 0o600)


@posix_only
def test_agent_default_file_away_from_real_home_is_opted_out(
    tmp_path: Path, real_home_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """XDG_CONFIG_HOME pointing elsewhere: that default file isn't trusted."""
    xdg = tmp_path / "repo" / ".dg"
    (xdg / "deepctl").mkdir(parents=True)
    (xdg / "deepctl" / "config.yaml").write_text(NO_OPT_OUT, encoding="utf-8")
    (xdg / "deepctl" / "config.yaml").chmod(0o600)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setattr(
        client, "_default_config_path", lambda: xdg / "deepctl" / "config.yaml"
    )
    path = _custom(tmp_path, NO_OPT_OUT)
    assert is_enabled(Config(config_path=str(path))) is False
