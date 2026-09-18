# Spec: EPMCDME-14888 — Routing Analytics & Savings

**Date:** 2026-09-14
**Ticket:** EPMCDME-14888
**Complexity:** XL (score 28) — 3 slices, delivered sequentially
**Blocked:** Slice 3 is hard-blocked on EPMCDME-13429 landing on main.

---

## Overview

Platform owners need routing analytics to decide whether to promote routing as the default for
agents and pilot projects. Today, routing metadata (`requested_model`, `tier`, `decision_source`,
`confidence`, classifier token counts) exists in `SwitchyardMeta` and `LiteLLMRouterMeta` but is
deliberately stripped before reaching `RoutingInfo`, so nothing is written to Elasticsearch and
nothing is queryable.

This spec defines three sequential slices to close that gap. Routing dimensions are written as a
**dedicated `routing_call_usage` ES event**, separate from `conversation_assistant_usage`, following
the established codebase pattern of per-domain metric names sharing the `codemie_metrics_logs*`
index (see `MetricName` enum — datasource, CLI, LLM-proxy, workflow events all coexist this way).
`conversation_assistant_usage` documents are not modified.

Routing events must be emitted from **both** call paths where routing decisions are made:

- The **conversation/agent path** (`ConversationMonitoringService.send_conversation_metric` call
  site), where routing arrives via `TokensUsage.routing`.
- The **LiteLLM proxy path** (`/v1/messages`, `/chat/completions`, `/v1/response`, and all other
  endpoints registered in `config.LITE_LLM_PROXY_ENDPOINTS`), where routing arrives via
  `SwitchyardMeta` (from `apply_switchyard_proxy_routing`) and `x-litellm-router-*` response
  headers.

- **Slice 1** — Extend `RoutingInfo` and emit `routing_call_usage` events from both paths.
- **Slice 2** — `RoutingHandler` with ES aggregations against `routing_call_usage`, wired into `AnalyticsService`, exposed via new REST endpoints.
- **Slice 3** — Counterfactual savings calculation and a savings endpoint (conditional on EPMCDME-13429).

CLI ClickHouse parity is out of scope for this ticket (deferred to a separate ticket).

---

## Non-Goals

- Adding routing fields to `conversation_assistant_usage` ES events or to `MetricsAttributes`
  constants for that event type.
- CLI ClickHouse routing metrics or savings parity (deferred to a separate ticket).
- Backfilling routing fields into historical ES documents.
- SwitchyardMeta signal float fields (`signal_score`, `signal_confidence`, `signal_severity`,
  `signal_spinning`, `signal_exploring`, `signal_production`) — not required by this ticket.
- EPMCDME-13429 implementation itself — this ticket consumes its output; it does not implement it.
- Changes to the LiteLLM proxy configuration or `litellm_config.yaml`.
- A new feature flag to gate routing analytics; deployments with `LLM_PROXY_ENABLED=False` will
  naturally receive empty/zero results from routing aggregations.
- Routing events for non-streaming proxy responses or error responses — the proxy's usage-tracking
  path (`LLM_PROXY_TRACK_USAGE`) only executes for successfully completed streaming responses, and
  routing event emission follows the same gate.

---

## Slice 1 — RoutingInfo Extension and Separate Routing Event

### RoutingInfo changes (`src/codemie/core/routing_info.py`)

Add the following optional fields to `RoutingInfo`, all defaulting to `None`:

```python
requested_model: str | None = None
tier: str | None = None
decision_source: str | None = None
confidence: float | None = None
classifier_input_tokens: int | None = None
classifier_output_tokens: int | None = None
classifier_cached_tokens: int | None = None
```

Update `merged_over` to forward these fields (last non-None wins for scalars; sum for token
integers, matching the existing pattern for `classifier_cost_usd`). Update `is_empty` to account
for the new fields. Existing tests in `tests/enterprise/test_routing_info.py` must be extended.

### Extractor changes

- `SwitchyardRoutingExtractor` (`src/codemie/enterprise/switchyard/extractor.py`): populate the new
  `RoutingInfo` fields from the corresponding `SwitchyardMeta` fields rather than discarding them.
- `LiteLLMRouterExtractor` (`src/codemie/enterprise/litellm/routing_headers.py`): map
  `x-litellm-router-tier`, `x-litellm-router-cause` (→ `decision_source`),
  `x-litellm-router-score` (→ `confidence`), and `x-litellm-classifier-{prompt,completion,total}_tokens`
  to the new fields.

### Callback and aggregation (conversation/agent path)

`TokensCalculationCallback.on_llm_end` requires no structural change; the richer `RoutingInfo`
from the updated extractors flows into `LLMRun.routing` automatically.

`RequestSummary.calculate()` (`request_summary_manager.py`): update the routing rollup to call
`merged_over` across all per-run `RoutingInfo` values, replacing the current manual
last-non-None / sum logic.

### New MetricName constant

Add to `MetricName` enum (`src/codemie/service/analytics/metric_names.py`):

```python
ROUTING_CALL_USAGE = "routing_call_usage"
```

### New RoutingMonitoringService (`src/codemie/service/monitoring/routing_monitoring_service.py`)

A new `RoutingMonitoringService(BaseMonitoringService)` with a single class method:

```python
send_routing_metric(
    user: User,
    routing: RoutingInfo,
    conversation_id: str | None,
    assistant_id: str | None,
    project: str,
    request_id: str | None = None,
    endpoint: str | None = None,
) -> None
```

It calls `send_count_metric(name="routing_call_usage", attributes={...})` with the routing
dimensions plus the correlation fields (`user_id`, `user_email`, `conversation_id`, `assistant_id`,
`project`, `request_id`, `endpoint`). It is a no-op when `routing.is_empty()`. The `endpoint`
attribute distinguishes conversation-path events (None / omitted) from proxy-path events
(`/v1/chat/completions`, `/v1/messages`, etc.) without requiring a separate metric name.

### Wiring — conversation/agent path

The call site that today calls `ConversationMonitoringService.send_conversation_metric` should also
call `RoutingMonitoringService.send_routing_metric` when `tokens_usage.routing` is not None and not
empty. `conversation_assistant_usage` documents are not modified.

### Wiring — LiteLLM proxy path

Routing metadata is available in the proxy path from two sources:
1. `switchyard_routing_meta: SwitchyardMeta | None` — produced by `apply_switchyard_proxy_routing()`
   in `_proxy_to_llm_proxy()` (`src/codemie/enterprise/litellm/proxy_router.py`) and already passed
   to `_streaming_response_with_usage_tracking()`.
2. `x-litellm-router-*` response headers — available in `downstream_response.headers` inside
   `_finalize_stream_usage_tracking()`.

The emission point is `_finalize_stream_usage_tracking()`, alongside the existing call to
`LLMProxyMonitoringService.track_usage()`. To make both sources available at that point:

- `_streaming_response_with_usage_tracking()` should forward `routing_meta` to
  `_finalize_stream_usage_tracking()` as an additional parameter (the function signature and the
  single call site both change).
- Inside `_finalize_stream_usage_tracking()`, compose a `RoutingInfo` from:
  - `SwitchyardRoutingExtractor` applied to `routing_meta` (if present), and
  - `LiteLLMRouterExtractor` applied to `response_headers`.
  using `compose_routing_info` / `default_routing_extractors` with a synthetic response object or
  direct extractor calls.
- When the composed `RoutingInfo` is not empty, add a background task for
  `RoutingMonitoringService.send_routing_metric(...)` with `request_id`, `session_id` (as
  `conversation_id`), and `endpoint` populated from `request_info`.

This emission is gated on `config.LLM_PROXY_TRACK_USAGE` (the existing guard on
`_finalize_stream_usage_tracking`), consistent with all other proxy-path metric emission.

Requests without routing metadata (neither Switchyard meta nor LiteLLM router headers) produce no
`routing_call_usage` event and are excluded from routing aggregations by document absence.

---

## Slice 2 — RoutingHandler and REST Endpoints

### RoutingHandler (`src/codemie/service/analytics/handlers/routing_handler.py`)

New handler wired into `AnalyticsService` as a lazy `@property routing_handler`. Uses
`MetricsElasticRepository` and `AnalyticsQueryPipeline` following the existing handler convention.
All aggregations filter on `metric_name = MetricName.ROUTING_CALL_USAGE` within `codemie_metrics_logs*`.

| Method | Output | ES operation |
|---|---|---|
| `get_routing_summary` | session count, request count, total classifier overhead cost | cardinality + sum aggregations |
| `get_model_switches` | ordered timeline for a single session by `conversation_id` | terms + date-histogram |
| `get_tier_distribution` | count per `tier` value | terms aggregation |
| `get_decision_source_distribution` | count per `decision_source` value | terms aggregation |
| `get_classifier_overhead` | classifier cost and tokens broken out from primary cost | sum aggregations |

Response shapes follow existing conventions: `SummariesResponse` for scalar rows,
`TabularResponse` for paginated per-session results.

### REST endpoints (`src/codemie/rest_api/routers/analytics.py`)

New routes added to the existing analytics router:

```
GET /analytics/routing/summary
GET /analytics/routing/sessions
GET /analytics/routing/sessions/{session_id}/switches
GET /analytics/routing/tier-distribution
GET /analytics/routing/decision-source-distribution
GET /analytics/routing/classifier-overhead
```

All endpoints share the standard `period`, `scope`, and pagination query parameters already used
by existing analytics endpoints.

---

## Slice 3 — Counterfactual Savings (conditional on EPMCDME-13429)

**This slice must not be started until EPMCDME-13429 is merged to main.** The savings formula
requires per-call cost from the LiteLLM consolidated spend source that EPMCDME-13429 establishes.

### SavingsHandler (`src/codemie/service/analytics/handlers/savings_handler.py`)

Contains a `ModelPricingRegistry` — initially config-driven (a dict mapping model identifiers to
`{input_price_per_token, output_price_per_token}`). The pricing registry source will be confirmed
once EPMCDME-13429 defines how per-call pricing is surfaced from the LiteLLM proxy.

Savings formula per request (applied against `routing_call_usage` events joined to LiteLLM
per-call spend records via `request_id`):

```
counterfactual_cost = (input_tokens × requested_model_input_price)
                    + (output_tokens × requested_model_output_price)
savings = max(0, counterfactual_cost − actual_routed_cost_incl_classifier_overhead)
```

Reconciliation guardrail: aggregate savings for any query window must not exceed the aggregate
counterfactual baseline for the same window — total savings are capped at `sum(counterfactual_cost)`
to prevent savings figures from exceeding what routing theoretically could have saved.

The join key between `routing_call_usage` ES events and LiteLLM per-call spend records is
`request_id`. The exact join mechanism must be confirmed once EPMCDME-13429 is available.

### REST endpoint

```
GET /analytics/routing/savings
```

Returns aggregate savings, total routed cost, total counterfactual cost, and classifier overhead
as a `SummariesResponse` for the requested period and scope.

---

## Acceptance Criteria

**Slice 1 (data foundation):**
- `RoutingInfo` carries `requested_model`, `tier`, `decision_source`, `confidence`, and classifier
  token fields; `merged_over` and `is_empty` handle them correctly.
- Both extractors populate the new fields; existing extractor unit tests pass; new cases are covered.
- A `routing_call_usage` ES event is emitted for each conversation/agent request where routing was
  active, carrying routing dimensions and correlation fields (`conversation_id`, `request_id`).
- A `routing_call_usage` ES event is emitted for each proxy-endpoint request (`/v1/messages`,
  `/chat/completions`, `/v1/response`, and equivalent endpoints) where routing was active and
  `LLM_PROXY_TRACK_USAGE=True`, carrying routing dimensions, `request_id`, and `endpoint`.
- `conversation_assistant_usage` documents are unchanged — no routing fields added to that event type.
- Requests (from either path) with no routing metadata produce no `routing_call_usage` event and
  are excluded from routing aggregations by document absence.

**Slice 2 (analytics surface):**
- Routed session count and request count are queryable for a selected period and scope.
- Model-switch timeline is available for a selected session via `conversation_id`.
- Tier and decision-source distributions are exposed as distinct endpoints.
- Classifier overhead cost is reported separately and does not double-count primary model cost.
- All new endpoints return empty/zero results on deployments where `LLM_PROXY_ENABLED=False`.

**Slice 3 (savings — conditional on EPMCDME-13429):**
- Savings = actual routed cost minus counterfactual cost of the requested (more expensive) model
  for the same token count, including routing overhead.
- Aggregate savings do not exceed aggregate counterfactual baseline for the same query window.
- Savings endpoint returns a reconciled figure sourced from consolidated LiteLLM per-call spend.

---

## Open Risks

- **request_id correlation (Slice 3):** Whether `request_id` on `routing_call_usage` events matches
  the LiteLLM spend-log call identifier is speculative until EPMCDME-13429 lands and can be
  inspected. If the IDs differ, the Slice 3 join strategy must be redesigned.
- **ModelPricingRegistry source (Slice 3):** EPMCDME-13429 may surface pricing via a LiteLLM API
  endpoint; the registry implementation may need to become a proxy query rather than a config dict.
- **ES index backfill:** `routing_call_usage` events will not exist for traffic before Slice 1 is
  deployed. Routing analytics will show zero for historical traffic.
- **Deployments with LLM_PROXY_ENABLED=False:** All routing fields rely on LiteLLM router or
  Switchyard headers. Analytics return empty results on such deployments; acceptable and documented
  as a non-goal.
- **Proxy non-streaming and error paths emit no routing events:** `_finalize_stream_usage_tracking()`
  is only reached for successfully completed streaming responses with `LLM_PROXY_TRACK_USAGE=True`.
  Non-streaming proxy responses and error responses do not emit `routing_call_usage` events. Routing
  analytics will undercount proxy requests on deployments or model APIs that respond non-streaming.
- **compose_routing_info integration in proxy path:** The existing `compose_routing_info` and
  `default_routing_extractors` functions operate on LangChain response objects and response
  metadata dicts. Reusing them directly in `_finalize_stream_usage_tracking()` may require a
  thin adapter or direct extractor calls to handle the raw `httpx.Response` headers dict from
  the proxy path; this is a localized implementation detail to resolve during planning.
