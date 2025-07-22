# Browser Debug Command Architecture

## Overview

The `deepctl debug browser` command provides a comprehensive browser capability checking tool for diagnosing compatibility issues with Deepgram's web-based services. It launches a local web server, serves a debug page, and performs various browser capability checks required for Deepgram's audio and real-time features.

## Architecture

### Components

1. **Command Handler** (`command.py`)

   - Manages the overall flow of the debug process
   - Starts both HTTP and WebSocket servers
   - Handles browser opening and user interaction
   - Collects and displays results

2. **HTML Debug Page** (`static/debug.html`)

   - Self-contained HTML file with inline CSS and JavaScript
   - Performs browser capability checks
   - Communicates results back to CLI via WebSocket
   - Provides visual feedback during testing

3. **Data Models** (`models.py`)
   - `BrowserCapability`: Individual capability check result
   - `BrowserCapabilities`: Complete set of browser capabilities
   - `WebSocketMessage`: Communication protocol between browser and CLI
   - `BrowserDebugResult`: Final result returned by the command

### Workflow

1. User runs `deepctl debug browser`
2. CLI finds an available port (starting from 3000)
3. Starts HTTP server to serve the debug page
4. Starts WebSocket server for bidirectional communication
5. Prompts user to press Enter to open browser (or shows URL if `--no-browser`)
6. Browser loads debug page and connects to WebSocket
7. JavaScript performs capability checks:
   - Web Audio API
   - AudioContext
   - AudioWorklet API
   - WebSocket API
   - Fetch API
   - ES6+ Features
   - DOM APIs
   - Console API
   - Timer APIs
   - Secure Context (HTTPS/localhost)
8. Results are sent back to CLI via WebSocket
9. CLI displays formatted results
10. Browser shows completion message
11. WebSocket connection closes
12. Command completes

## Features

### Browser Capability Checks

The command checks for all critical browser APIs required by Deepgram:

- **Audio APIs**: Essential for audio playback and processing
  - AudioContext/webkitAudioContext
  - AudioWorklet for PCM processing
- **Communication APIs**: Required for real-time streaming
  - WebSocket with binary data support
  - Fetch API for HTTP requests
- **JavaScript Features**: Modern JS requirements
  - ES6+ syntax (async/await, classes, etc.)
  - Typed Arrays for audio data
- **Browser APIs**: Core functionality
  - DOM manipulation
  - Console methods
  - Timers (setTimeout, setInterval)
- **Security**: AudioWorklet requires secure context
  - HTTPS or localhost verification

### Command Options

- `--port, -p`: Specify port (default: auto-select from 3000)
- `--no-browser`: Don't automatically open browser
- `--timeout`: Timeout waiting for browser connection (default: 60s)

### Output Format

Results are displayed in a formatted table showing:

- Feature name
- Support status (✓ or ✗)
- Additional details
- User agent string
- Overall compatibility assessment

## Implementation Details

### Port Selection

The command automatically finds an available port starting from 3000, checking up to 100 consecutive ports. This ensures the debug server can start even if common ports are in use.

### WebSocket Protocol

Messages use JSON format with the following structure:

```json
{
  "type": "capability_check|info|error|warning|complete",
  "message": "Optional human-readable message",
  "data": {
    // Type-specific data
  }
}
```

### Static File Serving

The HTML file is served from the package's static directory using `importlib.resources`.

### Async Implementation

The command uses Python's asyncio for:

- Running WebSocket and HTTP servers concurrently
- Handling multiple WebSocket connections
- Non-blocking server operation

## Testing

Unit tests cover:

- Command initialization and properties
- Port finding functionality
- Browser opening behavior
- No-browser mode
- WebSocket message handling

Tests use mocking to avoid actual server startup and browser launching.

## Security Considerations

- Servers only bind to localhost (not exposed to network)
- No sensitive data is transmitted
- WebSocket uses standard browser security model
- Debug page contains no external dependencies

## Future Enhancements

Potential improvements:

- Export results to file
- Compare against known browser compatibility matrix
- Test specific Deepgram API endpoints
- Performance benchmarking
- Network latency testing
- Microphone access testing
