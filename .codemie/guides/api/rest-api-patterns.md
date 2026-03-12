# REST API Patterns

AI-optimized guide to FastAPI patterns in CodeMie

---

## FastAPI Application Setup

### Application Initialization

```python
# src/codemie/rest_api/main.py
from fastapi import FastAPI
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: DB migrations, default data, background tasks
    logger.info("Starting CodeMie application")
    alembic_upgrade_postgres()
    create_default_applications()
    yield
    # Shutdown: cleanup tasks
    for task in tasks:
        task.cancel()

app = FastAPI(lifespan=lifespan)
```

**Key Points**:
- Use `@asynccontextmanager` for startup/shutdown logic
- Initialize DB, run migrations, create default data in startup
- Cleanup resources in shutdown

### Router Registration

```python
# src/codemie/rest_api/main.py
from codemie.rest_api.routers import assistant, conversation, index

app.include_router(assistant.router)
app.include_router(conversation.router)
app.include_router(index.router)
```

**Pattern**: Import routers → register with `include_router()`

---

## Router Organization

### Router Structure

```python
# src/codemie/rest_api/routers/assistant.py:80-84
from fastapi import APIRouter, Depends
from codemie.rest_api.security.authentication import authenticate

router = APIRouter(
    tags=["Assistant"],
    prefix="/v1",
    dependencies=[],
)
```

**Pattern**: Routers organized by feature/resource (assistant, conversation, index)
- **Tags**: Group endpoints in OpenAPI docs
- **Prefix**: Version prefix `/v1`
- **Dependencies**: Global auth can be set here or per-endpoint

### Complete Import Organization

```python
# src/codemie/rest_api/routers/assistant.py:1-78
# 1. Standard library
import json
import asyncio
import yaml
from time import time
from typing import Annotated, Literal, Optional, List

# 2. Third-party
from pydantic import BaseModel, Field
from fastapi import APIRouter, status, Request, Depends, BackgroundTasks, Query
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder

# 3. codemie-tools
from codemie_tools.base.models import ToolKit

# 4. Local imports (by layer)
from codemie.configs import logger, config
from codemie.configs.logger import set_logging_info
from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import BaseResponse, AssistantChatRequest
from codemie.rest_api.models.assistant import Assistant, AssistantRequest
from codemie.rest_api.security.authentication import authenticate
from codemie.service.assistant_service import AssistantService
```

**Key Points**:
- Organize imports by source: standard → third-party → codemie-tools → local
- Group local imports by layer: configs → core → models → services
- Use explicit imports (avoid `from module import *`)

### Anti-Pattern: Business Logic in Routes

```python
# WRONG: Business logic in endpoint
@router.post("/assistants")
def create_assistant(request: AssistantRequest):
    assistant = Assistant(**request.model_dump())
    assistant.created_by = CreatedByUser(id=user.id)
    # ... complex validation, data processing ...
    assistant.save()
```

**Problem**: Violates separation of concerns, untestable
**Solution**: Delegate to service layer

---

## Request/Response Patterns

### Pydantic Request Models

```python
# src/codemie/rest_api/models/assistant.py
from pydantic import BaseModel, Field

class AssistantRequest(BaseModel):
    name: str
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    project: str = DEMO_PROJECT
    toolkits: list[ToolKitDetails] = Field(default_factory=list)

    @model_validator(mode='before')
    @classmethod
    def normalize_input_data(cls, data: Any) -> Any:
        # Sanitize inputs, remove NULL bytes
        if isinstance(data, dict) and 'name' in data:
            data['name'] = data['name'].replace('\x00', '')
        return data
```

**Key Points**:
- Use `Optional` for nullable fields
- `Field(default_factory=list)` for list defaults
- `@model_validator` for input sanitization

### Response Models

```python
# src/codemie/rest_api/models/assistant.py
class AssistantListResponse(BaseModel):
    id: str
    name: str
    description: str
    icon_url: Optional[str] = None
    created_by: Optional[CreatedByUser] = None
```

**Pattern**: Separate list/detail response models for optimal payloads

---

## Dependency Injection

### Authentication

```python
# src/codemie/rest_api/routers/assistant.py
from codemie.rest_api.security.authentication import authenticate

@router.get("/assistants/{assistant_id}")
def get_assistant_by_id(
    assistant_id: str,
    user: User = Depends(authenticate)
):
    assistant = _get_assistant_by_id_or_raise(assistant_id)
    _check_user_can_access_assistant(user, assistant, "view", Action.READ)
    return assistant
```

**Pattern**: `Depends(authenticate)` injects authenticated `User`

### Router-Level Dependencies

```python
router = APIRouter(
    tags=["Conversation"],
    prefix="/v1",
    dependencies=[Depends(authenticate)],  # Applied to ALL endpoints
)
```

**Pattern**: Apply auth to all endpoints via router `dependencies`

---

## Error Handling

### Custom Exception Class

```python
# src/codemie/core/exceptions.py
class ExtendedHTTPException(Exception):
    def __init__(self, code: int, message: str, details: str = None, help: str = None):
        self.code = code
        self.message = message
        self.details = details
        self.help = help
```

### Helper Function Pattern for Errors

```python
# src/codemie/rest_api/routers/assistant.py
from codemie.core.exceptions import ExtendedHTTPException
from fastapi import status

def _get_assistant_by_id_or_raise(assistant_id: str) -> Assistant:
    """Helper function: Get assistant or raise 404"""
    assistant = Assistant.find_by_id(assistant_id)
    if not assistant:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Assistant not found",
            details=f"No assistant found with the id '{assistant_id}'.",
            help="Please check the assistant id and ensure it is correct."
        )
    return assistant

def _check_user_can_access_assistant(
    user: User,
    assistant: Assistant,
    operation: str,
    action: Action
):
    """Helper function: Check access permissions or raise 403"""
    if not Ability(user).can(operation, assistant):
        raise_access_denied(action, user.is_admin)
```

**Pattern**: Use helper functions with `_` prefix for:
- Resource lookups with validation (`_get_*_or_raise`)
- Permission checks (`_check_*`)
- Data transformations (`_transform_*`)

**Key Points**:
- Consistent error structure: `code`, `message`, `details`, `help`
- Use HTTP status constants from `fastapi.status`
- Helper functions centralize repeated validation logic

### Exception Handler

```python
# src/codemie/rest_api/main.py
@app.exception_handler(ExtendedHTTPException)
async def extended_http_exception_handler(request: Request, exc: ExtendedHTTPException):
    if exc.code >= 500:
        logger.error(exc.details, exc_info=True)
    return JSONResponse(
        status_code=exc.code,
        content={"error": {"message": exc.message, "details": exc.details, "help": exc.help}}
    )
```

**Pattern**: Global exception handler converts exceptions to JSON responses

### Anti-Pattern: Generic Errors

```python
# WRONG: No context
raise HTTPException(status_code=404, detail="Not found")

# RIGHT: Structured error
raise ExtendedHTTPException(
    code=status.HTTP_404_NOT_FOUND,
    message="Resource not found",
    details="The assistant with ID 'abc' does not exist",
    help="Verify the ID or list available assistants"
)
```

---

## Middleware Patterns

### CORS Middleware

```python
# src/codemie/rest_api/main.py
from starlette.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Key Points**:
- Enable CORS for frontend access
- Configure origins, methods, headers

### Custom Middleware

```python
# src/codemie/rest_api/main.py
@app.middleware("http")
async def configure_logging(request: Request, call_next):
    uuid_str = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request.state.uuid = uuid_str
    set_logging_info(uuid=uuid_str, user_id="")
    return await call_next(request)
```

**Pattern**: Use `@app.middleware("http")` for request/response processing
**Use Cases**: Logging, tracing, request ID injection

---

## Complete Endpoint Patterns

### List Endpoint with Pagination

```python
# src/codemie/rest_api/routers/assistant.py:126-166
from typing import Annotated
from fastapi import Query
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder

@router.get(
    "/assistants",
    status_code=status.HTTP_200_OK,
    response_model=list[AssistantListResponse],
    response_model_by_alias=True,
)
def index_assistants(
    user: User = Depends(authenticate),
    scope: Annotated[AssistantScope, Query()] = AssistantScope.VISIBLE_TO_USER,
    minimal_response: bool = False,
    filters: str = None,
    page: int = 0,
    per_page: int = 12,
):
    """Returns all saved assistants with pagination and filtering"""
    try:
        parsed_filters = json.loads(filters) if filters else None
    except json.JSONDecodeError:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid filters",
            details="Filters must be a valid encoded JSON object.",
            help="Please check the filters and ensure they are in the correct format.",
        )

    repository = AssistantRepository()
    result = repository.query(
        user=user,
        scope=scope,
        filters=parsed_filters,
        page=page,
        per_page=per_page,
        minimal_response=minimal_response,
    )

    return JSONResponse(content=jsonable_encoder(result), status_code=status.HTTP_200_OK)
```

**Key Points**:
- Use `Annotated` for query parameters with enum types
- Validate JSON filters in try/except block
- Delegate to repository layer for query logic
- Use `jsonable_encoder()` for Pydantic → JSON conversion

### Get by ID Endpoint

```python
# src/codemie/rest_api/routers/assistant.py:185-200
@router.get(
    "/assistants/id/{assistant_id}",
    status_code=status.HTTP_200_OK,
    response_model=Assistant,
    response_model_by_alias=True,
)
def get_assistant_by_id(
    request: Request,
    assistant_id: str,
    user: User = Depends(authenticate)
):
    """Returns saved assistant by id"""
    assistant = _get_assistant_by_id_or_raise(assistant_id)
    assistant.user_abilities = Ability(user).list(assistant)
    _check_user_can_access_assistant(user, assistant, "view", Action.READ)
    return assistant
```

**Pattern**:
1. Validate resource exists (helper function)
2. Enrich with user-specific data (abilities)
3. Check permissions (helper function)
4. Return resource

### Create Endpoint

```python
@router.post(
    "/assistants",
    status_code=status.HTTP_201_CREATED,
    response_model=BaseModelResponse[Assistant],
)
def create_assistant(
    request: AssistantRequest,
    user: User = Depends(authenticate)
):
    """Create a new assistant"""
    assistant = Assistant(**request.model_dump())
    assistant.created_by = CreatedByUser(id=user.id, name=user.name)
    assistant.save()

    return BaseModelResponse(
        message="Assistant created successfully",
        data=assistant
    )
```

**Pattern**:
1. Pydantic validates request automatically
2. Convert request → model with `model_dump()`
3. Enrich with user context
4. Save via service/repository
5. Return structured response

---

## Validation Patterns

### Request Validation

```python
# FastAPI auto-validates using Pydantic
@router.post("/assistants")
def create_assistant(request: AssistantRequest, user: User = Depends(authenticate)):
    # request is already validated by Pydantic
    assistant = Assistant(**request.model_dump())
```

### Validation Error Handler

```python
# src/codemie/rest_api/main.py
from fastapi.exceptions import RequestValidationError

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    messages = [error.get("msg") for error in errors]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"error": {"message": "; ".join(messages)}}
    )
```

**Pattern**: Catch validation errors, format user-friendly messages

### Anti-Pattern: Manual Validation

```python
# WRONG: Manual validation
@router.post("/assistants")
def create_assistant(data: dict):
    if "name" not in data:
        raise HTTPException(400, "Name required")
    # ...

# RIGHT: Pydantic validation
@router.post("/assistants")
def create_assistant(request: AssistantRequest):
    # Pydantic validates automatically
```

---

## Async Patterns

### Async Endpoints

```python
# src/codemie/rest_api/routers/assistant.py
@router.post("/assistants/{assistant_id}/model")
async def ask_assistant_by_id(
    raw_request: Request,
    assistant_id: str,
    background_tasks: BackgroundTasks,
    request: AssistantChatRequest,
    user: User = Depends(authenticate),
):
    asyncio.create_task(raw_request.state.wait_for_disconnect())
    result = await asyncio.to_thread(_ask_assistant, assistant, raw_request, request, user, background_tasks)
    return result
```

**Key Points**:
- Use `async def` for async operations
- `await asyncio.to_thread()` for sync code in async context
- `asyncio.create_task()` for concurrent tasks

### Background Tasks

```python
@router.post("/assistants/{assistant_id}/marketplace/publish")
def publish_assistant_to_marketplace(
    assistant_id: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(authenticate)
):
    # Immediate response
    assistant.is_global = True
    assistant.update()

    # Schedule background indexing
    background_tasks.add_task(
        _index_marketplace_assistant,
        assistant_id=assistant_id,
        user=user
    )

    return BaseResponse(message="Published successfully")
```

**Pattern**: Use `BackgroundTasks` for non-blocking operations (indexing, emails)

---

## References

- **Source**: `src/codemie/rest_api/`
- **Related**: [Endpoint Conventions](./endpoint-conventions.md), [Layered Architecture](../architecture/layered-architecture.md)
- **External**: [FastAPI Docs](https://fastapi.tiangolo.com/)
