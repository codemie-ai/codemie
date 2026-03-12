# Repository Layer & Data Access Patterns

**Category**: Data | **Complexity**: Medium | **Prerequisites**: SQLModel, Layered Architecture

CodeMie implements Repository pattern for data access abstraction with two distinct approaches: File-based (multi-cloud storage) and SQL-based (PostgreSQL with SQLModel).

---

## Repository Architecture Overview

| Type | Base Class | Implementations | Purpose |
|------|-----------|-----------------|---------|
| **File Repositories** | `FileRepository` (ABC) | FileSystem, AWS S3, Azure Blob, GCP Cloud Storage | Multi-cloud file storage abstraction |
| **SQL Repositories** | Abstract Repository (ABC) | SQLModel concrete implementations | Database CRUD operations with ORM |
| **Elastic Repositories** | `BaseElasticRepository` | Elasticsearch implementations | Document search and retrieval |
| **Factory** | `FileRepositoryFactory` | N/A | Instantiate file repositories by storage type |

Source: `src/codemie/repository/`

---

## Pattern 1: File Repository Abstraction

### Structure

```python
from abc import ABC, abstractmethod
from codemie_tools.base.file_object import FileObject

class FileRepository(ABC):
    """Abstract base for file storage operations across providers"""

    @abstractmethod
    def write_file(self, name: str, mime_type: str, owner: str, content: Any = None) -> FileObject:
        pass

    @abstractmethod
    def read_file(self, file_name: str, owner: str, mime_type: str = None) -> FileObject:
        pass

    @abstractmethod
    def create_directory(self, name: str, owner: str) -> DirectoryObject:
        pass
```

Source: `src/codemie/repository/base_file_repository.py:29-69`

### Concrete Implementation Example

```python
class FileSystemRepository(FileRepository):
    def write_file(self, name: str, mime_type: str, owner: str, content: Any = None) -> FileObject:
        directory = self.create_directory(owner=owner, name=name)
        file_path = f"{directory.get_path()}/{name}"
        mode = 'wb' if isinstance(content, bytes) else 'w'
        with open(file_path, mode) as file:
            file.write(content)
        return FileObject(name=name, mime_type=mime_type, path=file_path, owner=owner, content=content)

    def read_file(self, file_name: str, owner: str, mime_type: str = None) -> FileObject:
        file_path = f"{config.FILES_STORAGE_DIR}/{owner}/{file_name}"
        read_mode = 'r' if mime_type.startswith('text') else 'rb'
        with open(file_path, read_mode) as file:
            content = file.read()
        return FileObject(name=file_name, mime_type=mime_type, path=os.path.dirname(file_path),
                         content=content, owner=owner)
```

Source: `src/codemie/repository/file_system_repository.py:15-79`

**Key Points**: Type detection (text vs binary) | Error handling with try/except | Owner-based directory isolation

---

## Pattern 2: Repository Factory

### Structure

```python
from enum import Enum
from pydantic import BaseModel

class FileStorageType(Enum):
    GCP = 'gcp'
    AWS = 'aws'
    AZURE = 'azure'
    FILE_SYSTEM = 'filesystem'

class FileRepositoryFactory(BaseModel):
    @classmethod
    def get_current_repository(cls) -> FileRepository:
        storage_type = cls.get_current_storage_type()
        return cls._get_repository(storage_type)

    @classmethod
    def _get_repository(cls, storage_type: FileStorageType) -> FileRepository:
        if storage_type == FileStorageType.GCP:
            return GCPFileRepository()
        elif storage_type == FileStorageType.AZURE:
            return AzureFileRepository(
                connection_string=config.AZURE_STORAGE_CONNECTION_STRING,
                storage_account_name=config.AZURE_STORAGE_ACCOUNT_NAME
            )
        elif storage_type == FileStorageType.FILE_SYSTEM:
            return FileSystemRepository()
        elif storage_type == FileStorageType.AWS:
            return AWSFileRepository(region_name=config.AWS_S3_REGION, root_bucket=config.AWS_S3_BUCKET_NAME)
```

Source: `src/codemie/repository/repository_factory.py:10-46`

**Usage**: Service layer calls `FileRepositoryFactory.get_current_repository()` to obtain correct implementation based on config

---

## Pattern 3: SQLModel Repository Pattern

### Abstract Repository Interface

```python
from abc import ABC, abstractmethod
from typing import Optional, List, Any

class AssistantUserInterationRepository(ABC):
    """Abstract interface defining data access contract"""

    @abstractmethod
    def record_usage(self, assistant_id: str, user_id: str, project: Optional[str] = None) -> Any:
        """Create or update usage record"""
        pass

    @abstractmethod
    def get_by_assistant_and_user(self, assistant_id: str, user_id: str) -> Optional[Any]:
        """Retrieve single record by composite key"""
        pass

    @abstractmethod
    def get_unique_users_count(self, assistant_id: str) -> int:
        """Aggregate query for counts"""
        pass
```

Source: `src/codemie/repository/assistants/assistant_user_interaction_repository.py:19-92`

---

## Pattern 4: SQLModel CRUD Implementation

### Session Management & Basic CRUD

```python
from sqlmodel import Session, select
from datetime import datetime, UTC

class SQLAssistantUserInterationRepository(AssistantUserInterationRepository):

    def record_usage(self, assistant_id: str, user_id: str, project: Optional[str] = None) -> AssistantUserInterationSQL:
        usage = self.get_by_assistant_and_user(assistant_id, user_id)

        if usage:
            # UPDATE existing
            with Session(AssistantUserInterationSQL.get_engine()) as session:
                usage.usage_count += 1
                usage.last_used_at = datetime.now(UTC)
                session.add(usage)
                session.commit()
                session.refresh(usage)  # Reload from DB to get generated values
                return usage
        else:
            # CREATE new
            with Session(AssistantUserInterationSQL.get_engine()) as session:
                usage = AssistantUserInterationSQL(
                    id=str(uuid4()),
                    assistant_id=assistant_id,
                    user_id=user_id,
                    usage_count=1,
                    first_used_at=datetime.now(UTC),
                    last_used_at=datetime.now(UTC)
                )
                session.add(usage)
                session.commit()
                session.refresh(usage)
                return usage
```

Source: `src/codemie/repository/assistants/assistant_user_interaction_repository.py:114-157`

**Key Patterns**:
- `with Session(Model.get_engine()) as session:` — Context manager handles connection lifecycle
- `session.add(obj)` → `session.commit()` → `session.refresh(obj)` — Standard write sequence
- Explicit ID generation with `uuid4()` to avoid null constraint violations
- UTC timestamps for consistency

---

## Pattern 5: SQLModel Query Patterns

### Basic Read Query

```python
def get_by_assistant_and_user(self, assistant_id: str, user_id: str) -> Optional[AssistantUserInterationSQL]:
    with Session(AssistantUserInterationSQL.get_engine()) as session:
        query = select(AssistantUserInterationSQL).where(
            AssistantUserInterationSQL.assistant_id == assistant_id,
            AssistantUserInterationSQL.user_id == user_id
        )
        return session.exec(query).first()  # Returns None if not found
```

Source: `src/codemie/repository/assistants/assistant_user_interaction_repository.py:177-192`

### Count with Subquery (N+1 Prevention)

```python
from sqlmodel import func

def get_unique_users_count(self, assistant_id: str) -> int:
    with Session(AssistantUserInterationSQL.get_engine()) as session:
        query = select(func.count()).select_from(
            select(AssistantUserInterationSQL)
            .where(AssistantUserInterationSQL.assistant_id == assistant_id)
            .subquery()
        )
        return session.exec(query).one()  # Returns single integer
```

Source: `src/codemie/repository/assistants/assistant_user_interaction_repository.py:159-175`

**Subquery technique** avoids N+1 by aggregating in single query.

### Filtering with Multiple Conditions

```python
from sqlalchemy import and_

def get_reactions_by_user(self, user_id: str, reaction_type: Optional[ReactionType] = None) -> List[AssistantUserInterationSQL]:
    with Session(AssistantUserInterationSQL.get_engine()) as session:
        conditions = [AssistantUserInterationSQL.user_id == user_id]

        if reaction_type is not None:
            conditions.append(AssistantUserInterationSQL.reaction == reaction_type)
        else:
            conditions.append(AssistantUserInterationSQL.reaction.is_not(None))

        query = select(AssistantUserInterationSQL).where(and_(*conditions))
        return session.exec(query).all()
```

Source: `src/codemie/repository/assistants/assistant_user_interaction_repository.py:240-265`

**Dynamic filtering**: Build condition list, apply with `and_(*conditions)`

---

## Pattern 6: SQLModel Model Definition

```python
from sqlmodel import Field, Index
from sqlalchemy import UniqueConstraint
from datetime import datetime, UTC
from uuid import uuid4

class AssistantUserInterationSQL(BaseModelWithSQLSupport, AssistantUserInterationBase, table=True):
    __tablename__ = "assistant_user_interaction"

    # Primary key with default factory
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)

    # Indexed fields for query performance
    assistant_id: str = Field(index=True)
    user_id: str = Field(index=True)

    # Timestamps with UTC default
    first_used_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_used_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Optional fields
    reaction: Optional[ReactionType] = Field(default=None)
    project: Optional[str] = Field(default=None, index=True)

    __table_args__ = (
        UniqueConstraint('assistant_id', 'user_id', name='uix_assistant_user'),
        Index('ix_assistant_usage_assistant_id', 'assistant_id'),
        Index('ix_assistant_usage_user_id', 'user_id'),
        Index('ix_assistant_usage_project', 'project'),
        Index('ix_assistant_usage_reaction', 'reaction'),
        Index('ix_assistant_usage_last_used_at', 'last_used_at'),
    )
```

Source: `src/codemie/rest_api/models/usage/assistant_user_interaction.py:23-51`

**Key Patterns**:
- `table=True` — SQLModel ORM mapping
- `default_factory` for dynamic defaults (UUID, timestamp)
- Indexes defined inline (`index=True`) and in `__table_args__`
- `UniqueConstraint` for composite keys
- Type hints required (SQLModel uses for SQL types)

---

## Pattern 7: Elasticsearch Repository

```python
from abc import ABC, abstractmethod
from elasticsearch import Elasticsearch

class BaseElasticRepository(ABC):
    def __init__(self, elastic_client: Elasticsearch, index_name: str):
        self._elastic_client = elastic_client
        self._index_name = index_name

    def get_by_id(self, _id: str) -> AbstractElasticModel:
        item = self._elastic_client.get(index=self._index_name, id=_id)
        return self.to_entity(item["_source"])

    def get_all(self, query: Optional[dict] = None, limit: Optional[int] = None) -> list[AbstractElasticModel]:
        query = query if query else {"match_all": {}}
        size = limit if limit is not None else 10000
        results = self._elastic_client.search(index=self._index_name, query=query, size=size)
        return [self.to_entity(hit["_source"]) for hit in results["hits"]["hits"]]

    def save(self, entity: AbstractElasticModel) -> AbstractElasticModel:
        self._elastic_client.index(index=self._index_name, id=entity.get_identifier(),
                                   document=entity.model_dump())
        return entity

    @abstractmethod
    def to_entity(self, item: dict) -> AbstractElasticModel:
        """Subclass implements mapping from dict to entity"""
        pass
```

Source: `src/codemie/repository/base_elastic_repository.py:11-68`

**Pattern**: Base class provides common ES operations, subclasses implement `to_entity()` for type conversion

---

## Pattern 8: Error Handling

### File Repository Error Pattern

```python
def write_file(self, name: str, mime_type: str, owner: str, content: Any = None) -> FileObject:
    try:
        logger.debug(f"Writing file to {directory.get_path()}. FileName: {name}")
        mode = 'wb' if isinstance(content, bytes) else 'w'
        with open(file_path, mode) as file:
            file.write(content)
    except Exception as e:
        logger.error(f"Error writing file to {file_path}: {e}")
        raise  # Re-raise to propagate to caller
    return FileObject(...)
```

Source: `src/codemie/repository/file_system_repository.py:23-31`

**Pattern**: Log error details, re-raise to allow caller to handle | No exception transformation

### SQL Repository Error Pattern

```python
def set_reaction_value(self, assistant_id: str, user_id: str, reaction_value: Optional[ReactionType]) -> bool:
    # Validation before DB operations
    if reaction_value is not None and reaction_value not in [ReactionType.LIKE, ReactionType.DISLIKE]:
        logger.error(f"Invalid reaction value: {reaction_value}")
        return False  # Early return on validation failure

    usage = self.get_by_assistant_and_user(assistant_id, user_id)
    if not usage and reaction_value:
        usage = self.record_usage(assistant_id, user_id)  # Create if needed
    elif not usage and not reaction_value:
        return True  # Nothing to do

    with Session(AssistantUserInterationSQL.get_engine()) as session:
        usage.reaction = reaction_value
        session.add(usage)
        session.commit()
        return True
```

Source: `src/codemie/repository/assistants/assistant_user_interaction_repository.py:267-303`

**Pattern**: Validation first | Create-if-needed logic | Boolean return for success/failure | Session auto-rolls back on exception

---

## Anti-Patterns

### ❌ WRONG: Session Leaks

```python
# BAD: Session not closed
session = Session(Model.get_engine())
query = select(Model).where(...)
result = session.exec(query).first()
return result  # Session never closed!
```

**Why**: Session holds DB connection | Memory leak | Connection pool exhaustion

### ✅ RIGHT: Context Manager

```python
# GOOD: Context manager ensures cleanup
with Session(Model.get_engine()) as session:
    query = select(Model).where(...)
    result = session.exec(query).first()
return result
```

### ❌ WRONG: N+1 Query Problem

```python
# BAD: One query per iteration
assistants = get_all_assistants()  # Query 1
for assistant in assistants:
    count = get_user_count(assistant.id)  # Query 2, 3, 4, ... N
    assistant.user_count = count
```

**Why**: N+1 queries (1 + N) | Slow for large datasets | DB load

### ✅ RIGHT: Aggregate with Subquery

```python
# GOOD: Single query with aggregation
query = select(func.count()).select_from(
    select(Model).where(Model.assistant_id == assistant_id).subquery()
)
return session.exec(query).one()
```

### ❌ WRONG: Missing Transaction

```python
# BAD: No commit, changes lost
with Session(Model.get_engine()) as session:
    obj = Model(...)
    session.add(obj)
    # Missing: session.commit()
return obj
```

**Why**: Changes not persisted | Rollback on session close

### ✅ RIGHT: Commit & Refresh

```python
# GOOD: Commit then refresh
with Session(Model.get_engine()) as session:
    obj = Model(...)
    session.add(obj)
    session.commit()  # Persist to DB
    session.refresh(obj)  # Reload with generated values
return obj
```

---

## Integration with Service Layer

Services call repositories for data access, never directly accessing database:

```python
class AssistantService:
    def __init__(self):
        self.usage_repo = SQLAssistantUserInterationRepository()

    def track_usage(self, assistant_id: str, user_id: str) -> dict:
        # Service orchestrates, repository handles data
        usage = self.usage_repo.record_usage(assistant_id, user_id)
        return {"usage_count": usage.usage_count, "last_used": usage.last_used_at}
```

See: [Service Layer Patterns](../architecture/service-layer-patterns.md) | [Layered Architecture](../architecture/layered-architecture.md)

---

## When to Use

### Use File Repository Pattern When
- [ ] Multi-cloud storage required (AWS/Azure/GCP/local)
- [ ] Storage provider may change
- [ ] File operations abstracted from business logic

### Use SQLModel Repository Pattern When
- [ ] PostgreSQL database access
- [ ] Type-safe ORM preferred over raw SQL
- [ ] CRUD operations with relationships

### Use Elasticsearch Repository When
- [ ] Full-text search required
- [ ] Document-based data model
- [ ] High-volume read queries

---

## References

**Source Files**:
- `src/codemie/repository/base_file_repository.py` — File repository abstraction
- `src/codemie/repository/repository_factory.py` — Factory pattern implementation
- `src/codemie/repository/assistants/assistant_user_interaction_repository.py` — SQLModel repository
- `src/codemie/rest_api/models/usage/assistant_user_interaction.py` — SQLModel model definition
- `src/codemie/repository/base_elastic_repository.py` — Elasticsearch abstraction

**Related Patterns**:
- [Service Layer Patterns](../architecture/service-layer-patterns.md) — Integration with services
- [Layered Architecture](../architecture/layered-architecture.md) — Repository position in architecture

**Dependencies**: SQLModel ^0.0.24, FastAPI ^0.115.0, Elasticsearch 0.3.2
