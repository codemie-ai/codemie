# Layered Architecture Pattern

## Quick Summary

CodeMie implements a 3-tier architecture: API Layer (FastAPI routers) → Service Layer (business logic) → Repository Layer (data access). Each layer has strict boundaries with async/await throughout. Errors propagate via `ExtendedHTTPException`.

**Category**: Architecture
**Complexity**: Medium
**Prerequisites**: Python async, FastAPI, dependency injection

---

## Architecture Diagram

```
┌─────────────────────────────────────────────┐
│  API Layer (rest_api/)                      │
│  ├── FastAPI routers (20+ endpoints)        │
│  ├── Request/response models (Pydantic)     │
│  ├── Authentication & authorization         │
│  └── Exception handlers                     │
└──────────────────┬──────────────────────────┘
                   │ Calls
                   ↓
┌─────────────────────────────────────────────┐
│  Service Layer (service/)                   │
│  ├── Business logic orchestration           │
│  ├── LLM service (multi-provider)           │
│  ├── Assistant/conversation management      │
│  ├── Platform indexing & plugins            │
│  └── Workflow execution                     │
└──────────────────┬──────────────────────────┘
                   │ Calls
                   ↓
┌─────────────────────────────────────────────┐
│  Repository Layer (repository/)             │
│  ├── FileRepositoryFactory (abstraction)    │
│  ├── FileSystemRepository (local files)     │
│  ├── AWSFileRepository (S3)                 │
│  ├── AzureFileRepository (Blob)             │
│  └── GCPFileRepository (Cloud Storage)      │
└──────────────────┬──────────────────────────┘
                   │ Persists
                   ↓
┌─────────────────────────────────────────────┐
│  Data Layer                                 │
│  ├── PostgreSQL (SQLModel ORM)              │
│  ├── Elasticsearch (vector search)          │
│  └── Cloud Storage (files, artifacts)       │
└─────────────────────────────────────────────┘
```

---

## Layer Responsibilities

### API Layer (rest_api/)

**Purpose**: HTTP interface, request validation, response formatting

```python
# src/codemie/rest_api/routers/assistant.py
@router.get("/assistants")
async def list_assistants(user: User = Depends(authenticate)):
    return await AssistantService.list_assistants(user)  # Delegate to service
```

**Responsibilities**: Validate requests, authenticate users, delegate to services, format responses, handle errors

**Characteristics**: No business logic, Pydantic models, dependency injection, async endpoints

---

### Service Layer (service/)

**Purpose**: Business logic, orchestration, LLM integration

```python
# src/codemie/service/assistant_service.py
class AssistantService:
    @staticmethod
    async def list_assistants(user: User) -> List[Assistant]:
        scope = AssistantScope.USER if not user.is_admin else AssistantScope.ALL
        assistants = await AssistantRepository.get_assistants(user.id, scope)
        return [Assistant.from_db_model(a) for a in assistants]
```

**Responsibilities**: Business rules, orchestration, data transformation, repository coordination

**Characteristics**: Stateless, async operations, no HTTP knowledge (no Request/Response objects)

---

### Repository Layer (repository/)

**Purpose**: Data access abstraction, persistence operations

```python
# src/codemie/repository/repository_factory.py
class FileRepositoryFactory:
    @classmethod
    def get_current_repository(cls):
        storage_type = cls.get_current_storage_type()
        if storage_type == FileStorageType.AWS:
            return AWSFileRepository(region_name=config.AWS_S3_REGION, ...)
        elif storage_type == FileStorageType.AZURE:
            return AzureFileRepository(connection_string=config.AZURE_..., ...)
        # GCP, FileSystem...
```

**Responsibilities**: Abstract storage, select implementation, provide uniform interface (save/load/delete)

**Characteristics**: Factory pattern, polymorphism (all implement `BaseFileRepository`), config-driven, pure data ops

---

## Dependency Flow & Patterns

```python
# ✅ Correct: API → Service → Repository
@router.post("/upload")
async def upload_file(file: UploadFile):
    return await FileService.upload(file)  # API → Service → Repo

class FileService:
    @staticmethod
    async def upload(file: UploadFile):
        repo = FileRepositoryFactory.get_current_repository()
        return {"url": await repo.save(file.filename, file.file.read())}
```

**Dependency Injection**: Shared services (e.g., `llm_service`) initialized in `main.py` lifespan, accessed as singletons

| Layer | Responsibility | Example |
|-------|----------------|---------|
| **API** | HTTP protocol | Parse JSON, validate, auth, status codes |
| **Service** | Business logic | Apply rules, orchestrate, transform data |
| **Repository** | Data access | SQL queries, cloud APIs, caching |

**Cross-Cutting**: Logging middleware injects request context (`X-Request-ID`); global exception handlers convert `ExtendedHTTPException` → JSON responses

---

## Anti-Patterns

```python
# ❌ Business logic in API layer
@router.post("/chat")
async def chat(request: ChatRequest):
    cost = request.tokens * 0.03 if request.model == "gpt-4" else request.tokens * 0.002
    if user.budget < cost: raise HTTPException(403, "Insufficient budget")  # WRONG
    return await llm.invoke(request.prompt)

# ✅ Delegate to service
@router.post("/chat")
async def chat(request: ChatRequest):
    return await AssistantService.chat(request)  # Service handles budget logic
```

```python
# ❌ Direct DB access from API
@router.get("/assistants")
async def get_assistants(session: Session = Depends(get_session)):
    return session.exec(select(Assistant)).all()  # WRONG - skip service layer

# ✅ Use service → repository
@router.get("/assistants")
async def get_assistants(user: User = Depends(authenticate)):
    return await AssistantService.list_assistants(user)  # Service→Repo
```

```python
# ❌ HTTP knowledge in service
class AssistantService:
    async def chat(self, request: Request):  # WRONG: HTTP Request object
        if request.headers.get("X-Custom"): ...  # WRONG: HTTP headers

# ✅ Domain models only
class AssistantService:
    async def chat(self, chat_request: ChatRequest):  # Pydantic model, no HTTP
        ...
```

```python
# ❌ Database logic in agent tools (violates layered architecture)
class BadAnalyticsTool(CodeMieTool):
    def execute(self, user_name: str, project: str):
        from sqlmodel import Session, select
        from codemie.clients.postgres import PostgresClient

        # WRONG: DB queries directly in tool layer
        with Session(PostgresClient.get_engine()) as session:
            stmt = select(ConversationAnalytics).where(
                ConversationAnalytics.user_name == user_name,
                ConversationAnalytics.project == project
            )
            results = session.exec(stmt).all()

        return format_results(results)

# ✅ Tool calls service, service handles DB (correct layering)
class GoodAnalyticsTool(CodeMieTool):
    def execute(self, user_name: str, project: str):
        from codemie.service.conversation_service import ConversationService

        # CORRECT: Tool → Service → Repository
        results = ConversationService.get_analytics(
            user_name=user_name,
            project=project
        )

        return format_results(results)
```

```python
# ❌ Bypassing existing service — reimplementing data access it already provides
class MigrationAdapter:
    async def read_config(self, key: str) -> str | None:
        async with get_async_session() as session:  # WRONG: DynamicConfigService already does this
            result = await session.execute(select(DynamicConfig).where(DynamicConfig.key == key))
            record = result.scalars().first()
            return record.value if record else None

# ✅ Reuse existing service (add async variant to service if missing)
class MigrationAdapter:
    async def read_config(self, key: str) -> str | None:
        record = await DynamicConfigService.aget_by_key(key)  # Reuse existing service
        return record.value if record else None
```

**Reuse Existing Services**: Before writing data access logic, check if a service already exposes the needed operation. If it only has sync methods, add an async variant to the service — don't bypass it with direct DB queries.

**Agent Tools Follow Same Rules**: Agent tools are part of the API layer (tool interface for LLM agents). They must delegate to services, not access DB directly. This ensures:
- **Reusability**: Service logic can be used by REST APIs, tools, CLI, background jobs
- **Testability**: Test service methods without mocking database connections
- **Maintainability**: Single source of truth for business logic and data access

**Example**: `src/codemie/agents/tools/platform/platform_tool.py:742-749` - GetConversationAnalyticsTool properly delegates to ConversationService

---

## When to Use

### Use This Pattern When

- [ ] Building API services with clear separation of concerns
- [ ] Need to swap implementations (e.g., AWS → Azure storage)
- [ ] Want testable code (mock repositories, test services independently)
- [ ] Require multiple API interfaces (REST + GraphQL + CLI)

### Don't Use This Pattern When

- [ ] Building simple CRUD apps (may be over-engineered)
- [ ] Tight coupling is acceptable (single-use scripts)

---

## References

- **Source**: `src/codemie/rest_api/`, `src/codemie/service/`, `src/codemie/repository/`
- **Analysis**: `local/docs/analysis/architectural-patterns.md`
- **Related**: [project-structure.md](project-structure.md) (module organization)
- **Examples**: `src/codemie/rest_api/routers/assistant.py`, `src/codemie/service/assistant_service.py`
