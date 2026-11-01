# deepctl-cmd-mcp

> Part of [deepctl](https://github.com/deepgram/cli) — Official Deepgram CLI

MCP server for deepctl: Deepgram documentation search over stdio

## Installation

This package is included with deepctl and does not need to be installed separately.

### Install deepctl

```bash
# Install with pip
pip install deepctl

# Or install with uv
uv tool install deepctl

# Or install with pipx
pipx install deepctl

# Or run without installing
uvx deepctl --help
pipx run deepctl --help
```

## Commands

| Command | Entry Point |
|---------|-------------|
| `deepctl mcp` | `deepctl_cmd_mcp.command:McpCommand` |

## MCP Server Setup

The `deepctl mcp` command runs an MCP (Model Context Protocol) server over
stdio that gives AI code editors one tool: search of Deepgram's documentation.

### Prerequisites

```bash
# Login to Deepgram (stores credentials in system keyring)
deepctl login

# Or set the environment variable
export DEEPGRAM_API_KEY="your-api-key"
```

### Claude Code

Add to your project's `.mcp.json`:

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

Or pass `DEEPGRAM_API_KEY` through the editor's `env` block, which forwards it
from your shell (a key saved by `deepctl login` takes precedence):

```json
{
  "mcpServers": {
    "deepgram": {
      "type": "stdio",
      "command": "uvx",
      "args": ["deepctl", "mcp"],
      "env": { "DEEPGRAM_API_KEY": "${DEEPGRAM_API_KEY}" }
    }
  }
}
```

Don't write the key itself into `.mcp.json` or any other file you commit.

### Cursor

Add to `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "deepgram": {
      "command": "uvx",
      "args": ["deepctl", "mcp"]
    }
  }
}
```

### Windsurf

Add to `~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "deepgram": {
      "command": "uvx",
      "args": ["deepctl", "mcp"]
    }
  }
}
```

### Alternative: pipx

Replace `uvx` with `pipx run` in any of the above configurations:

```json
{
  "command": "pipx",
  "args": ["run", "deepctl", "mcp"]
}
```

### Alternative: Local development

If running from a local clone of the repository:

```json
{
  "command": "uv",
  "args": ["run", "dg", "mcp"]
}
```

### Transport modes

The MCP server runs over stdio: your editor starts `dg mcp` and talks to it over
stdin and stdout. `--transport sse` was removed in deepctl 0.4.1. `--transport
streamable-http` was never supported. Both exit with an error, so remove them
from your MCP config.

## Dependencies

- `mcp>=1.22.0,<2.0.0`
- `rich>=13.9.4`
- `click>=8.1.7`
- `pydantic>=2.10.1`

## License

MIT — see [LICENSE](../../LICENSE)
