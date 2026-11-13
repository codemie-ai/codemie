# Technical Research

**Task**: user attribution spend ledger elasticsearch
**Generated**: 2026-11-13
**Research path**: filesystem

---

## 1. Original Context

Remove Elasticsearch altogether from the user attribution and rely only on the litellm_spend_hourly table (user attribution reads: person spend via attr_user_id / attr_principal, per-user analytics widgets, and the Usage Attribution feature where it reads spend). Ticket: EPMCDME-17194. Context: MR 4839 on branch EPMCDME-17194_analytics-money-reuse-spend-cutover already moves projects-activity total_cost and cli_cost onto the ledger above the coverage floor; checkpoint tag checkpoint/EPMCDME-17194-before-es-removal marks its tip (7a783f65f0). Findings so far: litellm_spend_hourly holds only spend and row_count over 11 dimensions (attr_source, attr_form, attr_user_id, attr_principal, attr_project, attr_category, codemie_origin, call_type, codemie_workflow_id, model_group, end_user); about 77% of local spend has no attr_user_id (principal rows); Usage Attribution (features:usageAttribution) currently reads Elasticsearch metrics plus the app DB and does not reference the hourly table.

(Caller instructions: current branch EPMCDME-17194_user-attribution-litellm-only is cut from main, MR 4839 changes are NOT on it. Read-only research. Map both candidate scopes (A) spend-port person reads + per-user analytics handlers and (B) the Usage Attribution feature, with file:line evidence, tests, risks and a feasible-scope recommendation.)

**Scope correction received mid-research (owner, via coordinator), applied throughout:** the goal is NOT to remove Elasticsearch. The rule is: **no single figure may be assembled from both sources at once.** Per figure, pick exactly one engine - the ledger (`litellm_spend_hourly`) wherever it has the data for that figure, Elasticsearch, unqualified, only where the ledger cannot supply it at all. This is the per-column fallback reasoning of MR 4839's `get_projects_activity`. A field that stays 100% on ES because the ledger has nothing for it is not a finding. The analysis must state whether the existing time split at the coverage floor F (ES below F + ledger at/above F combined into one number) counts as double-sourcing.

---

## 2. Codebase Findings

### Existing Implementations

**The ledger and its projection**
- `src/codemie/repository/spend_logs_model.py:71-173` - `LiteLLMSpendLog` raw ledger: request_id, start_time, spend, call_type, model, model_group, prompt/completion/total tokens, session_id, end_user, codemie_request_id, codemie_run_id, codemie_workflow_id, codemie_origin, attr_source/form/project/category/user_id/principal/channel.
- `spend_logs_model.py:245` `PROJECTED_DIMENSIONS` and `:273-276` the hourly table: `hour`, the 11 dimensions, `spend`, `row_count`. It has **no tokens, no session_id, no codemie_request_id, no model (only model_group), no repository/branch/client, no budget_fallback_from**.
- `src/codemie/repository/spend_logs_repository.py:33-42` - windowed SUM/COUNT answered from the hourly projection where possible, edge hours and metric-store ids corrected from raw rows; gated by `SPEND_LEDGER_PROJECTION_ENABLED`.

**Scope A - the person predicates (all in `spend_logs_repository.py`)**
- `_fallback_principal_match` L231-249: `attr_source='parsed' AND attr_form='principal' AND attr_principal IN aliases`; `aliases is None` = bridge unresolved (no fallback half).
- `predicate_for_holder` L252-289: `PersonalHolder` -> `_personal_predicate` (L341-372: explicit half keyed on user id + category + a non-project, non-`N/A` `end_user`; fallback half by aliases + category); `ProjectMemberHolder` L270-279 (project + category + `attr_user_id` + charged a customer, no alias half); `PersonTotalHolder` -> `_person_total_predicate` L375-387 (`attr_user_id = id OR fallback`).
- `access_predicate` L408-448 (caller boundary, person term with `aliases=None`), `report_filter_predicate` L466-507 (dashboard `UserTerm`s -> `_person_total_predicate` with the term's aliases; personal projects -> `_personal_pot` L326-338), `composed_predicate` L524-576 (user term = person total).
- Every one of these columns (`attr_user_id`, `attr_principal`, `attr_source`, `attr_form`, `attr_project`, `attr_category`, `end_user`) is a projected dimension, so **every person predicate is fully answerable from `litellm_spend_hourly`**.

**Scope A - the cutover and the legacy below-floor path**
- `src/codemie/configs/config.py:783` `SPEND_LEDGER_COVERAGE_FLOOR = "2026-11-01T00:00:00Z"`; `:810` `SPEND_LEDGER_LEGACY_BELOW_FLOOR: bool = True`. The comment at `:802-806` on this branch still says "False - the default" (stale; MR 4839 commit 7a783f65f0 corrects it).
- `src/codemie/service/spend/composition.py:201-306` `register_spend_cutover`; `:261` returns early when the kill switch is off; `:285-286` builds `LegacySpendLedger` + `CutoverSpendLedger(legacy, current, floor)` per slot.
- `src/codemie/service/spend/cutover.py:154` `CutoverSpendLedger`; `_split` L204, `_legs` L234; docstring L15-83: below F served by the legacy engine, at/above F by the ledger, a crossing period by both, combined with `combining.combine_results` (L157). **L44-83 documents the two-clock band**: ES legs filter `@timestamp` (emission), ledger legs filter `start_time`; every call started in `[F - L, F)` whose document was emitted at/after F is either dropped by both legs or counted by both, silently, `L = SPEND_LEDGER_EMISSION_LAG_HOURS` = 24h.
- `src/codemie/service/spend/legacy_ledger.py:189` `LegacySpendLedger`; `consumed_for_holders` L245-318: **`PersonTotalHolder` passes through to the ES `DefaultSpendLedger`**; `PersonalHolder`/`ProjectHolder`/`ProjectMemberHolder`/`ProjectGroupHolder` get `SpendResult.no_figure` below F (no ES figure at all). Same rule in `default_ledger.py:455-474` (`_person_filters(user_id, aliases)` for person totals; category-keyed holders `no_figure`).
- `src/codemie/service/analytics/handlers/money_coverage.py:111-137` `pre_floor_boundary` (returns None when the kill switch is off or coverage unactivated, so the handler sends the whole window to the port); `:140-156` `split_at_floor`.

**Scope A - per-user analytics consumers, figure by figure**

| Surface | file:line | Money engine today | Same figure from two engines? |
|---|---|---|---|
| users-spending `total_cost_usd` | `user_handler.py:279-385`, holders L440-466 | Port `consumed_for_holders(PersonTotalHolder)`; below F -> ES (legacy pass-through), at/above F -> ledger | Time split only (disjoint ranges) |
| users-spending footer | `user_handler.py:86-120` `_footed_against_ledger`, `:426-437` | Port `aggregate_by_spend_group(TOTAL)`, same split | Time split only |
| users-platform-spending | `user_handler.py:553-621`, enricher L387-424 | `read_group_spend(SpendGroup.PLATFORM)`; below F legacy -> ES platform metric names | Time split only |
| users-cli-spending | `user_handler.py:623-685` | `read_group_spend(SpendGroup.CLI)`; same | Time split only |
| **users-activity** 5 money columns | `user_handler.py:786-1054`; sums L868-947; parser L976-1038 | **Elasticsearch at every period**, `use_bucket_selector=True` (L842), ES-side order on `1-bucket>1-metric` (L956), `round(..,2)`/`or 0` | No - one engine for all five columns today |
| engagement weekly spending | `engagement_handler.py:255-358`, port call L303 | Port `aggregate_by_time_bucket` by `TrafficClass`; below F only CLI answered (ES), other three absent | Time split; ASSISTANT series above F = ledger money selected by ES-resolved request ids |
| budget soft/hard limit | `budget_handler.py:140-290`, candidates L315-370, port L272 | `PersonalHolder` -> ledger; below F `no_figure` | No - ledger-only already |
| budget usage / personal budget / project members | `budget_usage_service.py:374,382`; `rest_api/routers/analytics/spending.py:99,107`; `user_management_service.py:88,120`; `member_spend_service.py:210,236,358,440` | Category-keyed holders -> ledger only; below F `no_figure` | No |
| leaderboard person total / CLI | `service/leaderboard/collector.py:1144-1196` | Port `PersonTotalHolder` / `aggregate_composed_by_traffic_class(ComposedScope(user=term))[CLI]` (one call per person) | Time split only |
| summary cards (users filter) | `summary_handler.py:95-112` | Port `aggregate_by_spend_group` | Time split only |
| platform tool | `agents/tools/platform/platform_tool.py:556,589,669` | Port, `UserTerm` | Time split only |

- Candidate identities for users-spending/platform/CLI are enumerated from ES (terms agg on `attributes.user_email`/`user_name`), money from the port; a ledger-only spender with no ES document has no row and is reported through the footer's `_partial`/`_missing` keys (`user_handler.py:86-120`). Identity rows are not money.
- `SpendService._metric_store_ids` (`spend_service.py:737-792`) resolves ES correlation ids for the ASSISTANT union class and for ASSISTANT/CONVERSATION/DATASOURCE dimension scopes, even above F. It is called by `aggregate_by_time_bucket` (L258), `aggregate_by_traffic_class` (L318), `aggregate_composed_by_traffic_class` (L527), `aggregate_by_dimension` (L457). Person-only reads (`consumed_for_holders`, `aggregate_by_spend_group[_per_filter]`) do not call it. ES then decides *which ledger rows* the class covers. It does not supply the money (`traffic_class.py:25-38` table: only ASSISTANT needs the metric store).
- MR 4839 reference pattern (`git diff main...checkpoint/EPMCDME-17194-before-es-removal`, 3 files: `config.py`, `project_handler.py` +148, `test_project_handler.py` +374): `_enrich_projects_activity` moves only `total_cost` and `cli_cost` (SpendGroup TOTAL and CLI). Each is ES below F via a `_pre_floor_money_aggs` bucket and the ledger at/above F via `read_group_spend`, combined with `combine_rendered_spend`. `assistant_cost`/`workflow_cost`/`datasource_cost` stay ES at all periods. Its own docstring flags "Mixed-engine risk (spec Risk 1). Above F a row's total_cost is the ledger's whole spend for the project while assistant_cost + workflow_cost + datasource_cost still come from Elasticsearch ... need not add up. Nothing here reconciles them, and the row carries no label for it." It also switches ES-side money ordering to `CANDIDATE_ORDER` plus Python-side ordering.

**Scope B - Usage Attribution (`src/codemie/service/usage_attribution/`)**
- Gate: `constants.py:19` `FEATURE_KEY = "usageAttribution"`; `rest_api/routers/analytics/usage_attribution.py:79-92` 404s when `features:usageAttribution` is off; admins/maintainers/auditors only.
- **No ledger read exists anywhere in the feature.** A grep for `spend_service|ledger|SpendLog|litellm_spend|hourly` across the package and router matches only "hourly budget" in `limits.py`/`analysis.py`. Sources are ES (`MetricsElasticRepository` in `handler.py:364-370,837-839,886-894,922`, raw `elasticsearch` client in `worker.py:70-74,410-416`), the app DB (`app_db.py` - shared projects, funding assignments/allocations/stops, people) and Postgres CLI analytics (`postgres_sources.py`, `framework_merge.py`).
- ES field constants `constants.py:33-58`; request bodies `queries.py`; run in the worker `worker.py:128-275` (cards A0 on the evidence window `ev_from/ev_to`; totals A1, shared CLI A6/A6b, web projects A7/A7b, web funding, web people A8, persons A2 on the money window `money_from/money_to`; values A3, tools A4, sessions A5, persons on the evidence window).

ES read inventory and what `litellm_spend_hourly` could supply:

| Read (queries.py) | Fields it needs | Hourly can supply | Hourly cannot supply |
|---|---|---|---|
| `cards_body` L219-236 (A0) | user_id/repository/project cardinality, CLI and web `money_spent`, fallback-project cardinality | CLI/web spend (via class/origin), distinct `attr_user_id` | repository, budget_fallback_from, distinct people on principal rows (aliases only) |
| `totals_body` L239-268 (A1) | money by source (cli, other_proxy, web_assistant/workflow/datasource) x who_pays (`who_pays_filters` L180-194: litellm_customer_id prefix, budget_fallback_from(+kind), shared list) | spend; customer via `end_user` (project prefix / `N/A`); CLI via `attr_category`/`codemie_origin`; workflow/embedding classes natively | `budget_fallback_from`/`_kind` -> the shared_no_budget vs personal split; assistant class needs ES ids; ES metric-name semantics of "other_proxy" |
| `web_pay_body` L321-334, `web_funding_aggs` L271-318 | web money per project x member, per funded epoch-ms interval (funding.py L15-27: "Web Platform records carry no LiteLLM customer, so who paid is rebuilt from the app DB") | spend per `attr_project`/`attr_user_id` per hour; the actual charged customer in `end_user` | exact sub-hour interval bounds from the hourly table alone (edge hours come from raw rows); fallback flag |
| `persons_body` L337-354 (A2) | per user: spend, per-repository spend x who_pays, top 3 projects, fallback projects, user_name | per-user spend (explicit id + alias match), per project, customer form | **repository** (the pairs the allocation runs on), fallback, name |
| `values_body` L357-381 (A3) | per repository: spend, people, sessions, personal/shared/fallback projects, branches, clients, models | - (no repository dimension) | everything keyed on repository; branch; client; session |
| `tools_body` L384-404 (A4), `sessions_body` L407-424 (A5) | tool names, lines, prompts, sessions, MCP, skills (no money) | nothing | all (classifier evidence) |
| `web_projects_body` L431-439, `web_fb_body` L442-454, `web_people_body` L457+ (A7, A7b, A8) | web money per project / fallback project / person, metric type, people | per project/person spend; workflow/embedding classes | fallback project; assistant split needs ES ids |
| `shared_cli_body`/`shared_cli_fb_body` L496-517 (A6) | shared-project CLI money per repository/people/client/model, fallback | per project CLI spend, model_group | repository, client, fallback; `llm_model` vs `model_group` vocabulary |
| popups `person_cli_detail_body` L520-556, `person_web_detail_body` L559-568, `project_web_detail_body` L588-607, `sessions_extra_body` L571-585 | session ids, first/last timestamps, repo/branch/client per session, prompts, skills, datasource `repo_name`, assistant/workflow names | per-person/project spend totals only | session ids, per-session anything, names, branch/client |

- Money post-processing: `money.py` (allocation by person/project, Not Charged Back L15-21: needs the per person x repository x who-pays cells from A2/A3 plus classifier output); `handler.totals` L350-399 assembles `total = cli_billed + web_total`, `charged_back = cli.charged_back + web_pay[CHARGED]` and fills `web_pay[PERSONAL]` as `web_total - sum(web_pay)` (L379).
- Classifier inputs (`classifier.py`, `identity.py` repository rules L15-27, `framework_sessions.py`): repository, tool names, sessions, MCP, skills, lines. None exist in the ledger.
- UA has no coverage-floor logic. It reads ES for every period.

### Architecture and Layers Affected

- Scope A: analytics handler layer (`user_handler.py`, and `engagement_handler.py`/`budget_handler.py` only if touched) -> `ledger_group_spend.read_group_spend` / `SpendService` port (`spend_service.py`) -> `CutoverSpendLedger` -> {`LegacySpendLedger` -> `DefaultSpendLedger` (ES) below F; `SpendLogsLedger` (`enterprise/litellm/spend_ledger.py:124`) at/above F} -> `SpendLogsRepository` -> `spend_projection` (`litellm_spend_hourly` + raw edges). Composition root: `spend/composition.py`, config `configs/config.py:772-832`. Router: `rest_api/routers/analytics/activity.py:204-216` (users-activity) -> `analytics_service.py:587-598`.
- Scope B: router `rest_api/routers/analytics/usage_attribution.py` -> `handler.py` -> worker process (`worker.py`, `fetch.py` partitioned ES aggregations with retry) -> `parsing.py` -> `analysis.py`/`money.py`/`classifier.py`/`sections.py`; cache `cache.py`.

### Integration Points

- Identity: `report_filter.py:108-109` (`UserTerm(user_id, aliases_for_user(username, email))`, or `aliases=None` for an unmatched identifier); `identity_bridge.py:101` `aliases_for_user`; `user_repository.afind_users_by_identifiers`. `UserIdentityResolver` is display-only.
- ES metric store for candidates (users tables) and for ASSISTANT correlation ids (`spend_service.py:758-772`, `dimension_scope.resolve_metric_store_ids`).
- UA: Elasticsearch metrics index (`config.ELASTIC_METRICS_INDEX`, `worker.py:416`), app DB, Postgres CLI analytics (`postgres_sources.feature_enabled` L35).

### Patterns and Conventions

- One predicate per meaning, only in `spend_logs_repository.py` (module docstring L15-21): no handler-written attribution WHERE clause.
- Absent is never zero: `render_spend(None)`, `SpendResult.no_figure`, unavailable flag below F when the legacy engine is off.
- Per-column cutover (MR 4839): below F each moved column keeps its own ES pre-floor bucket, at/above F the ledger's `SpendGroup`, combined with `combine_rendered_spend`. An empty ledger answer is not replaced by the ES one.
- `SpendGroup` (`spend_group.py:15-41`): `TOTAL = PLATFORM + CLI`, `EMBEDDING ⊂ PLATFORM` holds on the ledger side only; "Below the coverage floor, each group is the summary card's own Elasticsearch aggregation reproduced verbatim ... That side does not foot as a partition and never did."
- Per-person class split has no batched read: `aggregate_composed_by_traffic_class` one `ComposedScope` per call (`collector.py:1177-1196` docstring).

---

## 3. Documentation Findings

### Guides and Architecture Docs

`.ai-run/guides/` exists. No guide covers the spend port, the cutover or Usage Attribution (grep for `usageAttribution|litellm_spend_hourly|SPEND_LEDGER` matched only `integration/cli-analytics-storage.md`, which concerns the CLI analytics tables). Relevant general guides: `architecture/layered-architecture.md`, `data/repository-patterns.md`, `data/elasticsearch-integration.md`, `development/configuration-patterns.md`, `testing/testing-patterns.md`. UA module docstrings cite `architecture.md §2.3, §7.2-7.4` and `classification-model.md §6`. Neither file was found in this repository.

Prior task folders: `docs/superpowers/tasks/2026-11-12-analytics-money-reuse-spend-cutover/` (technical-analysis, spec, plan, code-review for MR 4839). Its analysis already lists `get_users_activity` as "the same unconverted pattern as get_projects_activity".

### Architectural Decisions

- `config.py:795-810`: the cutover reuses F. There is no second cutover date.
- `cutover.py:44-83`: the two-clock band is "documented rather than repaired", pinned in `tests/codemie/service/spend/test_cutover.py`.
- `legacy_ledger.py:245-318`: category-keyed holders have no pre-floor figure, and the person total passes through to ES.
- `traffic_class.py:25-38`: only ASSISTANT needs metric-store ids.
- `engagement_handler.py:262-273`: below F the three non-CLI series are absent rather than zero ("the owner accepted the gap over the false zero").
- `leaderboard/collector.py:1155-1158`: the person total "is the person's total across everything ... Nothing is added to it - in particular no Elasticsearch web-spend aggregate, which is what the display copy used to be summed with". This is a removed earlier instance of same-window double-sourcing.
- `postgres_sources.py:110-114`: ES and Postgres skill names "are never merged" (one source per session).

### Derived Conventions

See Section 2 Patterns. BDD-style test class names state the invariant. Long rationale docstrings. Ruff via `make ruff`.

---

## 4. Testing Landscape

### Existing Coverage

Scope A (`tests/codemie/service/analytics/handlers/`):
- `test_user_handler.py`: `TestGetUsersSpending` L87, `TestGetUsersActivity` L271 (4 tests L274-380, incl. `test_get_users_activity_aggregation_structure` L380, which pins the ES money sub-aggregations and parser), `TestGetUsersPlatformSpendingMetrics` L934, `TestUsersSpendingFromLedger` L1069, `TestUsersSpendingFooterAgainstTheLedger` L1241, `TestPlatformSpendingByUsersFromLedger` L1520, `TestCliSpendingByUsersFromLedger` L1675.
- `test_engagement_handler.py`: `TestThePortRead` L107, `TestTheRows` L154, `TestAbsenceIsNotZero` L195, `TestTheQualificationKeys` L268.
- `test_budget_handler.py`: `TestAccessBoundary` L171 through `TestResponseShape` L516; `test_budget_usage_service.py`, `test_member_spend_service.py`, `test_report_filter.py`, `test_ledger_group_spend.py`.
- `tests/codemie/service/spend/`: `test_legacy_ledger.py` (`TestACategoryKeyedHolderHasNoFigure` L176, `TestThePersonTotalPassesThrough` L260, `TestAPersonalHolderIsNeverProbedWithAPersonTotal` L320, `TestTheCliShareAndThePersonTotalCannotDoubleCount` L672), `test_cutover.py` (two-clock band both directions), `test_cutover_registration.py` (`TestTheKillSwitch` L182, `TestAnUnactivatedCoverageIsNotSplitAtAll` L222, `TestTheMixedConfigurationCountsEveryPreFloorRowOnce` L326), `test_identity_bridge.py`, `test_batched_holder_reads.py`, `test_access_boundary.py`, `test_spend_group*.py`, `test_traffic_class.py`, `test_default_ledger.py`.
- `tests/codemie/repository/`: `test_spend_logs_repository*.py`, `test_spend_projection.py`, `test_spend_logs_holder_access_boundary.py`.
- Router/contract: `tests/codemie/rest_api/routers/test_analytics_capability_guards.py`, OpenAPI baseline `tests/codemie/rest_api/router_guards/baselines/openapi.json`.
- MR 4839's projects-activity tests (+374 lines in `test_project_handler.py`) exist only on the checkpoint tag, not on this branch.

Scope B (`tests/codemie/service/usage_attribution/`, 16 files, about 4.8k lines): `test_handler.py` (`TestPeopleChargeback` L98, `TestWebWhoPaysInTotals` L386, `test_totals_are_computed_and_cached` L432, `test_totals_charge_back_the_funded_web_platform_spend` L476), `test_funding.py` (`TestWebFundingRequest` L136), `test_sections_and_queries.py`, `test_pipeline.py`, `test_worker.py`, `test_analysis_service.py`, `test_framework_sessions.py`, `test_framework_wiring.py`, `test_person_recent_sessions.py`, `test_identity.py`, `test_people_*`, `test_settings.py`; fixtures `builders.py`, `fixtures/repository_normalization_vectors.json`; router `tests/codemie/rest_api/routers/test_usage_attribution.py` (380 lines).

### Testing Framework and Patterns

pytest (`pytest.ini`: `--import-mode=importlib -n 3`, `pythonpath = src`, async function-scoped loops). Handler tests mock `MetricsElasticRepository`, patch `<handler module>.ledger_coverage` / `spend_service` / `read_group_spend` and `config.SPEND_LEDGER_LEGACY_BELOW_FLOOR` (helper `_cutover_at` pattern in `test_project_handler.py`). UA tests feed scripted ES aggregation responses through `builders.py`. Per project rules, tests are written or run only on explicit request. None were run for this research.

### Coverage Gaps

- `get_users_activity` money: only structure/parse tests. There is no floor, port, access-scope or report-filter test, and no test that the five columns foot.
- No cross-surface test that users-activity `total_cost` agrees with users-spending `total_cost_usd` for one person and window (they come from different engines above F today).
- No test pinning that the ASSISTANT class/series depends on ES-resolved ids above F, beyond spend-package unit tests.
- No parity test of the ledger vs ES for any UA figure. UA has no ledger path to test.

---

## 5. Configuration and Environment

### Environment Variables

`configs/config.py`: `SPEND_LEDGER_COVERAGE_FLOOR` L783 (default `2026-11-01T00:00:00Z`), `SPEND_LEDGER_IDENTITY_FLOOR` L787 (empty), `SPEND_LEDGER_READY` L790, `SPEND_LEDGER_EMISSION_LAG_HOURS` L795 (24), `SPEND_LEDGER_MAX_METRIC_IDS` L798, `SPEND_LEDGER_LEGACY_BELOW_FLOOR` L810 (True, global, not per surface), `SPEND_LEDGER_PROJECTION_ENABLED` L816 (True). UA: `USAGE_ATTRIBUTION_ES_CONCURRENCY` L196, `USAGE_ATTRIBUTION_ANALYSIS_TIMEOUT_SECONDS` L197.

### Configuration Files

- `config/customer/customer-config.yaml` (locally modified in the working tree) - `features:usageAttribution` component resolved in `usage_attribution/handler.py:128`.
- Alembic: `src/external/alembic/versions/c7f15537b2c3_create_litellm_spend_hourly.py` (trigger-maintained projection).

### Feature Flags and Deployment Concerns

- `SPEND_LEDGER_LEGACY_BELOW_FLOOR` is read at startup (`composition.py:261`) and per request (`money_coverage.py:135`). Flipping it affects every port consumer and every split handler (project/assistant/workflow too), not only person reads. With it off, sub-F time reports as unavailable, not zero.
- `features:usageAttribution` gates the whole UA family (404 when off).
- Local data (`litellm_csv/litellm_spend_hourly` export, the DB was not queried): 14,914 rows, $3,910.77, hours 2026-09-24 07:00 to 2026-11-12 16:00. By spend: parsed/principal/no user id 78.8%; explicit/principal/user id 8.3%; parsed/project_scoped/user id 7.0%; explicit/project_scoped 5.4%; unattributed 0.4%. Of the $3,080.67 principal-only spend: about $2,396 email-like principals, $516 other principals, and **$168.39 (4.3% of all spend) whose `attr_principal` is a JSON blob (`device_id`/`account_uuid`/`session_id`)** that no alias set can match. `litellm_spend_logs` export is empty (0 bytes).

---

## 6. Risk Indicators

- **The time split at F is not double-sourcing in the owner's sense, with one documented exception.** Every port figure crossing F is `[start, F)` from ES plus `[F, end)` from the ledger, combined by `combine_results`. The ranges are disjoint, so no instant is read from two engines. The exception is the two-clock band (`cutover.py:44-83`): each call started in `[F-24h, F)` and emitted at or after F is silently dropped or counted by both engines, and the figure reads exact. This is the only place where one call can reach a single figure from both engines. Owner decision needed on whether it is acceptable.
- **Same-window blends found in per-user reads: none on this branch.** users-activity is ES-only across all five columns. Every other person figure is port-only (time split) or ledger-only.
- Speculative: converting users-activity the MR 4839 way (total_cost + cli_cost to the ledger, assistant/workflow/datasource kept on ES) creates exactly the cross-column inconsistency the owner names: above F, `total_cost` is the ledger's whole person spend while the parts are ES metric-name sums, so total != sum of parts with no label. ES `total_cost` today is the union of four metric names (`user_handler.py:920-947`), not all of a person's spend.
- Speculative: moving the three part columns to the ledger instead needs a per-person class split (`aggregate_composed_by_traffic_class`, one call per person, no batch). The ASSISTANT class above F selects ledger rows by ES correlation ids (`spend_service.py:737-792`), and class semantics differ from ES metric names (`codemie_origin`, generator/analysis origins land in no ES group). Pre-F, the legacy engine answers only the CLI class, so the three parts would be absent below F.
- Ordering and pagination: users-activity orders and paginates in ES on money (`use_bucket_selector=True`, `order 1-bucket>1-metric`). Any ledger column moves ordering to Python over the candidate cap. MR 4839 did the same for projects (`CANDIDATE_ORDER`).
- Identity coverage: 78.8% of local ledger spend is principal-only. A per-person ledger figure includes it only through `aliases_for_user(username, email)`. The $168.39 of JSON-blob principals and any principal that is neither username nor email belong to no person. No `identity_bridge` portion fires for them, because the alias set is resolved. ES keys those calls on `attributes.user_id`/`user_email`, so moving a per-person figure to the ledger can lower it with no label.
- `SPEND_LEDGER_LEGACY_BELOW_FLOOR=False` (the literal "ledger-only" reading of scope A) is global: it blanks sub-F money on project/assistant/workflow/summary surfaces too. Lowering F (local hourly starts 2026-09-24) is the alternative lever, but only if the load is complete from that instant (`config.py:779-782`).
- `SpendGroup` cards do not foot below F (`spend_group.py:37-40`). For a window crossing F, `TOTAL = PLATFORM + CLI` holds only on the ledger leg. This is a cross-card inconsistency created by the time split, not a single-figure blend.
- **Usage Attribution has no double-sourced money figure: it never reads the ledger.** Under the corrected rule nothing there is a finding. Each ES field the ledger lacks (repository, branch, client, session, fallback, tools, names) stays on ES legitimately.
- Speculative: moving any one UA money figure to the ledger would itself create the forbidden blend. `totals` builds `total = cli_billed + web_total` and `charged_back = cli.charged_back + web_pay[CHARGED]` (`handler.py:375-385`). A3/A6 money is keyed on repository, which the ledger lacks. A2 per-person spend must equal the sum of its repository pairs for `money.py` allocation, and the shared_no_budget/personal split needs `budget_fallback_from`. A ledger CLI total beside ES who-pays, repository or person cells would not foot.
- UA web who-pays semantics differ: UA rebuilds who paid from app-DB funding intervals (`funding.py:15-27`), while the ledger's `end_user` records the customer actually charged. If they disagree, that is a reconciliation question, not a migration.
- Adjacent, non-money, non-ledger: UA's framework-session count is one figure built from ES and Postgres (`framework_sessions.py:208-242`, `min(sessions, max(ES, Postgres))`). It matches the shape of the owner's rule if that rule extends beyond money.
- Stale comment `config.py:802-806` on this branch. MR 4839 fixes it, and the fix is not on this branch.
- No guide documents the spend port, the cutover or UA. The UA design docs it cites (`architecture.md`, `classification-model.md`) are not in the repository.

---

## 7. Summary for Complexity Assessment

Under the owner's corrected rule (one engine per figure, ledger preferred, ES only where the ledger has nothing), the evidence narrows the work sharply. Scope A's person predicates (`attr_user_id` OR parsed `attr_principal IN aliases`, `spend_logs_repository.py:231-387,466-507`) are all expressible on `litellm_spend_hourly`. Every per-user money figure already goes through the spend port except users-activity: users-spending total and footer, platform, CLI, leaderboard, summary, engagement, plus the ledger-only budget/personal/project-member holders. The port's ES-below-F/ledger-at-or-above-F combination tiles disjoint time ranges and is not a same-window blend. Its one same-call exposure is the documented 24h two-clock band at F (`cutover.py:44-83`), which needs an explicit owner call. Category-keyed holders (budget soft/hard limit, personal budgets, project members) are already ledger-only, with `no_figure` below F.

The one per-user surface the rule actually bites is `UserHandler.get_users_activity` (`user_handler.py:786-1054`), the twin of MR 4839's projects-activity. It has five ES-only money columns, ES-side money ordering and no floor, port, access or report-filter handling, and only structure tests. The ledger can supply `total_cost` and `cli_cost` (SpendGroup TOTAL/CLI). The three part columns have no batched per-person ledger equivalent, and the ASSISTANT one depends on ES ids. Moving only two columns reproduces MR 4839's own "Mixed-engine risk": total != sum of parts above F with no label. That is the inconsistency the owner names, so the per-column choice needs a spec decision (move all five via the per-person class split, move two and label, or leave all five on ES). Layers: one analytics handler plus port consumption. The spend package, repository and config likely need no change. The test-surface additions mirror MR 4839's.

Scope B (Usage Attribution) reads no ledger at all, so it contains no double-sourced figure, and under the corrected rule it is not a finding. Most UA money is keyed on fields the hourly table lacks (repository, branch, client, session, `budget_fallback_from`), and its totals and allocations must foot against those cells. Moving any single UA money figure to the ledger would create the forbidden blend. Recommendation: keep UA wholly on ES and treat it as out of scope. Confine EPMCDME-17194 to users-activity under the per-figure rule. Record the two-clock band and principal-only identity coverage (78.8% of local spend, 4.3% JSON-blob principals no alias can match) as risks for any per-person ledger figure.

---

## 8. External References

None named by the task outside this repository. In-repo references the task named, all resolved:
- Git tag `checkpoint/EPMCDME-17194-before-es-removal` -> `7a783f65f0` ("EPMCDME-17194: Correct the stale default in the legacy below-floor kill switch comment"). `main..tag` holds 3 commits (469881b62f, 637a85e065, 7a783f65f0) touching `src/codemie/configs/config.py` (12 lines), `src/codemie/service/analytics/handlers/project_handler.py` (+148), and `tests/codemie/service/analytics/handlers/test_project_handler.py` (+374). Key quoted fact (project_handler `_enrich_projects_activity` docstring on the tag): "Per-column cutover, not per-row. Only these two of the five money columns move to the ledger ... `assistant_cost`, `workflow_cost` and `datasource_cost` stay on Elasticsearch whatever the window, because the ledger has no like-for-like figure for them. An empty ledger answer is never replaced by the Elasticsearch one ... Above F a row's total_cost is the ledger's whole spend for the project while assistant_cost + workflow_cost + datasource_cost still come from Elasticsearch ... need not add up. Nothing here reconciles them, and the row carries no label for it."
- The task's "about 77% of local spend has no attr_user_id" was checked against the `litellm_csv/litellm_spend_hourly` export only (78.8% parsed/principal with no user id). The database was not queried.
