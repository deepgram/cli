"""Plain-text diagnostic output shared by utilities outside deepctl-core."""

import sys
from typing import TextIO

from rich.console import Console


def is_non_interactive() -> bool:
    """Agent or automation, as ``deepctl_core.output.is_agentic`` decides.

    ``deepctl_core.agents`` is stdlib only, so importing it here costs
    nothing that core's console doesn't already pay.
    """
    from deepctl_core.agents import is_agentic

    return is_agentic()


def create_diagnostic_console(
    *, file: TextIO | None = None, force_terminal: bool | None = None
) -> Console:
    """Create a stderr diagnostic console with the shared plain-text policy."""
    from deepctl_core.agents import no_color

    non_interactive = is_non_interactive()
    # Plain text for agents and under NO_COLOR (rich alone keeps bold and
    # dim); never no_color=False, so rich honors FORCE_COLOR and TERM.
    plain = non_interactive or no_color()
    return Console(
        file=file or sys.stderr,
        force_terminal=force_terminal,
        no_color=True if plain else None,
        color_system=None if plain else "auto",
        highlight=not non_interactive,
    )
