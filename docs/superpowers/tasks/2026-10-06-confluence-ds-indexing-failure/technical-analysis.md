# Technical Research

**Task**: confluence datasource indexing knowledge base
**Generated**: 2026-10-06
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-15405 — Confluence data source indexing fails in kb-data-source

Bug: In project epm-e3s, Confluence data source kb-data-source fails on indexing.

Steps:
1. Open Confluence data source kb-data-source in epm-e3s
2. Start indexing

Expected: Indexing completes successfully.

Actual:
No content with id <ContentId{id=1718030710}> can be found.
Log referral number is ee7f6489-46ea-4a6a-b003-5003bf33f80c

Labels: AI-Generated, AI/Run, Backend, Horizon2, Triaged, codemie_feedback
Status: Ready for dev
Issue type: Bug

---

## 2. Codebase Findings

### Existing Implementations

- **`src/codemie/datasource/loader/confluence_loader.py`** — `ConfluenceDatasourceLoader` extends upstream `langchain_community.document_loaders.ConfluenceLoader` (pinned `langchain-community = 0.4.1` in `pyproject.toml`). Customizations: tenacity `@retry` on `_search_content_by_cql` and `is_public_page` (transient HTTP 502/503/504 and `ConnectionError` only); overridden `lazy_load()` paginates CQL via `_links.next` and delegates to upstream `process_pages()`. Expand on search: `body.view` (via `ContentFormat.VIEW`), `version`, optional `metadata.labels`. No per-page error isolation; any exception aborts the whole indexing run.
- **Upstream `ConfluenceLoader` (langchain_community 0.4.1)** — `process_pages()` calls `is_public_page()` when `include_restricted_content` is false (hits `get_all_restrictions_for_content`). `process_page()` may call `get_page_comments`, `get_attachments_from_content`, and attachment download helpers. `get_page_by_id` path has upstream tenacity; CQL search path in CodeMie uses custom `_search_content_by_cql` with retries.
- **`src/codemie/datasource/confluence_datasource_processor.py`** — `ConfluenceDatasourceProcessor` (`INDEX_TYPE = "knowledge_base_confluence"`). Builds loader via `_initialize_confluence_loader`; `_enhance_cql_query()` wraps CQL with `type=page` when `type` absent and appends `ORDER BY type DESC`. `IndexKnowledgeBaseConfluenceConfig.from_confluence_index_info()` restores only `cql` and `include_restricted_content` from persisted `ConfluenceIndexInfo` (other flags fall back to runtime defaults on reindex/resume/cron).
- **`src/codemie/datasource/base_datasource_processor.py`** — `_load_and_process_documents()` iterates `loader.lazy_load()`; uncaught exceptions hit `index.set_error(str(ex))` (~line 261). `_persist_load_stats()` merges `get_load_stats()` from loader (`FAILED_DOCUMENTS_KEY`, `SKIPPED_DOCUMENTS_KEY`) when implemented; **Confluence loader does not implement `get_load_stats()`**.
- **`src/codemie/rest_api/routers/index.py`** — POST `/index/knowledge_base/confluence` creates datasource with full `IndexKnowledgeBaseConfluenceConfig`. PUT `/index/knowledge_base/confluence` with `full_reindex=true` builds `ConfluenceDatasourceProcessor` with `index=kb_index[0]` only (no request body config); `_init_index()` reloads config from DB via `from_confluence_index_info`.
- **`src/codemie/triggers/actors/datasource.py`** — Cron/webhook reindex uses same `from_confluence_index_info` path (~line 286).
- **`src/codemie/rest_api/models/index.py`** — `ConfluenceIndexInfo` JSONB stores `cql`, `include_restricted_content` (default `False`), `include_archived_content`, `include_attachments`, `include_comments`, etc. `IndexKnowledgeBaseConfluenceConfig` defaults `include_restricted_content=True` (mismatch with persisted model default).
- **`src/codemie/service/index/datasource_health_check_service.py`** — Uses `ConfluenceDatasourceProcessor.check_confluence_query` (CQL count only; does not exercise `process_pages`).
- **Dependencies** — `atlassian-python-api = ^4.0.6`, `langchain-community = 0.4.1`; Confluence client comes from langchain/atlassian stack, credentials via `SettingsService.get_confluence_creds` and `codemie_tools` `ConfluenceConfig`.

### Architecture and Layers Affected

| Layer | Components |
|-------|------------|
| Loader / integration | `ConfluenceDatasourceLoader`, upstream `ConfluenceLoader.process_pages` / `process_page` |
| Processor | `ConfluenceDatasourceProcessor`, `BaseDatasourceProcessor._load_and_process_documents` |
| API / persistence | `index.py` routers, `ConfluenceIndexInfo`, reindex PUT without inline config |
| Triggers | `triggers/actors/datasource.py` reindex/resume |
| Error surfacing | `IndexInfo.set_error`, indexing logs (`request_uuid` / referral in ops tooling) |

### Integration Points

- Confluence REST: `rest/api/content/search` (CQL + expand), `get_all_restrictions_for_content`, `get_page_comments`, `get_attachments_from_content`, attachment download URLs.
- Elasticsearch indexing via `BaseDatasourceProcessor` batch pipeline.
- Project integration settings (`setting_id` on `IndexInfo`) for Confluence OAuth/token.

### Patterns and Conventions

- Loader-level retries (tenacity + `STORAGE_CONFIG.indexing_max_retries`) for **search** only; per-page upstream calls are fail-fast.
- Other loaders (e.g. `sharepoint_loader.py`, `xwiki_loader.py`) track partial failures via `get_load_stats()` / `FAILED_DOCUMENTS_KEY`; Confluence does not.
- CQL enhancement and `/wiki` URL suffix for `atlassian.net` in processor.
- Prior related work: EPMCDME-14732 (CQL retry), EPMCDME-15184 (connection retry on search), EPMCDME-10548 (`include_restricted_content` persistence).

### Root cause hypotheses (for investigation on epm-e3s / content id 1718030710)

The error string matches Confluence Cloud/Server REST **404 NotFoundException** JSON (`translation`: `No content with id <ContentId{id=…}> can be found`), not an in-repo exception type. Likely origins in this stack:

1. **Per-page follow-up API after CQL match (most likely for mid-index failure)** — CQL returns page metadata, then upstream `process_page` / `is_public_page` / comments / attachments requests a content id that Confluence no longer exposes (deleted/trashed/draft parent, broken hierarchy, or insufficient permission masked as 404). Fails entire job because nothing catches 404 at page granularity.
2. **Reindex config drift** — PUT reindex and triggers load only `cql` + `include_restricted_content` from DB. If `include_restricted_content` is `false` in `index_info.confluence`, `is_public_page` runs extra restriction API calls (more 404 surface). Attachments/comments flags in DB are ignored on reindex (defaults used).
3. **CQL / content-set issue** — `kb-data-source` CQL may include pages under spaces with corrupt ancestor data; known Confluence Cloud issue **CONFCLOUD-77618** (404 on content search when expand includes ancestor restriction expansions). CodeMie's search expand is `body.view,version[,metadata.labels]` (not ancestor restrictions), so this applies mainly if the failing call is still `content/search` with a different expand from Confluence or a pagination `next` URL carrying extra expand params.
4. **Stale search index** — Page id appears in CQL results then disappears before body/restriction fetch (race or index lag); rare but consistent with single-id 404 mid-run.
5. **Health check false negative** — `check_confluence_query` only runs `fetch_remote_stats` (`cql` limit 1); does not validate that every result page can be expanded/processed.

Confirm hypothesis by correlating log referral `ee7f6489-46ea-4a6a-b003-5003bf33f80c` with stack trace (which method: `_search_content_by_cql` vs `get_all_restrictions_for_content` vs `get_page_comments` vs attachment fetch) and by inspecting `index_info.confluence` for `kb-data-source` (CQL, flags).

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/confluence-integration.md` — Points to `confluence_datasource_processor.py` and base processor; no indexing failure / 404 handling guidance.
- `AGENTS.md` — Integration category references confluence guide.

### Architectural Decisions

- Retry policy for Confluence search documented in `config/datasources/datasources-config.yaml` (`retry_transient_status_codes`, `indexing_max_retries` under storage section).
- Task docs under `docs/superpowers/tasks/2026-09-09-EPMCDME-14732-*` and `2026-09-29-epmcdme-15184` record deliberate scope: retry on CQL search only, not upstream `process_pages` calls.

### Derived Conventions

- Indexing errors are stored as `str(exception)` on the datasource row; user-visible message is often the Confluence API body text.
- Partial success is supported at processor level via loader `get_load_stats()` but not wired for Confluence.

---

## 4. Testing Landscape

### Existing Coverage

- **`tests/codemie/datasource/loader/test_confluence_loader.py`** — `fetch_remote_stats`, `_search_content_by_cql`, `lazy_load` pagination, retry on search HTTP 502/503/504 and connection errors. Integration tests patch `process_pages` or set `include_restricted_content=True` to avoid restriction API. **No tests for upstream `process_page` 404 or skip behavior.**
- **`tests/codemie/datasource/test_confluence_datasource_processor.py`** — CQL enhancement, loader init, config round-trip for `include_restricted_content`, markdown splitting.
- **`tests/codemie/rest_api/routers/test_index.py`** — Confluence processor mocked on create; limited reindex coverage.

### Testing Framework and Patterns

- pytest, `unittest.mock.patch` / `MagicMock`, `requests.exceptions.HTTPError` with mocked `response.status_code` and text.

### Coverage Gaps

- No test that simulates Confluence 404 `No content with id` during `process_pages` / `process_page`.
- No test for reindex path config loaded from `ConfluenceIndexInfo` only.
- No processor test asserting indexing completes with skipped failed pages (pattern exists for SharePoint/XWiki loaders, not Confluence).

---

## 5. Configuration and Environment

### Environment Variables

- Confluence credentials resolved via settings service (project + `setting_id`); no feature-specific env vars in loader code.
- Datasource tuning: `config/datasources/datasources-config.yaml` — `confluence_loader.loader_max_pages` (1000), `loader_pages_per_request` (20), `loader_batch_size` (50), `loader_timeout` (180), `retry_transient_status_codes` [502,503,504]; storage `indexing_max_retries` (2 attempts per comments in yaml).

### Configuration Files

- `config/datasources/datasources-config.yaml` — Confluence loader limits and retry status codes.
- `src/codemie/datasource/datasources_config.py` — `CONFLUENCE_CONFIG`, `STORAGE_CONFIG` pydantic models.

### Feature Flags and Deployment Concerns

- No feature flag gates Confluence indexing behavior.
- Cloud vs on-prem (`ConfluenceConfig.cloud`) switches loader auth constructor in `_initialize_confluence_loader`.

---

## 6. Risk Indicators

- **Fail-fast semantics** — One bad page in a large CQL set aborts entire `kb-data-source` reindex; no skip counter for Confluence.
- **Upstream dependency** — Fix may require overriding `process_pages` / `process_page` / `is_public_page` in `ConfluenceDatasourceLoader` rather than patching langchain in place.
- **Speculative: implement page-level try/except** for HTTP 404 / atlassian not-found errors with warning log (page id, title, CQL context), increment `FAILED_DOCUMENTS_KEY`, implement `get_load_stats()` mirroring XWiki/SharePoint; product decision whether indexing status is still `complete` with partial failures.
- **Speculative: CQL search batch 404** — If stack trace shows failure inside `_search_content_by_cql`, add targeted retry with reduced `expand` or split batch / cursor recovery; align with CONFCLOUD-77618 workarounds used by other products (detect body signature `No content with id`).
- **Speculative: extend `from_confluence_index_info` / `to_confluence_index_info`** to round-trip all `ConfluenceIndexInfo` fields so reindex matches UI-stored options (attachments/comments/archived).
- **Ops dependency** — Content id `1718030710` may need Confluence admin cleanup (draft/trashed ancestor) if skip is unacceptable; health check will not catch until full index.
- **Regression risk** — Swallowing 404s could hide auth misconfiguration; distinguish 404 on restriction endpoint vs page body (403 vs 404) in logging.
- **Retry gap** — Per-page 404 is not transient; should not use existing CQL retry decorator.

### Suggested test strategy (for implementer)

1. Unit: mock `process_page` or Confluence client to raise `HTTPError` with Confluence JSON body for id `1718030710`; assert `lazy_load` yields other pages and `get_load_stats()` reports one failed/skipped.
2. Unit: `include_restricted_content=False` path — mock `get_all_restrictions_for_content` 404; assert skip vs fail per chosen policy.
3. Unit: `_search_content_by_cql` returns 404 on `get(next_url)` with signature message; assert retry or graceful handling if implemented.
4. Processor: optional integration test with patched loader returning mixed success/failure documents through `_load_and_process_documents` (heavy; may stay loader-only).
5. Manual: reindex `kb-data-source` on epm-e3s after fix; verify referral logging includes page id and indexing reaches `complete` with `processing_info.failed_documents` if applicable.

---

## 7. Summary for Complexity Assessment

Confluence knowledge-base indexing for EPMCDME-15405 flows through `ConfluenceDatasourceLoader.lazy_load()` → upstream `process_pages()` → `BaseDatasourceProcessor._load_and_process_documents()`. The reported error is a Confluence API 404 for a specific content id, not a CodeMie-defined message; the repository currently propagates it uncaught, so a single problematic page fails the whole datasource. Primary change surface is `src/codemie/datasource/loader/confluence_loader.py` (override processing or wrap upstream methods, optional `get_load_stats()`), with secondary touches to `confluence_datasource_processor.py` if config round-trip or CQL handling changes, and tests in `tests/codemie/datasource/loader/test_confluence_loader.py`. API and trigger layers likely unchanged unless product requires better error detail on `IndexInfo`.

Technical novelty is moderate: patterns for partial failure already exist in SharePoint/XWiki loaders; Confluence still delegates heavy lifting to langchain 0.4.1. Test coverage for Confluence focuses on CQL pagination and transient search retries, not 404 during page materialization. Key risks are product policy on partial index success, avoiding masking auth failures, and confirming the failing HTTP call via production logs before choosing skip-vs-fix-CQL vs Confluence content remediation.

---

## 8. External References

None named by the task.

(Investigation may reference public Atlassian issue **CONFCLOUD-77618** and community reports of identical `No content with id <ContentId{id=…}>` on Confluence REST search/expand; not cited as a task source of truth.)
