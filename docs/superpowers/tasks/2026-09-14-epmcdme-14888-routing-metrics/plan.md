# EPMCDME-14888 — Routing Analytics & Savings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Emit `routing_call_usage` ES events from both the conversation path and the LiteLLM proxy path, expose routing analytics via six new REST endpoints, and scaffold (but defer) counterfactual savings.

**Architecture:** A new `RoutingMonitoringService(BaseMonitoringService)` writes `routing_call_usage` documents to `codemie_metrics_logs*`. A new `RoutingHandler` reads them via `MetricsElasticRepository` + `AnalyticsQueryPipeline`. `conversation_assistant_usage` documents are never modified. A deferred `SavingsHandler` (Slice 3) is blocked on EPMCDME-13429.

**Tech Stack:** Python 3.11+, FastAPI, async Elasticsearch (aggregation API), pytest-asyncio, SQLModel.

Commit per task using the repository's existing convention.

## Global Constraints

- `routing_call_usage` is a new `MetricName`; `conversation_assistant_usage` is not changed — no routing fields added to that event or to `MetricsAttributes`.
- CLI ClickHouse path is out of scope (deferred to a separate ticket). Do not touch `cli_analytics.py` or `cli_analytics_repository.py`.
- Signal float fields (`signal_score`, `signal_confidence`, `signal_severity`, `signal_spinning`, `signal_exploring`, `signal_production`) are excluded from `RoutingInfo`.
- Proxy-path routing events are gated on `config.LLM_PROXY_TRACK_USAGE` and emitted only for successfully-completed streaming responses.
- No new feature flag; deployments with `LLM_PROXY_ENABLED=False` return empty/zero routing results by document absence.
- Slice 3 (`SavingsHandler`) must not be started until EPMCDME-13429 is merged to `main`.
- **Before writing any code:** inspect branch `EPMCDME-14083-routing-metrics` for partial implementations that overlap with Tasks 1–4; do not duplicate or regress that work.

---

### Task 1: Extend RoutingInfo + update both extractors [Slice 1]

**Files:**
- Modify: `src/codemie/core/routing_info.py`
- Modify: `src/codemie/enterprise/switchyard/extractor.py`
- Modify: `src/codemie/enterprise/litellm/routing_headers.py`
- Extend tests: `tests/enterprise/test_routing_info.py`, `tests/enterprise/switchyard/test_extractor.py`, `tests/enterprise/litellm/test_routing_headers.py`

**Interfaces:**
- Produces: `RoutingInfo` with fields `requested_model: str | None`, `tier: str | None`, `decision_source: str | None`, `confidence: float | None`, `classifier_input_tokens: int | None`, `classifier_output_tokens: int | None`, `classifier_cached_tokens: int | None`; `merged_over` sums the three integer token fields (additive, matching `classifier_cost_usd`) and forwards scalars last-non-None; `is_empty()` covers all fields.

**Test-first: yes — tests assert `merged_over` sums classifier token integers across multiple `RoutingInfo` instances; `SwitchyardRoutingExtractor` populates all seven new fields from `SwitchyardMeta`; `LiteLLMRouterExtractor` maps `x-litellm-router-tier`, `x-litellm-router-cause` → `decision_source`, `x-litellm-router-score` → `confidence`, `x-litellm-classifier-{prompt,completion,total}_tokens`.**

- [ ] Add failing tests for new fields and updated `merged_over`/`is_empty` in `tests/enterprise/test_routing_info.py`.
- [ ] Run: `pytest tests/enterprise/test_routing_info.py -v` — expect FAIL.
- [ ] Add seven fields to `RoutingInfo` (after line with `classifier_cost_usd`); update `merged_over` and `is_empty` accordingly:
  ```python
  requested_model: str | None = None
  tier: str | None = None
  decision_source: str | None = None
  confidence: float | None = None
  classifier_input_tokens: int | None = None
  classifier_output_tokens: int | None = None
  classifier_cached_tokens: int | None = None
  ```
- [ ] Run: `pytest tests/enterprise/test_routing_info.py -v` — expect PASS.
- [ ] Add failing extractor tests for new field population (Switchyard and LiteLLM extractor tests).
- [ ] Run both extractor test files — expect FAIL.
- [ ] In `extractor.py`: populate all seven new `RoutingInfo` fields from the corresponding `SwitchyardMeta` fields instead of discarding them.
- [ ] In `routing_headers.py`: map the four header groups to the new `RoutingInfo` fields; cast score to `float`, token values to `int`.
- [ ] Run: `pytest tests/enterprise/ -v` — expect all PASS.

---

### Task 2: RequestSummary rollup + MetricName constant [Slice 1]

**Files:**
- Modify: `src/codemie/service/request_summary_manager.py`
- Modify: `src/codemie/service/analytics/metric_names.py`
- Extend tests: `tests/codemie/service/test_request_summary_manager.py`

**Interfaces:**
- Produces: `MetricName.ROUTING_CALL_USAGE = "routing_call_usage"` (consumed by Tasks 3, 5, 6).

**Test-first: yes — test asserts that `RequestSummary.calculate()` over a 3-run fixture produces correct summed classifier tokens and last-non-None `tier` via `merged_over`, not manual field-by-field logic.**

- [ ] Add failing test for multi-run `merged_over` rollup in `tests/codemie/service/test_request_summary_manager.py`.
- [ ] Run: `pytest tests/codemie/service/test_request_summary_manager.py -v` — expect FAIL.
- [ ] Replace the manual last-non-None / sum loop in `RequestSummary.calculate()` with `functools.reduce(lambda a, b: a.merged_over(b), per_run_routings, RoutingInfo())`.
- [ ] Add to `MetricName` enum in `metric_names.py`: `ROUTING_CALL_USAGE = "routing_call_usage"`.
- [ ] Run: `pytest tests/codemie/service/test_request_summary_manager.py -v` — expect PASS.

---

### Task 3: RoutingMonitoringService + conversation/agent path wiring [Slice 1]

**Files:**
- Create: `src/codemie/service/monitoring/routing_monitoring_service.py`
- Modify: `src/codemie/service/conversation_service.py:296–305`
- Test: `tests/codemie/service/monitoring/test_routing_monitoring_service.py`

**Interfaces:**
- Consumes: `RoutingInfo` (Task 1), `MetricName.ROUTING_CALL_USAGE` (Task 2), `BaseMonitoringService.send_count_metric`.
- Produces: `RoutingMonitoringService.send_routing_metric(*, user, routing, conversation_id, assistant_id, project, request_id=None, endpoint=None) -> None`.

**Test-first: yes — tests assert `send_routing_metric` calls `send_count_metric(name="routing_call_usage", attributes={...})` with all routing dimensions present, and is a no-op when `routing.is_empty()`.**

- [ ] Write failing tests:
  ```python
  def test_noop_when_empty():
      with patch.object(RoutingMonitoringService, "send_count_metric") as m:
          RoutingMonitoringService.send_routing_metric(
              user=mock_user, routing=RoutingInfo(),
              conversation_id=None, assistant_id=None, project="p",
          )
          m.assert_not_called()

  def test_emits_routing_dimensions():
      ri = RoutingInfo(routed_model="haiku", tier="haiku",
                       requested_model="opus", decision_source="classifier",
                       confidence=0.92, classifier_cost_usd=0.0001)
      with patch.object(RoutingMonitoringService, "send_count_metric") as m:
          RoutingMonitoringService.send_routing_metric(
              user=mock_user, routing=ri, conversation_id="c1",
              assistant_id="a1", project="p1", request_id="r1",
          )
          attrs = m.call_args.kwargs["attributes"]
          assert attrs["routed_model"] == "haiku"
          assert attrs["decision_source"] == "classifier"
          assert attrs["request_id"] == "r1"
  ```
- [ ] Run — expect FAIL.
- [ ] Create `routing_monitoring_service.py`:
  ```python
  from codemie.core.routing_info import RoutingInfo
  from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService

  class RoutingMonitoringService(BaseMonitoringService):
      @classmethod
      def send_routing_metric(
          cls, *, user, routing: RoutingInfo,
          conversation_id: str | None, assistant_id: str | None,
          project: str, request_id: str | None = None,
          endpoint: str | None = None,
      ) -> None:
          if routing.is_empty():
              return
          cls.send_count_metric(
              name="routing_call_usage",
              attributes={
                  "user_id": str(user.id), "user_email": user.email,
                  "project": project, "conversation_id": conversation_id,
                  "assistant_id": str(assistant_id) if assistant_id else None,
                  "request_id": request_id, "endpoint": endpoint,
                  "routed_model": routing.routed_model,
                  "requested_model": routing.requested_model,
                  "tier": routing.tier,
                  "decision_source": routing.decision_source,
                  "confidence": routing.confidence,
                  "classifier_cost_usd": routing.classifier_cost_usd,
                  "classifier_input_tokens": routing.classifier_input_tokens,
                  "classifier_output_tokens": routing.classifier_output_tokens,
                  "classifier_cached_tokens": routing.classifier_cached_tokens,
              },
          )
  ```
- [ ] Run tests — expect PASS.
- [ ] In `conversation_service.py`, after `ConversationMonitoringService.send_conversation_metric(...)` (line ~304), add:
  ```python
  if tokens_usage.routing and not tokens_usage.routing.is_empty():
      RoutingMonitoringService.send_routing_metric(
          user=user, routing=tokens_usage.routing,
          conversation_id=conversation.conversation_id,
          assistant_id=str(assistant.id),
          project=str(assistant.project_name or ""),
          request_id=request_id,
      )
  ```
- [ ] Run: `pytest tests/codemie/service/ -v` — expect PASS.

---

### Task 4: Proxy path routing event emission [Slice 1]

**Files:**
- Modify: `src/codemie/enterprise/litellm/proxy_router.py:1054–1065` (signature of `_finalize_stream_usage_tracking`)
- Modify: `src/codemie/enterprise/litellm/proxy_router.py:1117–1140` (routing emit block inside function)
- Modify: `src/codemie/enterprise/litellm/proxy_router.py:1221–1231` (call site — add `routing_meta` kwarg)
- Test: `tests/codemie/enterprise/litellm/test_proxy_router.py` (create if absent)

**Interfaces:**
- Consumes: `SwitchyardRoutingExtractor`, `LiteLLMRouterExtractor`, `RoutingMonitoringService.send_routing_metric` (Task 3). The `routing_meta: SwitchyardMeta | None` parameter already exists on `_streaming_response_with_usage_tracking`; it is not yet forwarded to `_finalize_stream_usage_tracking`.

**Test-first: yes — tests assert that `RoutingMonitoringService.send_routing_metric` is added as a `background_tasks` entry when `routing_meta` is provided or LiteLLM router headers are present; not added when neither is present.**

- [ ] Write failing tests patching `RoutingMonitoringService.send_routing_metric` and inspecting `background_tasks.add_task` calls.
- [ ] Run — expect FAIL.
- [ ] Add `routing_meta: "SwitchyardMeta | None" = None` to the `_finalize_stream_usage_tracking` keyword-only signature.
- [ ] After the `LLMProxyMonitoringService.track_usage` `background_tasks.add_task(...)` block inside `_finalize_stream_usage_tracking`, add:
  ```python
  # Emit routing metric (proxy path)
  _switchyard_routing = (
      SwitchyardRoutingExtractor().extract({"_switchyard_routing": routing_meta})
      if routing_meta else RoutingInfo()
  )
  _litellm_routing = LiteLLMRouterExtractor().extract(response_headers)
  proxy_routing = _switchyard_routing.merged_over(_litellm_routing)
  if not proxy_routing.is_empty():
      background_tasks.add_task(
          RoutingMonitoringService.send_routing_metric,
          user=user,
          routing=proxy_routing,
          conversation_id=session_id,
          assistant_id=request_info.get("assistant_id"),
          project=request_info.get("project", ""),
          request_id=request_id,
          endpoint=endpoint,
      )
  ```
  Verify that `SwitchyardRoutingExtractor.extract` accepts the dict wrapper form `{"_switchyard_routing": meta}` — if it expects the raw `SwitchyardMeta`, call it directly.
- [ ] Update the call site at `proxy_router.py:1221–1231` to pass `routing_meta=routing_meta`.
- [ ] Run: `pytest tests/codemie/enterprise/litellm/ -v` — expect PASS.

---

### Task 5: RoutingHandler with ES aggregations [Slice 2]

**Prerequisite:** Slice 1 tasks (1–4) merged; `routing_call_usage` events flowing.

**Files:**
- Create: `src/codemie/service/analytics/handlers/routing_handler.py`
- Test: `tests/codemie/service/analytics/handlers/test_routing_handler.py`

**Interfaces:**
- Consumes: `MetricsElasticRepository`, `AnalyticsQueryPipeline`, `MetricName.ROUTING_CALL_USAGE`.
- Produces: `RoutingHandler(repo, pipeline)` with methods `get_routing_summary`, `get_model_switches`, `get_tier_distribution`, `get_decision_source_distribution`, `get_classifier_overhead` — all returning `SummariesResponse` or `TabularResponse`.

**Test-first: yes — each method tested with a mocked `MetricsElasticRepository`; assertions cover `metric_name=routing_call_usage` filter, response type, and non-error empty-result handling.**

- [ ] Write failing tests for all five `RoutingHandler` methods.
- [ ] Run — expect FAIL.
- [ ] Create `routing_handler.py` following the pattern of `llm_handler.py`. All queries filter `metric_name = MetricName.ROUTING_CALL_USAGE.value`:
  - `get_routing_summary`: cardinality on `attributes.conversation_id.keyword` (session count) + doc count (request count) + sum of `attributes.classifier_cost_usd`.
  - `get_model_switches`: date-histogram + terms on `attributes.routed_model.keyword`, filtered to `attributes.conversation_id.keyword = session_id`.
  - `get_tier_distribution`: terms on `attributes.tier.keyword`.
  - `get_decision_source_distribution`: terms on `attributes.decision_source.keyword`.
  - `get_classifier_overhead`: sum of `attributes.classifier_cost_usd`, `attributes.classifier_input_tokens`, `attributes.classifier_output_tokens`.
- [ ] Run: `pytest tests/codemie/service/analytics/handlers/test_routing_handler.py -v` — expect PASS.

---

### Task 6: Wire RoutingHandler into AnalyticsService + REST endpoints [Slice 2]

**Files:**
- Modify: `src/codemie/service/analytics/analytics_service.py`
- Modify: `src/codemie/rest_api/routers/analytics.py`
- Extend tests: `tests/codemie/rest_api/routers/test_analytics.py`

**Interfaces:**
- Consumes: `RoutingHandler` (Task 5).
- Produces: six new routes on the analytics router.

**Test-first: yes — 6 endpoint tests assert HTTP 200 and correct response schema; one test per empty-result case (zero rows, not 404/500).**

- [ ] Add failing tests for all six routes.
- [ ] Run — expect FAIL.
- [ ] In `analytics_service.py`: add `_routing_handler_instance: RoutingHandler | None = None` and a lazy `@property routing_handler` following the existing `_llm_handler_instance` / `llm_handler` pattern.
- [ ] In `analytics.py` router, add six routes delegating to `analytics_service.routing_handler`:
  ```
  GET /analytics/routing/summary                     → get_routing_summary(period, scope)
  GET /analytics/routing/sessions                    → get_model_switches (listing form, period, scope)
  GET /analytics/routing/sessions/{session_id}/switches → get_model_switches(session_id, period)
  GET /analytics/routing/tier-distribution           → get_tier_distribution(period, scope)
  GET /analytics/routing/decision-source-distribution → get_decision_source_distribution(period, scope)
  GET /analytics/routing/classifier-overhead         → get_classifier_overhead(period, scope)
  ```
  Use the same `period`, `scope`, and pagination query-parameter pattern as adjacent endpoints.
- [ ] Run: `pytest tests/codemie/rest_api/routers/test_analytics.py -v` — expect PASS.

---

### Task 7: [DEFERRED — do not start until EPMCDME-13429 is merged to main] SavingsHandler + savings endpoint [Slice 3]

**Files:**
- Create: `src/codemie/service/analytics/handlers/savings_handler.py`
- Modify: `src/codemie/service/analytics/analytics_service.py`
- Modify: `src/codemie/rest_api/routers/analytics.py`
- Test: `tests/codemie/service/analytics/handlers/test_savings_handler.py`

**Interfaces:**
- Consumes: per-call LiteLLM spend records joined to `routing_call_usage` events via `request_id`; `ModelPricingRegistry` (config dict or LiteLLM API endpoint — confirm once EPMCDME-13429 lands).
- Produces: `SavingsHandler.get_savings(user, period, scope) -> SummariesResponse` with `aggregate_savings`, `total_routed_cost`, `total_counterfactual_cost`, `classifier_overhead`.

**Test-first: yes — tests assert the savings formula `max(0, counterfactual_cost − actual_routed_cost_incl_classifier_overhead)`, the reconciliation guardrail (aggregate savings ≤ sum of counterfactual costs for the window), and that requests with no routing metadata are excluded rather than counted as zero-savings events.**

- [ ] Confirm EPMCDME-13429 is merged; confirm `request_id` on `routing_call_usage` matches the LiteLLM spend-log call identifier; redesign join if they differ.
- [ ] Confirm `ModelPricingRegistry` source and implement accordingly.
- [ ] Write failing tests for formula, guardrail, and exclusion behavior.
- [ ] Implement `SavingsHandler` with `ModelPricingRegistry` and savings calculation.
- [ ] Add `GET /analytics/routing/savings` route to `analytics.py`.
- [ ] Run: `pytest tests/codemie/service/analytics/handlers/test_savings_handler.py -v` — expect PASS.

---

## Negative-constraints record

| Constraint | Honored by |
|---|---|
| No routing fields in `conversation_assistant_usage` or `MetricsAttributes` | Tasks 3–4 write only to `RoutingMonitoringService`; no task touches `ConversationMonitoringService` write path or `MetricsAttributes`. |
| No CLI ClickHouse changes | No task touches `cli_analytics.py` or `cli_analytics_repository.py`. |
| No backfill | Not mentioned in any task. |
| No SwitchyardMeta signal floats | Not included in Task 1 field list. |
| No litellm_config.yaml changes | Not touched. |
| No new feature flag | Not introduced; empty results by document absence instead. |
| Routing events only for streaming responses with LLM_PROXY_TRACK_USAGE | Task 4 adds emission inside the block already gated at `proxy_router.py:1220`. |
| Not implementing EPMCDME-13429 | Task 7 deferred and explicitly conditioned on that ticket merging. |
