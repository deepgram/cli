"""Telemetry wiring in the root entry point.

Telemetry is decided after option parsing from the resolved Config, the
`--no-telemetry` flag turns it off for one run, and the SDK is imported only
when an error is actually reported.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from typing import Any
from unittest.mock import patch

import click
import pytest
from deepctl_core import BaseCommand

DEAD_DSN = "http://public@127.0.0.1:9/1"


@pytest.fixture
def telemetry_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Telemetry on by default, fresh per-run state, an isolated HOME."""
    from deepctl_telemetry import client

    monkeypatch.delenv(client.DISABLE_ENV_VAR, raising=False)
    monkeypatch.setenv(client.DSN_ENV_VAR, DEAD_DSN)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    if sys.platform == "win32":
        # platformdirs ignores HOME and XDG_CONFIG_HOME on Windows.
        # WIN_PD_OVERRIDE_* needs platformdirs >= 4.8.0.
        monkeypatch.setenv("WIN_PD_OVERRIDE_LOCAL_APPDATA", str(tmp_path / ".config"))
    # The startup checks cache under the real home (resolved at import) and
    # query PyPI.
    for module, names in (
        ("deepctl_cmd_update.startup_check", ("check_and_notify",)),
        ("deepctl_cmd_update.plugin_update_check", ("check_plugins_and_notify",)),
        ("deepctl_cmd_skills.startup_check", ("check_and_notify",)),
    ):
        mod = pytest.importorskip(module)
        for name in names:
            monkeypatch.setattr(mod, name, lambda **kwargs: None)
    monkeypatch.setattr(client, "_state", client._fresh_state())
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    return client


@pytest.fixture
def boom_command():
    """A root subcommand that fails with an unexpected error."""
    main_mod = sys.modules.get("deepctl.main") or __import__(
        "deepctl.main", fromlist=["cli"]
    )

    @click.command("telemetry-test-boom")
    def boom() -> None:
        raise RuntimeError("unexpected")

    main_mod.cli.add_command(boom)
    yield "telemetry-test-boom"
    main_mod.cli.commands.pop("telemetry-test-boom", None)


class _ModeCommand(BaseCommand):
    """A BaseCommand that fails the way real commands do."""

    name = "telemetry-test-mode"
    help = "Test command"

    def handle(self, config: Any, auth_manager: Any, client: Any, **kwargs: Any) -> Any:
        mode = kwargs["mode"]
        if mode == "missing-file":
            try:
                open("/nonexistent/recording.wav", "rb")  # noqa: SIM115
            except FileNotFoundError:
                raise click.ClickException("File not found: recording.wav")
        if mode == "transcription-failed":
            try:
                raise ConnectionError("400 Bad Request")
            except Exception as e:
                raise click.ClickException(f"Transcription failed: {e}")
        if mode == "skill-fetch":
            try:
                from deepctl_core.skill_bundle import SkillFetchError

                raise SkillFetchError("could not fetch skill")
            except Exception as exc:
                raise click.ClickException(str(exc)) from exc
        if mode == "unexpected":
            raise KeyError("unexpected")
        return None


@pytest.fixture
def mode_command():
    """`dg telemetry-test-mode MODE`, dispatched through BaseCommand.execute."""
    main_mod = sys.modules.get("deepctl.main") or __import__(
        "deepctl.main", fromlist=["cli"]
    )
    command = _ModeCommand()

    @click.command("telemetry-test-mode")
    @click.argument("mode")
    @click.pass_context
    def mode_cmd(ctx: click.Context, mode: str) -> None:
        command.execute(ctx, mode=mode)

    main_mod.cli.add_command(mode_cmd)
    yield "telemetry-test-mode"
    main_mod.cli.commands.pop("telemetry-test-mode", None)


def _run_main(argv: list[str]) -> int:
    from deepctl.main import main

    # main() blocks httpx's CLI module; don't leave that to later tests.
    missing = object()
    saved = sys.modules.get("httpx._main", missing)
    try:
        with patch("sys.argv", argv):
            try:
                main()
            except SystemExit as exc:
                return exc.code if isinstance(exc.code, int) else 0
        return 0
    finally:
        if saved is missing:
            sys.modules.pop("httpx._main", None)
        else:
            sys.modules["httpx._main"] = saved  # type: ignore[assignment]


def _opt_out_config(tmp_path: Path) -> str:
    path = tmp_path / "opt-out.yaml"
    path.write_text("telemetry:\n  enabled: false\n")
    return str(path)


class TestErrorPath:
    def test_unexpected_error_is_reported(self, telemetry_on, boom_command) -> None:
        with patch("sentry_sdk.init") as mock_init, patch(
            "sentry_sdk.capture_exception"
        ) as mock_capture, patch("sentry_sdk.flush"):
            assert _run_main(["dg", boom_command]) == 1
        mock_init.assert_called_once()
        (exc,), _ = mock_capture.call_args
        assert isinstance(exc, RuntimeError)

    def test_config_file_opt_out_is_honored(
        self, telemetry_on, boom_command, tmp_path: Path
    ) -> None:
        config = _opt_out_config(tmp_path)
        with patch("sentry_sdk.init") as mock_init:
            assert _run_main(["dg", "--config", config, boom_command]) == 1
        mock_init.assert_not_called()
        assert telemetry_on._state["opt_out"] == "config"
        assert telemetry_on._state["config_path"] == config

    def test_no_telemetry_flag_is_honored(self, telemetry_on, boom_command) -> None:
        with patch("sentry_sdk.init") as mock_init:
            assert _run_main(["dg", "--no-telemetry", boom_command]) == 1
        mock_init.assert_not_called()
        assert telemetry_on._state["opt_out"] == "flag"

    def test_usage_errors_are_not_reported(self, telemetry_on) -> None:
        with patch("sentry_sdk.init") as mock_init:
            assert _run_main(["dg", "--definitely-not-a-flag"]) == 1
        mock_init.assert_not_called()


class TestWhatIsReported:
    @pytest.mark.parametrize(
        "mode", ["missing-file", "transcription-failed", "skill-fetch"]
    )
    def test_user_errors_are_not_reported(
        self, telemetry_on, mode_command, mode: str
    ) -> None:
        with patch("sentry_sdk.init") as mock_init:
            assert _run_main(["dg", mode_command, mode]) == 1
        mock_init.assert_not_called()

    def test_unexpected_command_error_is_reported(
        self, telemetry_on, mode_command
    ) -> None:
        with patch("sentry_sdk.init") as mock_init, patch(
            "sentry_sdk.capture_exception"
        ) as mock_capture, patch("sentry_sdk.flush"):
            assert _run_main(["dg", mode_command, "unexpected"]) == 1
        mock_init.assert_called_once()
        (exc,), _ = mock_capture.call_args
        assert isinstance(exc, KeyError)

    def test_project_file_cannot_re_enable(
        self, telemetry_on, boom_command, tmp_path: Path, monkeypatch
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "deepgram.yaml").write_text("telemetry:\n  enabled: true\n")
        monkeypatch.chdir(repo)
        config = _opt_out_config(tmp_path)
        with patch("sentry_sdk.init") as mock_init:
            assert _run_main(["dg", "--config", config, boom_command]) == 1
        mock_init.assert_not_called()

    @pytest.mark.parametrize(
        "explicit",
        ["output:\n  format: json\n", "telemetry:\n  enabled: true\n", None],
        ids=["no-telemetry-key", "explicit-true", "dev-null"],
    )
    def test_default_opt_out_survives_config_flag(
        self, telemetry_on, boom_command, tmp_path: Path, explicit: str | None
    ) -> None:
        layout = ("deepgram", "deepctl") if sys.platform == "win32" else ("deepctl",)
        default = tmp_path.joinpath(".config", *layout, "config.yaml")
        default.parent.mkdir(parents=True)
        default.write_text("telemetry:\n  enabled: false\n")
        if explicit is None:
            config = os.devnull
        else:
            config_file = tmp_path / "other.yaml"
            config_file.write_text(explicit)
            config = str(config_file)
        with patch("sentry_sdk.init") as mock_init:
            assert _run_main(["dg", "--config", config, boom_command]) == 1
        mock_init.assert_not_called()

    def test_ctrl_c_during_report_exits_2(
        self, telemetry_on, boom_command, capsys
    ) -> None:
        with patch("sentry_sdk.init"), patch("sentry_sdk.capture_exception"), patch(
            "sentry_sdk.flush", side_effect=KeyboardInterrupt
        ):
            assert _run_main(["dg", boom_command]) == 2
        assert "Stopped sending the error report." in capsys.readouterr().err


class TestReadOnceConfigSources:
    """A --config that is a FIFO, a pipe or /dev/stdin is never read, so it
    can't carry an opt-out: it counts as opted out."""

    OPT_OUT = "telemetry:\n  enabled: false\n"

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFOs")
    @pytest.mark.timeout(30)
    def test_fifo(self, telemetry_on, boom_command, tmp_path: Path) -> None:
        # Config never reads a FIFO, and a config file that isn't a regular
        # file counts as opted out.
        fifo = tmp_path / "config.fifo"
        os.mkfifo(fifo)
        with patch("sentry_sdk.init") as mock_init:
            assert _run_main(["dg", "--config", str(fifo), boom_command]) == 1
        mock_init.assert_not_called()
        assert telemetry_on._state["opt_out"] == "config"

    @pytest.mark.skipif(sys.platform == "win32", reason="no /dev/stdin on Windows")
    @pytest.mark.timeout(120)
    def test_dev_stdin(self, tmp_path: Path) -> None:
        probe, _ = _probe(
            ["--config", "/dev/stdin", "probe-boom"], tmp_path, stdin=self.OPT_OUT
        )
        assert probe["code"] == 1
        assert probe["opt_out"] == "config"
        assert probe["sdk_loaded"] is False

    @pytest.mark.skipif(
        sys.platform == "win32", reason="bash process substitution is POSIX-only"
    )
    @pytest.mark.timeout(120)
    def test_process_substitution(self, tmp_path: Path) -> None:
        import shutil

        bash = shutil.which("bash")
        if bash is None:
            pytest.skip("bash not available")
        script = (
            'exec "$0" -c "$1" "$2" <(printf "telemetry:\\n  enabled: false\\n")'
            " probe-boom"
        )
        result = subprocess.run(
            [bash, "-c", script, sys.executable, _PROBE, json.dumps(["--config"])],
            capture_output=True,
            text=True,
            env=_probe_env(tmp_path),
            timeout=120,
            check=False,
        )
        probe, _ = _probe_result(result)
        assert probe["code"] == 1
        assert probe["opt_out"] == "config"
        assert probe["sdk_loaded"] is False


class TestHelpNotice:
    def test_root_help_reflects_config_file(
        self, telemetry_on, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _run_main(["dg", "--config", _opt_out_config(tmp_path), "--help"])
        assert capsys.readouterr().out.rstrip().endswith("Telemetry is off.")

    @pytest.mark.parametrize("form", ["-c{}", "-qc{}", "--config={}"])
    def test_root_help_reflects_attached_config_value(
        self, telemetry_on, tmp_path: Path, capsys: pytest.CaptureFixture[str], form
    ) -> None:
        _run_main(["dg", form.format(_opt_out_config(tmp_path)), "--help"])
        assert capsys.readouterr().out.rstrip().endswith("Telemetry is off.")

    @pytest.mark.parametrize(
        ("forms", "expected"),
        [
            (["-c", "{off}", "-c", "{nokey}"], "Telemetry is on"),
            (["-c", "{nokey}", "-c", "{off}"], "Telemetry is off."),
            (["--config", "{off}", "--config={nokey}"], "Telemetry is on"),
            (["-c{off}", "-c", "{nokey}"], "Telemetry is on"),
            (["-c{nokey}", "-qc{off}"], "Telemetry is off."),
        ],
        ids=["c-c", "c-c-reversed", "long-then-eq", "attached-then-c", "cluster-last"],
    )
    def test_root_help_uses_the_last_config_value(
        self,
        telemetry_on,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        forms: list[str],
        expected: str,
    ) -> None:
        off = _opt_out_config(tmp_path)
        nokey = tmp_path / "nokey.yaml"
        nokey.write_text("output:\n  format: json\n")
        args = [f.format(off=off, nokey=nokey) for f in forms]
        _run_main(["dg", *args, "--help"])
        last = capsys.readouterr().out.rstrip().splitlines()[-1]
        assert last.startswith(expected)

    @pytest.mark.parametrize("attached", ["-pq", "-ojson", "-qpprod"])
    def test_flag_after_attached_short_value_is_seen(
        self, telemetry_on, capsys: pytest.CaptureFixture[str], attached: str
    ) -> None:
        _run_main(["dg", "--help", attached, "--no-telemetry"])
        assert capsys.readouterr().out.rstrip().endswith("Telemetry is off.")

    def test_root_help_reflects_no_telemetry_flag(
        self, telemetry_on, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _run_main(["dg", "--no-telemetry", "--help"])
        assert capsys.readouterr().out.rstrip().endswith("Telemetry is off.")

    def test_root_help_reflects_no_telemetry_flag_after_help(
        self, telemetry_on, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _run_main(["dg", "--help", "--no-telemetry"])
        assert capsys.readouterr().out.rstrip().endswith("Telemetry is off.")

    def test_no_telemetry_keeps_hyphenated_commands(self) -> None:
        import click

        from deepctl._cli import cli

        seen: list[list[str]] = []

        def record(self: Any, ctx: Any, args: list[str]) -> Any:
            seen.append(list(args))
            raise click.exceptions.Exit(0)

        for argv in (
            ["--no-telemetry", "debug-audio", "--help"],
            ["--quiet", "debug-audio"],
            # An option that takes a value still consumes the next token.
            ["--profile", "debug-audio", "debug-audio"],
        ):
            with patch.object(click.Group, "resolve_command", record):
                try:
                    cli.main(args=argv, prog_name="dg", standalone_mode=False)
                except click.exceptions.Exit:
                    pass
        assert seen == [
            ["debug", "audio", "--help"],
            ["debug", "audio"],
            ["debug", "audio"],
        ]

    def test_root_help_default_is_on(
        self, telemetry_on, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _run_main(["dg", "--help"])
        out = capsys.readouterr().out
        assert "--no-telemetry" in out
        assert "Telemetry is on" in out


_PROBE = textwrap.dedent(
    """
    import json, sys
    import click
    from deepctl.main import cli, main

    @cli.command("probe-boom")
    def probe_boom():
        raise RuntimeError("unexpected")

    sys.argv = ["dg"] + json.loads(sys.argv[1]) + sys.argv[2:]
    code = 0
    try:
        main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 0
    from deepctl_core.telemetry_context import command_context
    from deepctl_telemetry import client
    print("PROBE " + json.dumps({
        "code": code,
        "sdk_loaded": "sentry_sdk" in sys.modules,
        "enabled": client._state["enabled"],
        "opt_out": client._state["opt_out"],
        "context": command_context(),
    }), file=sys.stderr)
    """
)


def _probe_env(tmp_path: Path, dsn: str | None = DEAD_DSN) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("DEEPCTL_TELEMETRY_DISABLED", "DEEPCTL_TELEMETRY_DSN")
        and not key.startswith("DEEPGRAM_")
    }
    if dsn is not None:
        env["DEEPCTL_TELEMETRY_DSN"] = dsn
    env.update(
        {
            "HOME": str(tmp_path),
            "XDG_CONFIG_HOME": str(tmp_path / ".config"),
            "SHELL": "/bin/bash",
        }
    )
    if sys.platform == "win32":
        env["WIN_PD_OVERRIDE_LOCAL_APPDATA"] = str(tmp_path / ".config")
        # Path.home(), and so the startup checks' cache, reads USERPROFILE.
        env["USERPROFILE"] = str(tmp_path)
    return env


def _probe_result(result: subprocess.CompletedProcess[str]) -> tuple[dict, str]:
    lines = [ln for ln in result.stderr.splitlines() if ln.startswith("PROBE ")]
    assert lines, result.stderr[-2000:]
    return json.loads(lines[-1][len("PROBE ") :]), result.stderr


def _probe(
    args: list[str],
    tmp_path: Path,
    stdin: str | None = None,
    dsn: str | None = DEAD_DSN,
) -> tuple[dict, str]:
    result = subprocess.run(
        [sys.executable, "-X", "importtime", "-c", _PROBE, json.dumps(args)],
        capture_output=True,
        text=True,
        input=stdin,
        env=_probe_env(tmp_path, dsn),
        timeout=120,
        check=False,
    )
    return _probe_result(result)


class TestSdkNotLoadedOnSuccess:
    """A fresh interpreter: telemetry on, a command succeeds, SDK never loads."""

    def test_successful_command(self, tmp_path: Path) -> None:
        probe, stderr = _probe(["completion", "bash"], tmp_path)
        assert probe["code"] == 0
        assert probe["enabled"] is True
        assert probe["context"]["command"] == "dg completion"
        assert probe["sdk_loaded"] is False
        importtime = [ln for ln in stderr.splitlines() if ln.startswith("import time:")]
        assert importtime, "no -X importtime output"
        assert not [ln for ln in importtime if "sentry_sdk" in ln]

    def test_help_and_version(self, tmp_path: Path) -> None:
        for args in (["--help"], ["--version"], ["completion", "--help"]):
            probe, _ = _probe(args, tmp_path)
            assert probe["code"] == 0, args
            assert probe["sdk_loaded"] is False, args

    def test_sdk_loads_only_on_reported_error(self, tmp_path: Path) -> None:
        # Control for the probe itself: an unexpected error does load it.
        probe, _ = _probe(["probe-boom"], tmp_path)
        assert probe["code"] == 1
        assert probe["sdk_loaded"] is True

    def test_opt_out_error_does_not_load_sdk(self, tmp_path: Path) -> None:
        probe, _ = _probe(["--no-telemetry", "probe-boom"], tmp_path)
        assert probe["code"] == 1
        assert probe["sdk_loaded"] is False


class TestNoDestination:
    """No DEEPCTL_TELEMETRY_DSN, as for every user: nothing is reported."""

    def test_unexpected_error_sends_nothing(self, tmp_path: Path) -> None:
        probe, stderr = _probe(["probe-boom"], tmp_path, dsn=None)
        assert probe["code"] == 1
        assert probe["enabled"] is False
        assert probe["opt_out"] == "dsn"
        assert probe["sdk_loaded"] is False
        importtime = [ln for ln in stderr.splitlines() if ln.startswith("import time:")]
        assert importtime, "no -X importtime output"
        assert not [ln for ln in importtime if "sentry_sdk" in ln]

    def test_help_says_nothing_is_sent(self, tmp_path: Path) -> None:
        result = subprocess.run(
            [sys.executable, "-c", "from deepctl.main import main; main()", "--help"],
            capture_output=True,
            text=True,
            env=_probe_env(tmp_path, dsn=None),
            timeout=120,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.rstrip().endswith(
            "This version of dg sends no telemetry."
        )
