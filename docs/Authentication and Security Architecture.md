# Authentication and Security Architecture

## Overview

The deepctl CLI implements a secure authentication system that prioritizes credential security while maintaining usability across different platforms.

## Authentication Methods

### 1. Device Flow (Web Authentication)

- Uses OAuth2 device flow via community.deepgram.com
- Generates a random client ID for each authentication session
- Opens browser automatically for user convenience
- Returns an access token and project ID

### 2. API Key Authentication

- Direct input via `--api-key` flag
- Validates credentials before storage
- Supports CI/CD workflows via environment variables

## Credential Storage Hierarchy

Credentials are checked in the following order:

1. **Environment Variables** (highest priority)

   - `DEEPGRAM_API_KEY`
   - `DEEPGRAM_PROJECT_ID`
   - Best for CI/CD and temporary overrides

2. **System Keychain/Keyring**

   - Primary storage for API keys
   - Uses OS-specific secure storage:
     - macOS: Keychain Access
     - Windows: Windows Credential Manager
     - Linux: Secret Service API (GNOME Keyring, KDE Wallet)
   - Service name: `com.deepgram.dx.deepctl` (reverse domain notation)
   - Account naming pattern:
     - API keys: `api-key.{profile}`
     - Project IDs: `project-id.{profile}`
   - Example: `com.deepgram.dx.deepctl` / `api-key.default`

3. **Configuration File** (fallback)
   - Only stores non-sensitive data when keyring is available
   - Falls back to storing API keys if keyring unavailable
   - Location varies by platform:
     - macOS: `~/Library/Application Support/deepctl/config.yaml`
     - Linux: `~/.config/deepctl/config.yaml`
     - Windows: `%APPDATA%\deepctl\config.yaml`
   - Automatic migration from old `deepgram` directory to `deepctl`

## Cross-Platform Compatibility

The authentication system is fully cross-platform thanks to the Python `keyring` library, which automatically selects the appropriate secure storage backend for each operating system:

### Platform-Specific Backends

| Platform    | Backend                                    | Storage Location           |
| ----------- | ------------------------------------------ | -------------------------- |
| **macOS**   | `keyring.backends.macOS.Keyring`           | macOS Keychain             |
| **Windows** | `keyring.backends.Windows.WinVaultKeyring` | Windows Credential Manager |
| **Linux**   | `keyring.backends.SecretService.Keyring`   | GNOME Keyring / KDE Wallet |

### Unified Interface

The same code works on all platforms:

```python
# Store credentials (works on macOS, Windows, and Linux)
keyring.set_password("com.deepgram.dx.deepctl", "api-key.default", api_key)

# Retrieve credentials (works on all platforms)
api_key = keyring.get_password("com.deepgram.dx.deepctl", "api-key.default")
```

### Platform-Specific Access

- **macOS**: View in Keychain Access app or `security` command
- **Windows**: View in Credential Manager or `cmdkey` command
- **Linux**: View in Seahorse (GNOME) or KWalletManager (KDE)

### Fallback Behavior

If the system keyring is unavailable:

1. Warning is displayed to the user
2. Credentials stored in config file instead
3. Config location varies by platform:
   - macOS: `~/Library/Application Support/deepctl/config.yaml`
   - Linux: `~/.config/deepctl/config.yaml`
   - Windows: `%APPDATA%\deepctl\config.yaml`

### Testing Cross-Platform

Run the test script to verify keyring behavior:

```bash
uv run python scripts/test_keyring_platforms.py
```

## Security Features

### Keychain Integration

- API keys are encrypted by the OS
- No prompts on storage (standard OS behavior)
- May prompt when accessed by different applications
- Credentials visible in Keychain Access app

### Profile Support

- Each profile stores credentials separately
- Allows multiple Deepgram accounts
- Profile names used as keyring identifiers

### Secure Defaults

- Keyring storage preferred over plaintext
- API keys masked in output (`****` + last 4 chars)
- Warnings when falling back to file storage

## 1Password Integration (Future)

For users preferring 1Password, future integration could include:

```bash
# Store credentials in 1Password
op item create --category=login --title="deepctl-{profile}" \
  --vault="Development" \
  username="api_key" \
  password="{actual_api_key}"

# Retrieve credentials
op item get "deepctl-{profile}" --fields password
```

## Manual Credential Management

### View stored credentials:

**macOS:**

```bash
# View API key
security find-generic-password -a "api-key.default" -s "com.deepgram.dx.deepctl" -w

# View all deepctl credentials
security find-generic-password -s "com.deepgram.dx.deepctl"

# Or use Keychain Access app and search for "com.deepgram.dx.deepctl"
```

**Windows:**

```powershell
# View all credentials for deepctl
cmdkey /list | findstr "com.deepgram.dx.deepctl"

# Or use GUI: Control Panel → User Accounts → Credential Manager → Windows Credentials
# Look for entries starting with "com.deepgram.dx.deepctl"
```

**Linux:**

```bash
# Using secret-tool (GNOME)
secret-tool search service com.deepgram.dx.deepctl

# Or use GUI tools:
# - GNOME: Seahorse (Passwords and Keys)
# - KDE: KWalletManager
```

### Remove credentials:

```bash
deepctl logout

# Or manually in Keychain Access
```

## Best Practices

1. **Production Use**: Always use keyring/keychain storage
2. **CI/CD**: Use environment variables
3. **Development**: Consider using separate profiles
4. **Security**: Rotate API keys regularly
5. **1Password Users**: Can manually copy credentials from Keychain to 1Password

## Implementation Details

The `AuthManager` class handles all authentication:

- Device flow polling with exponential backoff
- Automatic keyring fallback to config file
- Profile-aware credential storage
- SSL verification disabled only for local development
