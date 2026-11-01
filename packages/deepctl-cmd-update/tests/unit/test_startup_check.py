"""Unit tests for startup update check."""

import io
import json
import os
import stat
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from deepctl_cmd_update import check_cache
from deepctl_cmd_update import startup_check as mod
from deepctl_cmd_update.startup_check import (
    _is_ci,
    _is_oneshot,
    check_and_notify,
    print_pending_notification,
)


_REAL_IS_CI = mod._is_ci
_REAL_IS_AGENT = mod._is_agent


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    """dg's cache directory, pointed at a fresh temp dir (not created yet)."""
    from deepctl import _cache

    root = tmp_path / "cache" / "deepctl"
    monkeypatch.setattr(_cache, "cache_root", lambda: str(root))
    return root


@pytest.fixture
def check_allowed(monkeypatch, cache_dir):
    """A run where the startup checks may start: an interactive person at a
    terminal, outside CI and ``dg mcp``. Returns the cache directory."""
    import deepctl_cmd_update.startup_check as core

    monkeypatch.delenv(core.NO_UPDATE_CHECK_ENV_VAR, raising=False)
    monkeypatch.setattr(core, "_is_ci", lambda: False)
    monkeypatch.setattr(core, "_stderr_is_terminal", lambda: True)
    monkeypatch.setattr(core, "_is_agent", lambda: False)
    monkeypatch.setattr(core, "_is_oneshot", lambda: False)
    monkeypatch.setattr(core, "_subcommand", lambda: None)
    monkeypatch.setattr(core, "_get_check_interval_seconds", lambda quiet: 86400.0)
    return cache_dir


@pytest.fixture(autouse=True)
def _reset_state():
    mod._thread = None
    mod._result = {}
    yield
    if mod._thread is not None:
        mod._thread.join(timeout=5.0)
    mod._thread = None
    mod._result = {}


def _finished_thread() -> threading.Thread:
    thread = threading.Thread(target=lambda: None)
    thread.start()
    thread.join()
    return thread


@pytest.fixture(autouse=True)
def _invoked_as_dg(monkeypatch):
    """Notices name the program as invoked; these tests expect dg."""
    monkeypatch.setenv("DEEPCTL_PROG_NAME", "dg")


class TestCIDetection:
    """Test CI environment detection."""

    @patch.dict("os.environ", {}, clear=True)
    def test_not_ci(self):
        assert _is_ci() is False

    @patch.dict("os.environ", {"CI": "true"}, clear=True)
    def test_ci_generic(self):
        assert _is_ci() is True

    @patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}, clear=True)
    def test_github_actions(self):
        assert _is_ci() is True

    @patch.dict("os.environ", {"GITLAB_CI": "true"}, clear=True)
    def test_gitlab_ci(self):
        assert _is_ci() is True

    @patch.dict("os.environ", {"JENKINS_URL": "http://ci"}, clear=True)
    def test_jenkins(self):
        assert _is_ci() is True

    @patch.dict("os.environ", {"TRAVIS": "true"}, clear=True)
    def test_travis(self):
        assert _is_ci() is True


class TestOneshotDetection:
    """Test one-shot execution detection."""

    def test_not_oneshot(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("UV_CACHE_DIR", raising=False)
        monkeypatch.delenv("PIPX_HOME", raising=False)
        monkeypatch.delenv("DEEPCTL_LAUNCHER", raising=False)
        monkeypatch.setattr(sys, "prefix", str(tmp_path / "venv"))
        assert _is_oneshot() is False

    def test_uvx_oneshot(self, monkeypatch, tmp_path):
        monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uvc"))
        monkeypatch.setattr(sys, "prefix", str(tmp_path / "uvc" / "archive-v0" / "x"))
        assert _is_oneshot() is True

    def test_npx_oneshot(self, monkeypatch, tmp_path):
        main = sys.modules.get("deepctl.main")
        if main is not None:
            monkeypatch.setattr(main, "LAUNCHER_CAPTURED", False, raising=False)
        monkeypatch.setenv("DEEPCTL_LAUNCHER", "npx")
        monkeypatch.setattr(sys, "prefix", str(tmp_path / "venv"))
        assert _is_oneshot() is True


class TestSkipConditions:
    """No check thread starts, and no file is written, when a run skips it."""

    def _assert_skipped(self, cache_dir):
        with patch.object(mod.threading, "Thread") as thread_cls:
            check_and_notify(current_version="1.0.0")
        thread_cls.assert_not_called()
        assert mod._thread is None
        assert not cache_dir.exists()

    def test_starts_when_allowed(self, check_allowed):
        with patch.object(mod, "_background_check", lambda ver, res: None):
            check_and_notify(current_version="1.0.0")
        assert mod._thread is not None
        assert mod._thread.daemon is True
        assert mod._thread.name == mod.THREAD_NAME

    def test_quiet(self, check_allowed):
        with patch.object(mod.threading, "Thread") as thread_cls:
            check_and_notify(current_version="1.0.0", quiet=True)
        thread_cls.assert_not_called()

    def test_ci(self, check_allowed, monkeypatch):
        monkeypatch.setattr(mod, "_is_ci", lambda: True)
        self._assert_skipped(check_allowed)

    def test_ci_env(self, check_allowed, monkeypatch):
        monkeypatch.setattr(mod, "_is_ci", _REAL_IS_CI)
        monkeypatch.setenv("CI", "1")
        self._assert_skipped(check_allowed)

    def test_agent(self, check_allowed, monkeypatch):
        monkeypatch.setattr(mod, "_is_agent", lambda: True)
        self._assert_skipped(check_allowed)

    def test_ai_agent_marker(self, check_allowed, monkeypatch):
        monkeypatch.setattr(mod, "_is_agent", _REAL_IS_AGENT)
        monkeypatch.setenv("AI_AGENT", "x")
        self._assert_skipped(check_allowed)

    def test_stderr_not_a_terminal(self, check_allowed, monkeypatch):
        monkeypatch.setattr(mod, "_stderr_is_terminal", lambda: False)
        self._assert_skipped(check_allowed)

    def test_mcp(self, check_allowed, monkeypatch):
        monkeypatch.setattr(mod, "_subcommand", lambda: "mcp")
        self._assert_skipped(check_allowed)

    @pytest.mark.parametrize("value", ["1", "true", "yes"])
    def test_opt_out_env(self, check_allowed, monkeypatch, value):
        monkeypatch.setenv(mod.NO_UPDATE_CHECK_ENV_VAR, value)
        self._assert_skipped(check_allowed)

    @pytest.mark.parametrize("value", ["", "0", "false"])
    def test_opt_out_env_off(self, check_allowed, monkeypatch, value):
        monkeypatch.setenv(mod.NO_UPDATE_CHECK_ENV_VAR, value)
        with patch.object(mod, "_background_check", lambda ver, res: None):
            check_and_notify(current_version="1.0.0")
        assert mod._thread is not None

    def test_oneshot(self, check_allowed, monkeypatch):
        monkeypatch.setattr(mod, "_is_oneshot", lambda: True)
        self._assert_skipped(check_allowed)

    def test_unusable_cache_dir(self, check_allowed, monkeypatch):
        """A foreign-owned cache dir: no check thread, no network, no state."""
        check_allowed.mkdir(parents=True, mode=0o755)
        monkeypatch.setattr(
            check_cache.os, "getuid", lambda: os.stat(check_allowed).st_uid + 1
        )
        with patch.object(mod.threading, "Thread") as thread_cls:
            check_and_notify(current_version="1.0.0")
        thread_cls.assert_not_called()
        assert list(check_allowed.iterdir()) == []

    def test_never(self, check_allowed, monkeypatch):
        monkeypatch.setattr(mod, "_get_check_interval_seconds", lambda quiet: None)
        self._assert_skipped(check_allowed)


class TestSubcommand:
    @pytest.mark.parametrize(
        ("args", "expected"),
        [
            (["mcp"], "mcp"),
            (["-q", "mcp", "--transport", "http"], "mcp"),
            (["--profile", "mcp", "models"], "models"),
            (["-o", "json", "mcp"], "mcp"),
            (["models", "mcp"], "models"),
            (["--quiet"], None),
        ],
    )
    def test_reads_the_run_args(self, monkeypatch, args, expected):
        from deepctl import _cli

        monkeypatch.setattr(_cli, "_run_args", args)
        assert mod._subcommand() == expected


class TestBackoff:
    """The cache file records every attempt, success or not."""

    def test_failure_is_recorded_and_waits_a_day(self, check_allowed):
        with patch("httpx.Client.get", side_effect=OSError("unreachable")):
            check_and_notify(current_version="1.0.0")
            mod._thread.join(timeout=5.0)

        state = json.loads((check_allowed / check_cache.UPDATE_FILE).read_text())
        assert state["ok"] is False
        assert state["latest"] is None
        assert state["source"] == "pypi"
        assert abs(state["checked_at"] - time.time()) < 60
        assert mod._result == {}

        # The next run within the interval makes no attempt.
        with patch.object(mod.threading, "Thread") as thread_cls:
            check_and_notify(current_version="1.0.0")
        thread_cls.assert_not_called()

    def test_attempt_recorded_before_connecting(self, check_allowed):
        """A check the process never lets finish still counts."""
        seen = {}

        def hang(*args, **kwargs):
            path = check_allowed / check_cache.UPDATE_FILE
            seen["state"] = json.loads(path.read_text())
            raise OSError("timed out")

        with patch("httpx.Client.get", side_effect=hang):
            check_and_notify(current_version="1.0.0")
            mod._thread.join(timeout=5.0)
        assert seen["state"]["ok"] is False

    def test_nothing_recorded_until_the_client_is_built(self, check_allowed):
        """Building the client (CA bundle, proxies) comes before the record."""
        with patch("httpx.Client.__init__", side_effect=OSError("no CA bundle")):
            check_and_notify(current_version="1.0.0")
            mod._thread.join(timeout=5.0)
        assert not (check_allowed / check_cache.UPDATE_FILE).exists()

    def test_run_ending_before_the_request_keeps_the_check_due(
        self, check_allowed, monkeypatch
    ):
        """Nothing is recorded until the request is about to go out."""
        monkeypatch.setitem(sys.modules, "httpx", None)  # import fails
        check_and_notify(current_version="1.0.0")
        mod._thread.join(timeout=5.0)
        assert not (check_allowed / check_cache.UPDATE_FILE).exists()
        assert check_cache.due(check_cache.UPDATE_FILE, 86400.0)

    def test_success_is_recorded(self, check_allowed):
        response = MagicMock()
        response.json.return_value = {"info": {"version": "2.0.0"}}
        with patch("httpx.Client.get", return_value=response):
            check_and_notify(current_version="1.0.0")
            mod._thread.join(timeout=5.0)

        state = json.loads((check_allowed / check_cache.UPDATE_FILE).read_text())
        assert state["ok"] is True
        assert state["latest"] == "2.0.0"
        assert mod._result == {"latest": "2.0.0", "current": "1.0.0"}

    def test_due_again_after_the_interval(self, check_allowed):
        assert check_cache.usable()
        check_cache.write(check_cache.UPDATE_FILE, ok=False, latest=None, source="pypi")
        path = check_allowed / check_cache.UPDATE_FILE
        state = json.loads(path.read_text())
        state["checked_at"] -= 86401
        path.write_text(json.dumps(state))
        assert check_cache.due(check_cache.UPDATE_FILE, 86400.0)

    def test_future_timestamp_is_due(self, check_allowed):
        assert check_cache.usable()
        check_cache.write(check_cache.UPDATE_FILE, ok=True, latest="1", source="pypi")
        path = check_allowed / check_cache.UPDATE_FILE
        state = json.loads(path.read_text())
        state["checked_at"] += 10 * 86400
        path.write_text(json.dumps(state))
        assert check_cache.due(check_cache.UPDATE_FILE, 86400.0)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
class TestCacheFile:
    def test_modes(self, cache_dir):
        assert check_cache.usable()
        check_cache.write(check_cache.UPDATE_FILE, ok=True, latest="1", source="pypi")
        assert stat.S_IMODE(cache_dir.stat().st_mode) == 0o700
        path = cache_dir / check_cache.UPDATE_FILE
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert [p.name for p in cache_dir.iterdir()] == [check_cache.UPDATE_FILE]

    def test_created_private_under_umask_002(self, cache_dir):
        old = os.umask(0o002)
        try:
            assert check_cache.usable()
        finally:
            os.umask(old)
        assert stat.S_IMODE(cache_dir.stat().st_mode) == 0o700

    @pytest.mark.parametrize("mode", [0o775, 0o770, 0o755, 0o777])
    def test_own_permissive_dir_is_tightened(self, cache_dir, mode):
        """Older versions made this directory with a plain mkdir (0775 under umask 002)."""
        cache_dir.mkdir(parents=True)
        os.chmod(cache_dir, mode)
        assert check_cache.usable()
        assert stat.S_IMODE(cache_dir.stat().st_mode) == 0o700
        check_cache.write(check_cache.UPDATE_FILE, ok=True, latest="1", source="pypi")
        assert check_cache.read(check_cache.UPDATE_FILE) is not None

    def test_foreign_owned_dir_is_unusable(self, cache_dir, monkeypatch):
        cache_dir.mkdir(parents=True, mode=0o755)
        monkeypatch.setattr(check_cache.os, "getuid", lambda: os.stat(cache_dir).st_uid + 1)
        assert check_cache.usable() is False
        assert stat.S_IMODE(cache_dir.stat().st_mode) == 0o755  # left alone

    def test_tightening_failure_is_unusable(self, cache_dir, monkeypatch):
        cache_dir.mkdir(parents=True)
        os.chmod(cache_dir, 0o775)

        def refuse(fd, mode):
            raise PermissionError("read-only")

        monkeypatch.setattr(check_cache.os, "fchmod", refuse)
        assert check_cache.usable() is False

    def test_symlinked_dir_is_unusable(self, cache_dir, tmp_path):
        other = tmp_path / "other"
        other.mkdir(mode=0o700)
        cache_dir.parent.mkdir(parents=True)
        cache_dir.symlink_to(other)
        assert check_cache.usable() is False

    def test_group_writable_dir_is_never_written_without_tightening(self, cache_dir):
        cache_dir.mkdir(parents=True, mode=0o700)
        os.chmod(cache_dir, 0o770)
        check_cache.write(check_cache.UPDATE_FILE, ok=True, latest="1", source="pypi")
        assert list(cache_dir.iterdir()) == []
        assert check_cache.read(check_cache.UPDATE_FILE) is None

    def test_group_writable_file_is_not_read(self, cache_dir):
        assert check_cache.usable()
        check_cache.write(check_cache.UPDATE_FILE, ok=True, latest="1", source="pypi")
        os.chmod(cache_dir / check_cache.UPDATE_FILE, 0o666)
        assert check_cache.read(check_cache.UPDATE_FILE) is None

    def test_symlinked_file_is_not_read(self, cache_dir, tmp_path):
        cache_dir.mkdir(parents=True, mode=0o700)
        target = tmp_path / "elsewhere.json"
        target.write_text(json.dumps({"checked_at": time.time()}))
        os.chmod(target, 0o600)
        (cache_dir / check_cache.UPDATE_FILE).symlink_to(target)
        assert check_cache.read(check_cache.UPDATE_FILE) is None

    @pytest.mark.parametrize("data", ["not json", "[]", '{"checked_at": true}'])
    def test_bad_contents_read_as_never_checked(self, cache_dir, data):
        cache_dir.mkdir(parents=True, mode=0o700)
        path = cache_dir / check_cache.UPDATE_FILE
        path.write_text(data)
        os.chmod(path, 0o600)
        assert check_cache.due(check_cache.UPDATE_FILE, 86400.0)


class TestNoConfigWrite:
    def test_check_never_saves_the_config(self, check_allowed):
        from deepctl_core import Config

        response = MagicMock()
        response.json.return_value = {"info": {"version": "2.0.0"}}
        with (
            patch.object(Config, "save") as save,
            patch.object(Config, "_set_config_value") as set_value,
            patch("httpx.Client.get", return_value=response),
        ):
            check_and_notify(current_version="1.0.0")
            mod._thread.join(timeout=5.0)
        save.assert_not_called()
        set_value.assert_not_called()


class TestPrintPendingNotification:
    """Test notification display."""

    def test_no_notification_when_no_thread(self, capsys):
        print_pending_notification()
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_notification_displayed(self):
        """Notification is printed to stderr when update found."""
        mod._result = {"latest": "2.0.0", "current": "1.0.0"}
        mod._thread = _finished_thread()

        stderr_capture = io.StringIO()
        with patch.object(sys, "stderr", stderr_capture):
            print_pending_notification()

        output = stderr_capture.getvalue()
        assert output.count("Update available") == 1
        assert "2.0.0" in output
        assert "1.0.0" in output
        assert "dg update" in output

        # Printed once: a second call has nothing to show.
        stderr_capture = io.StringIO()
        with patch.object(sys, "stderr", stderr_capture):
            print_pending_notification()
        assert stderr_capture.getvalue() == ""

    def test_running_check_is_dropped_without_waiting(self):
        """A check still running at exit is never joined."""
        release = threading.Event()
        mod._result = {"latest": "2.0.0", "current": "1.0.0"}
        mod._thread = threading.Thread(target=release.wait, daemon=True)
        mod._thread.start()
        thread = mod._thread
        try:
            with patch.object(thread, "join") as join:
                stderr_capture = io.StringIO()
                started = time.monotonic()
                with patch.object(sys, "stderr", stderr_capture):
                    print_pending_notification()
                elapsed = time.monotonic() - started
            join.assert_not_called()
            assert stderr_capture.getvalue() == ""
            assert elapsed < 2.0
            assert mod._thread is None
        finally:
            release.set()
            thread.join(timeout=5.0)

    def test_no_notification_when_up_to_date(self, capsys):
        """No output when no update is available."""
        mod._result = {}  # No update found
        mod._thread = _finished_thread()

        print_pending_notification()
        captured = capsys.readouterr()
        assert captured.err == ""

    @pytest.mark.parametrize(
        "error",
        [
            BrokenPipeError(32, "Broken pipe"),
            ValueError("I/O operation on closed file"),
        ],
    )
    def test_broken_pipe_swallowed(self, error):
        """A closed/broken stderr (e.g. `dg mcp` host disconnect) is tolerated."""
        mod._result = {"latest": "2.0.0", "current": "1.0.0"}
        mod._thread = _finished_thread()

        broken_stderr = MagicMock()
        broken_stderr.write.side_effect = error
        with patch.object(sys, "stderr", broken_stderr):
            # Must not raise.
            print_pending_notification()


class TestSavedNotice:
    """An answer that came too late for its run gives the notice later."""

    @staticmethod
    def _save(cache_dir, **state):
        assert check_cache.usable()
        path = cache_dir / check_cache.UPDATE_FILE
        path.write_text(json.dumps(state))
        os.chmod(path, 0o600)

    @staticmethod
    def _notice() -> str:
        err = io.StringIO()
        with patch.object(sys, "stderr", err):
            print_pending_notification()
        return err.getvalue()

    def test_printed_once_per_interval(self, check_allowed):
        self._save(check_allowed, checked_at=time.time(), ok=True, latest="2.0.0")
        with patch.object(mod.threading, "Thread") as thread_cls:
            check_and_notify(current_version="1.0.0")
        thread_cls.assert_not_called()
        out = self._notice()
        assert out.count("Update available") == 1 and "2.0.0" in out
        state = check_cache.read(check_cache.UPDATE_FILE)
        assert abs(check_cache.notified_at(state) - time.time()) < 60

        check_and_notify(current_version="1.0.0")
        assert self._notice() == ""

    def test_again_after_the_interval(self, check_allowed):
        self._save(
            check_allowed,
            checked_at=time.time(),
            ok=True,
            latest="2.0.0",
            notified_at=time.time() - 86401,
        )
        check_and_notify(current_version="1.0.0")
        assert "2.0.0" in self._notice()

    @pytest.mark.parametrize(
        "state",
        [
            {"ok": True, "latest": "1.0.0"},
            {"ok": True, "latest": "0.9.0"},
            {"ok": False, "latest": None},
            {"ok": True, "latest": "not a version"},
        ],
    )
    def test_nothing_newer_no_notice(self, check_allowed, state):
        self._save(check_allowed, checked_at=time.time(), **state)
        check_and_notify(current_version="1.0.0")
        assert self._notice() == ""

    def test_late_answer_reaches_the_next_run(self, check_allowed):
        """A sub-second command drops the result; the second run shows it."""
        release = threading.Event()
        response = MagicMock()
        response.json.return_value = {"info": {"version": "2.0.0"}}

        def slow_get(*args, **kwargs):
            release.wait(5.0)
            return response

        with patch("httpx.Client.get", side_effect=slow_get):
            check_and_notify(current_version="1.0.0")
            thread = mod._thread
            assert self._notice() == ""  # run 1 ends before the answer
            release.set()
            thread.join(timeout=5.0)

        check_and_notify(current_version="1.0.0")  # run 2: not due
        assert mod._thread is None
        out = self._notice()
        assert out.count("Update available") == 1 and "1.0.0 → 2.0.0" in out

    def test_in_time_notice_is_not_repeated_from_the_cache(self, check_allowed):
        response = MagicMock()
        response.json.return_value = {"info": {"version": "2.0.0"}}
        with patch("httpx.Client.get", return_value=response):
            check_and_notify(current_version="1.0.0")
            mod._thread.join(timeout=5.0)
        assert "2.0.0" in self._notice()
        check_and_notify(current_version="1.0.0")
        assert self._notice() == ""



# One dg process finishing: claim the notice for 2.0.0 at a set time.
_CLAIM = """
import sys, time
from deepctl import _cache
from deepctl_cmd_update import check_cache
_cache.cache_root = lambda: sys.argv[1]
start = float(sys.argv[2])
while time.time() < start:
    pass
print(check_cache.claim_notice(check_cache.UPDATE_FILE, "2.0.0", 86400.0))
"""

class TestNoticeOnce:
    """Several dg processes finishing together show the notice once."""

    @staticmethod
    def _state(cache_dir, **extra):
        assert check_cache.usable()
        check_cache.write(check_cache.UPDATE_FILE, ok=True, latest="2.0.0", source="x")
        state = check_cache.read(check_cache.UPDATE_FILE)
        state.update(extra)
        check_cache._save(check_cache.UPDATE_FILE, state)
        return cache_dir

    def test_concurrent_processes_claim_once(self, check_allowed):
        import subprocess

        self._state(check_allowed)
        start = time.time() + 3.0
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", _CLAIM, str(check_allowed), str(start)],
                stdout=subprocess.PIPE,
                text=True,
            )
            for _ in range(6)
        ]
        results = [proc.communicate(timeout=60)[0].strip() for proc in procs]
        assert results.count("True") == 1, results
        assert results.count("False") == 5, results
        state = check_cache.read(check_cache.UPDATE_FILE)
        assert state["notified_latest"] == "2.0.0"
        assert not list(check_allowed.glob(".*.lock"))

    def test_lock_taken_shows_nothing(self, check_allowed):
        self._state(check_allowed)
        lock = check_allowed / f".{check_cache.UPDATE_FILE}.lock"
        lock.write_text("")
        assert check_cache.claim_notice(check_cache.UPDATE_FILE, "2.0.0", 86400.0) is False
        assert lock.exists()  # a fresh lock belongs to a live process
        os.utime(lock, (1, 1))
        assert check_cache.claim_notice(check_cache.UPDATE_FILE, "2.0.0", 86400.0) is False
        assert not lock.exists()  # a stale one is cleared for the next run
        assert check_cache.claim_notice(check_cache.UPDATE_FILE, "2.0.0", 86400.0) is True

    def test_a_check_finishing_later_keeps_the_record(self, check_allowed):
        """A second process's fresh check that lands after the first showed
        the notice doesn't make it show again."""
        self._state(check_allowed)
        assert check_cache.claim_notice(check_cache.UPDATE_FILE, "2.0.0", 86400.0)
        check_cache.write(check_cache.UPDATE_FILE, ok=False, latest=None, source="x")
        check_cache.write(check_cache.UPDATE_FILE, ok=True, latest="2.0.0", source="x")
        mod._result = {"latest": "2.0.0", "current": "1.0.0"}
        mod._thread = _finished_thread()
        err = io.StringIO()
        with patch.object(sys, "stderr", err):
            print_pending_notification()
        assert err.getvalue() == ""

    def test_a_claim_during_a_check_write_is_kept(self, check_allowed, monkeypatch):
        """A claim made while another process's check is between reading
        the state and saving it isn't erased by that save."""
        self._state(check_allowed)
        real_read, real_save = check_cache.read, check_cache._save
        read_done, claim_done = threading.Event(), threading.Event()

        def writer() -> bool:
            return threading.current_thread() is checker

        def read(name):
            state = real_read(name)
            if writer():
                read_done.set()
            return state

        def save(name, state):
            if writer():
                claim_done.wait(5.0)
            real_save(name, state)

        monkeypatch.setattr(check_cache, "read", read)
        monkeypatch.setattr(check_cache, "_save", save)
        checker = threading.Thread(
            target=check_cache.write,
            args=(check_cache.UPDATE_FILE,),
            kwargs={"ok": True, "latest": "2.0.0", "source": "x"},
        )
        checker.start()
        assert read_done.wait(5.0)
        first = check_cache.claim_notice(check_cache.UPDATE_FILE, "2.0.0", 86400.0)
        claim_done.set()
        checker.join(5.0)
        second = check_cache.claim_notice(check_cache.UPDATE_FILE, "2.0.0", 86400.0)
        assert [first, second].count(True) == 1, (first, second)
        assert not list(check_allowed.glob(".*.lock"))

    def test_a_lock_that_cannot_be_made_still_shows(self, check_allowed, monkeypatch):
        """No lock can be created (read-only mount, full disk): the notice
        shows rather than never."""
        self._state(check_allowed)
        real_open = os.open

        def no_lock(path, *args, **kwargs):
            if str(path).endswith(".lock"):
                raise PermissionError(13, "Permission denied")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(check_cache.os, "open", no_lock)
        claimed = check_cache.claim_notice(check_cache.UPDATE_FILE, "2.0.0", 86400.0)
        assert claimed is True

    def test_a_newer_version_shows_again(self, check_allowed):
        self._state(check_allowed, notified_at=time.time(), notified_latest="1.5.0")
        mod._result = {"latest": "2.0.0", "current": "1.0.0"}
        mod._thread = _finished_thread()
        err = io.StringIO()
        with patch.object(sys, "stderr", err):
            print_pending_notification()
        assert err.getvalue().count("Update available") == 1
