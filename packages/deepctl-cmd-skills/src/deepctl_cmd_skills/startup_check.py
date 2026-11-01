"""First-run prompt to nudge users to set up AI assistant skills.

Checks for installed AI coding CLIs in a daemon thread and prints a
one-line notification to stderr after the command, only if the thread has
already finished; dg never waits for it. Shows only once (a cache file
records that it was shown), and only to a person at a terminal: never in
CI, an AI agent session, under ``dg mcp``, with ``DEEPCTL_NO_UPDATE_CHECK``
set, or when stderr isn't a terminal.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

# Environment variables that indicate a CI environment
_CI_ENV_VARS = (
    "CI",
    "GITHUB_ACTIONS",
    "GITLAB_CI",
    "TRAVIS",
    "JENKINS_URL",
    "CIRCLECI",
    "BUILDKITE",
    "TF_BUILD",
    "CODEBUILD_BUILD_ID",
)

_CACHE_DIR = Path.home() / ".cache" / "deepctl"
_CACHE_FILE = _CACHE_DIR / "skills_prompt_shown"


def _is_ci() -> bool:
    return any(os.environ.get(v) for v in _CI_ENV_VARS)


def _is_oneshot() -> bool:
    if "UV_INTERNAL__PARENT_INTERPRETER" in os.environ:
        return True
    exe_path = str(Path(sys.executable).resolve())
    pipx_home = os.environ.get("PIPX_HOME", str(Path.home() / ".local" / "pipx"))
    pipx_cache = str(Path(pipx_home) / ".cache")
    return bool(exe_path.startswith(pipx_cache))


def _stderr_is_terminal() -> bool:
    try:
        return bool(sys.stderr is not None and sys.stderr.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def _is_agent() -> bool:
    try:
        from deepctl_core.output import is_agentic, is_ai_agent
    except ImportError:
        return False
    return is_agentic() or is_ai_agent()


def _opted_out() -> bool:
    """``DEEPCTL_NO_UPDATE_CHECK`` turns off every startup check."""
    value = os.environ.get("DEEPCTL_NO_UPDATE_CHECK", "").strip().lower()
    return value not in ("", "0", "false", "no")


def _is_mcp() -> bool:
    """This run is ``dg mcp`` (root options before it allowed)."""
    try:
        from deepctl_cmd_update.startup_check import _subcommand
    except ImportError:
        args = [a for a in sys.argv[1:] if not a.startswith("-")]
        return bool(args) and args[0] == "mcp"
    return _subcommand() == "mcp"


def _already_prompted() -> bool:
    return _CACHE_FILE.exists()


def _mark_prompted() -> None:
    try:
        _CACHE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        _CACHE_FILE.write_text("1")
    except OSError:
        pass


def _background_detect(result: dict[str, bool]) -> None:
    """Run AI CLI detection in a thread (best-effort)."""
    try:
        from deepctl_core.skill_generator import detect_ai_clis, get_skills_state

        state = get_skills_state()
        if state.get("installed_skills"):
            # Skills already installed, no need to prompt
            return

        detected = detect_ai_clis()
        if detected:
            result["should_prompt"] = True
    except Exception:
        pass


# Module-level state
_thread: threading.Thread | None = None
_result: dict[str, bool] = {}


def check_and_notify(quiet: bool = False) -> None:
    """Start a background check for AI CLIs.

    Call before CLI execution. Call :func:`print_pending_notification`
    after CLI execution to display the result.
    """
    global _thread, _result
    _thread = None
    _result = {}

    if (
        quiet
        or _opted_out()
        or _is_ci()
        or not _stderr_is_terminal()
        or _is_agent()
        or _is_oneshot()
        or _is_mcp()
        or _already_prompted()
    ):
        return

    _thread = threading.Thread(
        target=_background_detect,
        args=(_result,),
        name="deepctl-skills-check",
        daemon=True,
    )
    _thread.start()


def print_pending_notification() -> None:
    """Print the skills prompt if the check already found AI CLIs.

    Never waits: a check that is still running is dropped, and the prompt
    is recorded as shown only once it is printed.
    """
    global _thread

    thread, _thread = _thread, None
    if thread is None or thread.is_alive():
        return

    if _result.get("should_prompt"):
        try:
            sys.stderr.write(
                "\033[36mAI coding assistants detected. "
                "Run 'deepctl skills install' to set up integration.\033[0m\n"
            )
        except (BrokenPipeError, OSError, ValueError):
            # The output stream closed (e.g. an MCP host disconnected stdio
            # after `dg mcp` finished). The prompt is purely advisory.
            return
        _mark_prompted()
