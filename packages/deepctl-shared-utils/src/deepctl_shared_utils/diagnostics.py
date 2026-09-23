"""Plain-text diagnostic output shared by utilities outside deepctl-core."""

import os
import sys
from typing import TextIO

from rich.console import Console


def is_non_interactive() -> bool:
    """Mirror deepctl-core's agent and non-interactive output policy.

    Shared utilities intentionally do not depend on deepctl-core, so keep this
    lightweight detection aligned with ``deepctl_core.output.is_agentic``.
    """
    env = os.environ

    if "--non-interactive" in sys.argv or "--agent-friendly" in sys.argv:
        return True
    if env.get("CI") in ("true", "1"):
        return True
    if env.get("CLAUDECODE") or env.get("CLAUDE_CODE_ENTRYPOINT"):
        return True
    if env.get("CODEX_SANDBOX") or env.get("CODEX_SANDBOX_NETWORK_DISABLED"):
        return True
    if env.get("OR_APP_NAME") == "Aider" or "aider" in env.get("OR_SITE_URL", ""):
        return True

    score = 0
    if not sys.stdin.isatty():
        score += 1
    if not sys.stdout.isatty():
        score += 1
    if not env.get("TERM") or env.get("TERM") == "dumb":
        score += 1
    if "NO_COLOR" in env:
        score += 1

    return score >= 3


def create_diagnostic_console(
    *, file: TextIO | None = None, force_terminal: bool | None = None
) -> Console:
    """Create a stderr diagnostic console with the shared plain-text policy."""
    non_interactive = is_non_interactive()
    return Console(
        file=file or sys.stderr,
        force_terminal=force_terminal,
        no_color=non_interactive,
        highlight=not non_interactive,
    )
