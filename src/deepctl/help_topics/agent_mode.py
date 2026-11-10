"""``dg help agent-mode``: how dg detects AI agents, what this process
matched, how a person turns it off, and the trust rules that stay on.

Plain text on stdout, whatever the output format: it's documentation.
Offline, and it reads no credentials.
"""

from __future__ import annotations

import os
import sys
import textwrap
from typing import TYPE_CHECKING, Any, ClassVar

from deepctl_core import agents
from deepctl_core.base_command import BaseCommand

if TYPE_CHECKING:
    from collections.abc import Mapping

    from deepctl_core import AuthManager, Config, DeepgramClient

_SIGNAL_WIDTH = 31
_CONDITION_WIDTH = 15
_HERE_WIDTH = 5

_REMEDIES = (
    ("sh, bash, zsh", "unset {name}"),
    ("fish", "set -e {name}"),
    ("PowerShell", "Remove-Item Env:{name}"),
    ("cmd", "set {name}="),
)


def _para(text: str, indent: str = "  ") -> list[str]:
    return textwrap.wrap(
        text,
        width=76,
        initial_indent=indent,
        subsequent_indent=indent,
        break_long_words=False,
        break_on_hyphens=False,
    )


def _row(signal: str, condition: str, here: str, sources: str) -> str:
    return (
        f"  {signal:<{_SIGNAL_WIDTH}} {condition:<{_CONDITION_WIDTH}} "
        f"{here:<{_HERE_WIDTH}} {sources}"
    ).rstrip()


def _status(detection: agents.Detection) -> list[str]:
    marker = detection.marker
    if detection.override == "1":
        return ["  Agent mode: on, because DG_AGENT=1."]
    if detection.override == "0":
        lines = ["  Agent mode: off, because DG_AGENT=0."]
        if marker is not None:
            lines.append(
                f"  An AI-agent signal is still set ({marker.reason}), so the "
                "config file rules below apply."
            )
        return lines
    if marker is not None:
        verb = "matched" if marker.session else "is set"
        return [f"  Agent mode: on, because {marker.reason} {verb}."]
    if detection.soft_reason is not None:
        why = {
            "CI": "CI is set",
            "no terminal": "there is no terminal",
        }.get(detection.soft_reason, f"of {detection.soft_reason}")
        return [
            "  Agent mode: off.",
            f"  JSON output and no questions, because {why}.",
        ]
    return ["  Agent mode: off. No signal matched."]


def _config_rules(prog: str, config_path: str) -> list[str]:
    """The config-file trust rules and the command that fixes the file's
    permissions. Windows has neither: dg doesn't check the owner or
    permissions there, so there is nothing to fix."""
    if sys.platform == "win32":
        return _para(
            "On Windows, dg doesn't check the file's owner or permissions, so "
            "an agent session reads and saves the user config like any other "
            "session. There is nothing to fix."
        )
    return [
        *_para(
            f"While any signal above is set, {prog} won't read the user config "
            f"({config_path}), or the API key in it, if other users can "
            "change the file or another user owns it. It won't save to that "
            "file either. DG_AGENT=0 doesn't change this. To fix the file, "
            "make sure you own it, then run this yourself, not through an "
            "agent:"
        ),
        "",
        f"    chmod 600 {config_path}",
    ]


def render(
    prog: str,
    config_path: str,
    env: Mapping[str, str] | None = None,
    argv: list[str] | None = None,
) -> str:
    """The topic's text for this process (or the given environment)."""
    detection = agents.detect(env, argv)
    env_map: Mapping[str, str] = os.environ if env is None else env
    marker = detection.marker
    name = marker.reason if marker is not None and not marker.session else "NAME"

    lines = [
        "AGENT MODE",
        "",
        *_para(
            f"{prog} runs in agent mode when an AI coding agent runs it. In "
            f"agent mode {prog} writes JSON unless you pick a format with -o, "
            "never asks questions, shows no update or setup notices, and "
            "starts each line it writes to stderr with ERROR:, WARN:, INFO:, "
            "HINT:, OK: or DEPRECATED:."
        ),
        "",
        "HOW IT DECIDES",
        "",
        "  1. DG_AGENT=1 turns agent mode on. DG_AGENT=0 turns it off.",
        "  2. Otherwise the first signal below that matches turns it on.",
        "  3. Otherwise CI=true, --non-interactive, --agent-friendly, or no",
        "     terminal at all (stdin and stdout not terminals, TERM unset or",
        "     dumb) switch to JSON output and turn questions off.",
        "  NO_COLOR only turns color off.",
        "",
        "SIGNALS",
        "",
        _row("Signal", "Condition", "Here", "Source"),
    ]
    for signal in agents.HARD_SIGNALS:
        matched = signal.match(env_map) is not None
        lines.append(
            _row(
                signal.name,
                signal.condition,
                "yes" if matched else "-",
                ", ".join(signal.sources),
            )
        )
    lines += [
        "",
        '  "set" means present and not empty.',
        "",
        "THIS PROCESS",
        "",
        *_status(detection),
        "",
        "IF YOU ARE A PERSON",
        "",
        *_para(
            "If a signal is set in your own shell by mistake, remove it from "
            "your environment, and from the shell profile, alias or wrapper "
            "that sets it:"
        ),
        "",
    ]
    lines += [
        f"    {shell:<15} {command.format(name=name)}" for shell, command in _REMEDIES
    ]
    lines += [
        "",
        *_para(
            f"For {agents.DEVIN_MARKER} or a .pi/agent entry in PATH, run with "
            "DG_AGENT=0 for human output. The config file rules below still apply."
        ),
        "",
        "CONFIG FILES IN AGENT SESSIONS",
        "",
        *_config_rules(prog, config_path),
        "",
        "SOURCES",
        "",
    ]
    width = max(len(key) for key in agents.SOURCES)
    lines += [f"  {key:<{width}}  {url}" for key, url in agents.SOURCES.items()]
    return "\n".join(lines) + "\n"


class AgentModeCommand(BaseCommand):
    """Explain agent mode."""

    name = "agent-mode"
    help = (
        "Explain agent mode: how dg detects AI agents, what this shell "
        "matched, how to turn it off, and the config trust rules"
    )
    short_help = "Explain agent mode"

    requires_auth = False
    requires_project = False
    ci_friendly = True

    examples: ClassVar[list[str]] = ["dg help agent-mode"]
    agent_help = (
        "Print, as text, the AI-agent detection table with sources, which "
        "signals this process matched, per-shell remedies, and the config "
        "trust rules in agent sessions. Offline."
    )

    def handle(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> Any:
        del config, auth_manager, client, kwargs
        from deepctl_core.hosts import user_config_display
        from deepctl_core.output import invoked_prog

        text = render(invoked_prog(), user_config_display())
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except ValueError:
            pass
        except OSError as exc:
            from deepctl_core.signals import reader_gone

            if not reader_gone(exc, sys.stdout):
                raise
        return None
