# Technical Research

**Task**: analytics workflows aggregation elasticsearch insights
**Generated**: 2026-10-26
**Research path**: codegraph

---

## 1. Original Context

Ticket EPMCDME-11348 (MR codemie-ui !2176, Sort Insights chats and workflows rows). Fix: the Insights Workflows table order is wrong across pages. In src/codemie/service/analytics/handlers/workflow_handler.py, _build_workflows_aggregation (around line 128) orders the terms aggregation by {"_count": "desc"}, which counts metric documents (one per LLM call), while the table's Total Runs column is the cardinality of attributes.execution_id. Change the terms ordering so the global order is Total Runs (unique_executions) DESC with deterministic tiebreak on workflow name ASC, so every page slice follows the same global order Kibana uses. Keep other sub-aggregations unchanged. Add or update unit tests under tests/ for the workflows aggregation ordering. Run ruff and the relevant tests. Do not touch codemie-ui.

---

## 2. Codebase Findings

### Existing Implementations
- `codemie/src/codemie/service/analytics/handlers/workflow_handler.py`
  - `WorkflowHandler` (class, line 36). Constructs `AnalyticsQueryPipeline(user, repository)` (line 41).
  - `get_workflows` (lines 43-77) calls `self._pipeline.execute_tabular_query(...)` with `group_by_field=WORKFLOW_NAME_KEYWORD_FIELD` and `use_bucket_selector=True` (line 76).
  - `_build_workflows_aggregation(query, fetch_size)` (lines 79-141). Builds a terms agg on `WORKFLOW_NAME_KEYWORD_FIELD` via `AggregationBuilder.build_terms_agg`, with `order={"_count": "desc"}` at line 128. Sub-aggs (lines 89-122): `unique_executions` (cardinality on `attributes.execution_id.keyword`), `successful` / `failed` / `aborted` (filter on `attributes.status.keyword` + cardinality `unique_count`), `total_cost` (sum of `attributes.money_spent`), `total_tokens` (painless script), `avg_time`, `unique_users` (cardinality on `USER_NAME_KEYWORD_FIELD`), `last_run` (max of `@timestamp`). The body is `{"query", "size": 0, "aggs": {"paginated_results": terms_agg}}`.
  - `_parse_workflows_result` (lines 143-195). `total_runs = bucket["unique_executions"]["value"]` (line 152), so the Total Runs column is the distinct execution_id count, not `doc_count`.
  - `get_top_workflow_usage` (lines 214-249) and `_build_top_workflow_usage_aggregation` (lines 251-306). The latter uses `order={"1-bucket": "desc"}` (line 298), i.e. ordering by a filter sub-aggregation's doc count.
- `codemie/src/codemie/service/analytics/aggregation_builder.py`
  - `AggregationBuilder.build_terms_agg(group_by_field, fetch_size, order, sub_aggs, script=None)` (lines 30-71). `order` is typed `dict[str, str]` and placed verbatim into `terms.order` (line 68). Raises `ValueError` when `fetch_size > 10000` (lines 53-58).
  - `slice_buckets_for_page` (lines 149-166) slices a pre-sorted bucket list; it does not sort.
- `codemie/src/codemie/service/analytics/query_pipeline.py`
  - `AnalyticsQueryFilters` (lines 42-54) and `AnalyticsQueryPipeline` (line 57).
  - `execute_tabular_query` (lines 104-205). With `use_bucket_selector=True` (lines 151-157): one request with `agg_builder(query, 10000)`, optional `totals_aggs` injected, `totals_result = data_result`. With `False` (lines 158-177): data query at `(page+1)*per_page` plus a sibling `total_buckets` cardinality, and a separate totals query at size 10000 unless `totals_aggs` is given. Then `total_count = len(data_buckets)` when `use_bucket_selector` (line 185), otherwise the cardinality value (line 188). Page rows are taken by `slice_buckets_for_page` (line 191).
  - `execute_tabular_query_with_flattened_rows` (lines 237-344) sorts in Python with `sort_keys`; it does not use terms ordering.
- `codemie/src/codemie/service/analytics/analytics_service.py`
  - `get_top_workflow_usage` (lines 1033-1054) delegates to `self._workflow_handler`. `get_workflows` also delegates to the workflow handler (its body was not read in full).
- `codemie/src/codemie/rest_api/routers/analytics/usage.py`
  - `get_top_workflow_usage` router (lines 320-337) builds `AnalyticsService(user)` and calls the service.

### Architecture and Layers Affected
- Router: `rest_api/routers/analytics/usage.py` (workflow endpoints).
- Service: `service/analytics/analytics_service.py` (`AnalyticsService`, lazy `_workflow_handler`).
- Handler: `service/analytics/handlers/workflow_handler.py` (`WorkflowHandler`).
- Shared pipeline: `service/analytics/query_pipeline.py` (`AnalyticsQueryPipeline.execute_tabular_query`).
- Shared aggregation helper: `service/analytics/aggregation_builder.py` (`AggregationBuilder`, 28 callers of `build_terms_agg` across handlers).
- Persistence: `MetricsElasticRepository.execute_aggregation_query` (used by the pipeline; not read in this research).

### Integration Points
- Elasticsearch metrics index, queried through `MetricsElasticRepository`. Index fields used: `attributes.execution_id.keyword`, `attributes.workflow_name.keyword`, `attributes.status.keyword`, `attributes.money_spent`, `attributes.user_id.keyword`, `@timestamp`, `metric_name.keyword`.
- Kibana is named in the ticket as the source of the target order. It is outside this repository; its definition was not examined.
- Client-side consumer: codemie-ui Insights tab (out of scope for this task per ticket).

### Patterns and Conventions
- Fetch-and-slice: the handler builds an ES terms agg whose `order` defines global order; the pipeline slices pages from the returned buckets.
- Terms aggregation is wrapped as `paginated_results`; per-metric sub-aggs are named `unique_executions`, `total_cost`, etc.
- Other handlers order by a sub-aggregation key with a single criterion and no tiebreak: `budget_handler.py` uses `order={"max_spent": "desc"}` (lines 84, 149). Many handlers use `{"_count": "desc"}` (for example `cli/handler.py` lines 506, 1083, 1223).
- Repository guidance (`.ai-run/guides/data/elasticsearch-integration.md`): ES query bodies belong in repository or service classes, not routers.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/data/elasticsearch-integration.md`: keep ES details in repository or service classes; index setup belongs in startup or dedicated services. Read in this research.
- Other `.ai-run/guides` files were listed (for example `data/database-optimization.md`, `testing/testing-service-patterns.md`) but not read.

### Architectural Decisions
- No ADR or decision record covering analytics terms ordering was located during this research.
- Inline docstring in `_build_workflows_aggregation` (lines 80-85) records that cardinality is HyperLogLog++ (approximate above `precision_threshold`, default 3000, exact below).

### Derived Conventions
- Single-key `order` dicts are the house style in `build_terms_agg` callers. No existing caller passes a multi-criteria order in this scan.

---

## 4. Testing Landscape

### Existing Coverage
- `codemie/tests/codemie/service/analytics/test_query_pipeline.py`: covers `execute_tabular_query` (for example `test_execute_tabular_query_calls_components_in_correct_order`, `test_execute_tabular_query_bucket_selector_computes_totals_from_data_result`).
- `codemie/tests/codemie/service/analytics/test_analytics_service.py`: delegation tests, including `test_get_workflows_delegates_with_date_range`.
- `codemie/tests/codemie/rest_api/routers/test_analytics.py`: `TestWorkflowsEndpoint` (line 525), `test_workflows_endpoint_basic` (line 530).
- `codemie/tests/codemie/service/analytics/handlers/test_budget_handler.py`: includes `test_build_budget_hard_limit_aggregation`, which calls the private builder directly and inspects the body. This is the closest pattern for an ordering test.
- No test file matching `*workflow*handler*` exists under `codemie/tests/` (Glob result). No test asserts the `order` of the workflows terms aggregation.

### Testing Framework and Patterns
- pytest, class-based test groups, `unittest.mock` (`MagicMock`, `@patch` on module paths), and a `mock_user` fixture in `test_analytics_service.py` (line 45). Framework versions were not read from `pyproject.toml`.

### Coverage Gaps
- `WorkflowHandler._build_workflows_aggregation` ordering: no test.
- `WorkflowHandler._parse_workflows_result`: no test found in this scan.

---

## 5. Configuration and Environment

### Environment Variables
- `ELASTIC_URL`, `ELASTIC_USERNAME`, `ELASTIC_PASSWORD` used by `ElasticSearchClient.get_client` (`codemie/src/codemie/clients/elasticsearch.py` lines 37-44).
- `retrieval_available(config)` gate in `ElasticSearchClient.is_configured` (line 30).

### Configuration Files
- None specific to workflow analytics were identified in this research.

### Feature Flags and Deployment Concerns
- No feature flag gating the workflows aggregation was found in the explored symbols. Deployment manifests were not examined.

---

## 6. Risk Indicators

1. Cardinality ordering is approximate: `unique_executions` is HyperLogLog++ (`workflow_handler.py` lines 82-85). Counts above the precision threshold are approximate, so the order can differ slightly from exact distinct counts.
2. Tie order is unspecified: many workflows can share a count (for example 1), and `build_terms_agg` has no tiebreak. An explicit secondary `_key` ascending criterion is needed for deterministic page slices.
3. Speculative: `build_terms_agg` types `order` as `dict[str, str]` (`aggregation_builder.py` line 33). A two-criteria order needs the ES array form, which means widening a helper with 28 callers or building the order locally in `workflow_handler.py`.
4. `use_bucket_selector=True` path: the workflows request uses `agg_builder(query, 10000)` in one call (`query_pipeline.py` line 153) and `total_count = len(data_buckets)` (line 185). Slicing is correct only if ES returns the full ordered list up to 10000. The flag name is misleading: the workflows aggregation has no `bucket_selector`.
5. Terms ordering by a sub-aggregation is shard-approximate: low risk at `size=10000`, but not zero.
6. Kibana parity is unverified: the target order is defined outside this repo and Kibana's metric definition was not checked.
7. Test gap: a unit test can assert only the request body; actual ES order needs a live cluster.
8. Scope: `get_top_workflow_usage` orders by `{"1-bucket": "desc"}` (`workflow_handler.py` line 298), a different table not changed by fixing `_build_workflows_aggregation`.
9. Unverified: the "one document per LLM call" premise and the metric filters in `get_workflows` (lines 43-77) were not read in full.

---

## 7. Summary for Complexity Assessment

The workflows table is produced by one handler method, `WorkflowHandler._build_workflows_aggregation` (`workflow_handler.py` lines 79-141), whose terms order sits at line 128. Shared dependencies are `AggregationBuilder.build_terms_agg` (28 callers) and `AnalyticsQueryPipeline.execute_tabular_query` (9 callers). Technical novelty is low: the handler already uses fetch-and-slice and sub-aggregation metrics. Coverage posture is weak: no workflow-handler test file exists and no test asserts the workflows terms order.

Key risk factors are approximate cardinality ordering, unspecified tie order without an explicit tiebreak, the single-criterion `dict[str, str]` order type of the shared helper, and unverified parity with Kibana's order.

---

## 8. External References

None named by the task as a source of truth. The task mentions GitLab MR codemie-ui !2176 as context only; no facts were required from it and it was not read.
