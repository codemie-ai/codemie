# Error Handling Patterns

## Quick Summary

Error handling patterns for custom exceptions, exception hierarchies, API error responses, error propagation, and graceful degradation in CodeMie. Implement consistent, robust error handling following FastAPI best practices.

**Category**: Development/Error Handling
**Complexity**: Medium
**Prerequisites**: Python exceptions, FastAPI, Pydantic, async/await, logging

## Prerequisites

- **Python Exceptions**: Exception handling, hierarchies
- **FastAPI**: `@app.exception_handler` decorator
- **Pydantic**: `RequestValidationError`
- **Async/Await**: Async exception handling
- **Logging**: Error tracking with `logger`

---

## Exception Hierarchy

### Custom Exception Classes

| Exception | Purpose | Key Attributes | Source |
|-----------|---------|----------------|--------|
| `ExtendedHTTPException` | Rich HTTP error responses | `code`, `message`, `details`, `help` | exceptions.py:4-32 |
| `TaskException` | Task errors with original exception | `original_exc` | exceptions.py:34-39 |
| `InterruptedException` | Workflow interruptions | `message` | exceptions.py:42-45 |
| `PlatformToolError` | Platform tool errors | `message`, `details` | exceptions.py:82-88 |
| `UnauthorizedPlatformAccessError` | Non-admin platform access | `message` | exceptions.py:91-96 |
| `InvalidFilterCombinationError` | Invalid filter combinations | `details` | exceptions.py:99-104 |

### Exception Inheritance

```
Exception
├── ExtendedHTTPException
├── TaskException
├── InterruptedException
└── PlatformToolError
    ├── UnauthorizedPlatformAccessError
    └── InvalidFilterCombinationError
```

---

## Pattern 1: ExtendedHTTPException

**Rich Error Responses** (src/codemie/core/exceptions.py:4-32, rest_api/main.py:363-384)

```python
# Definition
class ExtendedHTTPException(Exception):
    def __init__(self, code: int, message: str, details: str | dict = None, help: str = None):
        self.code = code
        self.message = message
        self.details = details
        self.help = help

# Usage
raise ExtendedHTTPException(
    code=400,
    message="Invalid email format",
    details="Email must contain @ symbol",
    help="Example: user@example.com"
)

# FastAPI Handler
@app.exception_handler(ExtendedHTTPException)
async def extended_http_exception_handler(request: Request, exc: ExtendedHTTPException):
    if exc.code >= 500:
        logger.error(exc.details, exc_info=True)
    else:
        logger.warning(f"Status: {exc.code}, Error: {exc.message}")

    return JSONResponse(
        status_code=exc.code,
        content={"error": {"message": exc.message, "details": exc.details, "help": exc.help}}
    )

# Response JSON
{"error": {"message": "Invalid email format", "details": "...", "help": "..."}}
```

---

## Pattern 2: Exception Preservation

**TaskException with original_exc** (src/codemie/core/exceptions.py:34-39)

```python
class TaskException(Exception):
    original_exc: Optional[Any] = None
    def __init__(self, *args, **kwargs):
        self.original_exc = kwargs.pop("original_exc", None)
        super().__init__(*args)

# Usage - preserve original exception
try:
    risky_operation()
except ValueError as e:
    raise TaskException("Task failed", original_exc=e)
```

---

## Pattern 3: FastAPI Exception Handlers

### Elasticsearch ApiError Handler

**External Service Errors** (src/codemie/rest_api/main.py:345-360)

```python
@app.exception_handler(ApiError)
async def elastic_exception_handler(request: Request, exception: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "message": "Elastic service unavailable",
                "details": f"Error communicating with Elastic: {str(exception)}",
                "help": "Try again later. Contact admin if issue persists."
            }
        }
    )
```

### RequestValidationError Handler

**Pydantic Validation Errors** (src/codemie/rest_api/main.py:387-419)

```python
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    logger.exception(exc)

    errors = exc.errors()
    messages = [error.get("msg", "Validation error") for error in errors]
    error_message = "; ".join(messages) if messages else "Validation Error"

    return JSONResponse(
        status_code=422,
        content={"error": {"message": error_message}}
    )
```

---

## Pattern 4: Error Propagation

### Raise vs Re-raise vs Raise From

| Pattern | Syntax | Traceback | Use Case |
|---------|--------|-----------|----------|
| **New exception** | `raise NewError()` | Loses original | Replace error context |
| **Re-raise** | `raise` | Preserves full | Log then propagate unchanged |
| **Chain exception** | `raise NewError() from e` | Chains via `__cause__` | Wrap with context preservation |

**Examples**:

```python
# Raise new - loses original traceback
try:
    process_data(data)
except ValueError:
    raise ExtendedHTTPException(code=400, message="Invalid data")

# Re-raise - preserves full traceback
try:
    critical_operation()
except Exception as e:
    logger.error(f"Critical failure: {e}", exc_info=True)
    raise

# Raise from - preserves exception chain
try:
    process_data(data)
except ValueError as e:
    raise ExtendedHTTPException(code=400, message="Invalid data", details=str(e)) from e
```

### Async Exception Handling

```python
async def async_operation():
    try:
        result = await external_api_call()
        return result
    except httpx.HTTPError as e:
        logger.error(f"API call failed: {e}")
        raise ExtendedHTTPException(code=503, message="External service unavailable", details=str(e))
```

---

## Pattern 5: Graceful Degradation

### Non-Fatal Initialization

**Continue on Failure** (src/codemie/rest_api/main.py:67-82)

```python
def _initialize_optional_service():
    try:
        optional_service.initialize()
        logger.info("Optional service initialized")
    except Exception as e:
        logger.error(f"Service init failed: {e}")
        # Non-fatal - service continues without optional feature
```

### Retry with httpx

**Transient Failure Handling**

```python
try:
    response = httpx.post(url, json=payload, timeout=5.0)
    response.raise_for_status()
except httpx.HTTPError as e:
    logger.error(f"HTTP error: {e}")
    raise ExtendedHTTPException(code=503, message="External API failed", details=str(e))
except Exception as e:
    logger.error(f"Unexpected error: {e}")
    raise
```

### Fallback Defaults

```python
def get_user_preferences(user_id: str) -> dict:
    try:
        return preference_service.fetch(user_id)
    except Exception as e:
        logger.warning(f"Preferences fetch failed: {e}")
        return {"theme": "default", "language": "en"}
```

### Specific Exception Catching

```python
try:
    result = external_service.call()
except ServiceUnavailableException:
    raise  # Re-raise specific exceptions
except Exception as e:
    logger.error(f"Unexpected error: {e}")
    raise  # Re-raise unexpected errors
```

---

## Pattern 6: Logging Integration

### Log Levels by Severity

| Error Type | Log Level | When | Example |
|------------|-----------|------|---------|
| 5xx errors | `ERROR` | Server failures | `logger.error(exc.details, exc_info=True)` |
| 4xx errors | `WARNING` | Client errors | `logger.warning(f"Invalid: {exc.message}")` |
| Degraded | `WARNING` | Fallback active | `logger.warning(f"Using defaults: {e}")` |
| Expected | `INFO` | Business logic errors | `logger.info(f"User not found: {id}")` |

### Exception Context

```python
# 5xx - include traceback
if exc.code >= 500:
    logger.error(exc.details, exc_info=True)
else:
    logger.warning(f"Status: {exc.code}, Error: {exc.message}")

# NEVER log sensitive data
logger.warning(f"Auth failed for user: {username}")  # OK
# logger.warning(f"Auth failed: {username}:{password}")  # NEVER
```

---

## Examples

### API Endpoint Error Handling

```python
@router.post("/users")
async def create_user(user_data: UserCreate):
    try:
        user = await user_service.create(user_data)
        return {"user_id": user.id}
    except UserAlreadyExistsError:
        raise ExtendedHTTPException(
            code=409,
            message="User already exists",
            details=f"Email {user_data.email} is registered",
            help="Use different email or login"
        )
    except DatabaseError as e:
        logger.error(f"DB error: {e}", exc_info=True)
        raise ExtendedHTTPException(code=500, message="Failed to create user")
```

### Service Layer Propagation

```python
async def fetch_user_data(user_id: str) -> UserData:
    try:
        user = await db.get_user(user_id)
        if not user:
            raise ExtendedHTTPException(code=404, message="User not found", details=f"No user {user_id}")
        return user
    except DatabaseError as db_err:
        raise TaskException(f"Failed to fetch user {user_id}", original_exc=db_err)
```

### External Service Integration

```python
async def call_external_service(service_id: str, params: dict):
    try:
        result = await external_api.call(service_id, params)
        logger.info(f"Service call successful: {service_id}")
        return result
    except httpx.HTTPError as e:
        logger.error(f"Service call failed: {e}")
        raise ExtendedHTTPException(code=503, message="External service unavailable", details=str(e))
    except Exception as e:
        logger.error(f"Unexpected: {e}", exc_info=True)
        raise ExtendedHTTPException(code=500, message="Internal error")
```

### Workflow Interruption

```python
async def execute_workflow(workflow_id: str):
    try:
        result = await workflow_executor.run(workflow_id)
        return result
    except InterruptedException as e:
        logger.info(f"Workflow interrupted: {e.message}")
        await save_workflow_state(workflow_id)
        return {"status": "interrupted", "message": e.message}
    except Exception as e:
        logger.error(f"Workflow failed: {e}", exc_info=True)
        raise
```

---

## Anti-Patterns

### ❌ Swallowing Exceptions

```python
# WRONG - silent failure
try:
    critical_operation()
except Exception:
    pass

# CORRECT - log and handle
try:
    critical_operation()
except Exception as e:
    logger.error(f"Critical failed: {e}", exc_info=True)
    raise ExtendedHTTPException(code=500, message="Operation failed")
```

### ❌ Overly Broad Catching

```python
# WRONG - catches everything
try:
    result = api_call()
except Exception:
    return default_value

# CORRECT - specific exceptions
try:
    result = api_call()
except (httpx.HTTPError, TimeoutError) as e:
    logger.warning(f"API failed: {e}")
    return default_value
```

### ❌ Missing Context

```python
# WRONG - context lost
try:
    process_data(data)
except ValueError:
    raise CustomException("Failed")

# CORRECT - preserve context
try:
    process_data(data)
except ValueError as e:
    raise CustomException(f"Failed: {e}") from e
```

### ❌ Vague Messages

```python
# WRONG
raise ExtendedHTTPException(code=400, message="Invalid input")

# CORRECT
raise ExtendedHTTPException(
    code=400,
    message="Invalid email format",
    details="Email must contain @ and domain",
    help="Example: user@example.com"
)
```

### ❌ Repeated Error Strings

```python
# WRONG - duplicated strings
raise ExtendedHTTPException(code=400, message="Query too expensive", details="Query requires too much memory")
raise ExtendedHTTPException(code=400, message="Query too expensive", details="Query requires too much memory")

# CORRECT - define module-level constants
QUERY_TOO_EXPENSIVE_MSG = "Query too expensive"
MEMORY_LIMIT_DETAILS = "Query requires too much memory"
raise ExtendedHTTPException(code=400, message=QUERY_TOO_EXPENSIVE_MSG, details=MEMORY_LIMIT_DETAILS)
```

---

## Verification

### Exception Testing

```python
import pytest

def test_extended_http_exception():
    with pytest.raises(ExtendedHTTPException) as exc_info:
        raise ExtendedHTTPException(code=400, message="Invalid", details="Email required")

    assert exc_info.value.code == 400
    assert exc_info.value.message == "Invalid"
```

### API Error Response Testing

```python
from fastapi.testclient import TestClient

def test_api_error_response(client: TestClient):
    response = client.post("/users", json={})
    assert response.status_code == 422
    assert "error" in response.json()
```

---

## Troubleshooting

**Issue**: Exception not caught by handler
**Solution**: Register handler before app startup with `@app.exception_handler(ExceptionType)`

**Issue**: Lost exception traceback
**Solution**: Use `raise from` to preserve chain: `raise NewError() from e`

**Issue**: Sensitive data in logs
**Solution**: Sanitize before logging: `safe_msg = sanitize(str(e)); logger.error(safe_msg)`

---

## Next Steps

- **Testing Patterns** → [Testing Patterns](../testing/testing-patterns.md) for exception testing with `pytest.raises`
- **Logging Patterns** → Story 5.3 for logging integration
- **API Patterns** → [REST API Patterns](../api/rest-api-patterns.md) for API error responses
- **Service Patterns** → [Service Layer Patterns](../architecture/service-layer-patterns.md) for error propagation

---

## References

- **Source Files**:
  - `src/codemie/core/exceptions.py` - Custom exceptions
  - `src/codemie/rest_api/main.py` - FastAPI handlers
- **Related Patterns**:
  - [Testing Patterns](../testing/testing-patterns.md)
  - [REST API Patterns](../api/rest-api-patterns.md)
  - [Service Layer Patterns](../architecture/service-layer-patterns.md)
- **External Resources**:
  - [FastAPI Exception Handling](https://fastapi.tiangolo.com/tutorial/handling-errors/)
  - [Python Exception Best Practices](https://docs.python.org/3/tutorial/errors.html)
