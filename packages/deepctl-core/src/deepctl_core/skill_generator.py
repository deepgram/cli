"""Skill generator for AI coding assistant integration.

Generates skill/instruction files that teach AI coding CLIs (Claude Code,
Codex, Gemini CLI, etc.) how to use deepctl.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class CommandMetadata:
    """Metadata for a single deepctl command."""

    name: str
    full_command: str
    help: str
    agent_help: str
    requires_auth: bool
    ci_friendly: bool
    examples: list[str]
    arguments: list[dict[str, Any]]
    is_group: bool
    parent_group: str | None
    source: str  # "builtin" or "plugin"


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

_SKILLS_DIR = Path.home() / ".deepctl" / "skills"
_STATE_FILE = _SKILLS_DIR / "skills.json"


def get_skills_state() -> dict[str, Any]:
    """Read the skills state file."""
    try:
        return json.loads(_STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"installed_skills": {}, "auto_update": True}


def save_skills_state(state: dict[str, Any]) -> None:
    """Persist the skills state file."""
    _SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(state, indent=2))


def _commands_hash(commands: list[CommandMetadata]) -> str:
    """Compute a deterministic hash of the command set."""
    blob = json.dumps(
        [
            {
                "name": c.full_command,
                "help": c.help,
                "examples": c.examples,
                "agent_help": c.agent_help,
            }
            for c in sorted(commands, key=lambda c: c.full_command)
        ],
        sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()[:16]


def skills_need_update(commands: list[CommandMetadata]) -> bool:
    """Return True if the installed skills are stale."""
    state = get_skills_state()
    if not state.get("installed_skills"):
        return False
    new_hash = _commands_hash(commands)
    return any(
        info.get("commands_hash") != new_hash
        for info in state["installed_skills"].values()
    )


# ---------------------------------------------------------------------------
# Command metadata collection
# ---------------------------------------------------------------------------


def collect_command_metadata() -> list[CommandMetadata]:
    """Introspect all entry points and build a list of CommandMetadata."""
    commands: list[CommandMetadata] = []

    eps = metadata.entry_points()

    # Top-level commands
    for ep in eps.select(group="deepctl.commands"):
        try:
            cmd_class = ep.load()
            instance = cmd_class()
            is_group = getattr(instance, "is_group", False)
            commands.append(
                CommandMetadata(
                    name=instance.name,
                    full_command=f"deepctl {instance.name}",
                    help=instance.help,
                    agent_help=getattr(instance, "agent_help", ""),
                    requires_auth=getattr(instance, "requires_auth", False),
                    ci_friendly=getattr(instance, "ci_friendly", True),
                    examples=list(getattr(instance, "examples", [])),
                    arguments=_safe_get_arguments(instance),
                    is_group=is_group,
                    parent_group=None,
                    source="builtin",
                )
            )
        except Exception:
            pass

    # External plugins
    for ep in eps.select(group="deepctl.plugins"):
        try:
            cmd_class = ep.load()
            instance = cmd_class()
            commands.append(
                CommandMetadata(
                    name=instance.name,
                    full_command=f"deepctl {instance.name}",
                    help=instance.help,
                    agent_help=getattr(instance, "agent_help", ""),
                    requires_auth=getattr(instance, "requires_auth", False),
                    ci_friendly=getattr(instance, "ci_friendly", True),
                    examples=list(getattr(instance, "examples", [])),
                    arguments=_safe_get_arguments(instance),
                    is_group=False,
                    parent_group=None,
                    source="plugin",
                )
            )
        except Exception:
            pass

    # Subcommands (deepctl.subcommands.*)
    for ep in eps:
        if ep.group and ep.group.startswith("deepctl.subcommands."):
            parent = ep.group.rsplit(".", 1)[-1]
            try:
                cmd_class = ep.load()
                instance = cmd_class()
                commands.append(
                    CommandMetadata(
                        name=instance.name,
                        full_command=f"deepctl {parent} {instance.name}",
                        help=instance.help,
                        agent_help=getattr(instance, "agent_help", ""),
                        requires_auth=getattr(instance, "requires_auth", False),
                        ci_friendly=getattr(instance, "ci_friendly", True),
                        examples=list(getattr(instance, "examples", [])),
                        arguments=_safe_get_arguments(instance),
                        is_group=False,
                        parent_group=parent,
                        source="builtin",
                    )
                )
            except Exception:
                pass

    return commands


def _safe_get_arguments(instance: Any) -> list[dict[str, Any]]:
    """Safely call get_arguments(), returning [] on failure."""
    try:
        args = instance.get_arguments()
        # Sanitize — remove non-serializable types
        clean: list[dict[str, Any]] = []
        for arg in args:
            entry: dict[str, Any] = {}
            for k, v in arg.items():
                if k == "type":
                    entry[k] = getattr(v, "__name__", str(v))
                else:
                    entry[k] = v
            clean.append(entry)
        return clean
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Content rendering
# ---------------------------------------------------------------------------


def _render_command_section(cmd: CommandMetadata) -> str:
    """Render a single command's section for a skill file."""
    lines: list[str] = []

    # Header
    arg_names = ""
    for a in cmd.arguments:
        if not a.get("is_option", False):
            name = a.get("name", "")
            if name:
                arg_names += f" <{name}>"
    lines.append(f"### {cmd.full_command}{arg_names}")

    # Description
    lines.append(cmd.help)

    # Auth indicator
    if cmd.requires_auth:
        lines.append("Requires authentication.")

    # Options
    options = [a for a in cmd.arguments if a.get("is_option", False)]
    if options:
        opt_parts: list[str] = []
        for opt in options:
            names = ", ".join(opt.get("names", []))
            opt_help = opt.get("help", "")
            default = opt.get("default")
            desc = names
            if opt_help:
                desc += f" — {opt_help}"
            if default is not None and default != "" and not opt.get("is_flag", False):
                desc += f" (default: {default})"
            opt_parts.append(desc)
        lines.append("Options: " + "; ".join(opt_parts))

    # Examples
    if cmd.examples:
        lines.append("Examples:")
        for ex in cmd.examples:
            lines.append(f"  {ex}")

    return "\n".join(lines)


def render_skill_content(
    commands: list[CommandMetadata],
    version: str,
    *,
    include_frontmatter: bool = False,
) -> str:
    """Render the full skill file content.

    Args:
        commands: List of command metadata
        version: deepctl version string
        include_frontmatter: If True, prepend YAML frontmatter (for Claude Code)

    Returns:
        Rendered Markdown content
    """
    lines: list[str] = []

    if include_frontmatter:
        lines.append("---")
        lines.append(
            "description: Use deepctl (Deepgram CLI) — transcribe audio, "
            "manage projects, debug streams, and more"
        )
        lines.append("---")
        lines.append("")

    lines.append("# deepctl CLI")
    lines.append("")
    lines.append(
        f"> Auto-generated by deepctl v{version} — "
        "regenerate with `deepctl skills update`"
    )
    lines.append("")
    lines.append("Aliases: `deepctl`, `deepgram`, `dg`")
    lines.append("")

    # Authentication section
    lines.append("## Authentication")
    lines.append("")
    lines.append("Run `dg login` or set `DEEPGRAM_API_KEY` env var.")
    lines.append("")

    # Commands section
    lines.append("## Commands")
    lines.append("")

    # Top-level commands first
    top_level = [c for c in commands if c.parent_group is None]
    for cmd in sorted(top_level, key=lambda c: c.name):
        lines.append(_render_command_section(cmd))
        lines.append("")

        # If this is a group, add its subcommands
        if cmd.is_group:
            subs = [c for c in commands if c.parent_group == cmd.name]
            for sub in sorted(subs, key=lambda c: c.name):
                lines.append(_render_command_section(sub))
                lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Generator base class
# ---------------------------------------------------------------------------


class SkillGenerator(ABC):
    """Base class for AI CLI skill file generators."""

    cli_name: str = ""
    display_name: str = ""

    @abstractmethod
    def detect(self) -> bool:
        """Return True if this AI CLI is installed/available."""

    @abstractmethod
    def get_skill_paths(self) -> list[Path]:
        """Return the file paths where skills will be written."""

    @abstractmethod
    def generate(
        self, commands: list[CommandMetadata], version: str
    ) -> dict[Path, str]:
        """Generate skill file contents.

        Returns:
            Mapping of file path -> content string
        """

    def install(
        self, commands: list[CommandMetadata], version: str
    ) -> list[Path]:
        """Generate and write skill files. Returns paths written."""
        files = self.generate(commands, version)
        written: list[Path] = []
        for path, content in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            written.append(path)
        return written

    def remove(self) -> list[Path]:
        """Remove installed skill files. Returns paths removed."""
        removed: list[Path] = []
        for path in self.get_skill_paths():
            if path.exists():
                path.unlink()
                removed.append(path)
        return removed

    def is_installed(self) -> bool:
        """Check if skill files exist."""
        return any(p.exists() for p in self.get_skill_paths())


# ---------------------------------------------------------------------------
# Concrete generators
# ---------------------------------------------------------------------------


class ClaudeCodeGenerator(SkillGenerator):
    """Generator for Claude Code (Anthropic)."""

    cli_name = "claude"
    display_name = "Claude Code"

    def detect(self) -> bool:
        return (
            Path.home().joinpath(".claude").is_dir()
            or shutil.which("claude") is not None
        )

    def get_skill_paths(self) -> list[Path]:
        return [Path.home() / ".claude" / "commands" / "deepctl.md"]

    def generate(
        self, commands: list[CommandMetadata], version: str
    ) -> dict[Path, str]:
        content = render_skill_content(
            commands, version, include_frontmatter=True
        )
        return {self.get_skill_paths()[0]: content}


class CodexGenerator(SkillGenerator):
    """Generator for OpenAI Codex CLI."""

    cli_name = "codex"
    display_name = "OpenAI Codex"

    _BEGIN = "<!-- BEGIN deepctl CLI Reference (auto-generated by deepctl) -->"
    _END = "<!-- END deepctl CLI Reference -->"

    def detect(self) -> bool:
        return (
            Path.home().joinpath(".codex").is_dir()
            or shutil.which("codex") is not None
        )

    def get_skill_paths(self) -> list[Path]:
        return [Path.home() / ".codex" / "instructions.md"]

    def generate(
        self, commands: list[CommandMetadata], version: str
    ) -> dict[Path, str]:
        content = render_skill_content(commands, version)
        wrapped = f"{self._BEGIN}\n{content}{self._END}\n"
        path = self.get_skill_paths()[0]
        return {path: self._merge(path, wrapped)}

    def _merge(self, path: Path, section: str) -> str:
        """Merge delimited section into existing file content."""
        if not path.exists():
            return section
        existing = path.read_text()
        if self._BEGIN in existing:
            before = existing[: existing.index(self._BEGIN)]
            after_end = existing.find(self._END)
            after = (
                existing[after_end + len(self._END) :]
                if after_end != -1
                else ""
            )
            return before + section + after.lstrip("\n")
        return existing.rstrip("\n") + "\n\n" + section

    def remove(self) -> list[Path]:
        path = self.get_skill_paths()[0]
        if not path.exists():
            return []
        content = path.read_text()
        if self._BEGIN not in content:
            return []
        before = content[: content.index(self._BEGIN)]
        after_end = content.find(self._END)
        after = (
            content[after_end + len(self._END) :]
            if after_end != -1
            else ""
        )
        remaining = (before + after).strip()
        if remaining:
            path.write_text(remaining + "\n")
        else:
            path.unlink()
        return [path]


class GeminiGenerator(SkillGenerator):
    """Generator for Google Gemini CLI."""

    cli_name = "gemini"
    display_name = "Gemini CLI"

    _BEGIN = "<!-- BEGIN deepctl CLI Reference (auto-generated by deepctl) -->"
    _END = "<!-- END deepctl CLI Reference -->"

    def detect(self) -> bool:
        return (
            Path.home().joinpath(".gemini").is_dir()
            or shutil.which("gemini") is not None
        )

    def get_skill_paths(self) -> list[Path]:
        return [Path.home() / ".gemini" / "GEMINI.md"]

    def generate(
        self, commands: list[CommandMetadata], version: str
    ) -> dict[Path, str]:
        content = render_skill_content(commands, version)
        wrapped = f"{self._BEGIN}\n{content}{self._END}\n"
        path = self.get_skill_paths()[0]
        return {path: self._merge(path, wrapped)}

    def _merge(self, path: Path, section: str) -> str:
        if not path.exists():
            return section
        existing = path.read_text()
        if self._BEGIN in existing:
            before = existing[: existing.index(self._BEGIN)]
            after_end = existing.find(self._END)
            after = (
                existing[after_end + len(self._END) :]
                if after_end != -1
                else ""
            )
            return before + section + after.lstrip("\n")
        return existing.rstrip("\n") + "\n\n" + section

    def remove(self) -> list[Path]:
        path = self.get_skill_paths()[0]
        if not path.exists():
            return []
        content = path.read_text()
        if self._BEGIN not in content:
            return []
        before = content[: content.index(self._BEGIN)]
        after_end = content.find(self._END)
        after = (
            content[after_end + len(self._END) :]
            if after_end != -1
            else ""
        )
        remaining = (before + after).strip()
        if remaining:
            path.write_text(remaining + "\n")
        else:
            path.unlink()
        return [path]


class AmazonQGenerator(SkillGenerator):
    """Generator for Amazon Q Developer CLI."""

    cli_name = "amazonq"
    display_name = "Amazon Q Developer"

    def detect(self) -> bool:
        return Path.home().joinpath(".amazonq").is_dir()

    def get_skill_paths(self) -> list[Path]:
        return [Path.home() / ".amazonq" / "rules" / "deepctl.md"]

    def generate(
        self, commands: list[CommandMetadata], version: str
    ) -> dict[Path, str]:
        content = render_skill_content(commands, version)
        return {self.get_skill_paths()[0]: content}


class AiderGenerator(SkillGenerator):
    """Generator for Aider CLI."""

    cli_name = "aider"
    display_name = "Aider"

    _SKILL_FILE = Path.home() / ".deepctl" / "skills" / "deepctl-conventions.md"

    def detect(self) -> bool:
        return shutil.which("aider") is not None

    def get_skill_paths(self) -> list[Path]:
        return [self._SKILL_FILE]

    def generate(
        self, commands: list[CommandMetadata], version: str
    ) -> dict[Path, str]:
        content = render_skill_content(commands, version)
        return {self._SKILL_FILE: content}

    def install(
        self, commands: list[CommandMetadata], version: str
    ) -> list[Path]:
        written = super().install(commands, version)
        # Add read reference to aider config if not already present
        self._ensure_config_ref()
        return written

    def _ensure_config_ref(self) -> None:
        """Add the skill file as a read reference in ~/.aider.conf.yml."""
        conf_path = Path.home() / ".aider.conf.yml"
        ref = str(self._SKILL_FILE)
        try:
            import yaml

            if conf_path.exists():
                data = yaml.safe_load(conf_path.read_text()) or {}
            else:
                data = {}
            read_list = data.get("read", [])
            if not isinstance(read_list, list):
                read_list = [read_list] if read_list else []
            if ref not in read_list:
                read_list.append(ref)
                data["read"] = read_list
                conf_path.write_text(yaml.dump(data, default_flow_style=False))
        except Exception:
            pass

    def remove(self) -> list[Path]:
        removed = super().remove()
        # Remove reference from aider config
        conf_path = Path.home() / ".aider.conf.yml"
        ref = str(self._SKILL_FILE)
        try:
            import yaml

            if conf_path.exists():
                data = yaml.safe_load(conf_path.read_text()) or {}
                read_list = data.get("read", [])
                if isinstance(read_list, list) and ref in read_list:
                    read_list.remove(ref)
                    data["read"] = read_list
                    conf_path.write_text(
                        yaml.dump(data, default_flow_style=False)
                    )
        except Exception:
            pass
        return removed


class OpenCodeGenerator(SkillGenerator):
    """Generator for OpenCode CLI."""

    cli_name = "opencode"
    display_name = "OpenCode"

    _BEGIN = "<!-- BEGIN deepctl CLI Reference (auto-generated by deepctl) -->"
    _END = "<!-- END deepctl CLI Reference -->"

    def detect(self) -> bool:
        return (
            Path.home().joinpath(".opencode").is_dir()
            or shutil.which("opencode") is not None
        )

    def get_skill_paths(self) -> list[Path]:
        return [Path.home() / ".opencode" / "agents.md"]

    def generate(
        self, commands: list[CommandMetadata], version: str
    ) -> dict[Path, str]:
        content = render_skill_content(commands, version)
        wrapped = f"{self._BEGIN}\n{content}{self._END}\n"
        path = self.get_skill_paths()[0]
        return {path: self._merge(path, wrapped)}

    def _merge(self, path: Path, section: str) -> str:
        if not path.exists():
            return section
        existing = path.read_text()
        if self._BEGIN in existing:
            before = existing[: existing.index(self._BEGIN)]
            after_end = existing.find(self._END)
            after = (
                existing[after_end + len(self._END) :]
                if after_end != -1
                else ""
            )
            return before + section + after.lstrip("\n")
        return existing.rstrip("\n") + "\n\n" + section

    def remove(self) -> list[Path]:
        path = self.get_skill_paths()[0]
        if not path.exists():
            return []
        content = path.read_text()
        if self._BEGIN not in content:
            return []
        before = content[: content.index(self._BEGIN)]
        after_end = content.find(self._END)
        after = (
            content[after_end + len(self._END) :]
            if after_end != -1
            else ""
        )
        remaining = (before + after).strip()
        if remaining:
            path.write_text(remaining + "\n")
        else:
            path.unlink()
        return [path]


class CursorGenerator(SkillGenerator):
    """Generator for Cursor IDE CLI."""

    cli_name = "cursor"
    display_name = "Cursor"

    def detect(self) -> bool:
        return (
            Path.home().joinpath(".cursor").is_dir()
            or shutil.which("cursor") is not None
        )

    def get_skill_paths(self) -> list[Path]:
        return [Path.home() / ".cursor" / "rules" / "deepctl.mdc"]

    def generate(
        self, commands: list[CommandMetadata], version: str
    ) -> dict[Path, str]:
        content = render_skill_content(commands, version)
        return {self.get_skill_paths()[0]: content}


class ClineGenerator(SkillGenerator):
    """Generator for Cline CLI."""

    cli_name = "cline"
    display_name = "Cline"

    def detect(self) -> bool:
        return Path.home().joinpath(".cline").is_dir()

    def get_skill_paths(self) -> list[Path]:
        return [Path.home() / ".cline" / "rules" / "deepctl.md"]

    def generate(
        self, commands: list[CommandMetadata], version: str
    ) -> dict[Path, str]:
        content = render_skill_content(commands, version)
        return {self.get_skill_paths()[0]: content}


# ---------------------------------------------------------------------------
# Registry of all generators
# ---------------------------------------------------------------------------

_ALL_GENERATORS: list[type[SkillGenerator]] = [
    ClaudeCodeGenerator,
    CodexGenerator,
    GeminiGenerator,
    AmazonQGenerator,
    AiderGenerator,
    OpenCodeGenerator,
    CursorGenerator,
    ClineGenerator,
]


def get_all_generators() -> list[SkillGenerator]:
    """Return instances of all registered generators."""
    return [cls() for cls in _ALL_GENERATORS]


def detect_ai_clis() -> list[SkillGenerator]:
    """Return generators for detected AI CLIs."""
    return [g for g in get_all_generators() if g.detect()]
