"""Unit tests for startup update check."""

import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from deepctl_cmd_update.startup_check import (
    _is_ci,
    _is_oneshot,
    _read_cache_timestamp,
    _write_cache_timestamp,
    check_and_notify,
    print_pending_notification,
)


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

    @patch.dict(
        "os.environ",
        {"UV_INTERNAL__PARENT_INTERPRETER": ""},
        clear=False,
    )
    def test_not_oneshot(self):
        import os
        os.environ.pop("UV_INTERNAL__PARENT_INTERPRETER", None)
        assert _is_oneshot() is False

    @patch.dict(
        "os.environ",
        {"UV_INTERNAL__PARENT_INTERPRETER": "/usr/bin/python3"},
        clear=True,
    )
    def test_uvx_oneshot(self):
        assert _is_oneshot() is True


class TestCacheTimestamp:
    """Test file-based timestamp caching."""

    def test_read_missing_cache(self, tmp_path):
        """Missing cache file returns 0.0."""
        with patch(
            "deepctl_cmd_update.startup_check._CACHE_FILE",
            tmp_path / "nonexistent",
        ):
            assert _read_cache_timestamp() == 0.0

    def test_write_and_read_cache(self, tmp_path):
        """Written timestamp can be read back."""
        cache_file = tmp_path / "last_version_check"
        with patch(
            "deepctl_cmd_update.startup_check._CACHE_FILE", cache_file
        ), patch(
            "deepctl_cmd_update.startup_check._CACHE_DIR", tmp_path
        ):
            _write_cache_timestamp()
            ts = _read_cache_timestamp()
            assert ts > 0.0
            assert abs(ts - time.time()) < 5.0


class TestCheckAndNotify:
    """Test the startup check lifecycle."""

    @patch.dict("os.environ", {"CI": "true"}, clear=False)
    def test_suppressed_in_ci(self):
        """No thread is started in CI."""
        import deepctl_cmd_update.startup_check as mod

        mod._thread = None
        mod._result = {}
        check_and_notify(current_version="1.0.0")
        assert mod._thread is None

    @patch(
        "deepctl_cmd_update.startup_check._is_ci", return_value=False
    )
    @patch(
        "deepctl_cmd_update.startup_check._is_oneshot",
        return_value=False,
    )
    @patch(
        "deepctl_cmd_update.startup_check._get_check_interval_seconds",
        return_value=None,
    )
    def test_suppressed_when_never(self, *_mocks):
        """No thread is started when check_frequency=never."""
        import deepctl_cmd_update.startup_check as mod

        mod._thread = None
        mod._result = {}
        check_and_notify(current_version="1.0.0")
        assert mod._thread is None

    def test_suppressed_in_quiet_mode(self):
        """No thread is started in quiet mode."""
        import deepctl_cmd_update.startup_check as mod

        mod._thread = None
        mod._result = {}
        check_and_notify(current_version="1.0.0", quiet=True)
        assert mod._thread is None

    @patch.dict("os.environ", {}, clear=False)
    @patch(
        "deepctl_cmd_update.startup_check._is_ci", return_value=False
    )
    @patch(
        "deepctl_cmd_update.startup_check._is_oneshot",
        return_value=False,
    )
    @patch(
        "deepctl_cmd_update.startup_check._get_check_interval_seconds",
        return_value=86400.0,
    )
    @patch(
        "deepctl_cmd_update.startup_check._read_cache_timestamp",
        return_value=0.0,
    )
    @patch(
        "deepctl_cmd_update.startup_check._background_check",
    )
    def test_thread_starts(
        self,
        mock_bg_check,
        *_mocks,
    ):
        """A daemon thread is started when conditions are met."""
        import deepctl_cmd_update.startup_check as mod

        mod._thread = None
        mod._result = {}

        # Make _background_check a no-op
        mock_bg_check.side_effect = lambda ver, res: None

        check_and_notify(current_version="1.0.0")
        assert mod._thread is not None
        assert mod._thread.daemon is True
        mod._thread.join(timeout=2.0)


class TestPrintPendingNotification:
    """Test notification display."""

    def test_no_notification_when_no_thread(self, capsys):
        """No output when no thread was started."""
        import deepctl_cmd_update.startup_check as mod

        mod._thread = None
        mod._result = {}
        print_pending_notification()
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_notification_displayed(self):
        """Notification is printed to stderr when update found."""
        import deepctl_cmd_update.startup_check as mod

        # Simulate completed thread with result
        mod._result = {"latest": "2.0.0", "current": "1.0.0"}
        mod._thread = threading.Thread(target=lambda: None)
        mod._thread.start()
        mod._thread.join()

        # Capture stderr
        import io

        stderr_capture = io.StringIO()
        with patch.object(sys, "stderr", stderr_capture):
            print_pending_notification()

        output = stderr_capture.getvalue()
        assert "2.0.0" in output
        assert "1.0.0" in output
        assert "deepctl update" in output

    def test_no_notification_when_up_to_date(self, capsys):
        """No output when no update is available."""
        import deepctl_cmd_update.startup_check as mod

        mod._result = {}  # No update found
        mod._thread = threading.Thread(target=lambda: None)
        mod._thread.start()
        mod._thread.join()

        print_pending_notification()
        captured = capsys.readouterr()
        assert captured.err == ""
