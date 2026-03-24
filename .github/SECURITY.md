# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in the Deepgram CLI, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, please email [security@deepgram.com](mailto:security@deepgram.com) with:

- A description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

We will acknowledge receipt within 48 hours and provide an estimated timeline for a fix.

## Supported Versions

| Version | Supported |
|---------|-----------|
| Latest  | Yes       |
| < Latest | Best effort |

## Credential Safety

The Deepgram CLI stores credentials in your system keyring (macOS Keychain, Linux Secret Service, Windows Credential Manager). API keys are never written to plaintext config files.

When using environment variables (`DEEPGRAM_API_KEY`), ensure they are not logged or exposed in CI output.
