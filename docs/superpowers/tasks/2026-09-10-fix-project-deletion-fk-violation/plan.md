# Fix Project Deletion FK Violation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix FK violation when deleting projects by counting active budgets in resource checks and nullifying soft-deleted budget references.

**Architecture:** Extend the existing project resource counting system to include budgets, blocking deletion when active budgets exist. For soft-deleted budgets, nullify their `project_name` FK before hard-deleting the project, tracking the operation in the activity event.

**Tech Stack:** FastAPI, SQLModel, PostgreSQL, pytest

**Spec:** `docs/superpowers/tasks/2026-09-10-fix-project-deletion-fk-violation/spec.md`

## Global Constraints

- Commit per task using the repository's existing convention
- Follow repository patterns from `.ai-run/guides/`
- All database operations use sync session (ProjectService context)
- Activity events track all state transitions

---

### Task 1: Add Budget Counting to Project Resource Checks

**Test-first: yes — test_project_with_budgets_raises_409 fails with KeyError "budgets_count"**

**Files:**
- Modify: `src/codemie/repository/application_repository.py:590-670` (get_project_entity_counts_bulk)
- Test: `tests/codemie/service/project/test_project_service_delete_update.py`

**Interfaces:**
- Consumes: Budget model from `codemie.service.budget.budget_models`
- Produces: `get_project_entity_counts_bulk` returns dict with `budgets_count` field

- [ ] **Step 1: Write failing test**

Add to `test_project_service_delete_update.py` following the pattern at lines 141-219. Test `test_project_with_budgets_raises_409` mocks `get_project_entity_counts_bulk` to return `{"budgets_count": 2}` and asserts delete_project raises 409 with "budgets" in the message.

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/codemie/service/project/test_project_service_delete_update.py::test_project_with_budgets_raises_409 -v`

Expected: KeyError "budgets_count" or assertion failure on 409 response.

- [ ] **Step 3: Add budgets query to entity counts**

In `application_repository.py:607`, add lazy import: `from codemie.service.budget.budget_models import Budget`

At line 614, add `"budgets_count": 0` to result dict initialization.

After line 669 (last entity query), add budgets query to UNION ALL:
```python
budgets_q = (
    select(
        Budget.project_name.label("proj"),
        literal("budgets").label("entity_type"),
        func.count(Budget.budget_id).label("cnt"),
    )
    .where(Budget.project_name.in_(project_names), Budget.deleted_at.is_(None))
    .group_by(Budget.project_name)
)
```

Include `budgets_q` in the `union_all()` at line 670.

- [ ] **Step 4: Run test to verify pass**

Run: `pytest tests/codemie/service/project/test_project_service_delete_update.py::test_project_with_budgets_raises_409 -v`

Expected: PASS. The existing `_check_has_no_resources` logic at line 493 automatically rejects deletion when `budgets_count > 0`.

- [ ] **Step 5: Verify existing tests still pass**

Run: `pytest tests/codemie/service/project/test_project_service_delete_update.py -v`

Expected: All tests PASS. The new `budgets_count` field is summed generically with other counts.

---

### Task 2: Nullify Soft-Deleted Budget References on Project Deletion

**Test-first: yes — test_project_with_soft_deleted_budgets_unlinks_them fails with no nullification logic**

**Files:**
- Modify: `src/codemie/service/project/project_service.py:450-503` (delete_project)
- Test: `tests/codemie/service/project/test_project_service_delete_update.py`

**Interfaces:**
- Consumes: Budget model, session.exec() for sync queries
- Produces: PROJECT_DELETED event with `affected_budgets: List[str]` attribute

- [ ] **Step 1: Write failing test**

Add `test_project_with_soft_deleted_budgets_unlinks_them` and `test_project_delete_emits_affected_budgets_in_event` to `test_project_service_delete_update.py`. First test patches session to verify UPDATE query with `project_name=NULL`. Second test verifies activity event attributes include `affected_budgets` list.

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/codemie/service/project/test_project_service_delete_update.py::test_project_with_soft_deleted_budgets_unlinks_them tests/codemie/service/project/test_project_service_delete_update.py::test_project_delete_emits_affected_budgets_in_event -v`

Expected: Tests fail — no nullification logic exists.

- [ ] **Step 3: Add import and nullification logic**

At top of `project_service.py`, add to imports: `from codemie.service.budget.budget_models import Budget`

In `delete_project`, after line 493 (`_check_has_no_resources`) and before line 494 (`delete_by_name`), add:

Query soft-deleted budgets: `soft_deleted_budgets = session.exec(select(Budget.budget_id).where(Budget.project_name == project_name, Budget.deleted_at.is_not(None))).all()`

Collect IDs: `affected_budget_ids = [str(b) for b in soft_deleted_budgets]`

Nullify references: `if affected_budget_ids: session.exec(update(Budget).where(Budget.budget_id.in_(soft_deleted_budgets)).values(project_name=None))`

- [ ] **Step 4: Extend activity event attributes**

At line 495-503, modify `ActivityEventCreate` to add `attributes={"affected_budgets": affected_budget_ids}` parameter.

- [ ] **Step 5: Run tests to verify pass**

Run: `pytest tests/codemie/service/project/test_project_service_delete_update.py::test_project_with_soft_deleted_budgets_unlinks_them tests/codemie/service/project/test_project_service_delete_update.py::test_project_delete_emits_affected_budgets_in_event -v`

Expected: Both tests PASS.

- [ ] **Step 6: Run full test suite for project service**

Run: `pytest tests/codemie/service/project/ -v`

Expected: All tests PASS. Existing deletion tests continue working; new budget scenarios covered.

---

## Self-Review Checklist

**Spec coverage:**
- ✓ Active budgets block deletion (Task 1)
- ✓ Soft-deleted budgets unlinked (Task 2)
- ✓ Activity event includes affected_budgets (Task 2)
- ✓ Existing validation still applies (Task 1 extends existing checks)

**Negative constraints:**
- No migration required ✓ (no migration task)
- No FK constraint change ✓ (no migration task)
- No backfill ✓ (only nullify at deletion time in Task 2)
- No new activity event type ✓ (reuse PROJECT_DELETED in Task 2)
- No cascade to soft-delete active budgets ✓ (Task 1 blocks deletion instead)
- No budget-specific error messaging ✓ (reuse generic resource check in Task 1)

**Placeholder scan:** No TBD, TODO, or "add appropriate" placeholders present.

**Type consistency:** `affected_budgets: List[str]` used consistently across Task 2.
