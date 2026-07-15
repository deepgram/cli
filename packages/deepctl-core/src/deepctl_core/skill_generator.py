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
_REPO_CACHE_DIR = _SKILLS_DIR / "repo_cache"
_SKILLS_REPO = "deepgram/skills"
_SKILLS_BRANCH = "main"


def get_skills_state() -> dict[str, Any]:
    """Read the skills state file."""
    try:
        result: dict[str, Any] = json.loads(_STATE_FILE.read_text())
        return result
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"installed_skills": {}, "auto_update": True}


def save_skills_state(state: dict[str, Any]) -> None:
    """Persist the skills state file."""
    _SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(state, indent=2))


def fetch_repo_skills(force: bool = False) -> dict[str, str]:
    """Download skill markdown files from the deepgram/skills GitHub repo.

    Returns a mapping of skill name -> markdown content.
    Caches locally to avoid repeated network requests.
    """
    import urllib.request

    cache_marker = _REPO_CACHE_DIR / ".fetched"
    if not force and cache_marker.exists():
        # Check if cache is less than 1 hour old
        import time

        try:
            age = time.time() - cache_marker.stat().st_mtime
            if age < 3600:  # 1 hour
                return _read_cached_skills()
        except OSError:
            pass

    base = f"https://raw.githubusercontent.com/{_SKILLS_REPO}/{_SKILLS_BRANCH}"
    skill_names = ["api", "docs", "setup-mcp", "starters"]
    skills: dict[str, str] = {}

    _REPO_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    for name in skill_names:
        url = f"{base}/skills/{name}/SKILL.md"
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                content = resp.read().decode("utf-8")
            skills[name] = content
            (  # Cache to disk
                _REPO_CACHE_DIR / f"{name}.md"
            ).write_text(content)
        except Exception:
            # Use cached version if available
            cached = _REPO_CACHE_DIR / f"{name}.md"
            if cached.exists():
                skills[name] = cached.read_text()

    if skills:
        cache_marker.write_text("1")

    return skills


def _read_cached_skills() -> dict[str, str]:
    """Read previously cached repo skills."""
    skills: dict[str, str] = {}
    if not _REPO_CACHE_DIR.exists():
        return skills
    for md_file in _REPO_CACHE_DIR.glob("*.md"):
        skills[md_file.stem] = md_file.read_text()
    return skills


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
    # Discover subcommand groups by checking known group commands
    group_names = [c.name for c in commands if c.is_group and c.parent_group is None]
    for group_name in group_names:
        sub_group = f"deepctl.subcommands.{group_name}"
        for ep in eps.select(group=sub_group):
            try:
                cmd_class = ep.load()
                instance = cmd_class()
                commands.append(
                    CommandMetadata(
                        name=instance.name,
                        full_command=f"deepctl {group_name} {instance.name}",
                        help=instance.help,
                        agent_help=getattr(instance, "agent_help", ""),
                        requires_auth=getattr(instance, "requires_auth", False),
                        ci_friendly=getattr(instance, "ci_friendly", True),
                        examples=list(getattr(instance, "examples", [])),
                        arguments=_safe_get_arguments(instance),
                        is_group=False,
                        parent_group=group_name,
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


def render_developer_guide(
    version: str,
    *,
    include_frontmatter: bool = False,
) -> str:
    """Render the Deepgram Developer Guide skill content.

    This replaces the old command-metadata rendering with a comprehensive
    guide covering all Deepgram products, SDKs, and developer resources.

    Args:
        version: deepctl version string
        include_frontmatter: If True, prepend YAML frontmatter (for Claude Code)

    Returns:
        Rendered Markdown content
    """
    lines: list[str] = []

    if include_frontmatter:
        lines.append("---")
        lines.append(
            "description: Deepgram Developer Guide — build with speech-to-text, "
            "text-to-speech, audio intelligence, and voice agents"
        )
        lines.append("---")
        lines.append("")

    lines.append("# Deepgram Developer Guide")
    lines.append("")
    lines.append(
        f"> Auto-generated by deepctl v{version} — regenerate with `dg skills update`"
    )
    lines.append("")

    # --- Overview ---
    lines.append("## Overview")
    lines.append("")
    lines.append(
        "Deepgram is an AI speech platform providing APIs for speech-to-text (STT), "
        "text-to-speech (TTS), audio intelligence, and real-time voice agents."
    )
    lines.append("")
    lines.append("- **Console:** <https://console.deepgram.com>")
    lines.append("- **Docs:** <https://developers.deepgram.com>")
    lines.append("- **API Reference:** <https://developers.deepgram.com/reference>")
    lines.append("")

    # --- Authentication ---
    lines.append("## Authentication")
    lines.append("")
    lines.append(
        "All API requests require an API key. Create one at "
        "<https://console.deepgram.com/api-keys>."
    )
    lines.append("")
    lines.append("```bash")
    lines.append("# Set as environment variable")
    lines.append('export DEEPGRAM_API_KEY="your-api-key"')
    lines.append("")
    lines.append("# Or use the CLI")
    lines.append("dg login")
    lines.append("```")
    lines.append("")

    # --- Speech-to-Text ---
    lines.append("## Speech-to-Text (STT)")
    lines.append("")
    lines.append("Convert audio to text — pre-recorded files or real-time streams.")
    lines.append("")
    lines.append("### Models")
    lines.append("")
    lines.append("- **Nova-3** — Latest and most accurate. Best for most use cases.")
    lines.append("- **Nova-2** — Previous generation. Still excellent accuracy.")
    lines.append(
        "- **Whisper** — Open-source model, available via Deepgram's infrastructure."
    )
    lines.append("")
    lines.append("### Key Features")
    lines.append("")
    lines.append("- **Diarization** (`diarize=true`) — Speaker identification")
    lines.append(
        "- **Smart Formatting** (`smart_format=true`) — "
        "Punctuation, casing, numerals, dates"
    )
    lines.append("- **Redaction** (`redact=true`) — PII removal (PCI, SSN, numbers)")
    lines.append(
        "- **Language Detection** (`detect_language=true`) — "
        "Auto-detect spoken language"
    )
    lines.append(
        "- **Keywords** (`keywords=word:boost`) — Boost recognition of specific terms"
    )
    lines.append(
        "- **Utterances** (`utterances=true`) — Segment transcript by speaker turns"
    )
    lines.append("- **Paragraphs** (`paragraphs=true`) — Auto-paragraph the transcript")
    lines.append("")
    lines.append("### Pre-recorded Example (Python)")
    lines.append("")
    lines.append("```python")
    lines.append("from deepgram import DeepgramClient, PrerecordedOptions")
    lines.append("")
    lines.append('dg = DeepgramClient("DEEPGRAM_API_KEY")')
    lines.append("")
    lines.append('with open("audio.wav", "rb") as f:')
    lines.append('    source = {"buffer": f.read()}')
    lines.append("")
    lines.append("options = PrerecordedOptions(")
    lines.append('    model="nova-3", smart_format=True, diarize=True')
    lines.append(")")
    lines.append("")
    lines.append('response = dg.listen.rest.v("1").transcribe_file(source, options)')
    lines.append("print(response.results.channels[0].alternatives[0].transcript)")
    lines.append("```")
    lines.append("")
    lines.append("### Pre-recorded Example (JavaScript)")
    lines.append("")
    lines.append("```javascript")
    lines.append('import { createClient } from "@deepgram/sdk";')
    lines.append("")
    lines.append('const dg = createClient("DEEPGRAM_API_KEY");')
    lines.append("")
    lines.append("const { result } = await dg.listen.prerecorded.transcribeFile(")
    lines.append('  fs.readFileSync("audio.wav"),')
    lines.append('  { model: "nova-3", smart_format: true, diarize: true }')
    lines.append(");")
    lines.append("")
    lines.append("console.log(result.results.channels[0].alternatives[0].transcript);")
    lines.append("```")
    lines.append("")
    lines.append("### Streaming Example (Python)")
    lines.append("")
    lines.append("```python")
    lines.append("from deepgram import DeepgramClient, LiveOptions")
    lines.append("")
    lines.append('dg = DeepgramClient("DEEPGRAM_API_KEY")')
    lines.append('connection = dg.listen.websocket.v("1")')
    lines.append("")
    lines.append("def on_message(self, result, **kwargs):")
    lines.append("    transcript = result.channel.alternatives[0].transcript")
    lines.append("    if transcript:")
    lines.append('        print(f"Transcript: {transcript}")')
    lines.append("")
    lines.append('connection.on("Results", on_message)')
    lines.append("")
    lines.append('options = LiveOptions(model="nova-3", language="en")')
    lines.append("connection.start(options)")
    lines.append("# Send audio data via connection.send(audio_bytes)")
    lines.append("```")
    lines.append("")

    # --- Text-to-Speech ---
    lines.append("## Text-to-Speech (TTS)")
    lines.append("")
    lines.append(
        "Generate natural-sounding speech from text using Deepgram's Aura voices."
    )
    lines.append("")
    lines.append("### Models")
    lines.append("")
    lines.append(
        "- **Aura-2** — Latest generation. High quality, low latency, many voices."
    )
    lines.append("- **Aura** — Previous generation. Solid quality and performance.")
    lines.append(
        "- **Flux** — Conversational TTS on the Speak v2 WebSocket API "
        "(streaming, turn-based). Voices like `flux-alexis-en`."
    )
    lines.append("")
    lines.append("### Popular Voices")
    lines.append("")
    lines.append("Aura voices follow `aura-2-{name}-en`. Examples:")
    lines.append("- `aura-2-andromeda-en`, `aura-2-arcas-en`, `aura-2-atlas-en`")
    lines.append("- `aura-2-luna-en`, `aura-2-stella-en`, `aura-2-helios-en`")
    lines.append("")
    lines.append(
        "Flux (Speak v2) voices follow `flux-{name}-en` (English at launch), "
        "e.g. `flux-alexis-en`."
    )
    lines.append("")
    lines.append("Full voice list: <https://developers.deepgram.com/docs/tts-models>")
    lines.append("")
    lines.append("### Aura TTS — Speak v1, batch REST (Python)")
    lines.append("")
    lines.append("```python")
    lines.append("from deepgram import DeepgramClient")
    lines.append("")
    lines.append('client = DeepgramClient(api_key="DEEPGRAM_API_KEY")')
    lines.append("")
    lines.append("audio = client.speak.v1.audio.generate(")
    lines.append('    text="Hello from Deepgram!",')
    lines.append('    model="aura-2-andromeda-en",')
    lines.append('    encoding="mp3",')
    lines.append(")")
    lines.append('with open("output.mp3", "wb") as f:')
    lines.append("    for chunk in audio:")
    lines.append("        f.write(chunk)")
    lines.append("```")
    lines.append("")
    lines.append("### Flux TTS — Speak v2, WebSocket streaming (Python)")
    lines.append("")
    lines.append("```python")
    lines.append("from deepgram import DeepgramClient")
    lines.append("from deepgram.speak.v2.types.speak_v2speak import SpeakV2Speak")
    lines.append("")
    lines.append('client = DeepgramClient(api_key="DEEPGRAM_API_KEY")')
    lines.append("")
    lines.append("with client.speak.v2.connect(")
    lines.append('    model="flux-alexis-en", encoding="linear16", sample_rate="24000"')
    lines.append(") as conn:")
    lines.append(
        '    conn.send_speak(SpeakV2Speak(type="Speak", text="Hello from Flux!"))'
    )
    lines.append("    conn.send_flush()")
    lines.append("    conn.send_close()")
    lines.append('    with open("output.raw", "wb") as f:')
    lines.append("        for message in conn:")
    lines.append("            if isinstance(message, bytes):")
    lines.append("                f.write(message)  # raw linear16 PCM, 24kHz mono")
    lines.append("```")
    lines.append("")
    lines.append("### Aura TTS — Speak v1 (JavaScript)")
    lines.append("")
    lines.append("```javascript")
    lines.append('import { createClient } from "@deepgram/sdk";')
    lines.append("")
    lines.append('const client = createClient("DEEPGRAM_API_KEY");')
    lines.append("")
    lines.append("const response = await client.speak.v1.audio.generate({")
    lines.append('  text: "Hello from Deepgram!",')
    lines.append('  model: "aura-2-andromeda-en",')
    lines.append("});")
    lines.append("const buffer = await response.arrayBuffer();")
    lines.append("// Write buffer to a file or audio output")
    lines.append("```")
    lines.append("")

    # --- Audio Intelligence ---
    lines.append("## Audio Intelligence")
    lines.append("")
    lines.append(
        "Extract meaning from audio beyond transcription. "
        "Add these features as query parameters to STT requests."
    )
    lines.append("")
    lines.append(
        "- **Summarization** (`summarize=v2`) — Generate a summary of the audio content"
    )
    lines.append(
        "- **Topic Detection** (`detect_topics=true`) — "
        "Identify topics discussed in the audio"
    )
    lines.append("- **Intent Recognition** (`intents=true`) — Detect speaker intents")
    lines.append(
        "- **Sentiment Analysis** (`sentiment=true`) — Analyze sentiment per utterance"
    )
    lines.append("")

    # --- Voice Agent API ---
    lines.append("## Voice Agent API")
    lines.append("")
    lines.append(
        "Build real-time conversational voice AI with Deepgram's Voice Agent API. "
        "Combines STT, TTS, and LLM orchestration over a single WebSocket."
    )
    lines.append("")
    lines.append("### Key Capabilities")
    lines.append("")
    lines.append("- Real-time bidirectional audio streaming")
    lines.append("- Barge-in support (interrupt the agent mid-speech)")
    lines.append("- Function calling (agent can invoke tools)")
    lines.append("- Configurable LLM provider and voice")
    lines.append("")
    lines.append("Docs: <https://developers.deepgram.com/docs/voice-agent>")
    lines.append("")

    # --- SDKs ---
    lines.append("## SDKs")
    lines.append("")
    lines.append("| Language | Package | Install |")
    lines.append("|----------|---------|---------|")
    lines.append(
        "| Python | "
        "[deepgram-sdk](https://github.com/deepgram/deepgram-python-sdk) | "
        "`pip install deepgram-sdk` |"
    )
    lines.append(
        "| JavaScript/TS | "
        "[@deepgram/sdk](https://github.com/deepgram/deepgram-js-sdk) | "
        "`npm install @deepgram/sdk` |"
    )
    lines.append(
        "| Go | "
        "[deepgram-go-sdk](https://github.com/deepgram/deepgram-go-sdk) | "
        "`go get github.com/deepgram/deepgram-go-sdk` |"
    )
    lines.append(
        "| .NET | "
        "[Deepgram.SDK](https://github.com/deepgram/deepgram-dotnet-sdk) | "
        "`dotnet add package Deepgram` |"
    )
    lines.append(
        "| Rust | "
        "[deepgram](https://github.com/deepgram/deepgram-rust-sdk) | "
        "`cargo add deepgram` |"
    )
    lines.append("")

    # --- deepctl CLI ---
    lines.append("## deepctl CLI")
    lines.append("")
    lines.append(
        "The `deepctl` CLI (aliases: `deepgram`, `dg`) provides command-line "
        "access to Deepgram features."
    )
    lines.append("")
    lines.append("```bash")
    lines.append("dg login                    # Authenticate")
    lines.append("dg listen audio.wav         # Transcribe a file")
    lines.append("dg listen --mic             # Live transcription from mic")
    lines.append('dg speak "Hello world"      # Text-to-speech')
    lines.append("dg projects list            # List projects")
    lines.append("dg usage                    # View API usage")
    lines.append("dg mcp                      # Start MCP server")
    lines.append("dg --help                   # Full command reference")
    lines.append("```")
    lines.append("")

    # --- MCP Server ---
    lines.append("## MCP Server Integration")
    lines.append("")
    lines.append(
        "deepctl includes an MCP (Model Context Protocol) server that "
        "exposes Deepgram tools to AI assistants."
    )
    lines.append("")
    lines.append("### Setup")
    lines.append("")
    lines.append("Add to your AI assistant's MCP configuration:")
    lines.append("")
    lines.append("```json")
    lines.append("{")
    lines.append('  "mcpServers": {')
    lines.append('    "deepgram": {')
    lines.append('      "command": "dg",')
    lines.append('      "args": ["mcp"]')
    lines.append("    }")
    lines.append("  }")
    lines.append("}")
    lines.append("```")
    lines.append("")
    lines.append(
        "The MCP server exposes tools for transcription, TTS, "
        "project management, and usage queries."
    )
    lines.append("")

    # --- Resources ---
    lines.append("## Resources")
    lines.append("")
    lines.append("- **Documentation:** <https://developers.deepgram.com>")
    lines.append("- **API Reference:** <https://developers.deepgram.com/reference>")
    lines.append("- **API Playground:** <https://playground.deepgram.com>")
    lines.append("- **Console:** <https://console.deepgram.com>")
    lines.append("- **Discord:** <https://discord.gg/deepgram>")
    lines.append("- **GitHub:** <https://github.com/deepgram>")
    lines.append("- **Community:** <https://community.deepgram.com>")
    lines.append("- **Starter Apps:** <https://github.com/deepgram-starters>")
    lines.append("- **Templates:** <https://templates.dx.deepgram.com>")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_skill_content(
    commands: list[CommandMetadata],
    version: str,
    *,
    include_frontmatter: bool = False,
) -> str:
    """Render the full skill file content.

    Delegates to :func:`render_developer_guide` to produce a comprehensive
    Deepgram developer guide rather than a CLI command reference.

    Args:
        commands: List of command metadata (retained for backward compatibility;
            not used for rendering)
        version: deepctl version string
        include_frontmatter: If True, prepend YAML frontmatter (for Claude Code)

    Returns:
        Rendered Markdown content
    """
    return render_developer_guide(version, include_frontmatter=include_frontmatter)


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
        self,
        commands: list[CommandMetadata],  # noqa: ARG002
        version: str,  # noqa: ARG002
    ) -> list[Path]:
        """Fetch skills from deepgram/skills repo and install them."""
        repo_skills = fetch_repo_skills(force=True)
        if not repo_skills:
            return []
        return self._write_repo_skills(repo_skills)

    def _write_repo_skills(self, repo_skills: dict[str, str]) -> list[Path]:
        """Write combined repo skill content to this tool's skill paths."""
        combined = "\n\n---\n\n".join(repo_skills.values())
        written: list[Path] = []
        for path in self.get_skill_paths():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(combined)
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

    @property
    def _skill_dir(self) -> Path:
        return Path.home() / ".claude" / "commands" / "deepgram"

    def detect(self) -> bool:
        return (
            Path.home().joinpath(".claude").is_dir()
            or shutil.which("claude") is not None
        )

    def get_skill_paths(self) -> list[Path]:
        return [
            self._skill_dir / f"{name}.md"
            for name in ["api", "docs", "setup-mcp", "starters"]
        ]

    def generate(
        self, commands: list[CommandMetadata], version: str
    ) -> dict[Path, str]:
        content = render_skill_content(commands, version, include_frontmatter=True)
        return {self._skill_dir / "deepgram.md": content}

    def _write_repo_skills(self, repo_skills: dict[str, str]) -> list[Path]:
        self._skill_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for name, content in repo_skills.items():
            path = self._skill_dir / f"{name}.md"
            path.write_text(content)
            written.append(path)
        return written

    def remove(self) -> list[Path]:
        removed: list[Path] = []
        if self._skill_dir.exists():
            for f in self._skill_dir.glob("*.md"):
                f.unlink()
                removed.append(f)
            try:
                self._skill_dir.rmdir()
            except OSError:
                pass
        return removed


class CodexGenerator(SkillGenerator):
    """Generator for OpenAI Codex CLI."""

    cli_name = "codex"
    display_name = "OpenAI Codex"

    _BEGIN = "<!-- BEGIN deepctl CLI Reference (auto-generated by deepctl) -->"
    _END = "<!-- END deepctl CLI Reference -->"

    def detect(self) -> bool:
        return (
            Path.home().joinpath(".codex").is_dir() or shutil.which("codex") is not None
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
            after = existing[after_end + len(self._END) :] if after_end != -1 else ""
            return before + section + after.lstrip("\n")
        return existing.rstrip("\n") + "\n\n" + section

    def _write_repo_skills(self, repo_skills: dict[str, str]) -> list[Path]:
        combined = "\n\n---\n\n".join(repo_skills.values())
        wrapped = f"{self._BEGIN}\n{combined}\n{self._END}\n"
        path = self.get_skill_paths()[0]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._merge(path, wrapped))
        return [path]

    def remove(self) -> list[Path]:
        path = self.get_skill_paths()[0]
        if not path.exists():
            return []
        content = path.read_text()
        if self._BEGIN not in content:
            return []
        before = content[: content.index(self._BEGIN)]
        after_end = content.find(self._END)
        after = content[after_end + len(self._END) :] if after_end != -1 else ""
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
            after = existing[after_end + len(self._END) :] if after_end != -1 else ""
            return before + section + after.lstrip("\n")
        return existing.rstrip("\n") + "\n\n" + section

    def _write_repo_skills(self, repo_skills: dict[str, str]) -> list[Path]:
        combined = "\n\n---\n\n".join(repo_skills.values())
        wrapped = f"{self._BEGIN}\n{combined}\n{self._END}\n"
        path = self.get_skill_paths()[0]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._merge(path, wrapped))
        return [path]

    def remove(self) -> list[Path]:
        path = self.get_skill_paths()[0]
        if not path.exists():
            return []
        content = path.read_text()
        if self._BEGIN not in content:
            return []
        before = content[: content.index(self._BEGIN)]
        after_end = content.find(self._END)
        after = content[after_end + len(self._END) :] if after_end != -1 else ""
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

    def install(self, commands: list[CommandMetadata], version: str) -> list[Path]:
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
                    conf_path.write_text(yaml.dump(data, default_flow_style=False))
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
            after = existing[after_end + len(self._END) :] if after_end != -1 else ""
            return before + section + after.lstrip("\n")
        return existing.rstrip("\n") + "\n\n" + section

    def _write_repo_skills(self, repo_skills: dict[str, str]) -> list[Path]:
        combined = "\n\n---\n\n".join(repo_skills.values())
        wrapped = f"{self._BEGIN}\n{combined}\n{self._END}\n"
        path = self.get_skill_paths()[0]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._merge(path, wrapped))
        return [path]

    def remove(self) -> list[Path]:
        path = self.get_skill_paths()[0]
        if not path.exists():
            return []
        content = path.read_text()
        if self._BEGIN not in content:
            return []
        before = content[: content.index(self._BEGIN)]
        after_end = content.find(self._END)
        after = content[after_end + len(self._END) :] if after_end != -1 else ""
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
