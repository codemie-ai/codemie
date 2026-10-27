# Technical Research

**Task**: spending personal-page users projects
**Generated**: 2026-08-27
**Research path**: filesystem

---

## 1. Original Context

Add per-project spending rows personal spending panel table on user's personal page. Currently, user's personal page displays only spending for user's own project in personal spending panel table. Per-project spending available only on Users list page admins. This feature should extend user's personal page so personal spending table also includes per-project spending rows. This will allow users see how spending distributed across projects directly from personal page, without requiring access admin-only Users list page. Acceptance Criteria: personal spending panel on user's personal page displays per-project spending rows. Per-project spending rows use same spending data source or calculation logic as per-project spending currently available on Users list page admins. existing spending row for user's own project remains visible not removed unless explicitly replaced by equivalent per-project rows. Spending values in personal page table match corresponding per-project spending values available on Users list page admins. Users can view only spending data belongs own accessible context. table layout remains consistent existing Your personal spending panel design. Existing admin Users list page per-project spending functionality not regressed. Empty or unavailable per-project spending data handled gracefully in table. Besides that: spending returned must be sorted by: project name, type (platform -> cli -> premium)

---

## 2. Codebase Findings

### Existing Implementations

**Personal spending panel — `GET /v1/analytics/budget_usage`**
- Router: `src/codemie/rest_api/routers/analytics.py` — `get_user_budget_usage` (~line 2997)
- Service: `src/codemie/service/analytics/handlers/budget_usage_service.py` — `BudgetUsageService.get_budget_usage`
- When called without `user_id`, resolves `subject_user_id = user.id` and `subject_label = user.username`, then calls `budget_usage_service.get_budget_usage(session, subject_user_id, subject_label)`
- Returns tabular data with columns: `project_name`, `current_spending`, `budget_reset_at`, `time_until_reset`, `budget_limit`, `total` (defined in `_get_key_spending_columns()` in the service module)
- Returns one row per personal budget category assignment (platform / cli / premium_models), labelled as `username`, `username (cli)`, `username (premium)`
- Uses lazy-refresh against LiteLLM: stale threshold controlled by `BUDGET_USAGE_STALENESS_THRESHOLD_MS` (default 600 000 ms / 10 min)
- Also handles an admin path: when `user_id` is provided, resolves via `_resolve_admin_budget_subject` and gates with `_authorize_admin_budget_view`

**Per-project spending — `GET /v1/analytics/user-project-spending`**
- Router: `src/codemie/rest_api/routers/analytics.py` — `get_user_project_spending` (~line 3069)
- Service: `src/codemie/service/analytics/handlers/member_spend_service.py` — `MemberSpendService.get_user_project_spend`
- **Admin-gated**: requires caller to be global admin, maintainer, auditor, or project admin sharing at least one project with the target user (`_authorize_admin_budget_view`)
- Returns one row per shared project the user belongs to with columns: `project_name` + one column per `BudgetCategory` (`platform`, `cli`, `premium_models`) as separate spend values, plus `{category}_limit` variants
- Data source: `project_spend_tracking` table with `spend_subject_type='member_budget'` — the user's per-project member budget spend, tracked separately from personal budgets
- Excludes personal projects (`project_type='personal'`)
- Uses a separate lazy-refresh: `_ensure_fresh` calls `get_active_provider().collect_member_budget_spend()` when stale; threshold `BUDGET_MEMBER_SPEND_STALENESS_THRESHOLD_MS` (default 600 000 ms)

**Key repository queries involved**:
- Personal budget assignments: `budget_repository.get_user_category_assignments(session, user_id)` → `UserBudgetCategoryAssignment` rows
- Personal spend snapshots: `ProjectSpendTrackingRepository.get_latest_by_budget_ids(session, budget_ids, subject_label)` — scoped by `project_name = username`, `spend_subject_type='budget'`
- Member spend snapshots: `ProjectSpendTrackingRepository.get_latest_member_rows_for_user(session, user_id)` → dict keyed by `(project_name, budget_category)`, `spend_subject_type='member_budget'`
- Member allocations: `project_member_budget_assignment_repository.get_active_by_user(session, user_id)` → `ProjectMemberBudgetAssignment` rows with `allocated_max_budget`

### Architecture and Layers Affected

| Layer | Component |
|---|---|
| Router | `src/codemie/rest_api/routers/analytics.py` — `get_user_budget_usage` endpoint |
| Service | `src/codemie/service/analytics/handlers/budget_usage_service.py` — `BudgetUsageService` |
| Service | `src/codemie/service/analytics/handlers/member_spend_service.py` — `MemberSpendService` (data source) |
| Repository | `src/codemie/repository/project_spend_tracking_repository.py` — `get_latest_member_rows_for_user` already exists |
| Repository | `src/codemie/repository/project_budget_repository.py` — `get_active_by_user` already exists |
| Model | `src/codemie/service/spend_tracking/spend_models.py` — `ProjectSpendTracking` |

### Integration Points

- **LiteLLM lazy-refresh**: `BudgetUsageService._refresh_from_litellm` and `MemberSpendService._ensure_fresh` are independent refresh paths; if both are triggered in the same request, each commits to the DB internally, so they must run in separate session scopes or the session commit/expire cycle must be managed carefully
- **`project_spend_tracking` table**: shared by both paths (`spend_subject_type='budget'` for personal, `spend_subject_type='member_budget'` for per-project)
- **`Application.ProjectType.PERSONAL`**: member_spend_service intentionally excludes personal projects; this exclusion must be preserved
- **Existing admin endpoint** `GET /v1/analytics/user-project-spending`: must not be regressed; it is a separate route from `budget_usage`

### Patterns and Conventions

- `BudgetUsageService` and `MemberSpendService` follow the same lazy-refresh service pattern: load from DB → check staleness → refresh from provider → return formatted (columns, rows) tuples
- Column definitions are returned alongside rows as `list[dict]` with keys `id`, `label`, `type`, `format` — callers wrap via `ResponseFormatter.format_tabular_response`
- Row building helpers (`_build_spending_row`, `_build_budget_usage_rows`) live at module level in `budget_usage_service.py`; equivalent helpers (`_build_spend_rows`, `_build_row`) live in `member_spend_service.py`
- `handle_analytics_errors` decorator wraps all analytics endpoints for consistent error handling
- `TabularResponse` is the declared `response_model` for spending endpoints

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/api/rest-api-patterns.md` — router registration and error handling conventions
- `.ai-run/guides/architecture/service-layer-patterns.md` — service orchestration boundaries; relevant for deciding where combined logic lives
- `.ai-run/guides/data/repository-patterns.md` — repository boundary rules; reuse existing repository methods rather than adding direct queries in services
- `.ai-run/guides/testing/testing-patterns.md` — test placement under `tests/codemie/` mirroring `src/`; mock provider boundaries

### Architectural Decisions

- Personal spend is tracked under `spend_subject_type='budget'`, keyed by `project_name = username` (the user's email/identifier). This is a deliberate convention noted in `get_latest_by_budget_ids` docstring: "project_name = user_identifier".
- Member spend is tracked under `spend_subject_type='member_budget'`, keyed by `(project_name, user_id)`. Personal projects are intentionally excluded from `get_user_project_spend` — documented in the method's docstring: "Their spend is metered against the user (spend_subject_type='budget') rather than the project".
- The admin endpoint guards via `_authorize_admin_budget_view`; the personal `budget_usage` endpoint does not require admin rights when the user views their own data.

### Derived Conventions

- Service methods return `(columns: list[dict], rows: list[dict])` tuples; router endpoints wrap them in `ResponseFormatter.format_tabular_response`
- Row labels for budget_usage follow the pattern: `username` (platform), `username (cli)`, `username (premium)` — defined in `_build_budget_usage_rows` via `category_labels` dict
- `BudgetCategory` enum drives column/row enumeration in `member_spend_service`; the order is platform → cli → premium_models, matching the task's sort requirement

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/rest_api/routers/test_analytics.py` — `TestGetUserKeySpending` covers `get_user_budget_usage`; mocks `BudgetUsageService.get_budget_usage` entirely; tests tabular shape and column presence
- `tests/codemie/rest_api/routers/test_analytics_budget_usage_labels.py` — verifies `username` is used as subject_label (not email)
- `tests/codemie/rest_api/routers/test_analytics_member_spending.py` — covers `get_user_project_spending` and `get_project_member_spending`; tests authorization (admin/project-admin/plain user), 404 on missing user, pagination defaults
- `tests/codemie/service/analytics/handlers/test_budget_usage_service.py` — unit tests for `_calculate_time_until_reset`, `_build_spending_row`, `_build_budget_usage_rows`, `_collect_spend_rows`, `BudgetUsageService.get_budget_usage`
- `tests/codemie/service/analytics/handlers/test_member_spend_service.py` — unit tests for `_category_columns`, `_spend_value`, `_limit_value`, `_build_row`, `MemberSpendService`

### Testing Framework and Patterns

- pytest with `@pytest.mark.asyncio` for async service/router tests
- `unittest.mock.patch` / `AsyncMock` / `MagicMock` for patching LiteLLM fetchers, DB sessions, repository calls
- Mock session constructed as `MagicMock` with `__aenter__`/`__aexit__` as `AsyncMock` (pattern visible in both test files)
- Fixtures: `mock_user` fixture used in `TestGetUserKeySpending`; local factory functions `_admin_user()`, `_plain_user()` in member spending tests

### Coverage Gaps

- No test exercises the `get_user_budget_usage` endpoint returning combined personal + per-project rows (the new behaviour)
- No test covers sorting of per-project rows within the `budget_usage` response
- No test covers the case where `member_spend_service._ensure_fresh` runs within the same request as `BudgetUsageService.get_budget_usage` (session lifecycle interaction)
- No test for graceful handling of empty per-project member assignments in the personal panel

---

## 5. Configuration and Environment

### Environment Variables

- `BUDGET_USAGE_STALENESS_THRESHOLD_MS` — default 600 000 ms; controls when `BudgetUsageService` triggers a LiteLLM refresh for personal budgets
- `BUDGET_MEMBER_SPEND_STALENESS_THRESHOLD_MS` — default 600 000 ms; controls when `MemberSpendService` triggers a global member spend refresh
- `ENABLE_USER_MANAGEMENT` — must be True for the admin `user_id` path in `budget_usage`; not required for the self-service path
- `LITELLM_SPEND_COLLECTOR_ENABLED` — gates the spend collector trigger; relevant to whether LiteLLM refresh paths are active

### Configuration Files

- `src/codemie/configs/config.py` — all thresholds and feature flags above declared in `AppConfig` (~lines 757-762)

### Feature Flags and Deployment Concerns

- `ENABLE_USER_MANAGEMENT` does not gate the self-service `budget_usage` path; changes there are always active when the endpoint is reachable
- No migrations required: `project_spend_tracking` and `project_member_budget_assignment` tables already exist with the needed columns

---

## 6. Risk Indicators

- **Column schema mismatch**: `budget_usage_service` columns are `(project_name, current_spending, budget_reset_at, time_until_reset, budget_limit, total)`. `member_spend_service` returns columnar data `(project_name, platform, cli, premium_models, ...)`. Extending `budget_usage` requires transforming per-project columnar rows into one row per project+category to match the existing schema. The `budget_reset_at` and `time_until_reset` fields have no equivalent in `ProjectMemberBudgetAssignment`; those columns will be null for per-project rows.
- **Session lifecycle across two lazy-refresh paths**: `BudgetUsageService._refresh_from_litellm` commits via `insert_budget_entries` and then calls `session.rollback()` + re-loads. `MemberSpendService._ensure_fresh` commits via `insert_member_budget_entries`. If both run in the same `AsyncSession`, the internal commits expire all previously loaded ORM objects. The implementations should be called with separate session scopes or `_ensure_fresh` must complete before any budget_usage load. This is the highest complexity risk.
- **No budget_reset_at for member allocations**: `ProjectMemberBudgetAssignment` stores `allocated_max_budget` but no `budget_reset_at`. Per-project rows in the personal panel cannot populate `budget_reset_at` or `time_until_reset`; the UI must handle null gracefully.
- **Sorting not currently implemented**: `_build_budget_usage_rows` iterates assignments without explicit ordering. The task requires sorting combined rows by project_name then category type (platform → cli → premium). Both personal and per-project rows must be sorted; the category ordering must align with `BudgetCategory` enum order.
- **`test_analytics.py:TestGetUserKeySpending`** mocks `BudgetUsageService.get_budget_usage` entirely. If per-project rows are fetched inside the same service call, the existing mock will suppress them in tests. If fetched at the router level, the mock boundary moves; either way, existing tests will need to be updated.
- **Admin `user_id` path scope**: The task targets the user's personal page (no `user_id` parameter). Applying the same extension to the admin-viewing path is unspecified; the combined logic should isolate which path it extends.
- **Personal project exclusion**: `member_spend_service.get_user_project_spend` already excludes `project_type='personal'` projects via an `Application` table query. This exclusion must be preserved when the same logic is called from `budget_usage_service`.

---

## 7. Summary for Complexity Assessment

The task touches three layers: the analytics router (`get_user_budget_usage`), the `BudgetUsageService`, and the `MemberSpendService`. The file change surface is small (primarily `budget_usage_service.py` and `analytics.py`), with corresponding test updates in `test_analytics.py` and `test_budget_usage_service.py`. No DB migration is needed; all required tables and repository methods already exist.

The primary technical novelty is combining two independent lazy-refresh paths (`BudgetUsageService` and `MemberSpendService`) inside a single user-facing request. Each path commits to the DB internally, which means the two paths must run in separate `AsyncSession` contexts or the session must be refreshed between them. This is a concrete ordering constraint with no existing pattern to follow in this codebase. A secondary complexity is the column schema transformation: `member_spend_service` returns columnar data (one row per project, one column per category) while `budget_usage_service` uses row-per-category layout; converting between these must preserve `allocated_max_budget` as the limit and leave `budget_reset_at`/`time_until_reset` as null.

Test posture for the existing `get_user_budget_usage` endpoint is shallow: the router test mocks `BudgetUsageService.get_budget_usage` completely, so it will miss the new code path regardless of where combined logic is placed. New unit tests covering sorting (project name order, category type order), null handling for missing member allocations, and the session lifecycle interaction are required. The risk from the session commit ordering is moderate; the rest of the implementation is additive with clear precedent.

---

## 8. External References

None named by the task.
