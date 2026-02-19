"""First-run prompt to nudge users to set up AI assistant skills.

Checks for installed AI coding CLIs and prints a one-line notification
to stderr. Shows only once (uses a cache file to track).
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


def _already_prompted() -> bool:
    return _CACHE_FILE.exists()


def _mark_prompted() -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
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
            _mark_prompted()
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
    _result = {}

    if quiet or _is_ci() or _is_oneshot() or _already_prompted():
        return

    _thread = threading.Thread(
        target=_background_detect,
        args=(_result,),
        daemon=True,
    )
    _thread.start()


def print_pending_notification() -> None:
    """Print the skills prompt if AI CLIs were detected."""
    global _thread

    if _thread is None:
        return

    _thread.join(timeout=2.0)
    _thread = None

    if _result.get("should_prompt"):
        sys.stderr.write(
            "\033[36mAI coding assistants detected. "
            "Run 'deepctl skills install' to set up integration.\033[0m\n"
        )
