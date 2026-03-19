# Test Suite for ext-3csolotions-s3presignedurl-lambda

Comprehensive test suite covering unit and integration tests for the presigned URL Lambda service.

## Test Structure

```
tests/
├── conftest.py              # Shared fixtures and configuration
├── unit/                    # Unit tests (isolated, fast)
│   ├── test_auth.py         # Authentication middleware tests
│   ├── test_s3helper.py     # S3Helper utility tests
│   └── test_validators.py  # Pydantic input validation tests
└── integration/             # Integration tests (mocked AWS)
    └── test_s3service.py    # S3Service integration tests
```

## Running Tests

### Install Test Dependencies

```bash
pip install -r requirements-test.txt
```

### Run All Tests

```bash
# Run all tests with coverage
pytest

# Run only unit tests
pytest tests/unit/

# Run only integration tests
pytest tests/integration/

# Run specific test file
pytest tests/unit/test_auth.py

# Run specific test
pytest tests/unit/test_auth.py::TestAPIKeyAuthentication::test_valid_api_key_returns_key
```

### Coverage Reports

```bash
# Generate HTML coverage report
pytest --cov=src --cov-report=html

# View report
open htmlcov/index.html  # macOS
start htmlcov/index.html # Windows
xdg-open htmlcov/index.html # Linux
```

### Run with Verbose Output

```bash
pytest -v -s
```

## Test Categories

Tests are marked with categories:

```bash
# Run only unit tests
pytest -m unit

# Run only integration tests
pytest -m integration

# Run only security tests
pytest -m security

# Skip slow tests
pytest -m "not slow"
```

## Test Coverage Goals

- **Overall:** 70%+ coverage required (enforced by pytest.ini)
- **Critical Components:** 90%+ coverage
  - Authentication (test_auth.py)
  - Input validation (test_validators.py)
  - Filename sanitization (test_s3helper.py)

## CI/CD Integration

Add to your CI/CD pipeline:

```yaml
# GitHub Actions example
- name: Run Tests
  run: |
    pip install -r requirements-test.txt
    pytest --cov=src --cov-report=xml

- name: Upload Coverage
  uses: codecov/codecov-action@v3
  with:
    files: ./coverage.xml
```

## Writing New Tests

### Unit Test Example

```python
import pytest
from src.utils.s3helper import S3Helper

def test_sanitize_filename(mock_logger, mock_env):
    """Test description"""
    helper = S3Helper(mock_logger, mock_env)
    result = helper.sanitize_filename("test.pdf")
    assert result == "test.pdf"
```

### Integration Test Example

```python
from moto import mock_s3
from src.adapter.s3service import S3Service

@mock_s3
def test_generate_presigned_url():
    """Test description"""
    service = S3Service(mock_logger, mock_env)
    url = service.generate_presigned_url(params)
    assert "amazonaws.com" in url
```

## Troubleshooting

### Import Errors

If you see `ModuleNotFoundError`, ensure you're running from project root:

```bash
cd ext-3csolotions-s3presignedurl-lambda
pytest
```

### Moto Errors

If AWS mocking fails, ensure moto is installed with all extras:

```bash
pip install 'moto[all]==4.2.9'
```

### Async Test Failures

Ensure pytest-asyncio is installed and asyncio_mode is set in pytest.ini:

```ini
[pytest]
asyncio_mode = auto
```

## Test Maintenance

- Run tests before every commit
- Update tests when changing business logic
- Maintain 70%+ coverage
- Add tests for all bug fixes
- Review coverage reports regularly
