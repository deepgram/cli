"""Unit tests for plugin update check."""

import io
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from deepctl_cmd_update.plugin_update_check import (
    _check_pypi_versions,
    _discover_community_plugins,
    _is_excluded,
    _read_plugin_cache_timestamp,
    _write_plugin_cache_timestamp,
    check_plugins_and_notify,
    print_pending_plugin_notifications,
)

# ---------------------------------------------------------------------------
# _is_excluded
# ---------------------------------------------------------------------------


class TestIsExcluded:
    """Test first-party package exclusion logic."""

    def test_builtin_cmd_excluded(self):
        assert _is_excluded("deepctl-cmd-login") is True

    def test_core_excluded(self):
        assert _is_excluded("deepctl-core") is True

    def test_shared_utils_excluded(self):
        assert _is_excluded("deepctl-shared-utils") is True

    def test_root_package_excluded(self):
        assert _is_excluded("deepctl") is True

    def test_example_plugin_excluded(self):
        assert _is_excluded("deepctl-plugin-example") is True

    def test_community_plugin_not_excluded(self):
        assert _is_excluded("deepctl-plugin-whisper") is False

    def test_arbitrary_community_plugin_not_excluded(self):
        assert _is_excluded("my-deepctl-addon") is False


# ---------------------------------------------------------------------------
# Cache timestamp
# ---------------------------------------------------------------------------


class TestPluginCacheTimestamp:
    """Test file-based plugin cache timestamp."""

    def test_read_missing_returns_zero(self, tmp_path):
        with patch(
            "deepctl_cmd_update.plugin_update_check._PLUGIN_CACHE_FILE",
            tmp_path / "nonexistent",
        ):
            assert _read_plugin_cache_timestamp() == 0.0

    def test_write_then_read_roundtrip(self, tmp_path):
        cache_file = tmp_path / "last_plugin_version_check"
        with (
            patch(
                "deepctl_cmd_update.plugin_update_check._PLUGIN_CACHE_FILE",
                cache_file,
            ),
            patch(
                "deepctl_cmd_update.plugin_update_check._CACHE_DIR",
                tmp_path,
            ),
        ):
            _write_plugin_cache_timestamp()
            ts = _read_plugin_cache_timestamp()
            assert ts > 0.0
            assert abs(ts - time.time()) < 5.0


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _make_dist(name, version, has_plugin_ep=False):
    """Build a minimal mock distribution object."""
    metadata = {"Name": name, "Version": version}
    eps = []
    if has_plugin_ep:
        ep = SimpleNamespace(group="deepctl.plugins", name="dummy", value="mod:obj")
        eps.append(ep)
    dist = MagicMock()
    dist.metadata.__getitem__ = lambda self, key: metadata[key]
    dist.metadata.get_all = MagicMock(return_value=[])
    dist.entry_points = eps
    return dist


class TestDiscoverCommunityPlugins:
    """Test community plugin discovery."""

    @patch("deepctl_cmd_update.plugin_update_check.importlib.metadata.distributions")
    def test_filters_excluded_packages(self, mock_dists):
        mock_dists.return_value = [
            _make_dist("deepctl-cmd-login", "1.0.0", has_plugin_ep=True),
            _make_dist("deepctl-core", "1.0.0", has_plugin_ep=True),
            _make_dist("deepctl-plugin-whisper", "0.1.0", has_plugin_ep=True),
        ]
        with patch(
            "deepctl_core.plugin_env.get_plugin_state",
            return_value={"plugins": {}},
        ):
            result = _discover_community_plugins()
        assert "deepctl-plugin-whisper" in result
        assert "deepctl-cmd-login" not in result
        assert "deepctl-core" not in result

    @patch("deepctl_cmd_update.plugin_update_check.importlib.metadata.distributions")
    def test_merges_plugins_json(self, mock_dists):
        """plugins.json entries are included alongside entry-point plugins."""
        mock_dists.return_value = [
            _make_dist("deepctl-plugin-whisper", "0.1.0", has_plugin_ep=True),
        ]
        mock_state = {
            "plugins": {
                "deepctl-plugin-asr": {"version": "0.2.0"},
            }
        }
        with patch(
            "deepctl_core.plugin_env.get_plugin_state",
            return_value=mock_state,
        ):
            result = _discover_community_plugins()
        assert result == {
            "deepctl-plugin-whisper": "0.1.0",
            "deepctl-plugin-asr": "0.2.0",
        }

    @patch("deepctl_cmd_update.plugin_update_check.importlib.metadata.distributions")
    def test_deduplicates_across_sources(self, mock_dists):
        """Entry-point version wins when plugin appears in both sources."""
        mock_dists.return_value = [
            _make_dist("deepctl-plugin-whisper", "0.3.0", has_plugin_ep=True),
        ]
        mock_state = {
            "plugins": {
                "deepctl-plugin-whisper": {"version": "0.1.0"},
            }
        }
        with patch(
            "deepctl_core.plugin_env.get_plugin_state",
            return_value=mock_state,
        ):
            result = _discover_community_plugins()
        # Entry-point version (0.3.0) takes precedence
        assert result == {"deepctl-plugin-whisper": "0.3.0"}

    @patch("deepctl_cmd_update.plugin_update_check.importlib.metadata.distributions")
    def test_no_plugins_returns_empty(self, mock_dists):
        mock_dists.return_value = []
        with patch(
            "deepctl_core.plugin_env.get_plugin_state",
            return_value={"plugins": {}},
        ):
            result = _discover_community_plugins()
        assert result == {}


# ---------------------------------------------------------------------------
# PyPI check
# ---------------------------------------------------------------------------


class TestCheckPypiVersions:
    """Test PyPI version checking."""

    @patch("httpx.Client")
    def test_finds_update(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"info": {"version": "0.2.0"}}
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = _check_pypi_versions({"deepctl-plugin-whisper": "0.1.0"})
        assert len(result) == 1
        assert result[0]["name"] == "deepctl-plugin-whisper"
        assert result[0]["current"] == "0.1.0"
        assert result[0]["latest"] == "0.2.0"

    @patch("httpx.Client")
    def test_no_update_when_current(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"info": {"version": "0.1.0"}}
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = _check_pypi_versions({"deepctl-plugin-whisper": "0.1.0"})
        assert result == []

    @patch("httpx.Client")
    def test_handles_per_plugin_failure(self, mock_client_cls):
        """A failing plugin doesn't prevent others from being checked."""
        call_count = 0

        def side_effect(url):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("network error")
            resp = MagicMock()
            resp.json.return_value = {"info": {"version": "0.5.0"}}
            resp.raise_for_status = MagicMock()
            return resp

        mock_client = MagicMock()
        mock_client.get.side_effect = side_effect
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        plugins = {
            "deepctl-plugin-failing": "0.1.0",
            "deepctl-plugin-ok": "0.1.0",
        }
        result = _check_pypi_versions(plugins)
        assert len(result) == 1
        assert result[0]["name"] == "deepctl-plugin-ok"

    def test_empty_plugins_returns_empty(self):
        result = _check_pypi_versions({})
        assert result == []


# ---------------------------------------------------------------------------
# check_plugins_and_notify
# ---------------------------------------------------------------------------


class TestCheckPluginsAndNotify:
    """Test the plugin check lifecycle."""

    @patch.dict("os.environ", {"CI": "true"}, clear=False)
    def test_suppressed_in_ci(self):
        import deepctl_cmd_update.plugin_update_check as mod

        mod._thread = None
        mod._result = {}
        check_plugins_and_notify()
        assert mod._thread is None

    def test_suppressed_in_quiet_mode(self):
        import deepctl_cmd_update.plugin_update_check as mod

        mod._thread = None
        mod._result = {}
        check_plugins_and_notify(quiet=True)
        assert mod._thread is None

    @patch("deepctl_cmd_update.plugin_update_check._is_ci", return_value=False)
    @patch(
        "deepctl_cmd_update.plugin_update_check._is_oneshot",
        return_value=False,
    )
    @patch(
        "deepctl_cmd_update.plugin_update_check._get_check_interval_seconds",
        return_value=86400.0,
    )
    @patch(
        "deepctl_cmd_update.plugin_update_check._read_plugin_cache_timestamp",
        return_value=0.0,
    )
    def test_fresh_cache_spawns_thread(self, *_mocks):
        """Cache expired → thread should be spawned."""
        import deepctl_cmd_update.plugin_update_check as mod

        mod._thread = None
        mod._result = {}

        with patch(
            "deepctl_cmd_update.plugin_update_check._background_plugin_check",
            side_effect=lambda res: None,
        ):
            check_plugins_and_notify()
            assert mod._thread is not None
            assert mod._thread.daemon is True
            mod._thread.join(timeout=2.0)

    @patch("deepctl_cmd_update.plugin_update_check._is_ci", return_value=False)
    @patch(
        "deepctl_cmd_update.plugin_update_check._is_oneshot",
        return_value=False,
    )
    @patch(
        "deepctl_cmd_update.plugin_update_check._get_check_interval_seconds",
        return_value=86400.0,
    )
    def test_fresh_cache_suppresses_thread(self, *_mocks):
        """Cache still fresh → no thread should be spawned."""
        import deepctl_cmd_update.plugin_update_check as mod

        mod._thread = None
        mod._result = {}

        with patch(
            "deepctl_cmd_update.plugin_update_check._read_plugin_cache_timestamp",
            return_value=time.time(),  # just checked
        ):
            check_plugins_and_notify()
            assert mod._thread is None


# ---------------------------------------------------------------------------
# print_pending_plugin_notifications
# ---------------------------------------------------------------------------


class TestPrintPendingPluginNotifications:
    """Test notification display."""

    def test_no_thread_no_output(self, capsys):
        import deepctl_cmd_update.plugin_update_check as mod

        mod._thread = None
        mod._result = {}
        print_pending_plugin_notifications()
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_single_plugin_format(self):
        import deepctl_cmd_update.plugin_update_check as mod

        mod._result = {
            "updates": [
                {
                    "name": "deepctl-plugin-whisper",
                    "current": "0.1.0",
                    "latest": "0.2.0",
                }
            ]
        }
        mod._thread = threading.Thread(target=lambda: None)
        mod._thread.start()
        mod._thread.join()

        stderr_capture = io.StringIO()
        with patch.object(sys, "stderr", stderr_capture):
            print_pending_plugin_notifications()

        output = stderr_capture.getvalue()
        assert "deepctl-plugin-whisper" in output
        assert "0.1.0" in output
        assert "0.2.0" in output
        assert "deepctl plugin update deepctl-plugin-whisper" in output

    def test_multiple_plugin_format(self):
        import deepctl_cmd_update.plugin_update_check as mod

        mod._result = {
            "updates": [
                {
                    "name": "deepctl-plugin-whisper",
                    "current": "0.1.0",
                    "latest": "0.2.0",
                },
                {"name": "deepctl-plugin-asr", "current": "1.0.0", "latest": "1.1.0"},
            ]
        }
        mod._thread = threading.Thread(target=lambda: None)
        mod._thread.start()
        mod._thread.join()

        stderr_capture = io.StringIO()
        with patch.object(sys, "stderr", stderr_capture):
            print_pending_plugin_notifications()

        output = stderr_capture.getvalue()
        assert "Plugin updates available:" in output
        assert "deepctl-plugin-whisper" in output
        assert "deepctl-plugin-asr" in output
        assert "deepctl plugin update <name>" in output

    @pytest.mark.parametrize(
        "error",
        [
            BrokenPipeError(32, "Broken pipe"),
            ValueError("I/O operation on closed file"),
        ],
    )
    def test_broken_pipe_swallowed(self, error):
        """A closed/broken stderr (e.g. `dg mcp` host disconnect) is tolerated."""
        import deepctl_cmd_update.plugin_update_check as mod

        mod._result = {
            "updates": [
                {
                    "name": "deepctl-plugin-whisper",
                    "current": "0.1.0",
                    "latest": "0.2.0",
                }
            ]
        }
        mod._thread = threading.Thread(target=lambda: None)
        mod._thread.start()
        mod._thread.join()

        broken_stderr = MagicMock()
        broken_stderr.write.side_effect = error
        with patch.object(sys, "stderr", broken_stderr):
            # Must not raise.
            print_pending_plugin_notifications()

    def test_no_updates_no_output(self, capsys):
        import deepctl_cmd_update.plugin_update_check as mod

        mod._result = {}
        mod._thread = threading.Thread(target=lambda: None)
        mod._thread.start()
        mod._thread.join()

        print_pending_plugin_notifications()
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_thread_timeout_handling(self):
        """If the thread is still running after timeout, no crash."""
        import deepctl_cmd_update.plugin_update_check as mod

        # Create a thread that would take longer than the timeout
        mod._result = {}
        mod._thread = threading.Thread(target=lambda: time.sleep(10), daemon=True)
        mod._thread.start()

        # Should not raise — just returns with no output
        with patch.object(mod._thread, "join", side_effect=lambda timeout=None: None):
            stderr_capture = io.StringIO()
            with patch.object(sys, "stderr", stderr_capture):
                print_pending_plugin_notifications()

            output = stderr_capture.getvalue()
            assert output == ""
