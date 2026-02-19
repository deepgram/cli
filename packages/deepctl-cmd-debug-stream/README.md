# deepctl-cmd-debug-stream

> Part of [deepctl](https://github.com/deepgram/cli) — Official Deepgram CLI

Stream debug subcommand for deepctl — WebSocket proxy for diagnosing audio streaming issues.

This is a subcommand of `deepctl debug`.

## Installation

Installed automatically with deepctl:

```bash
pip install deepctl
```

## Commands

| Command | Entry Point |
|---------|-------------|
| `deepctl debug stream` | `deepctl_cmd_debug_stream.command:StreamCommand` |

## Usage

```bash
# Start proxy with auto-selected port
deepctl debug stream

# Specify port
deepctl debug stream --port 8080

# Custom upstream host
deepctl debug stream --upstream staging-api.deepgram.com

# Increase audio sample size for analysis
deepctl debug stream --sample-size 131072
```

## Dependencies

- `click>=8.0.0`
- `rich>=13.0.0`
- `pydantic>=2.0.0`
- `aiohttp>=3.8.0`

## License

MIT — see [LICENSE](../../LICENSE)
