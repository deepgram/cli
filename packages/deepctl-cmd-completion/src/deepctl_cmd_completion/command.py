"""Shell completion command for deepctl."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from deepctl_core import AuthManager, BaseCommand, BaseResult, Config, DeepgramClient
from rich.console import Console

console = Console()

# Click derives the completion env var from the invocation name.
# Users call the CLI as "dg", so the var is _DG_COMPLETE.
_SHELLS: dict[str, dict[str, str]] = {
    "bash": {
        "eval_line": 'eval "$(_DG_COMPLETE=bash_source dg)"',
        "profile": "~/.bashrc",
        "check_marker": "_DG_COMPLETE=bash_source",
    },
    "zsh": {
        "eval_line": 'eval "$(_DG_COMPLETE=zsh_source dg)"',
        "profile": "~/.zshrc",
        "check_marker": "_DG_COMPLETE=zsh_source",
    },
    "fish": {
        "eval_line": "eval (env _DG_COMPLETE=fish_source dg)",
        "profile": "~/.config/fish/config.fish",
        "check_marker": "_DG_COMPLETE=fish_source",
    },
}


class CompletionResult(BaseResult):
    """Return structure for the completion command."""

    shell: str
    eval_line: str
    installed: bool = False
    install_path: str | None = None


class CompletionCommand(BaseCommand):
    """Generate shell completion scripts for dg."""

    name = "completion"
    help = "Generate shell completion script for bash, zsh, or fish"
    short_help = "Generate shell completion"

    requires_auth = False
    requires_project = False
    ci_friendly = True

    examples = [
        "dg completion",
        "dg completion bash",
        "dg completion zsh",
        "dg completion fish",
        'eval "$(dg completion bash)"',
        "dg completion zsh --install",
    ]
    agent_help = (
        "Output the shell completion eval line for bash, zsh, or fish. "
        "Source the output to enable tab-completion for all dg commands, "
        "options, and arguments. Use --install to append to the shell profile "
        "automatically. Shell is auto-detected from $SHELL if not specified."
    )

    def get_arguments(self) -> list[dict[str, Any]]:
        """Get command arguments."""
        return [
            {
                "name": "shell",
                "help": "Shell type: bash, zsh, fish (auto-detected if omitted)",
                "type": str,
                "required": False,
                "nargs": 1,
            },
            {
                "names": ["--install"],
                "help": "Append completion line to shell profile and exit",
                "is_flag": True,
                "is_option": True,
            },
        ]

    def handle(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> Any:
        """Handle completion command."""
        shell = kwargs.get("shell") or self._detect_shell()
        install = kwargs.get("install", False)

        if shell not in _SHELLS:
            console.print(
                f"[red]Unknown shell:[/red] {shell!r}. Supported: {', '.join(_SHELLS)}"
            )
            return BaseResult(
                status="error",
                message=f"Unknown shell: {shell}",
            )

        cfg = _SHELLS[shell]
        eval_line = cfg["eval_line"]
        profile_path = Path(cfg["profile"]).expanduser()

        if install:
            return self._install(shell, eval_line, cfg["check_marker"], profile_path)

        # Just print the eval line — users can pipe it directly:
        #   eval "$(dg completion bash)"
        console.print(eval_line)
        console.print(
            f"\n[dim]Add the above line to {cfg['profile']},"
            f" or run:[/dim]\n"
            f"  [bold]dg completion {shell} --install[/bold]",
            highlight=False,
        )

        return CompletionResult(
            status="success",
            shell=shell,
            eval_line=eval_line,
        )

    def _detect_shell(self) -> str:
        """Detect the current shell from $SHELL."""
        shell_bin = os.environ.get("SHELL", "")
        if "zsh" in shell_bin:
            return "zsh"
        if "fish" in shell_bin:
            return "fish"
        return "bash"

    def _install(
        self,
        shell: str,
        eval_line: str,
        check_marker: str,
        profile_path: Path,
    ) -> CompletionResult:
        """Append the completion line to the shell profile."""
        try:
            profile_path.parent.mkdir(parents=True, exist_ok=True)

            # Idempotent: skip if already present
            if profile_path.exists():
                existing = profile_path.read_text()
                if check_marker in existing:
                    console.print(
                        f"[yellow]Already installed[/yellow] in {profile_path}"
                    )
                    return CompletionResult(
                        status="info",
                        message="Already installed",
                        shell=shell,
                        eval_line=eval_line,
                        installed=False,
                        install_path=str(profile_path),
                    )

            with profile_path.open("a") as f:
                f.write(f"\n# deepctl (dg) shell completion\n{eval_line}\n")

            console.print(f"[green]✓[/green] Completion installed to {profile_path}")
            console.print(
                f"[dim]Restart your shell or run:[/dim]  source {profile_path}"
            )

            return CompletionResult(
                status="success",
                message=f"Installed to {profile_path}",
                shell=shell,
                eval_line=eval_line,
                installed=True,
                install_path=str(profile_path),
            )

        except OSError as e:
            console.print(f"[red]Failed to write to {profile_path}:[/red] {e}")
            return CompletionResult(
                status="error",
                message=str(e),
                shell=shell,
                eval_line=eval_line,
            )
