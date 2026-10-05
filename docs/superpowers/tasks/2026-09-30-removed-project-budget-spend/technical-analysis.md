# Technical Research

**Task**: budget project spending
**Generated**: 2026-09-30
**Research path**: filesystem

---

## 1. Original Context

implement C:\Users\kostiantyn_pshenych1\Documents\cdme\codemie\docs\superpowers\plans\2026-09-30-removed-project-budget-spend.md and C:\Users\kostiantyn_pshenych1\Documents\cdme\codemie\docs\superpowers\specs\2026-09-30-removed-project-budget-spend-design.md. ticket: EPMCDME-15048
Removed budget spending is not visible on UI after project budget redistribution
Summary: When a project has multiple assigned budgets with spending in the current budget period, and a project admin redistributes the budgets by removing one budget, the UI no longer allows users to view spending for the removed budget.
Description: Budget spending for the current budget period should remain visible even if the corresponding budget is removed from the project during budget redistribution. Users need this visibility to understand actual spend attribution for the full budget period and to avoid losing access to historical/current-period spending information.
Expected Result: Spending for the removed budget is still visible for the current budget period if that budget has recorded spendings. The UI allows users to distinguish between active assigned budgets and removed budgets with historical/current-period spend. Removed budgets with spendings are not hidden from reporting or spending visibility. Spending totals for the current budget period remain complete and auditable.
Actual Result: After the project admin removes the Premium budget during redistribution, the UI no longer provides a way to see spending for that removed budget.
Affected Areas: Project budget redistribution flow; Budget spending UI/widget; Spend visibility and reporting; Budget assignment and spend attribution logic; Current budget period spending aggregation.

---

## 2. Codebase Findings

### Existing Implementations
Backend (`codemie`, HEAD `b91c5cc13`; plan cites `2d050566a`, but every cited function exists at or near the cited lines):
- `src/codemie/rest_api/routers/projects.py`
  - `SpendingWidgetRow` (l.108): `budget_id, current_spending, budget_reset_at, time_until_reset, budget_limit, total`. It has no category or assignment flag.
  - `_parse_budget_reset_at` (455), `_budget_info` (462), `_drop_unassigned_budget_rows` (472). The last one keeps only rows whose `budget_id` is in the active assignments. This is the root cause named in the spec.
  - `_aggregate_budget_window` (483): the limit is the sum of `assigned_budgets.max_budget`. It falls back to non-deleted budgets behind the rows only when there are no assignments.
  - `_build_widget_rows` (514), `_key_row_to_widget` (540), `_project_spending_summary` (654).
  - `_attach_project_spending_summaries` (676, list page): reads `get_latest_spending_by_project(..., "project_budget")` and applies `_drop_unassigned_budget_rows` at l.707.
  - `_attach_project_detail_spending` (910): reads `get_latest_budget_rows_for_project(..., spend_subject_type="project_budget")`. It falls back to `budget`, then `key`, and applies the drop at l.939.
  - `_populate_project_spending_summary` (960), `_populate_project_spending_widget` (989). The widget builder is currently called inside the populate function with `project_name` and `budget_rows`.
  - `datetime`, `timezone`, `timedelta` and `UTC` are already imported (l.19).
- `src/codemie/repository/project_spend_tracking_repository.py`: `get_latest_before_by_member_budget_ids` (238). `get_latest_before_by_budget_category_ids` (185) already groups by `(project_name, budget_category)` using `max(created_at)`. Other methods are `get_latest_spending_by_project` (796), `get_latest_budget_rows_for_project` (946) and `get_lifetime_spend` (1027). `func` and `select` are already imported.
- `src/codemie/service/spend_tracking/spend_models.py`: `ProjectSpendTracking` has `budget_id`, `budget_category` (both nullable), `budget_period_spend: Decimal`, `spend_date` and `created_at`.
- `src/codemie/repository/budget_repository.py:205` `get_all_keyed_by_id`: `select(Budget)` with no `deleted_at` filter. Soft-deleted budgets, and their `budget_reset_at`, are present in `budgets_map`.
- `src/codemie/service/budget/project_budget_service.py`: `_soft_delete_project_budget_rows` (1647) sets only `deleted_at` and `provider_metadata` on the category budget and leaves `budget_reset_at` untouched. `delete_project_budget` (1697) and `delete_project_budget_group` (2305) call `delete_project_budget` once for each assignment, which is consistent with spec Decision 5.

Frontend (`codemie-ui-next`, branch `main`):
- `src/types/entity/projectManagement.ts` l.79 `ProjectSpendingWidgetRow` has the same six fields as the backend.
- `.../projectsManagement/components/ProjectBudgetCard.tsx`: `ProjectBudgetCardEmptyProps` (35), `EmptyCard` (103, shows "— not assigned —" with no spend), and `ProjectBudgetCard` (292), which does not pass through any spend on the empty variant.
- `.../projectsManagement/ProjectBudgetsSection.tsx`: `spendingByBudgetId` (~174) and the empty-card render (~222).
- `src/pages/settings/administration/ProjectDetailsPage.tsx:297` is the only producer of `spendingRows` (`project.spending_widget?.data?.rows`).

### Architecture and Layers Affected
- Repository: `ProjectSpendTrackingRepository`.
- API router: the pure helpers and response model in `projects.py`. The fix involves no service, migration or model change.
- Frontend: a type, a presentational card and a section component.

### Integration Points
- The spend collector writes `project_budget` rows. The spec says it copies the LiteLLM key `spend` into `budget_period_spend`. The collector is not modified.
- `project_budget_assignment_repository.get_assigned_budget_summaries_for_projects` supplies the active assignments (`budget_id`, `budget_category`, `max_budget`, `budget_reset_at`).
- `DailyBudgetResetReconciliationService` (`service/budget/daily_reset_reconciliation_service.py`) refreshes `budget_reset_at` on active budgets. Removed budgets keep the last value they had.

### Patterns and Conventions
- The repository latest-row lookups use a subquery joined back to the table, filtered on `spend_subject_type`.
- Router helpers are module-level pure functions that take `SimpleNamespace`-compatible rows, which keeps them easy to unit-test.
- Google-style docstrings are used on repository methods and short docstrings on router helpers.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/quality-gates.md`: `make ruff`, `make license-check`, `make test`, `make gitleaks`.
- `.ai-run/guides/testing/testing-api-patterns.md` and `.ai-run/guides/data/repository-patterns.md` apply.

### Architectural Decisions
- The spec (Decisions 1–5) is authoritative and is summarised in Section 8.
- The `_drop_unassigned_budget_rows` docstring records the original intent: "Snapshot rows outlive the budget they were taken for".

### Derived Conventions
- The personal-project and key-based spend paths must stay unchanged; the spec states this explicitly.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/repository/test_project_spend_tracking_repository.py`: `_compile_sql` helper (l.336) and SQL-string assertions against the sqlite dialect.
- `tests/codemie/rest_api/routers/test_projects_router.py`: `_mock_session_ctx` (l.60, `asynccontextmanager`). The plan's anchor tests have drifted from its line numbers: `test_get_project_detail_fetches_spending_for_project_admin` is at l.382 (plan says 369), `test_list_projects_builds_spending_summary...` at l.725 (plan ~780), and the personal-project tests at 2095, 2177 and 2246 (plan ~2133/2208/2278).
- `tests/codemie/rest_api/routers/test_projects_router_auditor.py:230` mocks `get_latest_budget_rows_for_project`.
- Frontend: `components/__tests__/ProjectBudgetCard.test.tsx` and `__tests__/ProjectBudgetsSection.test.tsx`. No test fixture builds a `ProjectSpendingWidgetRow`, so adding required fields will not break existing tests.

### Testing Framework and Patterns
- Backend: pytest with `pytest.mark.asyncio`/`anyio`, `AsyncMock`, `patch("codemie.rest_api.routers.projects._spend_repo")` and `SimpleNamespace` rows.
- Frontend: Vitest with Testing Library and `MemoryRouter`.

### Coverage Gaps
- No tests cover selecting spend rows for removed or unassigned categories.
- No tests show spend on the empty card.

---

## 5. Configuration and Environment

### Environment Variables
- `config.ENABLE_USER_MANAGEMENT` is patched in the router tests. No new variables.

### Configuration Files
- None relevant.

### Feature Flags and Deployment Concerns
- None. The change is additive to the API response (two optional/defaulted fields), with no migration.

---

## 6. Risk Indicators

- The selection depends on `budget_reset_at` of soft-deleted budgets, which is frozen at removal. For active budgets the value lags by up to a day until daily reconciliation runs. The plan documents this in the `_is_budget_period_open` docstring.
- `budget_reset_at` is an ISO string on `Budget`. Naive values need UTC coercion, which the plan handles.
- A category re-added before the collector runs yields a row with the old `budget_id`. It must be reported under the active budget; this is covered by a plan test.
- Replacing `get_latest_spending_by_project(..., "project_budget")` and `get_latest_budget_rows_for_project(..., "project_budget")` requires existing router tests to mock the new method (5 test sites).
- Speculative: `_populate_project_spending_widget` changes its signature, so any other caller would break. Grep found only one caller.
- A cross-repo contract: the frontend reads `is_assigned === false` and `budget_category`. Deploying the backend first is safe because the new fields have defaults.
- The plan's line numbers point at older HEAD `2d050566a`. Locate code by function name instead.
- `make gitleaks` needs docker; only podman is available.

---

## 7. Summary for Complexity Assessment

The change touches two repos and three layers: a repository method in `project_spend_tracking_repository.py`, pure helper functions and the `SpendingWidgetRow` response model in `rest_api/routers/projects.py`, and three frontend files (`projectManagement.ts`, `ProjectBudgetCard.tsx`, `ProjectBudgetsSection.tsx`). There is no migration, no service change and no config change. About 4 source files and 5 test files are modified.

The patterns involved are not new. The latest-row-per-category query mirrors `get_latest_before_by_budget_category_ids`, and the selection logic is a replacement for `_drop_unassigned_budget_rows`. The key enabling fact has been verified: `get_all_keyed_by_id` returns soft-deleted budgets, and soft delete preserves `budget_reset_at`. The subtle part is period-boundary logic across remove/re-add sequences, and the plan pins it with explicit tests.

Test infrastructure is mature (`_compile_sql`, `_mock_session_ctx`, `_spend_repo` patching), but none of it covers the new behaviour yet. Five existing router tests need a mock for the new repository method. The main risks are period-edge correctness, the double-count/re-add scenarios and plan line-number drift. Overall this is a small-to-medium, well-bounded change.

---

## 8. External References

- `C:\Users\kostiantyn_pshenych1\Documents\cdme\codemie\docs\superpowers\specs\2026-09-30-removed-project-budget-spend-design.md`: resolved (109 lines).
  - Decisions:
    1. A category without an active budget and with latest current-period spend > 0 shows that spend beside "— not assigned —".
    2. Budget Period Spend includes that spend, and the limit stays the sum of assigned budgets.
    3. The period ends at the removed budget's `budget_reset_at`.
    4. After a re-add, only the active budget is shown.
    5. Deleting the group equals removing each category.
  - Design:
    - New `get_latest_project_budget_rows_by_category(session, project_names)`.
    - Per-category selection replaces `_drop_unassigned_budget_rows`.
    - `SpendingWidgetRow` gains `budget_category: str | None` and `is_assigned: bool`. Unassigned rows have `budget_limit=None` and `total=0`.
    - The frontend looks up unassigned rows by `budget_category`.
- `C:\Users\kostiantyn_pshenych1\Documents\cdme\codemie\docs\superpowers\plans\2026-09-30-removed-project-budget-spend.md`: resolved (983 lines). There are 4 tasks:
  1. The repository method (ROW_NUMBER partitioned by project_name and budget_category, ordered by spend_date and created_at desc).
  2. Router work: `_is_budget_period_open`, `_select_category_rows`, `_build_category_widget_rows`, the new `_populate_project_spending_widget(*, response, widget_rows, key_row, budgets_map)`, and rewrites of both attach functions.
  3. Frontend: the `EmptyCard` `spendingRow` prop and `unassignedSpendingByCategory`.
  4. Gates and a manual check. After premium is set to 0%, Budget Period Spend should be 0.18 with the premium card showing "Spend 0.14, not assigned". After a re-add at 220 it should be 0.18 with 0.14 / 220.
- The frontend repo `C:\Users\kostiantyn_pshenych1\Documents\cdme\codemie-ui-next` resolved. All target files exist.
