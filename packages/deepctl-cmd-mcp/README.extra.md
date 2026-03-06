## MCP Server Setup

The `deepctl mcp` command runs an MCP (Model Context Protocol) proxy that
connects to Deepgram's developer API and exposes tools to AI code editors.

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

Or with an explicit API key:

```json
{
  "mcpServers": {
    "deepgram": {
      "type": "stdio",
      "command": "uvx",
      "args": ["deepctl", "--api-key", "YOUR_KEY", "mcp"]
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

By default the MCP server uses stdio (for editor integration). You can also
run it as an HTTP server:

```bash
# SSE transport
deepctl mcp --transport sse --port 8000
```
