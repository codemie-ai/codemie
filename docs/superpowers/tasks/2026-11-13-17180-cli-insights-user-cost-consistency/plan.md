# EPMCDME-17180 CLI Insights user-details cost reconciliation - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Details Total Cost, estimate, classification and rule reasons come from the row's own per-user ledger read; ES per-repository/category cost is labelled not charged; the UI passes the row's `user_id` to the modal.

**Architecture:** Split `_get_cli_insights_user_detail_payload` into a usage section (ES only, no user-level money, no classification) and a charged section (usage + `_cli_spend_by_user([user_id or ""], ...)[0]`, awaited in the same `asyncio.gather` as the two ES calls). Only the detail, key-metrics and classification-detail endpoints compose the charged section.

**Tech Stack:** Python/pytest-asyncio (`D:\Projects\codemie`); React/TypeScript/vitest (`D:\Projects\codemie-ui`).

**Spec:** `docs/superpowers/tasks/2026-11-13-17180-cli-insights-user-cost-consistency/spec.md`

Commit per task using the repository's existing convention. Each task names its repository. Backend stays on `EPMCDME-17180_cli-insights-user-cost-consistency`; Task 3 creates the UI branch.

## Global Constraints

- Money is `RenderedSpend` emitted with `spend_fields`; never `or 0`; absence is never `$0.00`.
- Read-only: `cli/handler.py` cost path, `_cli_spend_by_user`, `ledger_group_spend.py`, `report_filter.py`, `service/spend/*`.
  > Superseded by architecture review follow-up: `ledger_group_spend.py` gained `group_spend_by_user` / `group_spend_by_project` (AR-14) and `service/spend/rendering.py` gained `money_cell` (AR-13); the activity tables are now in scope (AR-5, AR-10). See spec Non-goals.
- Details ES matching (`_build_cli_insights_entity_query`, `insights_handler.py:1204-1229`) unchanged.
- No router/OpenAPI change; ids `total_cost`, `est_monthly_20d` and `TOTAL_COST_LABEL` unchanged.
- Estimate formula unchanged (`_projected_monthly_spend`, :103-119; days = `max((end-start).days, 1)`).
- Exact copy: `"Estimated Monthly Cost (projection: 20 x daily average over the period)"`, `"Cost (usage metrics, not charged)"`.
  > Superseded by architecture review follow-up: the label is `"Estimated Monthly Cost"` with the description `"Projection: 20 x daily average over the period"` (AR-4), and the qualifier is `"Cost from usage metrics (may differ from Total Cost)"` (AR-2). See spec AC4, AC5.

## Review Focus

- No id resolves: ledger gets `[""]`, card has no amount + `spend_unavailable`, never a 500 (Task 1).
  > Superseded by architecture review follow-up: the details read one user through `_cli_spend_of_user` → `dashboard_group_spend`, and no id returns no figure without any ledger read (AR-3). See spec AC1.
- Ledger unavailable or partial: card, meta and estimate carry the same flag (Task 1).
- The five usage-only endpoints make zero ledger reads (Task 1).
- Row click in either users table carries that row's `user_id` (Task 3).

---

### Task 1: [backend codemie] Details charged section reads the row's ledger figure

**Files:** Modify `src/codemie/service/analytics/handlers/cli/insights_handler.py:450-630` (endpoints + builder), `:1307-1320` (drop `total_cost`, `money_coverage_aggs` and comment :1311-1312 from `proxy_usage`; tokens/models stay), `:1382-1432` (drop `spend`, `est_monthly_spend`), `:1451-1589` (split widgets/meta). Test `tests/codemie/service/analytics/handlers/test_cli_handler.py`.

**Interfaces:** Produces `_get_cli_insights_user_usage_payload(...)` and `_get_cli_insights_user_charged_payload(...)`, both with the current builder's parameters (`user_name, user_id=None, time_period=None, start_date=None, end_date=None, users=None, projects=None`) and replacing it. Charged-only keys: `key_metrics`, `classification_metrics`, `classification`, `primary_category`, `total_cost`, `est_monthly_20d`, `spend_*`, `rule_reasons`. Shared prep (id resolution, `TimeParser.parse`, entity query) is a helper; no skip-ledger flag.

Test-first: yes - `TestDetailsCostReconcilesWithTheRow` fails because details read ES `proxy_usage.total_cost` (mocked 99.0) instead of the ledger figure the row and overview show.

- [ ] **Step 1: Write failing tests** in `TestDetailsCostReconcilesWithTheRow`. Fake the port at `codemie.service.analytics.handlers.ledger_group_spend` (`report_filter_for`, `spend_service.aggregate_by_spend_group`, `..._per_filter`; shapes as in `test_ledger_group_spend.py`), answering per user id. User `u-ada`, `projects=["alpha"]`, window 2026-08-01..2026-08-31.
  - exact 12.34: overview `cli_cost` (`users=["u-ada"]`), row `total_cost` (`_get_cli_insights_user_rows`), card and meta `total_cost` all equal, same `spend_*`;
  - partial and unavailable: details flags equal the row's, unavailable value `None`;
  - `user_id=None` with resolver -> None: `_cli_spend_by_user` awaited with `([""], start, end, ["alpha"])`, card `None` + `spend_unavailable`;
    > Superseded by architecture review follow-up: no ledger read at all for no id (AR-3).
  - figure 45.0 -> `est_monthly_20d == 30.0` (card and meta); `_classify_cli_entity` and `_build_cli_rule_reasons` get `spend=` the ledger figure;
  - `_cli_spend_by_user` not awaited by tools/models/workflow_intent/category_breakdown/repositories; awaited once by detail/key_metrics/classification_detail;
  - plan note: `_cli_spend_by_user(["u-ada"])[0] == _cli_spend_by_user(["u-bob","u-ada","u-cy"])[1]` over the same port;
  - self-matching user: details sessions, net lines, unique repositories equal the row's (characterization, passes before and after).
- [ ] **Step 2: Run** `poetry run pytest tests/codemie/service/analytics/handlers/test_cli_handler.py -k ReconcilesWithTheRow -v` - money/estimate/classification/no-read tests FAIL.
- [ ] **Step 3: Implement** the split; build estimate, classification, `rule_reasons`, money cards and meta only in the charged builder. Repoint endpoints :450-489 and :539-549 to charged, :491-537 and :551-585 to usage. Move existing ES-money detail tests (`test_get_cli_insights_user_detail` :1625, `TestForecastCardIsNeverAProjectionOfNothing` :2039) onto the ledger patch.
- [ ] **Step 4: Run** `poetry run pytest tests/codemie/service/analytics/handlers/test_cli_handler.py -v` - PASS.

### Task 2: [backend codemie] Estimate and usage-metric cost labels

**Files:** Modify `src/codemie/service/analytics/handlers/cli/constants.py` (near :79), `insights_handler.py:1621`, `:1799`, `:1838`. Test `test_cli_handler.py`.

**Interfaces:** Produces `EST_MONTHLY_COST_LABEL` and `USAGE_METRIC_COST_LABEL` in `cli/constants.py`, holding the two exact strings.

Test-first: yes - `test_detail_cost_labels_name_their_basis` fails: card reads "Est. Monthly (20 days)", both `cost` columns read "Cost".

> Superseded by architecture review follow-up: the strings changed to `EST_MONTHLY_COST_LABEL = "Estimated Monthly Cost"` plus `EST_MONTHLY_COST_DESCRIPTION`, and `USAGE_METRIC_COST_LABEL = "Cost from usage metrics (may differ from Total Cost)"` (AR-2, AR-4).

- [ ] **Step 1: Write the failing test**: key-metrics `est_monthly_20d` label and the `cost` column label in `category_breakdown_chart` and `repositories_table` equal the exact strings.
- [ ] **Step 2: Run** `poetry run pytest tests/codemie/service/analytics/handlers/test_cli_handler.py -k cost_labels -v` - FAIL.
- [ ] **Step 3: Implement** the constants and use them at the three sites; column ids stay `cost`.
- [ ] **Step 4: Run** the same command - PASS.

### Task 3: [UI codemie-ui] Row click hands the row's `user_id` to the modal

**Files:** Modify `src/pages/analytics/components/CLIInsightsTab.tsx:108` (`item.userId` -> `item.user_id`). Test `src/pages/analytics/components/__tests__/CLIInsightsTab.test.tsx`.

Test-first: yes - clicking a row with `user_id: 'u-ada'` opens the modal with `userId` null.

- [ ] **Step 1: Branch** in `D:\Projects\codemie-ui`: `git checkout main && git pull && git switch -c EPMCDME-17180_cli-insights-user-id`.
- [ ] **Step 2: Write the failing test**: extend the `TableWidget` mock (:42-46) to render the `customRenderColumns.user_name` cell for `{ user_name: 'Ada', user_id: 'u-ada' }` and the modal mock (:50-54) to print `userId`; click in "Top Spenders" and in "All Users", assert `userId:u-ada`.
- [ ] **Step 3: Run** `npx vitest run src/pages/analytics/components/__tests__/CLIInsightsTab.test.tsx` - FAIL.
- [ ] **Step 4: Implement** the field fix (no backend camelCase alias), rerun - PASS.

### Task 4: [UI codemie-ui] Category Breakdown description marks cost as not charged

**Files:** Modify `src/pages/analytics/components/cliInsights/CLIInsightsUserDetailsModal.tsx:172`. Test `src/pages/analytics/components/cliInsights/__tests__/CLIInsightsUserDetailsModal.test.tsx`.

Test-first: yes - with `showClassification` true, the description lacks "Cost (usage metrics, not charged)". *(Superseded string: see Step 1 note.)*

- [ ] **Step 1: Write the failing test**: description equals `"Repository distribution across deterministic CLI categories. Cost (usage metrics, not charged)."`.
  > Superseded by architecture review follow-up: the description is `"Repository distribution across deterministic CLI categories. Cost from usage metrics (may differ from Total Cost). Repository categories are scored on usage-metric cost."` (AR-2).
- [ ] **Step 2: Run** `npx vitest run src/pages/analytics/components/cliInsights/__tests__/CLIInsightsUserDetailsModal.test.tsx` - FAIL.
- [ ] **Step 3: Implement** on the Task 3 branch; `DistributionBarWidget` untouched. Rerun - PASS.
