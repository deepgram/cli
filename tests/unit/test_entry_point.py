"""The light entry point: argv peek, ``__main__``, launcher capture.

Most cases run a fresh interpreter, because what's under test is what a new
process imports and prints.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

ENTRY = "import sys; from deepctl.main import main; sys.exit(main())"


def _env(home: Path, **extra: str) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("DEEPGRAM_", "PYTHON", "DEEPCTL_LAUNCHER"))
    }
    env.update(
        HOME=str(home),
        XDG_CONFIG_HOME=str(home / ".config"),
        DEEPCTL_TELEMETRY_DISABLED="1",
        DEEPCTL_TELEMETRY_DSN="http://k@127.0.0.1:9/1",
        CI="1",
    )
    env.update(extra)
    return env


def _run(
    argv: list[str], home: Path, stdin: bytes = b"", **env: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        input=stdin.decode(),
        capture_output=True,
        text=True,
        env=_env(home, **env),
        timeout=60,
    )


class TestVersion:
    def test_fast_path_matches_click_byte_for_byte(self, tmp_path: Path) -> None:
        """The argv peek prints exactly what Click's version_option prints."""
        from deepctl._cli import cli

        click_out = CliRunner().invoke(cli, ["--version"]).output
        fast = _run([sys.executable, "-c", ENTRY, "--version"], tmp_path)
        assert fast.returncode == 0
        assert fast.stdout == click_out
        assert fast.stderr == ""

    def test_python_dash_m_and_isolated(self, tmp_path: Path) -> None:
        from deepctl import __version__

        for argv in (
            [sys.executable, "-m", "deepctl", "--version"],
            [sys.executable, "-I", "-m", "deepctl", "--version"],
        ):
            proc = _run(argv, tmp_path)
            assert proc.returncode == 0, proc.stderr
            assert proc.stdout == f"deepctl, version {__version__}\n"

    def test_closed_pipe_and_closed_stdout_exit_quietly(self, tmp_path: Path) -> None:
        """`dg --version | true` and `dg --version >&-`: exit 0, no traceback."""
        code = (
            "import os, subprocess, sys\n"
            "r, w = os.pipe(); os.close(r)\n"
            f"p = subprocess.run([sys.executable, '-c', {ENTRY!r}, '--version'],"
            " stdout=w, stderr=subprocess.PIPE, text=True)\n"
            "print(p.returncode, repr(p.stderr))\n"
            f"p = subprocess.run([sys.executable, '-c', {ENTRY!r}, '--version'],"
            " stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,"
            " preexec_fn=lambda: os.close(1))\n"
            "print(p.returncode, repr(p.stderr))\n"
        )
        proc = _run([sys.executable, "-c", code], tmp_path)
        assert proc.stdout.splitlines() == ["0 ''", "0 ''"], proc.stdout

    def test_version_constant_matches_metadata(self) -> None:
        import importlib.metadata

        from deepctl import __version__

        assert __version__ == importlib.metadata.version("deepctl")


class TestPackage:
    def test_deepctl_exports_no_main(self, tmp_path: Path) -> None:
        code = (
            "import deepctl; assert not hasattr(deepctl, 'main'), 'main exported'; "
            "assert 'main' not in deepctl.__all__"
        )
        proc = _run([sys.executable, "-c", code], tmp_path)
        assert proc.returncode == 0, proc.stderr

    def test_old_names_still_resolve_from_deepctl_main(self) -> None:
        import click

        import deepctl._cli as cli_mod
        import deepctl.main as main_mod

        assert isinstance(main_mod.cli, click.Group)
        assert main_mod.cli is cli_mod.cli
        # Reading `cli` loads the commands, as importing this module used to.
        assert cli_mod._commands_loaded
        assert callable(main_mod.preprocess_hyphenated_commands)
        with pytest.raises(AttributeError):
            main_mod.no_such_name  # noqa: B018

    def test_unknown_command_through_dash_m(self, tmp_path: Path) -> None:
        proc = _run([sys.executable, "-I", "-m", "deepctl", "nosuchcmd"], tmp_path)
        # Usage errors keep the published contract: 1 = error.
        assert proc.returncode == 1
        assert "No such command 'nosuchcmd'" in proc.stderr
        assert proc.stdout == ""


class TestLauncher:
    def test_popped_on_every_path(self, tmp_path: Path) -> None:
        """DEEPCTL_LAUNCHER is read once, and no child process inherits it."""
        code = textwrap.dedent(
            """
            import json, subprocess, sys
            import deepctl.main as m
            try:
                m.main()
            except SystemExit:
                pass
            child = subprocess.run(
                [sys.executable, "-c",
                 "import os; print(os.environ.get('DEEPCTL_LAUNCHER'))"],
                capture_output=True, text=True,
            ).stdout.strip()
            print(json.dumps({"launcher": m.LAUNCHER, "child": child}))
            """
        )
        for args in (["--version"], ["--quiet", "--version"], ["mcp"]):
            proc = _run(
                [sys.executable, "-c", code, *args],
                tmp_path,
                DEEPCTL_LAUNCHER="npm",
            )
            assert proc.returncode == 0, proc.stderr
            result = json.loads(proc.stdout.strip().splitlines()[-1])
            assert result == {"launcher": "npm", "child": "None"}, args

    def test_capture_is_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import deepctl.main as m

        monkeypatch.setattr(m, "LAUNCHER_CAPTURED", False)
        monkeypatch.setattr(m, "LAUNCHER", None)
        monkeypatch.setenv("DEEPCTL_LAUNCHER", "npx")
        assert m.capture_launcher() == "npx"
        assert "DEEPCTL_LAUNCHER" not in os.environ
        monkeypatch.setenv("DEEPCTL_LAUNCHER", "npm")
        assert m.capture_launcher() == "npx"


class TestUncaughtException:
    def test_rich_traceback_without_locals_and_clean_stdout(
        self, tmp_path: Path
    ) -> None:
        """An exception that escapes main() gets rich's traceback, no locals."""
        code = textwrap.dedent(
            """
            import sys
            import click
            import deepctl._cli as c

            class Escape(BaseException):
                pass

            @c.cli.command("boom")
            def boom():
                secret = "-".join(["LOCAL", "SENTINEL"])  # noqa: F841
                raise Escape("escaped")

            sys.argv = ["dg", "-o", "json", "boom"]
            from deepctl.main import main
            main()
            """
        )
        proc = _run([sys.executable, "-c", code], tmp_path)
        assert proc.returncode == 1
        assert proc.stdout == ""
        assert "Traceback" in proc.stderr
        assert "Escape" in proc.stderr
        assert "LOCAL-SENTINEL" not in proc.stderr


@pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() == 0,
    reason="needs Linux and a non-root user (root reads mode-000 directories)",
)
class TestUnreadableHome:
    """An unreadable HOME costs the plugins, not the run."""

    @pytest.fixture
    def home(self, tmp_path: Path):
        home = tmp_path / "h"
        home.mkdir()
        home.chmod(0)
        yield home
        home.chmod(0o700)

    @pytest.mark.parametrize("args", [["--version"], ["models", "--help"]])
    def test_exit_0_one_line_no_traceback(self, home: Path, args: list[str]) -> None:
        proc = _run([sys.executable, "-c", ENTRY, *args], home)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout
        assert "Traceback" not in proc.stderr
        lines = [line for line in proc.stderr.splitlines() if line.strip()]
        assert len(lines) <= 1, lines
        assert all(line.startswith("WARN: Couldn't load plugins: ") for line in lines)


class TestHttpxCliGuard:
    def test_import_httpx_skips_its_cli(self, tmp_path: Path) -> None:
        """After main(), httpx has its stub CLI, not the click one."""
        code = textwrap.dedent(
            """
            import inspect
            import sys

            sys.argv = ["dg", "--help"]
            from deepctl.main import main
            try:
                main()
            except SystemExit:
                pass
            import httpx
            print(
                sys.modules["httpx._main"] is None,
                inspect.isfunction(httpx.main),
                file=sys.stderr,
            )
            """
        )
        proc = _run([sys.executable, "-c", code], tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert proc.stderr.split()[-2:] == ["True", "True"], proc.stderr
