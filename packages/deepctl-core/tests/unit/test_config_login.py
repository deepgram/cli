"""Config details login relies on: the refusal carries its cause, and the
legacy-config migration never writes to stdout."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from deepctl_core import config as config_module
from deepctl_core.config import Config, ConfigSaveRefused


def test_save_refusal_carries_its_cause(tmp_path):
    exc = config_module._save_refused("writable", tmp_path / "c.yaml")
    assert isinstance(exc, ConfigSaveRefused)
    assert exc.why == "writable"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX homes")
def test_legacy_migration_prints_to_stderr_only(tmp_path, monkeypatch, capsys):
    import platformdirs

    from deepctl_core import config as config_module

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr("deepctl_core.output.is_ai_agent", lambda: False)
    monkeypatch.chdir(tmp_path)
    # The OS's own locations (~/.config on Linux, Application Support on
    # macOS), under this HOME.
    old = Path(platformdirs.user_config_dir("deepgram", "deepgram"))
    new = Path(platformdirs.user_config_dir("deepctl", "deepgram"))
    assert home in old.parents and home in new.parents
    old.mkdir(parents=True)
    (old / "config.yaml").write_text("output:\n  format: json\n")
    # Nothing an earlier test queued may count here.
    config_module.flush_config_notices()
    capsys.readouterr()

    Config()
    config_module.flush_config_notices()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("Copied the config from") == 1
    assert os.path.exists(new / "config.yaml")
