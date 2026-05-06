"""Subtle telemetry notice appended to `--help` output."""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from deepctl_core import Config


NOTICE_ON = (
    "Telemetry is on (anonymous error reports). "
    "Disable: dg config set telemetry.enabled false"
)
NOTICE_OFF = "Telemetry is off."

_installed = False


def render_notice(config: Config) -> str:
    """Return the dim one-line notice for the current telemetry state."""
    from .client import is_enabled

    text = NOTICE_ON if is_enabled(config) else NOTICE_OFF
    return click.style(text, dim=True)


def install_help_notice(config: Config) -> None:
    """Monkey-patch Click so every `--help` ends with the telemetry notice.

    Click formats help via `Command.get_help(ctx)`. We wrap that method
    once at startup so every command (including subcommands and plugin
    commands) picks up the footer without needing per-command wiring.
    """
    global _installed
    if _installed:
        return

    original_get_help = click.Command.get_help
    notice = render_notice(config)

    def get_help_with_notice(self: click.Command, ctx: click.Context) -> str:
        return original_get_help(self, ctx) + "\n\n" + notice

    click.Command.get_help = get_help_with_notice  # type: ignore[method-assign]
    _installed = True
