# Spec: Keep current-period spend visible when a project budget is removed

**Ticket**: EPMCDME-15048
**Source design**: `docs/superpowers/specs/2026-09-30-removed-project-budget-spend-design.md` (approved)
**Repos**: `codemie` (backend), `codemie-ui-next` (frontend)

## Problem

When a project admin redistributes a project budget group and removes one category (for example
Premium models), the UI stops showing the spend that category already had in the current budget
period. Budget Period Spend drops (project-3: 0.18 to 0.04) and the removed category's card shows
only "— not assigned —".

## Root cause

The spend collector writes `project_spend_tracking` rows with `spend_subject_type='project_budget'`
per category budget. Setting a category to 0% soft-deletes its category budget and assignment
(`ProjectBudgetService.delete_project_budget`); the spend rows remain, but
`projects._drop_unassigned_budget_rows` (`src/codemie/rest_api/routers/projects.py`, ~l.472) drops
every row whose `budget_id` has no active assignment, in both project detail and project list. The
frontend joins `spending_widget` rows to cards by `budget_id`, so it has nothing to show.

## Decisions (settled)

1. While a category has no active budget and its latest current-period spend is > 0, its card shows
   that spend next to "— not assigned —".
2. Budget Period Spend includes that spend. The limit stays the sum of assigned budgets.
3. A removed category's current period ends at the removed category budget's `budget_reset_at`;
   after that its spend is neither shown nor counted.
4. When the category is re-added, only its active budget is shown; no separate "removed" entry.
5. Deleting the whole budget group behaves the same as removing each category.

## Design

### Repository
New `ProjectSpendTrackingRepository.get_latest_project_budget_rows_by_category(session, project_names)`:
the latest `project_budget` row per `(project_name, budget_category)`, regardless of `budget_id`. It
replaces the `project_budget` reads in `_attach_project_detail_spending`
(`get_latest_budget_rows_for_project`) and `_attach_project_spending_summaries`
(`get_latest_spending_by_project`). Pattern reference: `get_latest_before_by_budget_category_ids`.

### Router (project detail and project list)
`_drop_unassigned_budget_rows` is replaced by a per-category selection:
- **Category with an active budget**: keep the row if it belongs to that budget, or to an earlier
  budget whose `budget_reset_at` is still in the future; report it under the active budget's
  `budget_id` and limit.
- **Category without an active budget**: keep the row only if its budget's `budget_reset_at` is in
  the future and `budget_period_spend > 0`.

Soft-deleted budgets keep `budget_reset_at` and are returned by `BudgetRepository.get_all_keyed_by_id`,
so the period check works for removed budgets. Naive `budget_reset_at` values are treated as UTC.

Budget Period Spend = sum of `budget_period_spend` over kept rows. Limit = `_aggregate_budget_window`
over assigned budgets (unchanged).

`SpendingWidgetRow` gains two fields:

```python
budget_category: str | None = None
is_assigned: bool = True
```

For unassigned rows `budget_limit` is `None` and `total` is `0`.

### Frontend (codemie-ui-next)
- `ProjectSpendingWidgetRow` (`src/types/entity/projectManagement.ts`) gains `budget_category` and
  `is_assigned`.
- `ProjectBudgetsSection`: for a category with no active budget, look up its `spending_widget` row by
  `budget_category` with `is_assigned === false`; if found, `EmptyCard` shows the spend next to
  "— not assigned —". Active cards keep joining by `budget_id`.

## Acceptance criteria

- AC1: Repository method returns exactly one row per `(project_name, budget_category)`, only
  `project_budget` rows, the latest one.
- AC2: Project detail: a removed category with current-period spend > 0 is included in Budget Period
  Spend and appears in `spending_widget` with `is_assigned=false`, its `budget_category`,
  `budget_limit=None`, `total=0`.
- AC3: A removed category is excluded (from both total and widget) once its `budget_reset_at` has
  passed, or when its `budget_period_spend` is 0.
- AC4: A re-added category yields exactly one row under the new active `budget_id` and limit,
  including before the collector has written a row for the new budget.
- AC5: After a re-add past the removed budget's `budget_reset_at`, the old budget's row is not counted.
- AC6: Project list: the removed category's current-period spend is included in the spending summary.
- AC7: The Budget Period Spend limit remains the sum of assigned budgets.
- AC8: Deleting the whole budget group gives the same result as removing each category.
- AC9: `EmptyCard` renders the spend next to "— not assigned —" when given an unassigned row, and
  renders as today without one.
- AC10: `ProjectBudgetsSection` places only `is_assigned=false` rows on empty cards (matched by
  `budget_category`); active cards still join by `budget_id`.
- AC11: Manual check (platform + premium, spend P and M): premium set to 0% gives Budget Period
  Spend P+M and premium card "Spend M, — not assigned —"; premium re-added gives P+M and M / new limit.
- AC12: Existing router tests are updated to mock the new repository method and pass; quality gates
  (`make ruff`, `make test`) pass.

## Non-goals

- No change to the spend collector or to how `budget_period_spend` is written.
- No change to the personal-project or key-based (`budget` / `key` subject) spend paths.
- No database migration, new model, service-layer or config change.
- No separate "removed budgets" section, history view, or entry once a category is re-added.
- No display of removed-budget spend after its `budget_reset_at` (no historical/past-period reporting).
- No change to how the Budget Period Spend limit is computed.
- No change to `DailyBudgetResetReconciliationService` or to soft-delete behaviour of budgets.

## Risks

- Removed budgets' `budget_reset_at` is frozen at removal; active budgets' value can lag up to a day
  until daily reconciliation.
- Cross-repo contract: frontend relies on `is_assigned` and `budget_category`; backend-first deploy is
  safe because the fields have defaults.
- The plan's line numbers target an older HEAD; locate code by function name.
