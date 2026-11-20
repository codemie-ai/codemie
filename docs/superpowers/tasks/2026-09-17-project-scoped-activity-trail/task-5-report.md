DONE

## Commit

`2a2bb0b8e` — "EPMCDME-14325: Emit project_name/actor snapshot on project budget events (task 5)"

## Changed files (confirmed via `git show --stat HEAD`)

- `src/codemie/service/budget/project_budget_service.py` (+33 lines)
- `tests/codemie/service/budget/test_budget_service_activity.py` (+442 lines)

No other files were touched. `activity_models.py`, the alembic migration, `project_service.py`, `personal_project_service.py`, and `project_assignment_service.py` were left untouched (verified by the stat output above containing only the two files).

## What was done per site

All 11 `ActivityEventCreate(...)` construction sites in `project_budget_service.py` now pass `project_name=`, `actor_email=None`, and `actor_display_name=` kwargs, with existing kwargs (domain, event_type, entity_type, entity_id, actor_id, attributes) left untouched:

1. `create_project_budget` (PROJECT_BUDGET_CREATED) — `project_name=data.project_name`, `actor_display_name=actor_name` (the only method with an actor display name available).
2. `update_project_budget` (PROJECT_BUDGET_UPDATED) — `project_name=_assignment.project_name if _assignment else None`, `actor_display_name=None`.
3. `rebalance_project_budget` (PROJECT_BUDGET_REBALANCED) — `project_name=assignment.project_name if assignment else None`, `actor_display_name=None`.
4. `reset_project_budget` (PROJECT_BUDGET_RESET) — `project_name=assignment.project_name`, `actor_display_name=None` (assignment is unconditional here per existing code, matching the prompt's hint).
5. `override_member_allocation` (MEMBER_ALLOCATION_OVERRIDDEN) — `project_name=assignment.project_name if assignment else None`, `actor_display_name=None`.
6. `clear_member_override` (MEMBER_ALLOCATION_OVERRIDE_CLEARED) — `project_name=assignment.project_name if assignment else None`, `actor_display_name=None`.
7. `delete_project_budget` (PROJECT_BUDGET_DELETED) — `project_name=assignment.project_name if assignment else None`, `actor_display_name=None` (this call had no pre-existing `attributes=` kwarg; none was added, only the 3 new kwargs alongside `actor_id`).
8. `create_project_budget_group` (PROJECT_BUDGET_GROUP_CREATED) — `project_name=data.project_name`, `actor_display_name=None`.
9. `update_project_budget_group` (PROJECT_BUDGET_GROUP_UPDATED) — `project_name=group.project_name`, `actor_display_name=None`.
10. `reset_project_budget_group` (PROJECT_BUDGET_GROUP_RESET) — `project_name=group.project_name`, `actor_display_name=None`.
11. `rebalance_project_budget_group` (PROJECT_BUDGET_GROUP_REBALANCED) — `project_name=group.project_name`, `actor_display_name=None`.

All local variable names used for the `project_name` source (`_assignment`, `assignment`, `group`, `data`) matched exactly what the prompt described — no discrepancies found against the actual current file.

## TDD flow

1. RED: extended the two pre-existing tests (`test_create_project_budget_emits_project_budget_created_event`, `test_delete_project_budget_emits_project_budget_deleted_event`) with new assertions on `project_name`/`actor_email`/`actor_display_name`, and added 9 new test classes (one per remaining site): `TestUpdateProjectBudgetActivityEvent`, `TestRebalanceProjectBudgetActivityEvent`, `TestResetProjectBudgetActivityEvent`, `TestOverrideMemberAllocationActivityEvent`, `TestClearMemberOverrideActivityEvent`, `TestCreateProjectBudgetGroupActivityEvent`, `TestUpdateProjectBudgetGroupActivityEvent`, `TestResetProjectBudgetGroupActivityEvent`, `TestRebalanceProjectBudgetGroupActivityEvent`. Ran the suite before touching production code: 11 failed / 10 passed, all failing for the expected reason (`project_name`/`actor_display_name` were `None` because the kwargs did not yet exist on the call sites).
2. Made the minimal production edits described above at all 11 sites.
3. GREEN: reran the suite — 21 passed, EXIT=0.
4. Ran `poetry run ruff format` on both files (reformatted the test file for line-length/whitespace only, no semantic change) and reran the suite again to confirm still green.
5. Ran the full `tests/codemie/service/budget/` directory (173 tests) to check for regressions — all passed.

## Verification commands actually run

- `poetry run pytest tests/codemie/service/budget/test_budget_service_activity.py -v > .../test.local.log 2>&1; echo "EXIT=$?"` → `EXIT=0`, `21 passed, 2 warnings`.
- `poetry run pytest tests/codemie/service/budget/ -q` → `EXIT=0`, `173 passed, 2 warnings` (no regressions across the whole budget test package).
- `poetry run ruff check src/codemie/service/budget/project_budget_service.py tests/codemie/service/budget/test_budget_service_activity.py` → `All checks passed!`
- `poetry run ruff format --check ...` → flagged the test file for formatting; ran `poetry run ruff format` to fix, then reran the activity test file (still 21 passed, EXIT=0).
- `git show --stat HEAD` → confirms exactly the two intended files changed.

## Concerns

None. All 11 sites matched the prompt's described line numbers/snippets and variable names closely enough that no ambiguity arose. `_assignment` in `update_project_budget` and `assignment` in `reset_project_budget` behaved exactly as described (the latter is fetched unconditionally via `self.get_project_budget`, so no `if assignment else None` guard was needed there, matching the prompt's explicit instruction).
