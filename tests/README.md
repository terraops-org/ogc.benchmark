# OGC Benchmark Test Suite

This directory contains comprehensive tests for the OGC benchmark tools.

## Test Structure

```
tests/
├── __init__.py              # Test package init
├── conftest.py              # gevent patch first, LOCUST_* env cleared
├── test_utils.py            # Unit tests for utility functions
├── test_wmts_benchmark.py   # Integration tests for WMTS benchmark
├── test_wms_benchmark.py    # Integration tests for WMS benchmark
├── test_wfs_benchmark.py    # Integration tests for WFS benchmark
├── test_exceptions.py       # Tests for custom exceptions
├── test_live_services.py    # Live service tests (network required)
└── README.md               # This file
```

## Test Categories

### Unit Tests
- **test_utils.py**: Tests utility functions like `get_feature_count` and bbox generators
- **test_exceptions.py**: Tests custom exception classes

### Integration Tests
- **test_wmts_benchmark.py**: Tests WMTS User class with mocked services
- **test_wms_benchmark.py**: Tests WMS User class with mocked services
- **test_wfs_benchmark.py**: Tests WFS User class with mocked services

### Live Service Tests
- **test_live_services.py**: Tests against real OGC services (requires network)

## Running Tests

Tests run in the uv environment (`uv sync` once, see the main README).

```bash
make test        # unit tests, no network   (uv run pytest -m "not network")
make test-live   # live PDOK services        (uv run pytest -m network)
make coverage    # unit tests + coverage report in htmlcov/
```

### Run Individual Test Files
```bash
uv run pytest tests/test_utils.py
uv run pytest tests/test_wmts_benchmark.py -v
uv run pytest tests/test_live_services.py -m network
```

`conftest.py` patches gevent before any test module imports `requests` (on Python 3.14+ a late
patch makes urllib3's SSL setup fail with RecursionError) and clears `LOCUST_*` environment
variables so option defaults come from the code. pytest settings live in `pyproject.toml`.

## Test Features

### Mocking Strategy
- **OWS Services**: Mocked using unittest.mock for isolated testing
- **HTTP Responses**: Mocked using responses library for predictable API responses
- **Locust Environment**: Mocked for testing User classes without full Locust setup

### Response Validation Testing
- Tests the new `catch_response` patterns
- Validates OGC exception handling
- Tests content type validation for different service types

### Error Handling Testing
- Connection failures
- Invalid service responses
- Malformed configurations
- OGC service exceptions

### Performance Patterns Testing
- Deterministic random number generation (seeded)
- Bounding box generation algorithms
- Service initialization patterns

## Test Data

Tests use mock data that simulates real OGC service responses:
- WMTS GetCapabilities responses with tile matrix sets
- WMS GetCapabilities responses with layer information
- WFS GetCapabilities responses with feature types
- Various error responses and edge cases

## Best Practices Tested

1. **Modern Locust Patterns**:
   - FastHttpUser usage
   - catch_response error handling
   - Environment variable support
   - Structured error handling

2. **OGC Standards Compliance**:
   - Proper request parameter formatting
   - Content type validation
   - Exception response handling
   - Service metadata parsing

3. **Code Quality**:
   - Error handling robustness
   - Configuration validation
   - Resource cleanup
   - Logging patterns

## Continuous Integration

The test suite is designed to work in CI/CD environments:
- Fast unit tests run on every commit
- Integration tests validate functionality
- Network tests can be run on-demand
- Coverage reports track test completeness

## Extending Tests

When adding new features:
1. Add unit tests for new utility functions
2. Add integration tests for new User class functionality
3. Update live service tests if new services are supported
4. Maintain high test coverage (aim for >90%)