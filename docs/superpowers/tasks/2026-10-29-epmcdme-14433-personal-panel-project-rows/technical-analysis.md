# Technical Research

**Task**: budget spending analytics litellm
**Generated**: 2026-10-29
**Research path**: filesystem

---

## 1. Original Context

Ticket EPMCDME-14433 (follow-up; the user-scoped member spend refresh from this ticket is already merged on main).
Extend the personal spending panel. Backend: GET /v1/analytics/budget_usage in repos/codemie (the current repo; src/codemie/service/analytics/handlers/budget_usage_service.py get_budget_usage, src/codemie/service/analytics/handlers/member_spend_service.py get_user_project_rows). UI: repos/codemie-ui (sibling checkout at ../codemie-ui), src/pages/settings/components/SpendingCard.tsx rendering rows through TableWidget; badge component src/components/details/DetailsBadge.tsx (styling precedent: src/pages/settings/administration/projectsManagement/components/BudgetOverrideIndicator.tsx). Both repos are on branch EPMCDME-14433_personal-panel-project-rows.

Requirements:
1. "Default" label: rows of the user's default project get a DetailsBadge "Default" in the UI and are sorted to the top of the table, ABOVE the personal-budget rows. Default = effective default from resolve_membership_default (src/codemie/rest_api/security/user.py), including the fallback. When the default is the personal project (named after the user's email), the personal-budget rows get the badge. In single-row mode (SpendingCard shows a doughnut, not a table) no badge.
2a. Projects where member spend-limit enforcement is OFF (project setting read via SettingsService.get_projects_enforcing_member_spend_limits) and that have a budget: show the user's rows (today they are hidden by enforced_only=True). Current spending = the member's own spend. Percent bar ("total" column) = TOTAL project spend / project budget max (e.g. project $100, X spent $50, Y spent $0: X sees $50 and 50%, Y sees $0 and 50%).
2b. Overridden members (allocation_mode FIXED), enforcement on or off: limit and percent use the override amount with the member's own spend. Use member_effective_max_budget (src/codemie/service/budget/budget_models.py, added by EPMCDME-16529) as the single source for which limit applies.
3. Total project spend must be LIVE, like member spend today. Today it is only written as spend_subject_type='project_budget' rows in project_spend_tracking by the nightly collector (spend_collector_service._collect_provider_project_budgets -> budget_provider_adapter._load_project_key_spend_from_litellm, which loops every project budget and calls LiteLLM /key/info with the project's key from SettingsService.get_project_litellm_creds_by_alias). Needed: a provider method scoped to given projects; refresh only the user's enforcement-off projects when stale (same TTL/pattern as _refresh_user_member_spend); a per-project advisory lock so two members don't double-write; insert a row only when the total changed or a reset transition is detected, otherwise touch the existing row's spend_date (no row growth per page view — a previous attempt EPMCDME-12991 !3632 was reverted for excessive rows); synchronous settings reads off the event loop; per-project error handling (InvalidSpendSnapshotError must not break the response). Reset cutoffs (get_project_reset_cutoffs_for_projects) are derived from project_budget rows — keep that consistent.
Constraints: admin spending endpoints (/v1/analytics/user-project-spending, /v1/analytics/project-member-spending) must return exactly what they return today; don't touch unrelated code; no new DB columns/tables; don't touch repos/codemie-code. The CLI statusline matches the personal row by email and must keep working.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/rest_api/routers/analytics/spending.py` — `get_user_budget_usage` (`/budget_usage`): subject is the caller (`user.id`, `user.username`) or, with `?user_id=`, a target user via `_resolve_admin_budget_subject` (label = `target_user_db.username`). Lines ~285–338: `/user-project-spending` -> `member_spend_service.get_user_project_spend`; `/project-member-spending` -> `get_project_member_spend`.
- `src/codemie/service/analytics/handlers/budget_usage_service.py`
  - `BudgetUsageService.get_budget_usage` (L350): calls `get_user_project_rows(..., enforced_only=True, with_reset=True, user_scoped_refresh=True)`, expands via `_expand_project_rows`, then loads/refreshes personal rows; returns `personal_rows + project_rows` (personal first). Failure in project rows degrades to personal-only + rollback.
  - `_build_spending_row(label, spending)` (L150): `total = current / limit * 100`; single place computing the percent from one `total_spend` / `max_budget` pair.
  - `_expand_project_rows` (L178): sorts projects case-insensitively, one row per category with non-None `{cat}_limit`, label `"<project>" + suffix` (`""`, `" (cli)"`, `" (premium)"`).
  - `_build_budget_usage_rows` (L199): personal rows labelled `_category_label(user_label, category)`.
  - `_get_key_spending_columns` (L79): columns `project_name, current_spending, budget_reset_at, time_until_reset, budget_limit, total`.
  - Personal refresh pattern: `_needs_refresh` (`BUDGET_USAGE_STALENESS_THRESHOLD_MS`), `_collect_spend_rows` (insert on change, touch unchanged via `touch_budget_spend_dates`).
- `src/codemie/service/analytics/handlers/member_spend_service.py`
  - `get_user_project_rows` (L272): memberships via `user_project_repository.aget_by_user_id` (returns `UserProject` rows with `project_name`, `is_default`); excludes personal projects (`application_repository.aget_personal_project_names`); `enforced_only` filters with `asyncio.to_thread(SettingsService.get_projects_enforcing_member_spend_limits, ...)`; loads allocations, member rows, `get_project_reset_cutoffs_for_projects`; user-scoped refresh when `_is_stale`; `_drop_rows_before_cutoff`; `_build_spend_rows`.
  - `_limit_value(allocation)` (L79) returns `allocation.allocated_max_budget` — not `member_effective_max_budget`.
  - `_refresh_user_member_spend` (L343): the reference pattern — `try_lock_member_refresh` advisory xact lock, re-check staleness under lock, provider fetch, `get_latest_before_by_member_budget_ids`, `_is_hidden_and_unchanged` / `_is_unchanged_since` (touch) / `_snapshot_to_row` (insert, `allow_zero` on first row), `insert_member_budget_entries` + `touch_member_spend_dates`, catch-all with rollback.
  - `_snapshot_to_row` builds `member_budget` rows; catches `InvalidSpendSnapshotError` per snapshot.
  - Shared with admin endpoints: `get_user_project_spend` calls `get_user_project_rows(session, user_id)` with all defaults; `get_project_member_spend` uses `get_project_reset_cutoffs`.
- `src/codemie/service/budget/budget_models.py` L44 `member_effective_max_budget(allocation, budget, enforce_limit)`: returns `allocation.allocated_max_budget` if `enforce_limit` or `allocation_mode == FIXED`, else `budget.max_budget`. Used by `project_member_runtime_sync.py`, `project_budget_router._displayed_member_max_budget`, `budget_service.py:1476`, `project_budget_service.py:571,1470`.
- `ProjectMemberBudgetAssignment` (same file): `project_budget_id`, `allocation_mode` (`AllocationMode.EQUAL|FIXED`, `budget_enums.py`), `allocated_max_budget`, `override_budget_id`, `shared_budget_id`, `effective_budget_id`, `budget_reset_at`. Child budget ids: `"{main}:shared"`, `"{main}:user:{user_id}"`.
- `src/codemie/rest_api/security/user.py` L31 `resolve_default_project` / L49 `resolve_membership_default(memberships, email)`: stored `is_default` wins, else `email` if a member project, else `min(names)`. Callers: `routers/user.py:73`, `user_profile_router.py:83`, `idp/local.py:101`, `authentication_service.py`, `registration_service.py`.
- `src/codemie/enterprise/litellm/budget_provider_adapter.py`
  - `_load_project_key_spend_from_litellm` (L1465): loads ALL active `ProjectBudgetAssignment`+`Budget`; per row gets `key_alias` from `provider_metadata` (`provider_budget_ref` or `key_alias`), calls `SettingsService.get_project_litellm_creds_by_alias` **synchronously on the event loop**, then `asyncio.to_thread(service.get_key_spending_info, api_key)`; builds `ProjectBudgetSpendSnapshot(spend=total_spend|spend, budget_reset_at, provider_subject_id=key_hash|alias)`. Whole loop in one try; any exception returns `[]`. Sequential, no per-row isolation.
  - `collect_project_budget_spend` (L1564) wraps it; `collect_member_budget_spend_for_allocations` (L1583) is the scoped precedent added by the merged 14433 work.
- Provider protocol `src/codemie/service/budget/provider.py` (L371/380) and no-op provider `provider_registry.py` (L260/273) — each provider method is declared in both.
- `src/codemie/service/spend_tracking/spend_collector_service.py` L173 `_collect_provider_project_budgets`: nightly; project rows use `get_latest_before_by_project_budget_ids(..., spend_subject_type="project_budget")`, skip zero delta unless `is_reset_transition` (L500); `_did_budget_reset` (L523) uses `budget.budget_reset_at`/`budget_duration`.
- `src/codemie/repository/project_spend_tracking_repository.py`
  - `get_latest_project_budget_rows_by_category` (L291), `insert_project_budget_entries` (L475, upsert on `(project_name, budget_id, spend_date)` partial index `spend_subject_type='project_budget'`, commits), `touch_budget_spend_dates` (subject `budget` only), `touch_member_spend_dates` (member only, guards against newer rows), `try_lock_member_refresh` (`pg_try_advisory_xact_lock(hashtext('member_spend_refresh:<user>'))` + shared global lock), `lock_all_member_refresh`.
  - `get_project_reset_cutoffs_for_projects` (L1120): scans 90 days of `project_budget` rows ordered by `spend_date`; a cutoff is the `spend_date` of the row preceding a decrease in `budget_period_spend`.
  - No project-budget touch method and no project-level advisory lock exist today.
- `src/codemie/service/project/project_spending_service.py` L329/L374: project list and project detail spending read `project_budget` rows (`get_latest_project_budget_rows_by_category`, `get_lifetime_spend` sums `daily_spend`).
- `SettingsService` (`src/codemie/service/settings/settings.py`): `get_project_litellm_creds_by_alias` (L1117, sync `retrieve_setting`), `get_projects_enforcing_member_spend_limits` (L1156, one sync query via `Settings.get_by_project_names`).
- `ProjectBudgetAssignment` repo (`project_budget_repository.py` L165 `get_active_for_projects`) returns project budget assignments for a project set.

UI (`../codemie-ui`):
- `src/pages/settings/components/SpendingCard.tsx`: fetches `TabularMetricType.KEY_SPENDING` (`'budget_usage'`), passes `{user_id}` when `userId` prop set. `rowCount <= 1` -> InfoCard + doughnut; `> 1` -> `TableWidget` with `initialData`, `hiddenColumns={['budget_limit']}`, `customRenderColumns` only for `total` (`SpendingProgressBar`). Rows are rendered in server order. Used by `ProfilePage.tsx` (self) and admin `UserDetailsPopup.tsx` (`userId` = another user).
- `TableWidget.tsx`: `customRenderColumns[col.id]` turns a column into `DefinitionTypes.Custom` and passes the raw row; extra row fields pass through `formattedRow = {...row}`; hidden columns via `hiddenColumns`.
- `DetailsBadge.tsx`: `value`, `icon`, `filled`, `className`, `href`. `BudgetOverrideIndicator.tsx`: `DetailsBadge value="Overridden"` with `!h-[17px] !rounded-md !px-1.5 !py-0 text-[10px] !font-medium text-text-secondary` inside `ml-1 inline-flex align-middle` span with react-tooltip attributes.
- No `default_project` in `src/store` / `src/types`.

### Architecture and Layers Affected
- API router: `rest_api/routers/analytics/spending.py` (`/budget_usage`).
- Service: `BudgetUsageService`, `MemberSpendService`.
- Repository: `ProjectSpendTrackingRepository`, `project_budget_repository`, `user_project_repository`.
- Provider/integration: `BudgetEnforcementProvider` protocol, no-op registry provider, `LiteLLMBudgetEnforcementProvider`.
- Settings: `SettingsService` sync reads.
- UI: `SpendingCard`, `TableWidget`, `DetailsBadge`.

### Integration Points
- LiteLLM `/key/info` via `service.get_key_spending_info(api_key)` (project virtual keys), `/customer/info` via `service.get_customer_info(ref)` (members).
- `analytics -> member_spend_service -> budget_usage_service._compute_spend_delta -> spend_collector_service` helpers (`_quantize_spend`, `_did_budget_reset`, `is_reset_transition`, `InvalidSpendSnapshotError`).
- `member_spend_service -> provider_registry.get_active_provider()`.
- CLI statusline (`../codemie-code/src/agents/plugins/claude/plugin/statusline.ts` L83 `matchBudgetRow`): finds row where `project_name.trim().toLowerCase() === "<email> (cli)"`, reads `current_spending`, `total`, `budget_reset_at`. Order-independent (`Array.find`).

### Patterns and Conventions
- Lazy refresh: read DB -> stale check -> advisory xact lock -> re-check -> provider fetch -> insert changed / touch unchanged -> `session.rollback()` and reload (commit expires ORM objects).
- Provider methods are declared in protocol + no-op + LiteLLM adapter; adapter logs `budget_event=... component=litellm_budget_provider`.
- Settings reads wrapped in `asyncio.to_thread` in async paths (`get_user_project_rows`).
- Spend equality via `LiteLLMSpendCollectorService._quantize_spend`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/` has no budget/spend-specific guide; relevant general guides: `architecture/service-layer-patterns.md`, `data/repository-patterns.md`, `testing/testing-service-patterns.md`. Prior design docs: `docs/superpowers/plans/2026-10-27-epmcdme-14433-user-scoped-member-spend-refresh.md` (untracked), `docs/superpowers/tasks/2026-08-27-per-project-spending-personal-page/spec.md`, `docs/superpowers/plans/2026-08-13-epmcdme-14071-member-spend-analytics.md`.

### Architectural Decisions
- 14433 refresh plan constraints: no new columns/tables/migrations; no Redis/in-process per-user marker — freshness from `spend_date`; touching `spend_date` accepted with side effect that `GET /v1/projects/{name}/spends` shows the row on the touch date; never touch a row at or before its reset cutoff; admin paths stay on global `_ensure_fresh`.
- `_ensure_fresh` docstring: admin freshness is per-process `_last_checked_at` only, because user-scoped writes would make a DB marker look fresh for everyone.
- `get_user_project_rows` docstring: personal projects excluded because their spend is metered on the user (`budget` rows).
- `resolve_default_project` docstring (EPMCDME-15738): fallback is never persisted; every reader must derive it.
- Commits: `7496d8ab5` added per-project rows, `d5ccfd4b8` reset date persistence, `5185910eb` (16529) `member_effective_max_budget`.

### Derived Conventions
Keyword-only flags on `get_user_project_rows` to gate personal-page behaviour so admin callers keep defaults.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/analytics/handlers/test_member_spend_service.py` (1442 lines): `TestGetUserProjectRows`, `TestPersonalProjectExclusion`, `TestProjectResetCutoffInReadPath`, `TestResetDates`, `TestUserScopedRefresh` (helper `_patched` with ExitStack patching repos, provider, `SettingsService.get_projects_enforcing_member_spend_limits`, lock).
- `tests/codemie/service/analytics/handlers/test_budget_usage_service.py`: `TestBuildSpendingRow`, `TestExpandProjectRows`, `TestGetBudgetUsageWithProjectRows`, refresh/dedup classes.
- `tests/codemie/repository/test_project_spend_tracking_repository.py`: project_budget insert/latest-before tests (mocked session).
- `tests/codemie/service/spend_tracking/test_spend_collector_service.py`, `tests/codemie/service/budget/test_provider_registry.py`, `tests/codemie/rest_api/routers/test_analytics.py`, `test_analytics_budget_usage_labels.py`.
- UI: `src/pages/analytics/components/widgets/__tests__/TableWidget.test.tsx`, `BudgetOverrideIndicator.test.tsx`.

### Testing Framework and Patterns
Backend pytest + pytest-asyncio, `unittest.mock` (`AsyncMock`, `patch.object`, `SimpleNamespace` allocations, real `ProjectSpendTracking` instances). UI vitest 5.0.2 (`npm test` = `vitest run`).

### Coverage Gaps
- No tests for `_load_project_key_spend_from_litellm` / `collect_project_budget_spend` in `tests/enterprise/litellm/test_budget_provider_adapter.py`.
- No `SpendingCard` test in `src/pages/settings/components/__tests__/` (only `ProfileCard.test.tsx`); `UserDetailsPopup` tests mock it out.
- No tests for `get_project_reset_cutoffs_for_projects` behaviour with touched rows.

---

## 5. Configuration and Environment

### Environment Variables
`BUDGET_USAGE_STALENESS_THRESHOLD_MS` = 600000, `BUDGET_MEMBER_SPEND_STALENESS_THRESHOLD_MS` = 600000 (`src/codemie/configs/config.py` L946–949). `DB_INSERT_BATCH_SIZE`, `DB_IN_CLAUSE_BATCH_SIZE` used by the repo.

### Configuration Files
Project settings rows: `ENFORCE_MEMBER_SPEND_LIMITS_ALIAS` (`ENVIRONMENT_VARS` credential, `enabled`), project LiteLLM creds (`LITE_LLM` credential, alias = key alias), both encrypted in `Settings`.

### Feature Flags and Deployment Concerns
`is_litellm_enabled()` (`enterprise/litellm/dependencies.py`) gates refreshes; `isEnterpriseEdition()` gates `SpendingCard` on `ProfilePage`. Nightly collector scheduled from `service/spend_tracking/scheduler.py`.

---

## 6. Risk Indicators

- `get_user_project_rows` and `_limit_value`/`_build_spend_rows` are shared with `/user-project-spending`; any change to limit or row selection must be gated so admin output is byte-identical.
- `get_project_reset_cutoffs_for_projects` computes cutoffs from consecutive `project_budget` rows. Touching an existing row's `spend_date` moves a potential cutoff timestamp; live reset rows also change cutoffs that `/project-member-spending` (`get_project_reset_cutoffs`) applies — admin output can shift when live writes detect a reset earlier than the nightly job would.
- `project_spending_service.py` (project list/detail, lifetime spend = sum of `daily_spend`) reads `project_budget` rows; live writes change what those pages show and must keep `daily_spend` deltas consistent with the nightly collector.
- Nightly collector diffs against `get_latest_before_by_project_budget_ids(..., before=target_snapshot_at)`; live rows dated later than the nightly snapshot time affect its baseline. Upsert key `(project_name, budget_id, spend_date)`.
- `_load_project_key_spend_from_litellm` calls `get_project_litellm_creds_by_alias` synchronously in async code and has all-or-nothing error handling.
- Existing `touch_budget_spend_dates` / `touch_member_spend_dates` are subject-type specific; no project_budget equivalent or project-level advisory lock key exists.
- Member rows key on `allocation.project_budget_id` while project rows key on `(project_name, budget_category)`; matching a member row to the project total spans these keys.
- Speculative: the UI has no subject default-project data and `SpendingCard` is also shown for another user (`UserDetailsPopup`), so the default flag likely has to come from the backend per row; `project_name` labels must stay unchanged for `matchBudgetRow` (`"<email> (cli)"`), and any new column in `columns` would render in `TableWidget` unless hidden.
- Speculative: with enforcement off, `member_effective_max_budget` returns `budget.max_budget`, which requires loading the project Budget per allocation (not loaded by `get_user_project_rows` today).
- Prior attempt EPMCDME-12991 (!3632) reverted for row growth.

---

## 7. Summary for Complexity Assessment

The change spans two repos and five backend layers: the `/budget_usage` router (subject resolution), `BudgetUsageService` (row assembly, ordering, percent computation), `MemberSpendService.get_user_project_rows` (enforcement filter, limit selection, refresh), `ProjectSpendTrackingRepository` (project_budget reads/writes, locks), and the budget provider protocol plus LiteLLM adapter (`_load_project_key_spend_from_litellm` is global, sequential, and does sync settings reads on the event loop). UI work is confined to `SpendingCard.tsx` with `TableWidget` custom rendering and `DetailsBadge` styling borrowed from `BudgetOverrideIndicator`.

Most of the backend work has a direct precedent in the merged user-scoped member refresh (`_refresh_user_member_spend`, `collect_member_budget_spend_for_allocations`, `try_lock_member_refresh`, `touch_member_spend_dates`). The new part is applying it to `project_budget` rows, which are read in many places today: reset cutoffs for both admin endpoints, project list/detail spending, lifetime spend, and the nightly collector's baseline. That shared use is the main risk, together with the requirement that the admin endpoints stay byte-identical while they share `get_user_project_rows` and `_limit_value`.

Backend tests are dense and mock-based for both services, with a reusable `_patched` harness. There are no tests for the project-key spend loader, the cutoff computation with touched rows, or `SpendingCard`. The CLI statusline depends on the `"<email> (cli)"` personal-row label and does not depend on row order.

---

## 8. External References

- `../codemie-ui` (`C:/Users/KostiantynPshenychny/Documents/epm-cdme/repos/codemie-ui`): resolved, on branch `EPMCDME-14433_personal-panel-project-rows`. `SpendingCard.tsx`, `DetailsBadge.tsx`, `BudgetOverrideIndicator.tsx`, `TableWidget.tsx` read; facts in Section 2 (UI).
- `repos/codemie-code` statusline (read-only, not to be modified): `src/agents/plugins/claude/plugin/statusline.ts` L83 — `const target = \`${userEmail.trim().toLowerCase()} (cli)\`; return rows.find(r => r.project_name?.trim().toLowerCase() === target)`; `formatBudgetSegment` uses `row.current_spending`, `row.total`, `row.budget_reset_at`. Also `skills/codemie-analytics/scripts/analytics-cli.js:646` calls `/v1/analytics/budget_usage`.
