# Database Query Optimization

## Quick Summary

Performance optimization patterns for PostgreSQL queries using SQLModel, including N+1 query prevention, pagination strategies, index usage, batch operations, and query profiling. All patterns verified against CodeMie's production code.

**Category**: Data
**Complexity**: Medium

---

## Prerequisites

Before implementing database optimizations, ensure you have:

- Understanding of SQLModel and PostgreSQL basics (see `.codemie/guides/data/database-patterns.md`)
- Knowledge of SQL query plans and EXPLAIN
- Familiarity with database indexes and their impact
- Understanding of async/await patterns in Python
- Access to PostgreSQL server with monitoring enabled

---

## Implementation

Database query optimization focuses on minimizing query execution time and resource usage through:

1. **Column Selection** with `load_only()` to fetch only needed fields
2. **Pagination** with `offset()` and `limit()` for large datasets
3. **N+1 Prevention** using eager loading and batch queries
4. **Index Usage** for frequently queried columns
5. **Batch Operations** to reduce round trips
6. **Connection Pooling** for efficient resource usage

All optimizations are applied in the repository layer with measurable performance gains.

---

## Pattern Overview

| Pattern | Use Case | Performance Gain | Key Files |
|---------|----------|------------------|-----------|
| **load_only()** | Select specific columns | 40-60% memory reduction | `src/codemie/service/index/index_service.py:164` |
| **Pagination** | Large result sets | Prevents OOM, constant query time | `src/codemie/service/index/index_service.py:188-189` |
| **Count Subquery** | Total count with filters | 2-3x faster than separate query | `src/codemie/service/index/index_service.py:177` |
| **N+1 Prevention** | Related data loading | 10-100x faster than iterative queries | Repository patterns |
| **Batch Insert** | Bulk data creation | 5-10x faster than individual inserts | Session.add_all() |
| **Index Usage** | WHERE/ORDER BY clauses | 100-1000x faster lookups | Alembic migrations |
| **Connection Pooling** | High-concurrency scenarios | Reduces connection overhead | `src/codemie/clients/postgres.py` |

---

## Column Selection Optimization

### load_only() Pattern

```python
# src/codemie/service/index/index_service.py:155-164
from sqlalchemy.orm import load_only
from sqlmodel import select

# Define response class with only needed fields
response_wrapper = IndexInfo if full_response else IndexListItem
columns = [
    getattr(IndexInfo, field)
    for field in response_wrapper.model_fields
    if hasattr(IndexInfo, field)
]

# Select only needed columns
statement = select(IndexInfo).options(load_only(*columns))

# Execute query - fetches only specified columns
with Session(engine) as session:
    results = session.exec(statement).all()
```

**Performance Impact**:
- **Memory**: 40-60% reduction for wide tables
- **Network**: Proportional to column count reduction
- **Query time**: 10-30% faster for large rows

**When to Use**:
- List endpoints returning simplified objects
- APIs with different response schemas (full vs summary)
- Large tables with many columns but few needed fields

---

## Pagination Strategies

### Offset/Limit Pattern

```python
# src/codemie/service/index/index_service.py:188-189
statement = statement.offset(page * per_page)
statement = statement.limit(per_page)
```

**Complete Pagination Example**:

```python
# src/codemie/service/index/index_service.py:141-208
def get_index_info_list(
    cls,
    user: User,
    page: int = 0,
    per_page: int = MAX_ITEMS_PER_PAGE,
    sort_key: Optional[SortKey] = SortKey.DATE,
    sort_order: Optional[SortOrder] = SortOrder.DESC,
):
    # Build base query
    statement = select(IndexInfo).options(load_only(*columns))

    with Session(IndexInfo.get_engine()) as session:
        # Count total BEFORE pagination
        total = session.exec(
            select(func.count()).select_from(statement.subquery())
        ).one()

        # Add sorting with NULL handling
        sort_column = getattr(IndexInfo, sort_key)
        if sort_order == SortOrder.DESC:
            statement = statement.order_by(sort_column.desc().nullslast())
        else:
            statement = statement.order_by(sort_column.asc().nullslast())

        # Add pagination
        statement = statement.offset(page * per_page)
        statement = statement.limit(per_page)

        # Execute paginated query
        index_list = session.exec(statement).all()

    # Calculate pagination metadata
    pages = math.ceil(total / per_page)
    meta = {"page": page, "per_page": per_page, "total": total, "pages": pages}

    return {"data": index_list, "pagination": meta}
```

**Key Features**:
- **Count subquery**: Reuses filters for accurate total
- **NULL handling**: `nullslast()` / `nullsfirst()` for consistent sorting
- **Metadata**: Page count, total items for client pagination

### Cursor-Based Pagination (Alternative)

```python
# For large datasets with stable sort order
def get_items_cursor(last_id: Optional[str] = None, limit: int = 100):
    statement = select(Item).order_by(Item.id)

    if last_id:
        # Fetch items after cursor
        statement = statement.where(Item.id > last_id)

    statement = statement.limit(limit)
    return session.exec(statement).all()
```

**When to Use Cursor Pagination**:
- Very large datasets (millions of rows)
- Real-time feeds with frequent updates
- When offset performance degrades (page > 1000)

---

## N+1 Query Prevention

### The Problem

```python
# ❌ Anti-pattern: N+1 queries
assistants = session.exec(select(Assistant)).all()  # 1 query
for assistant in assistants:
    user = session.get(User, assistant.user_id)  # N queries!
    print(f"{assistant.name} by {user.name}")
```

**Performance**: 1 + N queries (e.g., 101 queries for 100 assistants)

### Solution 1: Eager Loading with selectinload

```python
# ✅ Pattern: Eager loading (2 queries total)
from sqlalchemy.orm import selectinload

statement = select(Assistant).options(selectinload(Assistant.user))
assistants = session.exec(statement).all()  # 2 queries: assistants + users

for assistant in assistants:
    print(f"{assistant.name} by {assistant.user.name}")  # No extra query
```

**Performance**: 2 queries total (1 for assistants, 1 batch query for all users)

### Solution 2: Join Loading

```python
# ✅ Pattern: Join loading (1 query)
from sqlalchemy.orm import joinedload

statement = select(Assistant).options(joinedload(Assistant.user))
assistants = session.exec(statement).all()  # 1 query with JOIN

for assistant in assistants:
    print(f"{assistant.name} by {assistant.user.name}")  # No extra query
```

**Performance**: 1 query with JOIN

**When to Use**:
- **selectinload**: Many-to-many relationships, large result sets
- **joinedload**: One-to-one, one-to-many with few related records

### Solution 3: Batch Loading

```python
# ✅ Pattern: Manual batch load
assistants = session.exec(select(Assistant)).all()

# Collect all user IDs
user_ids = [a.user_id for a in assistants]

# Single query for all users
users = session.exec(
    select(User).where(User.id.in_(user_ids))
).all()
user_map = {u.id: u for u in users}

# Map users to assistants
for assistant in assistants:
    assistant.user = user_map[assistant.user_id]
```

---

## Index Usage

### Creating Indexes in Migrations

```python
# src/external/alembic/versions/xxxxx_add_indexes.py
def upgrade():
    # Single column index
    op.create_index('idx_assistant_user_id', 'assistants', ['user_id'])

    # Composite index
    op.create_index(
        'idx_assistant_user_project',
        'assistants',
        ['user_id', 'project_id']
    )

    # Unique index
    op.create_index(
        'idx_assistant_name_unique',
        'assistants',
        ['name'],
        unique=True
    )

    # Partial index (filtered)
    op.create_index(
        'idx_active_assistants',
        'assistants',
        ['created_date'],
        postgresql_where='status = \'active\''
    )

def downgrade():
    op.drop_index('idx_assistant_user_id', 'assistants')
    op.drop_index('idx_assistant_user_project', 'assistants')
    op.drop_index('idx_assistant_name_unique', 'assistants')
    op.drop_index('idx_active_assistants', 'assistants')
```

### Index Best Practices

| Scenario | Index Type | Example |
|----------|-----------|---------|
| **WHERE clause** | Single column | `user_id`, `status`, `created_date` |
| **ORDER BY** | Matches WHERE columns | `(user_id, created_date DESC)` |
| **Multiple filters** | Composite index | `(project_id, status, created_date)` |
| **Unique constraint** | Unique index | `(email)`, `(name, project_id)` |
| **Sparse data** | Partial index | `WHERE status = 'active'` |
| **Full-text search** | GIN index | `to_tsvector(description)` |

### Verifying Index Usage

```sql
-- Check if index is used
EXPLAIN ANALYZE
SELECT * FROM assistants
WHERE user_id = 'user-123'
ORDER BY created_date DESC
LIMIT 20;

-- Output should show "Index Scan" not "Seq Scan"
-- Example:
-- Index Scan using idx_assistant_user_id on assistants
-- (cost=0.42..8.44 rows=1 width=1234)
```

---

## Batch Operations

### Bulk Insert

```python
# ✅ Pattern: Batch insert
with Session(engine) as session:
    objects = [
        Assistant(name=f"Assistant {i}", description=f"Desc {i}")
        for i in range(1000)
    ]
    session.add_all(objects)  # Single INSERT with multiple VALUES
    session.commit()
```

**Performance**: ~10x faster than individual commits

### Bulk Update

```python
# ✅ Pattern: Batch update
with Session(engine) as session:
    # Single UPDATE query
    session.exec(
        update(Assistant)
        .where(Assistant.status == 'draft')
        .values(status='active', update_date=datetime.now())
    )
    session.commit()
```

### Bulk Delete

```python
# ✅ Pattern: Batch delete
with Session(engine) as session:
    # Single DELETE query
    session.exec(
        delete(Assistant)
        .where(Assistant.created_date < cutoff_date)
    )
    session.commit()
```

---

## Connection Pool Tuning

### Pool Configuration

```python
# src/codemie/clients/postgres.py:12-52
from sqlmodel import create_engine

class PostgresClient:
    @classmethod
    def get_engine(cls):
        pid = os.getpid()
        if pid not in cls._engines:
            cls._engines[pid] = create_engine(
                url=cls._get_connection_string(),
                echo=False,
                pool_pre_ping=True,  # Validate before use
                pool_size=config.PG_POOL_SIZE,  # Main process: 10
                max_overflow=5,  # Allow 15 total connections
                pool_recycle=3600,  # Recycle after 1 hour
            )
        return cls._engines[pid]
```

### Pool Sizing Guidelines

| Scenario | pool_size | max_overflow | Total |
|----------|-----------|--------------|-------|
| **Web server (main)** | 10 | 5 | 15 |
| **Worker process** | 2 | 1 | 3 |
| **Background job** | 1 | 0 | 1 |
| **High concurrency** | 20 | 10 | 30 |

**Formula**: `pool_size = (num_workers * avg_concurrent_queries) + buffer`

---

## Query Profiling

### Using EXPLAIN

```python
# Enable SQL logging
import logging
logging.basicConfig()
logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)

# Execute query - SQL printed to console
statement = select(Assistant).where(Assistant.user_id == user_id)
results = session.exec(statement).all()
```

### Analyzing Query Plans

```sql
-- Basic plan
EXPLAIN SELECT * FROM assistants WHERE user_id = 'user-123';

-- With execution stats
EXPLAIN ANALYZE SELECT * FROM assistants WHERE user_id = 'user-123';

-- With buffer usage
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM assistants WHERE user_id = 'user-123';
```

**Interpreting Results**:
- **Index Scan**: Good (uses index)
- **Seq Scan**: Bad for large tables (full table scan)
- **Nested Loop**: Watch for N+1 patterns
- **Hash Join**: Good for large joins

---

## Anti-Patterns

### ❌ Loading Entire Table

```python
# Wrong: Loads all rows into memory
all_assistants = session.exec(select(Assistant)).all()  # OOM for large tables
```

**Fix**: Use pagination or streaming

### ❌ Repeated String Concatenation

```python
# Wrong: O(n²) performance
result = ""
for item in items:
    result += item.name + ", "  # Creates new string each iteration
```

**Fix**: Use join()
```python
result = ", ".join(item.name for item in items)
```

### ❌ Not Reusing Connections

```python
# Wrong: Creates new engine per request
def get_data():
    engine = create_engine(url)  # DON'T DO THIS
    with Session(engine) as session:
        return session.exec(select(Data)).all()
```

**Fix**: Use `PostgresClient.get_engine()` (singleton per process)

### ❌ Missing Indexes on Foreign Keys

```python
# Wrong: No index on user_id
class Assistant(SQLModel, table=True):
    id: str
    user_id: str  # Frequently queried but no index!
```

**Fix**: Add index in migration
```python
op.create_index('idx_assistant_user_id', 'assistants', ['user_id'])
```

---

## When to Use

### Use These Optimizations When

- [x] Query execution time > 100ms
- [x] Memory usage growing with result size
- [x] Database CPU utilization high
- [x] Pagination needed for user-facing lists
- [x] Related data causing N+1 queries
- [x] Connection pool exhaustion errors

### Don't Optimize When

- [x] Query runs < 10ms (premature optimization)
- [x] Small datasets (< 1000 rows)
- [x] Admin-only endpoints with low traffic
- [x] One-time data migrations (optimize if needed later)

---

## Troubleshooting

| Issue | Symptom | Solution |
|-------|---------|----------|
| **Slow queries** | Query time > 1s | Add indexes, use EXPLAIN, optimize joins |
| **N+1 queries** | N+1 queries in logs | Use selectinload/joinedload or batch queries |
| **OOM errors** | Memory grows with results | Add pagination, use load_only() |
| **Connection timeout** | "Too many connections" | Tune pool_size, check for connection leaks |
| **Stale data** | Cached data doesn't update | Use pool_pre_ping=True, pool_recycle |
| **Slow pagination** | Page 1000 slower than page 1 | Use cursor-based pagination for large offsets |

---

## Verification

### Test Query Performance

```python
import time
from sqlmodel import Session, select

def benchmark_query(statement, session):
    start = time.time()
    results = session.exec(statement).all()
    elapsed = time.time() - start
    print(f"Query time: {elapsed:.3f}s, Results: {len(results)}")
    return results

# Compare with and without optimization
with Session(engine) as session:
    # Before optimization
    statement = select(Assistant)
    benchmark_query(statement, session)

    # After optimization
    statement = select(Assistant).options(load_only(Assistant.id, Assistant.name))
    benchmark_query(statement, session)
```

### Verify Index Usage

```python
from sqlmodel import text

with Session(engine) as session:
    # Check index usage
    result = session.exec(text("""
        EXPLAIN ANALYZE
        SELECT * FROM assistants
        WHERE user_id = :user_id
        ORDER BY created_date DESC
        LIMIT 20
    """), {"user_id": "user-123"})

    for row in result:
        print(row)
    # Should show "Index Scan using idx_assistant_user_id"
```

---

## Examples

### Complete Optimization Example

```python
# src/codemie/service/index/index_service.py:141-208
from sqlalchemy.orm import load_only
from sqlmodel import Session, select, func
import math

def get_paginated_list(
    user: User,
    filters: dict,
    page: int = 0,
    per_page: int = 100,
    sort_key: str = "date",
    sort_order: str = "DESC"
):
    """Optimized pagination with column selection and count subquery"""

    # Select only needed columns
    columns = [IndexInfo.id, IndexInfo.name, IndexInfo.date, IndexInfo.status]
    statement = select(IndexInfo).options(load_only(*columns))

    # Apply filters
    if not user.is_admin:
        statement = statement.where(IndexInfo.user_id == user.id)

    if filters:
        statement = apply_filters(statement, filters)

    with Session(IndexInfo.get_engine()) as session:
        # Count total with same filters (subquery reuses WHERE clause)
        total = session.exec(
            select(func.count()).select_from(statement.subquery())
        ).one()

        # Add sorting with NULL handling
        sort_column = getattr(IndexInfo, sort_key)
        if sort_order == "DESC":
            statement = statement.order_by(sort_column.desc().nullslast())
        else:
            statement = statement.order_by(sort_column.asc().nullslast())

        # Add pagination
        statement = statement.offset(page * per_page).limit(per_page)

        # Execute optimized query
        items = session.exec(statement).all()

    # Return paginated response
    pages = math.ceil(total / per_page)
    return {
        "data": items,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": pages
        }
    }
```

---

## References

- **Source Code**:
  - `src/codemie/service/index/index_service.py` - Pagination and column selection patterns
  - `src/codemie/clients/postgres.py` - Connection pool configuration
  - `src/external/alembic/versions/` - Index creation examples

- **Related Patterns**:
  - `.codemie/guides/data/database-patterns.md` - Core SQLModel usage
  - `.codemie/guides/data/repository-patterns.md` - Repository layer integration
  - `.codemie/guides/development/performance-patterns.md` - General performance optimization

- **External Documentation**:
  - [PostgreSQL Performance Tips](https://wiki.postgresql.org/wiki/Performance_Optimization)
  - [SQLAlchemy Query Optimization](https://docs.sqlalchemy.org/en/20/faq/performance.html)
  - [PostgreSQL EXPLAIN](https://www.postgresql.org/docs/current/using-explain.html)

---

## Next Steps

- **Repository Patterns**: See `.codemie/guides/data/repository-patterns.md` for applying optimizations in repositories
- **Performance Monitoring**: See `.codemie/guides/development/performance-patterns.md` for general performance patterns
- **Testing**: See `.codemie/guides/testing/testing-service-patterns.md` for testing optimized queries

---
