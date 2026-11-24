# Headroom core (slice 1 of 3) — Spec

Ticket: EPMCDME-15645. Slice: `headroom-core`. Branch: `EPMCDME-15645_headroom-per-assistant-workflow`.
Full-feature design (settled, not re-litigated): `docs/superpowers/specs/2026-10-08-headroom-per-assistant-workflow-design.md` (D1-D8).
Research: `docs/headroom/research-findings.md`. Assessed XL (27/36), split by the user; this spec covers slice 1 only.

## Goal

Land the self-contained, unit-tested Headroom building blocks, plus settings, feature flag, dependency and sidecar deployment, so that
slices 2 and 3 only have to wire them to assistants, workflows and nodes. Nothing in this slice changes runtime behavior: the hook is built
and tested but attached to no agent.

## Slices

| Slice | Content | Status |
|---|---|---|
| 1 `headroom-core` | This spec | in scope |
| 2 `assistant-scope` | `assistants.headroom_config` column and migration, `AssistantRequest`/`AssistantBase` field, `LangGraphAgent` kwarg, hook appended last in single and supervisor chains, `build_agent` wiring (LangGraph-only), regression tests, integration guide | follow-up |
| 3 `workflow-scope` | `workflows.headroom_config` column and migration, both YAML validators, `WorkflowConfigBase`/`WorkflowAssistant` parsing, `headroom` block preserved on workflow save (needs EPMCDME-15759 merged first), `AssistantExecutionOptions` plumbing, node > workflow > assistant precedence | follow-up |

## Behavior

### 1. Package `src/codemie/service/headroom/`

Follows design sections 1-4; only slice-specific decisions are restated here.

- `models.py`: `HeadroomStrategy` (lossless, balanced, aggressive), `HeadroomConfig`, `HeadroomOverride`, `ResolvedHeadroomConfig` (frozen dataclass), as in design section 1. Models are defined now but persisted nowhere.
- `resolver.py`: `resolve(node, workflow, assistant)`. Returns `None` when `HEADROOM_ENABLED` is false, when `customer_config.is_feature_enabled("headroom")` is false, or when the merged `enabled` is false. Otherwise merges field by field, first non-`None` of node, workflow, assistant, `HeadroomConfig` default.
- `strategies.py`: single table mapping strategy to sidecar `config` body and SDK `CompressConfig` kwargs. No other module names strategy values.
- `compressor.py`: `HeadroomCompressor` protocol and `CompressOutcome(messages, tokens_before, tokens_after)`.
- `sidecar_compressor.py`: sync httpx `POST {HEADROOM_SIDECAR_URL}/v1/compress`, Bearer `HEADROOM_PROXY_TOKEN`, timeout `HEADROOM_TIMEOUT_SECONDS`. Any timeout, connection error or non-2xx raises a typed internal error that the hook turns into fail-open.
- `sdk_compressor.py`: lazy `from headroom import compress`. `HEADROOM_OFFLINE=1`, `HEADROOM_BEACON=off`, `DO_NOT_TRACK=1` are set before the import. First `ImportError` is logged once and the compressor disables itself for the process.
- `factory.py`: `get_compressor()` returns a process-wide singleton, sidecar when `is_litellm_enabled()`, otherwise SDK (D2). No mode setting.
- `hook.py`: `build_headroom_pre_model_hook(cfg: ResolvedHeadroomConfig | None, llm_model: str)`. Returns `None` for `None` cfg (so a future `_compose_pre_model_hooks(..., hook)` call stays a no-op for unconfigured agents). Otherwise a sync `(state) -> dict` hook with this contract:
  - Returns `{}` when below `HEADROOM_MIN_TOKENS` (local tiktoken count, no compressor call), when nothing changed, and on every failure (D7). It never raises into the agent. Logs carry no message content.
  - Output messages are the original `BaseMessage` objects with only `content` replaced. `additional_kwargs`, `response_metadata`, `tool_calls`, `tool_call_id`, `id`, `name`, and `artifact` are preserved untouched (risk 5).
  - System and AI messages are never replaced. Human content is replaced only if `compress_user_messages`; Tool content only if `compress_tool_outputs` ("output" means tool results in context, not model output).
  - Non-string `content` (block lists, Anthropic thinking/tool_use blocks, LangChain-v1 image blocks with base64) is never overwritten. For the compressor request such messages are rendered as the concatenation of their text blocks only, with image blocks omitted, so the message count stays aligned. A message whose original content is not a `str` always keeps its original content.
  - A returned message count different from the input count is a fail-open.
  - Checkpointed history is never mutated; the hook returns `{"llm_input_messages": [...]}` only.
- Blocking cost: the sidecar call is synchronous inside a graph node. Worst case it adds `HEADROOM_TIMEOUT_SECONDS` to each LLM call before failing open. This is accepted and documented in the settings description.

### 2. Observability

Each hook invocation that reaches the compressor (and skipped-small invocations) emits OTel span `headroom.compress` with attributes `codemie.headroom.mode` (sidecar|sdk), `.strategy`, `.tokens_before`, `.tokens_after`, `.duration_ms`, `.outcome` (compressed|unchanged|skipped_small|failed_open), plus one structured log line. No message content in either.

### 3. Settings and feature flag

`Config` in `src/codemie/configs/config.py` gains, with defaults: `HEADROOM_ENABLED=False` (kill switch), `HEADROOM_SIDECAR_URL="http://headroom:8787"`, `HEADROOM_PROXY_TOKEN=""` (secret, never logged), `HEADROOM_TIMEOUT_SECONDS=10`, `HEADROOM_MIN_TOKENS=1000`, `HEADROOM_AGGRESSIVE_TARGET_RATIO` (default is the value chosen by spike item 4). `config/customer/customer-config.yaml` gains `features:headroom`, default `enabled: false`, modelled on `features:requestHedging`.

### 4. Dependency and deployment

- `pyproject.toml`: `headroom-ai = "0.40.0"` pinned, no extras; `poetry.lock` regenerated. Expect lock churn from its `litellm>=1.96.2,<2.0` requirement; our 1.99.2 pin must be retained. Add a `[tool.pip-licenses]` allow-list entry only if the license check fails.
- `docker-compose.yml`: new `headroom` service, image `ghcr.io/headroomlabs-ai/headroom:0.40.0`, following the litellm service pattern (pinned tag, `environment`, `healthcheck`, restart policy, resource limits), env `HEADROOM_OFFLINE=1`, `HEADROOM_BEACON=off`, `HEADROOM_COMPRESS_ALLOW_REMOTE=1`, `HEADROOM_PROXY_TOKEN`. Internal network only. The `codemie` service gets no `depends_on` on it and `HEADROOM_ENABLED` stays false, so the stack behaves as today.
- `litellm_config.yaml` gets no Headroom guardrail (D3).
- The MR description must ask a reviewer to post `/sanity` (AGENTS.md Sanity Regression rule).

### 5. Spike tasks (first plan tasks, no production code)

Results are written to `docs/headroom/research-findings.md` section 7, and the strategy table and `HEADROOM_AGGRESSIVE_TARGET_RATIO` default follow them:
1. Sidecar 0.40.0 accepted `config.mode` values; pick the sidecar mapping for `lossless`.
2. Linux wheel availability for `headroom-ai` 0.40.0.
3. SDK `compress()` works with no ML extras, offline.
4. A value for the aggressive target ratio (also: whether the SDK beacon runs for SDK-only use).

## Acceptance criteria

1. `src/codemie/service/headroom/` exists with the modules in section 1, each with the EPAM license header, and imports cleanly with `headroom-ai` absent (SDK import is lazy).
2. Resolver returns `None` when the kill switch or feature flag is off or merged `enabled` is false; otherwise merges field by field with precedence node > workflow > assistant > default (e.g. workflow `enabled: true` alone keeps the assistant's `strategy`).
3. Strategies table is the only place strategy values live; each strategy maps to the expected sidecar body and SDK kwargs.
4. Sidecar compressor sends the correct URL, Bearer header, body and timeout; timeout, connection error, 4xx and 5xx result in fail-open at the hook.
5. SDK compressor sets offline/telemetry-off env vars before import and disables itself once after `ImportError`.
6. Factory picks sidecar iff `is_litellm_enabled()`, else SDK, and reuses the instance.
7. Hook: `None` cfg gives `None`; below-min-tokens makes no compressor call and returns `{}`; system messages untouched; type toggles respected; list-content and image-block messages keep original content; metadata fields preserved; count mismatch, compressor error and unexpected exceptions return `{}`; unchanged returns `{}`; input state is not mutated.
8. `headroom.compress` span is emitted with the listed attributes and no message content.
9. New settings and `features:headroom` exist with the stated defaults; with defaults, no existing behavior changes (no agent, API, DB or workflow code is touched).
10. `headroom-ai==0.40.0` is added with updated `poetry.lock`; docker-compose `headroom` service is defined with healthcheck, resource limits and offline/telemetry-off env.
11. Spike items 1-4 are recorded in `docs/headroom/research-findings.md` section 7 and reflected in the strategy table and ratio default.
12. Unit tests under `tests/codemie/service/headroom/` cover items 2-8; `make ruff` and the license-header check pass.

## Ticket acceptance criteria mapping

| Ticket AC | Slice 1 |
|---|---|
| AC1 SDK approach researched and documented | Satisfied (research doc exists; spike results finalize section 7) |
| AC2 integrated per documented approach | Partial: components built, nothing attached |
| AC3 config per assistant / workflow | Deferred to slices 2, 3 |
| AC4 saved config affects only its scope | Deferred (slices 2, 3) |
| AC5 configured integration used at run time | Deferred (slices 2, 3) |
| AC6 unconfigured keeps behavior | Trivially holds (no wiring); regression tests in slice 2 |
| AC7 validation and regression tests | Partial: unit tests for the core package only |
| AC8 usage and configuration documented | Partial: research doc and settings descriptions; integration guide is slice 2 |

## Non-goals

- No DB columns, alembic migration, or persistence of `HeadroomConfig`/`HeadroomOverride`.
- No `LangGraphAgent` changes, no hook-chain edits (single or supervisor), no `AssistantService.build_agent` / `build_agent_for_workflow` changes.
- No workflow YAML parsing, schema (`execution_config_schema.yaml`) or workflow save-path changes; no `AssistantExecutionOptions` changes.
- No REST API fields, request/response models or UI/SDK contract implementation.
- No `.ai-run/guides/integration/headroom-integration.md` or AGENTS.md guide row (slice 2).
- No LiteLLM guardrail and no `litellm_config.yaml` change.
- No handling of structured-output bypass, `AIToolsAgent`, Bedrock or A2A-remote gaps (slice 2 decides).
- No prompt-cache mitigation, no Headroom output shaper, no async compressor, no ML extras.
- No change to Helm chart `deploy-templates/`.

## Risks left standing

- Linux wheel for `headroom-ai` 0.40.0 is unverified; if spike item 2 fails, the pinned dependency cannot be installed in the Linux image and the dependency approach must be re-scoped with the user before merging.
- Sidecar `lossless` mapping, offline SDK behavior and the aggressive ratio are unresolved until the spike.
- `poetry.lock` churn from the transitive `litellm` constraint; `/sanity` required.
- Blocking sync call adds up to `HEADROOM_TIMEOUT_SECONDS` per LLM call once wired (slice 2).
