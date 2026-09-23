# Deepgram CLI

[![Test](https://github.com/deepgram/cli/actions/workflows/test.yml/badge.svg)](https://github.com/deepgram/cli/actions/workflows/test.yml)
[![Version](https://img.shields.io/pypi/v/deepctl)](https://pypi.org/project/deepctl/)
[![Python](https://img.shields.io/pypi/pyversions/deepctl)](https://pypi.org/project/deepctl/)
[![License](https://img.shields.io/github/license/deepgram/cli)](https://github.com/deepgram/cli/blob/main/LICENSE)
```sh
Usage: dg [OPTIONS] COMMAND [ARGS]...

████████████████
██████████████████
████████████████████
█████████████████████
███████      ████████
███████       ███████
             ████████
      ███████████████
    ████████████████
  ████████████████
████████████████

deepctl — Official Deepgram CLI STT · TTS · Audio Intelligence
```

The official Deepgram CLI brings speech-to-text, text-to-speech, audio
intelligence, and project management directly into your terminal. Aliases:
`deepctl`, `deepgram`, `dg`.

## Installation

### Quick Install

**macOS / Linux (Homebrew):**

```bash
# Install and trust only the Deepgram formula.
brew install deepgram/tap/deepgram
```

Homebrew brings in `ffmpeg` and `portaudio` automatically — `dg listen --mic`, `dg debug probe`, and raw audio piping all work without further setup. To upgrade later: `brew upgrade deepgram`.

**macOS / Linux (curl):**

```bash
curl -fsSL https://deepgram.com/install.sh | sh
```

**Windows (PowerShell):**

```powershell
iwr https://deepgram.com/install.ps1 -useb | iex
```

### Package Managers

```bash
pip install deepctl          # pip
uv tool install deepctl      # uv
pipx install deepctl         # pipx
```

### Try Without Installing

```bash
uvx deepctl --help
pipx run deepctl --help
```

## Getting Started

```bash
# Authenticate with Deepgram
dg login

# Transcribe an audio file
dg listen recording.wav

# Text-to-speech (Flux TTS by default)
dg speak "Hello from Deepgram" -o hello.wav

# Live microphone transcription
dg listen --mic

# Analyze text for sentiment and topics
dg read "The product is amazing" --sentiment --topics
```

## Examples

### Speech-to-text

```bash
# File or URL — auto-detected
dg listen keynote.mp3 --diarize --model nova-3
dg listen https://cdn.example.com/podcast.mp3

# Pipe to jq for scripting
dg -o json listen standup.mp3 \
  | jq '.results.channels[0].alternatives[0].transcript'

# Live microphone with interim (partial) results
dg listen --mic --model nova-3 --interim

# Redact sensitive numbers and spell numbers as digits (files or live)
# Flux STT (v2) accepts --redact numbers|aggressive_numbers; v1 also pci, ssn, …
dg listen call.wav --redact numbers --numerals

# Raw audio stream from ffmpeg
ffmpeg -i video.mp4 -f s16le -ar 16000 -ac 1 - \
  | dg listen --encoding linear16
```

### Captions (WebVTT & SRT)

```bash
# Generate a WebVTT file
dg listen keynote.mp3 --webvtt --save-to keynote.vtt

# SRT with speaker labels
dg listen interview.mp3 --srt --diarize --save-to interview.srt

# Stream live captions from the microphone
dg listen --mic --webvtt

# Captions from a video via ffmpeg
ffmpeg -i video.mp4 -f s16le -ar 16000 -ac 1 - \
  | dg listen --encoding linear16 --srt
```

### Text intelligence

```bash
dg read earnings.txt --sentiment --summarize --topics
```

### Text-to-speech

```bash
# Stream directly to a player (Flux TTS streams a WAV; -loglevel error hides
# ffmpeg's cosmetic end-of-stream notice)
dg speak "Hello from Deepgram" | ffplay -loglevel error -nodisp -autoexit -
```

### Account & project management

```bash
dg whoami                                        # Auth status
dg projects --list                               # List projects
dg keys --create --comment 'ci-pipeline' --dry-run  # Dry-run key creation
dg usage --last-month                            # Usage stats
dg requests --status failed --endpoint listen   # Debug failed requests
```

### Raw API & MCP

```bash
# Hit any endpoint directly
dg api /v1/projects --jq '.projects[0].name'

# MCP server for AI editors (Claude Code, Cursor, etc.)
dg mcp --transport sse --port 8000
```

## Features

### Speech-to-Text

Transcribe audio files, URLs, or live microphone input.

```bash
dg listen meeting.wav --diarize --smart-format
dg listen https://example.com/audio.mp3 --model nova-3
dg listen --mic --model nova-3 --language en-US
cat audio.raw | dg listen --encoding linear16 --sample-rate 16000
```

### Text-to-Speech

Convert text to natural speech. Supports file output and piping.

By default `dg speak` uses Flux TTS (`flux-alexis-en`) — the Speak v2 WebSocket
API, which streams and emits raw audio; its `linear16` output is wrapped in a WAV
container so it is directly playable. Pass an `aura-*` model to use the Speak v1
batch REST API instead, which supports containerized formats like MP3.

```bash
# Flux TTS (v2, WebSocket streaming) — the default
dg speak "Hello from Flux" -o hello.wav
# Piped audio is a streaming WAV; -loglevel error hides ffmpeg's cosmetic
# end-of-stream notice (the audio is complete).
dg speak "Hello from Flux" | ffplay -loglevel error -nodisp -autoexit -

# Flux TTS streaming controls (flux-* only): --speed 0.85–1.15 (0.05 steps).
# --expressivity -2..2 is beta; its default 0 is nominal delivery.
dg speak "A little slower" --speed 0.9 --expressivity 1 -o slow.wav

# Aura (v1, batch REST) — opt in with -m aura-*; needed for MP3 output
dg speak "Welcome to Deepgram" -o welcome.mp3 -m aura-2-asteria-en
dg speak --file script.txt -o output.mp3 -m aura-2-luna-en
echo "Hello" | dg speak -o greeting.mp3 -m aura-2-asteria-en

# Aura-2 also has Spanish voices (e.g. aura-2-selena-es); run `dg models`
# for the full, current list.
dg speak "Hola, bienvenido a Deepgram" -o hola.mp3 -m aura-2-selena-es
```

### Text Intelligence

Analyze text for sentiment, summaries, topics, and intents.

```bash
dg read "Customer called about billing" --sentiment --summarize
dg read --file article.txt --topics --intents
cat feedback.txt | dg read --sentiment
```

### Project Management

Manage your Deepgram account from the terminal.

```bash
dg projects --list                        # List projects
dg keys --list                            # List API keys
dg keys --create --comment "staging"      # Create API key
dg members                                # List team members
dg members --invite user@co.com           # Invite member
dg usage --last-month                     # View usage stats
dg billing                                # Check balances
dg requests --limit 20 --status failed    # Request history
dg models --type tts                      # List available models
```

### Direct API Access

Make authenticated requests to any Deepgram endpoint.

```bash
dg api /v1/projects
dg api /v1/projects -X POST -f name="New Project"
dg api /v1/listen -X POST --input audio.wav --jq '.results'
```

### Debugging Tools

Diagnose audio, network, and browser issues.

```bash
dg debug audio --file recording.wav       # Analyze audio compatibility
dg debug network --verbose                # Test connectivity to Deepgram
dg debug probe --port 3100                # Live stream audio analysis
```

### MCP Server

Connect Deepgram tools to AI coding assistants (Claude Code, Cursor, etc.).

```bash
dg mcp                                    # Start MCP server (stdio)
dg mcp --transport sse --port 8000        # SSE transport
```

Add to your editor's MCP config:

```json
{
  "mcpServers": {
    "deepgram": {
      "type": "stdio",
      "command": "dg",
      "args": ["mcp"]
    }
  }
}
```

### AI Tool Integration

Install the Deepgram agent skills from
[`deepgram/skills`](https://github.com/deepgram/skills) into the AI coding
tools on this machine. Each skill is installed as a folder, into the
user-scope skills directory the tool's own documentation names.

```bash
dg skills status                          # Detect AI tools and show their skills directories
dg skills setup                           # Interactive setup wizard
dg skills install --all                   # Install for all detected tools
dg skills list                            # Show what is installed, and from which ref
dg skills update                          # Reinstall from upstream
dg skills remove --all                    # Uninstall (--cli NAME for one tool)
```

| Tool | Skills directory |
| --- | --- |
| Claude Code | `~/.claude/skills/` |
| OpenAI Codex | `~/.agents/skills/` |
| Gemini CLI | `~/.gemini/skills/` |
| Cursor | `~/.cursor/skills/` |
| OpenCode | `~/.config/opencode/skills/` |
| Cline | `~/.cline/skills/` |

Amazon Q Developer and Aider have no skills mechanism, so `dg skills` prints
`npx skills add deepgram/skills` for those rather than writing a file they
would not read.

Installs are pinned to a released `deepgram/skills` tag so the same deepctl
version always installs the same skills. Override with `--ref` or the
`DEEPCTL_SKILLS_REF` environment variable:

```bash
dg skills install --all --ref main        # track the upstream default branch
```

A failed download, an unknown ref, or an upstream manifest that does not match
the directories it lists is a hard failure (exit 1) with nothing written — a
partial install is indistinguishable from a complete one once it is on disk.

**deepctl only ever touches skill folder paths it installed.** Those
directories are shared: your own skills and other publishers' skills live in
them too. So `dg skills` records every folder it writes in
`~/.deepctl/skills/skills.json` and works on that list alone.

- `install`, `update` and `setup` refuse to overwrite a folder that is not on
  the list — if you already have a skill called `api`, the install exits 1 and
  writes nothing, naming the folder so you can rename it.
- `remove` deletes only the recorded folders. An unrelated skill in the same
  directory stays. A recorded folder it *could not* delete — a permission
  error, a read-only mount — stays recorded and `remove` exits 1, so the next
  `remove` or `update` can still reach it. Dropping the record there would
  leave Deepgram's own folders behind with nothing able to touch them.
- `status` counts only the recorded folders, not everything with a `SKILL.md`.
- Those exit codes are for the `dg skills` subcommands. `dg login` offers the
  same install after a successful login, and `dg plugin install/update/remove`
  refreshes what is already installed; both follow the identical ownership
  rules but report a collision or a download failure as a warning and still
  exit 0, because neither should fail over skills. Run `dg skills install` to
  see the error and get the exit code.
- If you delete `skills.json`, deepctl can no longer prove it installed
  anything: `remove` deletes nothing and `install` reports the collision rather
  than reclaiming the folders. Delete them by hand, then install again.
- The list holds *paths*, not fingerprints. Delete a folder deepctl installed
  and put your own folder — or a file — at the same path without running
  `dg skills remove`, and deepctl still counts that path as its own: the next
  `update` replaces it and `remove` deletes it. Where the filesystem ignores
  case, as macOS and Windows do by default, `API` and `api` are the same path
  for this purpose. So run `dg skills remove` first, or drop the entry from
  `skills.json`, before reusing a name deepctl installed under.
- A *symlink* is the exception: deepctl never writes or deletes through one.
  Put a symlink where a recorded skill folder was and that path stops being
  deepctl's — `install` and `update` exit 1 naming it rather than replacing
  it, and `remove` reports where it is, drops it from the list and leaves it
  on disk rather than following it to whatever it points at. Delete the
  symlink yourself to hand the name back; until you do, installing under that
  name keeps failing.

#### Upgrading from deepctl 0.2.16 through 0.3.0

Those versions wrote to paths that are not skills directories, so `install`,
`update`, `setup` and `remove` clear them for the tools that run — a command
that exits early, such as an install that hits a collision or cannot download,
clears nothing. Otherwise
otherwise four stale skills sit next to fourteen fresh ones. This is the one
thing `dg skills` touches outside its own skill folders, and it is scoped to
what 0.3.0 wrote:

| Path | What happens |
| --- | --- |
| `~/.claude/commands/deepgram/` | Deletes the five files 0.3.0 wrote by name; a command you added stays, and the directory goes only if that empties it |
| `~/.codex/instructions.md`, `~/.gemini/GEMINI.md`, `~/.opencode/agents.md` | Cuts out only the section between `<!-- BEGIN deepctl CLI Reference (auto-generated by deepctl) -->` and `<!-- END deepctl CLI Reference -->`; the rest of the file is yours and is kept |
| `~/.cursor/rules/deepctl.mdc`, `~/.cline/rules/deepctl.md`, `~/.amazonq/rules/deepctl.md` | Deleted — 0.3.0 created these files and nothing else writes them |
| `~/.aider.conf.yml` | Drops the stale `read:` entry pointing at deepctl's old conventions file |

deepctl 0.2.15 and earlier wrote one combined file at
`~/.claude/commands/deepctl.md` instead, with no marker around it. Nothing
distinguishes it from a `/deepctl` slash command you wrote yourself, so the
cleanup leaves it alone. Delete it by hand if it is there.

### Starter Apps

Scaffold a new project from Deepgram templates.

```bash
dg init --list                            # Browse templates
dg init node-live-transcription           # Clone and set up
```

## CI / Automation

Every command is CI-friendly. Authentication works via environment variables,
all interactive prompts have flag-based alternatives, and destructive operations
require explicit `--yes`.

```bash
# CI authentication
export DEEPGRAM_API_KEY="your-key"
export DEEPGRAM_PROJECT_ID="your-project-id"

# Non-interactive usage
dg listen recording.wav
dg speak "Deploy complete" -o notification.mp3 -m aura-2-asteria-en
dg keys --create --comment "ci-key" --scopes member
dg keys --delete KEY_ID --yes
dg read --file report.txt --summarize

# Output formats for scripting
dg projects --list -o json
dg keys --list -o csv
dg usage --last-week -o yaml
```

When running in a non-TTY environment (pipes, CI, or AI coding tools), the CLI
automatically switches to structured JSON output with plain-text status messages.

### Exit codes

Since 0.3.0, `dg` exits non-zero when a command fails — branch on the exit
code, not on parsing output:

| Code | Meaning |
| --- | --- |
| `0` | Success |
| `1` | Error — a failed command, a crash, or a usage error (bad flag, unknown command) |
| `2` | Cancelled by the user (Ctrl-C, or declining a confirmation prompt) |

Note that `dg` reports `2` for an interrupt rather than the shell's
conventional `130`, so the code is the same whether the cancellation came from
Ctrl-C or from declining a prompt.

If a CI step relied on `dg` always exiting `0` (every command did, before
0.3.0), it will now fail where it previously passed silently.

### Forcing non-interactive mode

Three explicit ways to skip every prompt and run with defaults — useful from a
real terminal where auto-detection wouldn't otherwise trigger:

```bash
# Global flag (works at any position)
dg --non-interactive listen recording.wav
dg listen --non-interactive recording.wav

# Environment variable (good for whole scripts)
CI=1 dg listen recording.wav
```

Also recognised: `--agent-friendly` (alias intended for AI coding tools — same
effect plus JSON metadata output), and the auto-detected env vars
`CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT`, `CODEX_SANDBOX`, and Aider's
`OR_APP_NAME` / `OR_SITE_URL`.

## Plugins

Extend the CLI with custom commands.

```bash
dg plugin search deepctl-               # Find plugins
dg plugin install <package>              # Install
dg plugin list                           # List installed
dg plugin remove <package>              # Remove
```

Create your own — see the [plugin example](packages/deepctl-plugin-example).

## Configuration

**Priority:** CLI flags > environment variables > profile config > project config

```bash
dg login                                 # Interactive or --api-key
dg login --profile staging --api-key SK  # Named profiles
dg profiles --list                       # List profiles
dg profiles --switch staging             # Switch profile
```

For structured output, use `--output json|yaml|table|csv` after leaf commands
that do not define their own output option, or before any command as a global
flag: `dg --output json <command>`. `dg speak --output FILE` writes audio to
`FILE`, so use `dg --output json speak ...` for Speak's structured output.

## Telemetry

The CLI phones home anonymous error reports to help us catch crashes and regressions before users have to file an issue. It's **on by default** and easy to turn off.

**What's collected:** Python exceptions, stack traces, the CLI version, and the Python runtime. Request bodies, headers, cookies, API keys, email addresses, IP addresses, and usernames are scrubbed before send. No performance traces, no profiling, no replays — errors only.

**Where it goes:** the `dx-cli` Sentry project owned by the Deepgram DX team.

### Opt out

Persistent (recommended):

```bash
dg config set telemetry.enabled false
```

One-shot (CI, scripts, single command):

```bash
DEEPCTL_TELEMETRY_DISABLED=1 dg listen recording.wav
```

### Override the destination

For forks or self-hosted Sentry, point telemetry at your own DSN:

```bash
export DEEPCTL_TELEMETRY_DSN='https://<key>@<your-sentry>/<project>'
```

`DEEPCTL_TELEMETRY_DISABLED` always wins. The default DSN is baked into the package and only used when the override is unset.

## Development

```bash
git clone https://github.com/deepgram/cli && cd cli
uv sync --group dev
make dev                                 # Format + lint + test
make check                               # Format + lint + typecheck (no tests)
```

### Architecture

<!-- BEGIN:architecture -->
```
cli/
├── src/deepctl/                      # Main CLI entry point
├── packages/
│   ├── deepctl-cmd-api/              # API command for deepctl
│   ├── deepctl-cmd-billing/          # Billing command for deepctl
│   ├── deepctl-cmd-completion/       # Shell completion command for deepctl
│   ├── deepctl-cmd-debug/            # Debug command group for deepctl
│   ├── deepctl-cmd-debug-audio/      # Audio debug subcommand for deepctl
│   ├── deepctl-cmd-debug-browser/    # Browser debug subcommand for deepctl
│   ├── deepctl-cmd-debug-network/    # Network debug subcommand for deepctl
│   ├── deepctl-cmd-debug-probe/      # Debug probe subcommand for deepctl — live ffprobe analysis during streaming
│   ├── deepctl-cmd-debug-toolkit/    # Toolkit subcommand for dg debug — runs field support scripts from deepgram/support-toolkit
│   ├── deepctl-cmd-ffprobe/          # FFprobe configuration command for deepctl
│   ├── deepctl-cmd-init/             # Init command for deepctl — scaffold Deepgram starter apps
│   ├── deepctl-cmd-keys/             # API keys management command for deepctl
│   ├── deepctl-cmd-listen/           # Listen (live speech-to-text) command for deepctl
│   ├── deepctl-cmd-login/            # Login command for deepctl
│   ├── deepctl-cmd-mcp/              # MCP proxy command for deepctl — connects to Deepgram's developer API
│   ├── deepctl-cmd-members/          # Members management command for deepctl
│   ├── deepctl-cmd-models/           # Models command for deepctl
│   ├── deepctl-cmd-plugin/           # Plugin management command for deepctl
│   ├── deepctl-cmd-projects/         # Projects command for deepctl
│   ├── deepctl-cmd-read/             # Read (text intelligence) command for deepctl
│   ├── deepctl-cmd-requests/         # Requests history command for deepctl
│   ├── deepctl-cmd-skills/           # AI coding assistant skill management for deepctl
│   ├── deepctl-cmd-speak/            # Speak (text-to-speech) command for deepctl
│   ├── deepctl-cmd-transcribe/       # Transcribe command for deepctl
│   ├── deepctl-cmd-update/           # Update command for deepctl
│   ├── deepctl-cmd-usage/            # Usage command for deepctl
│   ├── deepctl-core/                 # Core components for deepctl
│   ├── deepctl-plugin-example/       # Example plugin for deepctl
│   ├── deepctl-shared-utils/         # Shared utilities for deepctl
│   └── deepctl-telemetry/            # Opt-out phone-home telemetry for deepctl
├── tests/                            # Integration tests
└── Makefile                          # Development tasks
```
<!-- END:architecture -->

### Commands

<!-- BEGIN:commands -->
| Command | Description |
|---------|-------------|
| `deepctl api` | API command for deepctl |
| `deepctl billing` | Billing command for deepctl |
| `deepctl completion` | Shell completion command for deepctl |
| `deepctl debug audio` | Audio debug subcommand for deepctl |
| `deepctl debug browser` | Browser debug subcommand for deepctl |
| `deepctl debug network` | Network debug subcommand for deepctl |
| `deepctl debug probe` | Debug probe subcommand for deepctl — live ffprobe analysis during streaming |
| `deepctl debug toolkit` | Toolkit subcommand for dg debug — runs field support scripts from deepgram/support-toolkit |
| `deepctl debug` | Debug command group for deepctl |
| `deepctl ffprobe` | FFprobe configuration command for deepctl |
| `deepctl init` | Init command for deepctl — scaffold Deepgram starter apps |
| `deepctl keys` | API keys management command for deepctl |
| `deepctl listen` | Listen (live speech-to-text) command for deepctl |
| `deepctl login` | Login command for deepctl |
| `deepctl logout` | Login command for deepctl |
| `deepctl mcp` | MCP proxy command for deepctl — connects to Deepgram's developer API |
| `deepctl members` | Members management command for deepctl |
| `deepctl models` | Models command for deepctl |
| `deepctl plugin` | Plugin management command for deepctl |
| `deepctl profiles` | Login command for deepctl |
| `deepctl projects` | Projects command for deepctl |
| `deepctl read` | Read (text intelligence) command for deepctl |
| `deepctl requests` | Requests history command for deepctl |
| `deepctl skills` | AI coding assistant skill management for deepctl |
| `deepctl speak` | Speak (text-to-speech) command for deepctl |
| `deepctl transcribe` | Transcribe command for deepctl |
| `deepctl update` | Update command for deepctl |
| `deepctl usage` | Usage command for deepctl |
| `deepctl whoami` | Login command for deepctl |
<!-- END:commands -->

### Packages

<!-- BEGIN:packages -->
| Package | Description |
|---------|-------------|
| [`deepctl-cmd-api`](packages/deepctl-cmd-api) | API command for deepctl |
| [`deepctl-cmd-billing`](packages/deepctl-cmd-billing) | Billing command for deepctl |
| [`deepctl-cmd-completion`](packages/deepctl-cmd-completion) | Shell completion command for deepctl |
| [`deepctl-cmd-debug`](packages/deepctl-cmd-debug) | Debug command group for deepctl |
| [`deepctl-cmd-debug-audio`](packages/deepctl-cmd-debug-audio) | Audio debug subcommand for deepctl |
| [`deepctl-cmd-debug-browser`](packages/deepctl-cmd-debug-browser) | Browser debug subcommand for deepctl |
| [`deepctl-cmd-debug-network`](packages/deepctl-cmd-debug-network) | Network debug subcommand for deepctl |
| [`deepctl-cmd-debug-probe`](packages/deepctl-cmd-debug-probe) | Debug probe subcommand for deepctl — live ffprobe analysis during streaming |
| [`deepctl-cmd-debug-toolkit`](packages/deepctl-cmd-debug-toolkit) | Toolkit subcommand for dg debug — runs field support scripts from deepgram/support-toolkit |
| [`deepctl-cmd-ffprobe`](packages/deepctl-cmd-ffprobe) | FFprobe configuration command for deepctl |
| [`deepctl-cmd-init`](packages/deepctl-cmd-init) | Init command for deepctl — scaffold Deepgram starter apps |
| [`deepctl-cmd-keys`](packages/deepctl-cmd-keys) | API keys management command for deepctl |
| [`deepctl-cmd-listen`](packages/deepctl-cmd-listen) | Listen (live speech-to-text) command for deepctl |
| [`deepctl-cmd-login`](packages/deepctl-cmd-login) | Login command for deepctl |
| [`deepctl-cmd-mcp`](packages/deepctl-cmd-mcp) | MCP proxy command for deepctl — connects to Deepgram's developer API |
| [`deepctl-cmd-members`](packages/deepctl-cmd-members) | Members management command for deepctl |
| [`deepctl-cmd-models`](packages/deepctl-cmd-models) | Models command for deepctl |
| [`deepctl-cmd-plugin`](packages/deepctl-cmd-plugin) | Plugin management command for deepctl |
| [`deepctl-cmd-projects`](packages/deepctl-cmd-projects) | Projects command for deepctl |
| [`deepctl-cmd-read`](packages/deepctl-cmd-read) | Read (text intelligence) command for deepctl |
| [`deepctl-cmd-requests`](packages/deepctl-cmd-requests) | Requests history command for deepctl |
| [`deepctl-cmd-skills`](packages/deepctl-cmd-skills) | AI coding assistant skill management for deepctl |
| [`deepctl-cmd-speak`](packages/deepctl-cmd-speak) | Speak (text-to-speech) command for deepctl |
| [`deepctl-cmd-transcribe`](packages/deepctl-cmd-transcribe) | Transcribe command for deepctl |
| [`deepctl-cmd-update`](packages/deepctl-cmd-update) | Update command for deepctl |
| [`deepctl-cmd-usage`](packages/deepctl-cmd-usage) | Usage command for deepctl |
| [`deepctl-core`](packages/deepctl-core) | Core components for deepctl |
| [`deepctl-plugin-example`](packages/deepctl-plugin-example) | Example plugin for deepctl |
| [`deepctl-shared-utils`](packages/deepctl-shared-utils) | Shared utilities for deepctl |
| [`deepctl-telemetry`](packages/deepctl-telemetry) | Opt-out phone-home telemetry for deepctl |
<!-- END:packages -->

## Release

Merging [conventional commits](https://www.conventionalcommits.org/) to `main`
triggers [release-please](https://github.com/googleapis/release-please) to open
a release PR. Merging that PR creates tags and publishes all changed packages to
PyPI. Each package is versioned independently.

## Requirements

- Python 3.10+
- Cross-platform: Linux, Windows, macOS

## Contributing

1. Fork the repository
2. `uv sync --group dev`
3. `make dev` (formats, lints, tests)
4. Submit a pull request

See [CONTRIBUTING.md](CONTRIBUTING.md) for the contributor workflow.

## Links

- [Documentation](https://developers.deepgram.com/docs/cli)
- [API Reference](https://developers.deepgram.com/reference)
- [Discord](https://discord.gg/deepgram)
- [Issues](https://github.com/deepgram/cli/issues)

## License

MIT
