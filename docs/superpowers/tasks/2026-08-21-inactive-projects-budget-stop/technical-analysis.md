# Technical Research

**Task**: budget enrichment inactive cron applications
**Generated**: 2026-08-21T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Implement budget stopping logic for inactive projects. Add new table that has foreign key to applications called applications_enrichment. There should be basic columns alongside with is_active. Table would be populated by external service. Add is_active column to budget. Create cron job that once in 24 h will check for inactive projects from enrichment table and stop budget for them if exists and notify project admins that budget for project is stopped. Stopping budget means that project could not receive spent and all accessed resources spent should be redirected to personal budget.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/service/budget/budget_models.py` — SQLModel ORM: `Budget`, `UserBudgetAssignment`, `ProjectBudgetAssignment`, `ProjectMemberBudgetAssignment`, `ProjectBudgetGroup`; `Budget` has no `is_active` field currently; FK `project_name → applications.id`
- `src/codemie/service/budget/budget_service.py` — `BudgetService`; owns all budget CRUD, user assignment, provider sync, backfill
- `src/codemie/service/budget/project_budget_service.py` — `ProjectBudgetService`; project budget lifecycle (soft-delete, rebalance, reset)
- `src/codemie/service/budget/budget_enums.py` — `BudgetCategory`, `BudgetType`, `AllocationMode`, `SyncStatus` enums
- `src/codemie/service/budget/budget_resolution_service.py` — `BudgetResolutionService.resolve()` and `resolve_sync()`; when `get_project_budget_context` returns `None`, it already falls back to `_global_context(budget_category)` → `BudgetScope.GLOBAL`; `build_runtime_context` returns `None` for global scope → provider dispatch skipped → personal budget used automatically
- `src/codemie/service/budget/daily_reset_reconciliation_service.py` — `DailyBudgetResetReconciliationService.run()`; canonical pattern for async budget background service callable from scheduler
- `src/codemie/repository/budget_repository.py` — `BudgetRepository` async; CRUD for `budgets` and `user_budget_assignments` tables
- `src/codemie/repository/project_budget_repository.py` — `ProjectBudgetRepository`; `get_project_budget_context()` at line 327; both SQL paths (async/sync) filter `AND pba.deleted_at IS NULL` — need to add `AND b.is_active = TRUE` here to activate existing fallback; sync SQL path at ~line 196 in `budget_resolution_service.py` also needs same filter
- `src/codemie/repository/application_repository.py` — `ApplicationRepository`; queries `applications` table
- `src/codemie/repository/user_enrichment_repository.py` — `UserEnrichmentRepository` (sync); structural reference for new `ApplicationEnrichmentRepository`; but new repo must be **async** (follow `BudgetRepository` pattern)
- `src/codemie/core/models.py` — `Application` SQLModel (table `applications`); PK is `id = name`; has `deleted_at` soft-delete; FK `cost_center_id`
- `src/codemie/rest_api/models/user_management.py` — `UserDB` (table `users`, has `email`), `UserProject` (table `user_projects`, has `is_project_admin: bool`), `UserEnrichment` (table `user_enrichment` in schema `codemie`; structural reference for `ApplicationEnrichment`)
- `src/codemie/service/spend_tracking/scheduler.py` — `SpendTrackingScheduler`; **canonical scheduler pattern**; uses `LeaderLockContext`, `CronTrigger`, config-driven schedules
- `src/codemie/service/email_service.py` — `EmailService`; `send_email(to, subject, html_body)` via aiosmtplib SMTP
- `src/codemie/service/activity/activity_models.py` — `BudgetManagementEvent` constants; must add `PROJECT_BUDGET_STOPPED` event for audit trail
- `src/codemie/service/activity/activity_repository.py` — `activity_event_repository.async_insert()` used for every budget state change
- `src/codemie/configs/config.py` — central `Config` (Pydantic BaseSettings); `LITELLM_SPEND_COLLECTOR_ENABLED`/`SCHEDULE` pattern to replicate
- `src/codemie/rest_api/main.py` — `lifespan`; `_setup_spend_tracking_scheduler()` at line 396; entry point to register new scheduler
- `src/codemie/utils/leader_lock.py` — `LeaderLockContext`; PostgreSQL advisory lock; required for all background jobs

### Redirect-to-personal-budget: existing logic reuse

The redirect is already implemented via the resolution service's fallback chain:
1. `get_project_budget_context` returns `None` when budget not found or `is_active = FALSE`
2. `BudgetResolutionService.resolve()` calls `_global_context()` → `BudgetScope.GLOBAL`
3. `build_runtime_context()` returns `None` for `GLOBAL` scope → provider dispatch skipped
4. Caller uses personal/global budget automatically

**Only change needed**: add `AND b.is_active = TRUE` to two SQL WHERE clauses:
- `project_budget_repository.py:get_project_budget_context()` (both async SELECT and raw SQL variant)
- `budget_resolution_service.py:resolve_sync()` raw SQL (line ~194)

No logic changes to `BudgetResolutionService` itself.

### Architecture and Layers Affected

1. **ORM layer** — `Budget` model (add `is_active: bool = True`), new `ApplicationEnrichment` model
2. **Migration layer** — two new Alembic migrations under `src/external/alembic/versions/`
3. **Repository layer** — new `ApplicationEnrichmentRepository` (async); filter addition to `ProjectBudgetRepository.get_project_budget_context` + `BudgetResolutionService.resolve_sync` raw SQL
4. **Service layer** — new `InactiveProjectBudgetStopService`; extend `BudgetManagementEvent`
5. **Scheduler layer** — new job in `SpendTrackingScheduler`
6. **Config layer** — two new env vars in `Config`
7. **Application bootstrap** — `main.py` lifespan registration (if new scheduler class added)

### Integration Points

- `ProjectBudgetRepository.get_project_budget_context` — add `is_active` filter; cache cleared via `clear_budget_resolution_cache()` after stopping
- `ApplicationEnrichmentRepository` → `ApplicationEnrichment` table (greenfield, populated by external service)
- `UserProject` + `UserDB` — queried to find project admin emails
- `EmailService.send_email()` — notification delivery
- `activity_event_repository.async_insert()` — audit event for `PROJECT_BUDGET_STOPPED`
- `LeaderLockContext` — deduplication across replicas
- `APScheduler` `AsyncIOScheduler` + `CronTrigger` — daily job scheduling

### Patterns and Conventions

- SQLModel with `table=True`; async repos; partial unique indexes via `Index(..., postgresql_where=...)`; soft-delete via `deleted_at`
- `is_active: bool` on `Budget` should default `True`, not nullable, with an index (mirrors `UserDB.is_active`)
- `ApplicationEnrichment` lives in **default schema** (not `codemie` schema — that is user-specific only; only `UserEnrichment` uses it)
- Repository singleton at module bottom: `application_enrichment_repository = ApplicationEnrichmentRepository()`
- Service singleton at module bottom: `inactive_project_budget_stop_service = InactiveProjectBudgetStopService()`
- Scheduler job pattern: `_register_<job>_job()` → `scheduler.add_job(self._run_<job>, ...)` → `_run_<job>` acquires `LeaderLockContext`, calls `service.run()`
- All source files carry Apache 2.0 copyright header
- Structured log format: `component=<service> event=<snake_case>`
- Call `clear_budget_resolution_cache()` after setting `is_active = False` to invalidate TTL cache immediately

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/data/database-patterns.md` — mandates Alembic for all schema changes; migrations under `src/external/alembic/versions/`
- `.ai-run/guides/data/repository-patterns.md` — async repository access patterns
- `.ai-run/guides/architecture/service-layer-patterns.md` — service orchestration patterns
- `.ai-run/guides/development/logging-patterns.md` — structured `key=value` log format

### Architectural Decisions

No ADR files found. Conventions derived from code:
- `UserEnrichment` is the only model using `schema="codemie"` — `ApplicationEnrichment` must **not** use that schema
- `SpendTrackingScheduler` is the single locus for budget-adjacent background jobs
- Every background job must hold `LeaderLockContext` to prevent dual execution across replicas
- Redirect-to-personal-budget is achieved by making resolution return global scope (no changes to resolution logic)

### Derived Conventions

- New async repository must follow `BudgetRepository` (not `UserEnrichmentRepository` which is sync)
- `ApplicationEnrichment.is_active: bool` stored in DB, read-only for CodeMie (populated by external service)
- Notification path: query `UserProject` (filter `project_name`, `is_project_admin=True`) → join `UserDB` → `EmailService.send_email()`

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/budget/test_budget_service.py` — BudgetService unit tests
- `tests/codemie/repository/test_budget_repository.py` — BudgetRepository unit tests with `AsyncMock` sessions
- `tests/codemie/service/budget/test_project_budget_service.py` — ProjectBudgetService tests
- `tests/codemie/service/spend_tracking/test_spend_collector_service.py` — spend collector; reference for scheduler-job wrapper tests
- `tests/unit/repository/test_user_enrichment_repository.py` — UserEnrichmentRepository tests; **direct reference** for ApplicationEnrichmentRepository tests

### Testing Framework and Patterns

- pytest + pytest-asyncio; `--import-mode=importlib`; `@pytest.mark.asyncio`
- Repository tests: `AsyncMock()` session with mocked `execute().scalars().all/first()`; no live DB
- Service tests: `patch()` for repository singletons; `AsyncMock` for all async collaborators
- Helper factories: `_make_budget(**kwargs)` pattern inside test files; no shared conftest for budget domain

### Coverage Gaps

- No tests for `SpendTrackingScheduler` job wrappers — new inactive-project scheduler job runner needs tests
- No tests for `InactiveProjectBudgetStopService.run()` — new service
- `ApplicationEnrichment` model/repository: fully greenfield, no coverage
- Email notification path in budget domain: greenfield

---

## 5. Configuration and Environment

### Environment Variables

- `LITELLM_SPEND_COLLECTOR_ENABLED` / `LITELLM_SPEND_COLLECTOR_SCHEDULE` — canonical pattern to replicate
- New: `INACTIVE_PROJECT_BUDGET_STOP_ENABLED: bool = False` (off by default, safe rollout)
- New: `INACTIVE_PROJECT_BUDGET_STOP_SCHEDULE: str = "0 0 * * *"` (daily midnight UTC)
- `EMAIL_SMTP_HOST`, `EMAIL_SMTP_PORT`, `EMAIL_SMTP_USERNAME`, `EMAIL_SMTP_PASSWORD`, `EMAIL_FROM_ADDRESS`, `EMAIL_FROM_NAME`, `EMAIL_USE_TLS` — already present; used by `EmailService`

### Configuration Files

- `src/codemie/configs/config.py` — add two new fields following existing `LITELLM_*` pattern

### Feature Flags and Deployment Concerns

- New feature off by default via `INACTIVE_PROJECT_BUDGET_STOP_ENABLED = False`
- Two Alembic migrations required before deployment
- `ApplicationEnrichment` table populated solely by external service; CodeMie only reads it
- `Budget.is_active` migration must use `server_default=True` to avoid NULL failures on existing rows

---

## 6. Risk Indicators

- **Two Alembic migrations**: `add_application_enrichment_table` and `add_is_active_to_budget`; migration ordering matters; `Budget.is_active` must have `server_default='true'`
- **TTL cache invalidation**: after `Budget.is_active = False`, must call `clear_budget_resolution_cache()` to evict stale entries; otherwise redirection delayed up to 60s (acceptable but must be intentional)
- **Sync SQL in `resolve_sync`**: raw SQL string at `budget_resolution_service.py:~194` also needs `AND b.is_active = TRUE` — easy to miss when only updating the ORM path
- **Greenfield notification path**: `UserProject` join + `UserDB` email fetch + `EmailService` invocation all untested in budget context
- **`UserEnrichmentRepository` is sync**: structural reference is sync; new `ApplicationEnrichmentRepository` must be async — easy to get wrong
- **Activity event constant**: `BudgetManagementEvent` in `activity_models.py` must be extended; missing constant = silent audit gap
- **File surface**: 6–7 layers, ~12–15 files changed

---

## 7. Summary for Complexity Assessment

The task spans six architectural layers: ORM additions (Budget.is_active + new ApplicationEnrichment), two Alembic migrations, a new async repository, a new background service with email notifications, a new scheduler job, and config additions. The redirect-to-personal-budget requirement reuses the existing fallback chain in `BudgetResolutionService` — setting `Budget.is_active = False` and adding `AND b.is_active = TRUE` to two SQL WHERE clauses in `ProjectBudgetRepository` and `BudgetResolutionService.resolve_sync` is sufficient; no resolution logic changes needed. This significantly reduces complexity.

The most novel element is the admin notification path (query `UserProject` → `UserDB` → `EmailService.send_email()`), which has no budget-domain precedent. The `ApplicationEnrichment` model, repository, and scheduler job all have direct structural references in the codebase (`UserEnrichment`, `UserEnrichmentRepository`, `DailyBudgetResetReconciliationService`, `SpendTrackingScheduler`). Key risks are the two Alembic migrations (server_default handling), missing the sync SQL path when adding the `is_active` filter, and the TTL cache invalidation requirement after stopping a budget.

Test coverage for all new code is greenfield but the AsyncMock + patch pattern is consistent across the budget domain, so all new classes follow the same testing approach. Overall this is a contained medium-complexity feature with a clear implementation path.
