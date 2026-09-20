"""Install Deepgram skills into AI coding assistants.

Two different artifacts live in this module, and keeping them apart is the
point:

* The **Deepgram skills** themselves, fetched from deepgram/skills by
  :mod:`deepctl_core.skill_bundle`. A skill is a *folder* — ``SKILL.md``
  plus, for some, a ``references/`` subdirectory — and it is installed
  verbatim into whatever directory the target tool loads skills from.
* A generated **deepctl developer guide** (:func:`render_developer_guide`),
  for tools that have no skills directory and only read one long context or
  rules file. That is the one thing that legitimately gets merged into a
  file the user also edits, under HTML markers.

Mixing the two is what produced ``~/.claude/commands/deepgram/api.md`` (the
slash-command directory, holding a skill) and a 58 KB ``instructions.md``
with four skills concatenated inside a marker that claimed to be a CLI
reference.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Any

from deepctl_core.skill_bundle import fetch_skill_bundle

if TYPE_CHECKING:
    from deepctl_core.skill_bundle import RepoSkill

# The cross-tool installer that owns the directory conventions this module
# targets. Quoted verbatim to users whose tool has no skills directory yet.
SKILLS_CLI_HINT = "npx skills add deepgram/skills"

#: Filename that marks a directory as a skill.
SKILL_ENTRY_FILE = "SKILL.md"

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


def fetch_repo_skills(
    ref: str | None = None,
    *,
    force: bool = False,
) -> list[RepoSkill]:
    """Fetch every skill published by deepgram/skills.

    The list comes from the upstream ``.claude-plugin/marketplace.json``
    manifest, never from a list in this repo: upstream CI checks that
    manifest against the directories on disk in both directions on every
    pull request, so it is the one place that cannot drift.

    Args:
        ref: Upstream git ref. Defaults to the pinned release tag.
        force: Re-download even if the ref is already cached.

    Raises:
        SkillFetchError: The bundle could not be fetched or trusted. This
            is deliberately fatal — see :class:`SkillFetchError`.
    """
    return fetch_skill_bundle(ref, cache_dir=_REPO_CACHE_DIR, force=force)


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
        "Generate natural-sounding speech from text using Deepgram's Aura and "
        "Flux voices."
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
    lines.append(
        "`expressivity` is beta and defaults to `0` (nominal delivery) when omitted."
    )
    lines.append("")
    lines.append("```python")
    lines.append("from deepgram import DeepgramClient")
    lines.append("from deepgram.speak.v2.types.speak_v2speak import SpeakV2Speak")
    lines.append("")
    lines.append('client = DeepgramClient(api_key="DEEPGRAM_API_KEY")')
    lines.append("")
    lines.append("with client.speak.v2.connect(")
    lines.append('    model="flux-alexis-en",')
    lines.append('    encoding="linear16",')
    lines.append('    sample_rate="24000",')
    lines.append("    speed=1.0,  # 0.85–1.15 in 0.05 steps (optional)")
    lines.append("    expressivity=0,  # beta; -2..2, default 0 = nominal (optional)")
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


@dataclass(frozen=True)
class LegacyArtifact:
    """Something deepctl <= 0.3.0 wrote that the tool does not read as a skill.

    Cleaned up on install and remove so an upgrade does not leave a stale
    copy of four skills lying around next to a fresh copy of fourteen.
    """

    path: Path
    #: True when the path is a file the user also edits, so only deepctl's
    #: own marked-off section may be removed.
    shared: bool = False


class SkillGenerator(ABC):
    """Base class for installing Deepgram skills into one AI coding tool.

    A tool that loads skill *folders* overrides :meth:`skills_root` with the
    directory it reads. Skills are copied there verbatim — ``SKILL.md``,
    ``references/`` and anything else the skill ships.

    A tool with no skills directory installs nothing and reports
    :meth:`manual_hint` instead. Writing a Deepgram blob into a context file
    that the tool may or may not read, under a marker claiming to be
    something else, is how this command came to write 58 KB into
    ``~/.codex/instructions.md`` — a path current Codex does not read at all.
    """

    cli_name: str = ""
    display_name: str = ""

    #: Markers written by deepctl <= 0.3.0. They claimed to delimit a CLI
    #: reference but actually wrapped four concatenated skills. Retained
    #: only so that section can be found and removed again.
    _LEGACY_BEGIN = "<!-- BEGIN deepctl CLI Reference (auto-generated by deepctl) -->"
    _LEGACY_END = "<!-- END deepctl CLI Reference -->"

    @abstractmethod
    def detect(self) -> bool:
        """Return True if this AI CLI is installed/available."""

    def skills_root(self) -> Path | None:
        """User-scope directory this tool loads skill folders from.

        ``None`` means the tool has no documented skills directory, so
        skills cannot honestly be installed for it by copying files.
        """
        return None

    def legacy_paths(self) -> list[LegacyArtifact]:
        """Paths written by earlier deepctl versions, to be cleaned up."""
        return []

    def get_skill_paths(self) -> list[Path]:
        """Return the installed skill folders currently on disk."""
        root = self.skills_root()
        if root is None or not root.is_dir():
            return []
        return sorted(p for p in root.iterdir() if (p / SKILL_ENTRY_FILE).is_file())

    def install(
        self,
        commands: list[CommandMetadata],  # noqa: ARG002
        version: str,  # noqa: ARG002
        *,
        ref: str | None = None,
    ) -> list[Path]:
        """Fetch the upstream skills and install them for this tool.

        Raises:
            SkillFetchError: Upstream could not be fetched or trusted.
        """
        if self.skills_root() is None:
            self.clean_legacy()
            return []
        return self.install_skills(fetch_repo_skills(ref, force=True))

    def install_skills(self, skills: list[RepoSkill]) -> list[Path]:
        """Copy each skill folder into this tool's skills directory."""
        root = self.skills_root()
        if root is None:
            return []
        self.clean_legacy()
        root.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for skill in skills:
            dest = root / skill.name
            # Replace rather than merge: when a skill drops a reference
            # file upstream it has to disappear here too, or the assistant
            # keeps reading a page that no longer exists.
            if dest.is_dir():
                shutil.rmtree(dest)
            elif dest.exists():
                dest.unlink()
            shutil.copytree(skill.path, dest)
            written.append(dest)
        return written

    def remove(self) -> list[Path]:
        """Remove everything this generator installed."""
        removed = self.clean_legacy()
        for path in self.get_skill_paths():
            shutil.rmtree(path, ignore_errors=True)
            removed.append(path)
        root = self.skills_root()
        if root is not None and root.is_dir() and not any(root.iterdir()):
            try:
                root.rmdir()
            except OSError:
                pass
        return removed

    def clean_legacy(self) -> list[Path]:
        """Remove what deepctl <= 0.3.0 wrote for this tool."""
        removed: list[Path] = []
        for artifact in self.legacy_paths():
            if _clean_legacy_artifact(artifact, self._LEGACY_BEGIN, self._LEGACY_END):
                removed.append(artifact.path)
        return removed

    def is_installed(self) -> bool:
        """Check whether skills are installed for this tool."""
        return bool(self.get_skill_paths())

    def manual_hint(self) -> str | None:
        """How to get Deepgram skills into a tool deepctl cannot install to."""
        if self.skills_root() is not None:
            return None
        return (
            f"{self.display_name} has no documented skills directory. "
            f"For the Deepgram skills, run: {SKILLS_CLI_HINT}"
        )


def _clean_legacy_artifact(artifact: LegacyArtifact, begin: str, end: str) -> bool:
    """Remove one legacy artifact. Returns True if anything changed."""
    path = artifact.path
    if not path.exists():
        return False

    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        return True

    if not artifact.shared:
        path.unlink()
        return True

    # A file the user also writes: take out only deepctl's own section.
    try:
        content = path.read_text()
    except (OSError, UnicodeDecodeError):
        return False
    if begin not in content:
        return False

    head, _, rest = content.partition(begin)
    _, found, tail = rest.partition(end)
    # An unterminated marker means a truncated write; dropping the tail is
    # safer than leaving half a generated blob in the user's instructions.
    remaining = (head + (tail if found else "")).strip()
    if remaining:
        path.write_text(remaining + "\n")
    else:
        path.unlink()
    return True


# ---------------------------------------------------------------------------
# Concrete generators
#
# Every destination below is the user-scope skills directory each tool's own
# documentation names. Where a tool documents a native directory that is its
# own, skills go there so that `dg skills remove --cli <tool>` has exactly
# one thing to undo. Codex is the exception: its only documented user-scope
# location is the cross-tool ~/.agents/skills, and its ~/.codex/skills is
# marked deprecated in Codex's own source.
# ---------------------------------------------------------------------------


class ClaudeCodeGenerator(SkillGenerator):
    """Claude Code — https://code.claude.com/docs/en/skills."""

    cli_name = "claude"
    display_name = "Claude Code"

    def detect(self) -> bool:
        return (
            Path.home().joinpath(".claude").is_dir()
            or shutil.which("claude") is not None
        )

    def skills_root(self) -> Path | None:
        return Path.home() / ".claude" / "skills"

    def legacy_paths(self) -> list[LegacyArtifact]:
        # ~/.claude/commands/ is the single-file prompt directory. Claude
        # Code will not read a references/ folder next to a file there, and
        # a command file does not accept the `name:` key every SKILL.md has.
        return [LegacyArtifact(Path.home() / ".claude" / "commands" / "deepgram")]


class CodexGenerator(SkillGenerator):
    """OpenAI Codex CLI — https://developers.openai.com/codex/skills."""

    cli_name = "codex"
    display_name = "OpenAI Codex"

    def detect(self) -> bool:
        return (
            Path.home().joinpath(".codex").is_dir() or shutil.which("codex") is not None
        )

    def skills_root(self) -> Path | None:
        # Codex documents exactly one user-scope location, the cross-tool
        # one. ~/.codex/skills also loads, but Codex's source marks it
        # "Deprecated user skills location ... kept for backward
        # compatibility", so new installs should not go there.
        return Path.home() / ".agents" / "skills"

    def legacy_paths(self) -> list[LegacyArtifact]:
        # ~/.codex/instructions.md does not appear in current Codex docs or
        # source at all; global instructions are ~/.codex/AGENTS.md. It is
        # treated as shared anyway, in case a user adopted the file.
        return [LegacyArtifact(Path.home() / ".codex" / "instructions.md", shared=True)]


class GeminiGenerator(SkillGenerator):
    """Gemini CLI — google-gemini/gemini-cli docs/cli/skills.md."""

    cli_name = "gemini"
    display_name = "Gemini CLI"

    def detect(self) -> bool:
        return (
            Path.home().joinpath(".gemini").is_dir()
            or shutil.which("gemini") is not None
        )

    def skills_root(self) -> Path | None:
        return Path.home() / ".gemini" / "skills"

    def legacy_paths(self) -> list[LegacyArtifact]:
        # GEMINI.md is a real global context file, which is exactly why
        # deepctl should not be pasting 58 KB of skills into it.
        return [LegacyArtifact(Path.home() / ".gemini" / "GEMINI.md", shared=True)]


class CursorGenerator(SkillGenerator):
    """Cursor — https://cursor.com/docs/context/skills."""

    cli_name = "cursor"
    display_name = "Cursor"

    def detect(self) -> bool:
        return (
            Path.home().joinpath(".cursor").is_dir()
            or shutil.which("cursor") is not None
        )

    def skills_root(self) -> Path | None:
        return Path.home() / ".cursor" / "skills"

    def legacy_paths(self) -> list[LegacyArtifact]:
        # Cursor rules are project-scoped .cursor/rules/*.mdc; user-scope
        # rules are a settings-UI feature, so ~/.cursor/rules/deepctl.mdc
        # was never read by anything.
        return [LegacyArtifact(Path.home() / ".cursor" / "rules" / "deepctl.mdc")]


class OpenCodeGenerator(SkillGenerator):
    """OpenCode — https://opencode.ai/docs/skills."""

    cli_name = "opencode"
    display_name = "OpenCode"

    def detect(self) -> bool:
        return (
            Path.home().joinpath(".opencode").is_dir()
            or Path.home().joinpath(".config", "opencode").is_dir()
            or shutil.which("opencode") is not None
        )

    def skills_root(self) -> Path | None:
        return Path.home() / ".config" / "opencode" / "skills"

    def legacy_paths(self) -> list[LegacyArtifact]:
        return [LegacyArtifact(Path.home() / ".opencode" / "agents.md", shared=True)]


class ClineGenerator(SkillGenerator):
    """Cline — https://docs.cline.bot/features/skills."""

    cli_name = "cline"
    display_name = "Cline"

    def detect(self) -> bool:
        return Path.home().joinpath(".cline").is_dir()

    def skills_root(self) -> Path | None:
        return Path.home() / ".cline" / "skills"

    def legacy_paths(self) -> list[LegacyArtifact]:
        return [LegacyArtifact(Path.home() / ".cline" / "rules" / "deepctl.md")]


class AmazonQGenerator(SkillGenerator):
    """Amazon Q Developer CLI — no skills mechanism to install into.

    Q Developer has custom agents (``~/.aws/amazonq/cli-agents/*.json``)
    and project-scoped ``.amazonq/rules/`` pulled in through an agent's
    ``resources``. Neither is a skills directory, and the
    ``~/.amazonq/rules/deepctl.md`` this command used to write is not a
    path Q reads. So it reports the one-liner instead of writing a file.
    """

    cli_name = "amazonq"
    display_name = "Amazon Q Developer"

    def detect(self) -> bool:
        return Path.home().joinpath(".amazonq").is_dir()

    def legacy_paths(self) -> list[LegacyArtifact]:
        return [LegacyArtifact(Path.home() / ".amazonq" / "rules" / "deepctl.md")]


class AiderGenerator(SkillGenerator):
    """Aider — no skills mechanism; it reads whole files listed in config."""

    cli_name = "aider"
    display_name = "Aider"

    _LEGACY_FILE = Path.home() / ".deepctl" / "skills" / "deepctl-conventions.md"

    def detect(self) -> bool:
        return shutil.which("aider") is not None

    def legacy_paths(self) -> list[LegacyArtifact]:
        return [LegacyArtifact(self._LEGACY_FILE)]

    def clean_legacy(self) -> list[Path]:
        removed = super().clean_legacy()
        self._drop_config_ref()
        return removed

    def _drop_config_ref(self) -> None:
        """Drop the stale read reference from ~/.aider.conf.yml."""
        conf_path = Path.home() / ".aider.conf.yml"
        ref = str(self._LEGACY_FILE)
        try:
            import yaml

            if not conf_path.exists():
                return
            data = yaml.safe_load(conf_path.read_text()) or {}
            read_list = data.get("read", [])
            if isinstance(read_list, list) and ref in read_list:
                read_list.remove(ref)
                data["read"] = read_list
                conf_path.write_text(yaml.dump(data, default_flow_style=False))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Registry of all generators
# ---------------------------------------------------------------------------

_ALL_GENERATORS: list[type[SkillGenerator]] = [
    ClaudeCodeGenerator,
    CodexGenerator,
    GeminiGenerator,
    CursorGenerator,
    OpenCodeGenerator,
    ClineGenerator,
    AmazonQGenerator,
    AiderGenerator,
]


def get_all_generators() -> list[SkillGenerator]:
    """Return instances of all registered generators."""
    return [cls() for cls in _ALL_GENERATORS]


def detect_ai_clis() -> list[SkillGenerator]:
    """Return generators for detected AI CLIs."""
    return [g for g in get_all_generators() if g.detect()]


def installable_generators(
    generators: list[SkillGenerator],
) -> tuple[list[SkillGenerator], list[SkillGenerator]]:
    """Split generators into those with a skills directory and those without."""
    supported = [g for g in generators if g.skills_root() is not None]
    unsupported = [g for g in generators if g.skills_root() is None]
    return supported, unsupported
