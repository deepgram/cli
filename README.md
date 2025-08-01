# deepctl

> [!WARNING]  
> **Alpha Software**: This CLI is experimental and under active development.

Official Deepgram CLI. Modular Python package with plugin system.

## Development

### Setup

```bash
git clone https://github.com/deepgram/cli && cd cli
uv venv && uv pip install -e ".[dev]"
```

### Run CLI

```bash
uv run deepctl --help
uv run deepctl login
uv run deepctl transcribe audio.wav
```

### Development Commands

```bash
make dev                   # Format, lint, test
make test                  # Run tests
uv run tox                 # Test all Python versions
```

### Requirements

- Python 3.10+
- uv package manager
- Cross-platform: Linux, Windows, macOS

### Architecture

```
cli/
├── src/deepctl/           # Main CLI
├── packages/              # Command packages
├── tests/                 # Integration tests
└── Makefile               # Development tasks
```

### Plugin Development

```python
from deepctl_core.base_command import BaseCommand

class MyCommand(BaseCommand):
    name = "mycommand"
    help = "My custom command"

    def handle(self, config, auth_manager, client, **kwargs):
        pass
```

See [`packages/deepctl-plugin-example`](packages/deepctl-plugin-example).

### Testing

- `tests/` - Integration tests
- `packages/*/tests/unit/` - Unit tests
- Runs on Python 3.10-3.12, Linux/Windows/macOS

### Documentation

- [`docs/Quick Start For Contributors.md`](docs/Quick%20Start%20For%20Contributors.md)
- [`docs/Architecture and Design.md`](docs/Architecture%20and%20Design.md)
- [`docs/Testing and Test Strategy.md`](docs/Testing%20and%20Test%20Strategy.md)

## Installation

### Try Without Installing

```bash
uvx deepctl --help
pipx run deepctl --help
```

### Install

```bash
pip install deepctl
uv tool install deepctl
brew install deepctl
```

## Usage

### Commands

```bash
deepctl login
deepctl transcribe audio.wav
deepctl projects list
deepctl usage --month 2024-01
```

### Aliases

- `deepctl` (primary)
- `deepgram`
- `dg`

### Plugins

```bash
deepctl plugin search
deepctl plugin install <package>
deepctl plugin list
```

### Configuration

Priority: CLI args > env vars > `~/.deepgram/config.yaml` > `./deepgram.yaml`

### Output Formats

```bash
deepctl transcribe audio.wav --output json|yaml|table|csv
```

## Contributing

1. Fork repository
2. Run `make dev` (formats, lints, tests)
3. Add tests for changes
4. Submit pull request

## Links

- [Documentation](https://developers.deepgram.com/docs/cli)
- [Discord](https://discord.gg/deepgram)
- [Issues](https://github.com/deepgram/cli/issues)

## License

MIT
