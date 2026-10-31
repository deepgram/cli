"""Subtle telemetry notice appended to `--help` output."""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from collections.abc import Callable

    from deepctl_core import Config


# Golden text, compared byte for byte with tests/golden/notice_*.txt.
# NOTICE_ON shows only with a loopback DEEPCTL_TELEMETRY_DSN (tests and local
# sinks). It names the three opt-outs that exist: the flag for one run, the env
# var, and the config-file key.
NOTICE_ON = (
    "Telemetry is on: unexpected errors only, with no flag values, paths or "
    "keys. Turn off: --no-telemetry, DEEPCTL_TELEMETRY_DISABLED=1, or "
    "telemetry.enabled: false in config.yaml."
)
NOTICE_OFF = "Telemetry is off."
# No destination (no loopback DEEPCTL_TELEMETRY_DSN), or the build switch is
# off: what every user of this version sees.
NOTICE_NONE = "This version of dg sends no telemetry."

_installed = False
# The latest fallback passed to install_help_notice (one per main() call).
_fallback: list[Callable[[], Config]] = []


def render_notice(config: Config) -> str:
    """Return the one-line notice for the current telemetry state.

    Dim only on a terminal with NO_COLOR unset; `click.style` doesn't check
    either on its own.
    """
    from .client import _opt_out_source

    source = _opt_out_source(config)
    if source is None:
        text = NOTICE_ON
    elif source in ("build", "dsn"):
        text = NOTICE_NONE
    else:
        text = NOTICE_OFF
    if os.environ.get("NO_COLOR") or not _stdout_is_tty():
        return text
    return click.style(text, dim=True)


def _stdout_is_tty() -> bool:
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def _config_for_help(
    ctx: click.Context, fallback_config: Callable[[], Config]
) -> Config:
    """The Config this run resolved, or the fallback for root-level help.

    Subcommand help runs after the root callback has built the Config from
    `--config`/`--profile`, so it is on the root context. Root-level
    `--help` fires during option parsing, before that, so it uses the
    fallback.
    """
    obj = ctx.find_root().obj
    if isinstance(obj, dict) and obj.get("config") is not None:
        config: Config = obj["config"]
        return config
    try:
        return fallback_config()
    except Exception:
        from deepctl_core import Config

        return Config()


def install_help_notice(fallback_config: Callable[[], Config]) -> None:
    """Monkey-patch Click so every `--help` ends with the telemetry notice.

    Click formats help via `Command.get_help(ctx)`. We wrap that method
    once at startup so every command (including subcommands and plugin
    commands) picks up the footer without needing per-command wiring. The
    notice is rendered when help is shown, so it reflects `--config` and
    `--no-telemetry`.
    """
    global _installed
    _fallback[:] = [fallback_config]
    if _installed:
        return

    original_get_help = click.Command.get_help

    def get_help_with_notice(self: click.Command, ctx: click.Context) -> str:
        try:
            notice = render_notice(_config_for_help(ctx, _fallback[0]))
        except OSError:
            # No readable config (an unreadable HOME): telemetry was never
            # turned on for this run, so it is off. Help still prints.
            notice = NOTICE_OFF
            if not os.environ.get("NO_COLOR") and _stdout_is_tty():
                notice = click.style(notice, dim=True)
        return original_get_help(self, ctx) + "\n\n" + notice

    click.Command.get_help = get_help_with_notice  # type: ignore[method-assign]
    _installed = True
