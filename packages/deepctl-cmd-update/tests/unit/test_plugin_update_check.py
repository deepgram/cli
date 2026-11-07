"""Unit tests for plugin update check."""

import io
import sys
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from deepctl_cmd_update.plugin_update_check import (
    _check_pypi_versions,
    _discover_community_plugins,
    _is_excluded,
    check_plugins_and_notify,
    print_pending_plugin_notifications,
)

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
def _invoked_as_dg(monkeypatch):
    """Notices name the program as invoked; these tests expect dg."""
    monkeypatch.setenv("DEEPCTL_PROG_NAME", "dg")

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

    @pytest.fixture(autouse=True)
    def _reset(self):
        import deepctl_cmd_update.plugin_update_check as mod

        mod._thread = None
        mod._result = {}
        yield
        if mod._thread is not None:
            mod._thread.join(timeout=5.0)
        mod._thread = None

    def test_suppressed_with_the_core_check(self, check_allowed, monkeypatch):
        import deepctl_cmd_update.plugin_update_check as mod
        import deepctl_cmd_update.startup_check as core

        monkeypatch.setattr(core, "_stderr_is_terminal", lambda: False)
        with patch.object(mod.threading, "Thread") as thread_cls:
            check_plugins_and_notify()
        thread_cls.assert_not_called()
        assert not check_allowed.exists()

    def test_suppressed_in_ci(self, check_allowed, monkeypatch):
        import deepctl_cmd_update.plugin_update_check as mod
        import deepctl_cmd_update.startup_check as core

        monkeypatch.setattr(core, "_is_ci", lambda: True)
        check_plugins_and_notify()
        assert mod._thread is None

    def test_suppressed_in_quiet_mode(self, check_allowed):
        import deepctl_cmd_update.plugin_update_check as mod

        check_plugins_and_notify(quiet=True)
        assert mod._thread is None

    def test_due_spawns_thread(self, check_allowed):
        """No check within the interval → thread should be spawned."""
        import deepctl_cmd_update.plugin_update_check as mod

        with patch(
            "deepctl_cmd_update.plugin_update_check._background_plugin_check",
            side_effect=lambda res: None,
        ):
            check_plugins_and_notify()
            assert mod._thread is not None
            assert mod._thread.daemon is True
            assert mod._thread.name == mod.THREAD_NAME

    def test_failure_waits_a_day(self, check_allowed):
        """A failed check is recorded; the next run makes no attempt."""
        import deepctl_cmd_update.plugin_update_check as mod
        from deepctl_cmd_update import check_cache

        seen = {}

        def unreachable(*args, **kwargs):
            seen["state"] = check_cache.read(check_cache.PLUGIN_FILE)
            raise OSError("unreachable")

        with (
            patch.object(
                mod, "_discover_community_plugins", return_value={"deepctl-x": "1"}
            ),
            patch("httpx.Client.get", side_effect=unreachable),
        ):
            check_plugins_and_notify()
            mod._thread.join(timeout=5.0)
        # Recorded before the request went out.
        assert seen["state"] is not None and seen["state"]["ok"] is False
        assert check_cache.read(check_cache.PLUGIN_FILE) is not None

        with patch.object(mod.threading, "Thread") as thread_cls:
            check_plugins_and_notify()
        thread_cls.assert_not_called()

    def test_success_is_recorded(self, check_allowed):
        import deepctl_cmd_update.plugin_update_check as mod
        from deepctl_cmd_update import check_cache

        with patch.object(mod, "_discover_community_plugins", return_value={}):
            check_plugins_and_notify()
            mod._thread.join(timeout=5.0)
        state = check_cache.read(check_cache.PLUGIN_FILE)
        assert state is not None and state["ok"] is True
        assert check_cache.read(check_cache.UPDATE_FILE) is None


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
        assert "dg plugin update deepctl-plugin-whisper" in output

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
        assert "dg plugin update <name>" in output

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

    def test_running_check_is_dropped_without_waiting(self):
        """A check still running at exit is never joined."""
        import deepctl_cmd_update.plugin_update_check as mod

        release = threading.Event()
        mod._result = {
            "updates": [{"name": "deepctl-plugin-x", "current": "1", "latest": "2"}]
        }
        thread = threading.Thread(target=release.wait, daemon=True)
        thread.start()
        mod._thread = thread
        try:
            with patch.object(thread, "join") as join:
                stderr_capture = io.StringIO()
                with patch.object(sys, "stderr", stderr_capture):
                    print_pending_plugin_notifications()
            join.assert_not_called()
            assert stderr_capture.getvalue() == ""
            assert mod._thread is None
        finally:
            release.set()
            thread.join(timeout=5.0)



class TestPluginAttemptRecording:
    @pytest.fixture(autouse=True)
    def _reset(self):
        import deepctl_cmd_update.plugin_update_check as mod

        mod._thread = None
        yield
        if mod._thread is not None:
            mod._thread.join(timeout=5.0)
        mod._thread = None

    def test_nothing_recorded_before_the_request(self, check_allowed, monkeypatch):
        """A failure before any request (here: discovery) uses up nothing."""
        import deepctl_cmd_update.plugin_update_check as mod

        with patch.object(
            mod, "_discover_community_plugins", side_effect=RuntimeError("boom")
        ):
            check_plugins_and_notify()
            mod._thread.join(timeout=5.0)
        assert not (check_allowed / "plugin-update-check.json").exists()

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX owners")
    def test_unusable_cache_dir_skips(self, check_allowed, monkeypatch):
        import os

        import deepctl_cmd_update.plugin_update_check as mod
        from deepctl_cmd_update import check_cache

        check_allowed.mkdir(parents=True, mode=0o755)
        monkeypatch.setattr(
            check_cache.os, "getuid", lambda: os.stat(check_allowed).st_uid + 1
        )
        with patch.object(mod.threading, "Thread") as thread_cls:
            check_plugins_and_notify()
        thread_cls.assert_not_called()
