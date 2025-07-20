# API Key Verification Architecture

## Overview

The Deepgram CLI implements a robust API key verification system that validates credentials before executing commands and during the login process. This ensures users receive clear, actionable feedback when authentication issues occur.

## Key Components

### 1. Verification Method (`AuthManager.verify_credentials`)

The core verification functionality is implemented in the `AuthManager` class:

```python
def verify_credentials(
    self,
    api_key: Optional[str] = None,
    project_id: Optional[str] = None
) -> Tuple[bool, str, Optional[str]]
```

**Returns:**

- `success` (bool): Whether the credentials are valid
- `message` (str): Human-readable message about the result
- `error_type` (str | None): Type of error ('auth', 'project', 'network', or None)

### 2. Verification Process

1. **Credential Resolution**: Uses provided credentials or falls back to stored/environment values
2. **API Request**: Makes a GET request to `https://api.deepgram.com/v1/projects/{project_id}`
3. **Response Handling**:
   - 200: Success - credentials valid
   - 401: Invalid API key
   - 403: Valid key but no permission for project
   - 404: Project not found
   - Network errors: Connection issues

### 3. Integration Points

#### Login Flow

- Verifies credentials before storing them
- Prevents invalid credentials from being saved
- Provides immediate feedback during login

```python
# In login_with_api_key method
success, message, error_type = self.verify_credentials(api_key, project_id)
if not success:
    raise AuthenticationError(f"{error_type} verification failed: {message}")
```

#### Command Execution

- The `guard()` method now includes verification
- All commands with `requires_auth = True` automatically verify before execution
- Graceful error handling with specific guidance

```python
# In guard method
success, message, error_type = self.verify_credentials()
if not success:
    # Provide specific error messages based on error_type
    raise AuthenticationError(message)
```

## Storage Strategy

### Credential Storage Hierarchy

1. **Environment Variables** (highest priority)

   - `DEEPGRAM_API_KEY`
   - `DEEPGRAM_PROJECT_ID`
   - Ideal for CI/CD environments

2. **System Keyring** (secure storage)

   - Used when available
   - Falls back to config file if keyring fails

3. **Configuration File** (fallback)
   - Stored in platform-specific config directory
   - Profile-based for multiple accounts

### CI Mode Detection

The system detects CI mode when both environment variables are set:

```python
def is_ci_mode(self) -> bool:
    return bool(os.getenv("DEEPGRAM_API_KEY") and os.getenv("DEEPGRAM_PROJECT_ID"))
```

## Error Handling

### Error Types and User Guidance

1. **Authentication Errors** (`error_type: 'auth'`)

   - Invalid or expired API key
   - Guidance: "Run `deepctl login` to re-authenticate"

2. **Project Errors** (`error_type: 'project'`)

   - Project not found or no access
   - Guidance: "Run `deepctl login --project-id <valid-id>` to set a valid project"

3. **Network Errors** (`error_type: 'network'`)
   - Connection issues
   - Guidance: "Check your internet connection"

## Usage Examples

### CLI Authentication

```bash
# Login with API key and project ID
deepctl login --api-key sk-xxx --project-id proj-xxx

# Environment variables (CI mode)
export DEEPGRAM_API_KEY=sk-xxx
export DEEPGRAM_PROJECT_ID=proj-xxx
deepctl transcribe audio.mp3
```

### Programmatic Verification

```python
# Verify stored credentials
success, message, error_type = auth_manager.verify_credentials()

# Verify specific credentials
success, message, error_type = auth_manager.verify_credentials(
    api_key="sk-test",
    project_id="proj-123"
)
```

## Testing

The verification system includes comprehensive unit tests covering:

- Successful verification
- Various error scenarios (401, 403, 404)
- Network error handling
- Credential resolution from storage/environment
- CI mode detection

See `tests/unit/core/test_auth.py` for complete test coverage.

## Benefits

1. **Early Error Detection**: Catches invalid credentials before attempting operations
2. **Clear Error Messages**: Distinguishes between auth and project issues
3. **CI/CD Friendly**: Seamless integration with automated workflows
4. **Secure Storage**: Multiple storage options with appropriate fallbacks
5. **Consistent UX**: Same verification process across all commands
