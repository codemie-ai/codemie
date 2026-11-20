DONE

## Commit
`d9e196391` — `EPMCDME-14325: Emit project_name/actor snapshot on non-project budget events (task 6)`

## Changed files (confirmed via `git show --stat HEAD`)
- `src/codemie/service/budget/budget_service.py` (21 insertions — 7 sites)
- `src/codemie/service/budget/active_project_budget_restore_service.py` (3 insertions — 1 site)
- `src/codemie/service/budget/inactive_project_budget_stop_service.py` (3 insertions — 1 site)
- `src/codemie/service/budget/inactive_cost_center_budget_stop_service.py` (3 insertions — 1 site)
- `tests/codemie/service/budget/test_budget_service_activity.py` (238 insertions — extended 5 existing tests, added 4 new test classes)
- `tests/codemie/service/budget/test_inactive_project_budget_stop_service.py` (5 insertions — extended existing test)

No other files were touched. `activity_models.py`, the alembic migration, `project_service.py`, `personal_project_service.py`, `project_assignment_service.py`, and `project_budget_service.py` were not modified.

## What was done per site (all `project_name=None`, `actor_email=None`)

1. `budget_service.py::create_budget` (BUDGET_CREATED) — `actor_display_name=actor_name`.
2. `budget_service.py::update_budget` (BUDGET_UPDATED) — `actor_display_name=actor_name`.
3. `budget_service.py::sync_budgets_from_provider` orphan-delete loop (BUDGET_DELETED) — `actor_display_name=None`.
4. `budget_service.py::assign_budget_to_user` assign branch (USER_BUDGET_ASSIGNED) — `actor_display_name=None`.
5. `budget_service.py::assign_budget_to_user` remove branch (USER_BUDGET_REMOVED) — `actor_display_name=None`.
6. `budget_service.py::_persist_bulk_budget_assignments` assign branch (USER_BUDGET_ASSIGNED) — `actor_display_name=None`.
7. `budget_service.py::_persist_bulk_budget_assignments` remove branch (USER_BUDGET_REMOVED) — `actor_display_name=None`.
8. `active_project_budget_restore_service.py::_restore_project` (PROJECT_BUDGET_RESTORED) — `actor_display_name=None`; no `actor_id=` kwarg existed and none was added.
9. `inactive_project_budget_stop_service.py::run` (PROJECT_BUDGET_STOPPED) — `actor_display_name=None`; no `actor_id=` kwarg existed and none was added.
10. `inactive_cost_center_budget_stop_service.py::run` (PROJECT_BUDGET_STOPPED) — `actor_display_name=None`.

## Test-first process
- Extended tests for sites 1, 2, 4, 5 (existing classes `TestCreateBudgetActivityEvent`, `TestUpdateBudgetActivityEvent`, `TestAssignBudgetToUserActivityEvent`) with assertions on `project_name`, `actor_email`, `actor_display_name`.
- Added new test classes for sites 3, 6/7, 8, 10 in `test_budget_service_activity.py`: `TestSyncBudgetsFromProviderOrphanDeleteActivityEvent`, `TestPersistBulkBudgetAssignmentsActivityEvent`, `TestActiveProjectBudgetRestoreActivityEvent`, `TestInactiveCostCenterBudgetStopActivityEvent`.
- Extended the existing `test_run_stops_budgets_for_inactive_projects` test in `test_inactive_project_budget_stop_service.py` for site 9.
- Ran the full extended test file before any production change: confirmed RED for exactly the two sites where the expected value is non-`None` (`test_create_budget_emits_budget_created_event` and `test_update_budget_emits_budget_updated_event`, both failing with `AssertionError: assert None == 'Admin'`). Sites where the expected value is `None` passed immediately since `ActivityEventCreate` already defaults those fields to `None` (task 1's contribution) — this is a legitimate outcome, not a skipped check, since the assertions still lock in the intended contract.
- One unrelated test-fixture issue surfaced while writing the site-3 test (`BudgetSyncResult` requires a non-null `created_at` on returned Budget rows) — fixed by adding `created_at=datetime.now(timezone.utc)` to that test's fixture; not a production code change.
- Implemented the minimal production kwargs at all 10 sites, then re-ran to confirm GREEN.

## Verification
- `poetry run pytest tests/codemie/service/budget/ -q` → `EXIT=0`, `178 passed, 2 warnings`. Log at `docs/superpowers/tasks/2026-09-17-project-scoped-activity-trail/test.local.log`.
- `poetry run ruff check <6 files>` → "All checks passed!"
- `poetry run ruff format --check <6 files>` → "6 files already formatted"
- `git show --stat HEAD` confirms exactly the 6 files listed above.

## Concerns
None. All 10 sites match the plan's global rule (`project_name=None` everywhere) and the `actor_display_name` ruling (only `create_budget`/`update_budget` pass the local `actor_name`; all other 8 sites pass `None`).
