"""Warnings, errors and notices stay on stderr: a machine-format stdout holds
the payload and nothing else, and each warning prints once.

Every case runs dg in a fresh interpreter against a closed loopback port,
so nothing leaves the machine.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ENTRY = "import sys; from deepctl.main import main; sys.exit(main())"
FAKE_KEY = "fa1ce0000000000000000000000000000000000d"
AGENT_VARS = (
    "AI_AGENT",
    "AGENT",
    "GOOSE_TERMINAL",
    "CLAUDE_CODE_IS_COWORK",
    "CLAUDECODE",
    "CLAUDE_CODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CODEX_SANDBOX",
    "CODEX_CI",
    "CODEX_THREAD_ID",
    "CODEX_SANDBOX_NETWORK_DISABLED",
    "GEMINI_CLI",
    "COPILOT_CLI",
    "OPENCODE",
    "ANTIGRAVITY_AGENT",
    "AUGMENT_AGENT",
    "CURSOR_AGENT",
    "CURSOR_EXTENSION_HOST_ROLE",
    "OR_APP_NAME",
    "OR_SITE_URL",
    "DG_AGENT",
)


def _env(home: Path, **extra: str) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("DEEPGRAM_", "PYTHON", "DEEPCTL_")) and k not in AGENT_VARS
    }
    env.update(
        HOME=str(home),
        XDG_CONFIG_HOME=str(home / ".config"),
        DEEPCTL_TELEMETRY_DISABLED="1",
        DEEPCTL_TELEMETRY_DSN="http://k@127.0.0.1:9/1",
        DEEPCTL_NO_UPDATE_CHECK="1",
        DEEPGRAM_API_KEY=FAKE_KEY,
        DEEPGRAM_BASE_URL="http://127.0.0.1:9",
        PATH="/usr/local/bin:/usr/bin:/bin",
    )
    env.update(extra)
    return env


def _dg(home: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", ENTRY, *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=_env(home, **env),
        timeout=60,
        cwd=home,
    )


def _user_config(home: Path) -> Path:
    path = home / ".config" / "deepctl" / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def home(tmp_path: Path) -> Path:
    root = tmp_path / "home"
    root.mkdir()
    return root


class TestConfigWarnings:
    @pytest.mark.skipif(sys.platform == "win32", reason="XDG config location")
    def test_malformed_config_json_stdout_stays_clean(self, home: Path) -> None:
        _user_config(home).write_text("profiles: [\n  bad", encoding="utf-8")
        proc = _dg(home, "-o", "json", "models")
        json.loads(proc.stdout)  # one JSON document, nothing before it
        assert "Could not load config" not in proc.stdout
        assert proc.stderr.count("Could not load config") == 1
        assert "WARN: Could not load config" in proc.stderr

    @pytest.mark.skipif(
        sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
        reason="root reads any file",
    )
    def test_unreadable_config_json_stdout_stays_clean(self, home: Path) -> None:
        path = _user_config(home)
        path.write_text("output:\n  format: json\n", encoding="utf-8")
        path.chmod(0)
        try:
            proc = _dg(home, "-o", "json", "models")
        finally:
            path.chmod(0o600)
        json.loads(proc.stdout)
        assert proc.stderr.count("Could not load config") == 1

    @pytest.mark.skipif(sys.platform == "win32", reason="XDG config location")
    def test_malformed_config_human_output_warns_once_on_stderr(
        self, home: Path
    ) -> None:
        _user_config(home).write_text("profiles: [\n  bad", encoding="utf-8")
        proc = _dg(home, "models", DG_AGENT="0")
        assert "Could not load config" not in proc.stdout
        assert proc.stderr.count("Could not load config") == 1


class TestCommandErrors:
    def test_ffprobe_bad_path_errors_once(self, home: Path) -> None:
        proc = _dg(home, "ffprobe", "--path", "/nope/ffprobe")
        assert proc.returncode == 1
        assert proc.stderr.count("File not found") == 1
        assert "ERROR: File not found: /nope/ffprobe" in proc.stderr
        assert json.loads(proc.stdout)["status"] == "error"

    def test_ffprobe_bad_path_human_errors_once(self, home: Path) -> None:
        proc = _dg(home, "ffprobe", "--path", "/nope/ffprobe", DG_AGENT="0")
        assert proc.returncode == 1
        assert proc.stdout == ""
        assert proc.stderr.count("File not found") == 1

    def test_login_errors_go_to_stderr(self, home: Path) -> None:
        proc = _dg(home, "login", "--api-key", "bad", DG_AGENT="0")
        assert proc.returncode != 0
        assert proc.stdout == ""
        assert proc.stderr.strip()

    def test_login_errors_machine_format(self, home: Path) -> None:
        proc = _dg(home, "-o", "json", "login", "--api-key", "bad")
        assert proc.returncode != 0
        assert "Error:" not in proc.stdout
        json.loads(proc.stdout)


class TestVersionName:
    @pytest.mark.parametrize("name", ["dg", "deepctl", "deepgram"])
    def test_version_names_the_invoked_program(self, home: Path, name: str) -> None:
        from deepctl import __version__

        code = (
            f"import sys; sys.argv[0] = '/opt/bin/{name}'; "
            "from deepctl.main import main; sys.exit(main())"
        )
        fast = subprocess.run(
            [sys.executable, "-c", code, "--version"],
            capture_output=True,
            text=True,
            env=_env(home),
            timeout=60,
        )
        slow = subprocess.run(
            [sys.executable, "-c", code, "--quiet", "--version"],
            capture_output=True,
            text=True,
            env=_env(home),
            timeout=60,
        )
        assert fast.stdout == f"{name}, version {__version__}\n"
        assert slow.stdout == fast.stdout
        assert fast.stderr == slow.stderr == ""

    def test_version_uses_the_launcher_prog_name(self, home: Path) -> None:
        from deepctl import __version__

        code = (
            "import sys; sys.argv[0] = '/venv/bin/deepctl'; "
            "from deepctl.main import main; sys.exit(main())"
        )
        for args in (["--version"], ["--quiet", "--version"]):
            proc = subprocess.run(
                [sys.executable, "-c", code, *args],
                capture_output=True,
                text=True,
                env=_env(home, DEEPCTL_PROG_NAME="dg"),
                timeout=60,
            )
            assert proc.stdout == f"dg, version {__version__}\n"

    @pytest.mark.parametrize("value", ["/tmp/evil", "dg\x1b[2J", "dg;id", "dg x"])
    def test_version_ignores_a_name_that_isnt_plain(
        self, home: Path, value: str
    ) -> None:
        from deepctl import __version__

        code = (
            "import sys; sys.argv[0] = '/venv/bin/deepgram'; "
            "from deepctl.main import main; sys.exit(main())"
        )
        for args in (["--version"], ["--quiet", "--version"]):
            proc = subprocess.run(
                [sys.executable, "-c", code, *args],
                capture_output=True,
                text=True,
                env=_env(home, DEEPCTL_PROG_NAME=value),
                timeout=60,
            )
            assert proc.stdout == f"deepgram, version {__version__}\n"


class TestAgentModeNotice:
    """The notice needs a person at a terminal, which a subprocess pipe
    isn't; deepctl_core.agents' tests cover the wording. Here: never on
    stdout, and never without a terminal."""

    def test_no_notice_without_a_terminal(self, home: Path) -> None:
        proc = _dg(home, "-o", "json", "ffprobe", CLAUDECODE="1")
        assert "agent mode" not in proc.stderr
        assert "agent mode" not in proc.stdout

    @pytest.mark.skipif(sys.platform == "win32", reason="needs a pty")
    @pytest.mark.parametrize(
        ("env", "expected"),
        [
            (
                {"CLAUDECODE": "1"},
                "INFO: Using agent mode because CLAUDECODE is set. If you are a "
                "person, remove CLAUDECODE from your environment and shell profile "
                "(see dg help agent-mode). AI agents: don't change this.",
            ),
            ({"CLAUDECODE": "1", "DG_AGENT": "1"}, None),
            ({"CLAUDECODE": "1", "DG_AGENT": "0"}, None),
            ({}, None),
        ],
    )
    def test_notice_for_a_person_at_a_terminal(
        self, home: Path, env: dict[str, str], expected: str | None
    ) -> None:
        import pty
        import select

        master, slave = pty.openpty()
        code = (
            "import sys; sys.argv[0] = '/opt/bin/dg'; "
            "from deepctl.main import main; sys.exit(main())"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", code, "-o", "json", "ffprobe"],
            stdin=slave,
            stdout=subprocess.PIPE,
            stderr=slave,
            env=_env(home, TERM="xterm", DEEPCTL_PROG_NAME="dg", **env),
            cwd=home,
        )
        os.close(slave)
        out, _ = proc.communicate(timeout=60)
        err = b""
        while True:
            ready, _, _ = select.select([master], [], [], 0.5)
            if not ready:
                break
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            err += chunk
        os.close(master)
        text = err.decode("utf-8", "replace").replace("\r\n", "\n")
        flat = " ".join(text.split())
        assert b"agent mode" not in out
        if expected is None:
            assert "agent mode" not in flat
        else:
            assert flat.count(expected) == 1, text
