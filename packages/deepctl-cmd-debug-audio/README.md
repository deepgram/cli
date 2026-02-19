# deepctl-cmd-debug-audio

> Part of [deepctl](https://github.com/deepgram/cli) — Official Deepgram CLI

Audio debug subcommand for deepctl

This is a subcommand of `deepctl debug`.

## Installation

Installed automatically with deepctl:

```bash
pip install deepctl
```

## Commands

| Command | Entry Point |
|---------|-------------|
| `deepctl debug audio` | `deepctl_cmd_debug_audio.command:AudioCommand` |

## Dependencies

- `click>=8.0.0`
- `rich>=13.0.0`
- `pydantic>=2.0.0`
- `ffmpeg-python>=0.2.0`
- `httpx>=0.24.0`

## License

MIT — see [LICENSE](../../LICENSE)
