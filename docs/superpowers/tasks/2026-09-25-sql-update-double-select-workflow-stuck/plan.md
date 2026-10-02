# SQL Update Double-SELECT + Workflow Stuck-Step Materializer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the redundant double-SELECT in `BaseModelWithSQLSupport.update()` and stop the
conversation materializer from reporting finished/aborted/interrupted/auth-required workflow runs
as still in progress when a leftover `IN_PROGRESS` step row exists.

**Architecture:** Two independent, single-file behavioral fixes. Fix 1 keeps a strong reference to
the row `session.get()` already loaded so `session.merge()` doesn't force a second SELECT; verified
with a real (in-memory SQLite) session because the existing suite fully mocks `Session` and cannot
observe query count, `StaleDataError` semantics, mutable-JSON round-trips, or the concurrent-delete
race. Fix 2 threads the run's `overall_status` into the per-step `in_progress` computation so a
step's own stale status can no longer override the run's actual state.

**Tech Stack:** Python, SQLModel/SQLAlchemy, pytest, in-memory SQLite (`sqlite:///:memory:`) as the
real-session test seam.

**Spec:** docs/superpowers/tasks/2026-09-25-sql-update-double-select-workflow-stuck/spec.md

## Global Constraints

- No schema change or migration (spec Non-goals).
- No change to `finish()` / `_cleanup_inflight_states()` in `workflow_execution_service.py` (spec Non-goals).
- No change to `BaseModelWithSQLSupport.update()` beyond the single-line reference fix — no switch to `UPDATE ... WHERE id` or `merge(load=False)` (spec Non-goals).
- Commit per task using the repository's existing convention.

## Review Focus

- A row deleted between the kept-alive `session.get()` and `session.commit()` (concurrent-delete race) must still raise `StaleDataError` and must not resurrect the row via INSERT.
- A model with a mutable JSON column (`MutableList`-backed, e.g. `Conversation.history`) must still save and read back correctly through the fixed `update()`.
- `WorkflowConfig.update()`'s own `find_by_id` pre-check must not mask or change the `StaleDataError` the base method raises when the row is deleted after that pre-check succeeds.
- A non-`IN_PROGRESS` run (`SUCCEEDED`/`FAILED`/`ABORTED`/`INTERRUPTED`/`AUTHENTICATION_REQUIRED`) with a leftover `IN_PROGRESS` step must report `in_progress: false` for that step, not just for the run overall.
- All 21 existing tests in `tests/codemie/service/test_history_materializer.py` must stay green unmodified — the new `run_status` parameter must default to a value that preserves current behavior for every caller that doesn't pass it.

---

### Task 1: Fix `BaseModelWithSQLSupport.update()` double SELECT with a real-session regression seam

**Files:**
- Modify: `src/codemie/rest_api/models/base.py:526-531`
- Test: `tests/codemie/rest_api/models/test_base_model_update_sqlite.py` (new)

**Interfaces:**
- Consumes: `BaseModelWithSQLSupport.get_engine()` (classmethod, `src/codemie/rest_api/models/base.py:373-374`) — patch this per-test-class to return an in-memory SQLite engine instead of `PostgresClient.get_engine()`.
- Produces: no signature change to `update()`; same `PostResponse(id=self.id)` return type. Task 2 reuses this task's SQLite-engine-patch technique for `WorkflowConfig`.

**Test-first: yes — `test_update_issues_exactly_one_select` and `test_update_concurrent_delete_race_still_raises` (below) both fail against the current code: the former because two SELECTs are issued per `update()` call, the latter because the discarded `session.get()` reference changes the race's failure mode.**

- [ ] **Step 1: Write the failing tests** in the new file, reusing the `_UpdateTestModel` shape already defined in `tests/codemie/rest_api/models/test_base_model_update_deleted.py` (table with a `JSONB`-backed `created_by` field via `MutableDict`/`MutableList`, per that file's existing pattern). Create tables against an in-memory engine (`create_engine("sqlite:///:memory:")`, then `SQLModel.metadata.create_all(engine, tables=[_UpdateTestModel.__table__])`), patch `_UpdateTestModel.get_engine` (classmethod) to return it. Cases:
  1. `test_update_issues_exactly_one_select` — save a row, register a `sqlalchemy.event.listens_for(engine, "before_cursor_execute")` counter, call `.update()`, assert exactly one `SELECT` was issued against the table.
  2. `test_update_raises_stale_data_error_for_deleted_row_real_session` — save a row, delete it directly via the engine, call `.update()` on the original (now-stale) in-memory object, assert `StaleDataError` and that no new row was inserted (`SELECT count(*)` still 0).
  3. `test_update_round_trips_mutable_json_column` — save a row with a JSON dict, mutate the field in place, call `.update()`, re-fetch via a fresh session, assert the mutation persisted.
  4. `test_update_concurrent_delete_race_still_raises` — save a row, load a second in-process copy, delete the row after the fixed method's `session.get()` succeeds but before its `commit()` (patch `Session.commit` on the model's session to delete the row first, then call the original), assert `StaleDataError` propagates and no INSERT occurs.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/codemie/rest_api/models/test_base_model_update_sqlite.py -v`
Expected: tests 1 and 4 FAIL (current code re-SELECTs / doesn't hold the race window open the same way); tests 2 and 3 may already pass — confirm by reading assertion failures, not just exit code.

- [ ] **Step 3: Apply the fix**

Bind the discarded `session.get()` result to `existing` at `src/codemie/rest_api/models/base.py:527-528` so the strong reference survives until `merge()`/`commit()`:

```python
existing = session.get(type(self), self.id)
if existing is None:
    raise StaleDataError(f"Record {self.id} has been deleted")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/codemie/rest_api/models/test_base_model_update_sqlite.py tests/codemie/rest_api/models/test_base_model_update_deleted.py tests/codemie/rest_api/models/test_base_model.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

---

### Task 2: Verify `WorkflowConfig.update()` preserves `StaleDataError` under the fix

**Files:**
- Test: `tests/codemie/core/workflow_models/test_workflow_config.py` (extend `TestWorkflowConfigUpdatePreservesHistory`)

**Interfaces:**
- Consumes: the SQLite-engine-patch technique from Task 1 (`create_engine("sqlite:///:memory:")` + `SQLModel.metadata.create_all(engine, tables=[WorkflowConfig.__table__])`, patch `WorkflowConfig.get_engine`).
- Produces: nothing consumed by later tasks — this task only adds verification, no production code changes (Task 1 already shipped the only production change this depends on).

**Test-first: yes — `test_update_propagates_stale_data_error_when_deleted_after_find_by_id`: create a real `WorkflowConfig` row against an in-memory SQLite engine, call `WorkflowConfig.find_by_id` (real, succeeds), delete the row directly via the engine, then call `workflow.update()` on the original in-memory object and assert `StaleDataError` propagates uncaught (not `NotFoundException` — `WorkflowConfig.update()` at `src/codemie/core/workflow_models/workflow_config.py:315-328` does not catch `StaleDataError`, only its own separate `find_by_id is None` check raises `NotFoundException`) and that no row was resurrected.**

- [ ] **Step 1: Write the failing test** as described above, added to `TestWorkflowConfigUpdatePreservesHistory` in `tests/codemie/core/workflow_models/test_workflow_config.py`. It fails today only in the sense that no such test exists yet — it does not depend on prior tests being red; it must pass equally before and after Task 1, since `WorkflowConfig` runs its own `find_by_id` check first and only then calls the base fix. Run it once with Task 1 uncommitted (temporarily) to confirm it also passes against the unfixed base method, proving the base fix doesn't change `WorkflowConfig`'s external behavior.

Run: `poetry run pytest tests/codemie/core/workflow_models/test_workflow_config.py -v -k stale_data_error`
Expected: PASS (this is a verification test, not a red/green pair — no production code changes in this task).

- [ ] **Step 2: Commit**

---

### Task 3: Thread `run_status` through the workflow history materializer

**Files:**
- Modify: `src/codemie/service/conversation/history_materializer.py:82-135, 151-195`
- Test: `tests/codemie/service/test_history_materializer.py` (extend)

**Interfaces:**
- Consumes: `WorkflowExecutionStatusEnum` (already imported, `history_materializer.py:26`); `execution.overall_status` from the `WorkflowExecution` returned by `WorkflowService.find_workflow_execution_by_id`.
- Produces: `_get_execution_thoughts(execution_id, history_index=None, run_status: Optional[WorkflowExecutionStatusEnum] = None)` — new trailing optional parameter, default `None` (not live), so every existing caller/mock keeps working unchanged.

**Test-first: yes — new cases under a `TestMaterializeExecutionReferenceRunStatus` class: for each of `SUCCEEDED`, `FAILED`, `ABORTED`, `INTERRUPTED`, `AUTHENTICATION_REQUIRED`, mock the execution with that `overall_status` and one leftover `IN_PROGRESS` state (via `_mock_state(status=WorkflowExecutionStatusEnum.IN_PROGRESS)` and `WorkflowExecutionState.get_all_by_fields` patched per the existing pattern at test_history_materializer.py:210-213), and assert the resulting thought's `in_progress is False` and the message carries `execution.output` (not blanked). These fail today because `_get_execution_thoughts` sets `in_progress` from the step's own status only.**

- [ ] **Step 1: Write the failing tests** as described above (5 cases, one per non-`IN_PROGRESS` status), plus one regression case confirming the existing `IN_PROGRESS` run + `IN_PROGRESS` step combination is unchanged (`in_progress is True`, message `""`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/test_history_materializer.py -v -k RunStatus`
Expected: FAIL — leftover step reports `in_progress: True` and/or output is blanked for finished runs.

- [ ] **Step 3: Apply the fix**

In `_get_execution_thoughts()` (`history_materializer.py:151-152, 186`), add the parameter and gate `in_progress` on both conditions:

```python
def _get_execution_thoughts(
    execution_id: str,
    history_index: Optional[int] = None,
    run_status: Optional[WorkflowExecutionStatusEnum] = None,
) -> List[dict]:
    ...
    run_is_live = run_status == WorkflowExecutionStatusEnum.IN_PROGRESS
    # in the dict comprehension, replace the "in_progress" line with:
    "in_progress": run_is_live and state.status == WorkflowExecutionStatusEnum.IN_PROGRESS,
```

At the call site `history_materializer.py:102`, pass `run_status=execution.overall_status`. In `_resolve_execution_output()` (`history_materializer.py:124-126`), drop the now-redundant `or any(...)` term so blanking depends only on `execution.overall_status == WorkflowExecutionStatusEnum.IN_PROGRESS`.

- [ ] **Step 4: Run the full materializer suite to verify everything passes**

Run: `poetry run pytest tests/codemie/service/test_history_materializer.py -v`
Expected: all 21 pre-existing tests plus the new cases PASS (26+ total).

- [ ] **Step 5: Commit**
