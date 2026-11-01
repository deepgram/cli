"""Agent detection: whether an AI agent, automation or a person runs dg.

One table decides it, and ``dg help agent-mode`` prints the same table.
Every hard signal names the public source that documents or implements it;
a variable without one is never added.

Evaluation order:

1. ``DG_AGENT=1`` turns agent mode on and ``DG_AGENT=0`` turns it off, for
   output, prompts, notices and login. Other values are ignored.
2. The hard signals in ``HARD_SIGNALS``, top to bottom. The first match
   names the agent.
3. The soft signals (``CI``, ``--non-interactive``/``--agent-friendly``, or
   no terminal): automation is likely. They change presentation and prompts
   only.

Trust decisions (which config file and API key to use, where the key may be
sent, whether to save) read the raw hard signals through
:func:`is_ai_agent`. ``DG_AGENT`` never changes them, in either direction.

Stdlib only, and nothing is cached: tests and callers that change the
environment see the change on the next call.
"""

from __future__ import annotations

import os
import re
import sys
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

OVERRIDE_VAR = "DG_AGENT"

# Public sources for the table, read 2026-10-31 and again 2026-11-01.
SOURCES: dict[str, str] = {
    "gh": "https://github.com/cli/cli/blob/trunk/internal/agents/detect.go",
    "vercel": (
        "https://github.com/vercel/vercel/blob/main/packages/detect-agent/src/index.ts"
    ),
    "vscode": (
        "https://github.com/microsoft/vscode/blob/main/"
        "src/vs/platform/chat/common/aiAgentEnv.ts"
    ),
    "goose": (
        "https://github.com/block/goose/blob/main/"
        "documentation/docs/guides/environment-variables.md"
    ),
    "codex": "https://github.com/openai/codex/blob/main/codex-rs/core/src/spawn.rs",
    "aider": "https://github.com/Aider-AI/aider/blob/main/aider/llm.py",
    "claude-agent-sdk": (
        "https://github.com/anthropics/claude-agent-sdk-python/blob/main/"
        "src/claude_agent_sdk/_internal/transport/subprocess_cli.py"
    ),
}

_AGENT_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_AGENT_VALUES = ("amp", "goose")

# A file some agent sandboxes create (POSIX only). A module attribute so
# tests can point it elsewhere.
DEVIN_MARKER = "/opt/.devin"
# The variable the pi row reads.
HARD_PATH_VAR = "PATH"


def _set(env: Mapping[str, str], name: str) -> bool:
    """Present and not empty. Whitespace counts: a marker set to " " is
    still a marker, so trust never loosens on an odd value."""
    return bool(env.get(name, ""))


def _ai_agent_id(env: Mapping[str, str]) -> str:
    value = env.get("AI_AGENT", "")
    return value if _AGENT_ID.fullmatch(value) else "unknown"


class Match(NamedTuple):
    """A hard signal that matched."""

    agent_id: str
    # The variable that matched, or a description of the check (rows that
    # are a file or a PATH entry).
    reason: str
    # True for the checks that aren't a variable someone set on purpose.
    session: bool = False


class HardSignal(NamedTuple):
    """One row of the detection table."""

    name: str
    condition: str
    sources: tuple[str, ...]
    match: Callable[[Mapping[str, str]], Match | None]


def _when_set(name: str, agent_id: str) -> Callable[[Mapping[str, str]], Match | None]:
    def match(env: Mapping[str, str]) -> Match | None:
        return Match(agent_id, name) if _set(env, name) else None

    return match


def _ai_agent(env: Mapping[str, str]) -> Match | None:
    return Match(_ai_agent_id(env), "AI_AGENT") if _set(env, "AI_AGENT") else None


def _agent_value(env: Mapping[str, str]) -> Match | None:
    value = env.get("AGENT", "")
    return Match(value, "AGENT") if value in _AGENT_VALUES else None


def _cursor_role(env: Mapping[str, str]) -> Match | None:
    if env.get("CURSOR_EXTENSION_HOST_ROLE", "") == "agent-exec":
        return Match("cursor-cli", "CURSOR_EXTENSION_HOST_ROLE")
    return None


def _aider(env: Mapping[str, str]) -> Match | None:
    if env.get("OR_APP_NAME", "") == "Aider":
        return Match("aider", "OR_APP_NAME")
    if "aider" in env.get("OR_SITE_URL", ""):
        return Match("aider", "OR_SITE_URL")
    return None


def _devin(env: Mapping[str, str]) -> Match | None:
    del env
    if os.name != "posix":
        return None
    try:
        found = os.path.lexists(DEVIN_MARKER)
    except (OSError, ValueError):
        return None
    return Match("devin", DEVIN_MARKER, session=True) if found else None


def _pi(env: Mapping[str, str]) -> Match | None:
    path = env.get(HARD_PATH_VAR, "")
    if "/.pi/agent" in path or "\\.pi\\agent" in path:
        return Match("pi", "a .pi/agent entry in PATH", session=True)
    return None


# Order matters: Amp also sets CLAUDECODE, so AGENT comes first.
HARD_SIGNALS: tuple[HardSignal, ...] = (
    HardSignal("AI_AGENT", "set", ("gh", "vercel", "vscode"), _ai_agent),
    HardSignal("AGENT", "amp or goose", ("gh", "goose"), _agent_value),
    HardSignal(
        "GOOSE_TERMINAL", "set", ("goose",), _when_set("GOOSE_TERMINAL", "goose")
    ),
    HardSignal(
        "CLAUDECODE", "set", ("gh", "vercel"), _when_set("CLAUDECODE", "claude-code")
    ),
    HardSignal(
        "CLAUDE_CODE", "set", ("gh", "vercel"), _when_set("CLAUDE_CODE", "claude-code")
    ),
    HardSignal(
        "CLAUDE_CODE_IS_COWORK",
        "set",
        ("gh",),
        _when_set("CLAUDE_CODE_IS_COWORK", "cowork"),
    ),
    HardSignal(
        "CLAUDE_CODE_ENTRYPOINT",
        "set",
        ("claude-agent-sdk",),
        _when_set("CLAUDE_CODE_ENTRYPOINT", "claude-code"),
    ),
    HardSignal(
        "CODEX_SANDBOX", "set", ("gh", "vercel"), _when_set("CODEX_SANDBOX", "codex")
    ),
    HardSignal("CODEX_CI", "set", ("gh", "vercel"), _when_set("CODEX_CI", "codex")),
    HardSignal(
        "CODEX_THREAD_ID",
        "set",
        ("gh", "vercel"),
        _when_set("CODEX_THREAD_ID", "codex"),
    ),
    HardSignal(
        "CODEX_SANDBOX_NETWORK_DISABLED",
        "set",
        ("codex",),
        _when_set("CODEX_SANDBOX_NETWORK_DISABLED", "codex"),
    ),
    HardSignal(
        "GEMINI_CLI", "set", ("gh", "vercel"), _when_set("GEMINI_CLI", "gemini-cli")
    ),
    HardSignal("COPILOT_CLI", "set", ("gh",), _when_set("COPILOT_CLI", "copilot-cli")),
    HardSignal("OPENCODE", "set", ("gh",), _when_set("OPENCODE", "opencode")),
    HardSignal(
        "OPENCODE_CLIENT", "set", ("gh",), _when_set("OPENCODE_CLIENT", "opencode")
    ),
    HardSignal(
        "ANTIGRAVITY_AGENT",
        "set",
        ("gh", "vercel"),
        _when_set("ANTIGRAVITY_AGENT", "antigravity"),
    ),
    HardSignal(
        "AUGMENT_AGENT",
        "set",
        ("gh", "vercel"),
        _when_set("AUGMENT_AGENT", "augment-cli"),
    ),
    HardSignal(
        "CURSOR_AGENT", "set", ("gh", "vercel"), _when_set("CURSOR_AGENT", "cursor-cli")
    ),
    HardSignal(
        "CURSOR_EXTENSION_HOST_ROLE", "agent-exec", ("gh", "vercel"), _cursor_role
    ),
    HardSignal(
        "OR_APP_NAME / OR_SITE_URL",
        "Aider / aider",
        ("aider",),
        _aider,
    ),
    HardSignal(DEVIN_MARKER, "exists", ("vercel",), _devin),
    HardSignal("PATH", "has .pi/agent", ("gh",), _pi),
)

# Every environment variable a hard signal reads, except PATH.
HARD_ENV_VARS: tuple[str, ...] = (
    "AI_AGENT",
    "AGENT",
    "GOOSE_TERMINAL",
    "CLAUDE_CODE_IS_COWORK",
    "CLAUDECODE",
    "CLAUDE_CODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CODEX_SANDBOX",
    "CODEX_CI",
    "CODEX_THREAD_ID",
    "CODEX_SANDBOX_NETWORK_DISABLED",
    "GEMINI_CLI",
    "COPILOT_CLI",
    "OPENCODE",
    "ANTIGRAVITY_AGENT",
    "AUGMENT_AGENT",
    "CURSOR_AGENT",
    "CURSOR_EXTENSION_HOST_ROLE",
    "OR_APP_NAME",
    "OR_SITE_URL",
)


def hard_inputs() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Everything a hard signal reads, for a cache that must never be
    served under a different agent state: every variable name (``PATH``
    included) and every file (the Devin marker, POSIX only)."""
    files = (DEVIN_MARKER,) if os.name == "posix" else ()
    return (*HARD_ENV_VARS, HARD_PATH_VAR), files


def _environ(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def hard_match(env: Mapping[str, str] | None = None) -> Match | None:
    """The first hard signal that matches, ignoring ``DG_AGENT``."""
    env = _environ(env)
    for signal in HARD_SIGNALS:
        found = signal.match(env)
        if found is not None:
            return found
    return None


def is_ai_agent(env: Mapping[str, str] | None = None) -> bool:
    """True when an AI agent marked the environment (a raw hard signal).

    For trust decisions. Never ``CI``, terminal state, command-line flags or
    ``DG_AGENT``.
    """
    return hard_match(env) is not None


def override(env: Mapping[str, str] | None = None) -> str | None:
    """``"0"`` or ``"1"`` from ``DG_AGENT``; anything else is ``None``."""
    value = _environ(env).get(OVERRIDE_VAR, "").strip()
    return value if value in ("0", "1") else None


def agent_mode(env: Mapping[str, str] | None = None) -> bool:
    """Hard agent mode for output, prompts, notices and login.

    The raw hard signals, unless ``DG_AGENT`` says otherwise.
    """
    forced = override(env)
    if forced is not None:
        return forced == "1"
    return is_ai_agent(env)


def no_color(env: Mapping[str, str] | None = None) -> bool:
    """``NO_COLOR`` is set to a non-empty value (no-color.org)."""
    return bool(_environ(env).get("NO_COLOR", ""))


def _isatty(stream: object) -> bool:
    try:
        return bool(stream.isatty())  # type: ignore[attr-defined]
    except (AttributeError, OSError, ValueError):
        return False


def _flags_before_dashes(argv: list[str]) -> list[str]:
    return argv[: argv.index("--")] if "--" in argv else argv


def soft_reason(
    env: Mapping[str, str] | None = None, argv: list[str] | None = None
) -> str | None:
    """Why automation is likely (a soft signal), or ``None``."""
    env = _environ(env)
    words = _flags_before_dashes(sys.argv[1:] if argv is None else argv)
    for flag in ("--non-interactive", "--agent-friendly"):
        if flag in words:
            return flag
    if env.get("CI", "").strip().lower() in ("true", "1"):
        return "CI"
    score = 0
    if not _isatty(sys.stdin):
        score += 1
    if not _isatty(sys.stdout):
        score += 1
    if env.get("TERM", "") in ("", "dumb"):
        score += 1
    return "no terminal" if score >= 3 else None


def is_agentic(
    env: Mapping[str, str] | None = None, argv: list[str] | None = None
) -> bool:
    """Agent or automation: machine output by default, no prompts or notices."""
    forced = override(env)
    if forced is not None:
        return forced == "1"
    return is_ai_agent(env) or soft_reason(env, argv) is not None


class Detection(NamedTuple):
    """Everything detection found, for ``dg help agent-mode`` and debugging."""

    hard: bool
    soft: bool
    agent_id: str | None
    reason: str | None
    marker: Match | None
    soft_reason: str | None
    override: str | None


def detect(
    env: Mapping[str, str] | None = None, argv: list[str] | None = None
) -> Detection:
    """Run every check and report the result."""
    env = _environ(env)
    forced = override(env)
    marker = hard_match(env)
    soft = soft_reason(env, argv)
    if forced is not None:
        on = forced == "1"
        return Detection(
            on, False, "dg-agent-1" if on else None, OVERRIDE_VAR, marker, soft, forced
        )
    if marker is not None:
        return Detection(
            True, False, marker.agent_id, marker.reason, marker, soft, None
        )
    return Detection(False, soft is not None, None, soft, None, soft, None)
