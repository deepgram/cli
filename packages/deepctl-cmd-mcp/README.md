# deepctl-cmd-mcp

> Part of [deepctl](https://github.com/deepgram/cli) — Official Deepgram CLI

Local stdio MCP server for deepctl: search Deepgram's documentation from AI editors

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

`dg mcp` (also `deepctl mcp`) runs a local MCP (Model Context Protocol) server
over stdio. Your AI editor starts it and gets one tool,
`search_deepgram_knowledge_sources`, which searches Deepgram's documentation
and returns passages with their source URLs. Each search goes to Deepgram's
hosted documentation search and uses your Deepgram API key. `dg mcp` doesn't
transcribe, synthesize speech or call other Deepgram APIs.

### Prerequisites

The API key comes from `dg login` or the `DEEPGRAM_API_KEY` environment
variable. Never put the API key in the MCP config's `args`: a project config
such as `.mcp.json` is usually committed.

```bash
# Log in once (stores the API key in the system keyring)
dg login

# Or set the environment variable in the shell that starts your editor
export DEEPGRAM_API_KEY="your-api-key"
```

Without a key, the server still starts, and each search returns a tool error
that says to run `dg login` or set `DEEPGRAM_API_KEY`. If dg ignored the key in
your config file because other users can change the file or it's owned by
someone else, the error asks a person to fix the file instead; an AI agent
should ask the user.

Claude Desktop passes `env` values in its config literally, so
`"DEEPGRAM_API_KEY": "${DEEPGRAM_API_KEY}"` sends the text `${DEEPGRAM_API_KEY}`
as the key. Use `dg login` instead.

### Claude Code

```bash
claude mcp add deepgram --scope project -- uvx deepctl mcp
```

Or add to your project's `.mcp.json`:

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

### Installed CLI

If `dg` is already installed (Homebrew, the install script, `pip`, `uv tool`
or `pipx`), use it directly in any of the above configurations:

```json
{
  "command": "dg",
  "args": ["mcp"]
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

### Transport

The MCP server runs over stdio only: your editor starts `dg mcp` and talks to
it over stdin and stdout. SSE and HTTP transports aren't supported, and a
`--transport` other than `stdio` exits with an error, so remove it from your
MCP config.

## Dependencies

- `mcp>=1.23.1,<2.0.0`
- `rich>=13.9.4,<16`
- `click>=8.2.0,<9`
- `pydantic>=2.10.1,<3`

## License

MIT — see [LICENSE](../../LICENSE)
