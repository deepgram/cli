"""``dg help <topic>``: longer explanations that don't fit a command's
``--help``. Each topic is a subcommand that prints text to stdout, works
offline and reads no credentials."""

from __future__ import annotations

from typing import ClassVar

from deepctl_core.base_group_command import BaseGroupCommand


class HelpCommand(BaseGroupCommand):
    """Help topics."""

    name = "help"
    help = "Explain how dg behaves, one topic at a time"
    short_help = "Help topics"

    requires_auth = False
    requires_project = False
    ci_friendly = True

    examples: ClassVar[list[str]] = ["dg help agent-mode"]
    agent_help = (
        "Longer help topics. 'agent-mode' explains how dg detects AI agents, "
        "what agent mode changes, and the config trust rules in agent sessions."
    )

    invoke_without_command = False
