# Google Docs Integration

## Quick Summary

Integrate Google Docs to index documents into CodeMie knowledge base. Uses Google Docs API and Drive API for document retrieval, structured content parsing for headings/paragraphs, and automatic document transformation to LangChain Documents.

**Category**: Integration
**Complexity**: Medium
**Prerequisites**: BaseDatasourceProcessor pattern, LangChain Document model, Elasticsearch, Google Cloud service account

---

## Prerequisites

- `google-api-python-client` library
- Google Cloud service account with Docs API and Drive API enabled
- Service account JSON key file
- `GOOGLE_APPLICATION_CREDENTIALS` environment variable configured
- Documents shared with service account email

---

## Authentication

### Service Account Setup

```python
# src/codemie/datasource/loader/util.py:168
from googleapiclient.discovery import build

# Uses Application Default Credentials (ADC) or service account
service = build("docs", "v1")
document = service.documents().get(documentId=document_id).execute()
```

**Environment Setup**:
```bash
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account-key.json"
```

**Note**: Authentication handled outside loader via Google Cloud SDK credentials.

**Source**: `src/codemie/datasource/loader/util.py`

---

## Document Retrieval

### Document ID Extraction

```python
# src/codemie/datasource/google_doc/google_doc_datasource_processor.py:123-125
@classmethod
def _parse_google_doc_id(cls, url):
    document_id_regex = r".*/d/([a-zA-Z0-9-_]+)/edit.*"
    match = re.search(document_id_regex, url)
    return match.group(1) if match else None
```

**Supported URL Format**:
```
https://docs.google.com/document/d/1ABC123xyz/edit
                                   ^^^^^^^^^^^^
                                   Document ID
```

### Document Parsing

```python
# src/codemie/datasource/loader/util.py:41-42, 167-177
def parse_doc(self) -> Tuple[List, str]:
    service = build("docs", "v1")
    document = service.documents().get(documentId=self.document_id).execute()
    elements = document.get("body", {}).get("content", [])
    articles = self.get_articles(elements)
    chapters = self.get_titles(elements)
    return articles, chapters, document.get("documentId", "")
```

---

## Content Transformation

### Structured Content Parsing

```python
# src/codemie/datasource/loader/util.py:57-135
def get_articles(self, elements: List[Dict]) -> List[Dict]:
    titles = []
    content = ""
    articles = []

    for element in elements:
        text = self.get_element_text(element)
        style = self.get_element_style(element)

        if self.is_title(text, style):
            # Save previous article
            if content.strip() and titles:
                for title in titles:
                    articles.append({
                        "title": title,
                        "content": content.strip(),
                        "instructions": instructions,
                        "reference": reference
                    })
            titles.clear()
            titles.append(text)
            content = ""
        else:
            content += text.strip() + "\n"

    return articles
```

### Transformation Pipeline

1. Parse Google Doc structure (headings, paragraphs)
2. Extract articles by heading boundaries
3. Build table of contents from 2nd-level headings
4. Transform to LangChain Document with metadata
5. Index to Elasticsearch

**Document Structure**:
- **Headings** (style: `HEADING_1`, `HEADING_2`) → Article boundaries
- **Paragraphs** → Article content
- **Metadata** → Title, document ID, source URL

---

## Error Handling

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| `google.auth.exceptions.RefreshError` | Invalid credentials | Verify `GOOGLE_APPLICATION_CREDENTIALS` path |
| Permission denied | Document not shared | Share doc with service account email |
| API not enabled | Missing API activation | Enable Docs API and Drive API in GCP |
| Invalid document ID | Wrong URL format | Verify URL matches pattern |

### Validation Example

```python
def _validate_credentials(self):
    if not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        raise MissingIntegrationException("Google Docs")

    try:
        service = build("docs", "v1")
        service.documents().get(documentId=self.document_id).execute()
    except Exception as e:
        logger.error(f"Cannot access document. Error: {e}")
        raise UnauthorizedException(datasource_type="Google Docs")
```

---

## Example: Index Google Doc

```python
from codemie.datasource.google_doc.google_doc_datasource_processor import GoogleDocDatasourceProcessor

processor = GoogleDocDatasourceProcessor(
    datasource_name="product-specs",
    project_name="codemie",
    google_doc="https://docs.google.com/document/d/1ABC123xyz/edit",
    user=current_user
)

processor.process()
```

---

## Verification

### Check Indexed Content

```bash
# Elasticsearch query
curl -X GET "localhost:9200/kb_google_doc_product-specs/_search?pretty"

# Document count
curl -X GET "localhost:9200/kb_google_doc_product-specs/_count?pretty"

# Database check
SELECT repo_name, current_state, total_docs FROM index_info WHERE project_name='codemie';
```

### Test Authentication

```python
from googleapiclient.discovery import build

service = build("docs", "v1")
doc = service.documents().get(documentId="1ABC123xyz").execute()
print(f"Document title: {doc.get('title')}")
```

---

## Troubleshooting

### OAuth Token Refresh Failures

**Symptom**: `google.auth.exceptions.RefreshError`

**Solution**:
1. Verify `GOOGLE_APPLICATION_CREDENTIALS` points to valid JSON
2. Check service account has Docs API and Drive API enabled
3. Ensure document is shared with service account email
4. Regenerate service account key if expired

### Permission Denied

**Symptom**: `403 Forbidden` or permission error

**Solution**:
1. Share document with service account email (found in JSON key)
2. Grant at least "Viewer" access
3. Verify service account email is correct

### Invalid Document ID

**Symptom**: `404 Not Found`

**Solution**:
1. Verify URL format: `https://docs.google.com/document/d/[ID]/edit`
2. Extract ID correctly (alphanumeric with hyphens/underscores)
3. Check document exists and is accessible

---

## Configuration

```yaml
# config/datasources/datasources-config.yaml
loaders:
  google_doc_loader:
    chunk_size: 1000         # Tokens per chunk
    chunk_overlap: 50        # Token overlap
    loader_batch_size: 50    # Documents per batch
```

---

## Service Account Setup

### Step 1: Create Service Account

1. Go to Google Cloud Console → IAM & Admin → Service Accounts
2. Create service account (e.g., `codemie-docs-reader`)
3. Download JSON key file

### Step 2: Enable APIs

1. Go to APIs & Services → Library
2. Enable "Google Docs API"
3. Enable "Google Drive API"

### Step 3: Share Documents

Share target documents with service account email:
```
codemie-docs-reader@project-id.iam.gserviceaccount.com
```

### Step 4: Configure Environment

```bash
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account.json"
```

---

## References

**Source Files**:
- `src/codemie/datasource/google_doc/google_doc_datasource_processor.py`
- `src/codemie/datasource/loader/google_doc_loader.py`
- `src/codemie/datasource/loader/util.py`
- `src/codemie/datasource/base_datasource_processor.py`
- `src/codemie/datasource/exceptions.py`

**Related Guides**:
- [Confluence Integration](.codemie/guides/integration/confluence-integration.md)
- [External Services Overview](.codemie/guides/integration/external-services.md)
- [Cloud Integrations](.codemie/guides/integration/cloud-integrations.md)

**External Resources**:
- [Google Docs API](https://developers.google.com/docs/api)
- [Google Drive API](https://developers.google.com/drive/api)
- [Service Account Authentication](https://cloud.google.com/docs/authentication/production)
- [LangChain Document Loaders](https://python.langchain.com/docs/modules/data_connection/document_loaders/)
