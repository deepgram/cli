# deepctl-cmd-mcp

MCP (Model Context Protocol) server command for deepctl, enabling LLM clients to interact with Deepgram's AI assistant service.

## Features

- 🤖 Connect to Deepgram's Gnosis AI service via MCP
- 🔧 Multiple transport modes (stdio, SSE, streamable-http)
- 🔍 Intelligent question answering about Deepgram
- 📚 API specification lookup
- 💻 Code example generation
- 📖 Documentation search

## Installation

This package is installed as part of the deepctl CLI:

```bash
pip install deepgram-cli
```

Or install directly:

```bash
pip install deepctl-cmd-mcp
```

## Usage

Run the MCP server:

```bash
deepctl mcp
```

With options:

```bash
# Use SSE transport on custom port
deepctl mcp --transport sse --port 8080

# Enable debug logging
deepctl mcp --debug

# Use custom API key
deepctl mcp --api-key YOUR_API_KEY
```

## Known Limitations

### Signal Handling in STDIO Mode

When running in STDIO mode (default), you may need to press Ctrl+C twice to stop the server. This is a known limitation of the FastMCP framework. For production deployments, consider using SSE or HTTP transport modes which handle signals more gracefully.

## Transport Modes
