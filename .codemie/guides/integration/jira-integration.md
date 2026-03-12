# Jira Integration

## Quick Summary

Integrate Jira Cloud and Server to index issues into CodeMie knowledge base. Uses JQL (Jira Query Language) for filtering, automatic pagination handling, and incremental reindexing support for updated issues.

**Category**: Integration
**Complexity**: Medium
**Prerequisites**: BaseDatasourceProcessor pattern, LangChain Document model, Elasticsearch, Jira API credentials

---

## Prerequisites

- `atlassian` Python client library (Jira support)
- API token (Cloud) or Bearer token (Server)
- JQL query knowledge
- Understanding of `BaseDatasourceProcessor` and `BaseDatasourceLoader`

---

## Authentication

### Cloud Authentication

```python
# src/codemie/datasource/loader/jira_loader.py:152-157
from codemie_tools.core.project_management.jira.models import JiraConfig

config = JiraConfig(
    url="https://your-domain.atlassian.net",
    username="your_email@example.com",
    password="your_api_token",  # API token, not actual password
    cloud=True
)

jira = Jira(url=url, username=username, password=password, cloud=True, api_version=3)
```

### Self-Hosted Authentication

```python
config = JiraConfig(
    url="https://jira.internal.com",
    token="your_bearer_token",
    cloud=False
)

jira = Jira(url=url, token=token, cloud=False, api_version=2)
```

**Source**: `src/codemie/datasource/loader/jira_loader.py`

---

## JQL Queries with Pagination

### Cloud Pagination (Enhanced JQL)

```python
# src/codemie/datasource/loader/jira_loader.py
def _load_issues_for_cloud_jira(self):
    all_issues = []
    next_page_token = None
    while True:
        batch = self.jira.enhanced_jql(
            self.jql, fields=self.FIELDS,
            nextPageToken=next_page_token,
            limit=self.MAX_RESULTS
        )
        all_issues.extend(batch['issues'])
        if batch['isLast']:
            break
        next_page_token = batch['nextPageToken']
    return all_issues
```

### Self-Hosted Pagination (Standard JQL)

```python
def _load_issues_for_jira(self):
    start_at = 0
    all_issues = []
    while True:
        batch = self.jira.jql(self.jql, fields=self.FIELDS, start=start_at, limit=self.MAX_RESULTS)
        all_issues.extend(batch['issues'])
        if len(batch['issues']) < self.MAX_RESULTS:
            break
        start_at += self.MAX_RESULTS
    return all_issues
```

### JQL Examples

| Query | Purpose |
|-------|---------|
| `project=CODEMIE` | All issues in CODEMIE project |
| `project=CODEMIE AND status="In Progress"` | In-progress issues |
| `project=CODEMIE AND updated >= -7d` | Updated in last 7 days (incremental) |
| `project IN (DEV, QA) AND type=Bug` | Bugs across multiple projects |

**JQL Reference**: https://support.atlassian.com/jira-service-management-cloud/docs/use-advanced-search-with-jira-query-language-jql/

---

## Content Transformation

### Issue to Document

```python
# src/codemie/datasource/loader/jira_loader.py:159-196
def _transform_to_doc(self, issue: dict) -> Document:
    fields = issue.get('fields', {})
    key = issue.get('key')

    content = (
        f"Issue Key: {key}\n"
        f"Title: {fields.get('summary', 'No Summary')}\n"
        f"URL: {self.url.strip('/')}/browse/{key}\n"
        f"Status: {fields.get('status', {}).get('name', 'No Status')}\n"
        f"Assignee: {fields.get('assignee', {}).get('name', '')}\n"
        f"Created: {fields.get('created', 'No Creation Date')}\n"
        f"Issue Type: {fields.get('issuetype', {}).get('name', 'Unknown')}\n"
        f"Description: {fields.get('description', '')}\n"
    )

    return Document(
        page_content=content,
        metadata={'source': f"{key} - {summary}", 'key': key}
    )
```

**Indexed Fields**:
- Issue key, summary, URL, status, assignee, created date, issue type, description
- Metadata includes source reference and key for incremental updates

---

## Incremental Reindexing

### Cleanup Strategy

```python
# src/codemie/datasource/jira/jira_datasource_processor.py:82-95
def _cleanup_data_for_incremental_reindex(self, docs_to_be_indexed: list[Document]):
    """Remove data by Jira ticket number before reindexing."""
    updated_keys = [doc.metadata["key"] for doc in docs_to_be_indexed]

    self.client.delete_by_query(
        index=self._index_name,
        body={"query": {"terms": {"metadata.key.keyword": updated_keys}}},
        wait_for_completion=True,
        refresh=True
    )
```

### Incremental Flow

1. Query updated issues: `JQL + "updated >= -7d"`
2. Delete stale entries by issue key from Elasticsearch
3. Reindex only updated issues

**Example JQL for Incremental**:
```python
jql = "project=CODEMIE AND updated >= '2025-01-20'"
```

---

## Error Handling

### Credential Validation

```python
# src/codemie/datasource/loader/jira_loader.py:83-97
def _validate_creds(self):
    if self.cloud and (not self.username or not self.password):
        logger.error("Missing credentials for Cloud Jira integration")
        raise MissingIntegrationException("Jira")

    if not self.cloud and not self.token:
        logger.error("Missing token for Jira integration")
        raise MissingIntegrationException("Jira")

    try:
        self.jira.get_all_fields()  # Test connection
    except HTTPError as e:
        logger.error(f"Cannot authenticate user. Failed with error {e}")
        raise UnauthorizedException(datasource_type="Jira")
```

### Common Exceptions

| Exception | When | Source |
|-----------|------|--------|
| `MissingIntegrationException` | Missing credentials | `src/codemie/datasource/exceptions.py` |
| `UnauthorizedException` | Auth failure | `src/codemie/datasource/exceptions.py` |
| `InvalidQueryException` | Invalid JQL syntax | `src/codemie/datasource/exceptions.py` |
| `EmptyResultException` | No results returned | `src/codemie/datasource/exceptions.py` |

---

## Example: Index Jira Project

```python
from codemie.datasource.jira.jira_datasource_processor import JiraDatasourceProcessor
from codemie_tools.core.project_management.jira.models import JiraConfig

credentials = JiraConfig(
    url="https://jira.company.com",
    token="YOUR_BEARER_TOKEN",
    cloud=False
)

processor = JiraDatasourceProcessor(
    datasource_name="project-tracker",
    user=current_user,
    project_name="codemie",
    credentials=credentials,
    jql="project=CODEMIE AND status IN ('In Progress', 'Review')"
)

processor.process()
```

---

## Verification

### Check Indexed Issues

```bash
# Elasticsearch query
curl -X GET "localhost:9200/kb_jira_project-tracker/_search?pretty"

# Document count
curl -X GET "localhost:9200/kb_jira_project-tracker/_count?pretty"

# Database check
SELECT repo_name, current_state, total_docs FROM index_info WHERE project_name='codemie';
```

### Test Connection

```python
from codemie.datasource.loader.jira_loader import JiraLoader

loader = JiraLoader(...)
loader._validate_creds()  # Raises exception if auth fails
```

---

## Troubleshooting

### Rate Limiting (HTTP 429)

**Solution**:
- Reduce `MAX_RESULTS` batch size
- Increase retry wait times in tenacity config
- Implement backoff multiplier > 2
- Contact Jira admin for quota increase

### Invalid JQL Syntax

**Solution**:
- Test query in Jira web UI (Filters → Advanced Search)
- Escape special characters
- Verify field names (case-sensitive)
- Check JQL documentation: https://support.atlassian.com/jira-service-management-cloud/docs/use-advanced-search-with-jira-query-language-jql/

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
  jira_loader:
    chunk_size: 1000         # Tokens per chunk
    chunk_overlap: 50        # Token overlap
    loader_batch_size: 50    # Documents per batch
    max_results: 100         # Issues per API request
```

---

## References

**Source Files**:
- `src/codemie/datasource/jira/jira_datasource_processor.py`
- `src/codemie/datasource/loader/jira_loader.py`
- `src/codemie/datasource/base_datasource_processor.py`
- `src/codemie/datasource/exceptions.py`

**Related Guides**:
- [Confluence Integration](.codemie/guides/integration/confluence-integration.md)
- [X-ray Integration](.codemie/guides/integration/xray-integration.md)
- [External Services Overview](.codemie/guides/integration/external-services.md)
- [Repository Patterns](.codemie/guides/data/repository-patterns.md)

**External Resources**:
- [Jira Cloud REST API](https://developer.atlassian.com/cloud/jira/platform/rest/v3/)
- [JQL Documentation](https://support.atlassian.com/jira-service-management-cloud/docs/use-advanced-search-with-jira-query-language-jql/)
- [LangChain Document Loaders](https://python.langchain.com/docs/modules/data_connection/document_loaders/)
