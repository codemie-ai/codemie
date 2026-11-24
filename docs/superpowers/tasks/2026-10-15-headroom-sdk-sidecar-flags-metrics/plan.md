# Headroom SDK/Sidecar Flags, Savings Metrics and Review Follow-ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Explicit Headroom mode (off|sdk|sidecar), lean optional SDK packaging with Kompress parity, savings metric + analytics + conversation-level display, and MR 4461 leftovers.

**Architecture:** One setting selects the mode; `headroom-ai` becomes an optional poetry extra imported lazily only in `sdk` mode. The hook reports per-request savings (recounted locally) into `RequestSummary` -> `TokensUsage.headroom` -> persisted on `GeneratedMessage` / `WorkflowExecution.tokens_usage` JSON (no migration), then a metric and API field derive from it, mirroring routing.

**Commit per task using the repository's existing convention** (EPMCDME-15645 prefix). Do not touch the user's uncommitted edits in `docker-compose.yml`, `customer-config.yaml` values, or the guide; edit around them. Never copy secrets from `.env`.

## Acceptance criteria
- `HEADROOM_MODE` = `off|sdk|sidecar` selects behaviour independent of LiteLLM; legacy `HEADROOM_ENABLED` alone keeps old inference with a warning.
- Without the `headroom-sdk` extra installed, nothing imports `headroom` unless mode is `sdk`; missing package fails open.
- SDK mode runs Kompress when the ML extra is installed and download is allowed; otherwise one clear warning.
- A `headroom_compaction_usage` metric (original, compacted, saved tokens, mode, strategy) is emitted only when saved > 0.
- `GET /assistants/metrics/{id}` includes headroom savings only when > 0 (assistant and workflow conversations).
- `/headroom/*` analytics endpoints gated by `features:headroom`.
- `WorkflowHeadroomLayers`/`_resolve_headroom_config` live in `service/headroom/`; SDK env no longer overwrites operator values.

**Assumptions recorded:** mode setting is explicit; no UI in repo so "display" = API field (None, excluded); savings derive from persisted JSON, no Alembic migration; CR-008 (commit subjects) is a merge-time reword, no code.

---

### Task 1: Explicit mode flag and gating (+ CR-004)
**Files:** Modify `configs/config.py:614-624`, `service/headroom/factory.py`, `resolver.py:41`, `service/assistant_service.py:65,487`; Create `service/headroom/scope.py`; Tests in `tests/codemie/service/headroom/test_factory.py`, `test_resolver.py`, `test_assistant_service_headroom.py`.
- [ ] Add `HEADROOM_MODE: Literal["off","sdk","sidecar"] | None = None`. `get_compressor_mode()` returns it when set; when None: `off` unless `HEADROOM_ENABLED`, else legacy `sidecar if is_litellm_enabled() else sdk` plus one deprecation warning. `get_compressor()` builds by mode (no LiteLLM coupling). `resolver.resolve` gates on mode != "off" and `features:headroom`.
- [ ] Move `WorkflowHeadroomLayers` and `_resolve_headroom_config` to `scope.py`; assistant_service imports them. Non-`HeadroomConfig` values log a warning (type only) and are ignored.
- Test-first: yes — `HEADROOM_MODE=sidecar` with LiteLLM disabled returns `SidecarCompressor`; `sdk` with LiteLLM enabled returns `SDKCompressor`; `off` makes `resolve()` return None; legacy-only `HEADROOM_ENABLED` still works.

### Task 2: Lazy optional SDK packaging, Kompress parity, CR-006
**Files:** Modify `pyproject.toml:122,189-191`, `poetry.lock`, `Dockerfile:15-36`, `service/headroom/sdk_compressor.py:37-98`, `configs/config.py`; Tests `test_sdk_compressor.py`.
- [ ] Make `headroom-ai` optional; extras `headroom-sdk = ["headroom-ai"]` and `headroom-sdk-ml` (adds transformers, onnxruntime, huggingface-hub; versions per analysis section 6). Dockerfile gets build arg `INSTALL_HEADROOM_SDK` (default false) mapped to `-E`. Regenerate lock.
- [ ] `_apply_sdk_env` uses `os.environ.setdefault` for all keys (CR-006). Add `HEADROOM_SDK_ALLOW_MODEL_DOWNLOAD: bool = False`; it picks the default `HEADROOM_OFFLINE` ("0" when true). After import, check Kompress availability once (guarded, fail-open); for balanced/aggressive log one warning when unavailable.
- [ ] Add a parametrized parity test: sidecar and SDK compressors (mocked) yield equivalent `CompressOutcome` for the same strategy. Optional real-Kompress test marked skip unless `headroom` + ML extras importable; no model download in CI.
- Test-first: yes — with `HEADROOM_OFFLINE=0` preset, `_load_sdk` leaves it unchanged; mode `sidecar` never imports `headroom` (assert absent from `sys.modules`); import failure disables SDK once.

### Task 3: Savings capture and persistence
**Files:** Create `core/headroom_savings.py`; Modify `service/headroom/hook.py:33,63-110`, `agents/langgraph_agent.py:210`, `service/request_summary_manager.py:41-70`, `core/models.py:972`, `rest_api/models/conversation.py:136,420`, turn builder in `conversation_service.py`; Tests in `test_hook.py`, `test_request_summary_manager.py`, `tests/codemie/rest_api/models/`.
New DTO:
```python
class HeadroomSavings(BaseModel):
    tokens_original: int; tokens_compacted: int; tokens_saved: int
    mode: str; strategy: str | None = None; calls: int = 1
    def merged(self, other: "HeadroomSavings") -> "HeadroomSavings": ...  # sums, keeps mode
```
- [ ] `build_headroom_pre_model_hook(cfg, llm_model, request_id=None)`; `LangGraphAgent` passes `request_uuid`. On `compressed`, recount merged messages with `_count_text_tokens` (consistent for both modes; avoids overstating when parts are discarded); only if saved > 0 call `RequestSummaryManager().add_headroom(request_id, HeadroomSavings)`. Failures stay fail-open.
- [ ] `RequestSummary.calculate()` merges into `TokensUsage.headroom` (None when empty); turn builder copies it to `GeneratedMessage.headroom`.
- Test-first: yes — hook run with a mocked compressor records one `HeadroomSavings` with recounted tokens; `unchanged`/`skipped_small`/`not_applied` record nothing; `calculate()` sums two events.

### Task 4: Metric and analytics
**Files:** Create `service/monitoring/headroom_monitoring_service.py`, `service/analytics/handlers/headroom_handler.py`; Modify `service/analytics/metric_names.py` (`HEADROOM_COMPACTION_USAGE`), `conversation_service.py:389` area, `analytics_service.py`, `rest_api/routers/analytics.py`, workflow completion path that sets `WorkflowExecution.tokens_usage` (locate it; emit once there); Tests under `tests/codemie/service/monitoring/`, `tests/codemie/service/analytics/handlers/`.
- [ ] `HeadroomMonitoringService.send_compaction_metric` modelled on `RoutingMonitoringService`: no-op unless `tokens_saved > 0`; attributes include user, project, conversation_id, assistant_id, request_id, tokens_original, tokens_compacted, tokens_saved, mode, strategy (ints, not floats). Call `_emit_headroom_metric` beside `_emit_routing_metrics`.
- [ ] Handler (summary, by-mode/strategy, timeseries) modelled on `routing_handler.py`, endpoints `/headroom/*`, guard `_ensure_headroom_analytics_enabled()` on `features:headroom`, not `routingAnalytics`.
- Test-first: yes — metric not sent when saved is 0/None and sent once with all fields when > 0; analytics route returns 403/disabled when `features:headroom` is off.

### Task 5: Conversation usage level
**Files:** Modify `rest_api/models/conversation.py:237-244,355`, `rest_api/routers/assistant.py:1330-1409`; Tests `tests/codemie/rest_api/routers/` metrics tests.
- [ ] `ConversationMetricsOut` gains `headroom_tokens_saved: int | None`, `headroom_tokens_original: int | None`. `Conversation.get_headroom_savings()` sums `GeneratedMessage.headroom`; `_calculate_workflow_metrics` sums `execution.tokens_usage.headroom`. Return None when total saved is 0 (excluded by `response_model_exclude_none`).
- Test-first: yes — conversation with savings returns the totals; without savings the JSON has no headroom keys (assistant and workflow paths).

### Task 6: Docs and MR housekeeping
**Files:** Modify `.ai-run/guides/integration/headroom-integration.md`, `config/customer/customer-config.yaml` (description text only), `docker-compose.yml` only if a comment is needed (keep local edits).
- [ ] Preserve existing uncommitted guide edits. Add flag matrix (`HEADROOM_MODE`, `HEADROOM_ENABLED` legacy, `features:headroom`, extras, `HEADROOM_SDK_ALLOW_MODEL_DOWNLOAD`), Kompress/SDK parity and known limitations, metric + analytics + API contract, and `headroom-sdk` install note. Replace "slice 1" in Deployment step 4. Update CR-006 limitation.
- [ ] Record in guide/MR notes: description must ask a reviewer to post `/sanity` (dependency + Dockerfile change); CR-008 commits need reword/squash at merge.
- Test-first: no — documentation only.

## Negative-constraint pass
- "ONLY if config set": T2 (lazy import asserted by test; optional extra). "Only tokens saved": T3 records and T4 emits only when saved > 0. "Only show with savings": T5 returns None. "No migration": T3/T5 use JSON fields. "Do not reuse routingAnalytics": T4. "Preserve local edits / no secrets": header, T6. "No real model download in tests": T2 (mocks, skip-marked optional). CR-008: T6 note only.
