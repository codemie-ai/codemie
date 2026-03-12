# Testing API Patterns

## Quick Summary

FastAPI endpoint testing patterns using TestClient, authentication mocking, request/response validation, HTTP status code testing, and router-level integration testing with pytest-asyncio.

**Category**: Testing/API
**Complexity**: Medium
**Prerequisites**: pytest, pytest-asyncio, FastAPI TestClient, authentication understanding

## Prerequisites

- **FastAPI**: Web framework for API development
- **pytest**: ^8.3.1 - Core testing framework
- **pytest-asyncio**: ^0.23.7 - Async test support
- **TestClient**: FastAPI's synchronous test client
- **Authentication**: Understanding of JWT/token-based auth

---

## 🚨 TESTING POLICY - READ FIRST 🚨

Tests must be created, modified, or run **ONLY when EXPLICITLY requested by the user**.

- ❌ Do NOT proactively write, modify, or run tests
- ❌ Do NOT suggest running tests unless asked
- ✅ ONLY work on tests when user explicitly requests:
  - "write tests"
  - "run the tests"
  - "add test coverage"
- ❓ If unsure, **ASK FOR CLARIFICATION**

---

## FastAPI TestClient Setup

### Basic TestClient Pattern

**Pattern** (tests/codemie/rest_api/routers/test_assistant.py):

```python
from fastapi.testclient import TestClient
from codemie.rest_api.main import app

# Create test client
client = TestClient(app)

def test_get_endpoint():
    response = client.get("/v1/assistants")
    assert response.status_code == 200
```

### App Client Fixture

**Shared Client** (tests/codemie/rest_api/routers/conftest.py):

```python
import pytest
from fastapi.testclient import TestClient
from codemie.rest_api.main import app

@pytest.fixture(scope="module")
def app_client():
    """Reusable test client for API tests."""
    return TestClient(app)

# Usage in tests
def test_endpoint(app_client):
    response = app_client.get("/v1/assistants")
    assert response.status_code == 200
```

### Async TestClient

```python
from httpx import AsyncClient
import pytest

@pytest.fixture
async def async_client():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac

@pytest.mark.asyncio
async def test_async_endpoint(async_client):
    response = await async_client.get("/v1/assistants")
    assert response.status_code == 200
```

---

## Authentication Testing

### Auth Header Fixtures

**Pattern** (tests/codemie/rest_api/routers/test_index.py:25-40):

```python
@pytest.fixture
def auth_headers():
    """Mock authentication headers."""
    return {
        "Authorization": "Bearer test-token",
        "X-User-Id": "test-user-id"
    }

def test_protected_endpoint(app_client, auth_headers):
    response = app_client.get("/v1/assistants", headers=auth_headers)
    assert response.status_code == 200
```

### Mocking Authentication Dependency

**Pattern** (tests/codemie/rest_api/routers/test_assistant.py):

```python
from unittest.mock import patch, MagicMock

@patch('codemie.rest_api.routers.assistant.authenticate')
def test_create_assistant(mock_auth, app_client):
    # Setup mock user
    mock_user = MagicMock()
    mock_user.id = "user-123"
    mock_user.is_admin = False
    mock_auth.return_value = mock_user

    # Make request
    response = app_client.post(
        "/v1/assistants",
        json={"name": "Test Assistant", "description": "Test"}
    )

    assert response.status_code == 201
    assert response.json()["name"] == "Test Assistant"
```

### Admin-Only Endpoints

```python
@patch('codemie.rest_api.routers.assistant.authenticate')
@patch('codemie.rest_api.routers.assistant.admin_access_only')
def test_admin_endpoint(mock_admin, mock_auth, app_client):
    # Setup admin user
    mock_user = MagicMock(id="admin-123", is_admin=True)
    mock_auth.return_value = mock_user
    mock_admin.return_value = mock_user

    response = app_client.delete("/v1/assistants/test-id")
    assert response.status_code == 200
```

---

## Request/Response Validation

### POST Request Testing

**Pattern** (tests/codemie/rest_api/routers/test_index.py:56-69):

```python
@patch('codemie.rest_api.routers.index._index_unique_check')
@patch('codemie.rest_api.routers.index.SettingsService.get_jira_creds')
@patch('codemie.rest_api.routers.index.JiraDatasourceProcessor')
@pytest.mark.asyncio
async def test_index_knowledge_base_jira(
    mock_worker,
    mock_creds,
    mock_unique_check,
    mock_jira_request,
    auth_headers
):
    # Setup mocks
    mock_worker_instance = MagicMock()
    mock_worker_instance.started_message = "OK"
    mock_worker.return_value = mock_worker_instance
    mock_unique_check.return_value = True

    # Make POST request
    response = app_client.post(
        "/v1/index/knowledge_base/jira",
        json=mock_jira_request.dict(),
        headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json() == {"message": "OK"}
```

### Query Parameter Testing

```python
def test_list_with_query_params(app_client, auth_headers):
    response = app_client.get(
        "/v1/assistants",
        params={"limit": 10, "offset": 0, "sort": "created_at"},
        headers=auth_headers
    )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) <= 10
```

### Path Parameter Testing

```python
def test_get_by_id(app_client, auth_headers):
    assistant_id = "test-assistant-123"

    response = app_client.get(
        f"/v1/assistants/{assistant_id}",
        headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json()["id"] == assistant_id
```

### Request Body Validation

```python
def test_invalid_request_body(app_client, auth_headers):
    # Missing required field
    response = app_client.post(
        "/v1/assistants",
        json={"description": "Missing name field"},
        headers=auth_headers
    )

    assert response.status_code == 422  # Validation error
    assert "name" in response.json()["detail"][0]["loc"]
```

---

## HTTP Status Code Testing

### Success Responses

```python
def test_successful_get(app_client, auth_headers):
    response = app_client.get("/v1/assistants/123", headers=auth_headers)
    assert response.status_code == 200

def test_successful_create(app_client, auth_headers):
    response = app_client.post(
        "/v1/assistants",
        json={"name": "Test", "description": "Test"},
        headers=auth_headers
    )
    assert response.status_code == 201

def test_successful_delete(app_client, auth_headers):
    response = app_client.delete("/v1/assistants/123", headers=auth_headers)
    assert response.status_code == 204
```

### Error Responses

```python
def test_not_found(app_client, auth_headers):
    response = app_client.get("/v1/assistants/nonexistent", headers=auth_headers)
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()

def test_unauthorized(app_client):
    # No auth headers
    response = app_client.get("/v1/assistants")
    assert response.status_code == 401

def test_forbidden(app_client, auth_headers):
    # Non-admin user trying admin endpoint
    response = app_client.delete("/v1/assistants/123", headers=auth_headers)
    assert response.status_code == 403

def test_bad_request(app_client, auth_headers):
    response = app_client.post(
        "/v1/assistants",
        json={"invalid": "data"},
        headers=auth_headers
    )
    assert response.status_code == 422
```

---

## Service Layer Mocking

### Mocking Service Dependencies

**Pattern** (tests/codemie/rest_api/routers/test_assistant.py):

```python
@patch('codemie.rest_api.routers.assistant.AssistantService')
@patch('codemie.rest_api.routers.assistant.authenticate')
def test_create_assistant_with_service_mock(mock_auth, mock_service_class, app_client):
    # Setup auth mock
    mock_user = MagicMock(id="user-123")
    mock_auth.return_value = mock_user

    # Setup service mock
    mock_service = MagicMock()
    mock_service.create_assistant.return_value = {
        "id": "assistant-123",
        "name": "Test Assistant",
        "description": "Test"
    }
    mock_service_class.return_value = mock_service

    # Make request
    response = app_client.post(
        "/v1/assistants",
        json={"name": "Test Assistant", "description": "Test"}
    )

    assert response.status_code == 201
    mock_service.create_assistant.assert_called_once()
```

### Mocking Database Operations

```python
@patch('codemie.rest_api.routers.assistant.get_session')
@patch('codemie.rest_api.routers.assistant.authenticate')
def test_list_assistants(mock_auth, mock_session, app_client):
    # Setup mocks
    mock_user = MagicMock(id="user-123")
    mock_auth.return_value = mock_user

    mock_db = MagicMock()
    mock_session.return_value = mock_db
    mock_db.exec.return_value.all.return_value = [
        {"id": "1", "name": "Assistant 1"},
        {"id": "2", "name": "Assistant 2"}
    ]

    # Make request
    response = app_client.get("/v1/assistants")

    assert response.status_code == 200
    assert len(response.json()) == 2
```

---

## Background Task Testing

### Testing Background Tasks

```python
@patch('codemie.rest_api.routers.assistant.BackgroundTasks.add_task')
@patch('codemie.rest_api.routers.assistant.authenticate')
def test_async_operation(mock_auth, mock_bg_tasks, app_client):
    mock_user = MagicMock(id="user-123")
    mock_auth.return_value = mock_user

    response = app_client.post(
        "/v1/assistants/process",
        json={"assistant_id": "123"}
    )

    assert response.status_code == 202  # Accepted
    mock_bg_tasks.assert_called_once()
```

---

## Response Streaming Testing

### Testing Streaming Endpoints

```python
def test_streaming_response(app_client, auth_headers):
    response = app_client.get(
        "/v1/assistants/123/chat/stream",
        headers=auth_headers,
        stream=True
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream"

    # Collect streamed chunks
    chunks = []
    for chunk in response.iter_lines():
        if chunk:
            chunks.append(chunk.decode())

    assert len(chunks) > 0
```

---

## File Upload/Download Testing

### Testing File Uploads

```python
def test_file_upload(app_client, auth_headers):
    files = {
        "file": ("test.txt", b"Test content", "text/plain")
    }

    response = app_client.post(
        "/v1/files/upload",
        files=files,
        headers=auth_headers
    )

    assert response.status_code == 201
    assert "file_id" in response.json()
```

### Testing File Downloads

```python
def test_file_download(app_client, auth_headers):
    response = app_client.get(
        "/v1/files/test-file-id/download",
        headers=auth_headers
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert len(response.content) > 0
```

---

## Integration Testing Patterns

### Full Request/Response Flow

```python
@patch('codemie.rest_api.routers.workflow.WorkflowExecutionService')
@patch('codemie.rest_api.routers.workflow.authenticate')
@pytest.mark.asyncio
async def test_workflow_execution_flow(mock_auth, mock_service_class, app_client):
    # Setup mocks
    mock_user = MagicMock(id="user-123")
    mock_auth.return_value = mock_user

    mock_service = MagicMock()
    mock_service.execute_workflow.return_value = {
        "execution_id": "exec-123",
        "status": "running"
    }
    mock_service_class.return_value = mock_service

    # Start workflow
    start_response = app_client.post(
        "/v1/workflows/test-workflow/execute",
        json={"input": "test input"},
        headers={"Authorization": "Bearer test-token"}
    )

    assert start_response.status_code == 202
    execution_id = start_response.json()["execution_id"]

    # Check status
    status_response = app_client.get(
        f"/v1/workflows/executions/{execution_id}",
        headers={"Authorization": "Bearer test-token"}
    )

    assert status_response.status_code == 200
    assert status_response.json()["status"] == "running"
```

---

## Error Handling Testing

### Testing Exception Handling

```python
@patch('codemie.rest_api.routers.assistant.AssistantService')
@patch('codemie.rest_api.routers.assistant.authenticate')
def test_service_error_handling(mock_auth, mock_service_class, app_client):
    mock_user = MagicMock(id="user-123")
    mock_auth.return_value = mock_user

    mock_service = MagicMock()
    mock_service.create_assistant.side_effect = ValueError("Invalid configuration")
    mock_service_class.return_value = mock_service

    response = app_client.post(
        "/v1/assistants",
        json={"name": "Test", "description": "Test"}
    )

    assert response.status_code == 400
    assert "Invalid configuration" in response.json()["detail"]
```

---

## Examples

### Example 1: Complete CRUD Testing

```python
@patch('codemie.rest_api.routers.assistant.AssistantService')
@patch('codemie.rest_api.routers.assistant.authenticate')
class TestAssistantCRUD:
    """Complete CRUD operation testing."""

    def test_create(self, mock_auth, mock_service_class, app_client):
        # Setup
        mock_user = MagicMock(id="user-123")
        mock_auth.return_value = mock_user

        mock_service = MagicMock()
        mock_service.create.return_value = {"id": "1", "name": "Test"}
        mock_service_class.return_value = mock_service

        # Create
        response = app_client.post(
            "/v1/assistants",
            json={"name": "Test", "description": "Test"}
        )
        assert response.status_code == 201

    def test_read(self, mock_auth, mock_service_class, app_client):
        # Setup
        mock_user = MagicMock(id="user-123")
        mock_auth.return_value = mock_user

        mock_service = MagicMock()
        mock_service.get.return_value = {"id": "1", "name": "Test"}
        mock_service_class.return_value = mock_service

        # Read
        response = app_client.get("/v1/assistants/1")
        assert response.status_code == 200

    def test_update(self, mock_auth, mock_service_class, app_client):
        # Update
        response = app_client.put(
            "/v1/assistants/1",
            json={"name": "Updated", "description": "Updated"}
        )
        assert response.status_code == 200

    def test_delete(self, mock_auth, mock_service_class, app_client):
        # Delete
        response = app_client.delete("/v1/assistants/1")
        assert response.status_code == 204
```

---

## Anti-Patterns

### ❌ Not Mocking External Dependencies

```python
# BAD: Makes real database calls in tests
def test_create_assistant(app_client):
    response = app_client.post("/v1/assistants", json={...})
    # Touches real database

# GOOD: Mocks service layer
@patch('codemie.rest_api.routers.assistant.AssistantService')
def test_create_assistant(mock_service, app_client):
    mock_service.return_value.create.return_value = {...}
    response = app_client.post("/v1/assistants", json={...})
```

### ❌ Not Testing Error Cases

```python
# BAD: Only tests happy path
def test_get_assistant(app_client):
    response = app_client.get("/v1/assistants/123")
    assert response.status_code == 200

# GOOD: Tests both success and failure
def test_get_assistant_success(app_client):
    response = app_client.get("/v1/assistants/123")
    assert response.status_code == 200

def test_get_assistant_not_found(app_client):
    response = app_client.get("/v1/assistants/nonexistent")
    assert response.status_code == 404
```

---

## Next Steps

- **Service Testing**: Service layer patterns → `.codemie/guides/testing/testing-service-patterns.md`
- **Testing Patterns**: General patterns → `.codemie/guides/testing/testing-patterns.md`
- **REST API Development**: API patterns → `.codemie/guides/api/rest-api-patterns.md`

---

## References

- **Source**: tests/codemie/rest_api/routers/ (API test examples)
- **TestClient**: FastAPI testing utilities
- **pytest-asyncio**: https://github.com/pytest-dev/pytest-asyncio
- **FastAPI Testing**: https://fastapi.tiangolo.com/tutorial/testing/
