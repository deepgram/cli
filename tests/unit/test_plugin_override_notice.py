"""The plugin override notice respects --quiet when run through main()."""

import sys
from typing import ClassVar
from unittest.mock import Mock, patch

import pytest
from deepctl_core import BaseCommand, PluginManager, output


class _PluginLogin(BaseCommand):
    name = "login"
    help = "plugin login"
    requires_auth = False
    requires_project = False
    runs: ClassVar[list[int]] = []

    def handle(self, config, auth_manager, client, **kwargs):
        _PluginLogin.runs.append(1)


@pytest.fixture
def overriding_login(monkeypatch, tmp_path):
    """The real root group with a plugin 'login' that plugins.allow_override
    lets replace the built-in."""
    import deepctl.main  # loads the real commands

    main_mod = sys.modules["deepctl.main"]
    cli = main_mod.cli
    monkeypatch.setattr(cli, "commands", dict(cli.commands))
    monkeypatch.setattr(output, "_output_config", dict(output._output_config))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("DEEPCTL_TELEMETRY_DISABLED", "1")
    monkeypatch.setenv("DEEPCTL_TELEMETRY_DSN", "http://k@127.0.0.1:9/1")
    for module, names in (
        ("deepctl_cmd_update.startup_check", ("check_and_notify",)),
        ("deepctl_cmd_update.plugin_update_check", ("check_plugins_and_notify",)),
        ("deepctl_cmd_skills.startup_check", ("check_and_notify",)),
    ):
        mod = pytest.importorskip(module)
        for name in names:
            monkeypatch.setattr(mod, name, lambda **kwargs: None)
    monkeypatch.setattr(
        "deepctl_core.plugin_manager.allow_override_names",
        lambda: frozenset({"login"}),
    )
    ep = Mock()
    ep.name = "plugin-login"
    ep.value = "plugin_login_mod:Login"
    ep.load.return_value = _PluginLogin
    ep.dist = Mock()
    ep.dist.metadata = {"Name": "plugin-login-dist"}
    ep.dist.version = "1.0"
    with patch("deepctl_core.plugin_manager.metadata.entry_points") as eps:
        eps.return_value.select.return_value = [ep]
        PluginManager()._load_external_plugins(cli)
    _PluginLogin.runs.clear()
    return main_mod


@pytest.mark.parametrize(
    "argv,shown",
    [
        (["dg", "--quiet", "login"], False),
        (["dg", "-q", "login"], False),
        (["dg", "login"], True),
    ],
)
def test_override_notice_through_main(
    overriding_login, monkeypatch, capsys, argv, shown
):
    capsys.readouterr()  # nothing printed at load time is checked here
    monkeypatch.setattr(sys, "argv", argv)
    overriding_login.main()
    err = " ".join(capsys.readouterr().err.split())
    assert ("Using 'login' from plugin-login-dist 1.0" in err) is shown
    assert _PluginLogin.runs == [1]


def test_other_commands_dont_announce_the_override(
    overriding_login, monkeypatch, capsys
):
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", ["dg", "--help"])
    overriding_login.main()
    assert "Using 'login'" not in capsys.readouterr().err
