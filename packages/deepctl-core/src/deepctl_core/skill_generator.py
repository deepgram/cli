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
    from collections.abc import Callable, Iterable, Sequence

    from deepctl_core.skill_bundle import RepoSkill

# The cross-tool installer that owns the directory conventions this module
# targets. Quoted verbatim to users whose tool has no skills directory yet.
SKILLS_CLI_HINT = "npx skills add deepgram/skills"

#: Filename that marks a directory as a skill.
SKILL_ENTRY_FILE = "SKILL.md"

#: What deepctl <= 0.3.0 could put in ~/.claude/commands/deepgram/: one
#: file per repo skill it knew about. `deepgram.md` is the generated
#: guide, which only a dead `generate()` path ever produced -- listed so
#: a machine that has one is cleaned, not because a release wrote it.
_LEGACY_CLAUDE_COMMAND_FILES = (
    "api.md",
    "docs.md",
    "setup-mcp.md",
    "starters.md",
    "deepgram.md",
)


class SkillOwnershipError(Exception):
    """A destination already exists and deepctl did not put it there.

    Every tool deepctl installs into reads a *shared* skills directory —
    ``~/.claude/skills`` and friends hold skills from the user and from
    other publishers too. deepctl therefore only ever replaces or deletes
    a folder it recorded installing itself, and raises this instead of
    touching anything else.
    """

    def __init__(self, conflicts: Sequence[tuple[str, Path]]) -> None:
        self.conflicts = list(conflicts)
        listing = "\n".join(f"  {name}: {path}" for name, path in self.conflicts)
        super().__init__(
            "Refusing to overwrite skills deepctl did not install:\n"
            f"{listing}\n\n"
            f"Either {_STATE_FILE} has no record of deepctl installing "
            "them, so they belong to you or to another publisher, or the "
            "path is now a symlink, which deepctl never writes through. "
            "Rename or delete them and run the install again. Nothing "
            "was installed."
        )


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


def recorded_skill_paths(state: dict[str, Any], cli_name: str) -> list[str]:
    """Paths ``skills.json`` records deepctl having installed for one tool.

    This is deepctl's only claim of ownership over anything in a tool's
    skills directory. It is a *claim*, not a guarantee — every consumer
    re-checks each path against the tool's skills root before writing to
    it or deleting it, so a hand-edited or stale state file cannot point
    an operation somewhere else.

    A user who deletes ``skills.json`` therefore leaves deepctl unable to
    prove it owns anything: install refuses to overwrite the folders it
    previously wrote, and remove deletes nothing. That is the safe
    direction to fail in — the folders are still there to delete by hand.

    Every shape a hand-edited file can hold is tolerated by claiming
    nothing: ``installed_skills`` set to a list or a string, one tool's
    entry set to a null, ``paths`` that is not a list, and a non-string
    inside it all yield an empty result rather than an exception. Callers
    render tables and loops from this, so raising here turns a bad file
    into an error message naming a Python type instead of the file.
    """
    installed = state.get("installed_skills")
    entry = installed.get(cli_name) if isinstance(installed, dict) else None
    if not isinstance(entry, dict):
        return []
    paths = entry.get("paths")
    if not isinstance(paths, list):
        return []
    return [p for p in paths if isinstance(p, str)]


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
    #: For a directory: the exact filenames deepctl <= 0.3.0 wrote into
    #: it. Only those are deleted, and the directory itself only if that
    #: leaves it empty, so a file the user put alongside survives — even
    #: one with the same extension. Empty means deepctl owned the whole
    #: directory.
    contents: tuple[str, ...] = ()


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

    **Nothing here touches a folder path deepctl did not install.** Those
    directories are shared: ``~/.claude/skills`` holds the user's own
    skills and other publishers' skills next to Deepgram's. So install,
    update and remove all take the paths ``skills.json`` recorded for this
    tool, keep only those that are a direct child of :meth:`skills_root`,
    and work on that set alone. An existing folder deepctl cannot prove it
    installed is never replaced (:class:`SkillOwnershipError`) and never
    deleted.

    Ownership is by path, not by content. A recorded path stays deepctl's
    until ``dg skills remove`` drops the record, so a *folder* someone
    puts back at that path without removing first is replaced like
    deepctl's own — and on a case-insensitive filesystem ``API`` and
    ``api`` are the same path here. The one exception is a **symlink**:
    deepctl never writes or deletes through one, so a recorded path that
    became a symlink stops being deepctl's, install and update refuse it,
    and remove reports it instead of following it. Closing the rest would
    need a fingerprint or a marker file
    inside each installed skill, which also decides whether ``update`` may
    refresh a skill the user has edited; that is a product decision, not a
    detail of this class.
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

    def owned_skill_paths(self, recorded: Iterable[str | Path]) -> list[Path]:
        """The recorded paths this tool may safely write to or delete.

        ``recorded`` comes from :func:`recorded_skill_paths`. Each entry
        has to survive three checks before it counts as deepctl's:

        * it is absolute,
        * its own final component is not a symlink, and
        * it resolves to a *direct child* of this tool's skills root,
          with symlinks followed on both sides.

        Those last two are what make a hand-edited or stale
        ``skills.json`` harmless. The resolved-parent check drops an entry
        pointing at ``~/Documents`` or at
        ``~/.claude/skills/api/../../..``. The symlink check drops a skill
        folder someone replaced with a symlink *wherever it points*:
        resolving alone would let ``skills/api -> skills/my-own-skill``
        pass, because the target is a direct child of the same root, and
        deepctl would then unlink the name and bury their work.

        Only the final component is tested, so a record written through a
        symlinked ancestor -- ``/tmp`` for ``/private/tmp`` on macOS, a
        home directory reached through a link -- still counts as ours.
        """
        root = self.skills_root()
        if root is None:
            return []
        resolved_root = root.expanduser().resolve()
        owned: list[Path] = []
        seen: set[Path] = set()
        for entry in recorded:
            path = Path(entry).expanduser()
            if not path.is_absolute():
                continue
            if path.is_symlink():
                continue
            if path.resolve().parent != resolved_root:
                continue
            if path in seen:
                continue
            seen.add(path)
            owned.append(path)
        return owned

    def installed_skill_paths(self, recorded: Iterable[str | Path]) -> list[Path]:
        """Skill folders deepctl installed for this tool that are still there.

        Deliberately *not* "every folder under the skills root with a
        ``SKILL.md``": that would count the user's own skills, and every
        other publisher's, as Deepgram's.
        """
        return sorted(
            p
            for p in self.owned_skill_paths(recorded)
            if (p / SKILL_ENTRY_FILE).is_file()
        )

    def install_conflicts(
        self,
        skills: list[RepoSkill],
        recorded: Iterable[str | Path] = (),
    ) -> list[Path]:
        """Destinations that already exist and deepctl cannot claim.

        An upstream skill named ``api`` must not quietly replace a folder
        called ``api`` that somebody else wrote.
        """
        root = self.skills_root()
        if root is None:
            return []
        # Compare resolved paths, not the strings: a record written under
        # one spelling of the same directory (/tmp vs /private/tmp, a home
        # reached through a symlink) still describes the folder deepctl
        # installed, and matching on text would call it a stranger's.
        owned = {p.resolve() for p in self.owned_skill_paths(recorded)}
        conflicts: list[Path] = []
        for skill in skills:
            dest = root / skill.name
            if not (dest.exists() or dest.is_symlink()):
                continue
            # A symlink standing where a skill folder belongs is never
            # ours, whatever it points at -- including another folder in
            # this same root, which would otherwise resolve into `owned`.
            if dest.is_symlink() or dest.resolve() not in owned:
                conflicts.append(dest)
        return conflicts

    def install(
        self,
        commands: list[CommandMetadata],  # noqa: ARG002
        version: str,  # noqa: ARG002
        *,
        ref: str | None = None,
        recorded: Iterable[str | Path] = (),
    ) -> list[Path]:
        """Fetch the upstream skills and install them for this tool.

        One tool, one fetch, and **no ownership record written**. Call
        :func:`install_skills_for` instead for anything a user runs:
        looping over this method is what left folders on disk that
        ``skills.json`` did not know about, because the bundle was
        refetched per tool, no destination was checked against the other
        tools', and the state was saved only after the loop.

        Raises:
            SkillFetchError: Upstream could not be fetched or trusted.
            SkillOwnershipError: A destination exists that deepctl did
                not install.
        """
        if self.skills_root() is None:
            self.clean_legacy()
            return []
        return self.install_skills(
            fetch_repo_skills(ref, force=True), recorded=recorded
        )

    def install_skills(
        self,
        skills: list[RepoSkill],
        recorded: Iterable[str | Path] = (),
    ) -> list[Path]:
        """Copy each skill folder into this tool's skills directory.

        Raises:
            SkillOwnershipError: One of the destinations already exists
                and is not recorded as deepctl's. Checked for every skill
                up front, so a collision on the tenth leaves the first
                nine unwritten rather than half-installing.
        """
        root = self.skills_root()
        if root is None:
            return []
        recorded = list(recorded)
        conflicts = self.install_conflicts(skills, recorded)
        if conflicts:
            raise SkillOwnershipError([(self.display_name, p) for p in conflicts])

        self.clean_legacy()
        root.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for skill in skills:
            dest = root / skill.name
            # Replace rather than merge: when a skill drops a reference
            # file upstream it has to disappear here too, or the assistant
            # keeps reading a page that no longer exists. Only reachable
            # for a destination install_conflicts just cleared as ours.
            if dest.is_dir() and not dest.is_symlink():
                shutil.rmtree(dest)
            elif dest.exists() or dest.is_symlink():
                dest.unlink()
            shutil.copytree(skill.path, dest)
            written.append(dest)
        return written

    def prune_retired(
        self,
        recorded: Iterable[str | Path],
        skills: list[RepoSkill],
    ) -> list[Path]:
        """Delete folders deepctl installed that upstream no longer ships.

        Without this, a skill renamed or retired in ``deepgram/skills``
        stays on disk forever: the next install records only the skills
        that exist now, so the leftover drops off the ownership list and
        becomes something deepctl will neither update nor remove — and
        something it would refuse to overwrite if the name ever came back.

        Call it after the install has been recorded, never before. The
        record is what makes the folders just written deepctl's, so it
        has to land first; the cost is that a crash between the two
        leaves a retired folder behind with no record of it. That is the
        cheaper of the two failures — a stale folder the user can delete,
        rather than fourteen fresh ones deepctl would refuse to touch.
        """
        keep = {skill.name for skill in skills}
        pruned: list[Path] = []
        for path in self.owned_skill_paths(recorded):
            if path.name in keep or path.is_symlink() or not path.is_dir():
                continue
            shutil.rmtree(path, ignore_errors=True)
            if not path.exists():
                pruned.append(path)
        return pruned

    def remove(self, recorded: Iterable[str | Path] = ()) -> list[Path]:
        """Remove the skill folders deepctl recorded installing for this tool.

        Only those. A folder deepctl did not install is left alone even
        when it sits in the same directory and looks exactly like a skill,
        because it is somebody else's work. Symlinks never reach this
        method: :meth:`owned_skill_paths` drops them, so deepctl cannot
        delete through one.
        """
        removed = self.clean_legacy()
        for path in self.owned_skill_paths(recorded):
            if not path.exists():
                continue
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                # A recorded destination someone replaced with a plain
                # file. Still deepctl's path, and install would unlink
                # it to write the skill there, so remove has to be able
                # to finish the job too -- otherwise the record can
                # never be cleared and every later remove repeats the
                # same warning with no action that would resolve it.
                try:
                    path.unlink()
                except OSError:
                    pass
            # Both deletions swallow their errors, so ask the filesystem
            # rather than reporting a deletion that did not happen — the
            # caller drops the record on the strength of this list.
            if not path.exists():
                removed.append(path)
        # The skills root itself is left standing even when this emptied
        # it. deepctl did not necessarily create it -- ~/.agents/skills
        # is Codex's and `npx skills add`'s too -- and "only ever touch
        # folders deepctl installed" has to hold for the directory those
        # folders sat in as well. An empty directory costs nothing.
        return removed

    def clean_legacy(self) -> list[Path]:
        """Remove what deepctl <= 0.3.0 wrote for this tool."""
        removed: list[Path] = []
        for artifact in self.legacy_paths():
            if _clean_legacy_artifact(artifact, self._LEGACY_BEGIN, self._LEGACY_END):
                removed.append(artifact.path)
        return removed

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
        if not artifact.contents:
            shutil.rmtree(path, ignore_errors=True)
            return True
        # A directory deepctl <= 0.3.0 created but does not exclusively
        # own: delete only the files it wrote there, and the directory
        # itself only once nothing else is left in it.
        changed = False
        for name in artifact.contents:
            child = path / name
            if child.is_file() and not child.is_symlink():
                child.unlink()
                changed = True
        if not any(path.iterdir()):
            try:
                path.rmdir()
            except OSError:
                pass
        return changed

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
        #
        # Scoped to named filenames, never a `*.md` glob: a slash command
        # the user added here is also a .md file, so a glob would take it.
        return [
            LegacyArtifact(
                Path.home() / ".claude" / "commands" / "deepgram",
                contents=_LEGACY_CLAUDE_COMMAND_FILES,
            )
        ]


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


@dataclass
class SkillInstallReport:
    """What :func:`install_skills_for` did, tool by tool.

    Callers render this; the core never prints. ``conflicts`` and
    ``failures`` are only ever non-empty in best-effort mode, because
    otherwise the corresponding exception is raised instead.
    """

    #: The deepgram/skills revision that was installed.
    ref: str
    #: The bundle that was fetched, empty when nothing needed fetching.
    skills: list[RepoSkill]
    #: cli_name -> the skill folders written for it, in install order.
    written: dict[str, list[Path]]
    #: Selected tools that have no skills directory to install into.
    unsupported: list[SkillGenerator]
    #: (display_name, destination) pairs deepctl refused to overwrite.
    conflicts: list[tuple[str, Path]]
    #: (display_name, error) for tools that raised part-way through.
    failures: list[tuple[str, Exception]]

    @property
    def total_written(self) -> int:
        return sum(len(paths) for paths in self.written.values())


def _ownership_after_failure(
    gen: SkillGenerator,
    skills: list[RepoSkill],
    recorded: Iterable[str | Path],
) -> list[Path]:
    """Every folder of this tool's that deepctl must stay able to touch.

    Used when an install raises part-way: whatever landed at a
    destination the preflight already cleared as ours, plus anything
    previously recorded that is still on disk. Recording less would turn
    a half-written bundle into folders deepctl will neither update nor
    remove, and would later refuse to overwrite.

    Never called for :class:`SkillOwnershipError`, because that is
    raised before the first byte is written and the destinations it
    names are precisely the ones that are *not* deepctl's. A symlink is
    excluded for the same reason: it is never deepctl's, whatever it
    points at, so claiming one would both break that rule and strand the
    record on a path :meth:`SkillGenerator.remove` will not follow.
    """
    root = gen.skills_root()
    landed = (
        {
            root / skill.name
            for skill in skills
            if (root / skill.name).exists() and not (root / skill.name).is_symlink()
        }
        if root is not None
        else set()
    )
    surviving = {p for p in gen.owned_skill_paths(recorded) if p.exists()}
    return sorted(landed | surviving)


def _retire_unsupported(
    unsupported: Iterable[SkillGenerator],
    installed: dict[str, Any],
) -> bool:
    """Clean up after tools deepctl cannot install to, and un-record them.

    Nothing was written for them, so nothing may claim it was: an entry
    here would make ``dg skills list`` show a tool as installed with no
    skills, and ``dg skills update`` chase it every run.

    Returns:
        True when a record was dropped, so the caller knows to save.
    """
    changed = False
    for gen in unsupported:
        try:
            gen.clean_legacy()
        except OSError:
            # Best-effort: these are deepctl <= 0.3.0 leftovers for a tool
            # nothing is being installed to. An unreadable ~/.gemini/GEMINI.md
            # must not abort an install that is about to write real skill
            # folders for every other tool.
            pass
        # `in` rather than the return of pop(): a hand-edited skills.json
        # can hold a null for a tool, and popping that would look like
        # nothing was dropped and leave the record unsaved.
        if gen.cli_name in installed:
            del installed[gen.cli_name]
            changed = True
    return changed


def install_skills_for(
    generators: Sequence[SkillGenerator],
    state: dict[str, Any],
    *,
    commands: list[CommandMetadata],
    version: str,
    ref: str | None = None,
    fetch: Callable[[], list[RepoSkill]] | None = None,
    on_installed: Callable[[SkillGenerator, list[Path]], None] | None = None,
    best_effort: bool = False,
) -> SkillInstallReport:
    """Install the upstream skills for several tools under one contract.

    Every route that writes skill folders goes through here -- ``dg
    skills install`` and ``update``, the post-login prompt, and the
    refresh a plugin change triggers -- so they cannot drift apart on the
    one thing that matters: a folder on disk always has an ownership
    record.

    The contract is:

    * **Fetch once.** One bundle for every tool, so two destinations
      cannot end up holding different revisions.
    * **Preflight every destination first.** A collision in the last tool
      stops the first from being written at all, rather than leaving a
      half-applied update.
    * **Save after each tool.** If a later one fails, the folders already
      written stay deepctl's to update and remove.
    * **Record what landed even on failure**, so a tool that raises
      part-way still owns the folders that exist.
    * **Prune retired skills after recording**, never before: a crash in
      between should leave a stale folder, not an untracked one.

    ``state`` is mutated and saved in place.

    Args:
        generators: The tools to install for. Ones with no skills
            directory are reported in ``unsupported`` and never recorded.
        state: The loaded ``skills.json``.
        commands: Command metadata, for the recorded ``commands_hash``.
        version: The deepctl version to record.
        ref: The deepgram/skills revision, or ``None`` for the default.
        fetch: Overrides how the bundle is obtained, for a caller that
            reports a download failure in its own words. Called at most
            once, and only when there is something to install.
        on_installed: Called with each tool and its folders as that tool
            lands, so a caller can report it before a later tool fails.
        best_effort: Collect conflicts and errors into the report and
            keep going instead of raising, for callers such as login that
            must not fail the command they are attached to.

    Returns:
        A :class:`SkillInstallReport` for the caller to render.

    Raises:
        SkillFetchError: Upstream could not be fetched or trusted. Raised
            in both modes, because nothing has been written yet.
        SkillOwnershipError: A destination exists that deepctl did not
            install. Not raised when ``best_effort`` is set.
    """
    from datetime import datetime, timezone

    from deepctl_core.skill_bundle import resolve_skills_ref

    supported, unsupported = installable_generators(list(generators))
    report = SkillInstallReport(
        ref=resolve_skills_ref(ref),
        skills=[],
        written={},
        unsupported=unsupported,
        conflicts=[],
        failures=[],
    )
    # Not setdefault: a hand-edited skills.json can carry a null or a
    # list here, and every write below would then raise instead of
    # installing. recorded_skill_paths() tolerates the same damage.
    installed = state.get("installed_skills")
    if not isinstance(installed, dict):
        installed = {}
        state["installed_skills"] = installed

    if not supported:
        # Nothing to install means nothing to download.
        if _retire_unsupported(unsupported, installed):
            save_skills_state(state)
        return report

    skills = fetch() if fetch is not None else fetch_repo_skills(ref, force=True)
    report.skills = skills

    targets: list[SkillGenerator] = []
    for gen in supported:
        found = gen.install_conflicts(skills, recorded_skill_paths(state, gen.cli_name))
        if found:
            report.conflicts.extend((gen.display_name, p) for p in found)
        else:
            targets.append(gen)
    if report.conflicts and not best_effort:
        raise SkillOwnershipError(report.conflicts)

    # After the fetch and the preflight, so a download failure or a
    # collision leaves these tools' files alone -- but before the writes,
    # so a tool failing part-way through the loop cannot skip it and
    # leave a stale record behind.
    if _retire_unsupported(unsupported, installed):
        save_skills_state(state)

    commands_hash = _commands_hash(commands)
    now = datetime.now(timezone.utc).isoformat()
    for gen in targets:
        recorded = recorded_skill_paths(state, gen.cli_name)
        try:
            paths = gen.install_skills(skills, recorded)
        except Exception as exc:
            # A collision is raised before anything is written, and the
            # destinations it names are the ones that are NOT deepctl's.
            # Claiming them here would convert a refusal to touch
            # someone else's folder into a record saying it is ours,
            # which the next install would then delete.
            kept = (
                []
                if isinstance(exc, SkillOwnershipError)
                else _ownership_after_failure(gen, skills, recorded)
            )
            if kept:
                installed[gen.cli_name] = {
                    "paths": [str(p) for p in kept],
                    "installed_at": now,
                    "version": version,
                    "commands_hash": commands_hash,
                    "skills_ref": report.ref,
                    "skills": [p.name for p in kept],
                }
                save_skills_state(state)
            report.failures.append((gen.display_name, exc))
            if not best_effort:
                raise
            continue

        installed[gen.cli_name] = {
            "paths": [str(p) for p in paths],
            "installed_at": now,
            "version": version,
            "commands_hash": commands_hash,
            "skills_ref": report.ref,
            "skills": [s.name for s in skills],
        }
        # Record each tool as it lands. If the next one raises -- a
        # read-only mount, a full disk -- the folders already written
        # stay deepctl's to update and remove, instead of becoming
        # unowned litter it will later refuse to touch.
        save_skills_state(state)
        gen.prune_retired(recorded, skills)
        report.written[gen.cli_name] = paths
        # Report each tool as it lands, not once the loop is over: a
        # later tool raising must not hide the ones that did install and
        # are now recorded.
        if on_installed is not None:
            on_installed(gen, paths)

    return report
