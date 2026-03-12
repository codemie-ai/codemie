# Elasticsearch Integration & Vector Search

## Quick Summary

CodeMie uses Elasticsearch for semantic search, vector embeddings, and hybrid retrieval combining k-NN vector search with keyword search using Reciprocal Rank Fusion (RRF). Core patterns: per-process client singleton, repository abstraction for CRUD, vector search with dense_vector fields, and RRF-based hybrid search.

**Category**: Data/Search
**Complexity**: Medium-High

## Prerequisites

- **Python 3.12+**: Async/await patterns used throughout CodeMie
- **Elasticsearch 8.x**: k-NN vector search, dense_vector field type
- **LangChain Ecosystem**: `langchain-elasticsearch` 0.3.2, `langchain-core` for Document/Embedding abstractions
- **Embedding Models**: OpenAI, Azure OpenAI, or compatible embedding providers
- **Vector Similarity Understanding**: Cosine similarity, k-NN algorithm basics
- **Elasticsearch Client**: Official `elasticsearch` Python package

---

## Implementation

### Client Initialization Pattern

**Per-Process Singleton** - Prevents multiprocessing errors by creating separate client per process.

```python
# src/codemie/clients/elasticsearch.py
class ElasticSearchClient:
    _clients: dict[int, Elasticsearch] = {}

    @classmethod
    def get_client(cls) -> Elasticsearch:
        pid = os.getpid()
        if pid not in cls._clients:
            cls._clients[pid] = Elasticsearch(
                config.ELASTIC_URL,
                basic_auth=(config.ELASTIC_USERNAME, config.ELASTIC_PASSWORD),
                verify_certs=False,
                ssl_show_warn=False,
            )
        return cls._clients[pid]
```

**Usage**: Always use `ElasticSearchClient.get_client()` instead of creating direct `Elasticsearch()` instances.

### Repository Pattern (CRUD Operations)

**BaseElasticRepository** - Abstraction for index operations with type-safe models.

```python
# src/codemie/repository/base_elastic_repository.py
class BaseElasticRepository(ABC):
    def __init__(self, elastic_client: Elasticsearch, index_name: str):
        self._elastic_client = elastic_client
        self._index_name = index_name

    def get_by_id(self, _id: str) -> AbstractElasticModel:
        item = self._elastic_client.get(index=self._index_name, id=_id)
        return self.to_entity(item["_source"])

    def save(self, entity: AbstractElasticModel) -> AbstractElasticModel:
        self._elastic_client.index(
            index=self._index_name,
            id=entity.get_identifier(),
            document=entity.model_dump()
        )
        return entity

    @abstractmethod
    def to_entity(self, item: dict) -> AbstractElasticModel:
        pass
```

**Key Methods**:
- `get_by_id()` - Fetch single document by ID
- `get_all(query, limit)` - Search with optional query, default match_all
- `search_by_name(name_query)` - Exact + wildcard name search
- `save(entity)` - Index new document
- `update(entity)` - Update existing document

### Index Management Patterns

| Operation | Method | Usage |
|-----------|--------|-------|
| Read | `client.get(index, id)` | Fetch single doc by ID |
| Search | `client.search(index, query, size)` | Query documents |
| Create/Update | `client.index(index, id, document)` | Upsert document |
| Partial Update | `client.update(index, id, doc)` | Modify fields only |

**Keyword Search with Exact + Wildcard**:
```python
# src/codemie/repository/base_elastic_repository.py:42-48
query = {
    "bool": {
        "should": [
            {"term": {"name.keyword": name_query}},  # Exact match priority
            {"wildcard": {"name.keyword": f"*{name_query}*"}},
        ]
    }
}
repos_result = self._elastic_client.search(
    index=self._index_name, query=query, size=limit
)
```

### Vector Search (k-NN)

**Pattern**: Embed query → k-NN search on `dense_vector` field → Return top-k most similar docs.

(src/codemie/service/search_and_rerank/kb.py:293-335)

```python
def _knn_vector_search(self) -> list[tuple[Document, Any, Any]]:
    # Get embeddings and embed query
    embeddings = get_embeddings_model(self.kb_index.embeddings_model)
    query_vector = embeddings.embed_query(self.query)

    # Configure k-NN parameters
    knn_top_k = self.top_k * 3  # 3x multiplier
    num_candidates = min(knn_top_k * 3, 10000)  # Max 10k

    # Execute k-NN search
    return self.es.search(
        index=self.index_name,
        knn={"field": "vector", "k": knn_top_k, "num_candidates": num_candidates, "query_vector": query_vector},
        source=["text", "metadata"],
        size=knn_top_k
    )
```

**Key Parameters**: `field: "vector"` (dense_vector field), `k` (neighbors to return), `num_candidates` (search space), `query_vector` (embedded query)

### Keyword Search Patterns

(src/codemie/service/search_and_rerank/kb.py:250-291)

```python
# Text + metadata matching with path filtering
es_query = {
    "bool": {
        "minimum_should_match": 1,
        "should": [
            {"match_phrase": {"text": self.query}},
            {"match_phrase": {f"metadata.{self.exact_match_field}": self.query}},
            {"match_phrase": {f"metadata.{self.source_field}": self.query}},
        ] + [{"match_phrase": {"metadata.source": path}} for path in doc_paths]
    }
}
search_results = self.es.search(index=self.index_name, query=es_query, size=100)
```

**Aggregation Pattern**:
```python
# Fetch unique sources with metadata
agg_query = {
    "size": 0,
    "aggs": {"unique_sources": {"terms": {"field": "metadata.source.keyword", "size": 1000}}}
}
```

### Hybrid Search with RRF (Reciprocal Rank Fusion)

**Problem**: Combine vector (semantic) + keyword (exact match) search results.

**Solution**: RRF formula: `score(doc) = Σ [1 / (rank + 60)]` (60 from research paper).

(src/codemie/service/search_and_rerank/rrf.py)

```python
class RRF:
    MAGIC_NUMBER = 60

    def execute(self) -> List[Document]:
        exact_matches, fused_scores = self._preprocess_documents()
        reranked = self._filter_duplicates(self._rank_documents(fused_scores))
        return exact_matches + reranked[:self.top_k]

    def _preprocess_documents(self):
        exact_matches, fused_scores = {}, {}
        for rank, (doc, _score, _id) in enumerate(sorted(self.search_results, key=lambda x: x[1], reverse=True)):
            if doc.metadata[self.exact_match_field] in self.doc_paths:
                exact_matches[_id] = doc
            else:
                fused_scores.setdefault(_id, [_score, doc])
                fused_scores[_id][0] += 1 / (rank + self.MAGIC_NUMBER)
        return exact_matches, fused_scores
```

**Hybrid Flow** (src/codemie/service/search_and_rerank/kb.py:86-117):
```python
def execute(self) -> list[Document]:
    results = self._knn_vector_search()  # Vector
    results.extend(self._text_search(self._get_llm_sources()))  # Keyword
    return RRF(results, ...).execute()  # Combine with RRF
```

### Document Indexing & Reranking

**Document Structure**:
```python
{"text": "content...", "metadata": {"source": "file.py", "chunk_num": 0}, "vector": [0.123, ...]}
```

**Bulk Processing**: Batch size 50, LangChain `RecursiveCharacterTextSplitter`, embed chunks, index via `VectorStore.add_documents()`

**Index Names** (config.py:79-97): `applications`, `repositories`, `codemie_assistants`, `workflows`, `index_status`

**Reranking Strategy**:
1. Exact match priority (paths matched first)
2. RRF scoring (vector + keyword)
3. Deduplication by `source + chunk_num`

(src/codemie/service/search_and_rerank/rrf.py:81-102)

```python
def _filter_duplicates(self, results: dict) -> list:
    seen, filtered = set(), []
    for value in results.values():
        doc = value[1] if isinstance(value, tuple) else value
        key = f"{doc.metadata[self.source_field]}-{doc.metadata.get(self.chunk_field, 0)}"
        if key not in seen:
            filtered.append(doc)
            seen.add(key)
    return filtered
```

---

## Anti-Patterns

| Anti-Pattern | ❌ Wrong | ✅ Correct |
|--------------|---------|-----------|
| **New client per request** | `Elasticsearch(url, auth=...)` in function | `ElasticSearchClient.get_client()` |
| **Vector-only search** | `es.search(knn={...})` only | Hybrid search with RRF (vector + keyword) |
| **Wildcard without exact** | `{"wildcard": {"name.keyword": "*term*"}}` | `{"should": [{"term": ...}, {"wildcard": ...}]}` |

---

## Examples

### Repository Implementation

```python
from codemie.repository.base_elastic_repository import BaseElasticRepository
from codemie.clients.elasticsearch import ElasticSearchClient

class MyDocumentRepository(BaseElasticRepository):
    def __init__(self):
        super().__init__(ElasticSearchClient.get_client(), "my_documents")

    def to_entity(self, item: dict) -> MyDocumentModel:
        return MyDocumentModel(**item)

# Usage
repo = MyDocumentRepository()
doc = repo.get_by_id("doc-123")
repo.update(doc)
```

### Vector Search with LangChain

```python
from langchain_elasticsearch import ElasticsearchStore
from codemie.core.dependecies import get_embeddings_model

vectorstore = ElasticsearchStore(
    es_connection=ElasticSearchClient.get_client(),
    index_name="knowledge_base",
    embedding=get_embeddings_model("openai-embeddings")
)
docs = vectorstore.similarity_search("authentication patterns", k=5)
```

### Hybrid Search

```python
from codemie.service.search_and_rerank.kb import SearchAndRerankKB

results = SearchAndRerankKB(
    query="authentication patterns",
    kb_index=kb_index_info,
    llm_model="gpt-4",
    top_k=10
).execute()  # Vector + text + RRF
```

### Aggregation

```python
agg_result = ElasticSearchClient.get_client().search(
    index="repositories",
    body={"size": 0, "aggs": {"languages": {"terms": {"field": "metadata.language.keyword"}}}}
)
```

---

## Verification & Troubleshooting

### Verification

```python
# Test connection
client = ElasticSearchClient.get_client()
assert client.ping()

# Verify vector field
mapping = client.indices.get_mapping(index="knowledge_base")
assert mapping["knowledge_base"]["mappings"]["properties"]["vector"]["type"] == "dense_vector"
```

### Common Issues

| Issue | Solution |
|-------|----------|
| `ConnectionError` | Check `ELASTIC_URL`, verify ES running |
| `401 Unauthorized` | Verify `ELASTIC_USERNAME`/`ELASTIC_PASSWORD` |
| `404 Index not found` | Run indexing pipeline |
| Slow k-NN | Lower `num_candidates` or `k` |
| Missing results | Ensure same embedding model for query/index |
| Connection pool exhaustion | Use `ElasticSearchClient.get_client()` singleton |

---

## Next Steps

- **Database Integration**: See [database-patterns.md](./database-patterns.md) for PostgreSQL data sources
- **Repository Patterns**: See [repository-patterns.md](./repository-patterns.md) for data access layer
- **Service Layer**: See [service-layer-patterns.md](../architecture/service-layer-patterns.md) for service orchestration
- **Cloud Deployments**: Elasticsearch Cloud configuration in cloud-platform-integrations.md (Epic 4 Story 3)

**Source Files**:
- Client: `src/codemie/clients/elasticsearch.py`
- Repository: `src/codemie/repository/base_elastic_repository.py`
- Vector Search: `src/codemie/service/search_and_rerank/kb.py`
- RRF Algorithm: `src/codemie/service/search_and_rerank/rrf.py`
- Base Abstractions: `src/codemie/service/search_and_rerank/base.py`

---

## References

### Source Files
- `src/codemie/clients/elasticsearch.py` - ElasticSearchClient per-process singleton factory
- `src/codemie/repository/base_elastic_repository.py` - BaseElasticRepository CRUD abstraction
- `src/codemie/service/search_and_rerank/kb.py` - SearchAndRerankKB hybrid search implementation
- `src/codemie/service/search_and_rerank/rrf.py` - RRF (Reciprocal Rank Fusion) algorithm
- `src/codemie/service/search_and_rerank/base.py` - SearchAndRerankBase abstraction
- `src/codemie/datasource/base_datasource_processor.py` - Bulk indexing orchestration
- `src/codemie/configs/config.py` - Elasticsearch index name constants

### Related Patterns
- [Database Patterns](./database-patterns.md) - PostgreSQL data sources for Elasticsearch indexing
- [Repository Layer Patterns](./repository-patterns.md) - Data access layer architecture
- [Service Layer Patterns](../architecture/service-layer-patterns.md) - Service orchestration patterns

### External Resources
- [Reciprocal Rank Fusion Paper](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf) - Original RRF algorithm research (Cormack et al., 2009)
- [Elasticsearch 8.x k-NN Documentation](https://www.elastic.co/guide/en/elasticsearch/reference/current/knn-search.html) - Official k-NN vector search guide
- [LangChain Elasticsearch Integration](https://python.langchain.com/docs/integrations/vectorstores/elasticsearch) - LangChain ElasticsearchStore documentation
