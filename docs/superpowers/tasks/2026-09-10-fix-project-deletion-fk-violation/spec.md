# Spec: Fix Project Deletion FK Violation

**Task**: EPMCDME-13156  
**Generated**: 2026-09-10  
**Size**: M (15-20 points, 2-3 days)

---

## Problem

Project deletion fails with FK violation when soft-deleted budgets reference the project via `budgets.project_name`. The FK constraint from `budgets.project_name` to `applications.id` has no CASCADE or SET NULL action, causing the hard delete to fail.

Root cause: Migration `a9c8d7e6f5b4` created the FK without CASCADE. `ProjectService.delete_project` calls `_check_has_no_resources`, but that method only checks assistants, workflows, skills, datasources, and integrations via `get_project_entity_counts_bulk`. Budgets are not checked at all, so the validation passes and the hard delete attempt hits the FK violation.

---

## Solution

**Add budget counting to entity checks**. Extend `ApplicationRepository.get_project_entity_counts_bulk` to include `budgets_count` in its UNION ALL query, counting only active budgets (`deleted_at IS NULL`).

**Active budgets block deletion**. The existing `_check_has_no_resources` logic will automatically reject deletion when the new `budgets_count > 0`, returning 409 with the standard resource-blocking message.

**Soft-deleted budgets are unlinked**. Before project hard delete in `delete_project`, nullify `project_name` on all soft-deleted budgets that reference the project.

**Audit trail preserved**. Extend the existing `ProjectManagementEvent.PROJECT_DELETED` activity event with an `affected_budgets` attribute listing the budget IDs whose `project_name` was nullified.

No migration, no backfill, no FK constraint change.

---

## Acceptance Criteria

- Deleting a project with active budgets returns 409 with message showing budgets in the non-zero resource counts
- Deleting a project with only soft-deleted budgets succeeds and sets their `project_name` to null
- The PROJECT_DELETED activity event includes `affected_budgets: [<budget_id>, ...]` when budgets were unlinked
- Existing project deletion validation (users, assistants, workflows, resources) still applies
- All existing project deletion tests continue to pass

---

## Implementation Details

### Repository Layer

**File**: `src/codemie/repository/application_repository.py`

**Modify `get_project_entity_counts_bulk` (line 590)**:
- Add a sixth query to the UNION ALL for budgets:
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
- Add `"budgets_count": 0` to the result dict initialization (line 614)
- Include `budgets_q` in the `union_all()` call (line 670)
- Import `Budget` from `codemie.service.budget.budget_models` with other lazy imports (line 607)

This automatically enables the existing `_check_has_no_resources` validation to block deletion when active budgets exist, matching the pattern already used for assistants/workflows/skills/datasources/integrations.

### Service Layer

**File**: `src/codemie/service/project/project_service.py`

**Modify `delete_project` (line 450)**:
- After `_check_has_no_resources` validation passes (line 493), before `application_repository.delete_by_name` (line 494):
  1. Query all soft-deleted budgets: `SELECT budget_id FROM budgets WHERE project_name = ? AND deleted_at IS NOT NULL`
  2. For each budget, `UPDATE budgets SET project_name = NULL WHERE budget_id = ?`
  3. Collect budget IDs into `affected_budgets` list
- Extend PROJECT_DELETED event `attributes` dict (line 495-503) with `affected_budgets` field containing the list

**Async/Sync Coordination**:
- `BudgetRepository` is async; `ProjectService.delete_project` uses sync session
- Recommendation: Use sync session directly (`session.exec()`) to query and update `budgets` table, avoiding async boundary and repository layer for this narrow nullification operation
- Query: `session.exec(select(Budget.budget_id).where(Budget.project_name == project_name, Budget.deleted_at.is_not(None)))`
- Update: Direct SQL via `session.exec(update(Budget).where(Budget.budget_id == budget_id).values(project_name=None))`

### Activity Event Extension

**File**: `src/codemie/service/project/project_service.py` (delete_project method, line 495)

Extend the `ActivityEventCreate` to include attributes:
```python
activity_event_repository.insert(
    ActivityEventCreate(
        domain=ActivityDomain.PROJECT_MANAGEMENT,
        event_type=ProjectManagementEvent.PROJECT_DELETED,
        entity_type=ActivityEntityType.PROJECT,
        entity_id=project_name,
        actor_id=actor_id,
        attributes={
            "affected_budgets": affected_budget_ids  # List[str], empty if none
        },
    ),
    session,
)
```

---

## Non-goals

- Backfilling existing soft-deleted budgets with dangling project references (handle at deletion time only)
- Modifying FK constraint via migration (keep constraint as-is)
- Creating new activity event type (reuse PROJECT_DELETED)
- Cascading project deletion to soft-delete active budgets (explicit deletion required)
- Adding UI notification or bulk budget cleanup tool (out of scope)
- Handling other tables with FK to applications (they already have CASCADE or separate logic)
- Adding budget-specific error messaging (reuse existing generic "has resources" message)

---

## Testing Requirements

### Unit Tests

**File**: `tests/codemie/service/project/test_project_service_delete_update.py`

Add test cases following existing pattern (see lines 141-219 for assistants/workflows/skills/datasources examples):
1. `test_project_with_budgets_raises_409` — verify 409 when active budgets exist (mock `budgets_count=2`)
2. `test_project_with_soft_deleted_budgets_unlinks_them` — mock soft-deleted budgets, verify nullification SQL executed
3. `test_project_delete_emits_affected_budgets_in_event` — verify PROJECT_DELETED event includes affected_budgets attribute

### Edge Cases

- Project with no budgets (active or soft-deleted) — deletes normally, affected_budgets is empty list
- Project with multiple soft-deleted budgets — all are nullified and all IDs appear in affected_budgets
- Project with mix of active and soft-deleted budgets — deletion blocked by active budgets, soft-deleted budgets not touched
- Soft-deleted budget with project_name already null — not returned by query, not included in affected_budgets

---

## Risks

**Repository layer bypass**: Direct sync SQL in service layer bypasses `BudgetRepository` abstraction. This is acceptable for the narrow nullification operation, but establishes a precedent. Alternative of converting the service to async or wrapping in `asyncio.run()` adds complexity without clear benefit for a deletion flow.

**Production data visibility**: Existing soft-deleted budgets with dangling project references remain unaddressed until their project is deleted (or until a future manual cleanup). Not a breaking issue, but an audit or reporting query might surface these orphaned references.

**Validation order dependency**: Active budget check runs in `_check_has_no_resources` (line 493) before nullification logic. This is safe because `_check_has_no_resources` raises immediately if any resource count > 0, preventing execution from reaching the nullification block.

**Entity count cache implications**: `get_project_entity_counts_bulk` is called elsewhere in the codebase. Adding `budgets_count` returns one more field to all callers. Verified that callers sum `counts.values()` generically (line 441) or iterate `non_zero` items (line 442), so adding budgets is safe and automatic.

---

## Migration Notes

No database migration required. The FK constraint remains unchanged. All changes are in application code.

---

## References

- Technical analysis: `docs/superpowers/tasks/2026-09-10-fix-project-deletion-fk-violation/technical-analysis.md`
- Jira ticket: https://jiraeu.epam.com/browse/EPMCDME-13156
- Budget model: `src/codemie/service/budget/budget_models.py:101` (Budget.project_name FK)
- Project deletion flow: `src/codemie/service/project/project_service.py:450` (delete_project)
- Entity counts method: `src/codemie/repository/application_repository.py:590` (get_project_entity_counts_bulk)
- Existing resource tests: `tests/codemie/service/project/test_project_service_delete_update.py:141-219`
- FK constraint migration: `src/external/alembic/versions/a9c8d7e6f5b4_project_shared_subbudgets.py:66`
