# User Remap Bugs Implementation Plan

> **For agentic workers:** implement task-by-task; steps use checkbox (`- [ ]`) syntax.

**Goal:** Remapped non-UUID users can be removed from projects, and the background workflow schema save no longer reverts a remapped `created_by`.

**Architecture:** Bug 1 relaxes the shared `ProjectAssignmentService._validate_user_id_format` so it rejects only empty or blank ids. Bug 2 turns `WorkflowService.save_workflow_schema` into a targeted `UPDATE workflows SET schema_url=... WHERE id=...` in place of a full-object `merge` of the stale in-memory `WorkflowConfig`.

**Tech Stack:** Python, FastAPI, SQLModel/SQLAlchemy (PostgreSQL), pytest + unittest.mock.

**Requirements:** inline (no spec). See Acceptance criteria below. **Research:** `technical-analysis.md` in this directory.

Commit per task using the repository's existing convention. Run tests with `poetry run pytest <path> -v`.

## Acceptance criteria

- [ ] `DELETE /v1/projects/<project>/assignment/<user_id>` succeeds for an existing user with a non-UUID id (e.g. `autotest-remap-adc036d3ea7e`).
- [ ] `DELETE /v1/projects/<project>/assignments?user_id=<user_id>` succeeds for the same id.
- [ ] Once that member is removed, project delete succeeds with no 409 `HAS_ASSIGNED_USERS`. The delete flow itself does not change.
- [ ] User-id format validation accepts any non-empty string, the same as `GET /v1/admin/users/{user_id}`. Empty or blank ids still get 400.
- [ ] The creator guard (`_reject_if_creator`) still rejects removing the project creator.
- [ ] After a remap, workflow `created_by.user_id` stays on the new id even when the background schema save finishes later. Regression unit tests prove this.
- [ ] Generic `WorkflowConfig.update` and `BaseModelWithSQLSupport.update` keep their behavior.

## Global Constraints

- Verify with backend unit tests only. The external e2e harness (PR #55) is out of scope.
- Fix the root cause. No waits or retries in tests.
- Note on the original AC 2 ("within the remap transaction"): the research shows the remap SQL updates the workflow row correctly. A later background full-row write then overwrites it. So the fix sits **outside** the remap transaction, in `save_workflow_schema`, and `user_remap_service.py` does not change.

---

### Task 1: Accept non-UUID user ids in project assignment validation

**Files:**
- Modify: `src/codemie/service/project/project_assignment_service.py:255-270`
- Test: `tests/codemie/service/project/test_project_assignment_service.py:32-56`

**Test-first: yes — with `config.ENV` patched to `"production"`, `_validate_user_id_format("autotest-remap-adc036d3ea7e")` raises today and should not; `""` and `"   "` must raise 400 `"Invalid user_id format"`.**

- [ ] **Step 1:** In `TestProjectAssignmentServiceValidation`, replace `test_validate_user_id_invalid_format` (l.44-56), which asserts UUID rejection, with:
  - `test_validate_user_id_accepts_non_uuid`: ENV patched to `"production"`, id `"autotest-remap-adc036d3ea7e"`, expect no exception.
  - `test_validate_user_id_rejects_blank`: parametrized over `""` and `"   "`, ENV patched to `"production"`. Expect `code == 400`, `"Invalid user_id format"` in message and `"must not be empty"` in details.
  - Keep the valid-UUID test. Rename the class docstring to "user_id format validation".
- [ ] **Step 2:** Run `poetry run pytest tests/codemie/service/project/test_project_assignment_service.py -k validate_user_id -v`. Expect the non-UUID test to FAIL.
- [ ] **Step 3:** Change the body at l.255-270. Drop the `UUID` import and the `ENV == local` bypass (the bypass is pointless once the check is format-agnostic). Raise `ExtendedHTTPException(code=400, message="Invalid user_id format", details="user_id must not be empty")` when `not user_id or not user_id.strip()`. Update the docstring. Leave all 5 call sites and `_reject_if_creator` as they are.
- [ ] **Step 4:** Re-run the full test file. Expect PASS. If other tests in the file relied on UUID rejection, update them to the new contract.

### Task 2: Background schema save writes only `schema_url`

**Files:**
- Modify: `src/codemie/service/workflow_service.py:800-815` (`save_workflow_schema`)
- Test: `tests/codemie/service/test_workflow_service.py` (new tests, plus revising l.1318-1333, ~1487-1499 and ~1526-1542)

**Interfaces:**
- Produces: `WorkflowService.save_workflow_schema(workflow_config, workflow_schema)`, same signature. It no longer calls `workflow_config.update()`.

**Test-first: yes — a stale `WorkflowConfig` (created_by.user_id = old id) passed to `save_workflow_schema` must issue an UPDATE whose SET clause holds only `schema_url`, filtered by `id`, and must not call `workflow_config.update()`. Today it calls `update()`, which does a full-row merge.**

- [ ] **Step 1:** Add regression tests next to l.1318.
  - `test_save_workflow_schema_does_not_overwrite_remapped_created_by`: build `WorkflowConfig(id="wf_1", created_by=UserEntity(user_id="old-id", ...))`. Patch `FileRepositoryFactory` as the existing tests do. Patch `get_session` in `codemie.service.workflow_service` with a MagicMock session. Assert `workflow_config.update` (stubbed with `_stub_update`) is **not** called. Capture the statement from `session.exec`/`execute` and compile it. Assert its SET parameters equal `{"schema_url": ...}`: no `created_by`, `updated_by` or `yaml_config_history`. Assert the WHERE clause targets `id == "wf_1"`. Assert `session.commit` is called once.
  - `test_save_workflow_schema_skips_write_when_schema_empty`: `b""` leads to no session use.
  - Rewrite the three existing tests that assert `mock_update.assert_called_once_with()` or `call_count == 2` so they assert the targeted update. In the `_update_workflow_values` test, `update()` is still called once, for the history.
- [ ] **Step 2:** Run `poetry run pytest tests/codemie/service/test_workflow_service.py -k schema -v`. Expect FAIL.
- [ ] **Step 3:** In `save_workflow_schema`, keep the file write and `workflow_config.schema_url = ...` (so the in-memory object stays consistent). Then replace `workflow_config.update()` with a targeted update inside `with get_session(WorkflowConfig.get_engine()) as session:`, i.e. `sa_update(WorkflowConfig).where(WorkflowConfig.id == workflow_config.id).values(schema_url=...)` followed by `commit()`. Follow the existing `sqlalchemy.update as sa_update` usage, e.g. `src/codemie/rest_api/models/index.py:39`, and import `get_session` from wherever `base.py` gets it. Keep the `try/except` logging. Do not touch `WorkflowConfig.update`, `BaseModelWithSQLSupport.update`, or the router call sites (`workflow.py:559, 698, 1051`). They all go through this method, so all three paths are fixed.
- [ ] **Step 4:** Re-run `tests/codemie/service/test_workflow_service.py` and `tests/codemie/rest_api/routers/test_workflow.py`. Expect PASS.

---

## Negative-constraint pass

- "Do not change generic `WorkflowConfig.update` / `BaseModelWithSQLSupport.update`": Task 2 changes only `save_workflow_schema`, and no task edits `base.py` or `workflow_config.py`.
- "Not adding waits or retries in tests": the Task 2 tests are deterministic statement assertions with no sleeps or polling.
- "Keep `_reject_if_creator` unchanged": Task 1 edits only `_validate_user_id_format`.
- "Not the external e2e harness": no task touches it. Verification is unit tests only.
- "Out of scope: `updated_by` and `yaml_config_history` remapping, cache invalidation": no task touches them. The remap service does not change.

## Follow-ups (out of scope)

- Remap `workflows.updated_by` and `yaml_config_history[].created_by` (both `UserEntity`).
- Call `invalidate_user_from_cache` after a remap.
- Audit other background or deferred stale full-object `merge` writers that could revert remapped columns.
- Update the external e2e test (PR #55) once these fixes ship.
