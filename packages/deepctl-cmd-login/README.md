# deepctl-cmd-login

> Part of [deepctl](https://github.com/deepgram/cli) — Official Deepgram CLI

Login command for deepctl

## Installation

Installed automatically with deepctl:

```bash
pip install deepctl
```

## Commands

| Command | Entry Point |
|---------|-------------|
| `deepctl login` | `deepctl_cmd_login.command:LoginCommand` |
| `deepctl logout` | `deepctl_cmd_login.command:LogoutCommand` |
| `deepctl profiles` | `deepctl_cmd_login.command:ProfilesCommand` |

## Dependencies

- `click>=8.0.0`
- `rich>=13.0.0`
- `deepgram-sdk>=6.0.0rc2`
- `pydantic>=2.0.0`

## License

MIT — see [LICENSE](../../LICENSE)
