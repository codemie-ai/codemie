# External Service Integrations (Overview)

## Quick Summary

CodeMie integrates with external services (Confluence, Jira, X-ray, Google Docs) to index content into a unified knowledge base. All integrations follow a processor/loader pattern with service-specific authentication, query languages, and document transformation pipelines.

**Category**: Integration
**Complexity**: Medium-High
**Prerequisites**: Understanding of BaseDatasourceProcessor pattern, LangChain Document model, Elasticsearch indexing

---

## Integration Guides

| Service | Guide | Auth Method | Query Language | Use Case |
|---------|-------|-------------|----------------|----------|
| **Confluence** | [Confluence Integration](./confluence-integration.md) | API Token / OAuth | CQL | Index wikis, spaces, pages |
| **Jira** | [Jira Integration](./jira-integration.md) | API Token / OAuth | JQL | Index issues, tickets, projects |
| **X-ray** | [X-ray Integration](./xray-integration.md) | OAuth 2.0 Client Credentials | JQL + GraphQL | Index test cases, test suites |
| **Google Docs** | [Google Docs Integration](./google-docs-integration.md) | OAuth 2.0 / Service Account | Document ID | Index documents, specifications |

---

## Common Architecture

### Processor/Loader Pattern

All external service integrations follow this architecture:

```
External Service API
        ↓
    Loader (Authentication + Query Execution)
        ↓
    Processor (Transformation + Chunking)
        ↓
    LangChain Document
        ↓
    Elasticsearch Index
```

**Base Classes**:
- `BaseDatasourceProcessor` (src/codemie/datasource/base_datasource_processor.py)
- `BaseDatasourceLoader` (src/codemie/datasource/base_datasource_loader.py)

---

## Shared Patterns

### Error Handling & Retry Logic

All processors inherit retry logic with exponential backoff:

```python
# src/codemie/datasource/base_datasource_processor.py:520-531
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type, before_sleep_log
)

@retry(
    stop=stop_after_attempt(STORAGE_CONFIG.indexing_max_retries),
    wait=wait_exponential(
        multiplier=2,
        min=STORAGE_CONFIG.indexing_error_retry_wait_min_seconds,
        max=STORAGE_CONFIG.indexing_error_retry_wait_max_seconds
    ),
    retry=retry_if_exception_type(Exception),
    reraise=True,
    before_sleep=before_sleep_log(logger, logging.ERROR, exc_info=True)
)
def _process_document(self, datasource_name, source, chunks, store):
    # Retry with exponential backoff on failures
    pass
```

### Custom Exceptions

```python
# src/codemie/datasource/exceptions.py
class MissingIntegrationException(Exception):
    """Raised when integration credentials missing"""
    ERROR_MSG = "{} integration is not completed."

class UnauthorizedException(Exception):
    """Raised when authentication fails"""
    ERROR_MSG = "Cannot retrieve data from {}"

class InvalidQueryException(Exception):
    """Raised when CQL/JQL query invalid"""
    ERROR_MSG = "The provided {} expression cannot be parsed. {}"

class EmptyResultException(Exception):
    """Raised when query returns no results"""
    ERROR_MSG = "Based on {} expression empty result returned."
```

---

## Plugin System Integration (NATS)

CodeMie uses NATS message queue for async plugin-based external service processing.

### PluginManager

```python
# src/codemie/service/plugins/nats_plugin_manager.py:27-48
class PluginManager:
    """Manager for NATS-based plugins that discovers and maintains tool configurations."""

    def __init__(self):
        self.plugins: Dict[Tuple[str, str], Plugin] = {}
        self.disconnected_sub: Subscription = None
        self.live_sub: Subscription = None
        self.client = Client()
        self.nc = None

    async def start(self):
        """Start plugin manager and connection pool."""
        self._lookup_task = asyncio.create_task(self.plugin_lookup_task())
        logger.info("Plugin Manager started")
```

### NATS Topics

| Topic Suffix | Purpose |
|--------------|---------|
| `*.live` | Plugin heartbeat / status updates |
| `*.list` | Plugin tool list requests |
| `*.disconnected` | Plugin disconnection events |

**Source**: `src/codemie/service/plugins/nats_plugin_manager.py`

---

## Common Troubleshooting

### Rate Limiting (HTTP 429)

**Affects**: Confluence, Jira, X-ray

**Solution**:
- Reduce batch size (`pages_per_request`, `MAX_RESULTS`, `limit`)
- Increase retry wait times in tenacity configuration
- Implement backoff multiplier > 2 for aggressive rate limits
- Contact service admin to increase rate limit quotas

### Connection Timeouts

**Affects**: All services

**Solution**:
- Check network connectivity to external service
- Verify firewall rules allow outbound HTTPS
- Increase retry attempts in tenacity configuration
- Use VPN if service requires internal network access

### Authentication Failures

**Affects**: All services

**Solution**:
- Verify credentials are correct and not expired
- Check API/service account permissions
- Test connection using service's web UI or CLI
- Regenerate API keys/tokens if needed

### Invalid Query Syntax

**Affects**: Confluence (CQL), Jira (JQL), X-ray (JQL)

**Solution**:
- Test query in service's web UI query builder
- Escape special characters in query strings
- Verify field names (case-sensitive)
- Check query syntax documentation

---

## Configuration

```yaml
# config/datasources/datasources-config.yaml
loaders:
  confluence_loader:
    chunk_size: 1000
    chunk_overlap: 50
    loader_batch_size: 50

  jira_loader:
    chunk_size: 1000
    chunk_overlap: 50
    loader_batch_size: 50

  xray_loader:
    chunk_size: 1000
    chunk_overlap: 50
    loader_batch_size: 50

  google_doc_loader:
    chunk_size: 1000
    chunk_overlap: 50
    loader_batch_size: 50
```

---

## References

**Integration Guides**:
- [Confluence Integration](./confluence-integration.md)
- [Jira Integration](./jira-integration.md)
- [X-ray Integration](./xray-integration.md)
- [Google Docs Integration](./google-docs-integration.md)

**Source Files**:
- `src/codemie/datasource/base_datasource_processor.py`
- `src/codemie/datasource/exceptions.py`
- `src/codemie/service/plugins/nats_plugin_manager.py`

**Related Guides**:
- [Cloud Integrations](.codemie/guides/integration/cloud-integrations.md)
- [LLM Providers](.codemie/guides/integration/llm-providers.md)
- [Repository Patterns](.codemie/guides/data/repository-patterns.md)
- [Service Layer](.codemie/guides/architecture/service-layer-patterns.md)

**External Resources**:
- [LangChain Document Loaders](https://python.langchain.com/docs/modules/data_connection/document_loaders/)
- [Tenacity Retry Library](https://tenacity.readthedocs.io/)
- [NATS Messaging](https://docs.nats.io/)
