# Performance Patterns

## Quick Summary

Performance optimization patterns for CodeMie including async/await best practices, database query optimization, caching strategies, LangChain optimization, and common anti-patterns to avoid. Implements efficient I/O operations, smart caching, and proper resource management.

**Category**: Development/Performance | **Complexity**: Medium | **Prerequisites**: asyncio, SQLModel, functools, LangChain

---

## Pattern 1: Async/Await Best Practices

| Pattern | Use Case | Example Location |
|---------|----------|------------------|
| **Async I/O** | Non-blocking operations | src/codemie/rest_api/handlers/assistant_handlers.py:1-50 |
| **asyncio.gather()** | Concurrent operations | Run multiple I/O operations in parallel |
| **asyncio.to_thread()** | CPU-bound/blocking code | src/codemie/rest_api/routers/utils.py |

**Example - Concurrent Operations**:
```python
# Run I/O operations concurrently
results = await asyncio.gather(
    fetch_resource(id1),
    fetch_resource(id2),
    return_exceptions=True  # Handle errors gracefully
)
```

**When to Use asyncio.to_thread()**:
- CPU-bound operations (parsing, compression)
- Blocking sync libraries (legacy code)
- File I/O that blocks (non-async file operations)

---

## Pattern 2: Database Query Optimization

| Technique | Impact | Example Location |
|-----------|--------|------------------|
| **Select Only Required Columns** | 50-80% reduction in data transfer | src/codemie/service/index/index_service.py |
| **Pagination** | Prevents memory exhaustion | Use offset/limit with MAX 10,000 items |
| **Avoid N+1 Queries** | N queries → 1 query | Use joinedload() or JOIN |
| **Efficient Counting** | Fast counts without loading data | Use select(func.count()) |

**Example - Column Selection & Count**:
```python
from sqlalchemy.orm import load_only
from sqlmodel import select, func

# Load only needed columns
statement = select(IndexInfo).options(
    load_only(IndexInfo.id, IndexInfo.repo_name, IndexInfo.status)
)

# Efficient count
count = session.exec(select(func.count()).select_from(IndexInfo)).one()
```

**Example - Avoid N+1**:
```python
# ✅ GOOD: Single query with JOIN
statement = select(Assistant).join(User).options(joinedload(Assistant.user))
assistants = session.exec(statement).all()  # All data in 1 query
```

---

## Pattern 3: Caching Strategies

| Strategy | Use Case | Limitations | Example Location |
|----------|----------|-------------|------------------|
| **@lru_cache** | Pure functions, expensive computations | 128 items default, no TTL, hashable args only | src/codemie/service/index/index_service.py:23-27 |
| **Class-Level Cache** | Templates, configs (persistent across requests) | Manual invalidation required | src/codemie/service/assistant/assistant_service.py |
| **LLM Instance Cache** | Reuse expensive LLM connections | Memory overhead | Cache initialization costs |

**Example - Function Caching**:
```python
from functools import lru_cache

@lru_cache()
def get_provider_id(name: str) -> Optional[str]:
    """Cache provider ID lookups - rarely changes"""
    provider = Provider.get_by_fields({"name": name})
    return getattr(provider, "id", None)
```

**Example - Class-Level Cache**:
```python
class AssistantService:
    _cached_templates: dict = {}

    @classmethod
    def load_template(cls, template_id: str):
        if template_id in cls._cached_templates:
            return cls._cached_templates[template_id]
        template = load_from_filesystem(template_id)
        cls._cached_templates[template_id] = template
        return template
```

---

## Pattern 4: LangChain Optimization

| Technique | Benefit | Example Location |
|-----------|---------|------------------|
| **Streaming Callbacks** | Lower perceived latency, better UX | src/codemie/agents/callbacks/agent_streaming_callback.py |
| **Token Management** | Prevent context overflow, cost control | src/codemie/agents/callbacks/tokens_callback.py |
| **Monitoring Callbacks** | Track usage and performance | src/codemie/agents/callbacks/monitoring_callback.py |

**Example - Streaming**:
```python
class StreamingCallback(AsyncCallbackHandler):
    async def on_llm_new_token(self, token: str, **kwargs):
        await self.queue.put(token)  # Stream tokens in real-time
```

**Example - Token Limiting**:
```python
def count_tokens(text: str, model: str = "gpt-4") -> int:
    import tiktoken
    encoding = tiktoken.encoding_for_model(model)
    return len(encoding.encode(text))

# Truncate messages to fit context window
async def process_with_token_limit(messages: List[str], max_tokens: int = 8000):
    total_tokens = 0
    truncated_messages = []
    for msg in messages:
        if total_tokens + count_tokens(msg) > max_tokens:
            break
        truncated_messages.append(msg)
        total_tokens += count_tokens(msg)
    return await llm.generate(truncated_messages)
```

---

## Pattern 5: Common Anti-Patterns to Avoid

| ❌ Anti-Pattern | Impact | ✅ Solution |
|----------------|--------|-------------|
| **N+1 Queries** | 1 + N database queries | Use JOIN or joinedload() → 1 query |
| **Loading Entire Datasets** | Memory exhaustion | Process in batches with offset/limit |
| **String Concatenation in Loops** | O(n²) complexity | Use `"".join()` → O(n) |
| **Not Reusing Connections** | Connection overhead | Reuse session across operations |
| **Blocking I/O in Async** | Blocks event loop | Use async I/O or asyncio.to_thread() |

**Example - Avoid Blocking I/O**:
```python
# ✅ GOOD: Use async file I/O
async def process_file():
    async with aiofiles.open("file.txt") as f:
        data = await f.read()
    return process_data(data)

# Alternative: run in thread pool
async def process_file():
    data = await asyncio.to_thread(read_file, "file.txt")
    return process_data(data)
```

**Example - Batch Processing**:
```python
# ✅ GOOD: Process in batches
batch_size = 1000
offset = 0
while True:
    batch = session.exec(select(LogEntry).offset(offset).limit(batch_size)).all()
    if not batch:
        break
    for log in batch:
        process(log)
    offset += batch_size
```

---

## Pattern 6: Performance Monitoring

| Technique | Purpose | Example Location |
|-----------|---------|------------------|
| **Operation Timing** | Track request duration | src/codemie/rest_api/handlers/assistant_handlers.py |
| **Metrics Collection** | Structured performance data | Log duration, success, metadata |

**Example - Request Timing**:
```python
from time import time

async def handle_request(self, request):
    execution_start = time()
    try:
        result = await self._process(request)
        duration = time() - execution_start
        logger.info(f"Request completed: duration={duration:.2f}s, status=success")
        return result
    except Exception as e:
        duration = time() - execution_start
        logger.error(f"Request failed: duration={duration:.2f}s, error={str(e)}")
        raise
```

---

## Verification

| Test Type | Command | Purpose |
|-----------|---------|---------|
| **Performance Benchmarks** | `pytest tests/performance/ -v --benchmark` | Run performance tests |
| **Profile Slow Tests** | `pytest tests/ --durations=10` | Identify slowest tests |
| **Memory Profiling** | `python -m memory_profiler script.py` | Track memory usage |
| **SQL Query Logging** | `logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)` | See generated queries |

---

## Troubleshooting

| Issue | Symptom | Solutions |
|-------|---------|-----------|
| **Slow DB Queries** | Requests > 1s | Use `load_only()`, add pagination, create indexes, use `joinedload()` |
| **Memory Growth** | Memory increases over time | Clear LRU cache: `func.cache_clear()`, limit cache size: `@lru_cache(maxsize=100)` |
| **Event Loop Blocked** | `RuntimeWarning: coroutine was never awaited` | Always `await` async functions or use `asyncio.to_thread()` for blocking code |

---

## Quick Reference Summary

| Pattern | Key Technique | Performance Impact |
|---------|---------------|-------------------|
| **Async/Await** | Use async for I/O, asyncio.gather() for concurrency | Non-blocking operations, better throughput |
| **DB Optimization** | load_only(), pagination, avoid N+1 | 50-80% reduction in data transfer |
| **Caching** | @lru_cache, class-level caches | Instant response for cached data |
| **LangChain** | Streaming, token management, monitoring | Lower latency, cost control |
| **Anti-Patterns** | Avoid N+1, batch processing, reuse connections | Prevent memory exhaustion, connection overhead |
| **Monitoring** | Track timing, structured metrics | Identify bottlenecks |

---

## Related Documentation

- [Database Patterns](../data/database-patterns.md) - SQL optimization
- [REST API Patterns](../api/rest-api-patterns.md) - Async endpoints
- [Service Layer Patterns](../architecture/service-layer-patterns.md) - Caching strategies
- [Logging Patterns](./logging-patterns.md) - Performance logging
- [Configuration Patterns](./configuration-patterns.md) - Config caching

### Source Files

- Async Handlers: `src/codemie/rest_api/handlers/assistant_handlers.py`
- DB Optimization: `src/codemie/service/index/index_service.py`
- Caching: `src/codemie/service/assistant/assistant_service.py`
- LangChain Callbacks: `src/codemie/agents/callbacks/`
- Async Utilities: `src/codemie/rest_api/routers/utils.py`
