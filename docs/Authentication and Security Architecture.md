# Authentication and Security Architecture

## Overview

deepctl implements secure authentication with credential verification, multi-profile support, and cross-platform storage.

## Authentication Methods

### 1. Device Flow (Interactive)

- OAuth2 device flow via community.deepgram.com
- Auto-opens browser for authentication
- Returns API key and project ID

### 2. API Key (Direct)

- Via `--api-key` flag or environment variable
- Validates before storage
- Ideal for CI/CD workflows

## Authentication Precedence

Credentials are resolved in the following order (highest to lowest priority):

1. **Explicit Flags** (highest priority)

   - `--api-key` and `--project-id` flags on commands
   - Take precedence over all other sources

2. **Profile Credentials**

   - Active profile's stored credentials
   - API keys stored in system keyring
   - Project IDs stored in config file

3. **Environment Variables** (lowest priority)
   - `DEEPGRAM_API_KEY`
   - `DEEPGRAM_PROJECT_ID`
   - Used as fallback when no profile credentials exist

When using environment variables or explicit flags, the CLI logs which project is being affected for transparency.

## Multi-Profile Support

### Profile Management

- **Multiple Profiles**: Support for multiple authentication profiles (e.g., work, personal, production)
- **Active Profile**: The currently selected profile used for commands
- **Profile Switching**: Switch between profiles with credential verification

### Profile Commands

```bash
# Login to default profile
deepctl login

# Login to specific profile
deepctl login --profile production

# List all profiles
deepctl profiles --list

# Switch to different profile
deepctl profiles --switch production

# Show current profile
deepctl profiles --current

# Logout from current profile
deepctl logout

# Logout from specific profile
deepctl logout --profile production

# Logout from all profiles
deepctl logout --all
```

### Login Flow

1. **Environment Variable Detection**: Warns if `DEEPGRAM_API_KEY` is set
2. **Existing Profile Check**: Prompts for re-login or new profile creation
3. **Profile Naming**: Prompts for custom profile names when creating additional profiles
4. **Active Profile Update**: Automatically sets the logged-in profile as active

## Credential Storage

### Storage Architecture

1. **API Keys** (sensitive)

   - Stored ONLY in system keyring
   - Never stored in config files (unless keyring unavailable)
   - Service: `com.deepgram.dx.deepctl`
   - Key format: `api-key.<profile-name>`

2. **Project IDs** (non-sensitive)

   - Stored in profile configuration
   - Part of `~/.config/deepctl/config.yaml`

3. **Profile Configuration**
   - Active profile tracking
   - Profile-specific settings
   - Base URLs and preferences

### Platform-Specific Keyring

- **macOS**: Keychain Access
- **Windows**: Credential Manager
- **Linux**: Secret Service API (GNOME Keyring, KWallet)

## Security Features

- API keys encrypted by OS keyring
- Keys masked in output (\*\*\*\*last4)
- Re-authentication required for profile switching
- Warnings on fallback to file storage
- Automatic migration from old `deepgram` config

## Configuration File Structure

```yaml
# ~/.config/deepctl/config.yaml
default_profile: "default"
active_profile: "production" # Currently selected profile
profiles:
  default:
    project_id: "proj-123"
    base_url: "https://api.deepgram.com"
  production:
    project_id: "proj-456"
    base_url: "https://api.deepgram.com"
  personal:
    project_id: "proj-789"
    base_url: "https://api.deepgram.com"
```

## Manual Credential Management

### View credentials:

```bash
# macOS
security find-generic-password -s "com.deepgram.dx.deepctl" -a "api-key.default" -w

# Windows (PowerShell)
cmdkey /list | findstr "com.deepgram.dx.deepctl"

# Linux
secret-tool search service com.deepgram.dx.deepctl username api-key.default
```

### Remove credentials:

```bash
# Remove specific profile
deepctl logout --profile production

# Remove all profiles
deepctl logout --all

# Keep profile config but remove credentials
deepctl logout --keep-config
```

## Best Practices

1. **Local Development**

   - Use profiles for different projects/environments
   - Let deepctl manage credentials via keyring
   - Switch profiles as needed

2. **CI/CD Environments**

   - Use environment variables for simplicity
   - Or use explicit flags for maximum control
   - Avoid storing profiles in CI

3. **Security**

   - Rotate API keys regularly
   - Use separate profiles for different access levels
   - Review stored credentials periodically
   - Use `--keep-config` when rotating keys

4. **Team Workflows**
   - Create profiles matching your environments (dev, staging, prod)
   - Document profile naming conventions
   - Use consistent profile names across team
