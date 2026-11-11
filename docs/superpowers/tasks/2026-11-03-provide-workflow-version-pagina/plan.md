# Workflow Version Pagination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Page `GET /v1/workflows/{workflow_id}/versions` with `page` / `per_page` so a call never returns the whole revision history.

**Architecture:** The history is a JSONB list on the workflow row, so the service slices `yaml_config_history` (newest first) after the row is loaded. Each returned revision carries its absolute index into the full history so it stays usable as rollback `revision_index`. The router declares bounded query params and passes them to the service.

**Tech Stack:** FastAPI, Pydantic v2, pytest with `unittest.mock`.

**Spec:** none (inline requirements); research in `technical-analysis.md` in this directory.

Commit per task using the repository's existing convention.

## Acceptance criteria

- `GET /v1/workflows/{id}/versions` accepts `page` (0-based, >= 0) and `per_page` (>= 1, <= 100); invalid values return 422.
- Omitting both returns `page=0, per_page=20`: only the newest 20 revisions.
- Body stays unwrapped with `workflow_id`, `workflow_name`, `total_revisions`, `revisions`; revisions newest-first; `total_revisions` is the count of the whole history.
- Each revision exposes `revision_index`, its absolute 0-based position in the full newest-first history; `POST .../versions/rollback` is unchanged and accepts that value.
- A page past the end returns `revisions: []` with the real `total_revisions`.
- `tests/codemie/rest_api/router_guards/baselines/openapi.json` matches the new route; the updated tests pass.

## Global Constraints

- No code comments; no feature docs added to the repo; every new file/test follows the existing license header convention (no new files expected).
- Follow `.ai-run/guides/api/rest-api-patterns.md` and `.ai-run/guides/testing/testing-api-patterns.md`.
- Pagination param style follows `routers/vendor.py:133-134` (`Query(..., ge=...)`) with 0-based `page`, as in `GET /assistants/{id}/versions`.

## Assumptions (recorded explicitly)

1. Rollback `revision_index` stays an absolute index into the full newest-first history; `rollback_to_version` and `WorkflowRollbackRequest` are not touched. Each paged revision exposes `revision_index` (page * per_page + offset). The extra field is tolerated by the SDK models (`extra="ignore"` in python; node interface is compile-time only).
2. Slicing is in the service: `WorkflowConfig.get_by_id` still loads the whole JSONB history from Postgres. Response size is bounded; DB load and unbounded `yaml_config_history` growth are out of scope.
3. Default when params are omitted is `page=0, per_page=20` (user-chosen, matches assistant versions); older SDK callers silently get only the newest 20 revisions. Max `per_page` is 100 because each revision carries a full yaml snapshot.
4. Out-of-range `page` returns an empty `revisions` list (assistant precedent), not an error.
5. SDK repo changes (python `list_versions(page, per_page)`, node `listVersions(params)` + `revision_index` typed on `WorkflowRevision`, updated docstrings and tests) are a follow-up in `/Users/bohdan_maliar/Projects/codemie-dev/codemie-sdk`, not part of this plan.

## Review Focus

- `page` beyond the last page: empty `revisions`, `total_revisions` still the overall count (Task 1 test).
- Workflow with no history (`yaml_config_history` is `None` or `[]`): empty page, total 0 (Task 1 test).
- `page=0, per_page=0`, negative `page`, `per_page=101`: 422 from the router, not a slice error (Task 2 test).
- Last partial page: `revision_index` values continue from `page * per_page` and are usable by rollback (Task 1 test).
- Omitted params through HTTP: default 20 entries only (Task 2 test).

---

### Task 1: Paged response model and service slicing

**Files:**
- Modify: `src/codemie/rest_api/models/workflow_version.py:21-28`
- Modify: `src/codemie/service/workflow/workflow_version_service.py:33-40`
- Test: `tests/codemie/service/test_workflow_version_service.py` (`TestGetVersionHistory`, lines 57-89)

**Interfaces:**
- Produces in `workflow_version.py`: constants `DEFAULT_VERSIONS_PER_PAGE = 20`, `MAX_VERSIONS_PER_PAGE = 100`; `class WorkflowRevisionEntry(YamlConfigHistory)` adding `revision_index: int`; `WorkflowVersionHistoryResponse.revisions: list[WorkflowRevisionEntry]` (other fields unchanged).
- Produces: `WorkflowVersionService.get_version_history(cls, workflow: WorkflowConfig, page: int = 0, per_page: int = DEFAULT_VERSIONS_PER_PAGE) -> WorkflowVersionHistoryResponse`.

- [ ] **Step 1: Write the failing tests** in `TestGetVersionHistory`. Add a helper building N entries with distinct yaml (`rev-0` newest). Tests:
  - `test_slices_requested_page`: 5 entries, `page=1, per_page=2` -> `[e.yaml_config for e in revisions] == ["rev-2", "rev-3"]`, `[e.revision_index ...] == [2, 3]`, `total_revisions == 5`.
  - `test_last_partial_page_keeps_absolute_indices`: 5 entries, `page=2, per_page=2` -> one revision with `revision_index == 4`.
  - `test_page_past_end_returns_empty_with_total`: `page=10, per_page=2` -> `revisions == []`, `total_revisions == 5`.
  - `test_defaults_to_first_twenty`: 25 entries, no paging args -> 20 revisions, first `revision_index == 0`, last `== 19`, `total_revisions == 25`.
  - `test_none_history_returns_empty_page`: `history=None` -> `revisions == []`, `total_revisions == 0`.
  - Update existing three tests: replace `result.revisions == [entry]` with assertions on `result.revisions[0].yaml_config` / `revision_index == 0` (subclass instances no longer equal the stored `YamlConfigHistory`).

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/codemie/service/test_workflow_version_service.py::TestGetVersionHistory -v`
Expected: FAIL (unexpected `page` argument / missing `revision_index`).

- [ ] **Step 3: Implement** the model changes and `get_version_history` as in Interfaces. Slice `history[start:start + per_page]` with `start = page * per_page`; build each `WorkflowRevisionEntry` from the stored entry's fields plus `revision_index = start + offset`; `total_revisions = len(history)`. Do not touch `rollback_to_version`.

- [ ] **Step 4: Run to verify pass**

Run: `poetry run pytest tests/codemie/service/test_workflow_version_service.py -v`
Expected: PASS (including untouched rollback tests).

- [ ] **Step 5: Commit** (repository convention).

### Task 2: Router query params, router tests, OpenAPI baseline

**Files:**
- Modify: `src/codemie/rest_api/routers/workflow.py:964-974` (`get_workflow_versions`)
- Modify: `tests/codemie/rest_api/routers/test_workflow_versions.py:55-97, 118-153, 330-336`
- Modify: `tests/codemie/rest_api/router_guards/baselines/openapi.json` (GET `.../versions` entry, ~line 80083, plus the `WorkflowVersionHistoryResponse` schema and new `WorkflowRevisionEntry` schema)

**Interfaces:**
- Consumes: `WorkflowVersionService.get_version_history(workflow, page, per_page)`, `DEFAULT_VERSIONS_PER_PAGE`, `MAX_VERSIONS_PER_PAGE` from Task 1.
- Produces: `get_workflow_versions(workflow_id: str, page: Annotated[int, Query(ge=0)] = 0, per_page: Annotated[int, Query(ge=1, le=MAX_VERSIONS_PER_PAGE)] = DEFAULT_VERSIONS_PER_PAGE, user: User = Depends(authenticate))`. `Annotated` keeps real int defaults so direct function calls in tests do not receive `Query` objects.

- [ ] **Step 1: Write the failing tests** in `test_workflow_versions.py`:
  - Change the assertion at `:84` to `assert_called_once_with(workflow, 0, 20)` for a direct call, and add `test_passes_page_and_per_page_to_service` calling with `page=2, per_page=5` and asserting `assert_called_once_with(workflow, 2, 5)`.
  - Update the other direct `get_workflow_versions(...)` calls (`:63, 81, 123, 137, 153`) only if they break; pass `page=0, per_page=20` explicitly if needed.
  - New HTTP-level tests next to `:334` (use the existing client fixture/patching style from that class, with the service/`WorkflowConfig.get_by_id`/`Ability` patched): `page=-1`, `per_page=0`, `per_page=101` each return 422; omitted params call the service with `(workflow, 0, 20)`.
  - `test_declares_version_history_response_model` stays valid; add an assertion that the route's query params are `page` and `per_page`.

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_workflow_versions.py -v`
Expected: FAIL (call shape and 422 cases).

- [ ] **Step 3: Implement** the router signature from Interfaces and forward `page`, `per_page` positionally to the service; import the constants from `codemie.rest_api.models.workflow_version` (the module already imports `WorkflowVersionHistoryResponse` from it) and `Annotated`/`Query` if not yet imported. Authorization order is unchanged.

- [ ] **Step 4: Regenerate the OpenAPI baseline** after confirming the router-guard test fails on the diff:

Run: `poetry run pytest tests/codemie/rest_api/router_guards -v` (expect FAIL on the openapi baseline), then `poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines`, then `git diff --stat tests/codemie/rest_api/router_guards/baselines` to confirm that only the versions route and the two schemas changed.

- [ ] **Step 5: Run to verify pass**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_workflow_versions.py tests/codemie/rest_api/router_guards tests/codemie/service/test_workflow_version_service.py -v`
Expected: PASS.

- [ ] **Step 6: Commit** (repository convention).

---

## Negative-constraint pass

- "No code comments": no task adds comments (Global Constraints).
- "No feature docs in repo": no doc tasks; the SDK/docs follow-up is only noted in Assumption 5.
- "SDK repo changes out of scope": no task touches the SDK repo (Assumption 5).
- "Unbounded history growth out of scope" / "whole JSONB still loaded": no schema or storage task (Assumption 2).
- Rollback `revision_index` must stay absolute: `rollback_to_version` and `WorkflowRollbackRequest` untouched; Task 1 tests pin absolute indices.
- "Always bounded; old callers get newest 20": Task 1 `test_defaults_to_first_twenty`, Task 2 omitted-params test.
