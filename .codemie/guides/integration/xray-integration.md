# X-ray Test Management Integration

## Quick Summary

Integrate X-ray Cloud test management to index test cases into CodeMie knowledge base. Uses OAuth 2.0 client credentials, GraphQL API with JQL filtering, and supports Manual, Generic, and Cucumber test types.

**Category**: Integration
**Complexity**: Medium
**Prerequisites**: BaseDatasourceProcessor pattern, LangChain Document model, Elasticsearch, X-ray Cloud API credentials

---

## Prerequisites

- X-ray Cloud subscription
- Client ID and Client Secret (from X-ray Cloud API Keys)
- `XrayClient` from `codemie_tools.qa.xray`
- JQL query knowledge
- Understanding of `BaseDatasourceProcessor` and `BaseDatasourceLoader`

---

## Authentication

### OAuth 2.0 Client Credentials

```python
from codemie_tools.qa.xray.models import XrayConfig

config = XrayConfig(
    base_url="https://xray.cloud.getxray.app",
    client_id="<client_id>",
    client_secret="<client_secret>",
    limit=100,
    verify_ssl=True
)
```

**API Key Generation**:
1. Go to X-ray Cloud → Settings → API Keys
2. Create API Key
3. Copy Client ID and Client Secret

**Source**: `src/codemie/datasource/xray/xray_loader.py`

---

## Data Retrieval

### JQL Queries via GraphQL

```python
# src/codemie/datasource/xray/xray_loader.py
from codemie_tools.qa.xray.xray_client import XrayClient

client = XrayClient(
    base_url=config.base_url,
    client_id=config.client_id,
    client_secret=config.client_secret
)

# Fetch tests with pagination handled automatically
result = client.get_tests(jql="project = CALC AND type = Test", max_results=None)
tests = result.get("tests", [])
```

### JQL Examples

| Query | Purpose |
|-------|---------|
| `project = CALC AND type = Test` | All tests in CALC project |
| `project = CALC AND type = Test AND updated >= -7d` | Updated tests (last 7 days) |
| `project IN (CALC, QA) AND labels = smoke` | Smoke tests across projects |

**Note**: X-ray GraphQL API doesn't support `updatedDate >= timestamp` filters (unlike Jira REST API).

**JQL Reference**: https://support.atlassian.com/jira-service-management-cloud/docs/use-advanced-search-with-jira-query-language-jql/

---

## Content Transformation

### Test Case to Document

```python
# src/codemie/datasource/xray/xray_loader.py:_transform_to_doc()
Document(
    page_content="""
Test Key: CALC-123
Summary: Verify calculator addition
URL: https://xray.cloud.getxray.app/browse/CALC-123
Test Type: Manual

Steps:
1. Action: Open calculator
   Expected Result: Calculator opens successfully

Preconditions:
- CALC-100: Calculator application installed
    """,
    metadata={
        "source": "CALC-123 - Verify calculator addition",
        "key": "CALC-123",
        "test_type": "Manual",
        "project_id": "10001"
    }
)
```

### Supported Test Types

| Type | Includes |
|------|----------|
| **Manual** | Steps (action, data, expected result) |
| **Generic** | Unstructured description |
| **Cucumber** | Gherkin scenario definition |

**Note**: Test attachments indexed as metadata only (filename, ID), not content.

---

## Incremental Reindexing

### Limitation

X-ray GraphQL API doesn't support `updatedDate >= timestamp` filters (unlike Jira REST API).

### Workaround

```python
# src/codemie/datasource/xray/xray_datasource_processor.py
def _cleanup_data_for_incremental_reindex(self, docs_to_be_indexed: list[Document]):
    """Remove data by X-ray test key before reindexing"""
    updated_keys = [doc.metadata["key"] for doc in docs_to_be_indexed]
    self.client.delete_by_query(
        index=self._index_name,
        body={"query": {"terms": {"metadata.key.keyword": updated_keys}}},
        wait_for_completion=True,
        refresh=True,
    )
```

**Flow**: Fetch all tests → Delete existing tests by key → Reindex all fetched tests

**Recommendation**: Use precise JQL filters (e.g., `updated >= -7d`) to limit scope.

---

## Error Handling

### Common Exceptions

| Exception | When | Source |
|-----------|------|--------|
| `MissingIntegrationException` | Missing credentials | `src/codemie/datasource/exceptions.py` |
| `UnauthorizedException` | Auth failure | `src/codemie/datasource/exceptions.py` |
| `InvalidQueryException` | Invalid JQL syntax | `src/codemie/datasource/exceptions.py` |
| `EmptyResultException` | No results returned | `src/codemie/datasource/exceptions.py` |

### Validation Example

```python
def _validate_creds(self):
    if not self.client_id or not self.client_secret:
        raise MissingIntegrationException("X-ray")

    try:
        self.client.get_tests(jql="project = TEST", max_results=1)
    except Exception as e:
        logger.error(f"Cannot authenticate. Error: {e}")
        raise UnauthorizedException(datasource_type="X-ray")
```

---

## Example: Index X-ray Tests

```python
from codemie.datasource.xray.xray_datasource_processor import XrayDatasourceProcessor
from codemie_tools.qa.xray.models import XrayConfig

credentials = XrayConfig(
    base_url="https://xray.cloud.getxray.app",
    client_id="<client_id>",
    client_secret="<client_secret>",
    limit=100
)

processor = XrayDatasourceProcessor(
    datasource_name="calculator-tests",
    user=current_user,
    project_name="codemie",
    credentials=credentials,
    jql="project = CALC AND type = Test"
)

processor.process()  # Start indexing
```

---

## Verification

### Check Indexed Tests

```bash
# Elasticsearch query
curl -X GET "localhost:9200/kb_xray_calculator-tests/_search?pretty"

# Document count
curl -X GET "localhost:9200/kb_xray_calculator-tests/_count?pretty"

# Database check
SELECT repo_name, current_state, total_docs FROM index_info WHERE project_name='codemie';
```

### Health Check

```python
# src/codemie/service/index/datasource_health_check_service.py
@classmethod
def health_check_xray(cls, request: DatasourceHealthCheckRequest, user_id: str):
    xray_creds = SettingsService.get_xray_creds(
        user_id=user_id,
        project_name=request.project_name,
        setting_id=request.setting_id,
    )

    return DatasourceHealthCheckResponse(
        documents_count=XrayDatasourceProcessor.check_xray_query(
            jql=request.jql,
            credentials=xray_creds
        )
    )
```

---

## Troubleshooting

### OAuth Authentication Failures

**Symptom**: `401 Unauthorized`

**Solution**:
1. Verify Client ID and Client Secret are correct
2. Regenerate API key if expired
3. Check X-ray Cloud subscription is active
4. Ensure API key has test read permissions

### Large Test Suites (>10k tests)

**Symptom**: Slow indexing or timeouts

**Solution**:
- Use precise JQL filters to limit scope: `project = CALC AND updated >= -30d`
- Index incrementally by date ranges
- Reduce `limit` parameter for smaller batches

### Test Executions Not Indexed

**Question**: Why aren't test execution results indexed?

**Answer**: X-ray integration indexes test definitions only, not execution results. Test execution data is stored separately in X-ray.

---

## Configuration

```yaml
# config/datasources/datasources-config.yaml
loaders:
  xray_loader:
    chunk_size: 1000         # Tokens per chunk
    chunk_overlap: 50        # Token overlap
    loader_batch_size: 50    # Documents per batch
```

---

## FAQ

**Q: Why doesn't incremental reindex support `updatedDate` filter?**
A: X-ray GraphQL API doesn't expose `updatedDate >= timestamp` in JQL queries (unlike Jira REST API).

**Q: How to handle large test suites (>10k tests)?**
A: Use precise JQL filters to limit scope (e.g., `project = CALC AND updated >= -30d`).

**Q: Are test attachments indexed?**
A: No, only attachment metadata (filename, ID) is included in the document.

**Q: Are test executions indexed?**
A: No, only test definitions are indexed. Test execution results are stored separately in X-ray.

---

## References

**Source Files**:
- `src/codemie/datasource/xray/xray_datasource_processor.py`
- `src/codemie/datasource/xray/xray_loader.py`
- `codemie_tools.qa.xray.xray_client`
- `codemie_tools.qa.xray.models`
- `src/codemie/datasource/base_datasource_processor.py`
- `src/codemie/datasource/exceptions.py`

**Related Guides**:
- [Jira Integration](.codemie/guides/integration/jira-integration.md)
- [External Services Overview](.codemie/guides/integration/external-services.md)
- [Repository Patterns](.codemie/guides/data/repository-patterns.md)

**External Resources**:
- [X-ray Cloud API Documentation](https://docs.getxray.app/display/XRAYCLOUD/GraphQL+API)
- [X-ray JQL Support](https://docs.getxray.app/display/XRAYCLOUD/Test+Repository#TestRepository-UsingJQL)
- [JQL Documentation](https://support.atlassian.com/jira-service-management-cloud/docs/use-advanced-search-with-jira-query-language-jql/)
