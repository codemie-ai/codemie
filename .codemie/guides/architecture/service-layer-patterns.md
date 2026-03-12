# Service Layer Patterns

## Quick Summary

Service layer implements business logic orchestration between API and repository layers. Services handle operations like data processing, validation, external integrations, and transaction coordination without HTTP or persistence concerns.

**Category**: Service/Architecture
**Complexity**: Medium
**Prerequisites**: Python async/await, type hints, layered architecture

---

## Service Class Structure

### Pattern 1: Instance-Based Service (Stateful)

Class holds cached data or initialized dependencies.

```python
# src/codemie/service/assistant/assistant_service.py
class AssistantService:
    _cached_base_assistant_templates = {}
    _cached_admin_assistant_templates = {}

    def __init__(self):
        templates_dir = config.ASSISTANT_TEMPLATES_DIR
        self._cached_base_assistant_templates = AssistantService._load_assistant_templates_from_dir(templates_dir)

    def get_assistant_template_by_slug(self, slug: str) -> Assistant | None:
        if slug in self._cached_base_assistant_templates:
            return self._cached_base_assistant_templates.get(slug)
        return None
```

**Use for**: Services with initialization, caching, state management.

### Pattern 2: Static Method Service (Stateless)

No instance creation needed. Direct utility operations.

```python
# src/codemie/service/index/index_service.py
class IndexStatusService:
    @staticmethod
    def get_index_status_markdown(datasource: IndexInfo):
        content = "# Data Source Information\n"
        content += f"### Name: {datasource.repo_name or datasource.full_name}\n"
        return content

    @classmethod
    def get_index_info_list(cls, user: User, filters: Optional[Dict[str, Any]] = None):
        with Session(IndexInfo.get_engine()) as session:
            statement = select(IndexInfo)
            index_list = session.exec(statement).all()
        return {"data": index_list, "pagination": meta}
```

**Use for**: Stateless operations, database queries, transformations.

### Pattern 3: Dependency Injection (Constructor)

Services receive dependencies via `__init__`.

```python
# src/codemie/service/index/index_encrypted_settings_service.py
class IndexEncryptedSettingsService:
    def __init__(self, index: IndexInfo, user: User, x_request_id: str):
        self.index = index
        self.user = user
        self.x_request_id = x_request_id

    def run(self):
        self._check_permissions()
        settings = Settings.get_by_id(self.index.setting_id)
        return settings
```

**Use for**: Services with scoped context (user, request, specific resource).

---

## Service Responsibilities

| Responsibility | Service Layer | NOT Service Layer |
|---|---|---|
| **Business Logic** | ✅ Validation, processing, orchestration | ❌ HTTP parsing, response formatting |
| **Data Access** | ✅ Call repositories, coordinate transactions | ❌ Direct SQL, ORM queries (use repos) |
| **External APIs** | ✅ Call external services, transform data | ❌ Expose HTTP endpoints (API layer) |
| **Error Handling** | ✅ Business exceptions, retry logic | ❌ HTTP status codes (API layer) |
| **Authorization** | ✅ Permission checks, ownership validation | ❌ JWT parsing, session management |

---

## Async/Await Patterns

CodeMie is **async-first**. Services use async for I/O operations.

### Async Service Methods

```python
# src/codemie/service/conversation_service.py
class ConversationService:
    @classmethod
    def upsert_chat_history(
        cls,
        assistant_response: str,
        request: AssistantChatRequest,
        user: User,
    ):
        # Synchronous: Elasticsearch/ORM operations
        conversation = Conversation.get_by_id(request.conversation_id)
        conversation.update_chat_history(
            user_query=request.text,
            assistant_response=assistant_response,
        )
        conversation.save()
```

**Note**: Current implementation uses sync ORM. Async pattern for future:

```python
async def upsert_chat_history_async(...):
    conversation = await Conversation.get_by_id_async(request.conversation_id)
    await conversation.save_async()
```

---

## CRUD Operation Patterns

| Operation | Pattern | Example |
|---|---|---|
| **Create** | Validate → Build → Save | `conversation_id = str(uuid.uuid4())`<br>`conversation = Conversation(id=conversation_id, user_id=user.id)`<br>`conversation.save(refresh=True)` |
| **Read** | Query → Filter → Execute | `statement = select(IndexInfo).where(user_filter)`<br>`with Session(Model.get_engine()) as session:`<br>`  results = session.exec(statement).all()` |
| **Update** | Load → Modify → Save | `conversation.conversation_name = request.name`<br>`conversation.update()` |
| **Delete** | Find → Cascade → Remove | `for item in related: item.delete()`<br>`Model.delete_by_id(id)` |

```python
# src/codemie/service/conversation_service.py - Full CRUD example
class ConversationService:
    @classmethod
    def create_conversation(cls, user: User, folder: str = None):
        conversation = Conversation(id=str(uuid.uuid4()), user_id=user.id)
        conversation.save(refresh=True)
        return conversation

    @classmethod
    def get_index_info_list(cls, user: User, filters: Optional[Dict] = None):
        statement = select(IndexInfo).where(cls._owned_by_user_filter(user))
        with Session(IndexInfo.get_engine()) as session:
            return session.exec(statement).all()
```

---

## Service-to-Repository Integration

Services delegate data access to repository layer.

```python
# src/codemie/service/file_service/file_service.py
from codemie.repository.repository_factory import FileRepositoryFactory

class FileService:
    @classmethod
    def get_file_object(cls, file_name: str) -> FileObject:
        file_object = FileObject.from_encoded_url(file_name)
        file_repo = FileRepositoryFactory().get_current_repository()  # Factory pattern
        return file_repo.read_file(
            file_name=file_object.name,
            owner=file_object.owner,
            mime_type=file_object.mime_type
        )
```

**Key points**:
- Services call repository methods, not direct DB access
- Repository factory provides abstraction (cloud storage, local files)
- Service handles business logic (decoding, validation)

---

## Error Handling Patterns

### Pattern 1: Custom Service Exceptions

```python
# src/codemie/service/index/index_encrypted_settings_service.py
class IndexEncryptedSettingsError(Exception):
    """When user does not have permission to access encrypted settings."""
    pass

class IndexEncryptedSettingsService:
    def run(self):
        self._check_permissions()
        try:
            settings = Settings.get_by_id(self.index.setting_id)
        except KeyError:
            raise IndexEncryptedSettingsError(f"Settings with ID {self.index.setting_id} are not found.")
        return settings

    def _check_permissions(self):
        permission = Permission.get_for(user=self.user, instance=self.index, action=Action.READ)
        if not permission:
            raise IndexEncryptedSettingsError("User does not have permission to read the index settings.")
```

### Pattern 2: Graceful Degradation

```python
# src/codemie/service/assistant/assistant_service.py
@classmethod
def _load_assistant_templates_from_dir(cls, templates_dir: Path) -> dict[str, Assistant]:
    assistant_templates = {}
    try:
        for filename in os.listdir(templates_dir):
            if filename.endswith(".yaml"):
                with open(os.path.join(templates_dir, filename), 'r') as file:
                    assistant = Assistant.from_yaml(file.read())
                    assistant_templates[assistant.slug] = assistant
    except Exception as e:
        logger.error(f"Failed to load assistant template: {e}")
    return assistant_templates  # Returns partial results on error
```

---

## Factory Pattern for Services

Factories create service instances based on configuration.

```python
# src/codemie/service/encryption/encryption_factory.py
class EncryptionFactory(BaseModel):
    @classmethod
    def get_current_encryption_service(cls):
        service_type = cls.get_current_encryption_service_type()
        return cls.get_encryption_service(service_type)

    @classmethod
    def get_encryption_service(cls, encryption_type: EncryptionType):
        if encryption_type == EncryptionType.GCP_ENCRYPTION:
            return GCPKMSEncryptionService()
        elif encryption_type == EncryptionType.AWS_ENCRYPTION:
            return AWSKMSEncryptionService()
        elif encryption_type == EncryptionType.AZURE_ENCRYPTION:
            return AzureKMSEncryptionService()
        else:
            return PlainEncryptionService()
```

**Benefits**: Runtime service selection, easier testing, pluggable implementations.

---

## Abstract Base Classes

Define service contracts for inheritance.

```python
# src/codemie/service/permission/permission_base_service.py
from abc import ABC, abstractmethod

class PermissionBaseService(ABC):
    @abstractmethod
    def run(cls, *args, **kwargs):
        pass

    @staticmethod
    def _find_permission(resource_id: str, resource_type: ResourceType, principal_id: str) -> Permission:
        return Permission.get_by_fields(fields={
            "resource_type": resource_type,
            "resource_id": resource_id,
            "principal_id": principal_id,
        })
```

```python
# src/codemie/service/encryption/base_encryption_service.py
class BaseEncryptionService(ABC):
    @abstractmethod
    def encrypt(self, data):
        pass

    @abstractmethod
    def decrypt(self, data):
        pass
```

**Use for**: Enforcing consistent interfaces, plugin architectures, testability.

---

## Search and Processing Patterns

Services orchestrate complex operations (search + transform + filter).

```python
# src/codemie/service/search_and_rerank/base.py
class SearchAndRerankBase(ABC):
    """
    1. Keyword-based Search: Exact matches on keywords/file_path
    2. Query-based Search: Elasticsearch knn approach
    3. Exact Match Filtering: Always return exact matches first
    4. Reranking: RRF algorithm for relevance
    """

    @abstractmethod
    def execute(self) -> list[Document]:
        pass

    @property
    def es(self) -> ElasticSearchClient:
        return ElasticSearchClient.get_client()
```

**Pattern**: Template method + abstract base class for algorithm steps.

---

## Anti-Patterns

| Anti-Pattern | Problem | Solution |
|---|---|---|
| **Business logic in API layer** | Router handles validation, DB operations → hard to test, duplicate logic | Move logic to service, router only calls `Service.method()` |
| **Direct DB access in service** | `session.query(Model).filter()` in service → tight coupling, can't swap storage | Use repository: `repo.get(id)` |
| **God service** | Single service with 10+ unrelated methods → fragile, hard to test | Split by domain: `UserService`, `EmailService`, `AnalyticsService` |
| **Missing error handling** | No try/except → generic 500 errors | Raise custom exceptions: `ServiceError("User not found")` |
| **Sync instead of async** | Blocking I/O in async codebase → poor performance | Use `async def` for I/O operations |
| **Missing return types** | `def _helper(cls, key):` → violates type hint rules | ALL methods need `-> ReturnType` (including private helpers) |
| **Reimplementing existing service** | Writing DB queries for data another service already exposes → duplication, drift | Reuse existing service; add async variant if only sync exists |

---

## Caching Strategies

Services use various caching patterns to optimize performance.

### Pattern 1: Function-Level Caching with @lru_cache()

For pure functions with limited input space, use `@lru_cache()` decorator.

```python
# src/codemie/service/index/index_service.py
from functools import lru_cache

@lru_cache()
def get_provider_id(name: str) -> Optional[str]:
    provider = Provider.get_by_fields({"name": name})
    provider_id = getattr(provider, "id", None)
    return provider_id
```

**Best for**: Expensive database lookups, API calls with limited parameter combinations.

### Pattern 2: Class-Level Dictionary Caching

Services with state use class-level dictionaries to cache data across instances.

```python
# src/codemie/service/assistant/assistant_service.py
class AssistantService:
    _cached_base_assistant_templates = {}
    _cached_admin_assistant_templates = {}

    def __init__(self):
        templates_dir = config.ASSISTANT_TEMPLATES_DIR
        self._cached_base_assistant_templates = (
            AssistantService._load_assistant_templates_from_dir(templates_dir)
        )

    @classmethod
    def _load_assistant_templates_from_dir(cls, templates_dir: Path) -> dict[str, Assistant]:
        assistant_templates = {}
        try:
            for filename in os.listdir(templates_dir):
                if filename.endswith(".yaml"):
                    with open(os.path.join(templates_dir, filename), 'r') as file:
                        assistant = Assistant.from_yaml(file.read())
                        assistant_templates[assistant.slug] = assistant
        except Exception as e:
            logger.error(f"Failed to load assistant template: {e}")
        return assistant_templates  # Returns partial results on error
```

**Best for**: Configuration loading, template caching, expensive initialization data.

### Pattern 3: Module-Level Singleton

Instantiate service at module level for global cache sharing.

```python
# src/codemie/service/assistant/assistant_service.py (bottom of file)
assistant_service = AssistantService()
```

**Use**: Import singleton instance instead of creating new instances.

```python
from codemie.service.assistant import assistant_service

# Use the singleton
template = assistant_service.get_assistant_template_by_slug("code-reviewer")
```

**Best for**: Services with expensive initialization (loading files, DB connections).

### Pattern 4: LLM Instance Caching

Reuse LLM clients across requests to avoid repeated initialization.

```python
# src/codemie/service/llm_service/llm_service.py
class LLMService:
    _llm_cache: Dict[str, Any] = {}

    def get_llm(self, model_name: str):
        if model_name in self._llm_cache:
            return self._llm_cache[model_name]

        llm = self._initialize_llm(model_name)
        self._llm_cache[model_name] = llm
        return llm
```

**Best for**: Expensive client initialization (LLM, external APIs, database pools).

---

## Class Methods vs Instance Methods

Choose the appropriate method type based on state requirements.

| Method Type | When to Use | Example |
|---|---|---|
| **Instance Method** | Needs instance state (caches, config) | `def get_template(self, slug):`<br>`  return self._cached_templates[slug]` |
| **Class Method** | Needs class state or factory pattern | `@classmethod`<br>`def load_templates(cls, dir):`<br>`  return cls._load_from_dir(dir)` |
| **Static Method** | Pure logic, no class/instance state | `@staticmethod`<br>`def format_markdown(text):`<br>`  return f"# {text}"` |

### Example: Mixed Method Types

```python
# src/codemie/service/index/index_service.py
class IndexStatusService:
    # Static method: No state needed
    @staticmethod
    def get_index_status_markdown(datasource: IndexInfo):
        content = "# Data Source Information\n"
        content += f"### Name: {datasource.repo_name or datasource.full_name}\n"
        return content

    # Class method: Uses database session (class-level resource)
    @classmethod
    def get_index_info_list(cls, user: User, filters: Optional[Dict] = None):
        statement = select(IndexInfo).where(cls._owned_by_user_filter(user))
        with Session(IndexInfo.get_engine()) as session:
            return session.exec(statement).all()
```

---

## Additional Patterns

| Pattern | Use Case | Example |
|---|---|---|
| **Session Management** | Explicit DB sessions in services | `with Session(Model.get_engine()) as session:`<br>`  return session.exec(statement).all()` |
| **Module Singleton** | Expensive initialization (caching) | `assistant_service = AssistantService()`<br>at module level |
| **Validation Chain** | Multi-step business rule checks | `self._check_permissions()`<br>`self._check_config()`<br>`return self._execute()` |

---

## When to Use

### Use Service Layer When

- [x] Implementing business logic (validation, calculations, orchestration)
- [x] Coordinating multiple repositories or external APIs
- [x] Handling complex transactions or workflows
- [x] Encapsulating domain-specific operations
- [x] Need testable business logic separate from HTTP/DB concerns

### Don't Use Service Layer When

- [x] Simple CRUD with no business logic (direct repository call acceptable)
- [x] Implementing HTTP request/response handling (use API layer)
- [x] Writing database queries (use repository layer)
- [x] Managing authentication/sessions (use middleware/API layer)

---

## References

- **Source**: `src/codemie/service/` (assistant, index, conversation, file_service, encryption)
- **Caching Examples**: `src/codemie/service/index/index_service.py`, `src/codemie/service/assistant/assistant_service.py`
- **Related Patterns**: [Layered Architecture](./layered-architecture.md), [Database Patterns](../data/database-patterns.md), [Performance Patterns](../development/performance-patterns.md)
- **CodeMie Architecture**: API → Service → Repository separation of concerns

---