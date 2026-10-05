# Removed Project Budget Spend Implementation Plan

**Goal:** Keep a project category's current-period spend visible, and counted in Budget Period Spend, after its budget is removed from the budget group (EPMCDME-15048).

**Architecture:** Project detail and the project list read the latest `project_budget` row per category, whatever its `budget_id`, instead of dropping rows whose budget is no longer assigned. A category without an active budget keeps its row while that budget's period is open and its spend is > 0. The row is flagged `is_assigned=false`, and the frontend shows it on the category's "not assigned" card.

**Spec:** `docs/superpowers/tasks/2026-09-30-removed-project-budget-spend/spec.md`
**Reference code:** `docs/superpowers/plans/2026-09-30-removed-project-budget-spend.md` (the user's plan). Where a step below says "reference Task N", copy the named test or function body from there verbatim. Ignore that file's line numbers, commit formats and Task 4.

## Global Constraints

- Branch `EPMCDME-15048-removed-budget-spend` in both `codemie` and `C:/Users/kostiantyn_pshenych1/Documents/cdme/codemie-ui-next`.
- Commit per task using the repository's existing convention.
- Find code by function name. Line numbers have drifted since `2d050566a`.
- Leave these unchanged: the spend collector, the personal-project and key-based (`budget`/`key`) paths, how the limit is computed (`_aggregate_budget_window` over assigned budgets), soft delete, and reconciliation. Add no migration, service or config change.
- Backend narrow runs: `poetry run pytest <path> -v`. Frontend: run `npm ci` once, then `npm run test:unit -- --reporter=verbose <path>`.

## Review Focus

1. A removed category whose `budget_reset_at` has passed is neither counted nor shown (`test_removed_category_is_dropped_after_its_period_ends`).
2. A removed category with 0 spend is dropped (`test_removed_category_without_spend_is_dropped`).
3. When a category is re-added after the old budget's reset, the stale row is not counted (`test_stale_row_of_previous_budget_is_not_counted_after_readd_past_reset`).
4. When a category is re-added before the collector runs, the old row is reported once, under the active `budget_id` and limit (`test_readded_category_before_collector_reports_previous_row_under_active_budget`).
5. Personal and key-based projects keep their current output (existing tests, updated only to mock the new method).

---

### Task 1: Latest project_budget row per category (repository)

**Files:** `src/codemie/repository/project_spend_tracking_repository.py` (insert after `get_latest_before_by_member_budget_ids`); test `tests/codemie/repository/test_project_spend_tracking_repository.py`.

**Produces:** `ProjectSpendTrackingRepository.get_latest_project_budget_rows_by_category(session: AsyncSession, project_names: list[str]) -> list[ProjectSpendTracking]`

Test-first: yes — `TestLatestProjectBudgetRowsByCategory` fails with AttributeError. It covers: empty input returns `[]` without calling `session.execute`, and the compiled SQL contains `PARTITION BY project_spend_tracking.project_name, project_spend_tracking.budget_category ORDER BY`, `'project_budget'` and the project name (uses the existing `_compile_sql`).

- [ ] Write the test class (reference Task 1 Step 1) and confirm it fails.
- [ ] Implement it (reference Task 1 Step 3). A `row_number()` subquery partitioned by `(project_name, budget_category)`, ordered by `spend_date desc, created_at desc`, filtered to `spend_subject_type == "project_budget"` and `project_name IN`, is joined back on `id` with `row_rank == 1`. Use a Google-style docstring.
- [ ] Confirm the file's tests pass.

### Task 2: Per-category selection in project detail and list (router)

**Files:** `src/codemie/rest_api/routers/projects.py`; tests `tests/codemie/rest_api/routers/test_projects_router.py` and `test_projects_router_auditor.py`.

**Consumes:** Task 1 method. **Produces:**
- `SpendingWidgetRow.budget_category: Optional[str] = None` and `is_assigned: bool = True`, placed after `budget_id`
- `_is_budget_period_open(budget: Budget | None, now: datetime) -> bool`
- `_select_category_rows(budget_rows, assigned_budgets, budgets_by_id, now) -> list[tuple[row, assigned | None]]`
- `_build_category_widget_rows(selected, budgets_by_id) -> list[SpendingWidgetRow]`
- `_populate_project_spending_widget(*, response, widget_rows, key_row, budgets_map) -> None`

Test-first: yes — `TestIsBudgetPeriodOpen` and `TestUnassignedCategorySpend` fail on the import. Then the removed premium row is dropped (`current_spending == 0.04` where 0.18 is expected, and there is no `is_assigned=False` row). The list summary also reads 0.04.

- [ ] Add the reference Task 2 Step 1 test code (`TestIsBudgetPeriodOpen`, the helpers `_category_row`/`_category_budget`/`_assigned`, and `TestUnassignedCategorySpend` with all 7 tests). Import `timedelta` and `_is_budget_period_open`.
- [ ] Update the existing tests to mock `get_latest_project_budget_rows_by_category` (default `[]`):
  - `test_get_project_detail_fetches_spending_for_project_admin`: add a patch decorator and a `mock_get_category_rows` param. Assert it was awaited once with `["proj-a"]`, and that `get_latest_budget_rows_for_project` was awaited once (the `budget` fallback).
  - `test_list_projects_builds_spending_summary_from_latest_key_and_budget_rows`: the `get_latest_spending_by_project` side_effect becomes `[[stale_key_row, latest_key_row], [stale_budget_row, latest_budget_row]]`.
  - Add the mock in `test_personal_project_owner_sees_spending_in_list`, `test_personal_project_detail_returns_cumulative_and_period_spend`, `test_personal_project_detail_returns_spending_widget_with_budget_rows`, and in the auditor test that mocks `get_latest_budget_rows_for_project`.
- [ ] Run both router test files and confirm they fail.
- [ ] Implement:
  - Add the two `SpendingWidgetRow` fields.
  - Add `_is_budget_period_open` after `_parse_budget_reset_at`. It returns False for a missing budget or a missing reset, treats a naive reset as UTC, and returns `reset_at > now`. Its docstring notes that active budgets can lag by a day and that removed budgets freeze the value.
  - Replace `_drop_unassigned_budget_rows` with `_select_category_rows`, which maps assigned budgets by `budget_category`. When a category has an active budget, keep `(row, assigned)` if `row.budget_id == assigned.budget_id` or the row's budget period is open. When it has none, keep `(row, None)` only if the period is open and `float(row.budget_period_spend) > 0`.
  - Add `_build_category_widget_rows` after `_build_widget_rows` (reference body). Assigned rows use the active `budget_id`, limit and reset, with `total` as a % of the limit. Unassigned rows keep the row's `budget_id`, with `budget_limit=None`, `total=0`, the reset taken from `_budget_info`, and `is_assigned=False`.
  - `_attach_project_spending_summaries`: keep the `key`, `budget` and `get_latest_spending_by_project` reads. Replace the `project_budget` read with the Task 1 method, grouped by project. Use `_select_category_rows(...)` rows when a project has them, and fall back to `budget` rows otherwise.
  - `_attach_project_detail_spending`: read Task 1 rows first. Fall back to `get_latest_budget_rows_for_project` (`budget`), then `key`, and keep the `get_lifetime_spend` subject logic. Use `_select_category_rows` plus `_build_category_widget_rows` only for `project_budget`, and `_build_widget_rows` otherwise. `_populate_project_spending_summary` gets the selected rows.
  - `_populate_project_spending_widget` takes prebuilt `widget_rows` and appends `_key_row_to_widget` when `key_row` is set. It is only called from `_attach_project_detail_spending`. `timezone` is already imported.
- [ ] Run `poetry run pytest tests/codemie/rest_api/routers/ -v` and confirm it passes.

### Task 3: Unassigned spend on the empty card (frontend, codemie-ui-next)

**Files:** `src/types/entity/projectManagement.ts` (`ProjectSpendingWidgetRow`), `src/pages/settings/administration/projectsManagement/components/ProjectBudgetCard.tsx` (`ProjectBudgetCardEmptyProps`, `EmptyCard`, the empty branch of `ProjectBudgetCard`), `.../projectsManagement/ProjectBudgetsSection.tsx`; tests `components/__tests__/ProjectBudgetCard.test.tsx`, `__tests__/ProjectBudgetsSection.test.tsx`.

**Consumes:** the Task 2 widget fields. **Produces:** `ProjectSpendingWidgetRow.budget_category: BudgetCategory | null`, `is_assigned: boolean`, and the empty-variant prop `spendingRow?: ProjectSpendingWidgetRow | null`.

Test-first: yes — the new card and section tests fail with `Unable to find an element with the text: $0.14`, because the empty card shows no spend for an unassigned premium row.

- [ ] Add the tests from reference Task 3 Step 1. For the card: with an unassigned row it shows "— not assigned —" and `$0.14`; without one there is no "Spend" text. For the section: an unassigned row shows `$0.14`; an `is_assigned: true` row is not placed on empty cards (3 "— not assigned —" cards, no `$0.14`). Run both and confirm they fail.
- [ ] Implement:
  - Add the two type fields.
  - `EmptyCard` accepts `spendingRow`. Under "— not assigned —" it renders `Spend` plus `formatCurrency(spendingRow.current_spending)`. Both imports already exist.
  - `ProjectBudgetCard` forwards `props.spendingRow` to `EmptyCard`.
  - In `ProjectBudgetsSection`, after `spendingByBudgetId`, build `unassignedSpendingByCategory` from rows with `is_assigned === false && budget_category`, and pass `unassignedSpendingByCategory[category] ?? null` to the empty card. Active cards keep joining by `budget_id`.
- [ ] Run both test files and `npm run typecheck`, and confirm they pass.

---

negative-constraints (spec Non-goals):
- Collector untouched: no task edits it.
- Personal/key paths: Task 2 keeps the `budget`/`key` fallbacks and `_build_widget_rows`.
- No migration, service or config change: only the repository, router and UI change.
- No "removed" section or re-add entry: Task 2 re-attributes the row to the active budget, and Task 3 uses only the empty card.
- No past-period display: `_is_budget_period_open` gates unassigned rows.
- Limit unchanged: `_aggregate_budget_window` is untouched.
- Reconciliation and soft delete untouched.
