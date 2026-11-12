# deepctl-core

> Part of [deepctl](https://github.com/deepgram/cli) — Official Deepgram CLI

Core components for deepctl

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

- `click>=8.2.0,<9`
- `deepgram-sdk>=7.7.0,<8`
- `pydantic>=2.0.0,<3`
- `rich>=13.0.0,<16`
- `httpx>=0.24.0,<1`
- `pyjwt>=2.8.0`
- `keyring>=24.0.0,<26`
- `pyyaml>=6.0.0`
- `platformdirs>=3.0.0,<5`
- `packaging>=21.0`
- `websockets>=13.1,<18`

## License

MIT — see [LICENSE](../../LICENSE)
