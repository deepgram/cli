# deepctl

> [!WARNING]  
> **Alpha Software**: This CLI is experimental and under active development.

Official Deepgram CLI. Modular Python package with plugin system.

## Development

### Setup

```bash
git clone https://github.com/deepgram/cli && cd cli
uv sync
```

### Run CLI

```bash
uv run deepctl --help
uv run deepctl login
uv run deepctl transcribe audio.wav
```

### Development Commands

```bash
make dev                   # Format, lint, test
make check                 # Format, lint, typecheck (no tests)
make test                  # Run tests
```

### Requirements

- Python 3.10+
- uv package manager
- Cross-platform: Linux, Windows, macOS

### Architecture

<!-- BEGIN:architecture -->
```
cli/
├── src/deepctl/                      # Main CLI entry point
├── packages/
│   ├── deepctl-cmd-api/              # API command for deepctl
│   ├── deepctl-cmd-debug/            # Debug command group for deepctl
│   ├── deepctl-cmd-debug-audio/      # Audio debug subcommand for deepctl
│   ├── deepctl-cmd-debug-browser/    # Browser debug subcommand for deepctl
│   ├── deepctl-cmd-debug-network/    # Network debug subcommand for deepctl
│   ├── deepctl-cmd-debug-probe/      # Debug probe subcommand for deepctl — live ffprobe analysis during streaming
│   ├── deepctl-cmd-ffprobe/          # FFprobe configuration command for deepctl
│   ├── deepctl-cmd-init/             # Init command for deepctl — scaffold Deepgram starter apps
│   ├── deepctl-cmd-login/            # Login command for deepctl
│   ├── deepctl-cmd-mcp/              # MCP proxy command for deepctl — connects to Deepgram's developer API
│   ├── deepctl-cmd-plugin/           # Plugin management command for deepctl
│   ├── deepctl-cmd-projects/         # Projects command for deepctl
│   ├── deepctl-cmd-skills/           # AI coding assistant skill management for deepctl
│   ├── deepctl-cmd-transcribe/       # Transcribe command for deepctl
│   ├── deepctl-cmd-update/           # Update command for deepctl
│   ├── deepctl-cmd-usage/            # Usage command for deepctl
│   ├── deepctl-core/                 # Core components for deepctl
│   ├── deepctl-plugin-example/       # Example plugin for deepctl
│   └── deepctl-shared-utils/         # Shared utilities for deepctl
├── tests/                            # Integration tests
└── Makefile                          # Development tasks
```
<!-- END:architecture -->

### Plugin Development

```python
from deepctl_core.base_command import BaseCommand

class MyCommand(BaseCommand):
    name = "mycommand"
    help = "My custom command"

    def handle(self, config, auth_manager, client, **kwargs):
        pass
```

Register your command in `pyproject.toml` under the `deepctl.plugins` entry point group:

```toml
[project.entry-points."deepctl.plugins"]
mycommand = "my_plugin.command:MyCommand"
```

See [`packages/deepctl-plugin-example`](packages/deepctl-plugin-example).

### Testing

- `tests/` - Integration tests
- `packages/*/tests/unit/` - Unit tests
- Runs on Python 3.10-3.14, Linux/Windows/macOS

## Release

Merging conventional commits to `main` triggers [release-please](https://github.com/googleapis/release-please) to open a release PR. Merging that PR creates a `v*` tag, which triggers the PyPI publish workflow. All packages are version-locked.

## Installation

### Quick Install

**macOS / Linux:**

```bash
curl -fsSL https://deepgram.com/install.sh | sh
```

**Windows (PowerShell):**

```powershell
iwr https://deepgram.com/install.ps1 -useb | iex
```

### Install Options

```bash
# Install a specific version
curl -fsSL https://deepgram.com/install.sh | sh -s -- v0.2.1

# Force reinstall over existing installation
curl -fsSL https://deepgram.com/install.sh | sh -s -- --force

# Both
curl -fsSL https://deepgram.com/install.sh | sh -s -- --force v0.2.1
```

**Windows (PowerShell):**

```powershell
# Install a specific version
$env:DEEPCTL_VERSION='0.2.1'; iwr https://deepgram.com/install.ps1 -useb | iex

# Force reinstall
$env:DEEPCTL_FORCE='1'; iwr https://deepgram.com/install.ps1 -useb | iex
```

### Other Methods

```bash
uv tool install deepctl
pip install deepctl
pipx install deepctl
```

### Try Without Installing

```bash
uv run deepctl --help
pipx run deepctl --help
```

## Usage

### Commands

<!-- BEGIN:commands -->
| Command | Description |
|---------|-------------|
| `deepctl api` | API command for deepctl |
| `deepctl debug audio` | Audio debug subcommand for deepctl |
| `deepctl debug browser` | Browser debug subcommand for deepctl |
| `deepctl debug network` | Network debug subcommand for deepctl |
| `deepctl debug probe` | Debug probe subcommand for deepctl — live ffprobe analysis during streaming |
| `deepctl debug` | Debug command group for deepctl |
| `deepctl ffprobe` | FFprobe configuration command for deepctl |
| `deepctl init` | Init command for deepctl — scaffold Deepgram starter apps |
| `deepctl login` | Login command for deepctl |
| `deepctl logout` | Login command for deepctl |
| `deepctl mcp` | MCP proxy command for deepctl — connects to Deepgram's developer API |
| `deepctl plugin` | Plugin management command for deepctl |
| `deepctl profiles` | Login command for deepctl |
| `deepctl projects` | Projects command for deepctl |
| `deepctl skills` | AI coding assistant skill management for deepctl |
| `deepctl transcribe` | Transcribe command for deepctl |
| `deepctl update` | Update command for deepctl |
| `deepctl usage` | Usage command for deepctl |
<!-- END:commands -->

### Aliases

- `deepctl` (primary)
- `deepgram`
- `dg`

### Plugins

```bash
deepctl plugin search                    # Browse available plugins
deepctl plugin install <package>         # Install a plugin
deepctl plugin list -v                   # List installed plugins (verbose)
deepctl plugin remove <package>          # Remove a plugin
deepctl plugin update <package>          # Update a plugin
```

Plugin installation adapts to how deepctl was installed:

| Install method | Plugin strategy |
|---|---|
| `pip` / `uv` (venv) | Installs into current environment |
| `pipx` | `pipx inject deepctl <plugin>` |
| `uv tool` | `uv tool install deepctl --with <plugin>` |
| Homebrew / system / binary | Isolated venv at `~/.deepctl/plugins/venv/` |
| Development (editable) | Installs into current environment |
| `uvx` / `pipx run` | Not supported (ephemeral) |

Plugins installed into the isolated venv are automatically discovered and loaded on every CLI invocation.

### Configuration

Priority: CLI args > env vars > user config (`~/.config/deepctl/config.yaml` on Linux, `~/Library/Application Support/deepctl/config.yaml` on macOS) > `./deepgram.yaml`

### Output Formats

```bash
deepctl transcribe audio.wav --output json|yaml|table|csv
```

### MCP Server

Use `deepctl mcp` to connect Deepgram's developer tools to your AI code editor.

```bash
# Login first
deepctl login
```

Add to your editor's MCP config (Claude Code `.mcp.json`, Cursor `.cursor/mcp.json`, etc.):

```json
{
  "mcpServers": {
    "deepgram": {
      "type": "stdio",
      "command": "uvx",
      "args": ["deepctl", "mcp"]
    }
  }
}
```

See [`deepctl-cmd-mcp`](packages/deepctl-cmd-mcp) for full setup instructions including Cursor, Windsurf, pipx, and local development.

## Packages

<!-- BEGIN:packages -->
| Package | Description |
|---------|-------------|
| [`deepctl-cmd-api`](packages/deepctl-cmd-api) | API command for deepctl |
| [`deepctl-cmd-debug`](packages/deepctl-cmd-debug) | Debug command group for deepctl |
| [`deepctl-cmd-debug-audio`](packages/deepctl-cmd-debug-audio) | Audio debug subcommand for deepctl |
| [`deepctl-cmd-debug-browser`](packages/deepctl-cmd-debug-browser) | Browser debug subcommand for deepctl |
| [`deepctl-cmd-debug-network`](packages/deepctl-cmd-debug-network) | Network debug subcommand for deepctl |
| [`deepctl-cmd-debug-probe`](packages/deepctl-cmd-debug-probe) | Debug probe subcommand for deepctl — live ffprobe analysis during streaming |
| [`deepctl-cmd-ffprobe`](packages/deepctl-cmd-ffprobe) | FFprobe configuration command for deepctl |
| [`deepctl-cmd-init`](packages/deepctl-cmd-init) | Init command for deepctl — scaffold Deepgram starter apps |
| [`deepctl-cmd-login`](packages/deepctl-cmd-login) | Login command for deepctl |
| [`deepctl-cmd-mcp`](packages/deepctl-cmd-mcp) | MCP proxy command for deepctl — connects to Deepgram's developer API |
| [`deepctl-cmd-plugin`](packages/deepctl-cmd-plugin) | Plugin management command for deepctl |
| [`deepctl-cmd-projects`](packages/deepctl-cmd-projects) | Projects command for deepctl |
| [`deepctl-cmd-skills`](packages/deepctl-cmd-skills) | AI coding assistant skill management for deepctl |
| [`deepctl-cmd-transcribe`](packages/deepctl-cmd-transcribe) | Transcribe command for deepctl |
| [`deepctl-cmd-update`](packages/deepctl-cmd-update) | Update command for deepctl |
| [`deepctl-cmd-usage`](packages/deepctl-cmd-usage) | Usage command for deepctl |
| [`deepctl-core`](packages/deepctl-core) | Core components for deepctl |
| [`deepctl-plugin-example`](packages/deepctl-plugin-example) | Example plugin for deepctl |
| [`deepctl-shared-utils`](packages/deepctl-shared-utils) | Shared utilities for deepctl |
<!-- END:packages -->

## Contributing

1. Fork repository
2. Run `make dev` (formats, lints, tests)
3. Add tests for changes
4. Submit pull request

## Links

- [Documentation](https://developers.deepgram.com/docs/cli)
- [Discord](https://discord.gg/deepgram)
- [Issues](https://github.com/deepgram/cli/issues)

## License

MIT
