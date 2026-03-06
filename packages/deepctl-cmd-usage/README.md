# deepctl-cmd-usage

> Part of [deepctl](https://github.com/deepgram/cli) — Official Deepgram CLI

Usage command for deepctl

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
| `deepctl usage` | `deepctl_cmd_usage.command:UsageCommand` |

## Dependencies

- `click>=8.0.0`
- `rich>=13.0.0`
- `deepgram-sdk>=6.0.0rc2`
- `pydantic>=2.0.0`

## License

MIT — see [LICENSE](../../LICENSE)
