# Implementation Summary — Inactive Projects Budget Stop

**Task ID**: EPMCDME-13960  
**Branch**: `EPMCDME-13960_inactive-projects-budget-stop`  
**Completed**: 2026-08-25  
**Flow**: sdlc-light (Stage 4 - TDD Implementation)

## Commits

| Commit SHA | Subject |
|------------|---------|
| `88d872aca` | feat(EPMCDME-13960): add ApplicationEnrichment model and migration |
| `59b079dfb` | feat(EPMCDME-13960): add ApplicationEnrichmentRepository |
| `45d89c287` | feat(EPMCDME-13960): add Budget.is_active and filter inactive budgets from resolution |
| `050d1ce3b` | feat(EPMCDME-13960): add InactiveProjectBudgetStopService and PROJECT_BUDGET_STOPPED event |
| `4d030195e` | feat(EPMCDME-13960): add InactiveProjectBudgetScheduler and wire into main.py |
| `fe2117b67` | style: apply ruff formatting to new files |

## Files Created

### Models & Migrations
- `src/codemie/core/models.py` — added `ApplicationEnrichment` class
- `src/external/alembic/versions/x1y2z3a4b5c6_add_application_enrichment_table.py`
- `src/external/alembic/versions/y2z3a4b5c6d7_add_is_active_to_budgets.py`

### Repositories
- `src/codemie/repository/application_enrichment_repository.py` — async read-only repo with `get_inactive_application_ids()`

### Services
- `src/codemie/service/budget/inactive_project_budget_stop_service.py` — stops budgets, emits audit events, notifies admins
- `src/codemie/service/budget/inactive_project_budget_scheduler.py` — APScheduler wrapper with LeaderLockContext

### Tests
- `tests/codemie/repository/test_application_enrichment_repository.py` — 4 tests
- `tests/codemie/service/budget/test_budget_is_active.py` — 4 tests
- `tests/codemie/service/budget/test_inactive_project_budget_stop_service.py` — 3 tests
- `tests/codemie/service/budget/test_inactive_project_budget_scheduler.py` — 3 tests

## Files Modified

### Models
- `src/codemie/service/budget/budget_models.py` — added `Budget.is_active` field with index

### Activity
- `src/codemie/service/activity/activity_models.py` — added `PROJECT_BUDGET_STOPPED = "budget.project_budget.stopped"`

### Repositories
- `src/codemie/repository/budget_repository.py` — added `list_active_project_budgets()`
- `src/codemie/repository/project_budget_repository.py` — added `AND b.is_active = TRUE` to 2 SQL queries

### Services
- `src/codemie/service/budget/budget_resolution_service.py` — added `AND b.is_active = TRUE` to `resolve_sync` raw SQL

### Configuration
- `src/codemie/configs/config.py` — added `INACTIVE_PROJECT_BUDGET_STOP_ENABLED` (default False) and `INACTIVE_PROJECT_BUDGET_STOP_SCHEDULE` (default "0 0 * * *")

### Application
- `src/codemie/rest_api/main.py` — added `_setup_inactive_project_budget_scheduler()` and wired into `lifespan`

## Test Results

**New Tests**: 14/14 GREEN  
**Existing Budget Tests**: 41/41 GREEN (no regressions)  
**Total Coverage**: All budget resolution, repository, and service tests pass

### Test Breakdown
- `test_application_enrichment_repository.py`: 4 PASSED
- `test_budget_is_active.py`: 4 PASSED
- `test_inactive_project_budget_stop_service.py`: 3 PASSED
- `test_inactive_project_budget_scheduler.py`: 3 PASSED

## Quality Gates

| Gate | Status | Notes |
|------|--------|-------|
| Ruff (format) | ✅ PASS | 2325 files unchanged |
| Ruff (check) | ✅ PASS | All checks passed |
| Build | ✅ PASS | codemie-0.8.0 built successfully |
| Tests (new) | ✅ PASS | 14/14 GREEN |
| Tests (existing) | ✅ PASS | 41/41 GREEN, no regressions |

## Architecture Compliance

✅ All repositories are **async** (followed `BudgetRepository` pattern, not sync `UserEnrichmentRepository`)  
✅ `LeaderLockContext` used in scheduler (lock ID `987654326`)  
✅ `ApplicationEnrichment` uses **default schema** (no `schema="codemie"`)  
✅ `Budget.is_active` has `server_default=text("true")` for existing rows  
✅ `email_service` imported **inside method** (deferred import pattern)  
✅ `clear_budget_resolution_cache()` called after stopping budgets  
✅ `INACTIVE_PROJECT_BUDGET_STOP_ENABLED` defaults to `False` (safe rollout)  
✅ Apache 2.0 headers on all new source files  
✅ All commits follow `feat(EPMCDME-13960):` format

## Key Design Decisions

1. **No budget resolution logic changes**: Added `AND b.is_active = TRUE` filter to 3 SQL WHERE clauses. When no active budget found, existing fallback chain routes spend to personal budget automatically.

2. **Separate scheduler**: Created `InactiveProjectBudgetScheduler` instead of adding to `SpendTrackingScheduler` because `_setup_spend_tracking_scheduler` has `LLM_PROXY_ENABLED` guard that would block the job.

3. **Lock ID allocation**: Used `987654326` (next in sequence after spend tracking locks 322/323/324 and leaderboard 325).

4. **Email notification**: Notifies all project admins (`UserProject.is_project_admin=True`, `UserDB.is_active=True`, non-deleted) via `email_service.send_email()`.

5. **Cache eviction**: Calls `clear_budget_resolution_cache()` after session flush to evict the 60s TTL cache so inactive budgets take effect immediately.

## Runtime Behavior

When `INACTIVE_PROJECT_BUDGET_STOP_ENABLED=True`:

1. Scheduler fires daily at midnight UTC (configurable via `INACTIVE_PROJECT_BUDGET_STOP_SCHEDULE`)
2. Acquires PostgreSQL advisory lock (only one pod runs in multi-replica deployment)
3. Queries `application_enrichment` for `is_active=False` rows
4. For each inactive project:
   - Queries all active budgets (`Budget.is_active=True`, `deleted_at IS NULL`)
   - Sets `Budget.is_active=False` on each
   - Emits `PROJECT_BUDGET_STOPPED` audit event
   - Queries project admins and sends email notification
5. Flushes session, clears budget resolution cache, logs summary
6. On next spend request, SQL filter returns no budget → fallback to personal budget

## Next Steps

Per sdlc-light flow:
- Stage 6: Validate (run full test suite, integration smoke test)
- Stage 7: Actual complexity (measure LOC, update estimates)
- Stage 8: Commit artifacts (update events.jsonl, mark plan tasks complete)
- Stage 9: Handoff (generate PR description, emit to Jira)
