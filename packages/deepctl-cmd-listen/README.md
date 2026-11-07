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

## URL sources

`dg listen <URL>` checks a URL by format only (an http or https scheme and a host). Pass `--check-url` to check that the URL responds first: a HEAD request, then a 1-byte GET if needed. That check sends no Deepgram API key and keeps no cookies, but any `user:pass@` you put in the URL goes with it, as with any fetch.

Status lines and errors show the URL as `scheme://host/…/<last path segment>`, without user info, query string or fragment. In `-o json` and `-o yaml` output, `source_path` keeps the full path (automation needs it to identify the file) but drops the user info, query string and fragment. If your host puts a token in the URL path, treat `source_path` as sensitive. The request to Deepgram always uses the full URL.

## Dependencies

- `click>=8.2.0,<9`
- `rich>=13.0.0,<16`
- `pydantic>=2.0.0,<3`
- `websockets>=13.1,<18`
- `deepgram-captions>=2.0.0`
- `sounddevice>=0.4.6`

## License

MIT — see [LICENSE](../../LICENSE)
