# Testing Service Layer Patterns

## Quick Summary

Service layer testing: business logic isolation, repository mocking, async testing, exception handling, dependency injection with pytest.

**Category**: Testing/Service | **Complexity**: Medium | **Prerequisites**: pytest ^8.3.1, pytest-asyncio ^0.23.7, pytest-mock ^3.14.0

## 🚨 TESTING POLICY

Tests ONLY when **EXPLICITLY requested**: "write tests", "run tests", "add test coverage"
❌ Do NOT proactively write/modify/run tests | ❓ If unsure, ASK

---

## Core Patterns

### Arrange-Act-Assert Pattern
**Reference**: tests/codemie/service/assistant/test_assistant_user_mapping_service.py:45-56

```python
def test_create_or_update_mapping(service, mock_repository, sample_mapping):
    # Arrange - Setup test data and mocks
    mock_repository.create_or_update_mapping.return_value = sample_mapping
    # Act - Execute the code under test
    result = service.create_or_update_mapping("asst-id", "user-id", [])
    # Assert - Verify expectations
    mock_repository.create_or_update_mapping.assert_called_once()
    assert result == sample_mapping
```

### Service Fixtures
**Reference**: tests/codemie/service/assistant/test_assistant_user_mapping_service.py:14-21

```python
@pytest.fixture
def mock_repository():
    return MagicMock(spec=AssistantUserMappingRepository)

@pytest.fixture
def service(mock_repository):
    return AssistantUserMappingService(repository=mock_repository)
```

---

## Repository Mocking

| Pattern | Code | Purpose |
|---------|------|---------|
| **Return Values** | `mock_repository.get_mapping.return_value = sample_mapping` | Mock successful responses |
| **None Returns** | `mock_repository.get_mapping.return_value = None` | Test not found scenarios |
| **Exceptions** | `mock_repository.get_mapping.side_effect = DatabaseError(...)` | Test error handling |
| **Retry Logic** | `mock_repository.create.side_effect = [ConnectionError(), data]` | Test retry on failure |

**Example** (tests/codemie/service/assistant/test_assistant_user_mapping_service.py:14-16):
```python
def test_get_mapping(service, mock_repository, sample_mapping):
    mock_repository.get_mapping.return_value = sample_mapping
    result = service.get_mapping("asst-id", "user-id")
    mock_repository.get_mapping.assert_called_once_with("asst-id", "user-id")
    assert result == sample_mapping
```

---

## Async Service Testing

**Setup**: Use `AsyncMock` instead of `MagicMock` + `@pytest.mark.asyncio` decorator

```python
from unittest.mock import AsyncMock

@pytest.fixture
def mock_async_repository():
    return AsyncMock(spec=AsyncAssistantRepository)

@pytest.fixture
def async_service(mock_async_repository):
    return AsyncAssistantService(repository=mock_async_repository)

@pytest.mark.asyncio
async def test_async_get_assistant(async_service, mock_async_repository):
    mock_async_repository.get_by_id.return_value = {"id": "123", "name": "Test"}
    result = await async_service.get_assistant("123")
    mock_async_repository.get_by_id.assert_called_once_with("123")
    assert result["id"] == "123"
```

---

## Business Logic Testing

**Focus**: Test service logic independently of repository

```python
def test_validate_configuration(service):
    """Test pure business logic without repository."""
    config = {"name": "Test Assistant", "tools": ["Git"], "max_tokens": 1000}
    assert service.validate_configuration(config) is True

    invalid = {"name": "", "tools": [], "max_tokens": -1}
    assert service.validate_configuration(invalid) is False

def test_calculate_permissions(service, mock_repository):
    """Test business rules with mocked data."""
    mock_repository.get_user.return_value = {"role": "admin"}
    permissions = service.calculate_permissions("user-123")
    assert {"read", "write", "delete"}.issubset(permissions)
```

---

## Dependency Injection Testing

**Pattern**: Create fixture per dependency, inject into service fixture

```python
@pytest.fixture
def mock_assistant_repo():
    return MagicMock(spec=AssistantRepository)

@pytest.fixture
def mock_tool_repo():
    return MagicMock(spec=ToolRepository)

@pytest.fixture
def complex_service(mock_assistant_repo, mock_tool_repo):
    return ComplexService(assistant_repo=mock_assistant_repo, tool_repo=mock_tool_repo)

def test_create_with_tools(complex_service, mock_assistant_repo, mock_tool_repo):
    mock_tool_repo.get_tools.return_value = ["Git", "JIRA"]
    mock_assistant_repo.create.return_value = {"id": "123"}

    result = complex_service.create_with_tools("Test", ["Git", "JIRA"])

    assert mock_tool_repo.get_tools.call_count == 1
    assert mock_assistant_repo.create.call_count == 1
    assert result["id"] == "123"
```

---

## Data Fixtures

**Reference**: tests/codemie/service/assistant/test_assistant_user_mapping_service.py:24-34

| Pattern | Use Case | Example |
|---------|----------|---------|
| **Simple Fixture** | Single test object | `@pytest.fixture def sample_mapping(): return AssistantUserMappingSQL(...)` |
| **Factory Pattern** | Multiple variations | `class Factory: @staticmethod def create(**kwargs): ...` |
| **Parametrized Fixture** | Multiple scenarios | `@pytest.fixture(params=[...]) def config(request): return request.param` |

```python
@pytest.fixture
def sample_mapping():
    return AssistantUserMappingSQL(
        id="test-id",
        assistant_id="asst-123",
        user_id="user-456",
        tools_config=[ToolConfig(name="Git", integration_id="git-int")]
    )
```

---

## Exception Testing

| Test Type | Pattern | Example |
|-----------|---------|---------|
| **Validation Errors** | `pytest.raises(ValidationError, match="...")` | Test invalid inputs raise correct errors |
| **Error Propagation** | `mock_repo.method.side_effect = Error()` | Test repository errors propagate |
| **Retry Logic** | `side_effect = [Error(), success_data]` | Test retry on transient failures |

```python
def test_validation_error(service):
    with pytest.raises(ValidationError, match="name is required"):
        service.create_assistant({"description": "No name"})

def test_retry_on_failure(service, mock_repository):
    mock_repository.create.side_effect = [ConnectionError(), {"id": "123"}]
    result = service.create_with_retry({"name": "Test"})
    assert result["id"] == "123"
    assert mock_repository.create.call_count == 2
```

---

## Mock Verification

| Verification Type | Method | Example |
|-------------------|--------|---------|
| **Called Once** | `assert_called_once()` | Verify method called exactly once |
| **Called With** | `assert_called_once_with(args)` | Verify exact arguments |
| **Call Count** | `assert mock.method.call_count == N` | Verify N calls |
| **Any Call** | `assert_any_call(args)` | Verify args in any call |
| **Not Called** | `assert_not_called()` | Verify never called |

```python
def test_verification(service, mock_repository):
    service.create_assistant({"name": "Test"})
    mock_repository.create.assert_called_once_with({"name": "Test"})

def test_batch(service, mock_repository):
    service.process_batch(["item1", "item2", "item3"])
    assert mock_repository.process.call_count == 3
    mock_repository.process.assert_any_call("item1")
```

---

## Service Composition Testing

**Pattern**: Mock each service dependency, verify orchestration logic

```python
@pytest.fixture
def orchestration_service(mock_assistant_service, mock_workflow_service):
    return OrchestrationService(
        assistant_service=mock_assistant_service,
        workflow_service=mock_workflow_service
    )

def test_create_with_workflow(orchestration_service, mock_assistant_service, mock_workflow_service):
    mock_assistant_service.create.return_value = {"id": "asst-123"}
    mock_workflow_service.create_for_assistant.return_value = {"id": "wf-123"}

    result = orchestration_service.create_with_workflow({"name": "Test", "template": "basic"})

    mock_assistant_service.create.assert_called_once()
    mock_workflow_service.create_for_assistant.assert_called_once_with("asst-123", "basic")
    assert result == {"assistant_id": "asst-123", "workflow_id": "wf-123"}
```

---

## Complete Test Suite Example

**Reference**: tests/codemie/service/assistant/test_assistant_user_mapping_service.py

```python
class TestAssistantUserMappingService:
    @pytest.fixture
    def mock_repository(self):
        return MagicMock(spec=AssistantUserMappingRepository)

    @pytest.fixture
    def service(self, mock_repository):
        return AssistantUserMappingService(repository=mock_repository)

    def test_create_success(self, service, mock_repository, sample_mapping):
        mock_repository.create_or_update_mapping.return_value = sample_mapping
        result = service.create_or_update_mapping("asst-123", "user-456", [])
        mock_repository.create_or_update_mapping.assert_called_once()
        assert result == sample_mapping

    def test_get_not_found(self, service, mock_repository):
        mock_repository.get_mapping.return_value = None
        result = service.get_mapping("invalid", "user-456")
        assert result is None

    def test_delete(self, service, mock_repository):
        service.delete_mapping("asst-123", "user-456")
        mock_repository.delete_mapping.assert_called_once_with("asst-123", "user-456")
```

---

## Anti-Patterns

| ❌ Don't | ✅ Do | Why |
|---------|-------|-----|
| Test repository logic in service tests | Test service business logic | Service tests should isolate service layer |
| Make real database calls | Mock repository with `MagicMock(spec=Repo)` | Tests should be fast and isolated |
| Mock every field in response | Mock only fields used in test | Reduces brittleness |
| Forget `spec=` in MagicMock | Always use `spec=ClassName` | Catches typos and interface changes |
| Use `time.sleep()` in async tests | Use `AsyncMock` and `await` | Proper async testing |

**Example - Testing Business Logic, Not Repository**:
```python
# ❌ BAD: Only tests repository was called
def test_create(service, mock_repository):
    mock_repository.create.return_value = {"id": "123"}
    service.create_assistant({"name": "Test"})

# ✅ GOOD: Tests service validation logic
def test_create_validates_name(service):
    with pytest.raises(ValidationError, match="name required"):
        service.create_assistant({"description": "No name"})
```

---

## Next Steps

- **API Testing**: API endpoint patterns → `.codemie/guides/testing/testing-api-patterns.md`
- **Testing Patterns**: General patterns → `.codemie/guides/testing/testing-patterns.md`
- **Service Development**: Service patterns → `.codemie/guides/architecture/service-layer-patterns.md`

---

## References

- **Source**: tests/codemie/service/ (Service test examples)
- **pytest-asyncio**: https://github.com/pytest-dev/pytest-asyncio
- **unittest.mock**: https://docs.python.org/3/library/unittest.mock.html
- **Service Layer**: `.codemie/guides/architecture/service-layer-patterns.md`
