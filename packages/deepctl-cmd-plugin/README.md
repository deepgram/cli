# deepctl-cmd-plugin

> Part of [deepctl](https://github.com/deepgram/cli) — Official Deepgram CLI

Plugin management command for deepctl

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
| `deepctl plugin` | `deepctl_cmd_plugin.command:PluginCommand` |

## Dependencies

- `packaging>=21.0`
- `toml>=0.10.2`

## License

MIT — see [LICENSE](../../LICENSE)
