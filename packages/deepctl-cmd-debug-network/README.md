# deepctl-cmd-debug-network

> Part of [deepctl](https://github.com/deepgram/cli) — Official Deepgram CLI

Network debug subcommand for deepctl

This is a subcommand of `deepctl debug`.

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
| `deepctl debug network` | `deepctl_cmd_debug_network.command:NetworkCommand` |

## Dependencies

- `click>=8.0.0`
- `rich>=13.0.0`
- `pydantic>=2.0.0`
- `httpx>=0.24.0`
- `requests>=2.31,<3.0`

## License

MIT — see [LICENSE](../../LICENSE)
