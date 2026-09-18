# Technical Research

**Task**: routing analytics litellm spend savings
**Generated**: 2026-09-14T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-14888 — Routing Analytics & Savings

Story: As a platform owner evaluating routing, I want analytics showing how models were switched and how much routing saved, built on consolidated LiteLLM spend data, so I can decide whether to make routing the default for agents and pilot projects using numbers that match actual spend.

Background:
Routing metadata exists per response — routed model, requested model, tier, decision source, confidence, classifier usage and signals — attached to conversation thoughts. Analytics expose model, tokens, cost, efficiency metrics today but contain no routing fields. Routing metadata is not carried into the analytics storage layer. EPMCDME-13429 makes LiteLLM the single source of truth for per-call cost and tokens. Routing metadata must be associable with LiteLLM spend log records to attribute routed cost and overhead.

Acceptance Criteria:
Data foundation:
- Routing dimensions (routed model, requested model, tier, decision source, classifier usage) are associated with LiteLLM spend records and queryable
- Cost/token values come from consolidated LiteLLM spend data (not recalculated internally)
- Requests missing routing metadata are excluded or flagged rather than silently counted as non-routed

Analytics surface:
- Number of routed sessions and requests shown for selected period/scope
- Timeline of model switches for a selected routed session
- Distribution of routing tiers and decision sources
- Classifier/judge overhead cost reported separately from primary model cost
- CLI report has same routing metrics and savings figures as platform UI

Savings:
- Savings = actual routed cost (from consolidated spend) minus counterfactual cost of the more expensive model in same route, including routing overhead
- Savings figures reconcile against total spend; savings never exceed actual routed spend + counterfactual baseline

Existing changes are on branch EPMCDME-14083-routing-metrics but this research is against main.

Related work items:
- docs/superpowers/work-items/routing-analytics-savings.md (if present)
- EPMCDME-13429: LiteLLM as spend source of truth
- EPMCDME-14213: Reconciling existing analytics cost with LiteLLM

---

## 2. Codebase Findings

### Existing Implementations

**Routing metadata value objects and extractors:**
- `src/codemie/core/routing_info.py` — `RoutingInfo` (Pydantic BaseModel, the canonical per-response routing value object) carries only `routed_model: str | None`, `routed_model_label: str | None`, `classifier_cost_usd: float | None`. Also defines `RoutingHeaderCodec`, `RoutingMetadataExtractor` protocol, `compose_routing_info`, and `default_routing_extractors`.
- `src/codemie/enterprise/switchyard/routing_meta.py` — `SwitchyardMeta` dataclass carries the full switchyard field set: `routed_model`, `requested_model`, `tier`, `decision_source`, `confidence`, `classifier_model`, `classifier_input/output/cached/cache_creation_tokens`, `classifier_cost_usd`, `classifier_p_solve`, `classifier_crux`, `classifier_primary_rule`, `classifier_capability_boundary`, and six signal floats (`signal_score`, `signal_confidence`, `signal_severity`, `signal_spinning`, `signal_exploring`, `signal_production`).
- `src/codemie/enterprise/litellm/litellm_router_meta.py` — `LiteLLMRouterMeta` dataclass carries: `tier`, `cause`, `score`, `routed_model`, `classifier_model`, `router_model_name`, `router_type`, `signals`, `escalated`, `escalation_keyword`, `classifier_prompt/completion/total_tokens`, `classifier_cost_usd`.
- `src/codemie/enterprise/switchyard/extractor.py` — `SwitchyardRoutingExtractor` extracts a partial `RoutingInfo` from `_switchyard_routing` response metadata key; strips down to only `routed_model` and `classifier_cost_usd`.
- `src/codemie/enterprise/litellm/routing_headers.py` — `LiteLLMRouterExtractor` reads `x-litellm-router-*` and `x-litellm-classifier-*` response headers; again maps to partial `RoutingInfo` (only `routed_model` and `classifier_cost_usd`).

**Callback layer — where routing info enters the per-run record:**
- `src/codemie/agents/callbacks/tokens_callback.py` — `TokensCalculationCallback.on_llm_end` reads proxy cost, classifier usage from headers, builds `RoutingInfo(routed_model=..., classifier_cost_usd=...)` and stores it on an `LLMRun`.
- `src/codemie/service/request_summary_manager.py` — `LLMRun` holds `routing: RoutingInfo | None`. `RequestSummary.calculate()` aggregates all per-run routings into a single `TokensUsage.routing` (last non-None `routed_model`, sum of `classifier_cost_usd` across runs).

**Domain model — routing on thoughts:**
- `src/codemie/chains/base.py` — `Thought` model includes `routing: Optional[RoutingInfo] = None` (the only persistent routing carrier on domain objects).

**Analytics storage layer — ES metrics:**
- `src/codemie/service/monitoring/conversation_monitoring_service.py` — `ConversationMonitoringService.send_conversation_metric` writes to the `conversation_assistant_usage` ES event. The attributes dict includes `user_id`, `user_name`, `user_email`, `assistant_id`, `assistant_name`, `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `money_spent`, `cached_tokens_money_spent`, `project`, `execution_time`, `llm_model`, `conversation_id`, `status`, and optionally `request_id`. **No routing fields are written.**
- `src/codemie/service/monitoring/metrics_constants.py` — `MetricsAttributes` class defines 50+ attribute keys; none are routing-related (`routed_model`, `tier`, `decision_source`, `requested_model`, `classifier_cost_usd` are all absent).
- `src/codemie/repository/metrics_elastic_repository.py` — `MetricsElasticRepository` queries index `ELASTIC_METRICS_INDEX = "codemie_metrics_logs*"` via async Elasticsearch client with ES|QL and aggregation APIs.

**Analytics service — query and handler layers:**
- `src/codemie/service/analytics/analytics_service.py` — `AnalyticsService` facade with lazy-loaded handlers: `SummaryHandler`, `AssistantHandler`, `WorkflowHandler`, `ToolsHandler`, `UserHandler`, `ProjectHandler`, `CLIHandler`, `CLIInsightsHandler`, `BudgetHandler`, `WebhookHandler`, `MCPHandler`, `LLMHandler`, `EmbeddingsHandler`, `EngagementHandler`, `LeaderboardHandler`. No `RoutingHandler` exists.
- `src/codemie/service/analytics/metric_names.py` — `MetricName` enum has `CONVERSATION_ASSISTANT_USAGE`, `CLI_LLM_USAGE_TOTAL` (= `codemie_litellm_proxy_usage`), `LLM_PROXY_REQUESTS_TOTAL`, and others; no routing-specific metric names.
- `src/codemie/service/analytics/query_pipeline.py` — `AnalyticsQueryPipeline` standard flow: parse time → `SecureQueryBuilder` → aggregation → `ResponseFormatter`.
- `src/codemie/service/analytics/handlers/llm_handler.py` — `LLMHandler.get_llms_usage` groups by `attributes.llm_model.keyword` across `CONVERSATION_ASSISTANT_USAGE`, `CLI_TOOL_USAGE_TOTAL`, `CLI_AGENT_USAGE_TOTAL`, `LLM_PROXY_REQUESTS_TOTAL`.

**LiteLLM spend data:**
- `src/codemie/service/spend_tracking/spend_models.py` — `ProjectSpendTracking` (SQLModel, table=`project_spend_tracking`) aggregates per-project spend with columns: `project_name`, `key_hash`, `spend_date`, `daily_spend`, `cumulative_spend`, `budget_period_spend`, `budget_id`, `budget_category`, `user_id`, `provider_subject_id`, `spend_subject_type`. **No per-call routing dimensions; no `routed_model`, `requested_model`, or `tier` columns.**
- `src/codemie/repository/project_spend_tracking_repository.py` — `ProjectSpendTrackingRepository` provides batched read/write operations against `project_spend_tracking`.
- `src/codemie/enterprise/litellm/client.py` — `get_llm_proxy_client()` returns an `httpx.AsyncClient` against `config.LITE_LLM_URL`. No spend-log querying methods on the backend; spend data arrives via the callback/metric pipeline.
- `src/codemie/enterprise/litellm/litellm_custom_callbacks.py` — `AutorouterCallback` writes routing decision fields and classifier token headers to the proxy HTTP response (outbound headers `x-litellm-router-*`, `x-litellm-classifier-*`). The proxy itself records spend via LiteLLM's internal spend log, but there is no backend-side retrieval of per-call LiteLLM spend log records from a database.

**CLI analytics (separate data path):**
- `src/codemie/rest_api/routers/cli_analytics.py` — CLI analytics endpoints hit ClickHouse (`ch_query`) via `LocalAnalyticsRepository`, not Elasticsearch.
- `src/codemie/repository/cli_analytics_repository.py` — queries ClickHouse `codemie_analytics` tables.

**Analytics REST router:**
- `src/codemie/rest_api/routers/analytics.py` — exposes existing analytics endpoints; no routing analytics endpoints exist.

### Architecture and Layers Affected

| Layer | Components |
|---|---|
| Domain / value-object | `RoutingInfo` (routing_info.py), `SwitchyardMeta`, `LiteLLMRouterMeta` |
| Callback / instrumentation | `TokensCalculationCallback`, `RequestSummaryManager`, `LLMRun` |
| Monitoring / ingestion | `ConversationMonitoringService`, `MetricsAttributes` |
| Repository | `MetricsElasticRepository` (reads), `ProjectSpendTrackingRepository` (reads) |
| Service / handler | `AnalyticsService`, new `RoutingHandler` |
| API / router | `analytics.py` router |
| Storage | Elasticsearch `codemie_metrics_logs*`, PostgreSQL `project_spend_tracking` |
| CLI | `cli_analytics.py` router, ClickHouse `codemie_analytics` |

### Integration Points

- `RoutingInfo` ← extracted from `SwitchyardMeta` / `LiteLLMRouterMeta` via `compose_routing_info` and `default_routing_extractors`.
- `Thought.routing` ← set via `langgraph_event_adapter.py` from LangGraph events.
- `LLMRun.routing` ← set by `TokensCalculationCallback.on_llm_end`.
- `TokensUsage.routing` ← aggregated by `RequestSummary.calculate()`.
- `conversation_assistant_usage` ES event ← emitted by `ConversationMonitoringService.send_conversation_metric` (routing fields absent here).
- LiteLLM proxy spend log ← records per-call cost/tokens inside LiteLLM's own storage (MySQL/PostgreSQL depending on deployment); this is the EPMCDME-13429 authoritative source referenced by the task.
- `project_spend_tracking` PostgreSQL table ← aggregated spend snapshots polled from LiteLLM API; no per-call granularity.
- ClickHouse `codemie_analytics` ← CLI session telemetry; separate from ES metrics.

### Patterns and Conventions

- Analytics handlers extend no shared base class for routing; the pattern for a new handler is to add a private `_routing_handler_instance` to `AnalyticsService` with a lazy `@property`, then delegate from new public methods.
- Metric attributes are string constants in `MetricsAttributes`; adding routing fields follows the same pattern.
- ES aggregations follow `AggregationBuilder` / `SecureQueryBuilder` / `AnalyticsQueryPipeline` composable pipeline.
- `RoutingHeaderCodec` mixin provides `to_headers` / `from_headers` for any routing dataclass. New dimensions can be added to `RoutingInfo` following its `merged_over` / `is_empty` pattern.
- Response shapes: `SummariesResponse` for scalar widget rows, `TabularResponse` for paginated table results — both via `ResponseFormatter`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/data/elasticsearch-integration.md` — covers ES repository access patterns and index setup conventions. Relevant: "Keep Elasticsearch details inside repository or service classes."
- `.ai-run/guides/architecture/layered-architecture.md`, `.ai-run/guides/architecture/service-layer-patterns.md` — govern handler and service structure (not read fully; referenced in AGENTS.md).
- `.ai-run/guides/testing/testing-patterns.md` — pytest testing conventions.
- No guide covers routing analytics specifically.

### Architectural Decisions

- `routing_info.py` module docstring documents: "This module depends on neither the switchyard nor the litellm package, so both can import RoutingInfo / RoutingMetadataExtractor from here without an import cycle. Concrete extractors live in their owning packages; the composer is assembled by the caller."
- `litellm_custom_callbacks.py` module docstring documents the correlation flow between AutorouterCallback's classifier sub-call and the outer request (TTLCache, single worker constraint, process-locality guarantee).
- `tokens_callback.py` comment: "DISPLAY routing (UI badge) is derived from the canonical composer, which intentionally excludes a plain proxy `model` — unlike `billed_model` above, which feeds the COST path and must reflect the actual billed model."

### Derived Conventions

- `RoutingInfo` is the only routing carrier surfaced beyond the extractor layer. Richer `SwitchyardMeta` / `LiteLLMRouterMeta` fields (`requested_model`, `tier`, `decision_source`, signal scores) are deliberately stripped during extraction — only `routed_model` and `classifier_cost_usd` survive into `RoutingInfo`.
- `MetricsAttributes` string constants are the canonical field names used in both the write path (monitoring services) and the read path (analytics handler aggregations). New fields must be added there before they can be queried.
- Analytics handlers receive `User` and `MetricsElasticRepository` and use `AnalyticsQueryPipeline` for all ES queries. They do not directly access PostgreSQL spend tables.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/enterprise/test_routing_info.py` — unit tests for `RoutingInfo.merged_over`, `is_empty`, `compose_routing_info`.
- `tests/enterprise/switchyard/test_routing_meta.py` — tests `SwitchyardMeta.from_dict` and header codec.
- `tests/enterprise/switchyard/test_extractor.py` — tests `SwitchyardRoutingExtractor.extract`.
- `tests/enterprise/litellm/test_routing_headers.py` — tests `LiteLLMRouterExtractor` and `LiteLLMRouterMeta`.
- `tests/codemie/service/test_request_summary_manager.py` — covers `RequestSummary.calculate()` routing rollup.
- `tests/codemie/rest_api/routers/test_analytics.py` — tests existing analytics endpoints.
- `tests/codemie/repository/test_metrics_elastic_repository.py` — tests ES repository.

### Testing Framework and Patterns

- pytest with async (`pytest-asyncio`); fixtures in `tests/conftest.py`.
- Mocks for `MetricsElasticRepository` with patched return values; no real ES instance in unit tests.
- Enterprise code tested in `tests/enterprise/`; unit tests in `tests/unit/`; router tests in `tests/codemie/rest_api/routers/`.

### Coverage Gaps

- No tests for routing dimensions flowing through `ConversationMonitoringService.send_conversation_metric`.
- No tests for routing aggregation in any analytics handler (no routing handler exists yet).
- No tests for savings calculation logic (no such logic exists).
- No tests for association of routing metadata with LiteLLM spend records.
- No tests for "requests missing routing metadata are excluded or flagged."
- No tests for CLI analytics routing metrics parity with platform UI.

---

## 5. Configuration and Environment

### Environment Variables

- `ELASTIC_METRICS_INDEX` — default `"codemie_metrics_logs*"` — the ES index pattern for all analytics metrics.
- `LLM_PROXY_ENABLED` — default `False` — gates all LiteLLM proxy-specific code paths (classifier usage headers, proxy cost extraction).
- `LLM_PROXY_TRACK_USAGE` — default `True` — if False, proxy cost and classifier usage extraction is skipped in `TokensCalculationCallback`.
- `LITE_LLM_URL` — default `""` — base URL for the httpx LiteLLM proxy client.
- `LLM_PROXY_TIMEOUT` — configures the proxy client timeout.
- `METRICS_ROTATION_ENABLED` — default `False` — quarterly ES index rotation; affects which index names exist.

### Configuration Files

- `src/codemie/configs/config.py` — single Pydantic settings class with all env vars above.
- `litellm_config.yaml` (repo root) — LiteLLM proxy configuration, including model routing tables; content not read in this research.

### Feature Flags and Deployment Concerns

- `LLM_PROXY_ENABLED=False` disables the entire routing header extraction path. Any new routing analytics code that depends on classifier cost or `x-litellm-router-*` headers is a no-op when this flag is false.
- `METRICS_ROTATION_ENABLED` changes ES index naming; routing analytics queries must use the same wildcard pattern already in use (`codemie_metrics_logs*`).
- No feature flag specifically gates routing analytics; one may be needed for phased rollout.

---

## 6. Risk Indicators

- **Routing fields are stripped before analytics.** `RoutingInfo` holds only `routed_model` and `classifier_cost_usd`. The full dimensions the task requires (`requested_model`, `tier`, `decision_source`, signal scores, `confidence`) exist in `SwitchyardMeta` and `LiteLLMRouterMeta` but are not promoted into `RoutingInfo` or written to any analytics store. Promoting these fields requires changing `RoutingInfo`, both extractors, `TokensCalculationCallback`, `RequestSummary.calculate`, and the ingestion path.
- **ES event write path has no routing fields.** `ConversationMonitoringService.send_conversation_metric` and `MetricsAttributes` have no routing attributes. All analytics aggregations that query `codemie_metrics_logs*` will find zero routing fields on existing documents; there is no backfill path described.
- **EPMCDME-13429 is a prerequisite, not yet complete on main.** The task specifies cost/token values must come from consolidated LiteLLM spend data. The current `ProjectSpendTracking` table is aggregated (daily snapshots per key/budget), not per-call. There is no mechanism to join per-call LiteLLM spend records with routing metadata from the backend side.
- **Savings formula requires a counterfactual model.** Calculating "what the expensive model would have cost" requires: (a) knowing the counterfactual model identity for each routed request (the `requested_model` field — currently not in `RoutingInfo`) and (b) pricing lookup for that model for the same token count. Neither infrastructure exists on main.
- **CLI analytics is on a separate data path (ClickHouse).** CLI metrics go through `codemie_analytics` ClickHouse tables, not `codemie_metrics_logs*` ES. Routing metrics parity between CLI and platform UI (acceptance criterion) requires routing fields to reach ClickHouse as well, or a separate aggregation strategy.
- **No routing handler or routing endpoints exist.** The analytics service has no `RoutingHandler`; `AnalyticsService` has no routing methods; no REST endpoints exist. The full handler/service/router surface must be created from scratch.
- **LLM_PROXY_ENABLED=False on many deployments.** All classifier overhead cost and LiteLLM router metadata is gated behind this flag. Routing analytics that depend on classifier cost will show zero on deployments where only Switchyard (non-LiteLLM router) or no routing is active.
- **Speculative:** Association between ES metric records and LiteLLM spend log records will likely require a shared correlation key (e.g., `request_id`). The ES `conversation_assistant_usage` event already emits `request_id` as an optional attribute, but it must be confirmed as the same ID present in LiteLLM spend logs.

---

## 7. Summary for Complexity Assessment

The feature requires work across six layers: the routing value-object model (`RoutingInfo`), the extractor layer (both `SwitchyardRoutingExtractor` and `LiteLLMRouterExtractor`), the callback/instrumentation layer (`TokensCalculationCallback`, `RequestSummaryManager`), the monitoring/ingestion layer (`ConversationMonitoringService`, `MetricsAttributes`), the analytics handler/service layer (new `RoutingHandler` on `AnalyticsService`), and the REST API layer (new routing endpoints). None of the routing dimensions the ticket requires — `requested_model`, `tier`, `decision_source`, classifier token counts — are carried beyond the extractor layer today; promoting them through the full stack to ES storage is a multi-file, multi-layer change.

The savings calculation is the most technically novel piece. No counterfactual cost logic exists anywhere in the codebase. It requires: (a) enriching `RoutingInfo` with `requested_model` so the "expensive model" identity is known, (b) a pricing lookup for that counterfactual model, (c) a reconciliation guarantee that savings never exceed actual spend. This is new domain logic with no existing test framework. The prerequisite work (EPMCDME-13429, making LiteLLM the authoritative cost source per call) is not on `main`; until it lands, cost values will continue to come from the internal calculation path and the "consolidated spend" acceptance criterion cannot be fully satisfied.

Test coverage gaps are significant: no tests exist for routing fields flowing through the monitoring write path, for routing-based ES aggregations, or for savings calculations. The testing surface will be proportional to the new handler/service depth. The CLI analytics parity requirement adds a second data path (ClickHouse) that must also carry routing metadata, which is a distinct integration concern from the ES path used by the platform UI.

---

## 8. External References

- `docs/superpowers/work-items/routing-analytics-savings.md` — named by the task; **does not exist** at this path in the repository on `main`. No content could be sourced.
- EPMCDME-13429 and EPMCDME-14213 are referenced as related Jira tickets; they are not files in this repository and could not be read. Their significance per the task context: EPMCDME-13429 establishes LiteLLM as the single authoritative per-call cost/token source; EPMCDME-14213 reconciles existing analytics cost with that LiteLLM source. Both are described as in-progress or planned work against `main`.
