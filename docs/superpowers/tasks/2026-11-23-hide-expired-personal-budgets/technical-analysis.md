# Technical Research

**Task**: budget personal spending expired
**Generated**: 2026-11-23
**Research path**: filesystem

---

## 1. Original Context

Only the expired budgets part: Profile → Your personal spending table must not display expired budgets (budget list filtering by status expired). Active budgets, future reset dates and the "Personal limit" percentage display must remain unchanged. Do NOT do the "Available" value wrapping/layout part. Ticket text: Expired budgets (anton_test_budget_1, budget-issue-test, budget_defect_test, budget_defect_test2) currently appear as rows with "Expired" in Time Until Reset and "-" for spending/availability; they should not be displayed. Acceptance: given a user with expired budgets, no expired rows displayed; active budgets still displayed with reset date, spending, availability. This is the CodeMie backend repo (Python/FastAPI); find the endpoint/service feeding the Profile personal spending/budgets table and where expired status is computed (also check budget-attribution guide .ai-run/guides/integration/budget-attribution.md). Note the frontend lives in a separate repo, so identify whether filtering belongs in the backend response.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/rest_api/routers/analytics/spending.py` — `get_user_budget_usage`, `GET /v1/analytics/budget_usage` (L314-384). Subject is the caller, or a target user via `?user_id=` (`_resolve_admin_budget_subject`). Calls `budget_usage_service.get_budget_usage` and wraps the result with `ResponseFormatter.format_tabular_response`. Prior task analysis (`docs/superpowers/tasks/2026-10-29-epmcdme-14433-personal-panel-project-rows/technical-analysis.md`) says the UI `SpendingCard.tsx` (Profile page and admin user popup) fetches this endpoint and renders rows in server order. I did not verify this against the UI repo.
- `src/codemie/service/analytics/handlers/budget_usage_service.py` — the single source of the table:
  - `_calculate_time_until_reset(budget_reset_at)` (L95-115): returns the string `"Expired"` when `reset_time - now <= 0`. This is where "expired" is computed. There is no status field. A row is expired only implicitly, when `time_until_reset == "Expired"`.
  - `_build_spending_row` (L133-176): builds each row dict (`project_name`, `current_spending`, `budget_reset_at`, `time_until_reset`, `budget_limit`, `total` (percent), annotation keys, `is_default`, `is_shared_limit`). A missing spend gives `current_spending=None`, which the UI shows as "-". Missing limit gives `total=None`.
  - `_build_budget_usage_rows` (L228-261): personal budget rows, one per `user_budget_assignments` row, labelled `_category_label(username, category)`. Budget `budget_reset_at` comes from the `budgets` table.
  - `_expand_project_rows` (L192-225): project allocation rows (one per project per category with a limit). `budget_reset_at` comes from `<category>_reset_at` supplied by `member_spend_service.get_user_project_rows(..., with_reset=True)`. These are also rendered through `_build_spending_row`, so they can also read "Expired".
  - `BudgetUsageService.get_budget_usage` (L286-336): loads assignments and budgets, calls `daily_budget_reset_reconciliation_service.refresh_overdue` (re-reads budgets whose reset passed and reconciles with the provider), computes spend per budget in `_consumed_per_budget` (skips rows with no derivable period, so no figure), then returns `default_rows + personal_rows + other_rows`.
- `src/codemie/service/budget/daily_reset_reconciliation_service.py:174` — `refresh_overdue`. A budget stays expired after it only when the provider refresh fails or is rate-limited (`REFRESH_RETRY_INTERVAL`), or when the stored reset is not advanced. Those are the rows that show "Expired" with no spend ("-").
- `src/codemie/service/spend/budget_period.py:80` — `parse_budget_reset_at` (also a duplicate at `service/project/project_spending_service.py:98`).
- A separate, similar path exists in `src/codemie/service/project/project_spending_service.py` (`format_time_until_reset`, L81) and `service/analytics/spending_formatter.py:152` (`calculate_time_until_reset`). These feed other endpoints (project spending widget, `/spending`) and are not the Profile table.
- Other mentions of `_build_spending_row` (`workflow_handler.py:911`, `assistant_handler.py:496`) are docstring references only. No other code calls it.

### Architecture and Layers Affected
- API/router: `rest_api/routers/analytics/spending.py` (probably unchanged).
- Service (analytics handler): `service/analytics/handlers/budget_usage_service.py`.
- Repository, spend port and reconciliation are read-only inputs.

### Integration Points
- `budget_repository` (`get_user_category_assignments`, `get_by_ids`), `spend_service.consumed_for_holders`, `member_spend_service.get_user_project_rows`, `daily_budget_reset_reconciliation_service.refresh_overdue`, `resolve_identity`.
- The UI is in a separate repo and consumes the response as a tabular response. The columns are unchanged, and only the row set would differ.
- Frontend behaviour noted in the prior analysis: it switches layout on `rowCount <= 1` (InfoCard with doughnut) versus `> 1` (TableWidget). Filtering rows server-side would change that layout when only one row is left. Not verified.

### Patterns and Conventions
- The service only formats. It computes no spend of its own (AD-1, class docstring).
- Pure module-level helpers are unit-tested directly.
- "Expired" is a string sentinel in `time_until_reset`, with the raw `budget_reset_at` retained in the row. There is no expired enum or status column. `parse_budget_reset_at(...) <= now` is the check used in `refresh_overdue`.
- The `/budget_usage` endpoint is shared by the Profile page and the admin user popup (`?user_id=`). A backend filter therefore applies to both.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/integration/budget-attribution.md` — covers which budget pays for LLM requests (target → default project → personal). "Funded" means an active project budget plus an allocation. It says nothing about the Profile spending table or expired display. It is only context. No guide covers `/budget_usage`.
- `.ai-run/guides/api/rest-api-patterns.md` and `endpoint-conventions.md` exist for router conventions.
- `docs/superpowers/tasks/2026-10-29-epmcdme-14433-personal-panel-project-rows/technical-analysis.md` — earlier research on the same endpoint and UI card.

### Architectural Decisions
- AD-1 (spend port is the single source of figures) is referenced in `budget_usage_service.py`.
- Docstring decision: absent figures are shown as unavailable, never as zero. An expired row's blank figure follows this rule.

### Derived Conventions
- No existing filtering of rows by reset status was found in `get_budget_usage`.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/analytics/handlers/test_budget_usage_service.py` — `TestCalculateTimeUntilReset` (L90, asserts "Expired" for a past timestamp, L99-101), `TestBuildSpendingRow`, `TestBuildBudgetUsageRows` (L248), `TestConsumedPerBudget`, `TestBudgetUsageServiceGetBudgetUsage` (L582, including a refresh scenario at ~L681 mentioning "Expired"), `TestExpandProjectRows` (L815), `TestGetBudgetUsageDefaultFlagAndOrdering` (L902).
- `tests/codemie/rest_api/routers/test_analytics.py`, `test_analytics_budget_usage_labels.py`, `test_analytics_capability_guards.py`, and the OpenAPI baseline `tests/codemie/rest_api/router_guards/baselines/openapi.json`.

### Testing Framework and Patterns
- pytest with `AsyncMock`/`MagicMock`/`patch`. Helpers `_assignment`, `_budget(reset_at=RESET_AT, ...)`, `_figure`, `_project_spend_row`, `_membership`. `RESET_AT` is a module constant (check it is in the future).
- Per AGENTS.md, tests are written or run only when the user asks.

### Coverage Gaps
- No test for omitting expired rows (none exists, since the behaviour is new). The existing tests build budgets with `RESET_AT`. If that constant is a past date, adding a filter would break existing tests, so it needs verification.

---

## 5. Configuration and Environment

### Environment Variables
- None found specific to this behaviour.

### Configuration Files
- None governing it.

### Feature Flags and Deployment Concerns
- The admin `?user_id=` path requires `ENABLE_USER_MANAGEMENT=True` (from the endpoint's Query description). No flags for the budget table. No migration or deployment manifests are involved.

---

## 6. Risk Indicators

- Speculative: filtering belongs in the backend. The response has no status field, and the frontend (separate repo) would otherwise have to string-match `time_until_reset == "Expired"`. A server-side filter in `get_budget_usage` or `_build_budget_usage_rows` is the single place that covers both Profile and admin popup.
- Ambiguity: the ticket names budgets such as `anton_test_budget_1`. These look like budget or project names, so the affected rows may be personal budget rows (label = username with category suffix), project rows (`_expand_project_rows`), or both. Personal rows are labelled with the username, not a budget name. The named items are more likely project-allocation rows. A filter applied only to personal rows would not hide them. Needs confirmation with the ticket owner or reproduction.
- Speculative: "expired" should be defined consistently, for example as `parse_budget_reset_at(reset_at) <= now` after `refresh_overdue`. Using the string sentinel "Expired" is fragile. Filtering must happen after the refresh, since refresh can renew a budget and make it active.
- Risk: rows with `budget_reset_at=None` are not expired and must remain visible.
- Risk: the default-row flag and ordering (`default_rows + personal_rows + other_rows`) must not change. The UI switches between InfoCard and table at `rowCount <= 1`. Hiding rows may change the layout for users with one remaining row.
- Risk: `_calculate_time_until_reset` is imported directly by tests and asserts "Expired". Do not change its contract, so as to leave other callers and tests unaffected.
- The OpenAPI baseline guard should be unaffected, since there is no schema change.

---

## 7. Summary for Complexity Assessment

The Profile personal spending table is fed by `GET /v1/analytics/budget_usage` (`spending.py` router) and built entirely in `budget_usage_service.py`. There, `_calculate_time_until_reset` produces the "Expired" string when the budget's reset time has passed, and both personal rows (`_build_budget_usage_rows`) and project allocation rows (`_expand_project_rows`) go through `_build_spending_row`. The response has no status field, and the frontend lives in a separate repo, so the backend is the natural place to drop expired rows. That keeps the Profile page and the admin user popup consistent. The change surface is small: one service file, plus unit tests in one existing test file.

Technical novelty is low (a filter in an existing pure formatting path with no schema, config or migration change). Test coverage on the module is good, with direct unit tests for the helpers and service-level tests with mocks. The new behaviour is not covered.

The main risks are scope and definition. It is unclear whether the named budgets are personal budget rows or project rows. Expiry must be evaluated after `refresh_overdue` reconciles overdue budgets. Rows with no reset date must remain. The UI row-count layout switch is a side effect to check.

---

## 8. External References

None named by the task. (The budget-attribution guide it mentioned was read: it is in-repo and has no bearing on the display logic.)
