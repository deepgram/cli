# deepctl-cmd-api

> Part of [deepctl](https://github.com/deepgram/cli) — Official Deepgram CLI

API command for deepctl — make authenticated REST requests to any Deepgram API endpoint.

## Installation

Installed automatically with deepctl:

```bash
pip install deepctl
```

## Commands

| Command | Entry Point |
|---------|-------------|
| `deepctl api` | `deepctl_cmd_api.command:ApiCommand` |

## Usage

```bash
# List projects
deepctl api /v1/projects

# POST with fields
deepctl api -X POST /v1/projects -f name="My Project"

# Filter with jq
deepctl api /v1/projects --jq '.projects[] | .name'

# Body from stdin
echo '{"text":"hello"}' | deepctl api -X POST /v1/speak --input -
```

## Dependencies

- `click>=8.0.0`
- `rich>=13.0.0`
- `pydantic>=2.0.0`
- `httpx>=0.24.0`

## License

MIT — see [LICENSE](../../LICENSE)
