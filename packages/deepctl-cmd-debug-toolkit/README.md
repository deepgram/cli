# deepctl-cmd-debug-toolkit

> Part of [deepctl](https://github.com/deepgram/cli) — Official Deepgram CLI

Toolkit subcommand for dg debug — runs field support scripts from deepgram/support-toolkit

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
| `deepctl debug toolkit` | `deepctl_cmd_debug_toolkit.command:ToolkitCommand` |

## Dependencies

- `httpx>=0.27.0`
- `pydantic>=2.10.1`
- `platformdirs>=4.0.0`
- `rich>=13.9.4`
- `click>=8.1.7`

## License

MIT — see [LICENSE](../../LICENSE)
