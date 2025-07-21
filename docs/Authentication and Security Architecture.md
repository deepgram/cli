# Authentication and Security Architecture

## Overview

deepctl implements secure authentication with credential verification and cross-platform storage.

## Authentication Methods

### 1. Device Flow (Interactive)

- OAuth2 device flow via community.deepgram.com
- Auto-opens browser for authentication
- Returns API key and project ID

### 2. API Key (Direct)

- Via `--api-key` flag or environment variable
- Validates before storage
- Ideal for CI/CD workflows

## Credential Storage

Storage hierarchy (checked in order):

1. **Environment Variables** (highest priority)

   - `DEEPGRAM_API_KEY`
   - `DEEPGRAM_PROJECT_ID`

2. **System Keyring** (secure)

   - macOS: Keychain Access
   - Windows: Credential Manager
   - Linux: Secret Service API
   - Service: `com.deepgram.dx.deepctl`

3. **Config File** (fallback)
   - Location varies by platform (see Cross-Platform Guide)
   - Only stores API keys if keyring unavailable

## Security Features

- API keys encrypted by OS keyring
- Keys masked in output (\*\*\*\*last4)
- Warnings on fallback to file storage
- Automatic migration from old `deepgram` config

## Profile Support

- Multiple profiles for different accounts
- Switch with `--profile` flag
- Separate keyring entries per profile

## Manual Credential Management

### View credentials:

```bash
# macOS
security find-generic-password -s "com.deepgram.dx.deepctl" -w

# Windows (PowerShell)
cmdkey /list | findstr "com.deepgram.dx.deepctl"

# Linux
secret-tool search service com.deepgram.dx.deepctl
```

### Remove credentials:

```bash
deepctl logout
```

## Best Practices

- Use keyring for local development
- Use environment variables for CI/CD
- Rotate API keys regularly
- Create separate profiles for different projects
