# Workflow Revision API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Status (2026-09-24):** implemented on `EPMCDME-14431_workflow-revision-api-v2`. This plan was rewritten to describe the shipped code; `spec.md` is the behavioral contract. The source files named below are the reference implementation. Code is intentionally not duplicated here so the plan cannot drift from it.

**Goal:** Expose two REST API endpoints — `GET /v1/workflows/{id}/versions` and `POST /v1/workflows/{id}/versions/rollback` — backed by a new `WorkflowVersionService`, mirroring the existing assistant versioning pattern.

**Architecture:** New `WorkflowVersionService` reads `WorkflowConfig.yaml_config_history` (existing JSONB column) and performs immutable-append rollbacks. It writes the target revision's YAML back to `yaml_config`, reparses and validates it like a save, and persists it together with the prepended audit entry in a single `update()`. Two endpoints are appended to the existing workflow router and load the row without parsing the live YAML. New Pydantic models live in a dedicated `workflow_version.py` models file. The service is imported at router module top, so tests can patch it.

**Tech Stack:** FastAPI, SQLModel, Pydantic v2, pytest + `unittest.mock`

---

## Global Constraints

- Python ≥ 3.12 (the service uses a PEP 695 generic, `_run_async[T]`); Pydantic v2 model syntax; `str | None` rather than `Optional[...]` in new code (code-quality guide).
- No new DB columns or migrations; `yaml_config_history` already exists. `YamlConfigHistory` gains `change_notes: str | None = None`, which is backward compatible with existing JSONB rows.
- Auth: `Ability(user).can(Action.READ/WRITE, workflow)` + `raise_access_denied` (HTTP 401, never 403); not-found: `raise_not_found(resource_id=..., resource_type="Workflow")`.
- No workflow-`mode` guard on rollback (matches assistant rollback): rollback never changes `mode`, and autonomous execution stays blocked in `workflow_executions.py`.
- `revision_index` is 0-based into `yaml_config_history`; 0 = most recent prior revision.
- Current active `yaml_config` is **not** included in the list response.
- Rollback is synchronous. It prepends the current config to history as a new `YamlConfigHistory` entry with `created_by` = authenticated user (via `user.as_user_model()`) and the request's `change_notes`. `yaml_config` and history are persisted in one `workflow.update(yaml_config_history=...)` call.
- The restored config is validated like a save (MCP access control and stripping, marketplace validation when global, `WorkflowExecutor.validate_workflow`); on failure nothing is persisted.
- Tags on new endpoints: `["Workflow Versioning"]`.
- Lint/format after every task: `make ruff`.
- Commit messages: `EPMCDME-14431: <Description>` (`.ai-run/guides/standards/git-workflow.md`).

---

### Task 1: Response/request models and history field

**Test-first: no** for the response model (pure Pydantic). The request-model length limit is covered by `TestWorkflowRollbackRequest` in Task 3.

**Files:**
- Create: `src/codemie/rest_api/models/workflow_version.py`
- Modify: `src/codemie/core/workflow_models/workflow_config.py`

**Interfaces:**
- Produces:
  - `CHANGE_NOTES_MAX_LENGTH = 1000`
  - `WorkflowVersionHistoryResponse(revisions: list[YamlConfigHistory], total_revisions: int, workflow_name: str, workflow_id: str)`
  - `WorkflowRollbackRequest(revision_index: int, change_notes: str | None = Field(default=None, max_length=CHANGE_NOTES_MAX_LENGTH))`
  - `YamlConfigHistory.change_notes: str | None = None`

- [x] **Step 1:** Create `workflow_version.py` with the license header, `from __future__ import annotations`, and the three definitions above.
- [x] **Step 2:** Add `change_notes: str | None = None` to `YamlConfigHistory`.
- [x] **Step 3:** `make ruff`
- [x] **Step 4:** Commit.

---

### Task 2: WorkflowVersionService

**Test-first: yes**

**Files:**
- Create: `src/codemie/service/workflow/__init__.py` (license header only)
- Create: `src/codemie/service/workflow/workflow_version_service.py`
- Create: `tests/codemie/service/test_workflow_version_service.py`

**Interfaces:**
- Consumes: `WorkflowVersionHistoryResponse` (Task 1); `WorkflowConfig`, `YamlConfigHistory`; `User`; `MCPServerDetails`; `ExtendedHTTPException`, `ValidationException`, `NotFoundException`; lazily imported `MCPAccessControlService`, `WorkflowMarketplaceService`, `WorkflowExecutor`.
- Produces:
  - `WORKFLOW_CONFIGURATION_ERROR = "Workflow Configuration error"`
  - `WorkflowVersionService.get_version_history(workflow) -> WorkflowVersionHistoryResponse`
  - `WorkflowVersionService.rollback_to_version(workflow, revision_index, user, change_notes: str | None = None) -> WorkflowConfig`
  - `WorkflowVersionService._collect_mcp_servers(workflow) -> list[MCPServerDetails]` (reused by the router)
  - `WorkflowVersionService._validate_restored_workflow(workflow, user) -> None`
  - `WorkflowVersionService._run_async[T](coro) -> T`

**Rollback algorithm** (see `spec.md` → Service for full detail):
1. 400 if history is empty, the index is out of range (negative or `>= N`), or the target `yaml_config` is empty or whitespace. Nothing is mutated.
2. Build the audit entry from the current `yaml_config`, `now`, the user and `change_notes`.
3. Set `yaml_config` to the target, clear `states`, `parse_execution_config()`, set `updated_by`.
4. `_validate_restored_workflow`: MCP `validate_on_save`, strip inline MCP config, marketplace `validate` when `is_global` (via `_run_async`), `WorkflowExecutor.validate_workflow`.
5. On failure: restore `yaml_config`. Re-raise `ExtendedHTTPException` / `ValidationException` / `NotFoundException`; wrap anything else as 400 `WORKFLOW_CONFIGURATION_ERROR`.
6. `workflow.update(yaml_config_history=[new_entry, *history])`; return the workflow.

- [x] **Step 1:** Create the package `__init__.py`.
- [x] **Step 2:** Write the failing tests (list below); an autouse fixture in `TestRollbackToVersion` patches `WorkflowExecutor.validate_workflow`.
- [x] **Step 3:** Run them and confirm they fail.
- [x] **Step 4:** Implement the service.
- [x] **Step 5:** `poetry run pytest tests/codemie/service/test_workflow_version_service.py -v`, all 27 pass.
- [x] **Step 6:** `make ruff`
- [x] **Step 7:** Commit.

**Tests — `tests/codemie/service/test_workflow_version_service.py` (27):**

`TestGetVersionHistory`
- `test_returns_history_response_with_correct_fields`
- `test_returns_empty_list_when_no_history`
- `test_does_not_include_current_yaml_config`

`TestRollbackToVersion`
- Pre-checks: `test_raises_400_when_history_is_empty`, `test_raises_400_when_index_out_of_range`, `test_raises_400_when_negative_index`, `test_raises_400_when_target_yaml_is_empty`, `test_raises_400_when_target_yaml_is_whitespace`, `test_empty_target_yaml_leaves_current_graph_unchanged`
- Mode (no guard): `test_rolls_back_autonomous_workflow`
- Persistence and audit: `test_calls_update_with_target_yaml_and_prepended_history`, `test_new_history_entry_has_created_by_set_to_user`, `test_new_history_entry_persists_change_notes`, `test_returns_updated_workflow`, `test_persists_yaml_and_history_in_a_single_update`
- Reparse: `test_reparses_execution_config_and_sets_updated_by`, `test_omitted_states_in_target_yaml_clears_current_states`
- Save-path validation: `test_validates_mcp_and_workflow_before_persist`, `test_validate_on_save_receives_restored_mcp_servers`, `test_validates_marketplace_when_workflow_is_global`, `test_skips_marketplace_when_workflow_is_not_global`
- Failure handling: `test_raises_400_when_parse_execution_config_fails`, `test_raises_400_when_validate_workflow_raises_valueerror`, `test_propagates_validation_exception_without_wrapping`, `test_raises_400_when_restored_yaml_is_invalid`, `test_yaml_config_restored_when_validation_fails`, `test_marketplace_validation_failure_does_not_persist`

---

### Task 3: Router endpoints

**Test-first: yes**

**Files:**
- Modify: `src/codemie/rest_api/routers/workflow.py`
- Create: `tests/codemie/rest_api/routers/test_workflow_versions.py`

**Interfaces:**
- Consumes: `WorkflowVersionService`, `WORKFLOW_CONFIGURATION_ERROR` (Task 2); `WorkflowRollbackRequest`, `WorkflowVersionHistoryResponse` (Task 1); existing `authenticate`, `Ability`, `Action`, `raise_access_denied`, `raise_not_found`, `BaseResponse`, `BackgroundTasks`, `run_in_thread_pool`, `update_workflow_schema`.
- Produces:
  - `_load_workflow_record(workflow_id) -> WorkflowConfig`: `WorkflowConfig.get_by_id` without parsing the live YAML; `KeyError` → 404, anything else propagates
  - `get_workflow_versions(workflow_id, user)` → `GET /workflows/{workflow_id}/versions`, `response_model=WorkflowVersionHistoryResponse`
  - `rollback_workflow_version(workflow_id, request, background_tasks, user)` → `POST /workflows/{workflow_id}/versions/rollback`, `response_model=BaseResponse`

**Router refactors in the same file:**
- Remove the local `WORKFLOW_CONFIGURATION_ERROR`; import it from the service module.
- `_collect_workflow_mcp_servers` delegates to `WorkflowVersionService._collect_mcp_servers`.

- [x] **Step 1:** Write the failing tests (list below). Router tests patch `WorkflowConfig.get_by_id`, `Ability` and `WorkflowVersionService` at `codemie.rest_api.routers.workflow`.
- [x] **Step 2:** Run them and confirm they fail.
- [x] **Step 3:** Add the imports at module top and append `_load_workflow_record` and both endpoints under a `# Workflow Versioning Endpoints` section header. Rollback checks WRITE (`raise_access_denied("update")`), calls `WorkflowVersionService.rollback_to_version(workflow=..., revision_index=..., user=..., change_notes=...)`, schedules `background_tasks.add_task(run_in_thread_pool, update_workflow_schema, rolled_back, user)`, and returns `BaseResponse(message=f"Workflow {workflow_id} rolled back to revision {revision_index}.")`.
- [x] **Step 4:** `poetry run pytest tests/codemie/rest_api/routers/test_workflow_versions.py -v`, all 18 pass.
- [x] **Step 5:** Regression check: `poetry run pytest tests/codemie/service/test_workflow_version_service.py tests/codemie/rest_api/routers/test_workflow_versions.py -q`, 45 pass.
- [x] **Step 6:** `make ruff`
- [x] **Step 7:** Commit.

**Tests — `tests/codemie/rest_api/routers/test_workflow_versions.py` (18):**

`TestGetWorkflowVersions`
- `test_loads_record_without_parsing_current_yaml`
- `test_returns_version_history`
- `test_declares_version_history_response_model`
- `test_declares_rollback_post_route`
- `test_raises_not_found_when_workflow_missing` (404)
- `test_non_keyerror_from_get_by_id_propagates`
- `test_raises_access_denied_when_cannot_read` (401, `Action.READ`)

`TestRollbackWorkflowVersion`
- `test_loads_record_without_parsing_current_yaml`
- `test_returns_success_response`
- `test_calls_rollback_service_with_correct_args`
- `test_raises_not_found_when_workflow_missing` (404)
- `test_non_keyerror_from_get_by_id_propagates`
- `test_raises_access_denied_when_cannot_write` (401, `Action.WRITE`)
- `test_schedules_schema_refresh_after_rollback`

`TestWorkflowRollbackRequest`
- `test_accepts_change_notes_at_max_length`
- `test_rejects_change_notes_over_max_length`

`TestWorkflowVersionRouteAuthentication` (real `TestClient`, no overrides)
- `test_get_versions_requires_authentication` (401)
- `test_rollback_requires_authentication` (401)

---

## Deferred (out of scope; shared with the save path)

- Unbounded growth of `yaml_config_history`.
- Unlocked read-modify-write on history under concurrent writes.
