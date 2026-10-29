# EPMCDME-14433 Personal Panel Project Rows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/v1/analytics/budget_usage` + `SpendingCard` show the default project, enforcement-off budgeted projects, capped override limits and live project totals; admin endpoints unchanged.
**Architecture:** A new keyword flag on `get_user_project_rows` gates all new selection/limit/refresh logic; project totals refresh per project under advisory locks (insert on change, touch when unchanged); `BudgetUsageService` adds `is_default` and ordering; the UI renders a badge.
**Tech Stack:** FastAPI, SQLModel/asyncpg, pytest-asyncio (`repos/codemie`); React + vitest (`repos/codemie-ui`).
**Spec:** `docs/superpowers/tasks/2026-10-29-epmcdme-14433-personal-panel-project-rows/spec.md` (read with `technical-analysis.md`).

Commit per task using the repository's existing convention (`EPMCDME-14433: <Capital sentence>`), in the repo the task changes. Run only the task's own tests (`poetry run pytest <file> -v`; UI `npx vitest run <file>`).

## Global Constraints
- `/user-project-spending` and `/project-member-spending` code paths and output byte-identical (AC13).
- No new DB columns, tables, migrations; no Redis/in-process freshness markers.
- `project_name` labels and `columns` unchanged; `"<email> (cli)"` row unchanged.
- Do not modify `_load_project_key_spend_from_litellm`; do not touch `repos/codemie-code`.
- `spend_collector_service.py` gets the lock only; rows it writes do not change.

## Review Focus
- Subject with no memberships or no email: default `None`, all `is_default` false, HTTP 200 (Task 6).
- Per-project lock busy after the shared global lock succeeded: rolled back before the next project, so no lock is held across projects (Task 5).
- Latest `project_budget` row of a removed category budget (`budget_id` differs from the active assignment): not used as the total (Task 4).
- Project budget without key alias or creds: skipped, other projects still returned (Task 2).
- Enforcement-off project with `max_budget` 0: `total` 0.0, no division error (Task 6, existing guard in `_build_spending_row`).

---

### Task 1: Project-budget refresh locks and touch (repository)
**Files:** Modify `src/codemie/repository/project_spend_tracking_repository.py` (constant near L33; member counterparts at L583-645). Test: `tests/codemie/repository/test_project_spend_tracking_repository.py`.
**Produces:** `_PROJECT_BUDGET_REFRESH_ALL_LOCK_KEY = "project_budget_refresh:all"`; `async try_lock_project_budget_refresh(session, project_name: str) -> bool` (shared try-lock on the global key, then `pg_try_advisory_xact_lock(hashtext('project_budget_refresh:<project>'))`, as `try_lock_member_refresh` L620); `async lock_all_project_budget_refresh(session) -> None` (exclusive, waiting); `async touch_project_budget_spend_dates(session, row_ids: list[UUID], now: datetime) -> None` — as `touch_member_spend_dates` but filtered to `project_budget`, newer-row guard on `(project_name, budget_id)`, and **no commit** (the caller commits while holding its lock).
Test-first: yes — lock SQL/keys asserted and False when either lock is busy; touch filters `project_budget`, skips rows with a newer sibling, no-ops on empty ids, never calls `session.commit`.
- [ ] Write failing tests; run, expect FAIL (attribute missing).
- [ ] Implement; run, expect PASS.

### Task 2: Project-scoped provider spend method
**Files:** Modify `src/codemie/service/budget/provider.py:371` (protocol), `src/codemie/service/budget/provider_registry.py:260` (no-op returns `[]`), `src/codemie/enterprise/litellm/budget_provider_adapter.py` (after L1565). Tests: `tests/codemie/service/budget/test_provider_registry.py`, `tests/enterprise/litellm/test_budget_provider_adapter.py`.
**Produces:** `async collect_project_budget_spend_for_projects(self, project_names: list[str]) -> list[ProjectBudgetSpendSnapshot]`. Adapter: empty input or no service -> `[]`; loads active `ProjectBudgetAssignment`+`Budget` with `project_name.in_(project_names)` (join as L1489-1494, own session); per row, alias and snapshot built as L1504-1549, but `get_project_litellm_creds_by_alias` runs via `asyncio.to_thread` and each row has its own try/except (log, continue). One `/key/info` per project budget key.
Test-first: yes — no-op returns `[]`; adapter returns only requested projects, calls creds via `asyncio.to_thread`, and a row whose `get_key_spending_info` raises or lacks alias/creds is skipped while the other row is returned.
- [ ] Write failing tests; run, expect FAIL.
- [ ] Implement in all three places; run, expect PASS.

### Task 3: Nightly project-budget step holds the global lock
**Files:** Modify `src/codemie/service/spend_tracking/spend_collector_service.py:173-269`. Test: `tests/codemie/service/spend_tracking/test_spend_collector_service.py`.
**Consumes:** `lock_all_project_budget_refresh` (Task 1).
Open the `get_async_session()` block first, await `lock_all_project_budget_refresh(session)`, then move both provider `collect_*` calls and the early return inside it; nothing else changes, so the lock is held until `insert_project_budget_entries` commits.
Test-first: yes — lock awaited before `provider.collect_project_budget_spend` (shared call-order mock); inserted project rows equal today's for the same snapshots.
- [ ] Write failing test; run, expect FAIL.
- [ ] Implement; run file, expect PASS.

### Task 4: Enforcement-off rows and override caps in `get_user_project_rows`
**Files:** Modify `src/codemie/service/analytics/handlers/member_spend_service.py:272-336`. Test: `tests/codemie/service/analytics/handlers/test_member_spend_service.py`.
**Produces:** keyword-only `effective_limits: bool = False`. Describe it by behaviour only, with no caller or UI names in code or docstrings: with it set, rows use the limit the provider actually enforces (`member_effective_max_budget`, an override capped by the project's remaining budget, live project totals), and enforcement-off projects that have a budget are included; without it, behaviour is today's (stored allocation as the limit). `/user-project-spending` and `/project-member-spending` must not pass it, so their output stays unchanged. When set it replaces the `enforced_only` filter: selection = enforced projects + non-enforced projects with an active `ProjectBudgetAssignment` (`project_budget_repository.get_active_for_projects`, budgets via `budget_repository.get_by_ids`). Enforced rows unchanged. Load assignments, budgets and totals after the member-refresh reload (L326-330), whose commit expires ORM objects. Per non-enforced project and category: no active budget -> `{cat}_limit = None`; else `member_spend` = `_spend_value` of the member row after the cutoff drop; `project_total` = `budget_period_spend` of the `get_latest_project_budget_rows_by_category` row for `(project, cat)` whose `budget_id` matches the assignment, else 0. Limit from `member_effective_max_budget(alloc, budget, enforce_limit=False)` (`budget_models.py:44`; `alloc` may be None):
- FIXED: `{cat}_limit = min(override, member_spend + max(0, project_max - project_total))`, no percent key, reset date from `_reset_dates` (override budget).
- otherwise: `{cat}_limit = budget.max_budget`, new key `{cat}_percent_spend = project_total`, `{cat}_reset_at = budget.budget_reset_at`.
Non-enforced allocations stay in `selected`, so `_refresh_user_member_spend` covers them.
Test-first: yes — spec examples: off $100, X $50 / Y $0 -> limit 100, percent_spend 50 for both; FIXED binding (20), non-binding (40), over budget (= member spend), zero (0); no budget -> no row; removed-category row ignored; enforced FIXED keeps `allocated_max_budget`; `effective_limits` unset -> identical rows and no `project_budget_repository` call.
- [ ] Write failing tests; run, expect FAIL.
- [ ] Implement; run file, expect PASS.

### Task 5: Live project-total refresh
**Files:** Modify `member_spend_service.py` (new method beside L343; `_snapshot_to_row` L145). Test: same file as Task 4.
**Consumes:** Tasks 1, 2, 4. **Produces:** `async _refresh_user_project_totals(self, session, project_names: list[str], cutoffs_by_project) -> None`, never raises.
When `user_scoped_refresh and effective_limits and self._is_litellm_enabled()`, refresh the stale non-enforced budgeted projects (newest `project_budget` row among active budget ids fails `_is_stale`, or no row) before totals are read, then `session.rollback()`. Per project, one transaction: `try_lock_project_budget_refresh` (False -> rollback, skip) -> re-check staleness -> `collect_project_budget_spend_for_projects([name])` -> `get_latest_before_by_project_budget_ids(..., now, spend_subject_type="project_budget")` -> per snapshot `_is_hidden_and_unchanged` skip / `_is_unchanged_since` touch / else row with `allow_zero=prev is None` -> `touch_project_budget_spend_dates`, then `insert_project_budget_entries` (commits; otherwise explicit `session.commit()`). Exception -> log, rollback, next project. Give `_snapshot_to_row` a keyword `spend_subject_type="member_budget"` and `user_id=getattr(snapshot, "user_id", None)`.
Test-first: yes — fresh and enforced projects not fetched; busy lock -> no fetch/write, rollback; unchanged -> touch only; changed or reset -> insert; first row inserted at zero; a raising project does not stop the next; `InvalidSpendSnapshotError` skips that snapshot; second call within TTL adds no rows.
- [ ] Write failing tests; run, expect FAIL.
- [ ] Implement; run file, expect PASS.

### Task 6: Default flag, ordering and subject email
**Files:** Modify `src/codemie/service/analytics/handlers/budget_usage_service.py:150-403`, `src/codemie/rest_api/routers/analytics/spending.py:152-241`. Tests: `tests/codemie/service/analytics/handlers/test_budget_usage_service.py`, `tests/codemie/rest_api/routers/test_analytics_budget_usage_labels.py`.
**Consumes:** Task 4 flag and `{cat}_percent_spend`.
Router: `_resolve_admin_budget_subject` also returns `target_user_db.email`; the self path uses `user.email`; pass it to `get_budget_usage(session, subject_user_id, subject_label, subject_email: str | None = None)`. Service: first resolve `resolve_membership_default(await user_project_repository.aget_by_user_id(...), subject_email)` and whether it is in `aget_personal_project_names` (failure -> `None`, logged). Call `get_user_project_rows(..., effective_limits=True, with_reset=True, user_scoped_refresh=True)`. `_build_spending_row` uses `spending["percent_spend"]` as the numerator when present; `_expand_project_rows(rows, default_project=None)` passes `{cat}_percent_spend` and sets `is_default`; personal rows get `is_default` = default is personal. Return default-project rows, personal rows, then the rest; `columns` unchanged. Update existing exact-row assertions for `is_default`.
Test-first: yes — default rows first and flagged; personal default -> personal rows first and flagged; fallback default (no stored flag, email project) honoured; admin `user_id` uses target email; off non-FIXED `total` = 50.0 for X and Y; labels unchanged; no memberships -> all false.
- [ ] Write failing tests; run, expect FAIL.
- [ ] Implement; run both files, expect PASS.

### Task 7: "Default" badge in SpendingCard (repo `C:/Users/KostiantynPshenychny/Documents/epm-cdme/repos/codemie-ui`)
**Files:** Modify `src/pages/settings/components/SpendingCard.tsx:225-250`. Create `src/pages/settings/components/__tests__/SpendingCard.test.tsx`.
In `getSpendingCustomRenderColumns` add a `project_name` renderer: the label, then when `item.is_default === true` a `span.ml-1.inline-flex.align-middle` holding `DetailsBadge value="Default"` with the `className` from `BudgetOverrideIndicator.tsx:16`. Doughnut mode untouched.
Test-first: yes — `analyticsStore.fetchTabularData` mocked: two rows, one `is_default` -> exactly one "Default", beside that label; one row with `is_default` -> no "Default".
- [ ] Write failing test; run `npx vitest run src/pages/settings/components/__tests__/SpendingCard.test.tsx`, expect FAIL.
- [ ] Implement; rerun, expect PASS.

## Negative-constraint pass
- Admin endpoints unchanged: Task 4 `effective_limits` defaults off and the admin endpoints never pass it; only Task 6 does.
- Labels/columns unchanged, `is_default` not a column: Task 6.
- No schema change, Redis or in-process markers: Tasks 1/5 use advisory locks and `spend_date`.
- No live refresh for enforced or non-member projects: Task 5 selection.
- Global loader not refactored: Task 2 adds a sibling.
- Collector gets the lock only; member nightly/touch untouched: Task 3.
- No cap on enforced rows; no Overridden/Shared badge; zero-limit and doughnut unchanged: Tasks 4, 6, 7.
- `repos/codemie-code` untouched: no task.
