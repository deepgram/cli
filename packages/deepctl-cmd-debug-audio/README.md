# deepctl-cmd-debug-audio

> Part of [deepctl](https://github.com/deepgram/cli) — Official Deepgram CLI

Audio debug subcommand for deepctl

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
| `deepctl debug audio` | `deepctl_cmd_debug_audio.command:AudioCommand` |

## Dependencies

- `click>=8.2.0,<9`
- `rich>=13.0.0,<16`
- `pydantic>=2.0.0,<3`
- `ffmpeg-python>=0.2.0`
- `httpx>=0.24.0,<1`

## License

MIT — see [LICENSE](../../LICENSE)
