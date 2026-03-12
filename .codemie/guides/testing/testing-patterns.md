# Testing Patterns

## Quick Summary

pytest patterns: test structure, async testing, mocking (CRITICAL: patch where USED not DEFINED), fixtures, coverage.

**Category**: Development/Testing | **Complexity**: Medium | **Prerequisites**: pytest ^8.3.1, pytest-asyncio ^0.23.7, pytest-cov ^5.0.0, pytest-mock ^3.14.0

---

## 🚨 TESTING POLICY - READ FIRST 🚨

**MANDATORY**: Tests ONLY when EXPLICITLY requested by user.

| ✅ DO | ❌ NEVER DO |
|-------|-------------|
| Write tests when user asks: "write tests", "add tests", "run tests" | Proactively write tests |
| Ask for clarification if unsure | Auto-run tests after implementation |
| | Modify tests without request |

**Rationale**: Focus on immediate task; tests are on-demand.

---

## 🔧 MANDATORY: pytest Framework

**ALWAYS use pytest** (version 8.3.x). Required extensions: pytest-asyncio, pytest-cov, pytest-mock.

❌ **DO NOT use** unittest, nose, or other frameworks.

---

## Test Structure

**Directory Layout**: `tests/` mirrors `src/codemie/` (core/, service/, rest_api/, agents/)

**Discovery Patterns**: Files `test_*.py`, functions `test_*`, classes `Test*`

---

## Pytest Basics

**Arrange-Act-Assert Pattern** (example: tests/codemie/service/assistant/test_assistant_user_mapping_service.py:45-56):
```python
def test_function(service, mock_obj):
    # Arrange: setup data/mocks
    mock_obj.method.return_value = expected
    # Act: execute code
    result = service.method()
    # Assert: verify
    assert result == expected
```

**Assertions**: `assert x == y`, `assert x in collection`, `mock.assert_called_once()`, `mock.assert_called_once_with(args)`

**Parametrization** (example: tests/codemie/service/test_workflow_service.py:76-88):
```python
@pytest.fixture(params=(True, False))
def fixture_with_variants(request):
    return Model(flag=request.param)  # Test runs twice
```

---

## Async Testing

**Mark async tests** with `@pytest.mark.asyncio` (example: tests/codemie/rest_api/routers/test_index.py:56-69)

```python
@pytest.mark.asyncio
async def test_async_function():
    result = await async_operation()
    assert result
```

**Async fixtures**:
```python
@pytest.fixture
async def async_client():
    async with AsyncClient(app=app) as ac:
        yield ac
```

**Event loop fix** (if needed): Add fixture `def anyio_backend(): return 'asyncio'` or set `asyncio_mode = "auto"` in pytest.ini

---

## 🔥 CRITICAL: Mocking Patterns

### ⭐ GOLDEN RULE: Patch Where USED Not DEFINED

**MOST IMPORTANT**: Patch where function is USED, not where DEFINED.

```python
# module_a.py imports: from external_module import external_function
# ✅ CORRECT: Patch where USED
@patch("module_a.external_function")

# ❌ WRONG: Patch where DEFINED (import already resolved)
@patch("external_module.external_function")
```

**Real example** (tests/codemie/rest_api/routers/test_index.py:53-55):
```python
# index.py does: from codemie.service import SettingsService
# ✅ CORRECT:
@patch('codemie.rest_api.routers.index.SettingsService.get_jira_creds')
# ❌ WRONG:
# @patch('codemie.service.SettingsService.get_jira_creds')
```

### MagicMock with Spec

```python
mock_obj = MagicMock(spec=RealClass)  # Type-safe, IDE autocomplete, fail fast on typos
```

### Mock Behaviors

| Pattern | Code |
|---------|------|
| Return value | `mock.method.return_value = "result"` |
| Raise exception | `mock.method.side_effect = ValueError("error")` |
| Multiple calls | `mock.method.side_effect = ["first", "second"]` |
| Dynamic | `mock.method.side_effect = lambda x: x * 2` |

---

## Fixtures

### Scopes

| Scope | Lifetime | Use Case |
|-------|----------|----------|
| `function` (default) | Per test | Test isolation |
| `class` | Per test class | Shared setup |
| `module` | Per file | Expensive setup |
| `session` | Test run | One-time setup |

### Patterns

**Shared fixtures**: Place in `conftest.py` (example: tests/codemie/core/workflow_models/conftest.py:1-19)

**Composition** (tests/codemie/service/assistant/test_assistant_user_mapping_service.py:19-21):
```python
@pytest.fixture
def mock_repo():
    return MagicMock(spec=Repository)

@pytest.fixture
def service(mock_repo):
    return Service(repository=mock_repo)  # Fixture uses other fixture
```

**Parametrization**: See Pytest Basics section above

---

## Coverage

### Commands

| Command | Purpose |
|---------|---------|
| `pytest --cov=codemie --cov-report=html` | HTML report (htmlcov/index.html) |
| `pytest --cov=codemie --cov-report=term-missing` | Terminal report with missing lines |
| `pytest --cov=codemie --cov-fail-under=80` | Fail if below threshold |

**Config**: See pyproject.toml:73-79 for pytest dependencies

---

## Test Organization

### Unit vs Integration

| Type | Scope | Mocking | Example |
|------|-------|---------|---------|
| Unit | Single function/class | Heavy | tests/codemie/core/test_ability.py:9-13 |
| Integration | Multiple components | Minimal | tests/codemie/rest_api/routers/test_index.py:56-69 |

### Markers

```python
@pytest.mark.asyncio  # Async tests
@pytest.mark.slow     # Mark slow tests

# Run: pytest -m asyncio  # Only async
# Run: pytest -m "not slow"  # Skip slow
```

---

## Best Practices

| Practice | ✅ DO | ❌ DON'T |
|----------|-------|----------|
| **Isolation** | Each test independent | Tests share state or depend on order |
| **Assertions** | `assert x == y` (specific) | `assert obj` (unclear) |
| **Setup/Teardown** | Use fixtures with yield | Manual cleanup in tests |

**Fixture with cleanup**:
```python
@pytest.fixture
def temp_file():
    file = create_file()  # Setup
    yield file            # Provide to test
    cleanup_file(file)    # Teardown
```

---

## Anti-Patterns

| ❌ Anti-Pattern | ✅ Fix |
|----------------|--------|
| Patch where DEFINED | Patch where USED (see Mocking section) |
| Tests share state (`self.user = ...`) | Each test independent (use fixtures) |
| Tests depend on execution order | Tests can run in any order |
| Unclear assertions (`assert obj`) | Specific assertions (`assert obj.x == y`) |

---

## Running Tests

| Command | Purpose |
|---------|---------|
| `pytest` | Run all tests |
| `pytest -v` | Verbose output |
| `pytest -s` | Show print statements |
| `pytest tests/path/test_file.py` | Single file |
| `pytest tests/path/test_file.py::test_func` | Single function |
| `pytest -k "pattern"` | Tests matching pattern |
| `pytest -m marker` | Tests with marker (asyncio, slow, etc) |

See Coverage section for coverage commands.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `RuntimeError: Event loop is closed` | Add fixture: `def anyio_backend(): return 'asyncio'` |
| Mock not working | Patch where USED not DEFINED (see Mocking section) |
| `ModuleNotFoundError: No module named 'codemie'` | Run `pip install -e .` or `poetry install` |
| Tests pass individually but fail together | Tests sharing state - ensure isolation |

---

## Related Guides

- API Testing: `.codemie/guides/testing/testing-api-patterns.md`
- Service Testing: `.codemie/guides/testing/testing-service-patterns.md`
- Service Patterns: `.codemie/guides/architecture/service-layer-patterns.md`
- REST API: `.codemie/guides/api/rest-api-patterns.md`

## References

- Test Suite: `tests/` directory
- Config: `pyproject.toml` (pytest dependencies)
- Example Fixtures: `tests/codemie/core/workflow_models/conftest.py`
- Example Tests: `tests/codemie/core/test_ability.py`, `tests/codemie/service/assistant/test_assistant_user_mapping_service.py`
- Docs: https://docs.pytest.org/, https://github.com/pytest-dev/pytest-asyncio
