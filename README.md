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
brew tap deepgram/tap
brew install deepgram
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

# Text-to-speech
dg speak "Hello from Deepgram" -o hello.mp3

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
# Stream directly to a player
dg speak "Hello from Deepgram" | ffplay -nodisp -autoexit -
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

```bash
dg speak "Welcome to Deepgram" -o welcome.mp3
dg speak --file script.txt -o output.mp3 -m aura-2-luna-en
echo "Hello" | dg speak -o greeting.mp3
dg speak "Stream me" | ffplay -nodisp -    # pipe to audio player
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

Automatically detect and configure AI coding assistants with Deepgram skills.

```bash
dg skills status                          # Detect AI tools
dg skills setup                           # Interactive setup wizard
dg skills install --all                   # Install for all detected tools
```

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
dg speak "Deploy complete" -o notification.mp3
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

Output format on any command: `--output json|yaml|table|csv`

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

See [AGENTS.md](AGENTS.md) for detailed architecture and conventions.

## Links

- [Documentation](https://developers.deepgram.com/docs/cli)
- [API Reference](https://developers.deepgram.com/reference)
- [Discord](https://discord.gg/deepgram)
- [Issues](https://github.com/deepgram/cli/issues)

## License

MIT
