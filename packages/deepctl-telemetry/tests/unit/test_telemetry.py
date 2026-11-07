"""Tests for deepctl_telemetry."""

from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from deepctl_telemetry import init_telemetry, is_enabled, render_notice
from deepctl_telemetry.client import DISABLE_ENV_VAR, _before_send, _rebuild_event


@pytest.fixture
def fresh_state(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Fresh per-run telemetry state; hooks restored afterwards."""
    import sys

    from deepctl_telemetry import client

    monkeypatch.setattr(client, "_state", client._fresh_state())
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    return client


@pytest.fixture(autouse=True)
def _telemetry_env(monkeypatch: pytest.MonkeyPatch, fresh_state: Any) -> None:
    """Undo the root conftest's opt-out so these tests see the real default.

    Also start from fresh per-run state: other suites parse documented
    commands such as `dg --no-telemetry ...`, which turns telemetry off for
    the rest of the process.
    """
    monkeypatch.delenv(DISABLE_ENV_VAR, raising=False)
    # The default user config file: absent unless a test writes it.
    default = Path(tempfile.mkdtemp()) / "default-config.yaml"
    monkeypatch.setattr(fresh_state, "_default_config_path", lambda: default)


def _default_file(fresh_state: Any) -> Path:
    path: Path = fresh_state._default_config_path()
    return path


def _config(value: bool | None = True, *, raw: str | None = None) -> Any:
    """A config whose user file sets `telemetry.enabled` to `value`.

    `None` leaves the key out; `raw` writes the file verbatim instead.
    """
    path = Path(tempfile.mkdtemp()) / "config.yaml"
    if raw is not None:
        path.write_text(raw, encoding="utf-8")
    elif value is not None:
        flag = "true" if value else "false"
        path.write_text(f"telemetry:\n  enabled: {flag}\n", encoding="utf-8")
    return SimpleNamespace(config_path=path)


class TestIsEnabled:
    def test_default_on(self) -> None:
        assert is_enabled(_config(True)) is True

    def test_config_off(self) -> None:
        assert is_enabled(_config(False)) is False

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(DISABLE_ENV_VAR, "1")
        assert is_enabled(_config(True)) is False

    @pytest.mark.parametrize(
        "value", ["1", "true", "TRUE", "yes", "on", "y", "2", "disabled", " 1"]
    )
    def test_env_any_other_value_disables(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv(DISABLE_ENV_VAR, value)
        assert is_enabled(_config(True)) is False

    @pytest.mark.parametrize("value", ["", "0", "false", "FALSE", "no", "off"])
    def test_env_false_values_keep_it_on(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv(DISABLE_ENV_VAR, value)
        assert is_enabled(_config(True)) is True


class TestUserConfigOptOut:
    """Only the user's own config file decides, and it fails closed."""

    @pytest.mark.parametrize(
        "raw",
        [
            'telemetry:\n  enabled: "false"\n',
            'telemetry:\n  enabled: "False"\n',
            'telemetry:\n  enabled: "no"\n',
            'telemetry:\n  enabled: "true"\n',
            "telemetry:\n  enabled: no\n",
            "telemetry:\n  enabled: 1\n",
            "telemetry:\n  enabled:\n",
            "telemetry: false\n",
            "telemetry: [enabled]\n",
            "telemetry:\n  enabled: true\noutput: {format: [json\n",
            "telemetry:\n  enabled: false\noutput: {format: [json\n",
            "profiles:\n  default:\ntelemetry:\n  enabled: false\n",
            "- just\n- a list\n",
            "telemetry.enabled: false\n",
            "telemetry.enabled: no\n",
            "telemetry.enabled: false\ntelemetry:\n  enabled: true\n",
            "telemetry:\n  enabled: false\ntelemetry:\n  enabled: true\n",
            "telemetry:\n  enabled: false\n  enabled: true\n",
            "telemetry: {enabled: false, enabled: true}\n",
            "telemetry:\n  enabled: true\n  enabled: true\n",
        ],
        ids=[
            "string-false",
            "string-False",
            "string-no",
            "string-true",
            "yaml-no",
            "integer",
            "null",
            "scalar-section",
            "list-section",
            "yaml-error-with-true",
            "yaml-error-with-false",
            "null-profile",
            "top-level-list",
            "dotted-key-false",
            "dotted-key-no",
            "dotted-key-false-with-section-true",
            "duplicate-section",
            "duplicate-enabled",
            "duplicate-enabled-flow",
            "duplicate-enabled-both-true",
        ],
    )
    def test_anything_but_boolean_true_is_off(self, raw: str) -> None:
        assert is_enabled(_config(raw=raw)) is False

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            "",
            "output:\n  format: json\n",
            "telemetry: {}\n",
            "telemetry:\n  enabled: true\n",
        ],
        ids=["no-file", "empty-file", "no-section", "empty-section", "true"],
    )
    def test_on_by_default_and_when_true(self, raw: str | None) -> None:
        config = _config(None) if raw is None else _config(raw=raw)
        assert is_enabled(config) is True

    def test_dotted_key_true_keeps_it_on(self) -> None:
        assert is_enabled(_config(raw="telemetry.enabled: true\n")) is True

    @pytest.mark.parametrize(
        "explicit",
        [
            "output:\n  format: json\n",
            "telemetry:\n  enabled: true\n",
            "",
        ],
        ids=["no-telemetry-key", "explicit-true", "empty"],
    )
    def test_default_file_opt_out_survives_config_flag(
        self, fresh_state: Any, explicit: str
    ) -> None:
        _default_file(fresh_state).write_text(
            "telemetry:\n  enabled: false\n", encoding="utf-8"
        )
        assert is_enabled(_config(raw=explicit)) is False

    def test_default_file_opt_out_survives_dev_null(self, fresh_state: Any) -> None:
        _default_file(fresh_state).write_text(
            "telemetry:\n  enabled: false\n", encoding="utf-8"
        )
        assert is_enabled(SimpleNamespace(config_path=Path(os.devnull))) is False

    def test_config_flag_opt_out_wins_over_default_file(
        self, fresh_state: Any
    ) -> None:
        _default_file(fresh_state).write_text(
            "telemetry:\n  enabled: true\n", encoding="utf-8"
        )
        assert is_enabled(_config(False)) is False

    def test_both_files_on(self, fresh_state: Any) -> None:
        _default_file(fresh_state).write_text(
            "telemetry:\n  enabled: true\n", encoding="utf-8"
        )
        assert is_enabled(_config(True)) is True

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFOs")
    @pytest.mark.timeout(10)
    def test_fifo_without_text_is_off_and_not_opened(self, tmp_path: Path) -> None:
        fifo = tmp_path / "config.fifo"
        os.mkfifo(fifo)  # no writer: opening it would block forever
        assert is_enabled(SimpleNamespace(config_path=fifo)) is False

    def test_device_without_text_is_off(self) -> None:
        assert is_enabled(SimpleNamespace(config_path=Path(os.devnull))) is False

    @pytest.mark.parametrize(
        ("text", "enabled"),
        [
            ("telemetry:\n  enabled: false\n", False),
            ("telemetry:\n  enabled: true\n", True),
            ("", True),
        ],
    )
    def test_text_from_config_is_used_without_opening_the_file(
        self, tmp_path: Path, text: str, enabled: bool
    ) -> None:
        gone = tmp_path / "read-once.yaml"  # never created
        config = SimpleNamespace(config_path=gone, user_config_text=text)
        with patch.object(Path, "read_text", side_effect=AssertionError("opened")):
            assert is_enabled(config) is enabled

    def test_unreadable_file_is_off(self, tmp_path: Path) -> None:
        # A directory where the file should be: exists, but can't be read.
        (tmp_path / "config.yaml").mkdir()
        assert is_enabled(SimpleNamespace(config_path=tmp_path / "config.yaml")) is False

    def test_project_file_cannot_turn_it_back_on(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from deepctl_core import Config

        user = tmp_path / "user.yaml"
        user.write_text("telemetry:\n  enabled: false\n", encoding="utf-8")
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "deepgram.yaml").write_text(
            "telemetry:\n  enabled: true\n", encoding="utf-8"
        )
        monkeypatch.chdir(repo)

        config = Config(config_path=str(user))
        # The project file's telemetry key is ignored, so the merged view
        # keeps the user's value too.
        assert config.get("telemetry.enabled") is False
        assert is_enabled(config) is False
        assert "Telemetry is off" in render_notice(config)

    def test_project_file_cannot_turn_it_off_either(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from deepctl_core import Config

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "deepgram.yaml").write_text(
            "telemetry:\n  enabled: false\n", encoding="utf-8"
        )
        monkeypatch.chdir(repo)
        assert is_enabled(Config(config_path=str(tmp_path / "absent.yaml"))) is True


GOLDEN = Path(__file__).resolve().parents[1] / "golden"
REPO_README = Path(__file__).resolve().parents[4] / "README.md"


class TestRenderNotice:
    def test_on_message_matches_golden(self) -> None:
        from deepctl_telemetry.notice import NOTICE_ON

        golden = (GOLDEN / "notice_on.txt").read_text(encoding="utf-8")
        assert golden.endswith("\n")
        assert NOTICE_ON == golden[:-1]
        assert render_notice(_config(True)) == NOTICE_ON

    def test_on_message_names_existing_opt_outs(self) -> None:
        notice = render_notice(_config(True))
        assert "--no-telemetry" in notice
        assert "DEEPCTL_TELEMETRY_DISABLED=1" in notice
        assert "telemetry.enabled: false" in notice
        assert "dg config" not in notice

    def test_off_message_matches_golden(self) -> None:
        golden = (GOLDEN / "notice_off.txt").read_text(encoding="utf-8")
        assert render_notice(_config(False)) == golden[:-1]

    def test_no_destination_message_matches_golden(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from deepctl_telemetry.client import DSN_ENV_VAR
        from deepctl_telemetry.notice import NOTICE_NONE

        monkeypatch.delenv(DSN_ENV_VAR, raising=False)
        golden = (GOLDEN / "notice_none.txt").read_text(encoding="utf-8")
        assert golden[:-1] == NOTICE_NONE
        assert render_notice(_config(True)) == NOTICE_NONE
        assert render_notice(_config(False)) == NOTICE_NONE

    def test_dim_only_on_a_terminal_without_no_color(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from deepctl_telemetry import notice

        monkeypatch.setattr(notice, "_stdout_is_tty", lambda: True)
        monkeypatch.delenv("NO_COLOR", raising=False)
        assert render_notice(_config(False)) != "Telemetry is off."
        assert "Telemetry is off." in render_notice(_config(False))

        monkeypatch.setenv("NO_COLOR", "1")
        assert render_notice(_config(False)) == "Telemetry is off."


class TestReadmeSection:
    def test_readme_telemetry_section_matches_golden(self) -> None:
        if not REPO_README.exists():
            pytest.skip("repository README not available")
        readme = REPO_README.read_text(encoding="utf-8")
        start = readme.index("## Telemetry\n")
        end = readme.index("\n## ", start + 1) + 1
        golden = (GOLDEN / "readme_telemetry.md").read_text(encoding="utf-8")
        assert readme[start:end] == golden + "\n"

    def test_readme_says_nothing_is_sent(self) -> None:
        golden = (GOLDEN / "readme_telemetry.md").read_text(encoding="utf-8")
        assert "This version of dg sends no telemetry" in golden
        assert "sentry.io" not in golden


def _patched_sdk() -> Any:
    from contextlib import ExitStack

    stack = ExitStack()
    mocks = SimpleNamespace(
        init=stack.enter_context(patch("sentry_sdk.init")),
        capture=stack.enter_context(patch("sentry_sdk.capture_exception")),
        flush=stack.enter_context(patch("sentry_sdk.flush")),
    )
    return stack, mocks


class TestLazyInit:
    """init_telemetry decides and installs hooks; the SDK loads on error only."""

    def test_init_does_not_initialize_sdk(self, fresh_state: Any) -> None:
        with patch("sentry_sdk.init") as mock_init:
            assert init_telemetry(_config(True)) is True
        mock_init.assert_not_called()

    def test_init_records_decision_and_config_path(self, fresh_state: Any) -> None:
        config = _config(False)
        assert init_telemetry(config) is False
        assert fresh_state._state["opt_out"] == "config"
        assert fresh_state._state["config_path"] == str(config.config_path)

    def test_disable_for_run_wins_over_config(self, fresh_state: Any) -> None:
        from deepctl_telemetry import disable_for_run

        disable_for_run()
        assert is_enabled(_config(True)) is False
        assert init_telemetry(_config(True)) is False
        assert fresh_state._state["opt_out"] == "flag"

    def test_report_uses_errors_only_settings(self, fresh_state: Any) -> None:
        init_telemetry(_config(True))
        stack, sdk = _patched_sdk()
        with stack, patch("sentry_sdk.start_session") as mock_start:
            assert fresh_state.report_exception(RuntimeError("x")) is True

        sdk.capture.assert_called_once()
        kwargs = sdk.init.call_args.kwargs
        assert kwargs["default_integrations"] is False
        assert kwargs["auto_enabling_integrations"] is False
        assert kwargs["integrations"] == []
        assert kwargs["traces_sample_rate"] == 0
        assert kwargs["profiles_sample_rate"] == 0
        assert kwargs["enable_logs"] is False
        assert kwargs["auto_session_tracking"] is False
        assert kwargs["attach_stacktrace"] is False
        assert kwargs["include_local_variables"] is False
        assert kwargs["include_source_context"] is False
        assert kwargs["max_breadcrumbs"] == 0
        assert kwargs["send_default_pii"] is False
        assert kwargs["server_name"] == ""
        assert kwargs["spotlight"] is False
        assert kwargs["http_proxy"] == ""
        assert kwargs["https_proxy"] == ""
        assert kwargs["before_send"] is _before_send
        assert kwargs["before_send_transaction"] is _rebuild_event
        mock_start.assert_not_called()

    def test_report_is_noop_when_disabled(self, fresh_state: Any) -> None:
        init_telemetry(_config(False))
        with patch("sentry_sdk.init") as mock_init:
            assert fresh_state.report_exception(RuntimeError("x")) is False
        mock_init.assert_not_called()

    def test_report_once_per_process(self, fresh_state: Any) -> None:
        init_telemetry(_config(True))
        stack, sdk = _patched_sdk()
        with stack:
            fresh_state.report_exception(RuntimeError("a"))
            fresh_state.report_exception(RuntimeError("b"))
        assert sdk.capture.call_count == 1

    def test_concurrent_reports_initialize_once(self, fresh_state: Any) -> None:
        init_telemetry(_config(True))
        barrier = threading.Barrier(8)

        def worker() -> None:
            barrier.wait()
            fresh_state.report_exception(RuntimeError("x"))

        stack, sdk = _patched_sdk()
        with stack:
            threads = [threading.Thread(target=worker) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        assert sdk.init.call_count == 1
        assert sdk.capture.call_count == 1

    @pytest.mark.parametrize(
        ("elapsed", "expected"),
        [(0.05, 0.5), (0.3, 0.3), (1.0, 0.05)],
        ids=["fast-import", "slow-import", "budget-spent"],
    )
    def test_flush_timeout(
        self, fresh_state: Any, elapsed: float, expected: float
    ) -> None:
        init_telemetry(_config(True))
        clock = iter([100.0, 100.0 + elapsed])
        stack, sdk = _patched_sdk()
        with stack, patch(
            "deepctl_telemetry.client.time.monotonic", side_effect=lambda: next(clock)
        ):
            fresh_state.report_exception(RuntimeError("x"))
        assert sdk.flush.call_args.kwargs["timeout"] == pytest.approx(expected)

    def test_report_swallows_sdk_errors(self, fresh_state: Any) -> None:
        init_telemetry(_config(True))
        with patch("sentry_sdk.init", side_effect=RuntimeError("boom")):
            assert fresh_state.report_exception(RuntimeError("x")) is False

    def test_keyboard_interrupt_during_flush_propagates(
        self, fresh_state: Any
    ) -> None:
        init_telemetry(_config(True))
        stack, sdk = _patched_sdk()
        sdk.flush.side_effect = KeyboardInterrupt
        with stack, pytest.raises(KeyboardInterrupt):
            fresh_state.report_exception(RuntimeError("x"))


class TestDsnOverride:
    @pytest.mark.parametrize(
        "dsn",
        [
            "http://k@127.0.0.1:9/1",
            "http://k@127.0.0.2:9/1",
            "http://k@localhost:9/1",
            "http://k@[::1]:9/1",
        ],
    )
    def test_loopback_override_is_used(
        self, monkeypatch: pytest.MonkeyPatch, dsn: str
    ) -> None:
        from deepctl_telemetry import client

        monkeypatch.setenv(client.DSN_ENV_VAR, dsn)
        assert client._dsn() == dsn

    @pytest.mark.parametrize(
        "dsn",
        [
            "https://k@attacker.example/1",
            "https://k@10.0.0.5/1",
            "https://k@127.0.0.1.attacker.example/1",
            "not a url",
        ],
    )
    def test_other_overrides_are_ignored(
        self, monkeypatch: pytest.MonkeyPatch, dsn: str
    ) -> None:
        from deepctl_telemetry import client

        monkeypatch.setenv(client.DSN_ENV_VAR, dsn)
        assert client._dsn() is None

    @pytest.mark.parametrize("dsn", [None, "", "   "])
    def test_no_override_means_no_destination(
        self, monkeypatch: pytest.MonkeyPatch, dsn: str | None
    ) -> None:
        from deepctl_telemetry import client

        if dsn is None:
            monkeypatch.delenv(client.DSN_ENV_VAR, raising=False)
        else:
            monkeypatch.setenv(client.DSN_ENV_VAR, dsn)
        assert client._dsn() is None

    def test_there_is_no_built_in_dsn(self) -> None:
        import inspect

        from deepctl_telemetry import client

        source = inspect.getsource(client)
        assert not hasattr(client, "SENTRY_DSN")
        assert "sentry.io" not in source
        assert not [ln for ln in source.splitlines() if ln.startswith("SENTRY_DSN")]


class TestBuildSwitch:
    """TELEMETRY_BUILD_ENABLED = False makes telemetry inert in every path."""

    @pytest.fixture(autouse=True)
    def _hard_off(self, fresh_state, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(fresh_state, "TELEMETRY_BUILD_ENABLED", False)
        monkeypatch.setenv(fresh_state.DSN_ENV_VAR, "http://public@127.0.0.1:9/1")

    def test_everything_is_off(self, fresh_state) -> None:
        import sys

        hook = sys.excepthook
        assert is_enabled(_config(True)) is False
        assert init_telemetry(_config(True)) is False
        assert fresh_state._state["opt_out"] == "build"
        assert sys.excepthook is hook
        assert render_notice(_config(True)) == "This version of dg sends no telemetry."

    def test_report_never_touches_the_sdk(self, fresh_state) -> None:
        init_telemetry(_config(True))
        fresh_state._state["enabled"] = True  # even if state were forced on
        with patch("sentry_sdk.init") as mock_init:
            assert fresh_state.report_exception(RuntimeError("x")) is False
        mock_init.assert_not_called()


class TestNoDestination:
    """Without a loopback DEEPCTL_TELEMETRY_DSN, nothing is set up or sent."""

    @pytest.fixture(autouse=True)
    def _no_dsn(self, fresh_state, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(fresh_state.DSN_ENV_VAR, raising=False)

    @pytest.fixture
    def sdk(self, monkeypatch: pytest.MonkeyPatch) -> Any:
        """Stands in for sentry_sdk: any use of the SDK is recorded."""
        import sys
        from unittest.mock import MagicMock

        fake = MagicMock(name="sentry_sdk")
        monkeypatch.setitem(sys.modules, "sentry_sdk", fake)
        return fake

    @pytest.fixture
    def connects(self, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
        """Records, and refuses, every outbound connection or DNS lookup."""
        import socket

        calls: list[Any] = []

        def refuse(*args: Any, **kwargs: Any) -> Any:
            calls.append(args)
            raise OSError("network use in a no-telemetry test")

        monkeypatch.setattr(socket.socket, "connect", refuse)
        monkeypatch.setattr(socket.socket, "connect_ex", refuse)
        monkeypatch.setattr(socket, "create_connection", refuse)
        monkeypatch.setattr(socket, "getaddrinfo", refuse)
        return calls

    @pytest.mark.parametrize("value", [True, None])
    def test_off_whatever_the_user_config_says(
        self, fresh_state, value: bool | None
    ) -> None:
        import sys

        hook, thread_hook = sys.excepthook, threading.excepthook
        assert is_enabled(_config(value)) is False
        assert init_telemetry(_config(value)) is False
        assert fresh_state._state["opt_out"] == "dsn"
        assert sys.excepthook is hook
        assert threading.excepthook is thread_hook

    def test_unexpected_error_sends_nothing(
        self, fresh_state, sdk: Any, connects: list[Any]
    ) -> None:
        init_telemetry(_config(True))
        assert fresh_state.report_exception(RuntimeError("unexpected")) is False
        assert sdk.mock_calls == []
        assert connects == []
        assert fresh_state._state["reported"] is False
        assert fresh_state._state["sent"] is False

    def test_even_if_state_were_forced_on(
        self, fresh_state, sdk: Any, connects: list[Any]
    ) -> None:
        init_telemetry(_config(True))
        fresh_state._state["enabled"] = True
        try:
            raise KeyError("unexpected")
        except KeyError as exc:
            assert fresh_state.report_exception(exc) is False
        assert sdk.mock_calls == []
        assert connects == []
        assert fresh_state._state["reported"] is False

    def test_sdk_is_not_imported(self, fresh_state) -> None:
        import subprocess
        import sys

        code = (
            "import sys\n"
            "from types import SimpleNamespace\n"
            "from pathlib import Path\n"
            "from deepctl_telemetry import client, init_telemetry\n"
            "cfg = SimpleNamespace(config_path=Path('/nonexistent/c.yaml'))\n"
            "assert init_telemetry(cfg) is False\n"
            "assert client.report_exception(RuntimeError('x')) is False\n"
            "assert 'sentry_sdk' not in sys.modules, 'sentry_sdk imported'\n"
            "print('OK')\n"
        )
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in (fresh_state.DSN_ENV_VAR, DISABLE_ENV_VAR)
        }
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "OK"
        assert result.stderr == ""


class TestReportable:
    """Only errors BaseCommand didn't expect are reported."""

    def test_plain_exception(self) -> None:
        from deepctl_telemetry.client import _reportable

        exc = RuntimeError("x")
        assert _reportable(exc) is exc

    def test_click_errors_are_not_reported(self) -> None:
        import click
        from deepctl_telemetry.client import _reportable

        assert _reportable(click.ClickException("Project ID required")) is None
        assert _reportable(click.UsageError("bad")) is None
        assert _reportable(click.Abort()) is None
        assert _reportable(KeyboardInterrupt()) is None
        assert _reportable(SystemExit(1)) is None

    def test_user_error_raised_while_handling_another_is_not_reported(
        self,
    ) -> None:
        import click
        from deepctl_telemetry.client import _reportable

        try:
            try:
                raise FileNotFoundError("missing.wav")
            except FileNotFoundError:
                raise click.ClickException("File not found")
        except click.ClickException as outer:
            assert _reportable(outer) is None

    def test_user_error_raised_from_another_is_not_reported(self) -> None:
        import click
        from deepctl_telemetry.client import _reportable

        try:
            try:
                raise ConnectionError("x")
            except ConnectionError as e:
                raise click.ClickException("Could not fetch skills") from e
        except click.ClickException as outer:
            assert _reportable(outer) is None

    def test_unexpected_command_error_reports_its_cause(self) -> None:
        from deepctl_core.base_command import UnexpectedCommandError
        from deepctl_telemetry.client import _reportable

        inner = KeyError("x")
        try:
            try:
                raise inner
            except KeyError as e:
                raise UnexpectedCommandError(str(e)) from e
        except UnexpectedCommandError as outer:
            assert _reportable(outer) is inner

    def test_unexpected_command_error_without_cause(self) -> None:
        from deepctl_core.base_command import UnexpectedCommandError
        from deepctl_telemetry.client import _reportable

        assert _reportable(UnexpectedCommandError("x")) is None


class TestRebuildEvent:
    def test_rebuild_event_drops_request_and_user(self) -> None:
        event = {
            "logger": "deepctl",
            "exception": {"values": [{"type": "RuntimeError", "value": "x"}]},
            "request": {"headers": {"Authorization": "Bearer secret"}},
            "user": {"email": "x@example.com", "id": "user-1"},
        }
        result = _rebuild_event(event, {})
        assert result is not None
        assert "request" not in result
        assert "user" not in result
        assert "logger" not in result

    def test_names_outside_known_packages_become_other(self) -> None:
        event = {
            "exception": {
                "values": [
                    {
                        "type": "Odd_TYPENAME_SENTINEL",
                        "module": "acme_client_project.errors",
                        "stacktrace": {
                            "frames": [
                                {
                                    "module": "acme_client_project.jobs",
                                    "function": "sync_SENTINEL",
                                    "lineno": 7,
                                },
                                {"module": "__main__", "function": "<module>"},
                                {
                                    "module": "deepctl_core.base_command",
                                    "function": "execute",
                                    "lineno": 170,
                                },
                                {"module": "json.decoder", "function": "decode"},
                            ]
                        },
                    },
                    {"type": "KeyError", "module": "builtins"},
                ]
            }
        }
        result = _rebuild_event(event, {})
        assert result is not None
        first, second = result["exception"]["values"]
        assert first["type"] == "<other>"
        assert first["stacktrace"]["frames"] == [
            {"module": "<other>", "function": "<other>", "lineno": 7},
            {"module": "<other>", "function": "<other>"},
            {
                "module": "deepctl_core.base_command",
                "function": "execute",
                "lineno": 170,
            },
            {"module": "json.decoder", "function": "decode"},
        ]
        assert second["type"] == "KeyError"
        assert "SENTINEL" not in repr(result)
        assert "acme" not in repr(result)

    def test_plugin_named_like_dg_is_other(self) -> None:
        event = {
            "exception": {
                "values": [
                    {
                        "type": "Odd_TYPENAME_SENTINEL",
                        "module": "deepctl_atk",
                        "stacktrace": {
                            "frames": [{"module": "deepctl_atk", "function": "run"}]
                        },
                    }
                ]
            }
        }
        result = _rebuild_event(event, {})
        assert result is not None
        (value,) = result["exception"]["values"]
        assert value["type"] == "<other>"
        assert value["stacktrace"]["frames"] == [
            {"module": "<other>", "function": "<other>"}
        ]

    def test_first_party_list_matches_the_workspace(self) -> None:
        from pathlib import Path as _Path

        from deepctl_telemetry.client import _FIRST_PARTY

        root = _Path(__file__).resolve().parents[4]
        if not (root / "packages").is_dir():
            pytest.skip("workspace not available")
        candidates = [*(root / "packages").glob("*/src/*"), *(root / "src").glob("*")]
        shipped = {
            p.name
            for p in candidates
            if (p / "__init__.py").is_file() and p.name != "deepctl_plugin_example"
        }
        assert shipped == _FIRST_PARTY

    def test_sdk_accepts_every_option(self) -> None:
        """The installed SDK knows every option we pass.

        `enable_logs` became a top-level option in sentry-sdk 2.35.0, which is
        the package floor; an older SDK raises on init and no report is sent.
        """
        import inspect

        from deepctl_telemetry.client import _sdk_options
        from sentry_sdk.consts import ClientConstructor

        accepted = set(inspect.signature(ClientConstructor.__init__).parameters)
        assert set(_sdk_options("http://k@127.0.0.1:9/1", "0.0.0")) <= accepted

    @pytest.mark.parametrize("scheme", ["http", "https"])
    def test_sdk_ignores_proxy_from_environment(
        self, scheme: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An empty proxy option keeps the SDK off any environment proxy.

        Without it the SDK reads HTTP(S)_PROXY, or the system proxy settings
        on macOS and Windows, and a report to a loopback DSN could leave the
        machine through that proxy.
        """
        import sentry_sdk
        import urllib3
        from deepctl_telemetry.client import _sdk_options

        for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            monkeypatch.setenv(name, "http://203.0.113.1:3128")
        for name in ("NO_PROXY", "no_proxy"):
            monkeypatch.delenv(name, raising=False)

        options = _sdk_options(f"{scheme}://k@127.0.0.1:9/1", "0.0.0")
        assert options["http_proxy"] == ""
        assert options["https_proxy"] == ""
        sdk_client = sentry_sdk.Client(**options)
        try:
            pool = getattr(sdk_client.transport, "_pool", None)
            assert pool is not None
            assert not isinstance(pool, urllib3.ProxyManager)
        finally:
            sdk_client.close(timeout=0)


class TestSentOnce:
    def test_before_send_lets_only_the_first_event_through(
        self, fresh_state: Any
    ) -> None:
        event = {"exception": {"values": [{"type": "RuntimeError"}]}}
        assert _before_send(dict(event), {}) is not None
        assert _before_send(dict(event), {}) is None
        assert fresh_state._state["sent"] is True

    def test_dropped_event_does_not_use_up_the_slot(self, fresh_state: Any) -> None:
        assert _before_send({"message": "log line"}, {}) is None
        event = {"exception": {"values": [{"type": "RuntimeError"}]}}
        assert _before_send(event, {}) is not None


class TestBrokenPipeFilter:
    """Drop broken/closed-stream events from `dg mcp` host disconnects.

    Anchor: DX-CLI-P (BrokenPipeError on the startup notification write,
    cascading into rich's ValueError: I/O operation on closed file).
    """

    def test_drops_broken_pipe_error(self) -> None:
        from deepctl_telemetry.client import _is_broken_pipe

        event = {
            "exception": {
                "values": [
                    {
                        "type": "BrokenPipeError",
                        "value": "[Errno 32] Broken pipe",
                        "mechanism": {"type": "excepthook", "handled": False},
                    }
                ]
            },
        }
        assert _is_broken_pipe(event)

    def test_drops_closed_file_value_error(self) -> None:
        from deepctl_telemetry.client import _is_broken_pipe

        event = {
            "exception": {
                "values": [
                    {
                        "type": "ValueError",
                        "value": "I/O operation on closed file",
                        "mechanism": {"type": "excepthook", "handled": False},
                    }
                ]
            },
        }
        assert _is_broken_pipe(event)

    def test_keeps_unrelated_value_error(self) -> None:
        from deepctl_telemetry.client import _is_broken_pipe

        event = {
            "exception": {
                "values": [{"type": "ValueError", "value": "bad config value"}]
            },
        }
        assert not _is_broken_pipe(event)

    def test_handles_missing_fields(self) -> None:
        from deepctl_telemetry.client import _is_broken_pipe

        assert not _is_broken_pipe({})
        assert not _is_broken_pipe({"exception": None})
        assert not _is_broken_pipe({"exception": {"values": []}})

    def test_rebuild_event_returns_none_for_broken_pipe(self) -> None:
        from deepctl_telemetry.client import _rebuild_event

        event = {
            "exception": {
                "values": [
                    {
                        "type": "BrokenPipeError",
                        "value": "[Errno 32] Broken pipe",
                        "mechanism": {"type": "excepthook", "handled": False},
                    }
                ]
            },
        }
        assert _rebuild_event(event, {}) is None
