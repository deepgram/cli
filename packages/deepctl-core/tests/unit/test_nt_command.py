"""Windows command lines for next_step: ``deepctl_core.nt_command`` and
``transport.prog`` on Windows.

The helper is pure, so its table runs on every OS; the last test runs the
lines it builds through cmd, PowerShell and Git Bash on Windows.
"""

from __future__ import annotations

import base64
import json
import ntpath
import os
import posixpath
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from deepctl_core import nt_command, transport

_REAL_PROG = transport.prog


# ── join: quoted, bare or refused ──────────────────────────────────────


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        (["dg", "speak", "hello"], "dg speak hello"),
        (["dg", "--yes", "--no-start", "-m", "x"], "dg --yes --no-start -m x"),
        (["dg", "a=b", "+x", "1.5", "a:b", "--", "-"], "dg a=b +x 1.5 a:b -- -"),
        (["dg", "--out-file=a.wav", "--a.b"], "dg --out-file=a.wav --a.b"),
        # Spaces and other characters go in double quotes.
        (["dg", "hi there"], 'dg "hi there"'),
        (["dg", "C:/Users/a b/app"], 'dg "C:/Users/a b/app"'),
        (["dg", "Its 5, ok."], 'dg "Its 5, ok."'),
        (["dg", "a,b", "@x", "Q A", "a@b"], 'dg "a,b" "@x" "Q A" "a@b"'),
        (["dg", "x]", "#x", "a.wav", "~"], 'dg "x]" "#x" a.wav "~"'),
        (["dg", "a  b"], 'dg "a  b"'),
        (["dg", "a\\b"], 'dg "a\\b"'),
        # Forward-slash drive and share paths need no quotes.
        (["dg", "--dir", "C:/Users/x/app"], "dg --dir C:/Users/x/app"),
        (["dg", "//host/share/x"], "dg //host/share/x"),
        # PowerShell splits an unquoted -a.b or -x/y: quoted.
        (["dg", "-o.wav"], 'dg "-o.wav"'),
        (["dg", "-ofoo.wav"], 'dg "-ofoo.wav"'),
        # PowerShell reads an unquoted -x: as a parameter missing its value.
        (["dg", "-x:"], 'dg "-x:"'),
        (["dg", "-Verbose:"], 'dg "-Verbose:"'),
        (["dg", "-x:y", "--x:"], "dg -x:y --x:"),
        # --name=value with a URL, drive or relative path: Git Bash keeps it.
        (
            ["dg", "--base-url=https://api.example.com/v1", "--dir=../x"],
            "dg --base-url=https://api.example.com/v1 --dir=../x",
        ),
        # The program itself must need no quotes (PowerShell).
        (["C:/Users/x/.venv/Scripts/dg.exe", "x"], "C:/Users/x/.venv/Scripts/dg.exe x"),
        (["C:/Program Files/dg/dg.exe", "x"], None),
        # cmd ends a command name at =: C:/p=1/... would run C:/p.bat.
        (["C:/p=1/Scripts/dg.exe", "--base-url=https://h/v1"], None),
        (["dg", "--base-url=https://h/v1", "a=b"], "dg --base-url=https://h/v1 a=b"),
        ([], None),
    ],
)
def test_join(tokens, expected):
    assert nt_command.join(tokens) == expected


@pytest.mark.parametrize(
    "token",
    [
        pytest.param("", id="empty: dropped by PowerShell 5.1"),
        pytest.param("Jos\u00e9", id="non-ASCII"),
        pytest.param("\u201chi\u201d", id="smart double quotes end PowerShell strings"),
        pytest.param("\u201ehi", id="low double quote ends PowerShell strings"),
        pytest.param("\u2018hi", id="smart single quote"),
        pytest.param('a"b', id="double quote"),
        pytest.param("%PATH%", id="percent: cmd expands"),
        pytest.param("$HOME", id="dollar: PowerShell and Bash expand"),
        pytest.param("$(echo X)", id="dollar-paren: runs"),
        pytest.param("`x", id="backtick"),
        pytest.param("Hi!", id="bang"),
        pytest.param("Q&A", id="ampersand"),
        pytest.param("a|b", id="pipe"),
        pytest.param("a<b", id="less-than"),
        pytest.param("a>b", id="greater-than"),
        pytest.param("a^b", id="caret"),
        # Code runs once a wrapper strips the double quotes: cmd /c "...",
        # powershell -Command "...", bash -c "...".
        pytest.param("Hi; New-Item pwned", id="semicolon: ends a statement"),
        pytest.param("Hi (New-Item pwned)", id="parenthesis: runs in PowerShell"),
        pytest.param("a(b", id="opening parenthesis"),
        pytest.param("a)b", id="closing parenthesis"),
        pytest.param("a{b", id="opening brace"),
        pytest.param("x @{a=New-Item pwned}", id="brace: a hashtable runs its values"),
        pytest.param("a}b", id="closing brace"),
        pytest.param("It's", id="apostrophe: ends a single-quoted wrapper"),
        # Globs once Git Bash strips the quotes: a file named "x $(cmd)"
        # in the directory would run cmd.
        pytest.param("*.wav", id="star"),
        pytest.param("How are you?", id="question mark"),
        pytest.param("[x]", id="bracket"),
        pytest.param("a\tb", id="tab"),
        pytest.param("a\nb", id="newline: cmd stops there"),
        pytest.param("a\rb", id="carriage return"),
        pytest.param("a\x00b", id="NUL"),
        pytest.param("a\x7fb", id="DEL"),
        pytest.param("C:\\x\\", id="trailing backslash escapes the quote"),
        pytest.param("a\\\\b", id="doubled backslash: Git Bash collapses it"),
        pytest.param("\\\\host\\share", id="backslash share path"),
        # Git Bash (MSYS) rewrites these, quoted or not.
        pytest.param("/x", id="msys: rooted"),
        pytest.param("/", id="msys: root"),
        pytest.param("a=/b", id="msys: after equals"),
        pytest.param("a@/b", id="msys: after at"),
        pytest.param("_/x", id="msys: after a leading symbol"),
        pytest.param("+/x", id="msys: after a leading plus"),
        pytest.param("-I/x", id="msys: option with slash"),
        pytest.param("--x=/y", id="msys: long option with slash"),
        pytest.param("--x=//h/s/x", id="msys: long option with a share"),
        pytest.param("--x=-a/b", id="msys: long option, dash value"),
        pytest.param("--x=a=/b", id="msys: long option, slash after equals"),
        pytest.param("--x=a/b:c", id="msys: long option, path list"),
        pytest.param("--x=.:/y", id="msys: long option, dot path list"),
        pytest.param("---x=a/b", id="msys: three dashes"),
        pytest.param("--x/y=a/b", id="msys: slash in the option name"),
        pytest.param("-x=a/b", id="msys: one dash and equals"),
        pytest.param("-x/y.z", id="msys: dash and slash"),
        pytest.param("//x", id="msys: double slash without a share"),
        pytest.param("//", id="msys: double slash"),
        pytest.param("///x", id="msys: triple slash"),
        pytest.param("a=//x", id="msys: double slash after equals"),
        pytest.param(".:/x", id="msys: path list"),
        pytest.param("//?/C:/x", id="msys: colon slash after a non-drive"),
        pytest.param("x/:.", id="msys: path list with a colon"),
        pytest.param("=-a/", id="msys: dash option after equals"),
    ],
)
def test_join_refuses(token):
    assert nt_command.refused(token)
    assert nt_command.join(["dg", "speak", token]) is None
    assert nt_command.join(["dg", "speak", "ok", token, "ok"]) is None


@pytest.mark.parametrize(
    "token",
    [
        "//host/share/x",
        "C:/x",
        "key=C:/x",
        "./x",
        "../x",
        "a/b",
        "a//b",
        "https://example.com/a",
        "https://localhost:8080/x",
        "--out-file=out/a.wav",
        "--out-file=./a.wav",
        "--out-file=C:/tmp/a.wav",
        "--base-url=https://api.example.com/v1",
        "--url=http://localhost:8080/x",
        "--dir=../x",
        "--dir=C:/x",
    ],
)
def test_slashes_msys_keeps(token):
    assert not nt_command.refused(token)


@pytest.mark.parametrize(
    ("line", "tokens"),
    [
        ('dg "a b" c', ["dg", "a b", "c"]),
        ("dg", ["dg"]),
        ('dg "x"', ["dg", "x"]),
        ('dg "a b', None),
        ('dg a"b', None),
        ("dg  x", None),
        ("dg x ", None),
        ('dg "a"b', None),
    ],
)
def test_split(line, tokens):
    assert nt_command.split(line) == tokens


@pytest.mark.parametrize(
    ("token", "expected"),
    [("-x/y", True), ("-a.b", True), ("-x:", True), ("-x:y", False), ("--x:", False)],
)
def test_needs_quotes_single_dash(token, expected):
    # -x/y is refused for Git Bash anyway; PowerShell would still split it.
    assert nt_command.needs_quotes(token) is expected


def test_join_checks_the_line_splits_back(monkeypatch):
    # Were quoting ever skipped, "a b" would come back as two tokens.
    monkeypatch.setattr(nt_command, "needs_quotes", lambda token: False)
    assert nt_command.join(["dg", "a b"]) is None
    assert nt_command.join(["dg", "ab"]) == "dg ab"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("C:\\Users\\me\\app", "C:/Users/me/app"),
        ("C:/Users/me/app", "C:/Users/me/app"),
        ("\\\\server\\share\\x", "//server/share/x"),
        ("\\\\?\\C:\\Users\\me", "C:/Users/me"),
        ("\\\\?\\UNC\\server\\share\\x", "//server/share/x"),
    ],
)
def test_forward_slashes(path, expected):
    assert nt_command.forward_slashes(path) == expected


# ── prog() on Windows, with a fake file system ─────────────────────────


def _fake_os(monkeypatch, name, files, which, argv0, *, path_mod=ntpath):
    """``transport`` sees ``os.name == name`` and only ``files`` (path to
    identity); ``which`` maps a command to what PATH finds."""

    def samefile(a, b):
        if a not in files or b not in files:
            raise OSError(a)
        return files[a] == files[b]

    fake_path = SimpleNamespace(
        basename=path_mod.basename,
        isfile=lambda p: p in files,
        samefile=samefile,
        abspath=lambda p: p,
        normcase=path_mod.normcase,
    )
    monkeypatch.setattr(transport, "os", SimpleNamespace(name=name, path=fake_path))
    monkeypatch.setattr(transport.shutil, "which", lambda cmd: which.get(cmd))
    monkeypatch.setattr(sys, "argv", [argv0])


SCRIPTS = "C:\\venv\\Scripts"


@pytest.mark.parametrize("argv0", [f"{SCRIPTS}\\dg", f"{SCRIPTS}\\dg.exe"])
def test_prog_nt_bare_name_when_on_path(monkeypatch, argv0):
    # The launcher strips .exe from argv[0] on every Python version.
    exe = f"{SCRIPTS}\\dg.exe"
    _fake_os(monkeypatch, "nt", {exe: 1}, {"dg": exe}, argv0)
    assert _REAL_PROG() == "dg"


def test_prog_nt_forward_slash_path_off_path(monkeypatch):
    exe = f"{SCRIPTS}\\dg.exe"
    _fake_os(monkeypatch, "nt", {exe: 1}, {}, f"{SCRIPTS}\\dg")
    assert _REAL_PROG() == "C:/venv/Scripts/dg.exe"


def test_prog_nt_keeps_the_exe_spelled_as_on_disk(monkeypatch):
    # From 3.12, which(argv0) adds PATHEXT's spelling (dg.EXE) even to a
    # path with a directory; the case-insensitive file system finds it.
    exe = f"{SCRIPTS}\\dg.exe"
    upper = f"{SCRIPTS}\\dg.EXE"
    argv0 = f"{SCRIPTS}\\dg"
    _fake_os(monkeypatch, "nt", {exe: 1, upper: 1}, {argv0: upper}, argv0)
    assert _REAL_PROG() == "C:/venv/Scripts/dg.exe"


def test_prog_nt_bare_name_when_path_spells_the_exe_differently(monkeypatch):
    # which("dg") gives dg.EXE: the same file even where stat can't say so.
    exe = f"{SCRIPTS}\\dg.exe"
    _fake_os(
        monkeypatch, "nt", {exe: 1}, {"dg": f"{SCRIPTS}\\dg.EXE"}, f"{SCRIPTS}\\dg"
    )
    assert _REAL_PROG() == "dg"


def test_prog_nt_bare_name_when_path_reaches_the_file_another_way(monkeypatch):
    # A junction or 8.3 name: another spelling, the same file.
    exe = f"{SCRIPTS}\\dg.exe"
    alias = "C:\\VENV~1\\Scripts\\dg.exe"
    _fake_os(monkeypatch, "nt", {exe: 1, alias: 1}, {"dg": alias}, exe)
    assert _REAL_PROG() == "dg"


def test_prog_nt_path_when_path_finds_another_dg(monkeypatch):
    exe = f"{SCRIPTS}\\dg.exe"
    other = "C:\\other\\dg.exe"
    _fake_os(monkeypatch, "nt", {exe: 1, other: 2}, {"dg": other}, exe)
    assert _REAL_PROG() == "C:/venv/Scripts/dg.exe"


@pytest.mark.parametrize(
    "scripts",
    [
        "C:\\Program Files\\venv\\Scripts",  # a space: the program can't be quoted
        "C:\\Users\\Jos\u00e9\\venv\\Scripts",
        "C:\\p=1\\venv\\Scripts",  # cmd would run C:\p.bat
        "\\\\server\\share\\Scripts",
    ],
)
def test_prog_nt_none_when_the_path_needs_quotes(monkeypatch, scripts):
    exe = f"{scripts}\\dg.exe"
    _fake_os(monkeypatch, "nt", {exe: 1}, {}, f"{scripts}\\dg")
    assert _REAL_PROG() is None


@pytest.mark.parametrize("argv0", [f"{SCRIPTS}\\dg", "C:\\x\\__main__.py"])
def test_prog_nt_none_without_a_file(monkeypatch, argv0):
    _fake_os(monkeypatch, "nt", {}, {}, argv0)
    assert _REAL_PROG() is None


def test_prog_posix_still_shell_quotes_the_path(monkeypatch):
    script = "/opt/my tools/dg"
    _fake_os(monkeypatch, "posix", {script: 1}, {}, script, path_mod=posixpath)
    assert _REAL_PROG() == shlex.quote(script) == "'/opt/my tools/dg'"


@pytest.mark.skipif(sys.platform != "win32", reason="the real Windows file system")
def test_prog_on_windows_with_an_argv0_without_exe(monkeypatch, tmp_path):
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    exe = scripts / "dg.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(sys, "argv", [str(scripts / "dg")])
    monkeypatch.setenv("PATH", str(scripts))
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    assert _REAL_PROG() == "dg"
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    expected = nt_command.forward_slashes(os.path.abspath(exe))
    assert _REAL_PROG() == nt_command.join([expected])


@pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe")
def test_no_program_path_with_equals_since_cmd_runs_a_bat_beside_it(
    monkeypatch, tmp_path
):
    scripts = tmp_path / "p=1" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "dg.exe").write_bytes(b"MZ")
    (tmp_path / "p.bat").write_text("@echo ran> ran.txt\r\n", encoding="ascii")
    program = nt_command.forward_slashes(str(scripts / "dg.exe"))
    cmd = os.environ.get("COMSPEC") or "cmd.exe"
    subprocess.run(
        f'"{cmd}" /d /s /c "{program} --version"',
        cwd=tmp_path,
        capture_output=True,
        timeout=60,
    )
    assert (tmp_path / "ran.txt").exists()  # cmd ran p.bat, not dg.exe
    assert nt_command.join([program, "--version"]) is None
    monkeypatch.setattr(sys, "argv", [str(scripts / "dg")])
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    assert _REAL_PROG() is None


# ── The lines through the real shells (Windows only) ───────────────────

ROUND_TRIP = [
    "hello",
    "C:/Users/a b/app",
    "C:/Users/x/app",
    "//host/share/x",
    "Its 5, ok.",
    "a,b",
    "@x",
    "--out-file=a.wav",
    "-o.wav",
    "-x:",
    "-o:x.wav",
    "--x:",
    "--base-url=https://api.example.com/v1",
    "--out-file=out/a.wav",
    "--dir=../x",
    "--out-file=C:/tmp/a.wav",
    "Q A",
    "x]",
    "#x",
    "~",
    "a b.wav",
    "--",
]


def _git_bash() -> str | None:
    override = os.environ.get("DG_TEST_GIT_BASH")
    if override:
        return override if os.path.isfile(override) else None
    git = shutil.which("git")
    if git is None:
        return None
    # <Git>\cmd\git.exe or <Git>\bin\git.exe or <Git>\mingw64\bin\git.exe;
    # never System32's bash.exe (WSL).
    root = Path(git).resolve().parent
    for base in (root.parent, root.parent.parent):
        bash = base / "bin" / "bash.exe"
        if bash.is_file():
            return str(bash)
    return None


def _shell(name: str) -> str | None:
    if name == "cmd":
        return os.environ.get("COMSPEC") or shutil.which("cmd")
    if name == "powershell":
        return shutil.which("powershell")
    if name == "pwsh":
        return shutil.which("pwsh")
    return _git_bash()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shells")
@pytest.mark.parametrize("shell", ["cmd", "powershell", "pwsh", "git-bash"])
def test_lines_run_the_same_in_each_windows_shell(shell, tmp_path):
    exe = _shell(shell)
    if exe is None:
        pytest.skip(f"{shell} is not installed")
    dump = tmp_path / "dump.py"
    dump.write_text(
        "import json, sys\n"
        "with open('out.jsonl', 'a', encoding='utf-8') as f:\n"
        "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n",
        encoding="utf-8",
    )
    program = nt_command.forward_slashes(sys.executable)
    script = nt_command.forward_slashes(str(dump))
    lines = [nt_command.join([program, script, token]) for token in ROUND_TRIP]
    if any(line is None for line in lines):
        pytest.skip(f"{sys.executable} needs quoting as a program")
    if shell == "cmd":
        command: str | list[str] = f'"{exe}" /d /s /c "{" & ".join(lines)}"'
    elif shell == "git-bash":
        (tmp_path / "lines.sh").write_text("\n".join(lines) + "\n", encoding="utf-8")
        command = [exe, "--noprofile", "--norc", "lines.sh"]
    else:
        text = "\n".join(lines)
        encoded = base64.b64encode(text.encode("utf-16-le")).decode()
        command = [exe, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]
    proc = subprocess.run(command, cwd=tmp_path, capture_output=True, timeout=120)
    out = tmp_path / "out.jsonl"
    received = (
        [json.loads(row) for row in out.read_text(encoding="utf-8").splitlines()]
        if out.exists()
        else []
    )
    assert received == [[token] for token in ROUND_TRIP], proc.stderr[-2000:]
