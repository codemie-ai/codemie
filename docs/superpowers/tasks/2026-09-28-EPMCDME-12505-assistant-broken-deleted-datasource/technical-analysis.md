# Technical Research

**Task**: assistant datasource context deletion
**Generated**: 2026-09-28
**Research path**: filesystem

---

## 1. Original Context

Jira EPMCDME-12505 "Assistant becomes broken if attached datasource is deleted" (Major, backend).
Steps: create datasource → attach to assistant → delete datasource → chat with assistant → 500 from /v1/assistants/{id}/model with "Assistant Error: An error occurred during assistant initialization: Cannot initialize assistant, missed datasource context in system: Datasource name: X". Found after changes for EPMCDME-12384.
AC: deleting an attached datasource must not make the assistant fail with 500; missing datasource references handled gracefully at init; platform either auto-removes invalid references or shows a clear actionable validation message; chat remains operational or a controlled non-500 error with guidance; regression coverage vs EPMCDME-12384.
Known starting points (verify): AssistantService.check_context (src/codemie/service/assistant_service.py ~L941, called ~L540) raises MissingContextException using Assistant.get_deleted_context (src/codemie/rest_api/models/assistant.py ~L870, matches by (repo_name, context_type) within assistant.project); routers/assistant.py catches MissingContextException ~L2344 and ~L2450 and raises an assistant error (check what HTTP code); DELETE /index/{index_id} in src/codemie/rest_api/routers/index.py ~L422 (and ProviderDatasourceDeletionService) doesn't update assistants; Assistant.by_datasource_run exists. Existing test tests/codemie/service/test_assistant_service_check_context.py. EPMCDME-12384 commit a217673f0 (datasource health status in SearchKBTool). Also investigate: how context is turned into tools (what happens downstream if a deleted context is simply skipped), other callers of check_context / get_deleted_context (e.g. workflows, assistant validation/update endpoints, UI-facing validation), whether assistants referencing deleted context can still be edited/saved, and how the assistant error maps to 500.
Scope decision (auto-drop at init vs cleanup on delete vs controlled 4xx) is still open — document options' impact, do not decide.

---

## 2. Codebase Findings

### Existing Implementations

All starting points were checked against branch `EPMCDME-12505` (HEAD `1ba7afa30`).

- `src/codemie/rest_api/models/assistant.py`
  - L70 `class MissingContextException(Exception)` is a plain exception with no HTTP semantics.
  - L100 `ContextType` has three values: `KNOWLEDGE_BASE`, `CODE`, `PROVIDER`. L113 `Context(context_type, name)`. L125 `Context.index_info_type(index)` maps an `IndexInfo` to a `ContextType`.
  - L870 `Assistant.get_deleted_context()` loads **every** `IndexInfo` in `self.project` through `IndexInfo.filter_by_projects` (models/index.py L1018, which has no repo-name filter), builds a set of `(repo_name, context_type)` pairs, and returns the **names** (a de-duplicated list) of contexts that are not in the set. It does not return `Context` objects, so a name that exists with a different type counts as missing and the type is lost.
  - L1232 `Assistant.by_datasource_run(datasource)` runs a JSONB `contains` query on `context` scoped to `datasource.project_name` and returns `AssistantListResponse` objects. It is the reverse lookup "which assistants use this datasource". It is only used by `GET /index/{index_id}/assistants` (routers/index.py L366–371).
- `src/codemie/service/assistant_service.py`
  - L943 `check_context(assistant)`: it returns early when `assistant.context` is falsy. Otherwise it calls `get_deleted_context()` and, if anything is missing, logs an error and raises `MissingContextException("Cannot initialize assistant, missed datasource context in system: \n - Datasource name: **X**")`.
  - L541 is the only call site, in `build_agent(...)`. It runs after the Bedrock early return (L537–539) and **before** `ToolkitService.get_tools` (L565). Bedrock assistants skip the check.
  - `git log -S` shows `check_context` and `get_deleted_context` both date from the initial commit `afc61fa12`. **EPMCDME-12384 did not introduce the fail-fast behaviour**; the ticket's "found after 12384" is correlation.
- `build_agent` callers. Every one of them hits `check_context`:
  - `src/codemie/rest_api/handlers/assistant_handlers.py` L645 (streaming `_handle_stream`: `build_agent` runs synchronously before `StreamingResponse` is returned, so the exception reaches the router instead of the stream), L1128 (`_background_generate`), L1189 (`_handle_sync`), L1290 (resume path).
  - `src/codemie/rest_api/handlers/hedged_handler.py` L485 (hedged requests).
  - `src/codemie/rest_api/a2a/server/codemie_task_manager.py` L162 (A2A).
  - `src/codemie/service/tools/assistant_factory.py` L72, `AssistantFactory.build()` with `is_subagent=True`. It logs and re-raises, so **one sub-assistant with a deleted datasource breaks the parent assistant**. It is reached from `service/assistant/assistant_engine_builder.py` L155/L195 (`create_assistant_executors`).
  - `src/codemie/service/assistant/assistant_health_check_service.py` L305. The health check builds the agent.
- `src/codemie/rest_api/routers/assistant.py`
  - L2237 `_create_assistant_error(...)` always returns `ExtendedHTTPException(code=HTTP_500_INTERNAL_SERVER_ERROR, ...)`. **This is the source of the 500.**
  - L2344 (`_ask_virtual_assistant`) and L2450 (`_ask_assistant`) both have `except MissingContextException`. Each wraps the message as "An error occurred during assistant initialization: …" with help text ("Check if the given datasource context is not deleted…"), calls `_save_error` (writes an ERROR chat-history entry), and raises the 500.
  - Endpoints that reach these: `/assistants/virtual/model` (L951), `/assistants/{assistant_id}/model` (L1037), `/assistants/slug/{assistant_slug:path}/model` (L1168 → L1209).
  - L2108: `AssistantVersionService.apply_version_to_assistant(assistant, request.version)` overlays a stored version's config, including `context`, when the chat request asks for a version.
  - **L2855 `_filter_invalid_datasources(assistant)` already exists.** It queries `IndexInfo(repo_name, index_type)` for the assistant's project with a targeted `IN` query, drops contexts whose `(name, context_type)` does not exist, and logs an info line. It is called on create (L759) and on update (L893, followed by `assistant.update()`). **Editing and re-saving an assistant that references a deleted datasource already silently removes the stale reference.** It is not called at chat time.
- `src/codemie/rest_api/routers/index.py` L419–455 `DELETE /index/{index_id}`. For provider datasources it calls `ProviderDatasourceDeletionService(...).run()`, otherwise `index.delete()`. It then removes guardrail assignments and sends a metric. **No assistant references are touched.**
- `src/codemie/rest_api/models/index.py` L1298 `IndexInfo.delete()` removes the git repo, the FAQ local clone, the ES index, and scheduler/webhook integrations, then deletes the DB row. It does nothing with assistants.
- `src/codemie/service/provider/datasource/provider_datasource_deletion_service.py` L33 `run()` deletes remotely via `ProviderDatasourceAdapter` and always runs `self.datasource.delete()` in `finally`. It does nothing with assistants.

### Downstream behaviour if a deleted context is skipped (no `check_context` raise)

`ToolkitService.add_context_tools` (src/codemie/service/tools/toolkit_service.py L1149) iterates `assistant.context`:

- **KNOWLEDGE_BASE** → `_add_kb_tools` (L1407) → `_find_index(KnowledgeBaseIndexInfo, ...)` (L1663) returns `None` with an error log, and the context is **silently skipped**.
- **PROVIDER** → `_add_provider_context_tools` (L1428) → `_find_index(ProviderIndexInfo, ...)` returns `None`, and the context is **silently skipped**.
- **CODE** → `_add_code_tools` (L1494) → `_get_code_fields` (L1627) → `_find_code_index` returns `None`, and it **raises `ToolException("Repository: X is not found. …")`**. `_add_git_related_tools` (L1563) calls `_get_code_fields` too. That `ToolException` is not a `MissingContextException`, so it lands in the router's generic `except Exception` and is **still a 500** with the generic "Retry your request" help.
- `src/codemie/service/tools/toolkit_settings_service.py` L206–215 (file-system toolkit config): for CODE contexts it calls `_find_code_index` (L302, returns `None`) and then dereferences `code_index.index_type`. **This throws `AttributeError` on a deleted CODE datasource.**
- The `has_code_context` flag (L1176, L1206) drives a warning when GIT tools are enabled without a CODE context. Dropping the deleted CODE context changes that path (warning only).

Removing or relaxing `check_context` alone therefore fixes KB and provider contexts but not CODE contexts. Those need either pre-filtering of `assistant.context` before `get_tools`, or tolerant handling in `_get_code_fields`, `_add_git_related_tools` and `toolkit_settings_service`.

### Other `assistant.context` consumers (non-init)

- `src/codemie/service/tools/tool_execution_service.py` L301–320 `_get_context_tools` uses the first context and calls `AssistantService._get_code_fields` for CODE (direct tool-invoke API). It would raise on a deleted CODE datasource.
- `src/codemie/agents/tools/platform/platform_tool.py` L212 `_transform_context(assistant.context)` exposes names only.
- `src/codemie/service/conversation_analysis/conversation_analysis_service.py` L483 reads names only.
- `src/codemie/workflows/assistant_generator/nodes/validation/utils.py` L89–125 `get_validated_context_info` already filters to contexts that exist (`IndexInfo.filter_for_user_repo_names`). This is the assistant-generator validation workflow.
- `src/codemie/service/assistant/assistant_health_check_service.py` L146–160 checks context with `IndexInfo.get_by_fields({"repo_name.keyword": name})`, which ignores project and type. It returns an `AssistantHealthCheckError(error_type="context_error")`. This is a third, inconsistent existence check.
- `src/codemie/service/assistant/assistant_version_service.py` L65/L123/L285/L331: version snapshots store `context`. `versioned('context')` takes it from the request when the field was sent, so a snapshot can hold a context that `_filter_invalid_datasources` later drops from the master record (filtering runs after `repository.update`). Stored versions keep deleted names indefinitely.

### Architecture and Layers Affected

- **API / Router**: `routers/assistant.py` (exception→HTTP mapping at L2237/L2344/L2450; `_filter_invalid_datasources` L2855; version overlay L2108), `routers/index.py` (delete endpoint L419; assistants-by-index L366).
- **Handlers**: `handlers/assistant_handlers.py`, `handlers/hedged_handler.py` (build_agent call sites; sync propagation).
- **Service**: `service/assistant_service.py` (`check_context`, `build_agent`), `service/tools/toolkit_service.py` (context→tools), `service/tools/toolkit_settings_service.py`, `service/tools/tool_execution_service.py`, `service/tools/assistant_factory.py` (sub-assistants), `service/assistant/assistant_health_check_service.py`, `service/assistant/assistant_version_service.py`, `service/provider/datasource/provider_datasource_deletion_service.py`.
- **Model / DB-Persistence**: `rest_api/models/assistant.py` (`get_deleted_context`, `by_datasource_run`, `Context`), `rest_api/models/index.py` (`IndexInfo.delete`, `filter_by_projects`, `filter_by_project_and_repo`). `context` is a JSONB column on the assistant table (queried with `.cast(JSONB).contains`).
- **Other entry points** (indirect): A2A `a2a/server/codemie_task_manager.py`.

### Integration Points

- Assistant ↔ IndexInfo are linked only by `(project, repo_name, context_type)` in the JSON column. There is no FK, so nothing cascades.
- The ES index and git repo are removed on delete, and provider datasources are also deleted remotely (AICE) through `ProviderDatasourceAdapter`.
- Guardrail assignments are already cleaned on datasource delete (`GuardrailService.remove_guardrail_assignments_for_entity`). This is precedent for delete-time cleanup of dependent references.
- Marketplace (global) assistants are reindexed in the background on update (routers/assistant.py L896+). A delete-time cleanup that mutates global assistants may need to trigger the same reindex.

### Patterns and Conventions

- Errors to clients go through `ExtendedHTTPException(code, message, details, help)` (`src/codemie/core/exceptions.py` L59). 4xx precedents in the same router include 422 for guardrails (L2420–2426).
- In-place model mutation plus `assistant.update()` for persistence (see L893–894).
- Targeted SQL `select(...).where(and_(project==, repo_name.in_(...)))` inside `Session(IndexInfo.get_engine())` (`_filter_invalid_datasources`), which beats `get_deleted_context`'s full-project scan.
- Class-method services (`AssistantService`, `ToolkitService`) with `@classmethod` helpers. Tests patch them with `unittest.mock.patch`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

`.ai-run/guides/` exists. The relevant files are:
- `.ai-run/guides/development/error-handling.md` (ExtendedHTTPException usage, status codes)
- `.ai-run/guides/api/rest-api-patterns.md`, `.ai-run/guides/api/endpoint-conventions.md`
- `.ai-run/guides/architecture/layered-architecture.md`, `.ai-run/guides/architecture/service-layer-patterns.md`
- `.ai-run/guides/data/database-patterns.md`, `.ai-run/guides/data/repository-patterns.md`
- `.ai-run/guides/testing/testing-patterns.md`, `testing-service-patterns.md`, `testing-api-patterns.md`
- `.ai-run/guides/agents/agent-tools.md`, `tool-overview.md`

No guide covers the assistant↔datasource reference lifecycle or MissingContextException.

### Architectural Decisions

- Silent filtering of invalid datasources on create/update is an established decision (the docstring at routers/assistant.py L2856–2863 says: "prevents errors when cloning or editing assistants across projects").
- Context→tool builders deliberately return `None` and skip for KB and provider contexts (log-and-continue), but raise for CODE. The asymmetry is undocumented.
- EPMCDME-12384 (`a217673f0`) added `DatasourceHealthMixin` (`src/codemie/agents/tools/datasource_health_mixin.py`, statuses FAILED / REINDEXING / STALE / OK surfaced in tool descriptions and ToolMessage status), `IndexInfo.last_reindex_triggered_at` plus a migration, the `SearchKBTool(index_info=...)` rename, and health in code tools. This is precedent for "tell the LLM or user that a datasource is degraded" instead of failing. It could host a "datasource deleted" notice, although a deleted datasource has no `IndexInfo` to build a tool from.

### Derived Conventions

- Existence is checked by `(project, repo_name, context_type)` everywhere except the health check (name only through ES-style `get_by_fields`).
- Router-level error translation: service exceptions are caught in `_ask_*` and converted to `ExtendedHTTPException`, and the error is saved to chat history through `_save_error`.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/test_assistant_service_check_context.py`: missing contexts raise `MissingContextException`, and the message format is asserted. **This asserts the current fail-fast behaviour and must change with any fix.**
- `tests/codemie/service/test_assistant_service_check_context_no_items.py` and `test_assistant_service_check_context_valid.py`: early-return and valid paths.
- `tests/codemie/service/test_assistant_service_headers.py` patches `check_context` (L42, L126).
- `tests/codemie/service/tools/test_toolkit_service.py`, `test_toolkit_service_headers.py`, `test_toolkit_service_git_datasource_warning.py`, `tests/codemie/service/test_assistant_service_add_git_tools_with_code_context.py`, `..._non_code_context.py`: cover `add_context_tools`, `_get_code_fields`, and the git-tools warning.
- `tests/codemie/rest_api/routers/test_index.py` L172/L198/L222: delete success, not-found, and no-permission (mocked; no assistant interaction).
- `tests/codemie/rest_api/routers/test_assistant.py` and siblings cover the assistant router generally.
- EPMCDME-12384 regression tests: `tests/codemie/agents/tools/kb/test_search_kb.py`, `tests/codemie/agents/tools/code/test_code_tools_health.py`, `tests/codemie/rest_api/models/test_index_health_fields.py`, `tests/codemie/triggers/actors/test_actor_datasource.py`, `tests/codemie/service/tools/test_tool_execution_search.py`.

### Testing Framework and Patterns

pytest with `unittest.mock` (`patch`, `patch.object`, `MagicMock(spec=Assistant)`), parametrized cases, and router tests with `auth_headers` fixtures, async test functions, and mocked `IndexInfo.get_by_id` / `Ability.can`. Tests are unit-level and patch the DB.

### Coverage Gaps

- `_filter_invalid_datasources` has **no tests** (grep finds only its definition and two call sites).
- No test covers the `except MissingContextException` → HTTP mapping in `_ask_assistant` / `_ask_virtual_assistant` (status code, `_save_error`).
- No test for `Assistant.get_deleted_context` or `Assistant.by_datasource_run`.
- No test for the delete endpoint's effect on assistants.
- No test for a deleted CODE context in `_get_code_fields`, or for `toolkit_settings_service` L206 with a `None` code index.
- No test for sub-assistant (`AssistantFactory.build`) failure with a missing context.

---

## 5. Configuration and Environment

### Environment Variables

None govern this behaviour. `config.TOOL_SELECTION_ENABLED` and `config.ENABLE_LANGGRAPH_AITOOLS_AGENT` sit on the same `build_agent` path but are unrelated.

### Configuration Files

None relevant.

### Feature Flags and Deployment Concerns

- No feature flag gates `check_context`.
- Delete-time cleanup would be a data mutation of the assistant JSONB `context` column, which needs no schema change. Existing assistants that already hold deleted references would still need an init-time or backfill fix, because delete-time cleanup only helps future deletes.

---

## 6. Risk Indicators

- **The 500 is hard-coded**: `_create_assistant_error` (routers/assistant.py L2237) always returns 500. A controlled 4xx needs either a separate constructor or a code parameter, and every one of its callers shares it.
- **Only fixing `check_context` is not enough for CODE datasources**: `ToolkitService._get_code_fields` (toolkit_service.py L1627) raises `ToolException`, which becomes a generic 500, and `toolkit_settings_service.py` L206–215 dereferences a `None` code index (`AttributeError`).
- **Several independent existence checks disagree**: `get_deleted_context` (full project scan, names only), `_filter_invalid_datasources` (targeted, returns Context pairs), the health check (`repo_name.keyword`, project-agnostic), and the assistant-generator `get_validated_context_info` (user-visible filter). A fix may want one helper.
- **Versions keep stale context**: `AssistantConfiguration.context` snapshots are not filtered. Chat with `request.version` (L2108) or rollback (`assistant_version_service.py` L285/L331) reintroduces deleted references even after cleanup-on-delete.
- **Sub-assistants cascade**: `AssistantFactory.build` re-raises, so a child assistant with a deleted datasource breaks its parent. Whether the parent's router maps that to 500 or to the MissingContext message depends on the exception type propagated.
- **The tool-invoke API** (`tool_execution_service._get_context_tools`) raises on a deleted CODE context through a separate code path.
- **Cleanup on delete (option B)** has to handle KB, CODE and provider types; provider deletion runs through a separate service with `finally: delete()`. Marketplace/global assistants may need reindexing, and bulk mutation of other users' assistants on delete is permission-sensitive: the deleter may not own those assistants.
- **Silent auto-drop at init (option A)** hides the problem from the owner. The AC asks for "clear actionable" messaging, so a notice (a chat thought, a response field, or a log only) must be designed. There is also the mutation question: filter in memory for the request only, or persist?
- **Controlled 4xx (option C)** keeps the assistant unusable until edited. Editing and re-saving already cleans it (`_filter_invalid_datasources` on update), so the help text could point there. It fails the "chat remains operational" half of the AC but satisfies "controlled non-500 error with guidance".
- **Existing tests assert fail-fast**: `test_assistant_service_check_context.py` must be rewritten; `test_assistant_service_headers.py` patches `check_context`.
- **The EPMCDME-12384 link is correlation, not cause**: `check_context` dates from the initial commit. Regression coverage should keep the 12384 health tests green (SearchKBTool / code tools health), but those tests do not exercise init.
- **Performance nit**: `get_deleted_context` loads every IndexInfo in the project on every chat turn.
- **`_filter_invalid_datasources` has no tests** although it is the likely reuse candidate.

---

## 7. Summary for Complexity Assessment

The 500 comes from a small, well-located chain. `AssistantService.build_agent` calls `check_context` (assistant_service.py L541/L943), which raises `MissingContextException` when `Assistant.get_deleted_context` finds a context whose `(repo_name, context_type)` no longer exists in the project. The routers `_ask_assistant` and `_ask_virtual_assistant` catch it and wrap it with `_create_assistant_error`, which hard-codes HTTP 500. The behaviour has existed since the initial commit; EPMCDME-12384 only added datasource-health surfacing in tools. The datasource delete endpoint (`routers/index.py` L419) and `ProviderDatasourceDeletionService` never touch assistants. However, a tested-nowhere helper `_filter_invalid_datasources` (routers/assistant.py L2855) already drops invalid contexts on assistant create and update, so re-saving an assistant already repairs it.

Layers touched: API/router (assistant and possibly index), service (AssistantService, ToolkitService, ToolkitSettingService, maybe ToolExecutionService and version service), and model (Assistant helpers). No schema migration is needed for any option. The file-change surface depends on the open scope decision:
- **Option A, auto-drop at init**: about 3–5 source files. Filter context before `get_tools`, move or reuse `_filter_invalid_datasources` into service or model, make the CODE paths tolerant, decide how to surface a notice.
- **Option B, cleanup on delete**: about 3–4 files (index router or a new service, provider deletion, `by_datasource_run` reuse, versions). It does not fix assistants that are already broken unless combined with A.
- **Option C, controlled 4xx**: about 2 files (router error mapping, help text pointing to edit/save).

Most combinations are about 4–7 source files plus 4–6 test files. All options follow established patterns; the only novel element is a user-visible "datasource removed" notice if A is chosen.

Test posture is mixed. `check_context` has three focused unit-test files, but they assert the fail-fast behaviour that must change. The router exception mapping, `_filter_invalid_datasources`, `get_deleted_context`, `by_datasource_run`, and the delete endpoint's effect on assistants are untested. Key risk factors for scoring:
- CODE contexts fail through a second path (`_get_code_fields` ToolException and a `toolkit_settings_service` None dereference), so relaxing `check_context` alone leaves a 500 for git datasources.
- Stored assistant versions and sub-assistants can reintroduce or cascade the failure.
- Existence checks are inconsistent across four places.
- The scope decision (A / B / C or a combination) is still open and changes the surface materially.

Overall this looks like low-to-medium complexity: a bounded, well-understood area with clear precedent, and the risk sits in the edge paths (CODE, versions, sub-assistants).
