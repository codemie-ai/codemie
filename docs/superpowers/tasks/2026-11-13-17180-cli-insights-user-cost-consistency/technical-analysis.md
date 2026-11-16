# Technical Research

**Task**: analytics cli-insights cost user-details
**Generated**: 2026-11-13
**Research path**: filesystem

---

## 1. Original Context

Summary: CLI Insights user details cost does not match the filtered overview and user row.

Description: CLI Insights shows a discrepancy between the cost displayed in the filtered overview and user row and the cost displayed in that user's details. This makes the user-details cost KPI unreliable for reviewing CLI spend. Investigate whether the views use different cost sources or filter scopes, and align the details with the authoritative charged cost. If a reliable details cost cannot be shown, hide or clearly distinguish the KPI rather than presenting it as actual spend.

Preconditions:
* CLI activity exists for a user during the selected period.
* The viewer can access Analytics → CLI Insights and open a user's details.

Steps to Reproduce:
1. Open Analytics → CLI Insights.
2. Set a date range and select a project and user.
3. Note the cost shown in the CLI Overview and the user row.
4. Open that user's details and compare the cost KPI. Review the estimated monthly cost as well.

Expected Result: For the same user, project, and date range, the details' actual-cost figure matches the authoritative charged-cost total shown in the filtered overview and user row. Any estimate is clearly identified as an estimate. If an accurate details cost is unavailable, the misleading KPI is hidden or marked unavailable.

Actual Result: A cost discrepancy appears between the filtered overview and user row and the selected user's details. Other usage metrics appear consistent between the views.

Affected Areas: CLI Insights overview, per-user list and user-details cost metrics; cost-source and filter consistency; user-details KPI presentation.

Acceptance Criteria:
1. For the same user, project, and date range, the overview, user row, and user-details actual cost reconcile to the authoritative charged-cost source, subject only to currency rounding.
2. User details apply the same relevant filters and user identity as the selected row.
3. If the details cost cannot be reconciled, it is not displayed as an unqualified "Total Cost"; it is hidden or clearly marked unavailable until corrected.
4. Estimated monthly cost is clearly distinguished from actual charged cost and is hidden or qualified if its basis is not reliable.
5. Regression tests cover filtered single-user cost consistency while confirming the other usage metrics remain consistent.

---

## 2. Codebase Findings

### Existing Implementations

**Purpose A - "show a CLI user's charged cost for the filtered window" (overview, row, details).** Two different sources serve it today:

- **Ledger (spend port), CLI group** - used by overview and every per-user/per-project table.
  - Overview card `cli_cost`: `service/analytics/handlers/cli/handler.py:99-131` → `dashboard_group_spend(period, access_scope, users, projects, SpendGroup.CLI)` (`service/analytics/handlers/ledger_group_spend.py:68`). Comment at handler.py:125: "the same figure the Analytics summary card shows".
  - User rows: `CLIInsightsHandler._cli_spend_by_user` (`service/analytics/handlers/cli/insights_handler.py:156-169`) → `user_report_filters(user_ids, projects)` + `read_group_spend(..., SpendGroup.CLI)` (`ledger_group_spend.py:83,158`). Consumers: `_get_cli_insights_user_rows` (:870, feeding `/cli-insights-users` and `/cli-insights-top-spenders`), `get_cli_insights_top_users_by_cost` (:326), `get_cli_insights_user_classification` (:256), `_aggregate_user_rows_by_enrichment_field` (:1924, enriched-user tables). Project rows: `_cli_spend_by_project` (:171).
  - Rules: one filter per row = the row's ES bucket key (`attributes.user_id`) resolved to a `UserTerm` (id + identity-bridge alias set, `service/analytics/handlers/report_filter.py` `report_filter_for`), inside the dashboard's `projects` term; personal projects matched through owners (`ReportFilter.personal_project_owners`, `service/spend/ledger.py:261-290`). CLI group = `traffic_class_predicate(TrafficClass.CLI)` (`service/spend/spend_group.py:76`, `traffic_class.py:229`, `attr_category == 'cli'` / `codemie_origin`). Below `SPEND_LEDGER_COVERAGE_FLOOR` (default `2026-11-01T00:00:00Z`, `configs/config.py:814`) with `SPEND_LEDGER_LEGACY_BELOW_FLOOR=True` the legacy ledger answers the CLI class from the ES proxy metric with `attributes.cli_request` (`service/spend/legacy_ledger.py:42-50`).
  - Same ledger read is used by `user_handler._enrich_group_spend` (`user_handler.py:400-420`, users-CLI-spending table) and `summary_handler.py:300`.
- **Elasticsearch sum - used only by the user-details modal.**
  - `_build_cli_insights_user_detail_aggregation` (`insights_handler.py:1307-1320`): `proxy_usage` filter on `metric_name == codemie_litellm_proxy_usage` (`MetricName.CLI_LLM_USAGE_TOTAL`), `sum(attributes.money_spent)` + `money_coverage_aggs`.
  - Read by `_extract_cli_user_detail_core_metrics` (:1382-1432) via `read_money_coverage(proxy_aggs, "total_cost")` (`service/analytics/handlers/money_coverage.py:73`): unavailable if nothing settled, `partial` if any doc lacks `money_spent`, else exact (rounded to 2 dp).
  - Shown as: Key Metrics card `total_cost` labelled `TOTAL_COST_LABEL = "Total Cost"` (`cli/constants.py:79`; card at :1611-1618); detail meta `total_cost` + `spend_*` fields (:1558-1560).
  - Also feeds detail classification (`_classify_cli_entity(..., spend=...)` :1516-1521) and `rule_reasons` (:1573-1580).
  - Per-repository / per-category cost in the modal: ES `repositories_data...proxy.total_cost` (:1360-1370) read in `classification_engine.py:381`.
  - Git history: `cec7166b54` (EPMCDME-12578, "Fix CLI insights detail view showing zero cost"); `e0ce2543cc` (EPMCDME-13429, moved rows/overview to the ledger, did not touch the detail aggregation); `dfedd6c18f` (EPMCDME-17060, batched per-row ledger read).

**Purpose B - "show an estimate of monthly CLI cost, clearly marked as an estimate".**
- `_projected_monthly_spend` (`insights_handler.py:103-119`): `figure / analysis_days * 20`, inherits `unavailable`/`partial`; `analysis_days = max((end-start).days, 1)` (:1413). Input is the ES detail spend above.
- Card `est_monthly_20d`, label `"Est. Monthly (20 days)"` (:1619-1626); meta key `est_monthly_20d` (:1559). No other estimate exists in CLI Insights (searched `est_monthly`, `projected`, `monthly` in `service/analytics`).

**Purpose C - "details apply the same user identity and filters as the selected row".**
- Row identity = ES `users` terms bucket key (`attributes.user_id`); displayed `user_name` is replaced by the resolved display name (`UserIdentityResolver.resolve_rows(..., {"user_name": "name", ...})`, :936).
- Detail identity: `_get_cli_insights_user_detail_payload` (:587-630): if `user_id` absent, `_resolve_cli_insights_user_id` (:632-698) matches name/email over ES identity buckets, then DB fallback (`UserIdentityResolver.resolve`). ES filter = `should` of `user_name == entity_name`, `user_email == entity_name`, `user_id == resolved id` (`_build_cli_insights_entity_query` :1204-1229), plus the dashboard `users`/`projects` via `_pipeline._build_query`. Docstring at :676: `CLI_LLM_USAGE_TOTAL` docs store email in `attributes.user_name`.
- Router: 8 detail endpoints (`rest_api/routers/analytics/cli_insights.py:214-467`) all take `user_name` (required), `user_id`, `time_period`, `start_date`, `end_date`, `users`, `projects`; each recomputes the full payload (`get_cli_insights_user_key_metrics` etc., `insights_handler.py:479-585`).

**Other touched code**
- `service/analytics/analytics_service.py` - thin delegations for the `get_cli_insights_user_*` methods.
- `service/spend/rendering.py:200` `spend_fields` - the three companion keys (`spend_unavailable`, `spend_partial`, `spend_missing_amount`) every money card emits.

### Decisions Already in the Code

- **The ledger is the money source of truth for CLI Insights tables** - `insights_handler.py:161-164` docstring: "The ledger is the one place money lives ... a CLI Insights figure summed from `attributes.money_spent` stays blank for every request whose cost the metric document never received." Detail aggregation comment (:1311-1312) still treats the ES sum as the card source. Alternative (ES sum) is what detail kept; no recorded reason for the detail being left on ES found.
- **Absence is never `$0.00`; qualification travels beside the figure** (`RenderedSpend` + `spend_fields`) - convention across `insights_handler.py:91-140`, `money_coverage.py`, overview card (`handler.py:125-131`). *Corrected by the architecture review follow-up:* the UI no longer draws partial or unavailable markings. EPMCDME-16846 hid and EPMCDME-16942 removed the spend qualification suffix and line from the analytics widgets on purpose (codemie-ui `a95b36cb1`, `5e6eb29d4`, `ba397d441`). Absent money renders as a dash and a partial figure as a bare amount; the `spend_*` flags still travel on the wire.
- **Per-row filter carries the dashboard's surviving term** (rows keep `projects`; `ledger_group_spend.py:26-30`, enforced at :179-181).
- **Ledger project semantics = charged project** (`budget-attribution.md` "Identity and analytics": `project` is the project that was charged; personal-budget charges reach the ledger with no project, matched via owners). ES detail filters on `attributes.project` of the metric document.
- **Ledger reads are batched, one statement per table** (EPMCDME-17060); single-row reads go through the same `read_group_spend`/`aggregate_by_spend_group_per_filter`.

### Task Statements Checked Against the Code

- "Investigate whether the views use different cost sources or filter scopes" - **holds**: overview/rows read the ledger CLI group (`handler.py:99`, `insights_handler.py:156`); detail reads ES `sum(money_spent)` on `codemie_litellm_proxy_usage` (`insights_handler.py:1307-1313`). Filter scopes also differ: user identity (ledger `UserTerm` id+aliases vs ES `should` on name/email/id) and project (ledger charged project + personal owners vs ES `attributes.project`).
- "Other usage metrics appear consistent between the views" - **holds structurally**: sessions/lines in rows and detail both come from ES with the same dashboard filters (`insights_handler.py:887-933` vs :1273-1300); not verified against live data.
- "authoritative charged-cost source" - the code names the ledger (spend port) as such (`insights_handler.py:161`, `spend_group.py` docstring).
- "user row" passing identity to details - **partly contradicted**: UI reads `item.userId` (`codemie-ui/src/pages/analytics/components/CLIInsightsTab.tsx:108`) but backend rows emit `user_id` (`insights_handler.py:924`); no camelCase transform found in UI api utils or backend analytics. So the modal is opened with `user_id = null` and the display name, and the backend re-resolves the id (`_resolve_cli_insights_user_id`). UI test passes `userId="user-1"` directly to the modal (`CLIInsightsUserDetailsModal.test.tsx:231`), so the tab-to-modal mapping is untested.
- "Estimated monthly cost ... hidden or qualified if its basis is not reliable" - the estimate already inherits `unavailable`/`partial` from its input (`insights_handler.py:103-119`); label is "Est. Monthly (20 days)"; its basis is the ES detail figure.

### Consumers of Changed Contracts

- `/v1/analytics/cli-insights-user-key-metrics` cards `total_cost`, `est_monthly_20d`: UI `codemie-ui/src/hooks/useCliUserDetail.ts:75` → `CLIInsightsUserDetailsModal.tsx:98` (`MetricsGrid`). Only opened from `CLIInsightsTab.tsx:302` (Top Spenders / users tables). Router tests: `tests/codemie/rest_api/routers/test_analytics_capability_guards.py:117-124`, `test_analytics.py`; OpenAPI baseline `tests/codemie/rest_api/router_guards/baselines/openapi.json` (changes only if signatures change).
- `/v1/analytics/cli-insights-user-detail` meta `total_cost`, `est_monthly_20d`, `spend_*`, `classification`, `rule_reasons`: UI `useCliUserDetail.ts:74` (modal header/email use meta; cost fields not rendered from meta - searched modal for `total_cost`/`est_monthly`).
- Detail `spend` also drives `classification`, `rule_reasons`, `classification_metrics` widget (`/cli-insights-user-classification-detail`), shown only when Usage Attribution flag is off (`CLIInsightsTab.tsx:97-99`).
- `_cli_spend_by_user`, `read_group_spend`, `user_report_filters`: consumers listed under Purpose A; `tests/codemie/service/analytics/handlers/test_ledger_group_spend.py`.
- No SDK/CLI consumers of the detail endpoints found (searched `codemie-sdk`, `codemie-code`).

### Architecture and Layers Affected

- API: `rest_api/routers/analytics/cli_insights.py` (detail endpoints).
- Service/handler: `service/analytics/handlers/cli/insights_handler.py` (detail payload, key-metric cards), `classification_engine.py` (repository cost).
- Spend port (read only): `service/analytics/handlers/ledger_group_spend.py`, `report_filter.py`, `service/spend/*`.
- UI (sibling repo `D:\Projects\codemie-ui`): `CLIInsightsTab.tsx`, `cliInsights/CLIInsightsUserDetailsModal.tsx`, `cliInsights/helpers.tsx:56-64` (`buildUserDetailParams`), `hooks/useCliUserDetail.ts`, `widgets/MetricCard.tsx`.

### Integration Points

- Elasticsearch metrics index via `MetricsElasticRepository` (usage + detail money today).
- Spend ledger = LiteLLM spend logs in PostgreSQL via `spend_service.aggregate_by_spend_group(_per_filter)`; pool/timeout `SPEND_LEDGER_POOL_SIZE`, `SPEND_LEDGER_STATEMENT_TIMEOUT_MS`; failure → every row "unavailable" (`ledger_group_spend.py:35-37`).
- Identity bridge (`service/spend/identity_bridge.py` `aliases_for_user`) and users table lookup in `report_filter_for`.

### Patterns and Conventions

- Money values are `RenderedSpend`; cells via `apply_spend_cells`, cards via `spend_fields`; never `or 0`.
- Handler methods take `(time_period, start_date, end_date, users, projects)`; `TimeParser.parse` → `time_range_or_none`.
- Access boundary: `self._pipeline.access_scope` passed into every ledger read.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/budget-attribution.md` - defines "charged project" semantics used by the ledger (Identity and analytics section). Relevant.
- `.ai-run/guides/integration/cli-analytics-storage.md`, `cli-analytics-events.md` - cover the OTel plugin events in PostgreSQL (dedup ledger, rollups); not the source of CLI Insights cost (CLI Insights reads ES + spend ledger). Low relevance.
- `.ai-run/guides/testing/testing-patterns.md`, `testing-service-patterns.md` - pytest policy.
- Prior task docs: `docs/superpowers/tasks/2026-11-10-epmcdme-17060-batched-group-spend/`, `2026-07-09-epmcdme-13429-litellm-cost-retrieval/` (no mention of the detail modal found).

### Architectural Decisions

- Module docstrings act as ADRs: `ledger_group_spend.py:15-38`, `money_coverage.py:15-31`, `spend_group.py` (TOTAL = PLATFORM + CLI), `ledger.py:261-286` (ReportFilter vs AccessScope; "AD-29": combine qualified figures, never subtract/add cells).

### Derived Conventions

- A money figure that cannot be trusted is published `unavailable` (no figure) or `partial`, not hidden by the backend. The UI shows no figure as a dash and a partial figure as a bare amount (EPMCDME-16846/16942); it no longer draws a qualification from the `spend_*` flags.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/analytics/handlers/test_cli_handler.py` (2583 lines): `TestCLISummary` (overview ledger cost, :229-286, patches `cli.handler.dashboard_group_spend`); `test_get_cli_insights_user_detail` (:1625, ES-mocked `proxy_usage.total_cost`); `TestForecastCardIsNeverAProjectionOfNothing` (:2039, est monthly states); `TestCliSpendIsReadFromTheLedgerPerRow` (:2371, patches `user_report_filters`/`read_group_spend`); `TestRepositoryAndCategoryMoneyInTheDetailModal` (:2437); ranking tests (:2115-2210).
- `test_ledger_group_spend.py` - batched read and filter construction.
- UI: `codemie-ui/.../cliInsights/__tests__/CLIInsightsUserDetailsModal.test.tsx`, `components/__tests__/CLIInsightsTab.test.tsx`. *(Corrected: `widgets/__tests__/MetricCard.spendQualification.test.tsx` was removed with the markings by EPMCDME-16942.)*

### Testing Framework and Patterns

- pytest + pytest-asyncio, `MagicMock(spec=MetricsElasticRepository)`, `AsyncMock` patches on module-level names in `codemie.service.analytics.handlers.cli.insights_handler`; helpers `_exact/_partial/_absent` (:37-49) and `_user_bucket` (:2100).

### Coverage Gaps

- No test asserts that a single user's row cost equals the detail `total_cost` for the same filters (AC5).
- No test of the tab-to-modal identity hand-off (`item.userId` vs `user_id`).
- Detail tests never exercise the ledger; detail identity resolution with `projects` filter set is not covered with money.

---

## 5. Configuration and Environment

### Environment Variables

- `SPEND_LEDGER_COVERAGE_FLOOR` (default `2026-11-01T00:00:00Z`), `SPEND_LEDGER_LEGACY_BELOW_FLOOR` (True), `SPEND_LEDGER_READY`, `SPEND_LEDGER_POOL_SIZE`, `SPEND_LEDGER_STATEMENT_TIMEOUT_MS`, `SPEND_LEDGER_EMISSION_LAG_HOURS` (24) - `configs/config.py:814-857`.

### Configuration Files

- `src/codemie/configs/config.py` only; no CLI-Insights-specific config.

### Feature Flags and Deployment Concerns

- UI flags: `features:userEnrichmentEnabled`, `FEATURE_FLAGS.USAGE_ATTRIBUTION` (hides classification sections in the modal, `CLIInsightsTab.tsx:95-99`). No backend flag for detail cost.

---

## 6. Risk Indicators

- Root-cause signal: detail `Total Cost` and `Est. Monthly` are ES `sum(money_spent)` (`insights_handler.py:1310`), while overview/rows are ledger CLI group - different sources, different user and project matching (see Existing Implementations A/C).
- Window-dependent divergence: below `SPEND_LEDGER_COVERAGE_FLOOR` the ledger CLI figure is itself ES-derived (`attributes.cli_request`), at/above it comes from LiteLLM spend logs; reproductions will differ by date range.
- Detail `spend` also drives `classification`, `rule_reasons` and repository/category cost (`classification_engine.py:381`); Speculative: changing the detail figure's source changes these too unless separated.
- Speculative: a per-repository/per-category ledger split may not exist (ledger rows carry no repository/branch); those cells would remain ES-sourced.
- UI passes `item.userId` (undefined) - detail identity relies on server-side re-resolution from a display name; ES `should` on `user_name` with a display name may not match proxy docs that store email there (`insights_handler.py:676`).
- Eight detail endpoints each recompute the full payload; Speculative: adding a ledger read to the payload multiplies ledger queries by 8 per modal open.
- Cross-repo: KPI label/hiding is UI (`codemie-ui`), figure source/flags are backend; AC3/AC4 may need both.

---

## 7. Summary for Complexity Assessment

The discrepancy has a direct structural explanation: the CLI Overview cost card (`cli/handler.py:99`) and every CLI Insights user row (`insights_handler.py:156` → `ledger_group_spend.read_group_spend`, `SpendGroup.CLI`) read the spend ledger introduced by EPMCDME-13429, while the user-details modal still sums `attributes.money_spent` over the `codemie_litellm_proxy_usage` ES documents (`insights_handler.py:1307-1313`) and projects its Est. Monthly card from that sum. The two also match the user and project differently: the ledger uses the row's `user_id` plus identity-bridge aliases and charged-project semantics (personal projects via owners); the detail uses an ES `should` on user_name/email/resolved id and `attributes.project`. The UI opens the modal with `item.userId`, a field the backend row does not emit (`user_id`), so the id is re-resolved server-side.

Layers touched: one handler file (`insights_handler.py`, detail payload + key-metric cards), possibly `classification_engine.py`, reuse of `ledger_group_spend`/`report_filter` (*corrected:* `ledger_group_spend` is not read-only; it gained the shared per-holder read, AR-14); the router signature need not change. UI side (`codemie-ui`): tab-to-modal identity mapping and possibly card labelling. The pattern for reading a user's CLI ledger figure already exists (`_cli_spend_by_user`), and the qualification vocabulary (`RenderedSpend`, `spend_*` flags; *corrected:* no UI qualification line since EPMCDME-16942) is in place, so novelty is low.

Tests: `test_cli_handler.py` is large with mock-based patterns for both ledger and ES money, but no test ties row cost to detail cost, and the UI identity hand-off is untested. Key risks: classification/rule-reason and repository cost coupling to the detail figure, coverage-floor-dependent behaviour, per-modal ledger query fan-out across 8 endpoints, and the cross-repo split.

---

## 8. External References

None named by the task.

## Facts Added by Review

Researched for the names the spec relies on, after the analysis above was written. Read like the facts layer; where a line here contradicts one above, this one was read later.

- The qualification suffix is added only to `format: currency` columns, or to columns that have a `<colId>_spend_*` prefixed annotation (`utils/analyticsFormatters.ts:135-143`).
- Suffixes are `' (at least; $X unattributed)'`, `' (at least; some spend unattributed)'` and `' (incomplete data)'` (`analyticsFormatters.ts:119-127`).
- **In the user-details modal**, `cliInsights/CLIInsightsUserDetailsModal.tsx:205-230` passes:
- **In the modal**, at `CLIInsightsUserDetailsModal.tsx:168-179` it renders only when `showClassification` is true. Props: `data={categoryBreakdownChart}`, title "Category Breakdown", `labelField="category"`, `valueField="percentage"`, `secondaryValueField="cost"`, `colorByLabel={getDeterministicChartColor}`.
- `spend.figure` is a `RenderedSpend.figure: float | None`, where None means no figure and never zero (`codemie/src/codemie/service/spend/rendering.py:97-104`).
- `spend_fields` (rendering.py:200-213) emits the three unprefixed `spend_*` keys.
- The endpoints are `/v1/analytics/cli-insights-user-category-breakdown` and `/cli-insights-user-repositories` (`codemie/src/codemie/rest_api/routers/analytics/cli_insights.py:406-466`). Both call `_get_cli_insights_user_detail_payload` and then `_format_custom_tabular_response` (insights_handler.py:551-584).
- Absent money renders as `'-'` and never as `$0.00` (`analyticsFormatters.ts:29-33, 63-70`; `utils/currency.ts:31-36` `formatSpend`). TableWidget follows this. DistributionBarWidget's legend does not: `?? 0` (90, 99).
- Chart widgets show qualification through `formatChartSpendQualificationSuffix` (`analyticsFormatters.ts:150-161`), used by `BarChartWidget.tsx:186` and `StackedBarChartWidget.tsx:278`. DistributionBarWidget does not use it.
- Header text comes from the backend by default. The only frontend override in production code is `LeaderboardTab.tsx:256-257` (`columnLabels={COLUMN_LABELS}`, `columnTooltips={DIMENSION_TOOLTIPS}`).
- "Repositories table column labels from backend": **holds**. The modal passes no `columnLabels`, and TableWidget falls back to `col.label` (204). The cost header is `"Cost"` from insights_handler.py:1838.
- "Category Breakdown chart cost rendering": **holds** for DistributionBarWidget.tsx:89-100. Null becomes `$0.00`, with no qualification suffix. The backend column label `"Cost"` (1798) is never shown by this widget.
- `CLIInsightsUserDetailsModal.tsx:205`
- `CLIInsightsTab.tsx:281,292`
- `cliInsights/EnrichedUserSection.tsx:113-137` (5)
- `RoutingAnalyticsSection.tsx:66`
- `leaderboard/LeaderboardTab.tsx:236`
- `widgets/DynamicWidget.tsx:67`
- `pages/settings/components/SpendingCard.tsx:308`

## Facts Added by the Architecture Review Follow-up (round 2)

- **The spend qualification markings are gone from the UI, on purpose.** The suffixes and the
  `formatChartSpendQualificationSuffix` lines quoted in the section above were hidden by EPMCDME-16846 and removed by
  EPMCDME-16942 (codemie-ui `ba397d441` and the commits before it). Those lines describe the UI before that
  change.
- **`ledger_group_spend.py` is not read-only in this change.** It holds `group_spend_by_user` and
  `group_spend_by_project`, the one composition of "this user's or project's figure for one spend group under the
  dashboard filter". CLI Insights, the users-spending tables and both activity tables use them. The Postgres half
  of a batched read scans the caller's whole boundary for the window whatever the row count: its `WHERE` holds only
  the window, the boundary and the dashboard filter, and rows are assigned to filters in Python (`SpendGroupBatch`).
  **That holds for the Postgres half only.** A window reaching below `SPEND_LEDGER_COVERAGE_FLOOR`
  (`configs/config.py:814`) is answered in two halves, legacy first and one after the other
  (`service/spend/cutover.py:241-245`, `:284`). The legacy half (`service/spend/legacy_ledger.py:405-417`) is the
  Elasticsearch ledger's `aggregate_by_spend_group_per_filter`, which sends one filter per candidate, at most
  `FILTER_BATCH_SIZE` = 5,000 per search, and runs the searches in series (`service/spend/default_ledger.py:100`,
  `:750-761`). Its cost grows with the candidate count, up to `BUCKET_SELECTOR_FETCH_SIZE` = 10,000 activity
  candidates (`service/analytics/query_pipeline.py:66`), so two searches. Each search is bounded by the metrics
  repository's per-request timeout, `DEFAULT_REQUEST_TIMEOUT` = 60 s (`repository/metrics_elastic_repository.py:58`,
  `:168`, `:189-193`), a module constant and not a setting. `SPEND_LEDGER_STATEMENT_TIMEOUT_MS` and the dedicated
  pool bound only the Postgres half (`clients/postgres.py:226-237`).
- **The Analytics activity endpoints** (`/v1/analytics/users-activity`, `/v1/analytics/projects-activity`;
  `UserHandler.get_users_activity`, `ProjectHandler.get_projects_activity`):
  - They run through `AnalyticsQueryPipeline.execute_enriched_tabular_query`, the pipeline's seam for a column
    filled outside Elasticsearch. It fetches every candidate bucket (up to `BUCKET_SELECTOR_FETCH_SIZE`, 10,000),
    awaits the row enricher once over every candidate row before the page is cut, takes the enricher's order, and
    takes the footer keys for the enricher's columns from the enricher (`EnrichedRows.columns`/`totals`).
    `TotalsCalculator` sums the remaining numeric columns and skips empty cells.
  - The enricher (`activity_cost.activity_cost_rows`) fills CLI Cost from one batched ledger read. It builds
    Total Cost with `combine_rendered_spend` over Assistant + Workflow + Datasource (exact) and CLI, so with no CLI
    figure the Total is the amount of the known parts, flagged unavailable and partial. Both cells carry
    column-prefixed `spend_*` flags (`column_money_cells`, AR-15). It orders the rows by that Total and foots both
    columns with `spend_footer` (the CLI money key is omitted when no row has a figure).
  - Before this change, Total Cost was the metric documents' sum including their own CLI money (the `1-bucket`
    aggregation). That aggregation now only orders the Elasticsearch candidates under the fetch cap.
  - The users table's row key (`user_email`) holds the Elasticsearch user id until `UserIdentityResolver.resolve_rows`
    runs after the pipeline. The projects table's `unknown` bucket names no project and gets no ledger figure.
  - Their only UI caller is the custom dashboard's Users Activity and Projects Activity widgets.
