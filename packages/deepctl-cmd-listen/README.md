# deepctl-cmd-listen

> Part of [deepctl](https://github.com/deepgram/cli) — Official Deepgram CLI

Listen (live speech-to-text) command for deepctl

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
| `deepctl listen` | `deepctl_cmd_listen.command:ListenCommand` |

## Dependencies

- `click>=8.0.0`
- `rich>=13.0.0`
- `pydantic>=2.0.0`
- `websockets>=13.1`
- `deepgram-captions>=2.0.0`
- `sounddevice>=0.4.6`
- `numpy>=1.24.0`

## License

MIT — see [LICENSE](../../LICENSE)
