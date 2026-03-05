# deepctl-cmd-debug-probe

> Part of [deepctl](https://github.com/deepgram/cli) — Official Deepgram CLI

Debug probe subcommand for deepctl — live ffprobe analysis during streaming

This is a subcommand of `deepctl debug`.

## Installation

Installed automatically with deepctl:

```bash
pip install deepctl
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
