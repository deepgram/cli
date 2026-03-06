# deepctl-cmd-debug-probe

> Part of [deepctl](https://github.com/deepgram/cli) — Official Deepgram CLI

Debug probe subcommand for deepctl — live ffprobe analysis during streaming

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
| `deepctl debug probe` | `deepctl_cmd_debug_probe.command:ProbeCommand` |

## Dependencies

- `click>=8.0.0`
- `rich>=13.0.0`
- `pydantic>=2.0.0`
- `aiohttp>=3.8.0`

## License

MIT — see [LICENSE](../../LICENSE)
