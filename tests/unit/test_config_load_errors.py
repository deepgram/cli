"""A user config that can't be parsed never shows its content: PyYAML
quotes the offending lines when it parses a string, and a line can hold an
API key."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from deepctl_core import config as config_module

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths")

SENTINEL = "sk-SNIPPETSENTINEL123"
BROKEN = f"profiles:\n  default:\n    api_key: {SENTINEL} project_id: [oops\n"


def _run(tmp_path, args, *, agent):
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("DEEPGRAM_", "CLAUDE", "CODEX_", "CURSOR_", "GEMINI_"))
        and k not in ("AI_AGENT", "XDG_CONFIG_HOME", "OR_APP_NAME", "OR_SITE_URL")
    }
    env.update(
        HOME=str(tmp_path / "home"),
        DEEPCTL_TELEMETRY_DISABLED="1",
        DEEPCTL_TELEMETRY_DSN="http://k@127.0.0.1:9/1",
        PYTHON_KEYRING_BACKEND="keyring.backends.fail.Keyring",
    )
    if agent:
        env["CLAUDECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-c", "from deepctl.main import main; main()", *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="XDG layout")
@pytest.mark.parametrize("where", ["default", "--config"])
@pytest.mark.parametrize("fmt", [[], ["-o", "json"]])
@pytest.mark.parametrize("agent", [False, True])
def test_broken_config_never_echoes_its_content(tmp_path, where, fmt, agent):
    (tmp_path / "home").mkdir()
    if where == "default":
        path = tmp_path / "home" / ".config" / "deepctl" / "config.yaml"
        path.parent.mkdir(parents=True)
        extra = []
    else:
        path = tmp_path / "custom.yaml"
        extra = ["--config", str(path)]
    path.write_text(BROKEN)
    path.chmod(0o600)
    result = _run(tmp_path, [*extra, *fmt, "projects", "list"], agent=agent)
    assert SENTINEL not in result.stdout
    assert SENTINEL not in result.stderr
    assert "project_id: [oops" not in result.stdout + result.stderr
    assert "Could not load config from" not in result.stdout
    assert result.stderr.count("Could not load config from") == 1, result.stderr
    assert "invalid YAML at line 3, column" in result.stderr


def test_yaml_errors_name_the_file_not_its_content():
    with pytest.raises(Exception) as err:
        config_module._yaml_from_text(BROKEN)
    assert SENTINEL not in str(err.value)
    assert "config.yaml" in str(err.value)
    summary = config_module._load_error_summary(err.value)
    assert summary.startswith("invalid YAML at line 3, column")


def test_save_over_a_broken_file_never_echoes_it(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("output:\n  format: json\n")
    config = config_module.Config(config_path=str(path))
    path.write_text(BROKEN)
    config._set_config_value("output.format", "yaml")
    with pytest.raises(OSError) as err:
        config.save()
    chain = []
    exc: BaseException | None = err.value
    while exc is not None:
        chain.append(str(exc))
        exc = exc.__cause__ or exc.__context__
    assert not [text for text in chain if SENTINEL in text]


@pytest.mark.parametrize(
    "text",
    [f"output:\n  format: {SENTINEL}\n", f"profiles:\n  - {SENTINEL}\n"],
)
def test_invalid_setting_value_is_not_echoed(tmp_path, capsys, text):
    path = tmp_path / "config.yaml"
    path.write_text(text)
    config_module.Config(config_path=str(path))
    config_module.flush_config_notices()
    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err


def test_load_error_summary_never_uses_the_exception_text():
    class Leaky(ValueError):
        pass

    summary = config_module._load_error_summary(Leaky(SENTINEL))
    assert summary == "a setting has an invalid value or type"


# ── YAML alias bombs ────────────────────────────────────────────


def _bomb(where: str) -> str:
    """Nine keys at each of eight alias levels: under 1 KB, about 9**9
    leaves once expanded."""
    keys = ", ".join(f"k{i}: 1" for i in range(9))
    lines = [f"l0: &l0 {{{keys}}}"]
    for level in range(1, 8):
        refs = ", ".join(f"k{i}: *l{level - 1}" for i in range(9))
        lines.append(f"l{level}: &l{level} {{{refs}}}")
    refs = ", ".join(f"k{i}: *l7" for i in range(9))
    lines.append(f"{where}: {{{refs}}}")
    return "\n".join(lines) + "\n"


def _child_cpu_seconds() -> float | None:
    """CPU time used by finished child processes, or None without resource."""
    try:
        import resource
    except ImportError:  # Windows
        return None
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime


def test_size_check_is_bounded_and_rejects_a_bomb():
    import time

    import yaml

    data = yaml.safe_load(_bomb("api"))
    start = time.process_time()
    with pytest.raises(config_module.ConfigTooLarge):
        config_module._check_config_size(data)
    assert time.process_time() - start < 2.0
    deep: dict = {}
    node = deep
    for _ in range(40):
        node["x"] = {}
        node = node["x"]
    with pytest.raises(config_module.ConfigTooLarge):
        config_module._check_config_size(deep)
    config_module._check_config_size({"profiles": {"default": {"api_key": "k"}}})


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="XDG layout")
@pytest.mark.parametrize("where", ["api", "tools", "top"])
@pytest.mark.parametrize("placement", ["project", "--config"])
@pytest.mark.parametrize("agent", [False, True])
def test_alias_bomb_never_hangs_dg(tmp_path, where, placement, agent):
    import time

    (tmp_path / "home").mkdir()
    text = _bomb("l8" if where == "top" else where)
    if placement == "project":
        (tmp_path / "deepgram.yaml").write_text(text)
        extra: list[str] = []
        warning = "Could not load project config from"
    else:
        custom = tmp_path / "custom.yaml"
        custom.write_text(text)
        custom.chmod(0o600)
        extra = ["--config", str(custom)]
        warning = "Could not load config from"
    for args in (["--version"], ["projects", "--current"]):
        cpu_before = _child_cpu_seconds()
        start = time.monotonic()
        result = _run(tmp_path, [*extra, *args], agent=agent)
        elapsed = time.monotonic() - start
        # Wall time is only a hang guard: a busy runner can stretch it.
        assert elapsed < 60, (args, elapsed)
        if cpu_before is not None:
            # The child's own CPU time is what a bomb would cost (minutes).
            cpu = _child_cpu_seconds() - cpu_before  # type: ignore[operator]
            assert cpu < 5, (args, cpu)
        if args == ["--version"]:
            # --version may answer before any config is read.
            assert result.returncode == 0, result.stderr
            assert result.stderr.count(warning) <= 1, result.stderr
        else:
            assert result.stderr.count(warning) == 1, result.stderr
            assert "too large or too deeply nested" in result.stderr


# ── Bomb files: generated, refused quickly, in every placement ───

# In-process limits are on CPU time (time.process_time). A busy host still
# inflates it, so the bounds are loose (2 s, where a real bomb costs
# minutes); the ConfigTooLarge assertions are the real gate.
BOMB_DIR = Path(__file__).parent / "fixtures" / "config_bombs"


def _merge_base() -> str:
    return "b: &b\n" + "".join(f"  k{i}: 1\n" for i in range(3000))


# The three 64 KiB bombs are built at test time rather than committed.
GENERATED_BOMBS = {
    "many_seq_64k": lambda: "l:\n" + "- 1\n" * 16381,
    "merge_seq_64k": lambda: (
        _merge_base() + "x:\n  <<: [" + ", ".join(["*b"] * 8385) + "]\n"
    ),
    "merge_wide_64k": lambda: _merge_base() + "l:\n" + "- <<: *b\n" * 3725,
}

# sha256 of the files as they were first committed.
GENERATED_BOMB_SHA256 = {
    "many_seq_64k": "11d7bdf1173c5f9bdf4a0493fc480dd4fdc58b75757ef0b0cda38ad73efc0dd8",
    "merge_seq_64k": "ba2713e4b697f0120c97bec7b197af564dd7fc464a9123b9a353d736d6199528",
    "merge_wide_64k": "8817fe27ece4b66124b1a64c4b2d8e27c746c1b103a956341b3175d54f0d15ae",
}

BOMBS = sorted({p.stem for p in BOMB_DIR.glob("*.yaml")} | set(GENERATED_BOMBS))


@pytest.fixture(scope="module")
def generated_bomb_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("config_bombs")
    for name, build in GENERATED_BOMBS.items():
        (out / f"{name}.yaml").write_bytes(build().encode())
    return out


@pytest.fixture
def path(request, generated_bomb_dir):
    name = request.param
    if name in GENERATED_BOMBS:
        return generated_bomb_dir / f"{name}.yaml"
    return BOMB_DIR / f"{name}.yaml"


def test_generated_bombs_match_the_committed_originals(generated_bomb_dir):
    for name, digest in GENERATED_BOMB_SHA256.items():
        data = (generated_bomb_dir / f"{name}.yaml").read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest, name
        assert not (BOMB_DIR / f"{name}.yaml").exists()


def test_all_bomb_files_are_present():
    assert len(BOMBS) == 20


@pytest.mark.parametrize("path", BOMBS, indirect=True)
def test_bomb_refused_at_parse_quickly(path):
    import time

    text = path.read_text()
    costs = []
    for _ in range(3):  # best of three: a busy host can only add time
        start = time.process_time()
        with pytest.raises(config_module.ConfigTooLarge):
            config_module._yaml_from_text(text)
        costs.append(time.process_time() - start)
    assert min(costs) < 2.0, costs


def test_normal_merge_keys_still_load():
    data = config_module._yaml_from_text("a: &a {x: 1}\nb: {<<: *a, y: 2}\n")
    assert data == {"a": {"x": 1}, "b": {"x": 1, "y": 2}}


def test_byte_cap(tmp_path):
    big = "a: " + "x" * (64 << 10) + "\n"
    with pytest.raises(config_module.ConfigTooLarge):
        config_module._yaml_from_text(big)
    path = tmp_path / "config.yaml"
    path.write_text(big)
    config = config_module.Config(config_path=str(path))
    assert config._load_failed is True


@pytest.mark.parametrize("path", BOMBS, indirect=True)
@pytest.mark.parametrize("placement", ["project", "user", "--config"])
@pytest.mark.parametrize("agent", [False, True])
def test_bomb_in_every_placement_loads_quickly_with_one_warning(
    tmp_path, monkeypatch, capsys, path, placement, agent
):
    import time

    from deepctl_core import output

    monkeypatch.setattr(config_module, "_pending_notices", [])
    monkeypatch.setattr(config_module, "_seen_notices", set())
    monkeypatch.setattr(output, "is_ai_agent", lambda: agent, raising=False)
    monkeypatch.chdir(tmp_path)
    text = path.read_text()
    user = tmp_path / "config.yaml"
    if placement == "project":
        (tmp_path / "deepgram.yaml").write_text(text)
        user.write_text("{}\n")
        warning = "Could not load project config from"
    else:
        user.write_text(text)
        warning = "Could not load config from"
    user.chmod(0o600)
    costs = []
    for _ in range(3):  # best of three, each from a cold parse cache
        config_module._parse_cache.clear()
        config_module._pending_notices.clear()
        config_module._seen_notices.clear()
        capsys.readouterr()
        start = time.process_time()
        config = config_module.Config(config_path=str(user))
        config.get_profile()
        config_module.flush_config_notices()
        costs.append(time.process_time() - start)
    assert min(costs) < 2.0, costs
    err = capsys.readouterr().err
    assert err.count(warning) == 1, err
    assert "it's too large or too deeply nested" in err


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="XDG layout")
@pytest.mark.parametrize("name", ["merge", "omap_output", "deep10k_flow_seq"])
@pytest.mark.parametrize("placement", ["project", "--config"])
@pytest.mark.parametrize("agent", [False, True])
def test_bomb_files_never_hang_dg(tmp_path, name, placement, agent):
    (tmp_path / "home").mkdir()
    text = (Path(__file__).parent / "fixtures" / "config_bombs" / f"{name}.yaml").read_text()
    if placement == "project":
        (tmp_path / "deepgram.yaml").write_text(text)
        extra: list[str] = []
        warning = "Could not load project config from"
    else:
        custom = tmp_path / "custom.yaml"
        custom.write_text(text)
        custom.chmod(0o600)
        extra = ["--config", str(custom)]
        warning = "Could not load config from"
    result = _run(tmp_path, [*extra, "projects", "--current"], agent=agent)
    assert result.returncode != 137
    assert result.stderr.count(warning) == 1, result.stderr
    assert "too large or too deeply nested" in result.stderr


# ── Merge budget, number forms, nesting depth, FIFOs, parse cache ───

MIB = 1 << 20


def _merge_wide(size):
    base = "b: &b\n" + "".join(f"  k{i}: 1\n" for i in range(9000))
    line = "- <<: *b\n"
    return base + "l:\n" + line * ((size - len(base) - 100) // len(line))


def _merge_seq(size):
    base = "b: &b\n" + "".join(f"  k{i}: 1\n" for i in range(9000))
    return base + "x:\n  <<: [" + ", ".join(["*b"] * ((size - len(base) - 200) // 4)) + "]\n"


@pytest.mark.parametrize("make", [_merge_wide, _merge_seq])
@pytest.mark.parametrize("size", [64 << 10, MIB])
def test_merge_bombs_refused_fast_at_any_size(make, size):
    """One whole-document merge budget, charged before any copy."""
    import time

    text = make(size)
    start = time.process_time()
    with pytest.raises(config_module.ConfigTooLarge):
        config_module._yaml_from_text(text)
    assert time.process_time() - start < 2.0


def test_merge_budget_is_per_document_and_normal_merges_load():
    base = "b: &b {" + ", ".join(f"k{i}: 1" for i in range(100)) + "}\n"
    ok = base + "l:\n" + "- <<: *b\n" * 50  # 5,000 merged entries
    assert len(config_module._yaml_from_text(ok)["l"]) == 50
    with pytest.raises(config_module.ConfigTooLarge):
        config_module._yaml_from_text(base + "l:\n" + "- <<: *b\n" * 120)
    # the budget starts again for each document
    assert len(config_module._yaml_from_text(ok)["l"]) == 50


def test_sexagesimal_numbers_are_strings():
    """YAML 1.1 base-60 forms are dropped; globally SafeLoader keeps them."""
    import time

    import yaml

    data = config_module._yaml_from_text("a: 1:30\nb: 1:30.5\nc: 42\nd: 0x1f\ne: 1.5\n")
    assert data == {"a": "1:30", "b": "1:30.5", "c": 42, "d": 31, "e": 1.5}
    assert yaml.safe_load("a: 1:30\n") == {"a": 90}
    under_cap = "a: " + ":".join(["59"] * 21000) + "\n"  # about 62 KB
    assert len(under_cap) < 64 << 10
    start = time.process_time()
    assert isinstance(config_module._yaml_from_text(under_cap)["a"], str)
    assert time.process_time() - start < 2.0
    over_cap = "a: " + ":".join(["59"] * 200000) + "\n"  # about 600 KB
    start = time.process_time()
    with pytest.raises(config_module.ConfigTooLarge):
        config_module._yaml_from_text(over_cap)
    assert time.process_time() - start < 2.0


@pytest.mark.parametrize(
    "text",
    [
        "a: " + "9" * 65 + "\n",
        "a: !!int " + ":".join(["59"] * 5000) + "\n",
        "a: !!float " + ":".join(["59"] * 5000) + ".5\n",
        "a: 0x" + "f" * 70 + "\n",
    ],
)
def test_long_numbers_refused(text):
    import time

    start = time.process_time()
    with pytest.raises(config_module.ConfigTooLarge):
        config_module._yaml_from_text(text)
    assert time.process_time() - start < 2.0
    assert config_module._yaml_from_text("a: " + "9" * 64 + "\n")["a"] == int("9" * 64)


def test_depth_limit_in_the_composer():
    """32 nested collections load, 33 don't, flow or block."""
    ok = config_module._yaml_from_text("x: " + "[" * 31 + "1" + "]" * 31 + "\n")
    assert ok["x"] is not None
    with pytest.raises(config_module.ConfigTooLarge):
        config_module._yaml_from_text("x: " + "[" * 33 + "1" + "]" * 33 + "\n")
    block_ok = "".join(" " * i + f"k{i}:\n" for i in range(31)) + " " * 31 + "v: 1\n"
    assert config_module._yaml_from_text(block_ok)
    block_deep = "".join(" " * i + f"k{i}:\n" for i in range(40)) + " " * 40 + "v: 1\n"
    with pytest.raises(config_module.ConfigTooLarge):
        config_module._yaml_from_text(block_deep)


@pytest.mark.parametrize(
    "text",
    [
        "output:\n  format: json\nnote: |\n"
        + "".join(f"  step {i}: see [section {i}\n" for i in range(40)),
        "desc: >\n" + "".join(f"  a {{ b {i}\n" for i in range(40)),
        "prompt: 'line one\n" + "".join(f"  item [{i}\n" for i in range(40)) + "  end'\n",
        'prompt: "line one\n' + "".join(f"  item [{i}\n" for i in range(40)) + '  end"\n',
        "links:\n" + "".join(f"  - https://ex.com/a?b[{i}=1\n" for i in range(40)),
        "".join(f"k{i}: a[b\n" for i in range(40)),
        "".join(f"k{i}: don't [stop\n" for i in range(40)),
        "l: [" + ",".join("[1,2]" for _ in range(500)) + "]\n",
    ],
)
def test_valid_configs_with_brackets_in_scalars_load(text):
    assert config_module._yaml_from_text(text)


def test_byte_cap_counts_bytes_not_characters(tmp_path, monkeypatch, capsys):
    """Multi-byte UTF-8 is capped by its bytes, on every read path."""
    from deepctl_core import output

    monkeypatch.setattr(config_module, "_pending_notices", [])
    monkeypatch.setattr(config_module, "_seen_notices", set())
    monkeypatch.setattr(output, "is_ai_agent", lambda: False, raising=False)
    monkeypatch.chdir(tmp_path)
    text = "a: '" + "\u00e9" * (40 << 10) + "'\n"  # about 40K characters, 80 KB
    assert len(text) < 64 << 10 < len(text.encode())
    (tmp_path / "deepgram.yaml").write_text(text, encoding="utf-8")
    user = tmp_path / "config.yaml"
    user.write_text(text, encoding="utf-8")
    config_module.Config(config_path=str(user))
    config_module.flush_config_notices()
    err = capsys.readouterr().err
    assert err.count("too large or too deeply nested") == 2, err


@pytest.mark.skipif(sys.platform == "win32", reason="FIFO")
def test_fifo_config_files_never_block(tmp_path, monkeypatch, capsys):
    """A FIFO project or user config is refused without blocking."""
    import time

    from deepctl_core import output

    monkeypatch.setattr(config_module, "_pending_notices", [])
    monkeypatch.setattr(config_module, "_seen_notices", set())
    monkeypatch.setattr(output, "is_ai_agent", lambda: False, raising=False)
    monkeypatch.chdir(tmp_path)
    os.mkfifo(tmp_path / "deepgram.yaml")
    os.mkfifo(tmp_path / "config.yaml")
    start = time.process_time()
    config_module.Config(config_path=str(tmp_path / "config.yaml"))
    config_module.flush_config_notices()
    assert time.process_time() - start < 2.0
    err = capsys.readouterr().err
    assert err.count("not a regular file") == 2, err


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="FIFO + XDG layout")
@pytest.mark.parametrize("agent", [False, True])
def test_fifo_project_file_never_hangs_dg(tmp_path, agent):
    (tmp_path / "home").mkdir()
    os.mkfifo(tmp_path / "deepgram.yaml")
    for args in (["--version"], ["projects", "--current"]):
        result = _run(tmp_path, args, agent=agent)
        assert result.returncode in (0, 1), result.stderr


def test_each_config_file_is_parsed_once_per_process(tmp_path, monkeypatch):
    """Good and refused files alike; a changed file is parsed again."""
    import time

    calls = []
    real = config_module._yaml_from_text

    def counting(text, *args, **kwargs):
        calls.append(len(text))
        return real(text, *args, **kwargs)

    monkeypatch.setattr(config_module, "_yaml_from_text", counting)
    monkeypatch.chdir(tmp_path)
    user = tmp_path / "config.yaml"
    user.write_text("output:\n  format: yaml\n")
    (tmp_path / "deepgram.yaml").write_text("l:\n" + "- 1\n" * 12000)  # refused
    for _ in range(4):
        config_module.Config(config_path=str(user))
    assert len(calls) == 2, calls
    time.sleep(0.01)
    user.write_text("output:\n  format: json\n")
    assert config_module.Config(config_path=str(user)).get("output.format") == "json"
    assert len(calls) == 3, calls


# ── Nested merges, aliased strings, save cap, cached refusals ───


def test_nested_merges_are_charged_at_their_flattened_size():
    """A merge source with its own merges counts what it will copy."""
    import time

    keys = ", ".join(f"k{i}: {i}" for i in range(1000))
    inner = ", ".join(["*b"] * 999)
    outer = ", ".join(["*a"] * 999)
    text = f"c: &c {{{keys}}}\nx: {{<<: [&a {{<<: [&b {{<<: *c}}, {inner}]}}, {outer}]}}\n"
    start = time.process_time()
    with pytest.raises(config_module.ConfigTooLarge):
        config_module._yaml_from_text(text)
    assert time.process_time() - start < 2.0
    normal = (
        "base: &base {format: json, color: true}\n"
        "inner: &inner {<<: *base, quiet: false}\n"
        "output: {<<: [*inner], verbose: true}\n"
        "explicit: {!!merge <<: *base}\n"
    )
    data = config_module._yaml_from_text(normal)
    assert data["output"] == {"format": "json", "color": True, "quiet": False, "verbose": True}
    assert data["explicit"] == {"format": "json", "color": True}


def test_aliased_strings_are_bounded_at_load():
    """56 KB that expands to about 196 MB is refused at load."""
    import time

    path = Path(__file__).parent / "fixtures" / "config_bombs" / "alias_str_fan.yaml"
    start = time.process_time()
    with pytest.raises(config_module.ConfigTooLarge):
        config_module._yaml_from_text(path.read_text())
    assert time.process_time() - start < 2.0
    ok = "s: &s " + "A" * 1000 + "\nl: [" + ", ".join(["*s"] * 100) + "]\n"
    assert len(config_module._yaml_from_text(ok)["l"]) == 100


def test_save_never_writes_a_file_dg_would_refuse(tmp_path):
    """The serialized text is capped before any write."""
    import errno as errno_module

    path = tmp_path / "config.yaml"
    path.write_text("output:\n  format: json\n")
    before = path.read_bytes()
    config = config_module.Config(config_path=str(path))
    config._set_config_value("update.installation_method", "x" * (70 << 10))
    with pytest.raises(OSError) as err:
        config.save()
    assert err.value.errno == errno_module.EFBIG
    assert err.value.filename == str(path)
    assert "too large to load" in str(err.value)
    config.save(implicit=True)  # quietly skipped
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.yaml"]


def test_parse_cache_is_keyed_on_content(tmp_path, monkeypatch):
    """A same-size rewrite with restored times is parsed again."""
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "config.yaml"
    path.write_text("output:\n  format: yaml\n")
    assert config_module.Config(config_path=str(path)).get("output.format") == "yaml"
    times = path.stat()
    with open(path, "r+") as f:
        f.write("output:\n  format: json\n")
    os.utime(path, ns=(times.st_atime_ns, times.st_mtime_ns))
    assert config_module.Config(config_path=str(path)).get("output.format") == "json"


def test_cached_refusal_is_a_fresh_exception_each_time(tmp_path):
    """The cached exception object is never raised itself."""
    path = tmp_path / "broken.yaml"
    path.write_text("a: [\n")
    seen = []
    for _ in range(2):
        with open(path, "rb") as f, pytest.raises(Exception) as err:
            config_module._parse_fd_cached(f)
        seen.append(err.value)
    assert type(seen[0]) is type(seen[1])
    assert seen[0] is not seen[1]
    assert str(seen[0]) == str(seen[1])
    # The cached refusal holds no traceback (its frames hold the text)
    cached = [v for ok, v in config_module._parse_cache.values() if not ok]
    assert cached and all(v.__traceback__ is None for v in cached)


# ── A save writes only text that loads back ─────────────────────

KEY = "sk-ROUNDTRIPKEY0001"
HEX_CFG = f"profiles:\n  default:\n    api_key: {KEY}\nextra: 0x{'F' * 62}\n"
MERGE_CFG = (
    f"profiles:\n  default:\n    api_key: {KEY}\n"
    "base: &base {" + ", ".join(f"k{i}: value{i}" for i in range(30)) + "}\n"
    "envs:\n" + "".join(f"  e{i}: {{<<: *base}}\n" for i in range(170))
)


@pytest.mark.parametrize("text", [HEX_CFG, MERGE_CFG], ids=["hex", "merge"])
def test_save_never_writes_a_config_that_would_not_load(tmp_path, monkeypatch, text):
    """The exact text a save would write is loaded back first."""
    import errno as errno_module

    monkeypatch.chdir(tmp_path)
    path = tmp_path / "config.yaml"
    path.write_text(text)
    path.chmod(0o600)
    before = path.read_bytes()
    config = config_module.Config(config_path=str(path))
    assert config._load_failed is False
    assert config.get_profile().api_key == KEY
    config._set_config_value("update.installation_method", "pip")
    config.save(implicit=True)  # skipped quietly
    assert path.read_bytes() == before
    with pytest.raises(OSError) as err:
        config.save()
    assert err.value.errno == errno_module.EFBIG
    assert str(err.value).startswith(
        "[Errno 27] this config would be too large to load, so nothing was saved"
    )
    assert KEY not in str(err.value)
    assert path.read_bytes() == before
    with pytest.raises(OSError):
        config.check_saveable()
    config_module._parse_cache.clear()
    assert config_module.Config(config_path=str(path)).get_profile().api_key == KEY


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="XDG layout")
@pytest.mark.parametrize("text", [HEX_CFG, MERGE_CFG], ids=["hex", "merge"])
def test_record_install_method_never_breaks_the_config(tmp_path, text):
    """The same check through the CLI: the install hook's implicit save."""
    path = tmp_path / "home" / ".config" / "deepctl" / "config.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(text)
    path.chmod(0o600)
    before = path.read_bytes()
    result = _run(tmp_path, ["--record-install-method", "pip", "--version"], agent=False)
    assert result.returncode == 0, result.stderr
    assert path.read_bytes() == before
    assert KEY not in result.stdout + result.stderr
    config_module._parse_cache.clear()
    assert config_module.Config(config_path=str(path)).get_profile().api_key == KEY


def test_login_refuses_before_the_keyring_when_the_config_would_not_load(
    tmp_path, monkeypatch
):
    """check_saveable makes the same dump-and-load check as a save."""
    from deepctl_core import auth as auth_module
    from deepctl_core.auth import AuthManager

    stored = []
    monkeypatch.setattr(auth_module.keyring, "set_password", lambda *a: stored.append(a))
    monkeypatch.setattr(auth_module.keyring, "get_password", lambda *a: None)
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "config.yaml"
    path.write_text(HEX_CFG)
    path.chmod(0o600)
    before = path.read_bytes()
    auth = AuthManager(config_module.Config(config_path=str(path)))
    monkeypatch.setattr(
        auth, "verify_credentials", lambda *a, **k: (True, "Credentials verified", None)
    )
    with pytest.raises(OSError) as err:
        auth.login_with_api_key("sk-NEWKEY0000", "proj-fake", _force_write=True)
    assert "too large to load" in str(err.value)
    assert stored == []
    assert path.read_bytes() == before


def test_set_members_count_against_the_string_cap():
    """!!set members are walked like list items."""
    big = "A" * 30000
    text = f"s: &s !!set {{? {big}}}\nl: [" + ", ".join(["*s"] * 20) + "]\n"
    with pytest.raises(config_module.ConfigTooLarge):
        config_module._yaml_from_text(text)
    assert config_module._yaml_from_text("s: !!set {? a, ? b}\n") == {"s": {"a", "b"}}
