# Database Patterns & SQLModel Usage

## Quick Summary

CodeMie uses SQLModel (SQLAlchemy + Pydantic) for type-safe PostgreSQL operations with connection pooling, Alembic migrations, and pgvector support. All operations follow repository pattern with comprehensive type hints.

**Category**: Data | **Complexity**: Medium | **Stack**: SQLModel 0.0.24+, PostgreSQL, Alembic, pgvector

---

## Pattern Overview

| Pattern | Use Case | Key Files | Lines |
|---------|----------|-----------|-------|
| **Base Models** | Table definitions with common fields | `src/codemie/rest_api/models/base.py` | 20-56, 343-356 |
| **Connection Pooling** | Per-process engine management | `src/codemie/clients/postgres.py` | 12-52 |
| **Session Management** | Safe session lifecycle | `src/codemie/repository/assistants/*.py` | 101-118 |
| **Type Converters** | Pydantic ↔ JSONB conversion | `src/codemie/rest_api/models/base.py` | 227-340 |
| **Query Builders** | Select, filter, paginate, JSONB queries | `src/codemie/repository/assistants/*.py` | 131-135, 372-390 |
| **Alembic Migrations** | Schema version control (developer-only) | `src/external/alembic/` | - |
| **pgvector** | Embedding storage (Elasticsearch primary) | Various | - |

---

## Model Definitions

### Base Model Pattern
See `src/codemie/rest_api/models/base.py:20-56` (CommonBaseModel) and `:343-356` (BaseModelWithSQLSupport)

**Key Features**:
- Auto null byte cleaning for PostgreSQL compatibility
- Common fields: `id` (primary key), `date`, `update_date`
- Built-in CRUD: `get_by_id()`, `save()`, `delete()`, `get_all()`
- Type-safe with comprehensive hints

### Custom JSONB Type Converter
See `src/codemie/rest_api/models/base.py:227-278`

```python
# Store Pydantic models in JSONB columns
from codemie.rest_api.models.base import PydanticType

class Document(SQLModel, table=True):
    id: str
    metadata: Optional[UserMetadata] = Field(
        sa_column=Column('metadata', PydanticType(UserMetadata))
    )
```

**Use**: Bi-directional conversion between Pydantic models and PostgreSQL JSONB with automatic validation.

---

## Connection Management
See `src/codemie/clients/postgres.py:12-52`

**Key Configuration**:
- **Per-process engines**: Fork-safe via PID-keyed cache
- **pool_pre_ping=True**: Validates connections before use
- **pool_size**: Dynamic (full for main, 2 for workers)
- **search_path**: Schema support via connect_args
- **Cleanup**: Automatic atexit disposal

---

## Session Management

**Standard Pattern** (see `src/codemie/repository/assistants/assistant_user_mapping_repository.py:101-118`):
```python
with Session(Model.get_engine()) as session:
    obj = Model(id=str(uuid4()), ...)
    session.add(obj)
    session.commit()
    session.refresh(obj)  # Sync with DB
    return obj
```

**Transaction Flow**: `add()` → `commit()` → `refresh()` | Auto-rollback on exception

---

## Query Patterns

| Pattern | Example | Reference |
|---------|---------|-----------|
| **Select + Filter** | `select(Model).where(Model.field == value)` | `assistant_user_mapping_repository.py:131-135` |
| **Pagination** | `.offset(skip).limit(size).order_by(Model.date.desc())` | `base.py:372-390` |
| **JSONB Path** | `Model.metadata['key'].astext` for nested queries | `base.py:506-524` |
| **JSONB Array Contains** | `Model.array_field.contains([{"key": "val"}])` | `base.py:393-433` |
| **Joins** | Use `selectinload()` for eager loading | Repository examples |

**JSONB Path Query Example**:
```python
# Query nested JSONB: created_by.name
field_expr = Model.get_field_expression("created_by.name")
query = select(Model).where(field_expr == "Alice")
```

---

## Alembic Migrations

### ⚠️ CRITICAL: Developer-Only Operations
**AI agents must NEVER run `alembic` commands. Migrations are human-managed only.**

**Configuration**: See `src/external/alembic/env.py:1-173`
- Schema locking prevents parallel migrations
- All models imported for autogenerate
- Custom enum handling for PostgreSQL

**Migration Files**: `src/external/alembic/versions/`
- Structure: `upgrade()` / `downgrade()` functions
- Example: `07418664fdcf_create_assistants.py`

**Developer Commands** (reference only):
```bash
alembic revision --autogenerate -m "description"  # Generate
alembic upgrade head                               # Apply
alembic downgrade -1                              # Rollback
```

---

## pgvector Integration

**Note**: Elasticsearch is primary for vector search. pgvector is fallback.

```python
# Vector column definition
from pgvector.sqlalchemy import Vector
embedding: Optional[Vector] = Field(sa_column=Column(Vector(1536)))

# Similarity search
select(Doc).order_by(Doc.embedding.cosine_distance(query_vec)).limit(10)

# Performance index (SQL)
CREATE INDEX ON docs USING ivfflat (embedding vector_cosine_ops) WITH (lists=100);
```

---

## Anti-Patterns

| ❌ Never Do | ✅ Do Instead | Why |
|------------|---------------|-----|
| Raw SQL without SQLModel | Use SQLModel ORM | No type safety, SQL injection risk |
| `session = Session(...)` without `with` | `with Session(...) as session:` | Session leaks |
| Loop queries (N+1) | `selectinload()` or joins | Performance degradation |
| `alembic` from AI agents | Developer-only operations | Schema changes require human review |
| F-strings in SQL | Parameterized queries | SQL injection vulnerability |
| `datetime.now(UTC)` in async DB writes | `datetime.now(UTC).replace(tzinfo=None)` | asyncpg rejects tz-aware datetimes for `TIMESTAMP WITHOUT TIME ZONE` columns (psycopg2 silently strips tzinfo — the bug only surfaces on the async path) |
| `Model(key=value, ...)` without `id=` | `Model(id=str(uuid4()), key=value, ...)` | `CommonBaseModel.id` has `default=None`; without an explicit value the primary key is NULL — asyncpg raises a constraint error and SQLAlchemy emits an SAWarning |

---

## When to Use

| ✅ Use PostgreSQL/SQLModel | ❌ Use Alternative |
|---------------------------|-------------------|
| Relational data with FK constraints | Simple key-value → Redis |
| ACID transactions required | Full-text search → Elasticsearch |
| Complex joins/aggregations | Schema-less docs → Elasticsearch |
| Type-safe models with validation | Temporary cache → Redis/memory |
| Schema migrations (Alembic) | - |

---

## Troubleshooting

| Issue | Solution | Config/Code |
|-------|----------|-------------|
| Stale connections | `pool_pre_ping=True` validates before use | `postgres.py:178` |
| Fork errors | Per-process engine cache | `postgres.py:171-174` |
| Null byte errors | Auto-sanitization in `__init__` | `base.py:72-80` |
| Migration conflicts | Alembic table locking | `env.py:175` |
| Schema not found | `search_path` in connect_args | `postgres.py:181` |
| Type conversion | Use `PydanticType` for JSONB | `base.py:227-278` |
| N+1 queries | `selectinload()` or joins | See repository patterns |
| `asyncpg.exceptions.DataError: can't subtract offset-naive and offset-aware datetimes` | Use `datetime.now(UTC).replace(tzinfo=None)` for all `TIMESTAMP WITHOUT TIME ZONE` columns. psycopg2 (sync) silently converts; asyncpg does not. Affects any model inheriting `CommonBaseModel` (`date`, `update_date`). | `base.py:37-39` |
| `SAWarning: Column 'X.id' … no Python-side or server-side default` followed by async commit failure | Always pass `id=str(uuid4())` when constructing new model instances. `CommonBaseModel.id` defaults to `None` in Python but the DB column is `NOT NULL`. | `base.py:37` |

---

## Complete CRUD Example

See `src/codemie/repository/assistants/assistant_user_mapping_repository.py`

```python
# Create
with Session(engine) as session:
    obj = Model(id=str(uuid4()), ...)
    session.add(obj)
    session.commit()
    session.refresh(obj)

# Read
with Session(engine) as session:
    query = select(Model).where(Model.field == value)
    result = session.exec(query).first()

# Update
with Session(engine) as session:
    obj = session.get(Model, id)
    obj.field = new_value
    session.add(obj)
    session.commit()

# Delete
with Session(engine) as session:
    obj = session.get(Model, id)
    session.delete(obj)
    session.commit()

# Transaction with auto-rollback
with Session(engine) as session:
    try:
        session.add(obj1)
        session.add(obj2)
        session.commit()  # Atomic
    except Exception:
        raise  # Auto-rollback
```

---

## References

**Source Code**:
- `src/codemie/rest_api/models/base.py` - Base models, type converters
- `src/codemie/clients/postgres.py` - Connection pooling
- `src/codemie/repository/assistants/` - Repository implementations
- `src/external/alembic/` - Migration files

**Related Guides**:
- `.codemie/guides/data/repository-patterns.md` - Repository layer
- `.codemie/guides/data/database-optimization.md` - Performance patterns
- `.codemie/guides/architecture/layered-architecture.md` - Architecture context

**External**:
- [SQLModel Docs](https://sqlmodel.tiangolo.com/)
- [Alembic Docs](https://alembic.sqlalchemy.org/)
- [pgvector GitHub](https://github.com/pgvector/pgvector)

---
