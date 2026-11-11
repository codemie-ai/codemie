# Technical Research

**Task**: workflow versions pagination
**Generated**: 2026-11-03
**Research path**: codegraph

---

## 1. Original Context

Provide workflow version pagination. Check codemie-sdk sibling for more context (sibling checkout at /Users/bohdan_maliar/Projects/codemie-dev/codemie-sdk; this backend repo is /Users/bohdan_maliar/Projects/codemie-dev/codemie). The user attached a GitHub PR review comment (by AntonYeromin, Collaborator) on sdk/codemie-nodejs/src/services/workflow.ts line 128 (`async listVersions(workflowId: string): Promise<WorkflowVersionHistoryResponse>` with doc comment "Get revision history for a workflow."), verbatim: "Efficiency: `listVersions` returns the full revision history, including every historical `yaml_config` snapshot, with no pagination. The sibling `list()` pages via `page` / `per_page`. A workflow edited hundreds of times will make each call fetch and deserialize its whole history in one response. Consider accepting `page` / `per_page` here too, or confirm the backend caps the history size." Goal: add pagination (page / per_page) to the workflow version (revision) history in this backend (endpoint, service, repository, response model), compatible with what the SDK sibling expects/will call. Also report how the SDK sibling (python and nodejs) currently calls the versions endpoints and what a compatible contract should look like.

---

## 2. Codebase Findings

### Existing Implementations

**Purpose A: list a workflow's revision history**
- `GET /v1/workflows/{workflow_id}/versions` - `src/codemie/rest_api/routers/workflow.py:964-974` `get_workflow_versions(workflow_id, user)`. No query params. Loads row via `_load_workflow_record` (`:956`, `WorkflowConfig.get_by_id`, 404 on KeyError, does not parse live yaml), checks `Ability(user).can(Action.READ, workflow)` (else `raise_access_denied("view")`), returns `WorkflowVersionService.get_version_history(workflow)`. `response_model=WorkflowVersionHistoryResponse`, tag `Workflow Versioning`.
- `WorkflowVersionService.get_version_history(cls, workflow)` - `src/codemie/service/workflow/workflow_version_service.py:33-40`. Takes the whole `workflow.yaml_config_history or []`, returns `revisions=list(history)`, `total_revisions=len(history)`, `workflow_name`, `workflow_id`. No slicing. Only caller: the router (and unit tests).
- `WorkflowVersionHistoryResponse` - `src/codemie/rest_api/models/workflow_version.py:24-28`: `revisions: list[YamlConfigHistory]`, `total_revisions: int`, `workflow_name: str`, `workflow_id: str`. No page/per_page fields.
- `YamlConfigHistory` - `src/codemie/core/workflow_models/workflow_config.py:64-68`: `yaml_config: str`, `date: datetime`, `created_by: Optional[UserEntity]`, `change_notes: str | None`. Each entry carries a full yaml snapshot.
- Storage: `WorkflowConfigBase.yaml_config_history` (`workflow_config.py:104`) is a JSONB column (`PydanticListType(YamlConfigHistory)`) on the `workflows` row (migration `src/external/alembic/versions/8f7d26d3ff1c_create_workflows.py:52`). There is no separate revision table and no repository class; `WorkflowConfig.get_by_id` loads the whole row including the entire history list. Order: newest first (index 0 = most recent prior revision); writers prepend (`workflow_service.py:946-956`, `workflow_version_service.py:104`). No cap or trimming of history found anywhere in `src` (grep of `yaml_config_history`: only workflow_config.py, workflow_service.py, workflow_version_service.py, workflow_config_index_service.py:252 `defer(...)` for minimal list responses).
- Rollback consumer of the same list: `rollback_to_version` - `workflow_version_service.py:43-105`, router `POST /workflows/{id}/versions/rollback` (`workflow.py:977-999`), request `WorkflowRollbackRequest.revision_index: int` (`workflow_version.py:31-33`). Index is positional into the FULL `yaml_config_history` (bounds-checked `0..len-1`, error help text points to `GET /v1/workflows/{id}/versions`).

**Purpose B: how the product already paginates sibling lists (conventions)**
- Assistant versions - `src/codemie/rest_api/routers/assistant/versions.py:39-65`: `GET /assistants/{id}/versions`, plain params `page: int = 0, per_page: int = 20` (no Query bounds), calls `AssistantVersionService.get_version_history(assistant, page, per_page)` (`assistant_version_service.py:203-224`) -> `AssistantConfiguration.get_version_history(assistant_id, page, per_page)` (`rest_api/models/assistant.py:1511-1524`, SQL `offset(page*per_page).limit(per_page)`, newest first). Response `AssistantVersionHistoryResponse` (`assistant.py:1555-1560`): `versions`, `total_versions` (from `assistant.version_count`), `assistant_name`, `assistant_id`. Closest sibling; no `has_more`/`page`/`per_page` echoed.
- Workflow list - `workflow.py:292-325`: `page: int = 0, per_page: int = 10` (0-based), delegates to `WorkflowConfigIndexService.run(...)`/`_query_postgres(page, per_page, ...)` (`workflow_config_index_service.py:236`); response `WorkflowListResponse` with `{data, pagination}`.
- Pagination metadata shapes in use: `{page, per_page, total_count, has_more}` (`service/analytics/response_formatter.py:36-42,89-101`; `service/usage_attribution/handler.py:248-256`, `has_more = start + per_page < total`); `PaginatedProjectBudgetListResponse {items,total,page,per_page}` (`rest_api/models/project_budget.py:98-102`).
- `MAX_ITEMS_PER_PAGE = 10_000` - `workflow_service.py:59` (execution listing, not versions).

**Other touched code**
- Tests: `tests/codemie/service/test_workflow_version_service.py` (`TestGetVersionHistory`, calls `get_version_history(workflow)` with MagicMock workflow, asserts `revisions == [entry]`, `total_revisions`); `tests/codemie/rest_api/routers/test_workflow_versions.py` (`TestGetWorkflowVersions` calls `get_workflow_versions(workflow_id=..., user=...)` directly, asserts `get_version_history.assert_called_once_with(workflow)` at `:84`; route/response_model check `:86-97`; HTTP-level GET at `:334`).
- `tests/codemie/rest_api/router_guards/baselines/openapi.json` (~line 80083): committed exact-text OpenAPI baseline; the `GET .../versions` entry currently lists only the `workflow_id` path parameter and `WorkflowVersionHistoryResponse` schema. Regenerated manually via `python -m tests.codemie.rest_api.router_guards.regenerate_baselines` (`tests/.../router_guards/baseline.py:29`).

### Decisions Already in the Code

- Revision identity is the positional index into the full newest-first history: `workflow_version_service.py:58-64`; `docs/superpowers/tasks/2026-08-27-epmcdme-14431-workflow-revision-api/spec.md:48-52,177` ("intentionally temporary", stale-index problem acknowledged; stable id deferred). Both SDK rollbacks document the same ("revision_index is 0-based into the revisions[] list returned by list_versions", python `services/workflow.py:306-310`, node `services/workflow.ts:133-135`).
- Pagination was explicitly deferred: spec.md:120 "No pagination (MVP)", :211 "Pagination on the versions list endpoint (MVP without pagination)"; unbounded growth of `yaml_config_history` listed as a known limitation (spec.md:185-187). SDK work was a separate repo/ticket (spec.md:207).
- Convention: pagination params are plain `page: int = 0` (0-based) and `per_page: int` function params, no `Query(ge=...)` bounds, in both assistant versions and workflow list routers; the Python SDK's `PaginationParams` enforces `page >= 0`, `per_page > 0` client-side (`codemie_sdk/models/common.py:34-42`).
- Convention: ordering newest-first for version lists (assistants: `order_by(version_number.desc())`; workflows: prepend).
- Data form mismatch: history is stored as one JSONB array inside the workflow row (not row-per-revision), so `offset/limit` SQL as used for `AssistantConfiguration` has no direct counterpart; the usual alternative is a revision table (as assistants have `AssistantConfiguration`). The row (with whole history) is loaded by `get_by_id` regardless of the page asked for.
- Error handling convention: invalid inputs raise `ExtendedHTTPException(code=400, message, details, help)` (`workflow_version_service.py:52-64`).
- Response contract convention: SDK reads this endpoint as a raw body, not wrapped in `{"data": ...}` (python `wrap_response=False`; test comment "not wrapped in data" `codemie-python/tests/test_workflow_versions.py:14`).

### Task Statements Checked Against the Code

- "`listVersions` returns the full revision history, including every historical `yaml_config` snapshot, with no pagination" - holds (`workflow_version_service.py:34-40`; node `services/workflow.ts:128-130`, python `services/workflow.py:285-298` send no query params).
- "The sibling `list()` pages via `page` / `per_page`" - holds (node `services/workflow.ts:57-78` sends `page`, `per_page` from `WorkflowListParamsSchema`; python `services/workflow.py:128-158` via `PaginationParams(page=0, per_page=10)`).
- "or confirm the backend caps the history size" - contradicted: no cap anywhere in `src` (see Existing Implementations; spec.md:186 records growth as unbounded).
- "add pagination (page / per_page) to ... endpoint, service, repository, response model" - partly unverified in shape: there is no workflow-revision repository; the history is a JSONB field of `WorkflowConfig` (the nearest analogue is the `WorkflowConfig`/`AssistantConfiguration` model classmethods).
- Keeps in place: positional `revision_index` rollback (`WorkflowRollbackRequest`, `rollback_to_version`) - consumers: router `workflow.py:983-999`, SDK `rollback()` in python and node and their tests; it indexes the full list, not a page.

### Consumers of Changed Contracts

Contract: `GET /v1/workflows/{id}/versions` response (`WorkflowVersionHistoryResponse`) and `WorkflowVersionService.get_version_history(workflow)` signature.
- `get_workflow_versions` router - `workflow.py:970-974` (only caller of the service method).
- Tests asserting the old call shape: `test_workflow_versions.py:84` (`assert_called_once_with(workflow)`); `test_workflow_version_service.py:58-89` (positional single arg); HTTP test `test_workflow_versions.py:334`.
- OpenAPI baseline `openapi.json` ~80083 (exact-text comparison; new query params/schema fields change it).
- SDK python: `list_versions(workflow_id)` `codemie-python/src/codemie_sdk/services/workflow.py:285-298` -> `GET /v1/workflows/{id}/versions`, `WorkflowVersionHistoryResponse`, `wrap_response=False`; no params. Model `models/workflow.py:182-201`: `WorkflowRevision` (`yaml_config`, `date`, `created_by: User|None`, `change_notes`), `WorkflowVersionHistoryResponse` (`workflow_id`, `workflow_name`, `total_revisions`, `revisions`), `extra="ignore"` on both (extra response fields such as `page`/`per_page` are tolerated). Tests `codemie-python/tests/test_workflow_versions.py:79-127` assert endpoint string `"/v1/workflows/wf-123/versions"` and the model.
- SDK node: `listVersions(workflowId)` `codemie-nodejs/src/services/workflow.ts:128-130` -> `this.api.get<WorkflowVersionHistoryResponse>(\`/v1/workflows/${workflowId}/versions\`)` (no query object); interface `src/models/workflow.ts:156-181` (`WorkflowRevisionCreatedBy {user_id, username, name}`, `WorkflowRevision`, `WorkflowVersionHistoryResponse {workflow_id, workflow_name, total_revisions, revisions}`); test `tests/unit/services/workflow.test.ts:493-534` asserts `apiHandler.get` called with exactly `"/v1/workflows/wf-123/versions"`. `api.get(url, queryParams)` accepts a query record (as used by `list()`).
- SDK `rollback(workflow_id, revision_index, change_notes)` (python `:300-328`, node `:136-149`) posts to `/versions/rollback` and relies on `revision_index` being an index into the list `list_versions` returned.
- Backend rollback error help text refers users to the versions list (`workflow_version_service.py:63`).
- Frontend/UI consumers of this endpoint: none found in this repo (UI is separate; spec.md:210 notes UI rollback is a separate story).

### Architecture and Layers Affected
- REST router (`rest_api/routers/workflow.py`) -> service (`service/workflow/workflow_version_service.py`) -> model (`core/workflow_models/workflow_config.py` JSONB field on `WorkflowConfig`); response model in `rest_api/models/workflow_version.py`. No repository layer exists for this feature.

### Integration Points
- SDK python and node call only `GET .../versions` and `POST .../versions/rollback`. Authorization: `Ability(user).can(Action.READ, workflow)`; auth via `authenticate`.

### Patterns and Conventions
- Class-method services (`@classmethod`), Pydantic response models, `ExtendedHTTPException` for 4xx, router-level `Depends(authenticate)`, license header on every file, no code comments (user preference).

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/api/rest-api-patterns.md`, `.ai-run/guides/api/endpoint-conventions.md`, `.ai-run/guides/data/database-optimization.md` (pagination/batching), `.ai-run/guides/testing/testing-api-patterns.md` exist and are relevant; not opened in detail for this analysis.
- Prior task docs: `docs/superpowers/tasks/2026-08-27-epmcdme-14431-workflow-revision-api/spec.md` (original contract, deferral of pagination).

### Architectural Decisions
- See "Decisions Already in the Code" (positional index; pagination deferred; unbounded history).

### Derived Conventions
- Assistant versions endpoint is the in-repo precedent for `page`/`per_page` on a versions list.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/test_workflow_version_service.py` - `TestGetVersionHistory` plus rollback tests (MagicMock workflow, `YamlConfigHistory` entries).
- `tests/codemie/rest_api/routers/test_workflow_versions.py` - router function-level tests with patched `WorkflowVersionService`, `Ability`, `WorkflowConfig.get_by_id`; route declaration tests; HTTP client tests (`:334`, `:343`).
- Router-guard OpenAPI/route-precedence baselines (`tests/codemie/rest_api/router_guards/`).
- Assistant-side pagination precedent tests: `tests/codemie/service/assistant/test_assistant_version_service.py`, `test_assistant_repository.py`.
- SDK: python `tests/test_workflow_versions.py` (DummyApi, asserts endpoint/model), node `tests/unit/services/workflow.test.ts:479-570` (jest, asserts exact URL).

### Testing Framework and Patterns
- pytest with `unittest.mock` (MagicMock/patch); SDK python pytest, node jest.

### Coverage Gaps
- No existing test of a multi-entry history, ordering, or any page slicing (all current tests use 0-1 entries).

---

## 5. Configuration and Environment

### Environment Variables
- None found for workflow history size or version pagination (searched `yaml_config_history`, `MAX_`, history limit in `workflow_service.py`).

### Configuration Files
- `tests/codemie/rest_api/router_guards/baselines/openapi.json` (committed API contract baseline).

### Feature Flags and Deployment Concerns
- None found. Response shape change on an existing route is visible to deployed SDK versions (they ignore extra fields in Python via `extra="ignore"`; node interface is compile-time only).

---

## 6. Risk Indicators

- Existing mechanism kept in place: positional `revision_index` rollback indexes the full history (see Task Statements/Consumers); SDK docs tell callers the index refers to `revisions[]` from `list_versions`.
- Speculative: if a page is returned, `revision_index` in rollback would need to stay absolute (page*per_page + offset) or SDK callers who rollback from `revisions[i]` of page 0 only keep working because 0 is newest; a page > 0 would give indices the SDK docs do not describe.
- Speculative: storage is one JSONB array on the workflow row; slicing in the service reduces response size but `WorkflowConfig.get_by_id` still loads the entire history from Postgres. Whether that satisfies the review comment's "fetch and deserialize" concern is a design question.
- Backward compatibility: current SDKs send no params; a default `per_page` smaller than the existing history would silently truncate results for existing callers (SDK python/node have `total_revisions` but do not loop). Default choice affects this (assistant versions default 20, workflow list default 10).
- `total_revisions` today equals `len(history)`; its meaning with paging is an open contract choice (assistant precedent: total count of all versions).
- Out-of-range page behaviour is unspecified by any precedent (assistant endpoint returns empty list; no bounds validation on `page`/`per_page`, so negative values reach slicing/SQL).
- Tests asserting `get_version_history.assert_called_once_with(workflow)` (`test_workflow_versions.py:84`) and the OpenAPI baseline will fail on any signature or parameter change.
- No guide-level pagination contract for non-list `{data, pagination}` responses; three metadata shapes exist in the repo.
- Unbounded growth of `yaml_config_history` stays unaddressed by pagination (spec.md:185-186).

---

## 7. Summary for Complexity Assessment

The change sits in three backend layers: the router `get_workflow_versions` (`routers/workflow.py:964`), `WorkflowVersionService.get_version_history` (`workflow_version_service.py:33`) and the response model `WorkflowVersionHistoryResponse` (`workflow_version.py:24`). There is no revision repository: history is a JSONB list on the `workflows` row, so there is no SQL offset/limit counterpart to the assistant-versions pattern (`AssistantConfiguration.get_version_history`). The closest in-repo precedent is `GET /assistants/{id}/versions` with `page: int = 0, per_page: int = 20`, newest first, `total_versions` as the overall count. Touch surface is small (3 source files, 2 test files, 1 OpenAPI baseline); the SDK siblings (python `list_versions`, node `listVersions`) currently send no query parameters and read a raw (unwrapped) body, python models use `extra="ignore"`.

Technical novelty is low; the open points are contract choices (defaults, total semantics, absolute rollback index) rather than new mechanisms. Test coverage of the existing endpoint is decent for shape and auth but has no multi-entry, ordering or paging cases, and the OpenAPI baseline is an exact-text guard that must be regenerated deliberately.

Key risks: backward compatibility with existing SDK callers that expect the full list, the positional `revision_index` contract of rollback, and the fact that the whole history is still loaded from the row. Previous spec deferred pagination explicitly and records unbounded history as a known limitation.

---

## 8. External References

- `/Users/bohdan_maliar/Projects/codemie-dev/codemie-sdk` (resolved, read). Facts:
  - Python SDK `sdk/codemie-python/src/codemie_sdk/services/workflow.py`: `list_versions(self, workflow_id: str) -> WorkflowVersionHistoryResponse` does `self._api.get(f"/v1/workflows/{workflow_id}/versions", WorkflowVersionHistoryResponse, wrap_response=False)`; docstring "containing the full revision list". `rollback(workflow_id, revision_index, change_notes=None)` POSTs `WorkflowRollbackRequest(revision_index>=0, change_notes).model_dump(exclude_none=True)` to `/v1/workflows/{id}/versions/rollback`. `list()` uses `PaginationParams(page=0, per_page=10).to_dict()` (page >= 0, per_page > 0) as `params`. Models at `models/workflow.py:182-210` (extra="ignore").
  - Node SDK `sdk/codemie-nodejs/src/services/workflow.ts:128-130`: `listVersions(workflowId: string): Promise<WorkflowVersionHistoryResponse>` -> `this.api.get(\`/v1/workflows/${workflowId}/versions\`)`; `list()` (`:57-78`) builds `queryParams = {page, per_page}` validated by `WorkflowListParamsSchema` and passes it as second arg to `api.get`. `rollback` validates with `WorkflowRollbackParamsSchema` (`schemas/workflow.ts:110`). Types `src/models/workflow.ts:156-181`.
  - Compatible contract signals from SDK side: query params named `page` (0-based) and `per_page`, body unwrapped, response keys `workflow_id`, `workflow_name`, `total_revisions`, `revisions` unchanged (extra keys tolerated by python; node interface would need fields added to type them), revisions ordered newest first with `revisions[0]` = most recent prior revision, rollback `revision_index` documented as an index into the list returned by `list_versions`.
- PR review comment (AntonYeromin) on `sdk/codemie-nodejs/src/services/workflow.ts:128`: quoted in Section 1; backend does not cap history (contradicted, Section 2).
