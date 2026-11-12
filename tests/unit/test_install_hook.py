"""The install-script hook reports a failed config write with exit 1."""

from __future__ import annotations

import importlib

from unittest.mock import MagicMock, patch

import pytest


def test_record_install_method_exits_1_when_save_fails():
    # Imported here so the CLI module is already set up by the other tests.
    main_module = importlib.import_module("deepctl.main")

    ctx = MagicMock(resilient_parsing=False)
    with (
        patch.object(main_module, "Config") as config_cls,
        pytest.raises(SystemExit) as exc,
    ):
        config_cls.return_value.save.side_effect = OSError(30, "Read-only file system")
        main_module._record_install_method_cb(ctx, MagicMock(), "pip")
    assert exc.value.code == 1
    ctx.exit.assert_not_called()


def test_record_install_method_success_exits_via_ctx():
    main_module = importlib.import_module("deepctl.main")

    ctx = MagicMock(resilient_parsing=False)
    with patch.object(main_module, "Config"):
        main_module._record_install_method_cb(ctx, MagicMock(), "pip")
    ctx.exit.assert_called_once_with()
