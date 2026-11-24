# Technical Research

**Task**: headroom compression sdk sidecar analytics
**Generated**: 2026-10-15
**Research path**: filesystem

---

## 1. Original Context

"we have already implemented changes for headroom integration, but there are some places, which worth to cover
1. Really clear config and feature flags, to turn on only sdk part or to turn on sidecar mode. For SDK approach need to dynamically load python package ONLY if config is set, to not overload backend with unnessesary libraries
2. need to add metrics for compaction usage data, we should track only actions, where we save tokens, inside metric we should put original tokens count, compacted output, savings, which mode was selected. Need to add analytics in same way we do for llm router usage
3. Show these savings data on conversation usage level. Show it only if we have savings, otherwise it doesn't make sence to show
4. For balance mode we should have Kompress ML model, verify that for SDK approach we do support it, both headroom usage options should work the same

in addition look at PR and review comments - https://gitbud.epam.com/epm-cdme/codemie/-/merge_requests/4461"

---

## 2. Codebase Findings

### Existing Implementations
Package `src/codemie/service/headroom/` (663 lines):
- `models.py` - `HeadroomStrategy` (lossless/balanced/aggressive), `HeadroomConfig` (stored), `HeadroomOverride` (node/workflow), `ResolvedHeadroomConfig` (frozen dataclass: compress_user_messages, compress_tool_outputs, strategy; no mode field).
- `resolver.py` - `resolve(node, workflow, assistant)`; returns None unless `config.HEADROOM_ENABLED` and `customer_config.is_feature_enabled("headroom")`.
- `factory.py` - `get_compressor()` (lru_cache) and `get_compressor_mode()`. **Mode is implicit**: `SidecarCompressor` if `codemie.enterprise.litellm.is_litellm_enabled()` else `SDKCompressor`. No dedicated setting selects sdk vs sidecar.
- `sidecar_compressor.py` - sync `httpx.post` to `{HEADROOM_SIDECAR_URL}/v1/compress`, Bearer `HEADROOM_PROXY_TOKEN`.
- `sdk_compressor.py` - lazy `_load_sdk()` imports `from headroom import CompressConfig, compress` on first call only; sets `HEADROOM_OFFLINE=1`, `HEADROOM_BEACON=off`, `DO_NOT_TRACK=1`, `HEADROOM_CCR_BACKEND=memory` (overwrites with a warning); replaces private `headroom.compress._pipeline` with a marker-free `TransformPipeline`; 4-worker `ThreadPoolExecutor` enforces `HEADROOM_TIMEOUT_SECONDS`. Import failure sets `_unavailable` and disables the SDK for the process.
- `strategies.py` - the only module naming strategy values: `sidecar_config()` (balanced -> `mode: lossy_inline`; aggressive adds `target_ratio`) and `sdk_compress_kwargs()` (lossless -> `mode="agent"`; aggressive -> `target_ratio`; balanced -> no kwargs).
- `hook.py` - `build_headroom_pre_model_hook(cfg, llm_model)`; `_compress_messages` counts local tokens (skip below `HEADROOM_MIN_TOKENS`), calls compressor, guards `tokens_before <= tokens_after`, merges text back. `_record(cfg, outcome, tokens_before, tokens_after)` is the **only telemetry today**: span attrs `codemie.headroom.{mode,strategy,tokens_before,tokens_after,outcome}` plus one `logger.info("headroom.compress ...")`. Outcomes: `compressed`, `unchanged`, `not_applied`, `skipped_small`, `failed_open`. No metric, no per-request correlation (hook has no user/conversation/request id; signature is `(cfg, llm_model)`).
- Wiring: `src/codemie/agents/langgraph_agent.py` (kwarg `headroom_config`, hook is last in `_compose_pre_model_hooks`, lines ~209, 320, 364); `src/codemie/service/assistant_service.py` (`WorkflowHeadroomLayers` line 65, `_resolve_headroom_config` line 487, call sites 667 and 949).

### Architecture and Layers Affected
Config (`configs/config.py` lines 614-624, `config/customer/customer-config.yaml`), service (`service/headroom/*`), agent hook (`agents/langgraph_agent.py`), monitoring (`service/monitoring/`), analytics (`service/analytics/` + `rest_api/routers/analytics.py`), conversation persistence/API (`rest_api/models/conversation.py`, `rest_api/routers/assistant.py`, `core/models.py::TokensUsage`, `service/request_summary_manager.py`), dependency packaging (`pyproject.toml`, `Dockerfile`), deployment (`docker-compose.yml`).

### Integration Points: how "llm router usage" is implemented (the template for items 2 and 3)
1. DTO `core/routing_info.py::RoutingInfo` (pydantic, additive-field merge via `merged_over`, `is_empty()`).
2. Carried per LLM run: `tokens_callback.py` builds `LLMRun(routing=...)` -> `RequestSummaryManager.update_llm_run`; `RequestSummary.calculate()` merges runs into `TokensUsage.routing`. Also stamped onto `Thought.routing` (`agent_invoke_callback.py`) and persisted in the conversation history JSON.
3. Metric: `service/monitoring/routing_monitoring_service.py::RoutingMonitoringService.send_routing_metric` (no-op if `routing.is_empty()`), uses `BaseMonitoringService.send_count_metric(name=MetricName.ROUTING_CALL_USAGE.value, attributes=...)`. Name registered in `service/analytics/metric_names.py` (`ROUTING_CALL_USAGE = "routing_call_usage"`). Attributes include user_id, user_email, project, conversation_id, assistant_id, request_id, llm_run_id, plus domain fields. Emission is in `conversation_service.py::_emit_routing_metrics` (one event per `LLMRun`, fallback to merged `tokens_usage.routing`) and `enterprise/litellm/proxy_router.py:1359`.
4. Analytics: `service/analytics/handlers/routing_handler.py` (707 lines, `AnalyticsQueryPipeline`, ES keywords like `attributes.routed_model.keyword`), lazy-loaded in `analytics_service.py` (~191, endpoints ~1251+), routes under `/routing/*` in `rest_api/routers/analytics.py` (~2819+), gated by customer flag `features:routingAnalytics` via `_ensure_routing_analytics_enabled()`.
5. Conversation level: `Conversation._routing_from_thoughts` (conversation.py ~367) derives message-level `GeneratedMessage.routing`; `Conversation.get_total_classifier_cost_usd()` (~355) sums thoughts; `ConversationMetricsOut` (conversation.py 237) has `total_classifier_cost_usd`, returned by `GET /assistants/metrics/{conversation_id}` (`routers/assistant.py` ~1378, `response_model_exclude_none=True`, so a None field is omitted from JSON - fits "show only when savings exist"). Workflow conversations take a separate branch `_calculate_workflow_metrics` (~1330) aggregating `WorkflowExecution.tokens_usage`. `ConversationMetrics` is a SQL table (`conversation_metrics`) with totals columns; `total_classifier_cost_usd` is computed at read time, not stored.
- Simpler metric precedent: `hedging_monitoring_service.py` (`send_log_metric`, JSON log line).
- No UI code in this repo; UI/SDK repos consume the API (the Headroom guide has an "API contract for UI and SDK repos" section).

### Patterns and Conventions
Monitoring services subclass `BaseMonitoringService` with classmethods; metric names live in `MetricName`; hook must stay fail-open and never log message content or the token; strategy values only in `strategies.py`; imports of headroom symbols from submodules (package `__init__` re-exports nothing).

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/integration/headroom-integration.md` (primary; has uncommitted user edits: Kompress paragraph, `aggressive` degradation note; read, not modified). States SDK vs sidecar only as "Headroom sidecar (HTTP) or SDK compressor" in the architecture diagram; Settings table lists no mode setting; Known limitations cover SDK env overwrite and 4-worker timeout pool.
- `docs/headroom/research-findings.md` (item 3: SDK works without ML extras; item 4: `target_ratio` only works when Kompress can run, needs `onnxruntime` + ~261 MB model from HF, "in the SDK-only deployment `aggressive` is therefore currently the same as `balanced`").
- Design: `docs/superpowers/specs/2026-10-08-headroom-per-assistant-workflow-design.md`; prior task dirs `docs/superpowers/tasks/2026-10-08-headroom-per-assistant-workflow/`, `2026-10-12-headroom-workflow-scope/`.
- Also relevant: `.ai-run/guides/development/configuration-patterns.md`, `.ai-run/guides/integration/cli-analytics-*.md` (analytics conventions), `.ai-run/guides/security/README.md` (dependency change -> `/sanity`).

### Architectural Decisions
Option A chosen (backend calls Headroom in a pre-model hook: sidecar when LiteLLM enabled, in-process otherwise). SDK default CCR markers suppressed via private `_pipeline` override. `HEADROOM_AGGRESSIVE_TARGET_RATIO=0.2`.

### Derived Conventions
Config flags in `config.py` as pydantic settings; customer flags as `features:*` components; analytics metrics named in `MetricName` and read by a handler per domain.

---

## 4. Testing Landscape

### Existing Coverage
`tests/codemie/service/headroom/` (test_hook 31, test_sdk_compressor 22, test_resolver 11, test_sidecar_compressor 8, test_models 5, test_factory 4, test_strategies 4 test functions); `tests/codemie/agents/test_langgraph_headroom_hook.py`; `tests/codemie/service/test_assistant_service_headroom.py`, `..._workflow_headroom.py`; migrations and model tests (see guide "Verification"). Router-analytics precedents: `tests/codemie/service/monitoring/test_routing_monitoring_service.py`, `tests/codemie/service/analytics/handlers/test_routing_handler.py`, `tests/codemie/service/test_conversation_service.py`, `tests/codemie/service/test_request_summary_manager.py`.

### Testing Framework and Patterns
pytest (+ pytest-asyncio); mocks of compressor/httpx; `_reset_for_tests()` in sdk_compressor; monitoring tests share `tests/codemie/service/monitoring/conftest.py`.

### Coverage Gaps
No test exercises real Kompress or the real SDK against a model; no test for metric emission from the hook (none exists); no test for mode selection other than `test_factory.py`'s LiteLLM-driven cases; no `headroom` entry in `tests/codemie/service/monitoring/`.

---

## 5. Configuration and Environment

### Environment Variables
`HEADROOM_ENABLED` (default false), `HEADROOM_SIDECAR_URL`, `HEADROOM_PROXY_TOKEN` (secret), `HEADROOM_TIMEOUT_SECONDS` (10), `HEADROOM_MIN_TOKENS` (1000), `HEADROOM_AGGRESSIVE_TARGET_RATIO` (0.2) - all in `src/codemie/configs/config.py`. Local `.env` sets them (token value present; do not copy). Sidecar container env in `docker-compose.yml`: `HEADROOM_OFFLINE`, `HEADROOM_BEACON`, `DO_NOT_TRACK`, `HEADROOM_COMPRESS_ALLOW_REMOTE`, `HEADROOM_LOSSLESS=1`, `HEADROOM_HOST`, `HEADROOM_SKIP_UPSTREAM_CHECK`, `HEADROOM_PROXY_TOKEN`, `HEADROOM_ALLOW_UNAUTHENTICATED_BIND`. No HEADROOM vars in `deploy-templates/`.

### Configuration Files
`config/customer/customer-config.yaml` `features:headroom` (committed value `false`; **uncommitted local edit sets `true`**; description there says "sidecar" though the flag also gates SDK mode). Uncommitted local edits in `docker-compose.yml`: publishes port 8787, `HEADROOM_OFFLINE: "0"`, `mem_limit: 2g`, AWS env passthrough (unrelated to Headroom). `payload_prose.txt`, `payload_rows.json` are untracked scratch files.

### Feature Flags and Deployment Concerns
- Two gates today (global `HEADROOM_ENABLED` + customer `features:headroom`); sdk vs sidecar is not a flag: it follows `is_litellm_enabled()` (`HAS_LITELLM` enterprise package AND `LLM_PROXY_ENABLED`).
- `headroom-ai = "0.40.0"` is in `[tool.poetry.dependencies]` (main, non-optional), so it is always installed (and pulls `litellm`, `tiktoken`, `rich`, `ast-grep-cli`, `click`, ...; wheel ~15 MB). Only the `import headroom` is lazy. `[tool.poetry.extras]` has `enterprise` and `pdf-svg` only; `Dockerfile` runs `poetry install --only main [-E enterprise] --no-root`.
- `headroom-ai` is not installed with ML extras: poetry.lock deps are core only.

---

## 6. Risk Indicators

- **Kompress is NOT supported in SDK mode as built (item 4).** Verified in `.venv` (`headroom-ai 0.40.0`): `transformers` and `torch` are absent; `onnxruntime 1.22.1` is present only transitively via `magika` (markitdown). Headroom's `kompress_compressor._is_onnx_available()` requires `onnxruntime` AND `transformers`, so `is_kompress_available()` is False. Independently, `sdk_compressor._SDK_ENV` forces `HEADROOM_OFFLINE=1`, which makes the SDK refuse the HuggingFace model download (`hf_hub_download_local_first`). The sidecar image is built with extras `proxy,code,bedrock` and runs with `HEADROOM_OFFLINE=0` (local edit), so it has Kompress; the two modes therefore differ for prose, `balanced` and `aggressive` (sidecar compresses prose, SDK compresses only structured content; `aggressive` == `balanced` in SDK). Guide "Known limitations" does not say this for SDK mode. The research doc (item 3/4) recorded this gap and the guide does not.
- Speculative: making SDK match sidecar needs the `proxy`/`ml`-like extras (at least `transformers>=5.5,<6`, `huggingface-hub>=1.5`, onnxruntime >=1.24 on py>=3.11; `ml` adds `torch>=2.12.1`), an offline-flag policy that allows the model download or a pre-cached model dir, and ~770 MiB RSS in the backend process; this conflicts with item 1's "do not overload backend". Optional packaging (poetry extra such as `-E headroom-sdk`, Dockerfile arg) and an explicit mode flag are design choices for spec.
- Speculative: no explicit mode setting; `factory.get_compressor_mode()` couples Headroom mode to LiteLLM, so "sidecar only" or "sdk only" cannot be forced, and a deployment with LiteLLM enabled can never use the SDK. `lru_cache` on `get_compressor()` means mode changes need a restart.
- Speculative: `headroom-ai` is a hard main dependency, so "load ONLY if config set" is satisfied at import time but not at install/image-size level.
- Metrics: the hook has no request/user/conversation context (`build_headroom_pre_model_hook(cfg, llm_model)`), `LangGraphAgent` has `request_uuid` but it is not passed in; `LLMRun` correlation is by LangChain run_id inside `TokensCallback`, not available in a pre-model hook. Savings should be reported only for outcome `compressed`; note guide caveat that `tokens_before/after` are the compressor's figures and may overstate savings when toggles discard parts of the result (`not_applied`/partial merge), so a recount of the merged messages (via `calculate_tokens`) may be required for accurate savings.
- Conversation-level display: `ConversationMetrics` is a SQL table (totals stored); new persisted totals would need an Alembic migration, whereas deriving from `Thought`/`LLMRun` JSON history needs none (as with classifier cost). Workflow conversations use a separate aggregation path (`_calculate_workflow_metrics`, `WorkflowExecution.tokens_usage`); sub-assistants/supervisor compress independently and each LangGraphAgent builds its own hook.
- Routing analytics endpoints are gated by `features:routingAnalytics` (default false); a Headroom analytics view needs its own or reused flag (`features:headroom`).
- Analytics docs currently reference no `routing_call_usage` guide; ES field mapping for new numeric attributes (int vs float) must be set carefully (see `send_log_metric` float coercion comment).
- Existing MR risks (see Section 8b): `HEADROOM_*` env overwrite in `_apply_sdk_env` still unconditional (CR-006); `dcd28aaf9`, `ddf7559c9`, `ba6a40c74` commit subjects lack `EPMCDME-` prefix (CR-008).
- Working tree is dirty with the user's local edits (customer flag true, compose port/offline/AWS); do not treat them as committed state.

---

## 7. Summary for Complexity Assessment

The change spans five layers: configuration (new explicit mode setting(s) and clearer flags in `config.py`, `customer-config.yaml`, `docker-compose.yml`, guide), packaging (making `headroom-ai` and its ML dependencies optional in `pyproject.toml`/`Dockerfile`/`poetry.lock`), the headroom service package (`factory.py`, `sdk_compressor.py`, `hook.py`, `models.py`), the monitoring/analytics stack (new monitoring service, `MetricName` entry, a handler plus routes and service wiring modelled on the ~700-line routing handler) and the conversation API (`ConversationMetricsOut`, `Conversation` aggregation, workflow branch). Estimated file surface is roughly 15-25 source files plus tests, with no existing UI code in this repo.

Technical novelty is moderate: every piece has an in-repo template (RoutingMonitoringService / routing_handler / classifier-cost aggregation), but two items are genuinely new: getting request/conversation context into a LangGraph pre-model hook for per-call metrics, and making Kompress work in-process. The latter is a verified functional gap (SDK lacks `transformers`, and the SDK path forces `HEADROOM_OFFLINE=1`), and fixing it conflicts with item 1's goal of a lean backend; it also changes dependency footprint and requires a `/sanity` handoff.

Test posture: headroom package is well covered by unit tests with mocks (about 85 test functions) and router-analytics code has parallel test suites to copy; there is no real-model or end-to-end Kompress coverage and no metric tests for Headroom. Main risks are the SDK/sidecar parity gap, packaging changes, accurate savings counting, ES mapping, and unresolved items from MR 4461 review.

---

## 8. External References

The task names MR https://gitbud.epam.com/epm-cdme/codemie/-/merge_requests/4461 as a source.

**Live MR comments: UNRESOLVED / could not be fetched.** `glab` 1.86.0 is installed and `glab auth status` reports a stored token for gitbud.epam.com, but every API call (`projects/epm-cdme%2Fcodemie/merge_requests/4461` and `/discussions`, also `glab api user`) returned `ERROR Unauthenticated.` The stored token is invalid or expired; re-run `glab auth login --hostname gitbud.epam.com` and retry (read-only GET of `.../merge_requests/4461/discussions?per_page=100`). Live resolved/unresolved status of discussion threads is therefore unknown; nothing below was taken from the live MR.

### 8b. Offline review artifacts: `docs/superpowers/reviews/2026-10-12-mr4461/`
Review of head `bf7db3e9a` ("Simplify Headroom hook telemetry"), decision **request-changes**, confidence low, 8 blocking findings, partial coverage (diff oversized). Status below was checked against the current source (HEAD `c32c47569`), not against the live MR:
- CR-001 (critical, guide intro says workflow scope arrives later / "slice" wording): guide intro now describes assistant + workflow + node scope; one stale remnant remains in Deployment step 4 ("slice 1"). Mostly addressed.
- CR-002 (guide Deployment omits workflows migration): fixed, both `f8bd0b700a64` and `e091153f77dd` listed.
- CR-003 (`Optional[HeadroomOverride]`): fixed, `workflow_config.py:125` uses `HeadroomOverride | None`.
- CR-004 (Headroom wiring in ~1000-line `assistant_service.py`; wrapper silently ignores non-`HeadroomConfig` values): **still open**, `WorkflowHeadroomLayers` and `_resolve_headroom_config` remain in `assistant_service.py` (lines 65, 487).
- CR-005 (sidecar path lacks inflation guard): fixed (`hook.py` guard `0 < tokens_before <= tokens_after`, commit `717e31a45`).
- CR-006 (process-wide `os.environ` overwrite in `_load_sdk`): **still open**; `_apply_sdk_env` still assigns unconditionally (warns on difference) and the guide lists it as a known limitation, which is documentation rather than a fix.
- CR-007 (SDK compression has no timeout): fixed, worker-thread deadline with 4-worker pool (guide documents it).
- CR-008 (commit subjects without `EPMCDME-####`): **still open** for `dcd28aaf9` ("headroom-workflow-scope: add planning artifacts"), `ddf7559c9`, `ba6a40c74`; needs reword or squash at merge.
- Not yet done per `code-review-final.json` business_review: MR description asking a reviewer to post `/sanity` (dependency change), and live check with local stack and sidecar (pending-stage-7).
- Recent commits `cbe188029` "Address MR review findings for Headroom" and `c32c47569` (text-block content) post-date the review; they may answer comments I cannot see.

Other named sources: none (no external paths).
