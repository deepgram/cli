# API Key Verification Architecture

## Overview

deepctl validates credentials before executing commands, providing clear feedback when authentication fails.

## Verification Method

The `AuthManager.verify_credentials()` method:

```python
def verify_credentials(
    api_key: Optional[str] = None,
    project_id: Optional[str] = None
) -> Tuple[bool, str, Optional[str]]
```

Returns:

- `success`: Whether credentials are valid
- `message`: Human-readable result message
- `error_type`: Error type (`'auth'`, `'project'`, `'network'`, or `None`)

## Verification Process

1. **Credential Resolution**: Uses provided credentials or stored/environment values
2. **API Request**: `GET https://api.deepgram.com/v1/projects/{project_id}`
3. **Response Handling**:
   - 200: Valid credentials
   - 401: Invalid API key
   - 403: Valid key, no project access
   - 404: Project not found

## Integration Points

### Login Flow

Validates credentials before storage:

```python
success, message, error_type = self.verify_credentials(api_key, project_id)
if not success:
    raise AuthenticationError(f"{error_type} verification failed: {message}")
```

### Command Execution

The `guard()` method verifies before running protected commands:

- Commands with `requires_auth = True` automatically verify
- Provides specific error guidance based on failure type

## Error Handling

| Error Type | Cause                       | User Guidance                               |
| ---------- | --------------------------- | ------------------------------------------- |
| `auth`     | Invalid/expired API key     | Run `deepctl login` to re-authenticate      |
| `project`  | Project not found/no access | Run `deepctl login --project-id <valid-id>` |
| `network`  | Connection issues           | Check internet connection                   |

## CI Mode

Automatically detected when both environment variables are set:

- `DEEPGRAM_API_KEY`
- `DEEPGRAM_PROJECT_ID`

## Benefits

- Early error detection before operations
- Clear distinction between auth and project issues
- CI/CD friendly with environment variables
- Consistent UX across all commands
