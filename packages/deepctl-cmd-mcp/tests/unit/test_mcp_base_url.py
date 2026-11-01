"""`dg mcp --base-url` sends the key only to the Deepgram developer API or a
host the user approved in their config; refusals go to stderr only."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest
from deepctl_core import hosts, output

from deepctl_cmd_mcp import __main__ as mcp_main
from deepctl_cmd_mcp.command import McpCommand


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(hosts, "_reported", set())
    monkeypatch.setattr(output, "is_agentic", lambda: False)
    monkeypatch.setattr(output, "is_ai_agent", lambda: False)
    monkeypatch.setattr(output.stderr_console, "_width", 400)


def run(base_url, allowed=()):
    config = MagicMock()
    config.allowed_hosts.return_value = list(allowed)
    config.config_path = "/x/config.yaml"
    auth_manager = MagicMock()
    auth_manager.get_api_key.return_value = "sk-USERKEY"
    with (
        patch("deepctl_cmd_mcp.command.asyncio.run") as run_proxy,
        patch("signal.signal"),
    ):
        result = McpCommand().handle(
            config, auth_manager, MagicMock(), transport="stdio", base_url=base_url
        )
    return result, run_proxy, auth_manager


def test_foreign_base_url_refused_on_stderr_only(capsys):
    with pytest.raises(SystemExit) as exc:
        run("https://evil.example")
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Not sending your API key to evil.example" in captured.err


def test_refused_before_reading_the_key():
    config = MagicMock()
    config.allowed_hosts.return_value = []
    auth_manager = MagicMock()
    with patch("signal.signal"), pytest.raises(SystemExit):
        McpCommand().handle(
            config, auth_manager, MagicMock(), base_url="https://evil.example"
        )
    auth_manager.get_api_key.assert_not_called()


def test_default_base_url_allowed():
    result, run_proxy, _ = run("https://api.dx.deepgram.com")
    assert result is None
    assert run_proxy.call_count == 1


def test_allowlisted_base_url_allowed():
    _, run_proxy, _ = run("https://dx.staging.example", allowed=["dx.staging.example"])
    assert run_proxy.call_count == 1


def test_stdout_is_protocol():
    assert McpCommand.stdout_is_protocol is True


def test_standalone_entrypoint_refuses_foreign_flag(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "sk-USERKEY")
    monkeypatch.setattr(
        sys, "argv", ["deepctl-cmd-mcp", "--base-url", "https://evil.example"]
    )
    monkeypatch.setattr(
        "deepctl_core.config.platformdirs.user_config_dir",
        lambda *a: str(tmp_path / "cfg"),
    )
    with (
        patch("signal.signal"),
        patch("deepctl_cmd_mcp.proxy.run_proxy") as run_proxy,
        pytest.raises(SystemExit) as exc,
    ):
        mcp_main.main()
    assert exc.value.code == 1
    run_proxy.assert_not_called()
    assert "evil.example" in capsys.readouterr().err


def test_standalone_entrypoint_agent_env_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(output, "is_agentic", lambda: True)
    monkeypatch.setattr(output, "is_ai_agent", lambda: True)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "sk-USERKEY")
    monkeypatch.setenv("DEEPGRAM_DX_URL", "https://evil.example")
    monkeypatch.setattr(sys, "argv", ["deepctl-cmd-mcp"])
    monkeypatch.setattr(
        "deepctl_core.config.platformdirs.user_config_dir",
        lambda *a: str(tmp_path / "cfg"),
    )
    with (
        patch("signal.signal"),
        patch("deepctl_cmd_mcp.proxy.run_proxy") as run_proxy,
        pytest.raises(SystemExit),
    ):
        mcp_main.main()
    run_proxy.assert_not_called()


def test_standalone_entrypoint_rejects_abbreviated_flag(monkeypatch, capsys):
    """--base is not --base-url: no argparse abbreviations."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "sk-USERKEY")
    monkeypatch.setattr(sys, "argv", ["deepctl-cmd-mcp", "--base=https://evil.test"])
    with (
        patch("signal.signal"),
        patch("deepctl_cmd_mcp.proxy.run_proxy") as run_proxy,
        pytest.raises(SystemExit) as exc,
    ):
        mcp_main.main()
    assert exc.value.code == 2
    run_proxy.assert_not_called()


def test_standalone_entrypoint_default_host_reads_no_config(monkeypatch, tmp_path):
    """The default developer API needs no config read (stdout stays clean)."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "sk-USERKEY")
    monkeypatch.delenv("DEEPGRAM_DX_URL", raising=False)
    monkeypatch.setattr(sys, "argv", ["deepctl-cmd-mcp"])
    with (
        patch("signal.signal"),
        patch("deepctl_core.Config", side_effect=AssertionError("no config")),
        patch("deepctl_cmd_mcp.proxy.run_proxy") as run_proxy,
        patch("asyncio.run") as run,
    ):
        mcp_main.main()
    run.assert_called_once()
    assert run_proxy.call_args.kwargs["base_url"] == "https://api.dx.deepgram.com"
    assert run_proxy.call_args.kwargs["api_key"]() == "sk-USERKEY"


def test_standalone_entrypoint_env_host_warns(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "sk-USERKEY")
    monkeypatch.setenv("DEEPGRAM_DX_URL", "https://dx.proxy.example")
    monkeypatch.setattr(sys, "argv", ["deepctl-cmd-mcp"])
    monkeypatch.setattr(
        "deepctl_core.config.platformdirs.user_config_dir",
        lambda *a: str(tmp_path / "cfg"),
    )
    with (
        patch("signal.signal"),
        patch("deepctl_cmd_mcp.proxy.run_proxy"),
        patch("asyncio.run") as run,
    ):
        mcp_main.main()
    run.assert_called_once()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "(from DEEPGRAM_DX_URL)" in captured.err


def test_proxy_gets_the_normalized_url():
    _, run_proxy, _ = run("HTTPS://API.DX.DEEPGRAM.COM/")
    assert run_proxy.call_count == 1


def test_proxy_url_is_canonical(monkeypatch):
    from deepctl_cmd_mcp import command as mcp_module

    seen = {}

    def fake_run_proxy(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(mcp_module, "run_proxy", fake_run_proxy)
    config = MagicMock()
    config.allowed_hosts.return_value = ["dx.proxy.example"]
    with (
        patch("deepctl_cmd_mcp.command.asyncio.run", lambda coro: None),
        patch("signal.signal"),
    ):
        McpCommand().handle(
            config, MagicMock(), MagicMock(), base_url="HTTPS://DX.Proxy.Example/"
        )
    assert seen["base_url"] == "https://dx.proxy.example"


def test_proxy_url_keeps_its_path(monkeypatch):
    from deepctl_cmd_mcp import command as mcp_module

    seen = {}
    monkeypatch.setattr(mcp_module, "run_proxy", lambda **kw: seen.update(kw))
    config = MagicMock()
    config.allowed_hosts.return_value = ["dx.proxy.example"]
    with (
        patch("deepctl_cmd_mcp.command.asyncio.run", lambda coro: None),
        patch("signal.signal"),
    ):
        McpCommand().handle(
            config, MagicMock(), MagicMock(), base_url="https://dx.proxy.example/mcp/"
        )
    assert seen["base_url"] == "https://dx.proxy.example/mcp"


@pytest.mark.parametrize(
    ("transport", "shown"),
    [("sse", "sse"), ("SSE", "sse"), ("streamable-http", "streamable-http")],
)
def test_standalone_entrypoint_refuses_other_transports(
    monkeypatch, capsys, transport, shown
):
    """`python -m deepctl_cmd_mcp --transport X` reports E_MCP_TRANSPORT, not
    an argparse error, for every transport an old config may name."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "sk-USERKEY")
    monkeypatch.setattr(sys, "argv", ["deepctl-cmd-mcp", "--transport", transport])
    with (
        patch("deepctl_cmd_mcp.proxy.run_proxy") as run_proxy,
        patch("signal.signal"),
        pytest.raises(SystemExit) as exc,
    ):
        mcp_main.main()
    assert exc.value.code == 1
    run_proxy.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"supports only the stdio transport, not {shown}." in captured.err
    assert "Use stdio" in captured.err
