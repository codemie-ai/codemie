# Routing Source Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make backend LiteLLM and Switchyard routing metrics use the same normalized dimensions and classifier-cost observability semantics as CLI analytics.

**Architecture:** Keep the existing `routing_call_usage` event and backend analytics endpoints. Add provider-neutral normalization helpers to the backend routing value object, populate the same canonical fields from both header families, and retain raw/provider-specific data only as additive metadata. Update analytics parsing to consume canonical tiers and decision sources without changing existing non-routing metrics.

**Tech Stack:** Python 3.11+, Pydantic, dataclasses, FastAPI, Elasticsearch aggregations, pytest.

**Spec:** `docs/superpowers/tasks/2026-09-14-epmcdme-14888-routing-metrics/spec.md` plus the approved parity design in conversation.

## Global Constraints

- Preserve the existing `routing_call_usage` metric and `/v1/analytics/routing/*` API surface.
- Do not modify CLI TypeScript files, CLI ClickHouse contracts, or frontend code.
- Do not add routing fields to `conversation_assistant_usage`.
- Canonical tiers must be `simple`, `medium`, `complex`, or `reasoning`; Switchyard `efficient` maps to `simple`, while LiteLLM `medium` remains `medium`.
- Canonical decision sources must be lower-case kebab-case, matching CLI normalization of underscores.
- Switchyard routing cost is known whenever routing is present, including a valid zero classifier cost; LiteLLM routing cost is known only when a valid classifier-cost value is present.
- Preserve backward-compatible existing fields and return empty analytics results when no routing documents exist.

---

### Task 1: Add shared routing normalization and parity fields

**Files:**
- Modify: `src/codemie/core/routing_info.py`
- Test: `tests/enterprise/test_routing_info.py`

**Interfaces:**
- Produce `normalize_routing_tier(raw: str | None) -> str | None`.
- Produce `normalize_decision_source(raw: str | None) -> str | None`.
- Extend `RoutingInfo` with `routing_family: Literal["switchyard", "litellm"] | None`, `routing_cost_known: bool | None`, `routing_tier_raw: str | None`, `classifier_model: str | None`, `router_type: str | None`, `router_score: float | None`, and `classifier_cache_creation_tokens: int | None`.
- `RoutingInfo.merged_over` keeps last-non-None semantics for identity/provider fields and additive semantics for classifier token/cost fields.

- [ ] Add failing tests for tier normalization: `simple -> simple`, `efficient -> simple`, `medium -> medium`, `middle -> medium` for legacy compatibility, `capable -> complex`, `complex -> complex`, `reasoning -> reasoning`, and unknown values lower-cased.
- [ ] Add failing tests for decision-source normalization: whitespace trimming, lower-casing, underscore-to-hyphen conversion, and `None`.
- [ ] Add failing tests that `is_empty()` considers the new fields and `merged_over()` sums classifier cache-creation tokens while preserving the latest scalar routing metadata.
- [ ] Run `pytest tests/enterprise/test_routing_info.py -v` and confirm the new tests fail before implementation.
- [ ] Implement the two pure normalization helpers and the new `RoutingInfo` fields with no provider imports.
- [ ] Update `is_empty()` and `merged_over()`; treat `routing_cost_known` as last-non-None, not additive.
- [ ] Run `pytest tests/enterprise/test_routing_info.py -v` and confirm it passes.

### Task 2: Normalize LiteLLM and Switchyard extraction

**Files:**
- Modify: `src/codemie/enterprise/litellm/router.py`
- Modify: `src/codemie/enterprise/litellm/proxy_router.py`
- Modify: `src/codemie/enterprise/litellm/litellm_router_meta.py`
- Modify: `src/codemie/enterprise/switchyard/router.py`
- Modify: `src/codemie/enterprise/switchyard/routing_meta.py`
- Test: `tests/enterprise/litellm/test_routing_headers.py`
- Test: `tests/enterprise/litellm/test_proxy_router.py`
- Test: `tests/enterprise/switchyard/test_routing_meta.py`
- Test: `tests/enterprise/test_routing_info.py`

**Interfaces:**
- LiteLLM extraction maps `x-litellm-router-tier`, `x-litellm-router-cause`, `x-litellm-router-score`, routed/requested model headers, router type, classifier model, classifier cost, and classifier token headers into canonical `RoutingInfo`.
- Switchyard extraction maps its typed routing metadata into the same canonical fields and emits `routing_family="switchyard"`, normalized tier/source, and `routing_cost_known=True` when routing exists.
- Proxy extraction emits `routing_family="litellm"` for LiteLLM header routing and sets `routing_cost_known=True` only when `x-litellm-classifier-cost` parses successfully.

- [ ] Add failing LiteLLM tests for canonical tier/source values, model/score/router metadata, classifier cache-creation handling, and missing/zero classifier cost.
- [ ] Add failing Switchyard tests for canonical `efficient/capable` tiers, normalized decision source, raw tier preservation, and zero-cost heuristic routing.
- [ ] Run the focused LiteLLM and Switchyard tests and confirm failure.
- [ ] Implement the mappings using the shared normalization helpers; do not duplicate normalization tables in provider modules.
- [ ] In proxy composition, preserve the existing duplicate-cost guard when both routing layers are present, but set LiteLLM `routing_cost_known` from the parsed cost rather than header presence.
- [ ] Run `pytest tests/enterprise/litellm/test_routing_headers.py tests/enterprise/litellm/test_proxy_router.py tests/enterprise/switchyard/test_routing_meta.py tests/enterprise/test_routing_info.py -v`.

### Task 3: Emit parity fields and align analytics presentation

**Files:**
- Modify: `src/codemie/service/monitoring/routing_monitoring_service.py`
- Modify: `src/codemie/service/analytics/handlers/routing_handler.py`
- Test: `tests/codemie/service/monitoring/test_routing_monitoring_service.py`
- Test: `tests/codemie/service/analytics/handlers/test_routing_handler.py`

**Interfaces:**
- `RoutingMonitoringService.send_routing_metric` continues to emit one `routing_call_usage` event and includes canonical routing fields, provider/cost observability fields, raw tier, classifier metadata, routed token fields, and existing savings fields.
- Routing analytics normalize legacy documents at read time so historical `efficient/capable` and underscore decision sources remain visible beside new canonical documents.

- [ ] Add failing monitoring tests that assert the emitted attributes include routing family, cost-known state, normalized tier/source, raw tier, classifier model/score, and cache-creation tokens.
- [ ] Add failing handler parser tests for legacy tier/source values and assert they produce the same canonical presentation as new documents.
- [ ] Run the focused monitoring and handler tests and confirm failure.
- [ ] Extend the monitoring attribute map without removing existing keys or emitting routing data into another metric.
- [ ] Update routing activity and decision-source parsers to use the shared canonical vocabulary and normalize legacy values at the response boundary.
- [ ] Keep zero-cost values as numeric zero; do not convert known zero cost into an absent/unknown value.
- [ ] Run `pytest tests/codemie/service/monitoring/test_routing_monitoring_service.py tests/codemie/service/analytics/handlers/test_routing_handler.py -v`.

### Task 4: Verify integration and regression boundaries

**Files:**
- No production files unless a focused test exposes a parity defect.

- [ ] Run the complete routing test set: `pytest tests/enterprise tests/codemie/service/monitoring/test_routing_monitoring_service.py tests/codemie/service/analytics/handlers/test_routing_handler.py tests/codemie/rest_api/routers/test_analytics.py -v`.
- [ ] Run `make ruff`.
- [ ] Run `make build`.
- [ ] Run `make license-check`.
- [ ] Inspect `git diff --check` and confirm only the parity implementation, tests, and this plan are changed.
- [ ] Report any unavailable gate explicitly rather than treating it as passed.
