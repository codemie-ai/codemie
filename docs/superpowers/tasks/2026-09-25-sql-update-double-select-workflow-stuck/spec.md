# Spec: Reduce conversation query load and correctly reflect completed workflows (backend)

**Jira**: EPMCDME-15256 (Story) — backend scope only (AC1, AC2; AC5/AC6 partial/shared as outcomes)
**Date**: 2026-09-25
**Authoritative design**: `docs/2026-09-24-sql-update-double-select.md`, `docs/2026-09-24-workflow-stuck-in-progress-steps.md`

## Problem

Production Query Insights show `SELECT conversations.* WHERE id = $1` running ~9M times, driven by
two independent backend defects:

1. `BaseModelWithSQLSupport.update()` runs the same row SELECT twice per call. SQLAlchemy's session
   holds loaded objects by weak reference; `update()` discards the `session.get()` result before
   `session.merge(self)`, so the object is GC'd and `merge()` re-SELECTs the row it just checked.
2. The conversation history materializer marks a workflow step `in_progress: true` purely from the
   step's own stored status, ignoring the run's actual `overall_status`. A run can finish
   (`SUCCEEDED`/`FAILED`/`ABORTED`/`INTERRUPTED`/`AUTHENTICATION_REQUIRED`) while leaving one step
   row stuck at `IN_PROGRESS` (root cause: `finish()` skips the cleanup `fail()`/`abort()` do — left
   as-is, see Non-goals). The UI reads that flag as "still running" and polls every 4s indefinitely,
   and the same logic blanks the finished run's answer.

## Fix 1 — `BaseModelWithSQLSupport.update()` double SELECT

**File**: `src/codemie/rest_api/models/base.py:518-531`

Bind the `session.get()` result to a local variable so the strong reference survives until
`merge()`/`commit()`, instead of discarding it inline in the `if` check:

```python
existing = session.get(type(self), self.id)
if existing is None:
    raise StaleDataError(f"Record {self.id} has been deleted")
session.merge(self)
session.commit()
```

Net effect: one SELECT per `update()` instead of two. No signature change, no behavior change to
callers — `StaleDataError` is still raised identically for deleted rows.

### Must remain true after the fix (verification scope, not implementation)

- `IndexInfo.update()` (`rest_api/models/index.py:488-492`) still converts `StaleDataError` into
  `IndexDeletedException`; `Cron.__run_resume` (`triggers/bindings/cron.py:402-414`) still catches
  it and stops cleanly — the deleted-datasource-during-watchdog-resume scenario is unchanged.
- `WorkflowConfig.update()` (`core/workflow_models/workflow_config.py:315-328`) — an override not
  named in the source doc, found via codegraph — does its own `find_by_id` existence check in a
  separate query before calling `super().update()`. Its behavior against a row deleted between its
  own check and the base method's must be explicitly verified, not assumed unaffected.
- `VirtualIdeAssistant.update()` is a no-op; unaffected.
- No model uses `Relationship()`, so `merge()` still copies only columns.
- Mutable JSON columns (e.g. `Conversation.history`, `MutableList`-backed) still save and read back
  correctly when `merge()` writes onto the object kept alive by `existing`.
- A delete occurring between `session.get()` and `session.commit()` (concurrent-delete race) still
  raises `StaleDataError` at flush and does not resurrect the row via INSERT.
- Exactly one SELECT is issued per `update()` call (the regression this fix targets).

None of the above are provable against the current test suite: `test_base_model_update_deleted.py`
and `test_base_model.py` fully mock `Session`, so neither the bug nor the fix is observable through
them. A real-database seam (in-memory SQLite is sufficient) is required to assert SELECT count and
to exercise the mutable-JSON and concurrent-delete scenarios; mocked-session tests cannot stand in
for it.

## Fix 2 — Workflow stuck-step materializer

**File**: `src/codemie/service/conversation/history_materializer.py`

### 2a. `_get_execution_thoughts()` (currently `history_materializer.py:151-195`)

Add an optional `run_status: Optional[WorkflowExecutionStatusEnum] = None` parameter. A step's
`in_progress` is true only when the run is live **and** the step itself is `IN_PROGRESS`:

```python
run_is_live = run_status == WorkflowExecutionStatusEnum.IN_PROGRESS
...
"in_progress": run_is_live and state.status == WorkflowExecutionStatusEnum.IN_PROGRESS,
```

Default `None` means "not live" — safe for any caller that doesn't pass it.

### 2b. `_materialize_execution_reference()` (`history_materializer.py:82-119`, the only caller)

Pass the run's own status through:

```python
thoughts = _get_execution_thoughts(
    execution_id, history_index=message.history_index, run_status=execution.overall_status
)
```

### 2c. `_resolve_execution_output()` (`history_materializer.py:122-135`)

Blank the answer only when the run itself is in progress; drop the now-redundant
`any(thought.get("in_progress") ...)` term since Fix 2a already makes that condition follow from
`run_status` alone:

```python
if execution.overall_status == WorkflowExecutionStatusEnum.IN_PROGRESS:
    return ""
```

### Expected behavior after the fix

| Run status | Leftover `IN_PROGRESS` step | `in_progress` | Answer |
|---|---|---|---|
| In Progress | yes (current step) | true | "" |
| Succeeded / Failed / Aborted / Interrupted / Auth required | yes | **false** | **execution.output** (auth payload for Auth required) |
| Succeeded | no | false | output |

## Acceptance criteria

- `BaseModelWithSQLSupport.update()` issues exactly one SELECT per call, verified at a real
  (in-memory SQLite is acceptable) database seam, not only through mocked-`Session` tests.
- Updating a deleted row still raises `StaleDataError` and does not INSERT it; `IndexInfo` /
  `Cron.__run_resume` / `WorkflowConfig.update()` all preserve their current behavior against a
  deleted row under the fix.
- A real-database test confirms a mutable JSON column (e.g. `Conversation.history`) round-trips
  correctly through the fixed `update()`.
- A real-database test confirms a delete occurring between the fixed method's check and its commit
  still raises `StaleDataError` and does not resurrect the row.
- For a finished, aborted, interrupted, or auth-required run with a leftover `IN_PROGRESS` step,
  `_get_execution_thoughts()` reports that step's `in_progress` as `false`, and
  `_resolve_execution_output()` returns the run's actual output (not blanked).
- For a run whose `overall_status` is `IN_PROGRESS`, behavior is unchanged: the active step's
  `in_progress` stays `true` and the answer stays blank.
- All 21 existing tests in `tests/codemie/service/test_history_materializer.py` pass unmodified.
- New test cases exist for: Succeeded+leftover step, Failed/Aborted+leftover step,
  Interrupted+leftover step, Authentication_required+leftover step (per the doc's test table).

## Non-goals

- No change to `finish()` or `_cleanup_inflight_states()` — leftover `IN_PROGRESS` steps continue to
  be written and are still only cleaned at the next pod restart via `startup_recovery.py`. This is a
  deliberate, documented decision (no call savings once Fix 2 ships; adds DB work to every
  successful run; changes what Workflow Details shows; no UI change depends on it).
- No fix to the unrelated `fail()`-then-`finish()`-overwrite behavior noted in the workflow doc's
  "Noticed, not in scope" section (`base_node.py` BadRequestError branch) — needs its own ticket.
- No new lighter polling endpoint.
- No UI/frontend change (stale-state fallback, tab-visibility/idle backoff — AC3/AC4).
- No schema change or migration — both fixes are behavioral only.
- No rewrite of `BaseModelWithSQLSupport.update()` beyond the single-line reference fix (e.g., no
  switch to a plain `UPDATE ... WHERE id` or `merge(load=False)` — the doc evaluated and rejected
  both).

## Out of scope but tracked as residual risk

- `BaseModelWithSQLSupport` has ~40 SQL model subclasses / 48 polymorphic `update()`
  implementations. Only `IndexInfo`, `WorkflowConfig`, and the no-op `VirtualIdeAssistant` are
  confirmed real overrides; the fix is a superclass change and every other subclass inherits it
  unmodified, so no other override-specific verification is required.
- Where leftover `IN_PROGRESS` steps originate is not confirmed (possible causes: `finish_state()`
  returning early on a missing row, `_direct_state_update` fallback failing, or parallel/iteration
  branches ending without reaching `finish_state()`) — Fix 2 makes the read path correct regardless
  of cause; the write-side root cause is not this task's scope.
- Production validation of the query-load drop (AC6) and live-run responsiveness (AC5) happens
  after rollout via Query Insights monitoring, not through a test added by this task.
