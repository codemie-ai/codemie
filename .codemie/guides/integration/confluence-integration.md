# Confluence Integration

## Quick Summary

Integrate Confluence Cloud and Server to index spaces, pages, and content into CodeMie knowledge base. Uses CQL (Confluence Query Language) for querying, markdown processing for content transformation, and windowed chunking for context preservation.

**Category**: Integration
**Complexity**: Medium
**Prerequisites**: BaseDatasourceProcessor pattern, LangChain Document model, Elasticsearch, Confluence API credentials

---

## Prerequisites

- `atlassian` Python client library
- API token (Cloud) or Personal Access Token (Server)
- CQL query knowledge
- Understanding of `BaseDatasourceProcessor` and `BaseDatasourceLoader`

---

## Authentication

### Cloud Authentication

```python
# src/codemie/datasource/loader/confluence_loader.py
from codemie_tools.core.project_management.confluence.models import ConfluenceConfig

config = ConfluenceConfig(
    url="https://your-domain.atlassian.net",
    token="your_api_token",
    username="your_email@example.com",
    cloud=True
)
```

### Self-Hosted Authentication

```python
config = ConfluenceConfig(
    url="https://confluence.internal.com",
    token="your_api_token",
    cloud=False
)
```

**Source**: `src/codemie/datasource/loader/confluence_loader.py`

---

## CQL Queries with Pagination

### Query Pattern

```python
# src/codemie/datasource/loader/confluence_loader.py:29-51
def _search_content_by_cql(self, cql: str, **kwargs) -> tuple[List[dict], str]:
    if kwargs.get("next_url"):
        response = self.confluence.get(kwargs["next_url"])
    else:
        url = "rest/api/content/search"
        params = {"cql": cql}
        params.update(kwargs)
        response = self.confluence.get(url, params=params)

    return response.get("results", []), response.get("_links", {}).get("next", "")
```

### Pagination with Retry

```python
@retry(
    stop=stop_after_attempt(self.number_of_retries),
    wait=wait_exponential(multiplier=1, min=min_retry_seconds, max=max_retry_seconds),
    before_sleep=before_sleep_log(logger, logging.WARNING)
)
def paginate_request(self, retrieval_method: Callable, **kwargs) -> List:
    # Fetch pages until max_pages reached or no next_url
    pass
```

### CQL Examples

| Query | Purpose |
|-------|---------|
| `space=DEVOPS AND type=page` | All pages in DEVOPS space |
| `space=DEVOPS AND type=page AND lastModified >= "2025-01-01"` | Updated pages since Jan 2025 |
| `space in (ENG, QA) AND label=documentation` | Pages with label across spaces |

**CQL Reference**: https://developer.atlassian.com/cloud/confluence/cql/

---

## Content Transformation

### Markdown Processing

```python
# src/codemie/datasource/confluence_datasource_processor.py:102-122
@classmethod
def process_markdown(cls, markdown: str) -> list[Document]:
    # Split by headers (H1, H2, H3)
    docs = cls.markdown_splitter.split_text(markdown)
    return docs
```

### Windowed Chunking for Context

```python
@classmethod
def join_markdown_chunks_by_window(cls, docs: list[Document],
                                   window_size: int = 3,
                                   window_overlap: int = 1) -> list[Document]:
    # Create overlapping windows: [Doc1,Doc2,Doc3], [Doc3,Doc4,Doc5], ...
    step = window_size - window_overlap
    new_docs = []
    for start in range(0, len(docs) - window_size + 1, step):
        window = docs[start : start + window_size]
        new_docs.append(cls.join_docs_window(window))
    return new_docs
```

**Pipeline**:
1. Confluence API → raw markdown
2. Split by markdown headers
3. Create windowed chunks (context overlap)
4. Add metadata (source, headers)
5. Index to Elasticsearch

---

## Error Handling

### Common Exceptions

| Exception | When | Source |
|-----------|------|--------|
| `MissingIntegrationException` | Missing credentials | `src/codemie/datasource/exceptions.py` |
| `UnauthorizedException` | Auth failure | `src/codemie/datasource/exceptions.py` |
| `InvalidQueryException` | Invalid CQL syntax | `src/codemie/datasource/exceptions.py` |
| `EmptyResultException` | No results returned | `src/codemie/datasource/exceptions.py` |

### Validation Example

```python
def _validate_creds(self):
    if not self.token:
        raise MissingIntegrationException("Confluence")

    try:
        self.confluence.get_all_spaces(limit=1)  # Test connection
    except HTTPError as e:
        logger.error(f"Cannot authenticate. Error: {e}")
        raise UnauthorizedException(datasource_type="Confluence")
```

**Source**: `src/codemie/datasource/loader/confluence_loader.py`

---

## Example: Index Confluence Space

```python
from codemie.datasource.confluence_datasource_processor import ConfluenceDatasourceProcessor
from codemie_tools.core.project_management.confluence.models import ConfluenceConfig

config = ConfluenceConfig(
    url="https://your-org.atlassian.net",
    token="YOUR_API_TOKEN",
    username="user@example.com",
    cloud=True
)

processor = ConfluenceDatasourceProcessor(
    datasource_name="engineering-docs",
    user=current_user,
    project_name="codemie",
    confluence=config,
    index_knowledge_base_config=IndexKnowledgeBaseConfluenceConfig(
        cql="space=ENGINEERING AND type=page",
        max_pages=1000,
        pages_per_request=50
    )
)

processor.process()  # Start indexing
```

---

## Verification

### Check Indexed Content

```bash
# Elasticsearch query
curl -X GET "localhost:9200/kb_confluence_engineering-docs/_search?pretty"

# Document count
curl -X GET "localhost:9200/kb_confluence_engineering-docs/_count?pretty"

# Database check
SELECT repo_name, current_state, total_docs FROM index_info WHERE project_name='codemie';
```

### Test Connection

```python
from codemie.datasource.loader.confluence_loader import ConfluenceDatasourceLoader

loader = ConfluenceDatasourceLoader(...)
stats = loader.fetch_remote_stats()  # Returns {'documents_count_key': N}
```

---

## Troubleshooting

### Rate Limiting (HTTP 429)

**Solution**:
- Reduce `pages_per_request` batch size
- Increase retry wait times in tenacity config
- Implement backoff multiplier > 2
- Contact Atlassian admin for quota increase

### Invalid CQL Syntax

**Solution**:
- Test query in Confluence web UI (Advanced Search)
- Escape special characters
- Verify field names (case-sensitive)
- Check CQL documentation: https://developer.atlassian.com/cloud/confluence/cql/

### Connection Timeouts

**Solution**:
- Check network connectivity
- Verify firewall rules for HTTPS
- Increase retry attempts in tenacity
- Use VPN if required

---

## Configuration

```yaml
# config/datasources/datasources-config.yaml
loaders:
  confluence_loader:
    chunk_size: 1000         # Tokens per chunk
    chunk_overlap: 50        # Token overlap
    loader_batch_size: 50    # Documents per batch
    max_pages: 1000          # Max pages to index
```

---

## References

**Source Files**:
- `src/codemie/datasource/confluence_datasource_processor.py`
- `src/codemie/datasource/loader/confluence_loader.py`
- `src/codemie/datasource/base_datasource_processor.py`
- `src/codemie/datasource/exceptions.py`

**Related Guides**:
- [Jira Integration](.codemie/guides/integration/jira-integration.md)
- [External Services Overview](.codemie/guides/integration/external-services.md)
- [Repository Patterns](.codemie/guides/data/repository-patterns.md)

**External Resources**:
- [Confluence Cloud REST API](https://developer.atlassian.com/cloud/confluence/rest/v2/)
- [CQL Documentation](https://developer.atlassian.com/cloud/confluence/cql/)
- [LangChain Document Loaders](https://python.langchain.com/docs/modules/data_connection/document_loaders/)
