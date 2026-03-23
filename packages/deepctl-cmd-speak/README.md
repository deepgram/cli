# deepctl-cmd-speak

> Part of [deepctl](https://github.com/deepgram/cli) — Official Deepgram CLI

Speak (text-to-speech) command for deepctl

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
| `deepctl speak` | `deepctl_cmd_speak.command:SpeakCommand` |

## Dependencies

- `click>=8.0.0`
- `rich>=13.0.0`
- `pydantic>=2.0.0`

## License

MIT — see [LICENSE](../../LICENSE)
