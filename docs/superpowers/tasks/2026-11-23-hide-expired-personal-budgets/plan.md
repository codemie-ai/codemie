# Hide Expired Budgets in Budget Usage Table Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** `GET /v1/analytics/budget_usage` no longer returns rows whose budget reset time has passed.

**Architecture:** Add a pure helper in `budget_usage_service.py` that drops rows whose `budget_reset_at` parses to an instant `<= now`. Apply it in `BudgetUsageService.get_budget_usage` after `refresh_overdue` and row building, to both personal and project rows, before the existing `default_rows + personal_rows + other_rows` split. `_calculate_time_until_reset` is untouched.

**Tech Stack:** Python, FastAPI service layer, pytest (`AsyncMock`/`MagicMock`).

**Spec:** requirements inline in the dispatch (no spec file); research in `technical-analysis.md` in this directory.

Commit per task using the repository's existing convention.

## Acceptance criteria

- A user with expired budgets gets no expired rows, personal or project.
- Active budgets are still returned with reset date, spending and availability.
- Unchanged: future-reset rows, the "Personal limit" percentage, rows with `budget_reset_at=None` (kept), the default-row flag, and the ordering default + personal + other.
- Out of scope: the "Available" wrapping/layout (frontend), `project_spending_service`, `spending_formatter`.

## Global Constraints

- Decide expiry with `parse_budget_reset_at(reset_at) <= now` from `codemie.service.spend.budget_period`. Never match the "Expired" string.
- `_calculate_time_until_reset` must keep returning "Expired".
- No schema, router or OpenAPI change.

---

### Task 1: Filter expired rows in get_budget_usage

**Files:**
- Modify: `src/codemie/service/analytics/handlers/budget_usage_service.py` (import near L24; new helper after `_build_budget_usage_rows`, ~L262; call site in `get_budget_usage`, L331-336)
- Test: `tests/codemie/service/analytics/handlers/test_budget_usage_service.py` (new class next to `TestBudgetUsageServiceGetBudgetUsage`, ~L582; reuse its helpers `_assignment`, `_budget`, `_project_spend_row` and its patching setup)

**Interfaces:**
- Produces: `_drop_expired_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]`. It keeps a row when its `budget_reset_at` is falsy or parses to an instant `> datetime.now(timezone.utc)`. An unparseable value (`ValueError`) keeps the row and logs a warning. It preserves order.

Test-first: yes — a user with one past-reset personal budget and one past-reset project row gets neither back, and the future-reset rows remain.

- [ ] **Step 1: Write failing tests.** Cover these cases through `get_budget_usage`, reusing the existing mocking pattern of the neighbouring class (budgets built with `_budget(reset_at=...)`; `RESET_AT` is 2099, so it is in the future):
  - a personal budget with a past `reset_at` is omitted;
  - a project row with a past `<category>_reset_at` is omitted;
  - a future-reset personal row and a future-reset project row are kept;
  - a row with `reset_at=None` is kept;
  - the default row keeps `is_default=True` and the order is default + personal + other with the expired rows removed.

  Add a direct unit test of `_drop_expired_rows` for the unparseable value (kept, no raise). Leave the existing `TestCalculateTimeUntilReset` "Expired" assertion as is.
- [ ] **Step 2: Run to confirm failure.** `pytest tests/codemie/service/analytics/handlers/test_budget_usage_service.py -k "expired or drop" -v` fails with `ImportError` or the expired rows still present.
- [ ] **Step 3: Implement.** Import `parse_budget_reset_at` alongside `budget_period_range` (L24). Add `_drop_expired_rows` with the contract above. In `get_budget_usage`, wrap `personal_rows` and `project_rows` with it after `_build_budget_usage_rows`, i.e. after `refresh_overdue` has run, before building `default_rows`/`other_rows`. Update the method docstring with one line stating that expired budgets are omitted.
- [ ] **Step 4: Run the whole module.** `pytest tests/codemie/service/analytics/handlers/test_budget_usage_service.py -v` passes. Any existing test that relied on an expired row being returned (the refresh scenario near L681) must keep passing, or be adjusted only if it asserted the expired row itself.
- [ ] **Step 5: Run router tests.** `pytest tests/codemie/rest_api/routers/test_analytics.py tests/codemie/rest_api/routers/test_analytics_budget_usage_labels.py -v` passes unchanged.

---

negative-constraints: honored. Task 1 leaves `_calculate_time_until_reset`, `project_spending_service` and `spending_formatter` untouched, matches on the parsed date rather than the "Expired" string, and does not touch the frontend "Available" layout.
