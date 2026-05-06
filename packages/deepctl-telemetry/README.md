# deepctl-telemetry

> Part of [deepctl](https://github.com/deepgram/cli) — Official Deepgram CLI

Opt-out phone-home telemetry for deepctl

This package provides internal APIs for deepctl and its command packages. It is not intended for direct use.

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

## Dependencies

- `sentry-sdk>=2.0.0`
- `click>=8.0.0`
- `rich>=13.0.0`

## License

MIT — see [LICENSE](../../LICENSE)
