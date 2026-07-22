# Technical Research

**Task**: datasource indexing restricted-content api
**Generated**: 2026-07-13T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Ticket: EPMCDME-10548. I discovered, that there are include_restricted_content on api request, that when set to true correctly indexes everything. Although, it is not passed correctly in some places, so it is not saved actually in db. This needs to be fixed too. But overall task is to make it new create datasource requests will have this field true by default

---

## 2. Codebase Findings

### Existing Implementations

The field `include_restricted_content` is a boolean flag that controls whether Confluence pages restricted to specific users/groups are included during indexing. It exists at four distinct levels of the stack:

- `src/codemie/rest_api/models/index.py:125-135` — `ConfluenceIndexInfo` (Pydantic sub-model stored as JSONB in the `index_info` table). Has `include_restricted_content: Optional[bool] = False`. This is the persistent representation.
- `src/codemie/rest_api/models/index.py:1236-1250` — `IndexKnowledgeBaseConfluenceRequest` (HTTP POST request schema). Has `include_restricted_content: Optional[bool] = False`. This is the API entry point.
- `src/codemie/rest_api/models/index.py:1432` — `UpdateKnowledgeBaseConfluenceRequest` (HTTP PUT request schema). Does **not** have `include_restricted_content` at all — only `cql`, `description`, `project_space_visible`.
- `src/codemie/datasource/confluence_datasource_processor.py:45-55` — `IndexKnowledgeBaseConfluenceConfig` (in-memory runtime config, Pydantic). Has `include_restricted_content: Optional[bool] = False`. This is the object that carries the value through the indexing pipeline.
- `src/codemie/datasource/confluence_datasource_processor.py:57-66` — `to_confluence_index_info()` and `from_confluence_index_info()` — the two broken conversion methods.
- `src/codemie/datasource/loader/confluence_loader.py` — `ConfluenceDatasourceLoader`. Receives `include_restricted_content` correctly and uses it to decide whether to call `is_public_page()` filtering.
- `src/codemie/rest_api/routers/index.py:1020` — POST endpoint `index_knowledge_base_confluence()`. Correctly maps the request field into `IndexKnowledgeBaseConfluenceConfig`.
- `src/codemie/triggers/actors/datasource.py:277` — reindex (cron/scheduled) path that calls `from_confluence_index_info()` to reconstruct config from DB.
- `src/codemie/triggers/actors/datasource.py:880` — resume-stale path that also calls `from_confluence_index_info()`.
- `src/codemie/triggers/bindings/webhook.py:471` — webhook-triggered reindex; uses `from_confluence_index_info()`.

### Architecture and Layers Affected

| Layer | Component | File |
|---|---|---|
| API Schema | `IndexKnowledgeBaseConfluenceRequest`, `ConfluenceIndexInfo`, `UpdateKnowledgeBaseConfluenceRequest` | `src/codemie/rest_api/models/index.py` |
| Router | `index_knowledge_base_confluence()` POST, `update_knowledge_base_confluence()` PUT | `src/codemie/rest_api/routers/index.py` |
| Runtime Config | `IndexKnowledgeBaseConfluenceConfig` | `src/codemie/datasource/confluence_datasource_processor.py` |
| Processor/Service | `ConfluenceDatasourceProcessor._init_index()`, `_init_loader()` | `src/codemie/datasource/confluence_datasource_processor.py` |
| DB Persistence | `IndexInfo` SQLModel table; `confluence` JSONB column via `PydanticType(ConfluenceIndexInfo)` | `src/codemie/rest_api/models/index.py` |
| Loader | `ConfluenceDatasourceLoader` | `src/codemie/datasource/loader/confluence_loader.py` |
| Triggers | reindex actors, webhook bindings | `src/codemie/triggers/actors/datasource.py`, `src/codemie/triggers/bindings/webhook.py` |

### Integration Points

**Internal module dependencies (relevant to this task):**

- `rest_api/routers/index.py` → `rest_api/models/index.py` (imports `IndexKnowledgeBaseConfluenceRequest`, `IndexInfo`)
- `rest_api/routers/index.py` → `datasource/confluence_datasource_processor.py` (imports `IndexKnowledgeBaseConfluenceConfig`, `ConfluenceDatasourceProcessor`)
- `datasource/confluence_datasource_processor.py` → `rest_api/models/index.py` (imports `IndexInfo`, `ConfluenceIndexInfo`)
- `datasource/confluence_datasource_processor.py` → `datasource/loader/confluence_loader.py` (imports `ConfluenceDatasourceLoader`)
- `triggers/actors/datasource.py` → `datasource/confluence_datasource_processor.py` (calls `from_confluence_index_info()`)
- `triggers/bindings/webhook.py` → `datasource/confluence_datasource_processor.py` (same)

**External services:**
- Confluence REST API (via `langchain_community.ConfluenceLoader` extended by `ConfluenceDatasourceLoader`) — `include_restricted_content=True` suppresses the `is_public_page()` filtering call on each document.

### Patterns and Conventions

- JSONB persistence via `PydanticType`: Datasource sub-settings are stored as JSON blobs in the `confluence` column of `index_info`. The field's value in DB is controlled entirely by what is passed to the `ConfluenceIndexInfo(...)` constructor before calling `IndexInfo.new()` or `.save()`. No DB migration is needed for field additions or default changes — the JSON envelope absorbs the change.
- Conversion pair pattern: `to_confluence_index_info()` / `from_confluence_index_info()` serve as the serialization boundary between the runtime config object and the persistent model. Both must be updated together when any field is added.
- Field defaults are set inline as Python literals (`Optional[bool] = False`). There is no config-injection, factory, or env-var driving these defaults — all three layers currently hardcode `False`.
- `IndexInfo.new()` stores the `ConfluenceIndexInfo` object verbatim — it does not merge or supplement field values.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/data/database-patterns.md` — confirms JSONB-backed `PydanticType` columns require no migration for field changes; persistence is controlled by what is passed to the model constructor. Relevant rule: changes to `ConfluenceIndexInfo` fields do not require a new Alembic migration.
- `.ai-run/guides/data/repository-patterns.md` — repositories own persistence; services must not bypass them. No direct guidance on field defaults.
- `.ai-run/guides/architecture/layered-architecture.md` — router validates/delegates to service; service orchestrates; repository persists. Confirmed: no DB code in routers.
- `.ai-run/guides/api/endpoint-conventions.md` — API schemas live in `src/codemie/rest_api/models/`; use Pydantic/SQLModel typed request models; router validates, service orchestrates.

### Architectural Decisions

No ADRs or DECISION comments were found for `restricted_content` or datasource field defaults anywhere in the codebase. The `False` default appears to be an original implementation choice with no recorded rationale.

### Derived Conventions

- Field defaults in Pydantic/SQLModel schemas are set at the class level as inline literals. To change a default for new API requests, change the literal in the request schema (`IndexKnowledgeBaseConfluenceRequest`) and in the runtime config class (`IndexKnowledgeBaseConfluenceConfig`). Changing only the request schema is insufficient — the runtime config default governs re-index paths.
- All flags in `IndexKnowledgeBaseConfluenceConfig` (`include_archived_content`, `include_attachments`, `include_comments`, `keep_markdown_format`, `keep_newlines`) are also currently not persisted — the conversion methods only handle `cql`. The fix for `include_restricted_content` should address all of them in the same change to avoid a recurring class of bug.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/datasource/test_confluence_datasource_processor.py` — covers `to_confluence_index_info()` and `from_confluence_index_info()` but only asserts `cql` and that `include_restricted_content` is `False`. The assertions will break after the default change and must be updated. Does NOT test that `include_restricted_content=True` is preserved through the round-trip.
- `tests/codemie/datasource/loader/test_confluence_loader.py` — covers `ConfluenceDatasourceLoader.lazy_load()` with both `include_restricted_content=False` (regular) and `=True` (integration fixture). Tests loading behavior only, not creation or persistence.
- `tests/codemie/rest_api/routers/test_index.py` — covers POST/PUT/reindex for Jira, Git, and Confluence reindex (PUT). No test for POST `/v1/index/knowledge_base/confluence` with `include_restricted_content` set.
- `tests/codemie/service/provider/datasource/test_provider_datasource_creation_service.py` — covers provider-pattern datasource creation path; does not touch Confluence-specific code.
- `tests/codemie/service/index/test_index_service.py` — covers `IndexStatusService` list/filter; no Confluence sub-object or restricted-content coverage.

### Testing Framework and Patterns

- pytest 8.3.x with pytest-asyncio 0.23.x, pytest-mock 3.14.x, pytest-cov 5.x, pytest-httpx 0.35.x
- `unittest.TestCase` style with `setUp()` for datasource processor unit tests
- Pure pytest style (fixtures + `assert`) for router and service tests
- `FastAPI TestClient` used at both app and router level
- `app.dependency_overrides[authenticate]` pattern for auth bypass
- Global `autouse` fixture in `tests/conftest.py` mocks `PostgresClient.get_engine` session-wide
- `@patch(...)` decorator stacking for router tests; `MagicMock(spec=...)` for typed mocks

### Coverage Gaps

- **No test for `include_restricted_content` round-trip through `to_confluence_index_info()` / `from_confluence_index_info()`** — the core bug is completely uncovered.
- **No test for `include_restricted_content` default value being `True`** — required after the default change.
- **No test for POST `/v1/index/knowledge_base/confluence` creation endpoint** in the router test file (only reindex/PUT is tested).
- **No test for `_init_index()` DB persistence** verifying the stored `index.confluence.include_restricted_content` value matches what was passed in.
- **No test for `from_confluence_index_info` preserving `include_restricted_content`** — the existing test uses a `MagicMock` with no `include_restricted_content` attribute set, so a fix or regression here goes undetected.

---

## 5. Configuration and Environment

### Environment Variables

No environment variable governs `include_restricted_content` or any Confluence restricted-content behavior. The field is purely controlled by Pydantic model defaults.

### Configuration Files

- `config/` — contains LLM configs and assistant/workflow YAML templates only. No datasource-level defaults.
- `datasource/datasources_config.py` — governs `CONFLUENCE_CONFIG` (loader page limits and timeout). Does not include `include_restricted_content`.

### Feature Flags and Deployment Concerns

- No feature flags found related to this field.
- **Existing DB records**: Rows already in `index_info` have `include_restricted_content` absent or `false` in the `confluence` JSONB column. After fixing `to_confluence_index_info()`, new creates and updates will persist the correct value — but existing records will not be updated retroactively. A manual SQL backfill may be needed if the intent is to apply the new default to pre-existing datasources:
  ```sql
  UPDATE index_info
  SET confluence = confluence || '{"include_restricted_content": true}'
  WHERE confluence IS NOT NULL AND confluence->>'include_restricted_content' IS NULL;
  ```
- **No Alembic migration required**: the `confluence` column is an existing JSONB column; adding or changing sub-fields requires no schema migration.

---

## 6. Risk Indicators

- **Silent data loss broader than one field**: `to_confluence_index_info()` and `from_confluence_index_info()` only round-trip `cql`. All other flags — `include_archived_content`, `include_attachments`, `include_comments`, `keep_markdown_format`, `keep_newlines` — are also silently dropped on every save. Fixing only `include_restricted_content` leaves the other fields broken. The correct fix is to forward all fields in both methods.
- **Three independent default locations must be changed in sync**: `ConfluenceIndexInfo` (line 127), `IndexKnowledgeBaseConfluenceConfig` (line 47), and `IndexKnowledgeBaseConfluenceRequest` (line 1239). Changing only the request schema is insufficient — the runtime config default governs reindex paths that do not go through the API.
- **Existing tests will break on default change**: `test_confluence_datasource_processor.py` asserts `assertFalse(result.include_restricted_content)` in both `to_confluence_index_info` and `from_confluence_index_info` tests. These assertions must be updated to `assertTrue`.
- **`UpdateKnowledgeBaseConfluenceRequest` lacks the field entirely**: The PUT endpoint cannot currently accept or change `include_restricted_content`. This is a secondary gap — it is not the reported bug, but it means callers who created a datasource before this fix cannot update the field via the API.
- **Three trigger paths call `from_confluence_index_info()`**: `triggers/actors/datasource.py:277`, `triggers/actors/datasource.py:880`, and `triggers/bindings/webhook.py:471`. All three reindex paths will benefit from fixing the conversion method, but all three also need to be tested — none currently are.
- **No test for the POST creation endpoint in `test_index.py`**: The existing Confluence router test only covers the reindex (PUT) path. The creation path has no router-level test at all.
- **Existing datasource records** in production DB carry `false` or absent `include_restricted_content`. A backfill decision is required if retroactive behavior change is intended.

---

## 7. Summary for Complexity Assessment

This task touches four architectural layers: API Schema, Runtime Config/Processor, Persistence (conversion methods), and Triggers. The primary file change surface is narrow — two files at the core (`src/codemie/datasource/confluence_datasource_processor.py` and `src/codemie/rest_api/models/index.py`) and one secondary file if `UpdateKnowledgeBaseConfluenceRequest` is extended (`rest_api/models/index.py` again). The trigger files (`triggers/actors/datasource.py`, `triggers/bindings/webhook.py`) require no code changes — they call `from_confluence_index_info()`, which will be fixed in place. Total estimated file changes: 2 source files, 1-2 test files.

The task does not introduce any novel patterns. All changes follow established conventions: inline Pydantic default changes and field forwarding in existing constructor calls. The only technically nuanced part is ensuring `to_confluence_index_info()` and `from_confluence_index_info()` forward all fields (not just `cql`) — this affects six boolean flags total, not just `include_restricted_content`. Fixing all of them in the same change is low-risk and avoids a recurring class of bug. No Alembic migration is needed because the `confluence` column is already a JSONB/PydanticType column.

Test coverage posture for this area is weak: the conversion methods are tested but only assert the already-broken `False` default, and the POST creation endpoint has no router test at all. Any PR for this task should update the existing processor tests (which will fail after the default change) and add a round-trip test for `include_restricted_content=True` through `to_confluence_index_info()` / `from_confluence_index_info()`. The risk profile is low-to-medium: the code change is small and pattern-following, but the test coverage required to prove the fix is non-trivial, and the secondary scope (all six dropped flags, the missing PUT field, existing DB records) requires explicit scoping decisions before implementation.
