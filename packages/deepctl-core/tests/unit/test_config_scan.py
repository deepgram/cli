"""A scan-only Config (``Config(scan_only=True)``, for ``--help``) reads the
same files and makes the same trust, permission and allowlist decisions as a
full one, with the same notices, warnings and side effects. It only skips
building the settings, so pydantic isn't imported.

Each scenario is built afresh for each mode in the same directory (a
permission fix changes the file), and everything observable is compared.
"""

from __future__ import annotations

import contextlib
import io
import itertools
import os
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
import yaml
from deepctl_core import config as config_module
from deepctl_core import hosts, output
from deepctl_core.config import Config
from deepctl_core.config_models import (
    DeepgramConfig,
    OutputConfig,
    ProfileConfig,
)

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="XDG layout (Linux)"
)

KEY = "fa1ce0000000000000000000000000000000000d"
TRUST_CONFIG = {
    "api": {"allowed_hosts": ["127.0.0.1:8000"]},
    "profiles": {"default": {"base_url": "http://127.0.0.1:8000", "api_key": KEY}},
    "tools": {"ffprobe_path": "/repo/tools/ffprobe"},
    "plugins": {"enabled": ["x"]},
    "update": {"check_enabled": False},
    "telemetry": {"enabled": False},
    "output": {"format": "table"},
}
W_PF_PROJECT = {
    "api": {"allowed_hosts": ["evil.example"]},
    "tools": {"ffprobe_path": "/tmp/evil"},
    "telemetry": {"a": 1, "b": 2, "c": 3, "d": 4},
    "update": {"check_enabled": False},
    "plugins": {"allow_override": ["login"]},
    "api_key": KEY,
    "project_id": "p",
    "active_profile": "evil",
    "default_profile": "evil",
    "base_url": "http://evil.example",
    "output": {"format": "json", "bogus": 1},
    "profiles": {
        "x": {
            "api_key": KEY,
            "project_id": "p",
            "dg_token_expires_at": "t",
            "base_url": "http://evil.example",
            "other": 1,
        },
        "eu": {"base_url": "https://api.eu.deepgram.com"},
        "y": 5,
    },
    "random": 1,
}
CUSTOM_CONFIG = {
    "api": {"allowed_hosts": ["evil.example"]},
    "tools": {"ffprobe_path": "/tmp/evil"},
    "plugins": {"allow_override": ["login"]},
    "update": {"a": 1, "b": 2, "c": 3, "d": 4},
    "profiles": {
        "default": {"api_key": KEY, "base_url": "http://evil.example"},
        "eu": {"base_url": "https://api.eu.deepgram.com"},
    },
}
# Data a merge can't take (it prints "Warning: Could not load config"), or
# keys it skips: the scan must fall back to a real merge for each.
ODD_USER_FILES = [
    "- a\n- b\n",
    "just a string\n",
    "5\n",
    "profiles: 5\n",
    "profiles:\n  a: 5\n",
    "profiles:\n  a: null\n",
    "foo: 1\n",
    "model_config: 1\n",
    "output: 5\n",
    "output: null\n",
    "profiles:\n  a:\n    copy: 1\n",
    "profiles:\n  a:\n    api_key: k\n    extra: 1\n",
    "output:\n  model_dump: 1\n",
    "1: 2\n",
    "profiles:\n  a:\n    1: 2\n",
    ": : :\n",
    "",
    "null\n",
    "default_profile: [1, 2]\n",
]


def _dump(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False)


class Scene:
    """Where the files of one scenario go."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.home = root / "home"
        self.real_home = self.home
        self.cwd = root / "work"
        self.config_kwargs: dict[str, Any] = {}
        # Another user owns the files (getuid/geteuid report a different id
        # while Config loads).
        self.foreign = False
        # Descriptors held open (a writer's lock) until the load is observed.
        self.held: list[int] = []

    @property
    def user_file(self) -> Path:
        return self.home / ".config" / "deepctl" / "config.yaml"

    def user(self, text: str | bytes, mode: int = 0o600) -> None:
        self.user_file.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            self.user_file.write_bytes(text)
        else:
            self.user_file.write_text(text)
        os.chmod(self.user_file, mode)

    def project(self, text: str) -> None:
        (self.cwd / "deepgram.yaml").write_text(text)


def _none(scene: Scene) -> None:
    del scene


def _trusted_key_0644(scene: Scene) -> None:
    scene.user(_dump({"profiles": {"default": {"api_key": KEY}}}), 0o644)


def _trusted(scene: Scene) -> None:
    scene.user(_dump(TRUST_CONFIG))


def _w_pf(scene: Scene) -> None:
    scene.user(_dump(TRUST_CONFIG))
    scene.project(_dump(W_PF_PROJECT))


def _w_pf_no_user(scene: Scene) -> None:
    scene.project(_dump(W_PF_PROJECT))


def _project_yaml_error(scene: Scene) -> None:
    scene.project("a: [\n")


def _project_list(scene: Scene) -> None:
    scene.project("- 1\n- 2\n")


def _custom(scene: Scene) -> None:
    path = scene.root / "repo" / "dg.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(_dump(CUSTOM_CONFIG))
    os.chmod(path, 0o600)
    scene.user(_dump(TRUST_CONFIG))
    scene.config_kwargs = {"config_path": str(path)}


def _fake_home(scene: Scene) -> None:
    """HOME isn't the account's home: in an AI agent session the file under
    it is distrusted (one W-CFG-UNTRUSTED line per refused key)."""
    scene.real_home = scene.root / "realhome"
    scene.real_home.mkdir()
    scene.user(_dump(TRUST_CONFIG))


def _writable_key(scene: Scene) -> None:
    scene.user(_dump({"profiles": {"default": {"api_key": KEY}}}), 0o666)


def _symlinked(scene: Scene) -> None:
    target = scene.root / "dotfiles" / "config.yaml"
    target.parent.mkdir(parents=True)
    target.write_text(_dump(TRUST_CONFIG))
    os.chmod(target, 0o600)
    scene.user_file.parent.mkdir(parents=True, exist_ok=True)
    scene.user_file.symlink_to(target)


def _foreign_key(scene: Scene) -> None:
    """Another user's file with API keys: in an AI agent session the keys
    aren't used (one W-CFG-UNTRUSTED "owner" line each)."""
    scene.user(
        _dump({"api_key": KEY, "profiles": {"default": {"api_key": KEY}, "b": {}}})
    )
    scene.foreign = True


def _foreign_trust(scene: Scene) -> None:
    """Another user's file that would widen trust: refused as the trust
    root in an AI agent session ("owner")."""
    scene.user(_dump(TRUST_CONFIG))
    scene.foreign = True


def _foreign_key_0644(scene: Scene) -> None:
    scene.user(_dump({"profiles": {"default": {"api_key": KEY}}}), 0o644)
    scene.foreign = True


def _not_utf8(scene: Scene) -> None:
    scene.user(b"profiles:\n  a: \xff\xfe\n")


def _fifo_user(scene: Scene) -> None:
    scene.user_file.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(scene.user_file, 0o600)


def _fifo_project(scene: Scene) -> None:
    os.mkfifo(scene.cwd / "deepgram.yaml", 0o600)


def _oversize_user(scene: Scene) -> None:
    scene.user("output:\n  format: json\n# " + "x" * (64 << 10) + "\n")


def _busy_user(scene: Scene) -> None:
    """Another dg process is rewriting the file in place (exclusive flock):
    the read gives up after the wait, and nothing from the file is used."""
    import fcntl

    scene.user(_dump(TRUST_CONFIG))
    fd = os.open(scene.user_file, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    scene.held.append(fd)


BOMBS = sorted(
    (Path(__file__).resolve().parents[4] / "tests" / "unit" / "fixtures" / "config_bombs").glob(
        "*.yaml"
    )
)


def _bomb(path: Path, where: str) -> Callable[[Scene], None]:
    def build(scene: Scene) -> None:
        text = path.read_text()
        if where == "user":
            scene.user(text)
        else:
            scene.project(text)

    build.__name__ = f"bomb {path.stem} {where}"
    return build


def _odd(text: str) -> Callable[[Scene], None]:
    def build(scene: Scene) -> None:
        scene.user(text)

    build.__name__ = f"odd {text!r}"
    return build


SCENARIOS: list[Callable[[Scene], None]] = [
    _none,
    _trusted_key_0644,
    _trusted,
    _w_pf,
    _w_pf_no_user,
    _project_yaml_error,
    _project_list,
    _custom,
    _fake_home,
    _writable_key,
    _symlinked,
    _foreign_key,
    _foreign_trust,
    _foreign_key_0644,
    _not_utf8,
    *[_odd(text) for text in ODD_USER_FILES],
    _fifo_user,
    _fifo_project,
    _oversize_user,
    _busy_user,
    *[_bomb(path, where) for path in BOMBS for where in ("user", "project")],
    _odd(
        "a: &a [x, x, x, x, x, x, x, x, x, x]\n"
        "b: &b [*a, *a, *a, *a, *a, *a, *a, *a, *a, *a]\n"
        "c: &c [*b, *b, *b, *b, *b, *b, *b, *b, *b, *b]\n"
        "d: &d [*c, *c, *c, *c, *c, *c, *c, *c, *c, *c]\n"
        "e: [*d, *d, *d, *d, *d, *d, *d, *d, *d, *d]\n"
    ),
    _odd(
        "m0: &m0 {k0: 0, k1: 1, k2: 2, k3: 3, k4: 4, k5: 5, k6: 6, k7: 7}\n"
        + "".join(
            f"m{i}: &m{i} {{<<: [*m{i - 1}, *m{i - 1}], x{i}: {i}}}\n"
            for i in range(1, 12)
        )
    ),
]


def _observe(
    scene_builder: Callable[[Scene], None],
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    agent: bool,
    scan_only: bool,
) -> dict[str, Any]:
    if root.exists():
        for path in root.rglob("*"):
            if not path.is_symlink():
                os.chmod(path, 0o700 if path.is_dir() else 0o600)
        shutil.rmtree(root)
    scene = Scene(root)
    scene.home.mkdir(parents=True)
    scene.cwd.mkdir()
    monkeypatch.setattr(config_module, "_LOCK_TIMEOUT", 0.2)
    scene_builder(scene)
    monkeypatch.setattr(config_module, "_pending_notices", [])
    monkeypatch.setattr(config_module, "_seen_notices", set())
    monkeypatch.setattr(hosts, "_reported", set())
    monkeypatch.setattr(config_module, "_real_home", lambda: str(scene.real_home))
    monkeypatch.setattr(output, "is_ai_agent", lambda: agent)
    monkeypatch.setenv("HOME", str(scene.home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.chdir(scene.cwd)
    out, err = io.StringIO(), io.StringIO()
    result: dict[str, Any] = {}
    owner = contextlib.ExitStack()
    if scene.foreign:
        other = os.getuid() + 1
        owner.enter_context(mock.patch.object(os, "getuid", return_value=other))
        owner.enter_context(mock.patch.object(os, "geteuid", return_value=other))
    with owner, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            config = Config(**scene.config_kwargs, scan_only=scan_only)
        except Exception as exc:
            result["raised"] = (type(exc).__name__, str(exc))
        else:
            result.update(
                config_path=str(config.config_path),
                user_config_text=config.user_config_text,
                trusted_file=config._trusted_file,
                untrusted_reason=config._untrusted_reason,
                key_untrusted=config._key_untrusted,
                key_untrusted_why=config._key_untrusted_why,
                load_failed=config._load_failed,
                load_not_regular=config._load_not_regular,
                load_busy=config._load_busy,
                user_config_refused=config.user_config_refused,
                user_data=config._user_data,
                base_url_sources=config._base_url_sources,
            )
    for fd in scene.held:
        os.close(fd)
    result["notices"] = list(config_module._pending_notices)
    result["stdout"] = out.getvalue()
    result["stderr"] = err.getvalue()
    result["modes"] = {
        str(p.relative_to(root)): stat.S_IMODE(p.lstat().st_mode)
        for p in sorted(root.rglob("*"))
    }
    return result


@pytest.mark.parametrize("agent", [False, True], ids=["person", "agent"])
@pytest.mark.parametrize("scene", SCENARIOS, ids=lambda s: s.__name__)
def test_scan_matches_full_load(
    scene: Callable[[Scene], None],
    agent: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "s"
    full = _observe(scene, root, monkeypatch, agent=agent, scan_only=False)
    scan = _observe(scene, root, monkeypatch, agent=agent, scan_only=True)
    assert scan == full


def test_scenarios_reach_the_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scenarios above do exercise the notices they're there for."""
    root = tmp_path / "s"
    seen: set[str] = set()
    warned = 0
    for scene, agent in itertools.product(SCENARIOS, (False, True)):
        result = _observe(scene, root, monkeypatch, agent=agent, scan_only=True)
        seen |= {notice[0] for notice in result["notices"]}
        warned += any(n[0] == "W-CFG-LOAD" for n in result["notices"])
    expected = {
        "W-CFG-LOAD",
        "I-PERM-FIXED",
        "W-PERM-AGENT",
        "W-CFG-UNTRUSTED",
        "W-CF-API",
        "W-CF-TOOLS",
        "W-CF-PLUGINS",
        "W-CF-UPDATE",
        "W-CF-BASEURL",
        "W-PF-API",
        "W-PF-TOOLS",
        "W-PF-TELEMETRY",
        "W-PF-UPDATE",
        "W-PF-PLUGINS",
        "W-PF-APIKEY",
        "W-PF-PROJECT",
        "W-PF-PROFILE",
        "W-PF-BASEURL",
        "W-PF-OTHER",
    }
    assert expected <= seen
    assert warned >= 10


def test_foreign_owner_scenarios_warn_per_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owner scenarios reach the per-key "owner" lines in agent mode, in
    both modes (so dropping them in a scan-only Config fails the test above)."""
    from deepctl_core import messages as msg

    owner = msg.W_CFG_UNTRUSTED_WHY["owner"]
    for scan_only in (False, True):
        result = _observe(
            _foreign_key, tmp_path / "s", monkeypatch, agent=True, scan_only=scan_only
        )
        lines = [n[1] for n in result["notices"] if n[0] == "W-CFG-UNTRUSTED"]
        assert len(lines) == 2, lines
        assert all(owner in line for line in lines)
        assert result["key_untrusted_why"] == "owner"
        person = _observe(
            _foreign_key, tmp_path / "s", monkeypatch, agent=False, scan_only=scan_only
        )
        assert person["notices"] == []


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0,
    reason="POSIX directory modes; root can read any directory",
)
def test_unreadable_home_raises_the_same(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir(mode=0o000)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    try:
        errors = []
        for scan_only in (False, True):
            try:
                config = Config(scan_only=scan_only)
            except OSError as exc:
                errors.append((type(exc), str(exc)))
            else:
                errors.append((None, str(config.config_path)))
    finally:
        os.chmod(home, 0o700)
    assert errors[0] == errors[1]


def test_field_lists_match_the_models() -> None:
    sections = {
        name: frozenset(field.annotation.model_fields)  # type: ignore[union-attr]
        for name, field in DeepgramConfig.model_fields.items()
        if isinstance(field.annotation, type)
        and issubclass(field.annotation, config_module._models().BaseModel)
    }
    assert sections == config_module._SECTION_FIELDS
    assert config_module._PROFILE_FIELDS == frozenset(ProfileConfig.model_fields)
    assert config_module._OUTPUT_FIELDS == tuple(OutputConfig.model_fields)
    scalars = set(DeepgramConfig.model_fields) - set(sections) - {"profiles"}
    assert scalars == config_module._SCALAR_FIELDS


def test_merge_is_safe_never_lies() -> None:
    """Data the scan skips merging always merges without an error."""
    values: list[Any] = [None, 1, "s", [1], {}, {"x": 1}]
    keys = [*sorted(DeepgramConfig.model_fields), "copy", "zzz", 1]
    nested = {
        "profiles": [{"a": {"api_key": "k"}}, {"a": {"copy": 1}}, {"a": 1}, {}],
        **{
            section: [{f: "v" for f in fields}, {"copy": 1}, {"zzz": 1}]
            for section, fields in config_module._SECTION_FIELDS.items()
        },
    }
    checked = 0
    for key in keys:
        for value in [*values, *nested.get(str(key), [])]:
            data = {key: value}
            if not config_module._merge_is_safe(data):
                continue
            config = Config.__new__(Config)
            config._config = DeepgramConfig()
            config._merge_config(data)
            checked += 1
    assert checked > 20


def test_scan_only_config_has_no_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    config = Config(scan_only=True)
    assert config.scan_only is True
    assert Config().scan_only is False
    with pytest.raises(RuntimeError, match="scan-only"):
        config.get("output.format")
    with pytest.raises(RuntimeError, match="scan-only"):
        _ = config.profile


def test_models_still_import_from_config() -> None:
    from deepctl_core.config import ApiConfig
    from deepctl_core.config import ProfileConfig as Profile

    assert Profile is ProfileConfig
    assert ApiConfig.__module__ == "deepctl_core.config_models"
    assert config_module.yaml is yaml


SCAN = """
import os, sys
from deepctl_core import _plugin_trust
from deepctl_core.config import Config
loaded = sorted(m for m in ("pydantic", "yaml", "rich") if m in sys.modules)
assert loaded == [], ("import", loaded)
_plugin_trust.allow_override_names()
config = Config(scan_only=True)
from deepctl_core import config as c
print(" ".join(sorted(m for m in ("pydantic", "yaml", "rich") if m in sys.modules)))
print(" ".join(n[0] for n in c._pending_notices))
"""


def _scan_subprocess(home: Path, cwd: Path) -> list[str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("DEEPGRAM_", "PYTHON", "XDG_"))
    }
    env["HOME"] = str(home)
    proc = subprocess.run(
        [sys.executable, "-c", SCAN],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.splitlines()


def test_scan_imports_no_pydantic(tmp_path: Path) -> None:
    home, work = tmp_path / "home", tmp_path / "work"
    home.mkdir()
    work.mkdir()
    assert _scan_subprocess(home, work) == ["", ""]
    cfg = home / ".config" / "deepctl"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.yaml").write_text(_dump(TRUST_CONFIG))
    os.chmod(cfg / "config.yaml", 0o600)
    (work / "deepgram.yaml").write_text(_dump({"api_key": KEY}))
    modules, notices = _scan_subprocess(home, work)
    assert modules == "yaml"
    assert notices == "W-PF-APIKEY"
