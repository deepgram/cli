# Deepgram CLI Test Suite

This is a powerful pytest test suite for the Deepgram CLI project. It includes comprehensive testing capabilities with fixtures, coverage reporting, and various testing utilities.

## Structure

```
tests/
├── conftest.py          # Global fixtures and test configuration
├── unit/                # Unit tests
│   ├── models/         # Model tests
│   ├── commands/       # Command tests
│   ├── core/           # Core functionality tests
│   └── utils/          # Utility tests
├── integration/         # Integration tests
└── fixtures/           # Test data and fixtures
```

## Running Tests

### Using pytest directly [[memory:3804050]]

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=src/deepgram_cli

# Run specific test file
uv run pytest tests/unit/models/test_file_info.py

# Run tests matching a pattern
uv run pytest -k "file_info"

# Run only unit tests
uv run pytest -m unit

# Run with verbose output
uv run pytest -v
```

### Using the test runner script

```bash
# Run all tests
uv run python scripts/test_runner.py test

# Run with full coverage report
uv run python scripts/test_runner.py coverage

# Run only unit tests
uv run python scripts/test_runner.py unit

# Run specific test file
uv run python scripts/test_runner.py specific --file tests/unit/models/test_file_info.py

# Run tests matching pattern
uv run python scripts/test_runner.py test --pattern "file_info"
```

## Test Features

### 1. Fixtures (conftest.py)

The test suite includes powerful fixtures:

- **Test Environment**: Automatic isolation for each test
- **Configuration**: Mock config and auth managers
- **HTTP Mocking**: Mock httpx clients for API testing
- **Sample Data**: Pre-configured test data
- **Validation Helpers**: Pydantic model validation utilities
- **Performance Testing**: Benchmark timers
- **Async Support**: Async testing capabilities

### 2. Test Markers

Available pytest markers:

- `@pytest.mark.unit` - Unit tests
- `@pytest.mark.integration` - Integration tests
- `@pytest.mark.slow` - Slow running tests
- `@pytest.mark.requires_auth` - Tests requiring authentication
- `@pytest.mark.requires_network` - Tests requiring network
- `@pytest.mark.benchmark` - Performance tests

### 3. Coverage Reporting

Coverage reports are generated in multiple formats:

- Terminal output with missing lines
- HTML report in `htmlcov/index.html`
- XML report in `coverage.xml`

View the HTML coverage report:

```bash
open htmlcov/index.html
```

### 4. Configuration

Test configuration is managed through:

- `pytest.ini` - Main pytest configuration
- `pyproject.toml` - Additional pytest settings

## Writing New Tests

### Example Unit Test

```python
import pytest
from deepgram_cli.models import YourModel

class TestYourModel:
    """Test suite for YourModel."""

    @pytest.mark.unit
    def test_model_creation(self):
        """Test basic model creation."""
        model = YourModel(field="value")
        assert model.field == "value"

    @pytest.mark.unit
    @pytest.mark.parametrize("input,expected", [
        ("value1", "result1"),
        ("value2", "result2"),
    ])
    def test_model_behavior(self, input, expected):
        """Test model behavior with different inputs."""
        model = YourModel(field=input)
        assert model.process() == expected
```

### Using Fixtures

```python
def test_with_config(self, mock_config):
    """Test using mock config fixture."""
    assert mock_config.get("current_profile") == "default"

def test_with_client(self, mock_deepgram_client):
    """Test using mock client fixture."""
    result = mock_deepgram_client.transcribe(...)
    assert result is not None
```

## Best Practices

1. **Use Type Hints**: All test parameters and return values should be typed [[memory:3798551]]
2. **Test Isolation**: Each test should be independent
3. **Clear Names**: Test names should describe what they test
4. **Use Fixtures**: Leverage fixtures for common setup
5. **Test Coverage**: Aim for high coverage with meaningful tests [[memory:3797880]]
6. **Parametrize**: Use parametrization for testing multiple cases
7. **Mock External**: Always mock external dependencies

## Current Status

- ✅ Test infrastructure set up
- ✅ Comprehensive fixtures available
- ✅ One example test file (test_file_info.py) with 29 tests
- 📝 Additional tests to be added by developer

## Notes

- Coverage requirement is temporarily set to 20% (should be increased as more tests are added)
- The test suite uses `uv` for dependency management
- All tests run in isolated environments to prevent side effects
