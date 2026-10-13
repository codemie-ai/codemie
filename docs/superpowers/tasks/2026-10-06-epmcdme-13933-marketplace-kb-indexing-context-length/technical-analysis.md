# Technical Research

**Task**: marketplace assistants datasource indexing embeddings
**Generated**: 2026-10-06T21:40:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-13933 (Bug, Backend). AI/Run FAQ: search_kb_marketplace_assistants returns indexing error for marketplace_assistants datasource. User asks 'do we have an assistant which can provide information from epam kb?'; tool call search_kb_marketplace_assistants with {'query': 'assistant EPAM KB provide information from EPAM knowledge base'} fails: "The root cause is: 'The datasource 'marketplace_assistants' last indexing attempt failed. Error: Error code: 400 - {'error': {'message': "This model's maximum context length is 8192 tokens, however you requested 10854 tokens (10854 in your prompt; 0 for the completion). Please reduce your prompt; or completion length.", 'type': 'invalid_request_error', 'code': '400'}}. Results may be incomplete, stale, or missing entirely.'" Expected: tool completes successfully. Acceptance criteria: For the same user input, the Search Kb Marketplace Assistants tool does not return the observed indexing/context length error. Affected: AI/Run FAQ, Search Kb Marketplace Assistants tool, marketplace_assistants datasource / marketplace knowledge base (indexing of marketplace assistants into embeddings; a document/chunk exceeds embedding model 8192-token limit).

---

## 2. Codebase Findings

**Root cause.** The marketplace indexer builds one Document per published assistant and embeds every document in a single `store.add_documents` call. Nothing splits the documents or limits their tokens. `Assistant.description` has no length cap, and neither does the tools list. One oversized assistant makes the embeddings API return 400, the whole full sync fails, and `IndexInfo.error` is set. While the flag is set, `SearchKBTool.execute` raises a `ToolException` before searching. The FAQ tool therefore fails on every query until a full sync succeeds.

### Existing Implementations
- `src/codemie/datasource/loader/platform/assistant_loader.py`
  - `AssistantLoader`. L88 `ASSISTANT_PROMPT_MAX_LENGTH = 1000` is a character limit that applies to the system prompt only.
  - L259-287 `_build_page_content` joins Name, Slug, URL, Project, **`Description` (no limit)**, System Prompt[:1000], Categories, Toolkits, **Tools (every tool name, no limit)**, Created by and Usage.
  - L312-330 `_entity_to_document` returns one doc per assistant. Its metadata is `{id, name, source, popularity_score}`.
  - L332-368 `load_single_entity`.
- `src/codemie/datasource/loader/platform/platform_loader.py`
  - `BasePlatformLoader.lazy_load` yields one document per entity.
- `src/codemie/datasource/platform/base_platform_processor.py`
  - L139-153 `_process` runs lazy_load, then guardrails, then `_add_documents`. It bypasses the base `_split_documents`/`_get_splitter`.
  - L169-188 `_add_documents` makes one `store.add_documents(documents, ids=[metadata.id])` call. It records progress of 1 chunk per doc.
  - L190-213 `index_single_entity`; L215-247 `remove_single_entity` deletes by ES id, which is the assistant id.
- `src/codemie/datasource/platform/assistant_datasource_processor.py` — `AssistantDatasourceProcessor` (INDEX_TYPE platform_assistant).
- `src/codemie/service/platform/platform_indexing_service.py`
  - `sync_all_platform_datasources`, `_sync_datasource`, `index_single_assistant`, `remove_single_assistant`.
- `src/codemie/datasource/base_datasource_processor.py`
  - `process()` (L143-264): on an exception it calls `index.set_error(str(ex))` and re-raises.
  - The standard splitter `_get_splitter` (L1070-1089) uses tiktoken `o200k_base` with chunk_size 2000.
- `src/codemie/agents/tools/kb/search_kb.py`
  - L155-157: `if index_info.error: raise ToolException(health_notice)`.
  - L175-176: the PLATFORM_ASSISTANT type uses `SearchAndRerankMarketplace`.
- `src/codemie/agents/tools/datasource_health_mixin.py` — L34-54 builds the "last indexing attempt failed ... Results may be incomplete" text.
- `src/codemie/core/dependecies.py` — L94-105 `AzureOpenAIEmbeddings(..., check_embedding_ctx_length=False)`. LangChain neither chunks nor rejects over-long input.
- `config/llms/llm-azure-config.yaml` — L165-171: the default embedding model is `text-embedding-ada-002`, with an 8191-token input limit.
- Token counters:
  - `src/codemie_tools/base/utils.py:176` `get_encoding` (tiktoken, falls back to o200k_base).
  - `src/codemie/core/utils.py:190` `calculate_tokens`.

### Architecture and Layers Affected
- **Loader** — AssistantLoader builds the page content. This is the primary fix point.
- **Datasource processor** — BasePlatformDatasourceProcessor `_add_documents`/`_process`. This is the secondary fix point, for resilience.
- **Service** — PlatformIndexingService. No change expected.
- **Agent tool** — SearchKBTool and its health mixin. No change expected; it behaves as designed once the index is healthy.
- **Router** — `assistant.py` publish/update triggers incremental indexing. `admin.py` `POST /admin/marketplace/reindex` triggers a full sync.

### Integration Points
- The FAQ assistant template (`config/templates/assistant/admin/codemie_onboarding_template.yaml` L46-50) has a `marketplace_assistants` knowledge_base context. `toolkit_service._add_kb_tools` turns that context into `search_kb_marketplace_assistants`.
- Embeddings are reached through the LiteLLM / Azure / Vertex / Bedrock providers (`get_embeddings_model`). The vector store is ElasticsearchStore.
- Incremental indexing on publish/update (`assistant.py` L951-957, L1579-1583, `_index_marketplace_assistant` L3168-3199) logs errors and swallows them. It never sets or clears the index error.
- A full sync runs either at startup (when `PLATFORM_DATASOURCES_SYNC_ENABLED` is set) or through the admin reindex endpoint.

### Patterns and Conventions
- Template-method processors that override `_init_loader`/`_process`.
- Pydantic whitelist sanitization (`AssistantDocument`, `SafeToolKit`, `SafeTool`).
- Loader class constants for truncation (`ASSISTANT_PROMPT_MAX_LENGTH`).
- Token budgets use tiktoken `o200k_base`. Other loaders chunk at 1000-2000 tokens.
- Structured logging with `extra={...}`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/data/elasticsearch-integration.md` — ES patterns. It does not cover chunk or token limits.
- `.ai-run/guides/testing/testing-patterns.md` and `testing-service-patterns.md` — pytest conventions.
- `.ai-run/guides/quality-gates.md` — `make test`. The MR must include the output of `make test-harness`.
- `.ai-run/guides/agents/agent-tools.md` — tool patterns.

### Architectural Decisions
- No ADR exists for the marketplace indexer. It was introduced in EPMCDME-9519 (afc61fa12).
- EPMCDME-12384 (a217673f0) introduced the health status and the ToolException raised on FAILED.

### Derived Conventions
- One ES document per assistant, keyed by the assistant id. Upsert and delete-by-id depend on this, so truncation fits better than splitting.
- Every other indexer bounds chunk tokens through `RecursiveCharacterTextSplitter.from_tiktoken_encoder`. The platform indexer is the only exception.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/datasource/loader/test_assistant_loader.py` — sanitize, `_entity_to_document` content/metadata, load_single_entity.
- `tests/codemie/datasource/platform_tests/test_assistant_datasource_processor.py` — INDEX_TYPE, loader init, remove_single_entity.
- `tests/codemie/service/platform/test_platform_indexing_service.py` — registry, sync, single index/remove.
- `tests/codemie/agents/tools/kb/test_search_kb.py` — health notices and the ToolException on error.

### Testing Framework and Patterns
- pytest 8 with pytest-mock, pytest-asyncio and pytest-xdist. `pytest.ini` sets `--import-mode=importlib -n 3`.
- Tests are class-based and use `unittest.mock.patch` on module paths and `Mock(spec=Assistant)`, with AAA comments.
- Targeted run: `poetry run pytest tests/codemie/datasource/loader/test_assistant_loader.py tests/codemie/datasource/platform_tests tests/codemie/service/platform`

### Coverage Gaps
- No test covers an oversized description or tools list in the page content.
- No test covers `BasePlatformDatasourceProcessor._process` / `_add_documents`, or a single bad document making the batch fail.
- No test checks the token budget against the embedding limit.

---

## 5. Configuration and Environment

### Environment Variables
- `PLATFORM_MARKETPLACE_DATASOURCE_NAME` (default `marketplace_assistants`)
- `PLATFORM_DATASOURCES_SYNC_ENABLED` (default False)
- `DATASOURCES_CONFIG_DIR`
- `AZURE_OPENAI_*`

### Configuration Files
- `config/datasources/datasources-config.yaml` holds per-loader chunk sizes and has no platform section. `LoadersConfig` has `extra="forbid"`, so adding a section needs a model field.
- `config/llms/*.yaml` defines the embedding models.

### Feature Flags and Deployment Concerns
- An index that is already FAILED stays FAILED after the fix until a full reindex succeeds. Ops must run `POST /admin/marketplace/reindex` or restart with `PLATFORM_DATASOURCES_SYNC_ENABLED=true`.

---

## 6. Risk Indicators

- **Unbounded input.** `Assistant.description` and the tool list have no caps. Any char-based cap is fragile, so the budget should be in tokens, using tiktoken.
- **All-or-nothing batch.** `_add_documents` embeds every document in one call. A single failure fails the whole index and blocks the FAQ tool for all queries.
- **Splitting cost.** Splitting into multiple chunks per assistant would break the id-based upsert/delete in `remove_single_entity` and the progress counting. Truncation is lower-risk.
- **Encoding mismatch.** Tokenizers differ: ada-002 uses cl100k while the repo convention is o200k_base. The budget must leave headroom, roughly 2000 tokens against an 8191 limit.
- **Silent incremental failures.** The publish path swallows errors, so an oversized assistant can silently fail to index.
- **Existing FAILED state.** It persists until a reindex runs; this is an operational step outside the code change.
- **Embedding model limits are not in config.** Embedding configs do not set `max_input_tokens`, so the budget has to be a constant.
- **Test gaps.** The platform processor `_process`/`_add_documents` paths have no tests.

---

## 7. Summary for Complexity Assessment

The fix is localized to the marketplace (platform) indexing pipeline: the loader layer (`AssistantLoader._build_page_content`) and possibly the processor layer (`BasePlatformDatasourceProcessor._add_documents`). The router, service and tool layers need no functional change. The expected change surface is 1-2 source files plus 1-2 test files.

The technique is not novel. Every other indexer already bounds chunks with tiktoken through `RecursiveCharacterTextSplitter.from_tiktoken_encoder`, and the loader already truncates the system prompt with a class constant. The recommended direction has two parts:

- Enforce a token budget on the page content, primarily by truncating the description and the tools list, so each assistant stays one document and id-based upsert/delete keeps working.
- Optionally, make full-sync embedding resilient per document, so one bad assistant cannot fail the whole datasource.

Test coverage for the loader exists. The platform processor's `_process`/`_add_documents` is untested, so new tests are needed. The key risks are:

- tokenizer mismatch, which calls for a conservative budget;
- the all-or-nothing batch;
- the operational need to re-run the full marketplace reindex to clear the persisted error state.
