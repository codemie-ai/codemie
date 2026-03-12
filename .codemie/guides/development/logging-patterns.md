# Logging Patterns

## Quick Summary

Structured logging using context variables (uuid, user_id, conversation_id) for request tracking, JSON output for production, and f-string formatting for all log messages. Implements consistent logging across API, service, workflow, and agent layers.

**Category**: Development/Logging | **Complexity**: Medium

---

## 🚨 CRITICAL: NEVER Use `extra` Parameter

**This is a PROJECT-SPECIFIC ANTI-PATTERN that causes LogRecord field conflicts.**

### ❌ FORBIDDEN Pattern

```python
# WRONG - Causes conflicts with Python's reserved LogRecord fields
logger.info("Processing request", extra={"user_id": user_id})
logger.info("Found items", extra={"count": count, "filters": filters})
```

**Problem**: The `extra` parameter conflicts with Python's reserved LogRecord attribute names (name, msg, args, created, filename, funcName, levelname, levelno, lineno, module, msecs, pathname, process, processName, relativeCreated, thread, threadName, exc_info, exc_text, stack_info, etc.).

### ✅ REQUIRED Pattern: Use F-Strings

```python
# CORRECT - Include all context in f-string message
logger.info(f"Processing request for user={user_id}")
logger.info(f"Found {count} items with filters={filters}")
```

**Why F-Strings**:
- No LogRecord conflicts
- Context automatically included in message
- Works with context variables (uuid, user_id, conversation_id)
- Simpler and more readable

---

## Logger Setup

### Configuration

**In every Codemie module, import the pre-configured singleton — never create your own logger instance:**

```python
# ✅ CORRECT — import the pre-configured singleton
from codemie.configs.logger import logger

# ✅ CORRECT — also import helpers when needed
from codemie.configs.logger import logger, set_logging_info

# ❌ WRONG — logging.getLogger() bypasses context enrichment (uuid/user_id/conversation_id)
import logging
logger = logging.getLogger(__name__)           # Never do this
logger = logging.getLogger("codemie")         # Never do this
logger = logging.getLogger("codemie_tools")   # Never do this
```

The singleton is defined once in `src/codemie/configs/logger.py`:

```python
# Internal — src/codemie/configs/logger.py (DO NOT replicate in other modules)
logger = logging.getLogger(log_config.LOGGER_NAME)  # LOGGER_NAME = "codemie"
```

Log output formats (defined in `LogConfig`):

```python
# Environment-specific formats
LOCAL_LOG_FORMAT = (
    'Timestamp: %(asctime)s | Level: %(levelname)s | UUID: %(uuid)s \n'
    'User ID: %(user_id)s | Conversation ID: %(conversation_id)s\n'
    'Message: %(message)s\n'
)

LOG_FORMAT = (  # Production JSON format
    '{"timestamp": "%(asctime)s", "level": "%(levelname)s", '
    '"uuid": "%(uuid)s", "user_id": "%(user_id)s", '
    '"conversation_id": "%(conversation_id)s", "message": "%(message)s"}'
)
```

**Source**: `src/codemie/configs/logger.py`

### Key Components

| Component | Purpose | Implementation |
|-----------|---------|----------------|
| `LogFormatter` | Custom formatter with exc_info handling | Converts exceptions to readable format |
| `record_factory` | Enriches log records with context | Adds uuid, user_id, conversation_id automatically |
| `set_logging_info` | Sets context variables per request | Called in middleware for request tracking |
| `json_serial` | JSON serialization for exceptions | Safely serializes Exception objects |

---

## Context Enrichment

### Context Variables Pattern

```python
# src/codemie/configs/logger.py:71-74, 115-128
import contextvars

# Global context variables
logging_uuid = contextvars.ContextVar("uuid")
logging_user_id = contextvars.ContextVar("user_id")
logging_conversation_id = contextvars.ContextVar("conversation_id")
current_user_email = contextvars.ContextVar("user_email", default="unknown")

# Set context at request start (middleware)
def set_logging_info(
    uuid: str = '-',
    user_id: str = '-',
    conversation_id: str = '-',
    user_email: str = "-"
):
    uuid = uuid if uuid is not None else '-'
    user_id = user_id if user_id is not None else '-'
    conversation_id = conversation_id if conversation_id is not None else '-'
    logging_uuid.set(uuid)
    logging_user_id.set(user_id)
    current_user_email.set(user_email)
    logging_conversation_id.set(conversation_id)
    logging.setLogRecordFactory(record_factory)

# Usage in middleware
set_logging_info(
    uuid=str(uuid.uuid4()),
    user_id=current_user.id,
    conversation_id=conversation_id,
    user_email=current_user.email
)

# All subsequent logs automatically include context
logger.info(f"Processing user request for project={project_name}")
# Output: {"timestamp": "...", "uuid": "abc-123", "user_id": "user_456", "conversation_id": "conv_789", "message": "Processing user request for project=MyProject"}
```

**Source**: `src/codemie/configs/logger.py`

### Custom Record Factory

```python
# src/codemie/configs/logger.py:93-105
def record_factory(*args, **kwargs):
    """Enriches ALL log records with context automatically"""
    record = old_factory(*args, **kwargs)
    record.uuid = logging_uuid.get('-')
    record.user_id = logging_user_id.get('-')
    record.conversation_id = logging_conversation_id.get('-')
    record.msg = process_record_msg(record.msg)  # JSON-safe
    return record

logging.setLogRecordFactory(record_factory)
```

**Effect**: Every logger call automatically includes uuid, user_id, conversation_id without extra parameter.

---

## Log Levels

### Decision Table

| Level | When to Use | Example Scenarios | exc_info Required |
|-------|-------------|-------------------|-------------------|
| **DEBUG** | Development/troubleshooting | Variable values, function entry/exit, state transitions | No |
| **INFO** | Normal operations | Request start/complete, successful operations | No |
| **WARNING** | Potential issues (recoverable) | Deprecated features, retries, non-critical failures | No |
| **ERROR** | Errors with recovery | Handled exceptions, operations with fallback | **Yes** |
| **CRITICAL** | System failures (unrecoverable) | Service crashes, data corruption | **Yes** |

### Usage Patterns

```python
# INFO - Normal events
logger.info("Starting assistant versioning migration")
logger.info(f"Found {len(assistants)} assistants")

# WARNING - Potential issues
if stats['errors'] > 0:
    logger.warning(f"{stats['errors']} assistants failed to migrate")

# ERROR - ALWAYS include exc_info=True
try:
    result = process_data()
except Exception as e:
    logger.error(f"Migration failed: {str(e)}", exc_info=True)

# DEBUG - Development only
logger.debug(f"Processing record: {record_id}")
```

**Source**: `src/external/migrations/migrate_assistants_to_versions.py`, `src/codemie/workflows/workflow.py`

---

## Sensitive Data Handling

### Never Log Sensitive Data

| Data Type | Action | Example |
|-----------|--------|---------|
| **PII** (emails, names) | Use user_id context | ✅ `user_id` context variable, ❌ email in message |
| **Credentials** | **NEVER log** | ❌ Passwords, API keys, tokens |
| **Exception Details** | Serialize safely | ✅ `json_serial()` extracts type/message only |
| **User Input** | Sanitize/truncate | Limit length, remove special characters |

```python
# ❌ WRONG - Logs sensitive data
logger.info(f"User login: {username}, password: {password}")
logger.error(f"API call failed with key: {api_key}")

# ✅ CORRECT - Use context variables
set_logging_info(user_id=user.id)
logger.info("User login successful")
logger.error("API call failed", exc_info=True)
```

### JSON Serialization for Exceptions

```python
# src/codemie/configs/logger.py:78-90
def json_serial(obj):
    """JSON serializer for objects not serializable by default"""
    if isinstance(obj, Exception):
        exception_data = {'type': obj.__class__.__name__, 'message': str(obj)}
        if isinstance(obj, json.JSONDecodeError):
            exception_data['lineno'] = obj.lineno
            exception_data['colno'] = obj.colno
            exception_data['pos'] = obj.pos
            exception_data['doc'] = obj.doc
        return exception_data
    return str(obj)

def process_record_msg(msg):
    """Make log message JSON-safe for production"""
    if config.is_local:
        return msg
    return json.dumps(msg, default=json_serial)[1:-1]
```

**Source**: `src/codemie/configs/logger.py`

---

## Error Logging with exc_info

### Pattern: ALWAYS Use exc_info=True for ERROR/CRITICAL

```python
# ERROR/CRITICAL levels - include exc_info=True for tracebacks
try:
    result = process_workflow()
except ValidationError as e:
    logger.error(f"Validation failed: {e}", exc_info=True)
except Exception as e:
    logger.error(f"Unexpected error: {e}", exc_info=True)
    raise  # Re-raise after logging

# Custom formatter handles exc_info
# src/codemie/configs/logger.py:13-24
class LogFormatter(uvicorn.logging.DefaultFormatter):
    def format(self, record):
        if record.exc_info:
            record.msg = repr(super().formatException(record.exc_info))
            if config.is_local:
                record.msg = record.msg.replace("\\n", "\n")  # Readable locally
            record.exc_info = None
            record.exc_text = None
            record.levelname = "ERROR"
        return super().format(record)
```

**Sources**: `src/codemie/workflows/workflow.py:720`, `src/external/migrations/migrate_assistants_to_versions.py:48`

**Related**: [Error Handling Patterns](./error-handling.md) for exception hierarchies

---

## Common Patterns

### Pattern 1: Request Lifecycle Logging

```python
# src/codemie/rest_api/handlers/assistant_handlers.py
def _populate_conversation_history(self, request: AssistantChatRequest):
    # Log at operation start
    if request.history or not request.conversation_id:
        logger.debug(f"History already provided or conversation_id missing. History messages: {len(request.history)}, conversation_id: {request.conversation_id or 'None'}")
        return

    # Log retrieval
    conversation = Conversation.find_by_id(request.conversation_id)
    if not conversation:
        logger.debug(f"Conversation {request.conversation_id} not found for user {self.user.id}")
        return

    # Log access denied
    if not Ability(self.user).can(Action.READ, conversation):
        logger.warning(f"User {self.user.id} denied access to conversation {request.conversation_id} owned by {conversation.user_id}")
        raise ExtendedHTTPException(...)

    # Log success
    try:
        request.history = conversation.to_chat_history()
        logger.debug(f"Retrieved conversation history for conversation_id: {request.conversation_id}, messages: {len(request.history)}, user_id: {self.user.id}")
    except Exception as e:
        logger.error(f"Unexpected error converting history for {request.conversation_id}: {str(e)}", exc_info=True)
        raise ExtendedHTTPException(...) from e
```

**Source**: `src/codemie/rest_api/handlers/assistant_handlers.py:63-128`

### Pattern 2: Migration Script Logging

```python
# src/external/migrations/migrate_assistants_to_versions.py:27-48
logger.info("=" * 80)
logger.info("Starting assistant versioning migration")
logger.info("=" * 80)

stats = AssistantVersionMigrationService.migrate_all_assistants()

logger.info("=" * 80)
logger.info("Migration completed successfully")
logger.info(f"Total assistants: {stats['total_assistants']}")
logger.info(f"Migrated: {stats['migrated']}")
logger.info(f"Skipped: {stats['skipped']}")
logger.info(f"Errors: {stats['errors']}")
logger.info("=" * 80)

if stats['errors'] > 0:
    logger.warning(f"{stats['errors']} assistants failed to migrate. Check logs for details.")
```

### Pattern 3: Workflow Error Logging

```python
# src/codemie/workflows/workflow.py:718-720
exception_type = type(e).__name__
error_message = str(e)
stacktrace = traceback.format_exc()

chunks_collector.append(f"AI Agent run failed: {exception_type}: {error_message}")
logger.error(f"AI Agent run failed: {stacktrace}", exc_info=True)
```

### Pattern 4: Performance Timing

```python
execution_start = time()
result = await execute_workflow()
duration = time() - execution_start

logger.info(f"Workflow execution completed in {duration:.2f}s, status={result.status}")
```

---

## Anti-Patterns to Avoid

### ❌ Creating Module-Local Logger Instances

```python
# WRONG — logging.getLogger(__name__) or any variant bypasses the configured
# record_factory and loses uuid/user_id/conversation_id context enrichment
import logging
logger = logging.getLogger(__name__)
logger = logging.getLogger("codemie")
logger = logging.getLogger("codemie_tools")

# CORRECT — always import the singleton
from codemie.configs.logger import logger
```

### ❌ Using extra Parameter

```python
# WRONG - Causes LogRecord conflicts
logger.info("Processing", extra={"user_id": user_id})

# CORRECT - Use f-string
logger.info(f"Processing for user={user_id}")
```

### ❌ Implicit String Concatenation

```python
# WRONG - Implicit concatenation (linter error: ISC001)
logger.info(
    f"Super admin detected: user_id={user_id}, "
    f"granting unrestricted access to all analytics data"
)

# WRONG - Using + operator in logging (linter error: G003)
logger.info(
    f"Super admin detected: user_id={user_id}, " +
    f"granting unrestricted access to all analytics data"
)

# CORRECT - Single-line f-string (ALWAYS preferred)
logger.info(f"Super admin detected: user_id={user_id}, granting unrestricted access to all analytics data")
```

**Why**:
- Implicit string concatenation (adjacent string literals) triggers linter error ISC001
- Using `+` operator in logging statements triggers linter error G003
- **Always prefer single-line f-strings** - they're clear, concise, and linter-compliant
- Only split across lines if the message is genuinely too long (> 120 chars), and even then consider refactoring

### ❌ Missing Context

```python
# WRONG - No context for debugging
logger.error("Operation failed")

# CORRECT - Include relevant context
logger.error(f"Database operation failed: operation=update_user, user_id={user_id}, table=users", exc_info=True)
```

### ❌ Wrong Log Level

```python
# WRONG - ERROR for expected conditions
if not user_found:
    logger.error("User not found")  # Normal case

# CORRECT - Use WARNING or INFO
if not user_found:
    logger.warning(f"User not found: user_id={user_id}")
```

### ❌ Missing exc_info for Errors

```python
# WRONG - No traceback
try:
    risky_operation()
except Exception as e:
    logger.error(f"Operation failed: {e}")  # No traceback

# CORRECT - Include exc_info=True
try:
    risky_operation()
except Exception as e:
    logger.error(f"Operation failed: {e}", exc_info=True)
```

### ❌ Excessive Logging in Loops

```python
# WRONG - Logs every iteration
for item in large_list:
    logger.info(f"Processing item {item}")  # Thousands of logs

# CORRECT - Log summary
logger.info(f"Processing {len(large_list)} items")
# ... process items ...
logger.info(f"Completed processing {len(large_list)} items in {duration:.2f}s")
```

---

## Async Logging

### Pattern: Non-blocking Logging

```python
async def process_request():
    logger.info("Request started")  # Synchronous logging (fast enough)
    result = await long_running_operation()
    logger.info(f"Request completed in {duration:.2f}s")
    # Don't use await with logger - standard logging is thread-safe
```

**Note**: Standard Python logging is thread-safe and fast enough for async contexts. No async logging libraries needed.

---

## Monitoring Integration

### Metrics Collection

```python
# Log structured events for metrics aggregation
logger.info(f"Workflow execution completed: workflow_id={workflow_id}, duration_ms={duration}, status=success, nodes_executed={node_count}")

# Downstream systems (Langfuse, CloudWatch) parse JSON logs
```

### Alert Triggers

```python
# ERROR/CRITICAL logs trigger alerts
logger.error(
    f"Database connection failed: retry_count={retry_count}, db_host={host}",
    exc_info=True
)
# Alert system monitors ERROR+ levels, notifies on-call
```

### JSON Format Benefits

- Elasticsearch indexing with structured fields
- CloudWatch Insights queries: `fields @timestamp, level, uuid | filter level = "ERROR"`
- Langfuse observability integration

---

## Testing Logging

```python
import pytest

def test_logging_context_enrichment(caplog):
    """Verify context variables appear in logs"""
    set_logging_info(uuid="test-uuid", user_id="test-user")
    logger.info("Test message")

    assert "test-uuid" in caplog.text
    assert "test-user" in caplog.text

def test_error_logging_with_exc_info(caplog):
    """Verify exc_info includes traceback"""
    try:
        raise ValueError("Test error")
    except ValueError:
        logger.error("Caught error", exc_info=True)

    assert "ValueError" in caplog.text
    assert "Test error" in caplog.text
    assert "Traceback" in caplog.text
```

**See**: [Testing Patterns](../testing/testing-patterns.md) for pytest patterns

---

## Troubleshooting

### Issue: Context Variables Not Appearing

**Symptom**: Logs show `-` for uuid/user_id/conversation_id

**Fix**: Call `set_logging_info()` at request start

```python
set_logging_info(uuid=str(uuid.uuid4()), user_id=current_user.id)
```

### Issue: JSON Serialization Errors

**Symptom**: `TypeError: Object of type X is not JSON serializable`

**Fix**: Use `str()` in log message

```python
logger.info(f"Event: data={str(complex_object)}")
```

### Issue: Logs Not Showing

**Symptom**: INFO logs missing in production

**Fix**: Check `LOG_LEVEL` environment variable

```bash
export LOG_LEVEL=INFO  # or DEBUG for verbosity
```

---

## References

### Source Files

- **Logger Configuration**: `src/codemie/configs/logger.py`
- **Context Variables**: `src/codemie/configs/logger.py:71-74`
- **Custom Formatter**: `src/codemie/configs/logger.py:13-24`
- **Record Factory**: `src/codemie/configs/logger.py:93-105`
- **JSON Serialization**: `src/codemie/configs/logger.py:78-90`
- **Request Handler**: `src/codemie/rest_api/handlers/assistant_handlers.py`
- **Workflow Logging**: `src/codemie/workflows/workflow.py`
- **Migration Example**: `src/external/migrations/migrate_assistants_to_versions.py`

### Related Documentation

- [Error Handling Patterns](./error-handling.md) - Exception hierarchies, exc_info usage
- [Testing Patterns](../testing/testing-patterns.md) - Log capture with pytest
- [REST API Patterns](../api/rest-api-patterns.md) - Request/response logging, middleware
- [Service Layer Patterns](../architecture/service-layer-patterns.md) - Service logging patterns

### External Resources

- [Python logging](https://docs.python.org/3/library/logging.html)
- [Contextvars](https://docs.python.org/3/library/contextvars.html)
- [FastAPI Logging](https://fastapi.tiangolo.com/tutorial/logging/)
