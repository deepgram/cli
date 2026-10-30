"""Unit tests for deepctl_core.launcher."""

import os
from unittest.mock import patch

from deepctl_core import launcher


@patch.dict(os.environ, {"DEEPCTL_LAUNCHER": "npm"}, clear=False)
def test_capture_reads_once_and_removes_the_variable():
    assert launcher.capture_launcher() == "npm"
    assert "DEEPCTL_LAUNCHER" not in os.environ
    # A later change to the environment does not alter the captured value.
    os.environ["DEEPCTL_LAUNCHER"] = "npx"
    assert launcher.get_launcher() == "npm"
    assert os.environ["DEEPCTL_LAUNCHER"] == "npx"


@patch.dict(os.environ, {}, clear=False)
def test_unset_means_no_launcher():
    os.environ.pop("DEEPCTL_LAUNCHER", None)
    assert launcher.get_launcher() is None
    assert launcher.launched_via_npm() is False


@patch.dict(os.environ, {"DEEPCTL_LAUNCHER": ""}, clear=False)
def test_empty_value_means_no_launcher():
    assert launcher.get_launcher() is None


@patch.dict(os.environ, {"DEEPCTL_LAUNCHER": "npm"}, clear=False)
def test_npm_global_install_upgrade_command():
    assert launcher.launched_via_npm() is True
    assert launcher.npm_upgrade_command() == "npm install -g @deepgram/cli@latest"


@patch.dict(os.environ, {"DEEPCTL_LAUNCHER": "npx"}, clear=False)
def test_npx_upgrade_command():
    assert launcher.launched_via_npm() is True
    assert launcher.npm_upgrade_command() == "npx @deepgram/cli@latest"


@patch.dict(os.environ, {"DEEPCTL_LAUNCHER": "something-else"}, clear=False)
def test_unknown_launcher_is_not_npm():
    assert launcher.launched_via_npm() is False
