# Cross-Platform Development Guide

## Core Principles

1. **Always use `pathlib.Path`** - Never hardcode path separators
2. **Use `platformdirs`** - For OS-appropriate config/cache directories
3. **Test on all platforms** - CI/CD runs on Linux, Windows, macOS
4. **Handle keyring failures** - Gracefully fall back to config file storage
5. **Environment variables** - Primary method for CI/CD configuration

## Key Cross-Platform Patterns in deepctl

### Configuration Paths

```python
# deepctl uses platformdirs for config locations
from platformdirs import user_config_dir

config_dir = Path(user_config_dir("deepctl", "deepgram"))
# macOS: ~/Library/Application Support/deepctl
# Linux: ~/.config/deepctl
# Windows: %APPDATA%\deepctl
```

### Credential Storage

```python
# System keyring with fallback
try:
    keyring.set_password("com.deepgram.dx.deepctl", "api-key.default", api_key)
except Exception:
    # Fall back to config file
    store_in_config_file(api_key)
```

### Environment Variables

deepctl checks these in order:

1. `DEEPGRAM_API_KEY` - Takes precedence (CI-friendly)
2. System keyring - Secure local storage
3. Config file - Fallback option

### Path Handling

```python
# ✅ Correct - used throughout deepctl
config_path = Path.home() / ".config" / "deepctl" / "config.yaml"
audio_file = Path(audio_path).resolve()

# ❌ Wrong - never do this
config_path = "~/.config/deepctl/config.yaml"
audio_file = audio_path.replace("\\", "/")
```

## Platform-Specific Behaviors

### Keyring Access

| Platform | Backend            | Access Method                               |
| -------- | ------------------ | ------------------------------------------- |
| macOS    | Keychain           | No prompts on storage, may prompt on access |
| Windows  | Credential Manager | Transparent access                          |
| Linux    | Secret Service     | Requires running keyring daemon             |

### File Paths

- Windows has 260-char path limit (unless long paths enabled)
- Case sensitivity differs: Windows (no), macOS (configurable), Linux (yes)
- Use `.resolve()` to get absolute paths consistently

## Testing Strategy

### Local Testing

```bash
# Test with different Python versions
uv venv --python 3.10 && uv run pytest
uv venv --python 3.12 && uv run pytest
```

### CI/CD Matrix

Tests run automatically on:

- Ubuntu (latest)
- Windows (latest)
- macOS (latest, both Intel and ARM)
- Python 3.10 through 3.12

## Common Issues and Solutions

1. **Keyring not available**: Automatically falls back to config file
2. **Path too long on Windows**: Use shorter paths or enable long path support
3. **SSL certificates**: Community site URL can be overridden via environment
4. **Home directory**: Always use `Path.home()`, never `~`

## Development Tools

- **uv**: Cross-platform package manager (recommended)
- **pytest**: Cross-platform test runner
- **GitHub Actions**: Multi-platform CI/CD
