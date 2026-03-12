# REST API Endpoint Conventions

AI-optimized guide to RESTful endpoint design in CodeMie

---

## Resource Naming

### URL Structure

```
/v1/{resource}
/v1/{resource}/{id}
/v1/{resource}/{id}/{sub-resource}
```

**Examples**:
```
GET    /v1/assistants              # List resources
GET    /v1/assistants/{id}         # Get single resource
POST   /v1/assistants              # Create resource
PUT    /v1/assistants/{id}         # Update resource
DELETE /v1/assistants/{id}         # Delete resource
POST   /v1/assistants/{id}/model   # Action on resource
```

### Naming Rules

| Rule | Example | Anti-Pattern |
|------|---------|--------------|
| Plural nouns | `/assistants` | `/assistant` |
| Lowercase | `/conversations` | `/Conversations` |
| Kebab-case | `/user-settings` | `/userSettings` |
| Hierarchical | `/assistants/{id}/versions/{version}` | `/assistant-versions/{version}` |

### Anti-Pattern: Verbs in URLs

```python
# WRONG: Verbs in URL
POST /v1/createAssistant
GET  /v1/getAssistants

# RIGHT: HTTP method expresses action
POST /v1/assistants
GET  /v1/assistants
```

---

## HTTP Methods

### Standard CRUD Operations

| Method | Endpoint | Purpose | Request Body | Response |
|--------|----------|---------|--------------|----------|
| GET | `/assistants` | List all | None | `List[Assistant]` |
| GET | `/assistants/{id}` | Get one | None | `Assistant` |
| POST | `/assistants` | Create | `AssistantRequest` | `BaseResponse` |
| PUT | `/assistants/{id}` | Update | `AssistantRequest` | `BaseResponse` |
| DELETE | `/assistants/{id}` | Delete | None | `BaseResponse` |

### Examples

```python
# src/codemie/rest_api/routers/assistant.py

# LIST
@router.get("/assistants", response_model=list[AssistantListResponse])
def index_assistants(
    user: User = Depends(authenticate),
    page: int = 0,
    per_page: int = 12
):
    repository = AssistantRepository()
    return repository.query(user=user, page=page, per_page=per_page)

# GET
@router.get("/assistants/id/{assistant_id}", response_model=Assistant)
def get_assistant_by_id(assistant_id: str, user: User = Depends(authenticate)):
    assistant = Assistant.find_by_id(assistant_id)
    return assistant

# CREATE
@router.post("/assistants", response_model=BaseResponse)
def create_assistant(request: AssistantRequest, user: User = Depends(authenticate)):
    assistant = Assistant(**request.model_dump())
    assistant.save()
    return BaseResponse(message="Assistant saved")

# UPDATE
@router.put("/assistants/{assistant_id}", response_model=BaseResponse)
def update_assistant(assistant_id: str, request: AssistantRequest, user: User = Depends(authenticate)):
    assistant = _get_assistant_by_id_or_raise(assistant_id)
    repository = AssistantRepository()
    repository.update(assistant, request, user)
    return BaseResponse(message="Assistant updated")

# DELETE
@router.delete("/assistants/{assistant_id}", response_model=BaseResponse)
def delete_assistant(assistant_id: str, user: User = Depends(authenticate)):
    Assistant.delete_assistant(assistant_id)
    return BaseResponse(message="Assistant removed")
```

### Non-CRUD Actions

```python
# Actions use POST with descriptive resource path
POST /v1/assistants/{id}/model                    # Execute assistant
POST /v1/assistants/{id}/marketplace/publish      # Publish to marketplace
POST /v1/assistants/{id}/marketplace/unpublish    # Unpublish
POST /v1/assistants/{id}/reactions                # React (like/dislike)
DELETE /v1/assistants/{id}/reactions              # Remove reactions
```

**Pattern**: `POST /{resource}/{id}/{action}` for non-CRUD operations

---

## Versioning

### URL Versioning

```python
# All routers use /v1 prefix
router = APIRouter(
    tags=["Assistant"],
    prefix="/v1",  # Version in URL
    dependencies=[]
)
```

**Pattern**: Version in URL path (`/v1`, `/v2`)
**Benefits**: Clear, cache-friendly, easy to route

### Anti-Pattern: Header Versioning

```python
# NOT USED in CodeMie
GET /assistants
Headers: { "API-Version": "1" }
```

**Why**: Less visible, harder to test, cache issues

---

## Pagination

### Query Parameters

```python
# src/codemie/rest_api/routers/assistant.py
@router.get("/assistants")
def index_assistants(
    user: User = Depends(authenticate),
    page: int = 0,         # Page number (0-indexed)
    per_page: int = 12,    # Items per page
):
    repository = AssistantRepository()
    result = repository.query(user=user, page=page, per_page=per_page)
    return result
```

**Pattern**: Offset-based pagination
- `page`: Page number (0-indexed)
- `per_page`: Items per page

### Response Format

```python
# List endpoint returns array directly
GET /v1/assistants?page=0&per_page=12
Response: [
    {"id": "1", "name": "Assistant 1"},
    {"id": "2", "name": "Assistant 2"}
]
```

**Note**: CodeMie returns arrays directly (no wrapper object with `total`, `page`, etc.)

### Version History Pagination

```python
# src/codemie/rest_api/routers/assistant.py
@router.get("/assistants/{assistant_id}/versions")
def get_assistant_versions(
    assistant_id: str,
    page: int = 0,
    per_page: int = 20,
    user: User = Depends(authenticate)
):
    return AssistantVersionService.get_version_history(
        assistant=assistant,
        page=page,
        per_page=per_page
    )

# Response includes pagination metadata
{
    "versions": [...],
    "total_versions": 42,
    "assistant_name": "My Assistant",
    "assistant_id": "abc123"
}
```

**Pattern**: Include metadata when pagination context is important

---

## Filtering

### Query Parameter Filters

```python
# src/codemie/rest_api/routers/assistant.py
@router.get("/assistants")
def index_assistants(
    user: User = Depends(authenticate),
    scope: AssistantScope = AssistantScope.VISIBLE_TO_USER,
    filters: str = None,  # JSON-encoded filters
):
    parsed_filters = json.loads(filters) if filters else None
    result = repository.query(
        user=user,
        scope=scope,
        filters=parsed_filters
    )
    return result
```

**Example Request**:
```
GET /v1/assistants?scope=VISIBLE_TO_USER&filters={"name":"demo"}
```

**Pattern**: JSON-encoded filters in query param for complex filtering

### Scope-Based Filtering

```python
# src/codemie/rest_api/routers/assistant.py
class AssistantScope(str, Enum):
    VISIBLE_TO_USER = "visible_to_user"
    OWNED_BY_USER = "owned_by_user"
    GLOBAL = "global"

@router.get("/assistants")
def index_assistants(
    scope: AssistantScope = AssistantScope.VISIBLE_TO_USER,
    user: User = Depends(authenticate)
):
    repository.query(user=user, scope=scope)
```

**Pattern**: Use enum-based scopes for common filter patterns

---

## Sorting

### Query Parameters

```python
# src/codemie/rest_api/routers/index.py
class SortKey(str, Enum):
    UPDATE_DATE = "update_date"
    NAME = "name"

class SortOrder(str, Enum):
    ASC = "asc"
    DESC = "desc"

@router.get("/index")
def index_indexes_progress(
    sort_key: Optional[SortKey] = SortKey.UPDATE_DATE,
    sort_order: Optional[SortOrder] = SortOrder.DESC,
    user: User = Depends(authenticate)
):
    return IndexStatusService.get_index_info_list(
        user=user,
        sort_key=sort_key,
        sort_order=sort_order
    )
```

**Example Request**:
```
GET /v1/index?sort_key=name&sort_order=asc
```

**Pattern**: Separate `sort_key` and `sort_order` params with enum validation

---

## Authentication & Authorization

### Authentication Header

```python
# src/codemie/rest_api/security/authentication.py
from fastapi.security import APIKeyHeader

# Supports multiple auth methods
user_id_header = APIKeyHeader(name="X-User-ID", auto_error=False)
keycloak_authorization_token = APIKeyHeader(name="Authorization", auto_error=False)
oidc_authorization_token = APIKeyHeader(name="Authorization", auto_error=False)

async def authenticate(
    request: Request,
    user_id: str = Depends(user_id_header),
    keycloak_auth_header: str = Depends(keycloak_authorization_token),
    oidc_auth_header: str = Depends(oidc_authorization_token)
) -> User:
    if keycloak_auth_header or oidc_auth_header:
        idp = get_idp_provider()
        return await idp.authenticate(request, auth_token)
    elif user_id:
        idp = get_idp_provider(IdentityProvider.LOCAL)
        return await idp.authenticate(request, user_id)
```

**Supported Headers**:
- `Authorization: Bearer <token>` (Keycloak/OIDC)
- `User-ID: <user_id>` (Local development)

### Authorization Checks

```python
# src/codemie/rest_api/routers/assistant.py
@router.get("/assistants/{assistant_id}")
def get_assistant_by_id(assistant_id: str, user: User = Depends(authenticate)):
    assistant = Assistant.find_by_id(assistant_id)
    # Check user can access
    if not Ability(user).can(Action.READ, assistant):
        raise_access_denied("view")
    return assistant
```

**Pattern**: Dependency injection for auth, explicit permission checks

---

## Response Formats

### Success Response

```python
# Standard response for mutations
class BaseResponse(BaseModel):
    message: str

# Example
@router.post("/assistants")
def create_assistant(...):
    return BaseResponse(message="Assistant saved")
```

### Resource Response

```python
# Single resource
@router.get("/assistants/{id}", response_model=Assistant)
def get_assistant_by_id(...):
    return assistant

# List of resources
@router.get("/assistants", response_model=list[AssistantListResponse])
def index_assistants(...):
    return [assistant1, assistant2]
```

### Error Response

```json
{
  "error": {
    "message": "Assistant not found",
    "details": "No assistant found with the id 'abc123'.",
    "help": "Please check the assistant id and ensure it is correct."
  }
}
```

**Pattern**: Consistent error structure with `message`, `details`, `help`

---

## Path Parameters vs Query Parameters

### Path Parameters

```python
# Resource identifiers
GET /v1/assistants/{assistant_id}
GET /v1/assistants/{assistant_id}/versions/{version_number}

# Use for: IDs, required identifiers
```

### Query Parameters

```python
# Optional filters, pagination, sorting
GET /v1/assistants?page=0&per_page=12&scope=VISIBLE_TO_USER

# Use for: Optional params, filters, pagination
```

### Anti-Pattern: Mixed Conventions

```python
# WRONG: ID in query param
GET /v1/assistants?id=abc123

# RIGHT: ID in path
GET /v1/assistants/abc123
```

---

## Alternative Resource Access

```python
# src/codemie/rest_api/routers/assistant.py

# By ID
@router.get("/assistants/id/{assistant_id}")
def get_assistant_by_id(assistant_id: str, user: User = Depends(authenticate)):
    return Assistant.find_by_id(assistant_id)

# By Slug
@router.get("/assistants/slug/{assistant_slug:path}")
def get_assistant_by_slug(assistant_slug: str, user: User = Depends(authenticate)):
    return Assistant.get_by_fields({"slug.keyword": assistant_slug})
```

**Pattern**: Use path prefix (`/id/`, `/slug/`) to distinguish access methods. `:path` captures full path (supports `/` in slug)

---

## Nested Resources

### Version History

```python
# Versions nested under assistant
GET    /v1/assistants/{id}/versions
GET    /v1/assistants/{id}/versions/{version}
GET    /v1/assistants/{id}/versions/{v1}/compare/{v2}
POST   /v1/assistants/{id}/versions/{version}/rollback
```

**Pattern**: Nest sub-resources under parent resource

---

## Status Codes

| Code | Usage | Example |
|------|-------|---------|
| 200 | Success | `GET /assistants/{id}` |
| 201 | Created | Not used (returns 200) |
| 400 | Bad Request | Invalid input |
| 401 | Unauthorized | Missing/invalid auth |
| 403 | Forbidden | No permission |
| 404 | Not Found | Resource doesn't exist |
| 422 | Unprocessable | Validation failed |
| 500 | Server Error | Unexpected error |
| 503 | Service Unavailable | Elastic down |

**Pattern**: Use semantic HTTP codes, handle via exception handlers

---

## References

- **Source**: `src/codemie/rest_api/routers/`
- **Related**: [REST API Patterns](./rest-api-patterns.md), [Layered Architecture](../architecture/layered-architecture.md)
- **External**: [REST API Guidelines](https://restfulapi.net/)
