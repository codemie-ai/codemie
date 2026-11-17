# Technical Research

**Task**: effort temperature model-settings llm-providers
**Generated**: 2026-10-08
**Research path**: filesystem

---

## 1. Original Context

Jira ticket EPMCDME-13335 — "Support effort parameter for newer models instead of deprecated temperature"

CodeMie should support Anthropic's newer adaptive thinking configuration via the `effort` parameter
for relevant Anthropic models (and this is now broadening to other LLM providers too — effort is
widely adopted across various LLM models, not just Anthropic). Users should be able to configure
supported models with effort levels such as `low`, `medium`, `high`, `xhigh`, and `max`, instead of
relying only on `temperature`. Temperature is deprecated/unsupported for newer model families and
currently causes API errors when used with them.

The implementation should cover both platform behavior and user guidance, so users can successfully
configure model-specific options without backend errors.

If feasible, the UI should show only settings relevant to the selected model. If provider/model
capability detection is too difficult or unreliable across many providers, CodeMie may expose both
`temperature` and `effort`, but should clearly explain that not all models support all options and
users should use only parameters supported by the selected model.

CodeMie should also warn users that selecting higher effort levels such as `xhigh` or `max` may
consume more tokens.

Acceptance criteria:
- CodeMie supports the Anthropic `effort` parameter (and ideally other providers') for relevant
  newer models.
- Supported `effort` values can be configured and are correctly passed to the backend/provider API.
- CodeMie does not send deprecated/unsupported `temperature` for models where it is not allowed.
- Either only applicable settings are shown per model, or both are shown with clear guidance.
- UI explains when to use `temperature` vs `effort`.
- UI warns that `xhigh`/`max` may increase token consumption.
- Existing models that still support `temperature` continue to work without regression.
- No backend API errors from invalid parameter usage when saving/using model settings.
- Behavior documented in user-facing guidance or inline help text.

Affected areas (per ticket): model settings UI, assistant/chat configuration forms, backend request
mapping and provider-specific parameter handling, validation layer for model/provider options, user
guidance/help text, potential model capability detection logic.

---

## 2. Codebase Findings

This repository (`AGENTS.md`) is the **CodeMie backend**; there is no frontend/UI code here (no `ui`
or `frontend` directory found). The ticket's "model settings UI" / "assistant configuration forms"
work therefore lives in a separate repo and is out of scope for file changes here — this repo covers
backend request mapping, provider parameter filtering, and validation.

### Existing Implementations

No `effort`, `reasoning_effort`, `thinking`, or `budget_tokens` parameter exists anywhere in the LLM
request pipeline today. (The only `effort` hits in the whole tree are unrelated CLI-analytics event
fields — `src/codemie/repository/cli_analytics/postgres/rollups.py`,
`src/codemie/repository/cli_analytics/vocabulary.py` — a different "CLI effort" telemetry concept,
not model parameters.) This is a greenfield addition for the actual LLM-request path.

Existing `temperature`/capability-filtering machinery that an `effort` parameter would need to parallel:

- `src/codemie/configs/llm_config.py:45-52` — `LLMFeatures` Pydantic model: `streaming`, `tools`,
  `temperature`, `parallel_tool_calls`, `system_prompt`, `max_tokens`, `top_p` booleans. No `effort`/
  `reasoning` field defined, even though some YAML entries already set `reasoning: true` (see below) —
  that key is silently dropped unless `LLMFeatures` sets `model_config = {"extra": ...}` (not found).
- `src/codemie/configs/llm_config.py:209-256` — `LLMModel`: per-model config (`base_name`,
  `deployment_name`, `features`, `cost`, `max_output_tokens`, etc.) loaded from YAML.
- `config/llms/llm-azure-config.yaml`, `llm-dial-config.yaml` — set `features.temperature: false` for
  `gpt-5*`, `o1`, `o3*`, `o4-mini` entries; `o3-2025-04-16` and `o4-mini-2025-04-16` additionally set
  `features.reasoning: true` (an undeclared field on `LLMFeatures` today).
- `config/llms/llm-aws-config.yaml` — Bedrock Claude 4.5/4.6 Sonnet/Haiku/Opus entries exist (the
  Anthropic model family the ticket targets) with **no** `features.temperature: false` override, i.e.
  they are currently treated as temperature-capable.
- `src/codemie/enterprise/litellm/models.py:93-102` — builds `LLMFeatures` from a live LiteLLM proxy
  catalog (`model_info.get("supported_openai_params")`), detecting `temperature in supported_params`
  automatically for proxy-backed models — the one place capability is auto-detected rather than
  YAML-declared.
- `src/codemie/enterprise/litellm/llm_factory.py:1035-1073` (`_filter_litellm_params`) — the central
  "strip unsupported params" point: forces `temperature=1` when `features.temperature is False`,
  nulls `max_tokens`/`top_p`, sets streaming overrides. An `effort` parameter would plug in next to
  this filter.
- `src/codemie/enterprise/litellm/llm_factory.py:1076-1133` (`get_litellm_chat_model`) and the
  internal `create_litellm_chat_model` (~line 229-330) — thread `temperature`/`top_p` as explicit
  kwargs end-to-end to the LiteLLM/AzureChatOpenAI client construction.
- `src/codemie/core/router_chat_model.py:135-141` — `LLMParams` frozen dataclass:
  `temperature: float | None`, `top_p: float | None` — "Generation parameters forwarded to
  get_llm_by_credentials for every candidate model" in the router; built/used at lines ~302-307.
- `src/codemie/core/dependecies.py:271` `get_llm_by_credentials` and `:409`
  `get_llm_by_credentials_raw` — provider-dispatch factory functions that take `temperature`/`top_p`
  and build the concrete LangChain client (ChatAnthropic, AzureChatOpenAI, Bedrock, Vertex) per
  provider.
- `src/codemie/core/workflow_models/workflow_models.py:57,84-94` — `WorkflowAssistant.temperature`
  field plus a `@model_validator` (`validate_temperature`) enforcing `float` type and `0..2` range —
  the existing pattern for validating a user-settable LLM parameter at the model layer.
- `src/codemie/rest_api/models/assistant.py` — four separate `temperature: Optional[float] = None`
  fields on different request/response models (lines 337, 628, 667, 1270) plus one with explicit
  `ge=0.0, le=2.0` bounds and a description at line 1470 (`VirtualAssistantChatRequest`) — temperature
  is duplicated across several request DTOs rather than centralized in one shared schema.
- `src/codemie/enterprise/switchyard/llm_clients.py:40-49` — a *different*, internal "Switchyard"
  routing-proxy wire format already reserves a `"reasoning": {}` namespace (alongside `"sampling": {}`)
  in its base request shape, and `src/codemie/enterprise/switchyard/message_format.py:184-188` maps a
  Claude "thinking" content block to a neutral `"reasoning"` block type for that same protocol. This is
  a routing-classifier stub, not the main chat completion path, but shows the wire protocol already
  has a reasoning/thinking concept distinct from `sampling.temperature`.

### Architecture and Layers Affected

- **Config layer**: `config/llms/*.yaml` (per-provider model catalogs) + `LLMModel`/`LLMFeatures` in
  `src/codemie/configs/llm_config.py`.
- **Provider/integration layer**: `src/codemie/core/dependecies.py` (provider dispatch),
  `src/codemie/enterprise/litellm/llm_factory.py` and `models.py` (LiteLLM proxy path).
- **Core routing layer**: `src/codemie/core/router_chat_model.py` (`LLMParams`, candidate routing).
- **API/request-model layer**: `src/codemie/rest_api/models/assistant.py` (multiple DTOs with their
  own `temperature` field), `src/codemie/core/workflow_models/workflow_models.py` (workflow assistant
  validation).
- **Service layer**: `src/codemie/service/assistant_service.py` and related assistant/version services
  construct and persist these models (not deeply inspected here; flagged for the plan stage).

### Integration Points

- LiteLLM proxy (enterprise, optional dependency `codemie-enterprise`) — capability auto-detection via
  `supported_openai_params`.
- Direct provider SDK clients (Anthropic, Azure OpenAI, AWS Bedrock, Google Vertex AI) via
  `get_llm_by_credentials` in `src/codemie/core/dependecies.py`.
- Switchyard (Rust-based routing proxy, separate protocol) — not the main parameter path but already
  has a `reasoning` wire-format slot, worth cross-checking during design so two "reasoning" concepts
  don't diverge.

### Patterns and Conventions

- Per-model capability flags live declaratively in YAML (`features:` block) and are filtered centrally
  in `_filter_litellm_params`, not scattered per call site — any new `effort` capability flag should
  follow this same declare-in-YAML-filter-centrally pattern rather than ad hoc branching.
- Parameters that go all the way to the provider client are threaded as explicit function
  kwargs/dataclass fields through several layers (`LLMParams` → `get_llm_by_credentials` →
  `create_litellm_chat_model`) rather than passed as a loose dict — any `effort` field needs the same
  explicit threading to avoid being silently dropped.
- Validation of user-supplied LLM generation parameters uses Pydantic field constraints
  (`ge`/`le` on `temperature`) and `@model_validator(mode='after')` methods (see
  `workflow_models.py:84-94`) rather than a separate validation module.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/llm-providers.md` — the P1 guide for this task category (per
  `AGENTS.md`'s classifier, "Integrations"/LLM Providers). States: use model provider YAML files and
  central LLM services, don't hardcode model names, keep AWS/Azure/GCP/Anthropic/LiteLLM paths
  pluggable, gate LiteLLM-specific behavior with `is_litellm_enabled()`, and route outbound header
  changes through the shared header-building helper — direct provider calls and LiteLLM-proxied calls
  tag traffic through *different* mechanisms and must not be mixed.
- `.ai-run/guides/development/configuration-patterns.md` and
  `.ai-run/guides/development/security-patterns.md` are also P1/P0 for "Development"/"Security"
  categories and likely relevant to how a new per-model config flag and validated API field should be
  added, but were not opened in this pass (budget-scoped); recommend the plan stage read them.

### Architectural Decisions

No ADR or inline `NOTE:`/`DECISION:` marker specific to effort/reasoning parameters was found. The
`LLMFeatures` capability-filter design (`_filter_litellm_params`) is itself the closest existing
"decision": unsupported params are silently coerced to safe defaults (e.g. forced `temperature=1`)
rather than rejected — important precedent for how `effort` vs `temperature` conflicts might be
resolved for the same request.

### Derived Conventions

- New model-level capability toggles are added to `LLMFeatures` and then declared per-model in the
  relevant `config/llms/*.yaml` file(s) — not inferred purely at runtime, except for the LiteLLM-proxy
  path where capability is auto-detected from the live catalog.
- `o3-2025-04-16`/`o4-mini-2025-04-16` already set `features.reasoning: true` in YAML even though
  `LLMFeatures` has no such field — this looks like a stale/forward-looking config edit from prior
  work, and is a concrete signal that reasoning/effort support was anticipated but never wired into
  the Pydantic model or the filter function.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/enterprise/litellm/test_llm_factory.py` and `tests/enterprise/litellm/test_models.py` —
  cover `_filter_litellm_params`-style behavior and `LLMFeatures` construction from LiteLLM catalog
  data.
- `tests/codemie/core/test_router_chat_model.py` — covers `RouterChatModel`/`LLMParams` behavior.
- `tests/codemie/agents/test_langgraph_agent_initialize_llm.py`,
  `tests/codemie/agents/test_assistant_agent/test_initialize_llm.py` — cover LLM initialization paths
  that likely pass `temperature`/`top_p`.
- `tests/codemie/rest_api/models/test_map_assistant_request_metadata.py`,
  `test_map_assistant_request_helpers.py` — cover request-model mapping, likely including
  `temperature` fields in `assistant.py`.
- `tests/codemie/service/test_llm_retirement_service.py` — relevant to the "deprecated for newer
  models" framing of this ticket (model retirement/compatibility concerns already have a tested
  service).

### Testing Framework and Patterns

pytest-based (`pytest.ini` at repo root); fixture/mock conventions not inspected in depth this pass —
defer to `.ai-run/guides/testing/testing-patterns.md` if tests are explicitly requested later.

### Coverage Gaps

- No tests reference `effort`, `reasoning_effort`, or `thinking`/`budget_tokens` anywhere — entirely
  untested surface, as expected for a greenfield parameter.
- No test currently exercises the `features.reasoning: true` YAML flag already present for
  `o3-2025-04-16`/`o4-mini-2025-04-16`, consistent with it not being read by any code path yet.

---

## 5. Configuration and Environment

### Environment Variables

None specific to `effort`/`temperature` identified in this pass; `README.md:61` documents
`MODELS_ENV` per the `llm-providers.md` guide for model/provider config selection generally.

### Configuration Files

- `config/llms/llm-azure-config.yaml`, `llm-aws-config.yaml`, `llm-gcp-config.yaml`,
  `llm-dial-config.yaml` — per-provider model catalogs; each `llm_models` entry carries a `features:`
  block (`temperature`, `max_tokens`, `streaming`, `tools`, `parallel_tool_calls`, `system_prompt`,
  `top_p`, plus the currently-unused `reasoning` key on two entries).

### Feature Flags and Deployment Concerns

No feature flag or env toggle currently gates temperature-vs-effort behavior. The LiteLLM enterprise
path is itself optionally enabled (`is_litellm_enabled()`, `LLM_PROXY_MODE` config in
`src/codemie/configs`), which matters because capability detection differs between the YAML-declared
path (static, provider-config-driven) and the LiteLLM-proxy path (dynamic, catalog-driven) — any
`effort` capability flag needs a story for both.

---

## 6. Risk Indicators

- **No existing `effort`/`reasoning` wiring anywhere in the request pipeline** — this is a new
  parameter threaded through at least 5 layers (YAML config → `LLMFeatures` → `_filter_litellm_params`
  → `LLMParams`/`get_llm_by_credentials` → request DTOs in `assistant.py`), all of which currently
  hardcode `temperature`/`top_p` as the only generation knobs.
- **`temperature` is duplicated across 4+ separate Pydantic models** in
  `src/codemie/rest_api/models/assistant.py` with inconsistent constraints (some have `ge/le` bounds
  and descriptions, some don't) — adding `effort` consistently across all of them (or consolidating)
  is a design decision, not something this research found a single source of truth for.
  Speculative: a shared mixin/base schema for generation parameters may reduce this duplication, but
  that is a design choice for the spec/plan stage, not a discovered constraint.
  Speculative: this task may need a backend validator that enforces "temperature and effort are
  mutually exclusive per the model's declared features" similar to `validate_temperature` in
  `workflow_models.py`, but no such cross-field validator currently exists to extend.
- **Stale/unused `features.reasoning: true` YAML entries** (`o3-2025-04-16`, `o4-mini-2025-04-16` in
  `llm-azure-config.yaml`) are not read by `LLMFeatures` or `_filter_litellm_params` today — any new
  `effort`/`reasoning` field design should reconcile with (or deliberately rename/replace) this
  existing but dormant key to avoid two different "reasoning" conventions in the same YAML file.
  Speculative: there may be an expectation that this key already means something close to "supports
  effort," making it a candidate anchor point for the new feature flag — worth confirming with the
  spec stage rather than assuming.
- **Two independent "reasoning" concepts already exist in the codebase**: the switchyard wire protocol
  (`llm_clients.py`, `message_format.py`) uses `"reasoning"` for Claude extended-thinking content
  blocks in a routing-classifier context unrelated to the main generation-parameter path. Naming this
  ticket's new parameter risks colliding conceptually (though not necessarily code-wise) with that
  existing usage.
- **No UI/frontend in this repository** — all UI-facing acceptance criteria (showing only relevant
  settings per model, inline help text, xhigh/max token-consumption warnings) are out of scope for
  backend changes here and depend on a separate repo; this backend work can only expose the
  model-capability metadata (e.g. supported effort levels, whether temperature is allowed) for that UI
  to consume.
- **LiteLLM-proxy capability detection vs static YAML declaration are two different mechanisms** that
  must both support any new `effort`-capability signal, or the two provider paths will diverge in
  behavior for the same model.
- **No tests exist for any part of the affected area for `effort`/`reasoning`**, so coverage must be
  built from scratch alongside the implementation.

---

## 7. Summary for Complexity Assessment

This task touches five architectural layers in the CodeMie backend: the YAML-driven model catalog
(`config/llms/*.yaml`), the shared capability model (`LLMFeatures`/`LLMModel` in
`src/codemie/configs/llm_config.py`), the central parameter-filtering function
(`_filter_litellm_params` in `src/codemie/enterprise/litellm/llm_factory.py`), the core routing
dataclass and provider dispatch (`LLMParams` in `router_chat_model.py`, `get_llm_by_credentials` in
`dependecies.py`), and multiple request/response DTOs in `src/codemie/rest_api/models/assistant.py`
plus `workflow_models.py`. There is no existing `effort`/`reasoning_effort`/`thinking` implementation
anywhere in the request pipeline today — this is a greenfield parameter addition layered onto a
well-established but temperature-only pattern, with two provider paths (static YAML-declared
capability vs. live LiteLLM-proxy auto-detected capability) that both need a story for the new field.

Technical novelty is moderate-to-high: the pattern to follow (declare capability in YAML, filter
centrally, thread through typed params) is well established and should be mimicked rather than
invented, but no code exists yet that maps model names to supported effort *values* (`low`/`medium`/
`high`/`xhigh`/`max`), only to boolean on/off capability flags — effort levels are a different shape of
data than the existing boolean `LLMFeatures`. A stale, unused `features.reasoning: true` YAML flag on
two models hints at prior intent that was never wired up, and should be reconciled rather than
duplicated. The UI-facing acceptance criteria (per-model setting visibility, inline help text,
token-consumption warnings) are out of scope for this backend repository and depend on a separate
frontend codebase, which should be factored into scoping conversations with the requester.

Test coverage for the affected area exists for `temperature`/`LLMFeatures` filtering today
(`tests/enterprise/litellm/test_llm_factory.py`, `test_models.py`, `test_router_chat_model.py`,
assistant-request-mapping tests) but has zero coverage for anything effort/reasoning-related, so new
tests are needed end-to-end rather than extending an existing effort-specific suite. Key risks are the
duplicated `temperature` field across 4+ DTOs (no single source of truth to extend), the two divergent
capability-detection mechanisms (YAML vs LiteLLM proxy) that must stay consistent, and an existing
unrelated `"reasoning"` concept in the Switchyard routing protocol that could create naming confusion
if not reconciled during design.

---

## 8. External References

None named by the task. `task_context` describes the ticket requirements directly and does not point
to an external file, directory, or URL as a source of truth.

---

## 9. Addendum (2026-10-08): LiteLLM Proxy Catalog — Dynamic Model Discovery and `effort`/`reasoning_effort` Detection

Scoped follow-up research answering four specific questions about `litellm_config.yaml` (the proxy's
`model_list`) and the pinned `litellm` SDK, left out of Section 2–5 above. Direct source reads only;
anything inferred rather than confirmed is marked **Inferred**.

### 9.1 How a proxy-only model becomes an `LLMModel`

- `src/codemie/rest_api/main.py:159-164`, `src/codemie/service/monitoring/llm_proxy_monitoring_service.py:417-419`,
  `src/codemie/rest_api/routers/admin.py:276-286` all call
  `codemie.enterprise.litellm.get_available_models()`.
- `get_available_models()` (`src/codemie/enterprise/litellm/dependencies.py:858-920`) calls
  `litellm.get_available_models(user_id=user_id, api_key=api_key)` where `litellm` is the
  **`LiteLLMService` instance** returned by `get_litellm_service_or_none()` (confusingly shares the
  `litellm` name with the open-source SDK import — it is not the SDK). Each raw dict returned is passed to
  `map_litellm_to_llm_model()` (`src/codemie/enterprise/litellm/models.py:39-100`), which builds
  `LLMFeatures` from `model_info.get("supported_openai_params", [])`.
- `LiteLLMService` itself is **not in this repository**: it lives in the `codemie-enterprise` PyPI
  package (`pyproject.toml:58`, optional extra, source index
  `codemie-enterprise-python` registry). It is **not installed in this project's virtualenv**
  (`ModuleNotFoundError: No module named 'codemie_enterprise'`), so its implementation could not be
  read directly in this pass.
- `Inferred`: `LiteLLMConfig` (built at `dependencies.py` from `config.LITE_LLM_URL` +
  `config.LITE_LLM_MASTER_KEY`, plus `request_timeout`/`list_request_timeout`/`models_cache_ttl`)
  strongly implies `LiteLLMService.get_available_models()` is an **HTTP client call** against the
  deployed LiteLLM proxy's REST API — most likely the proxy's `GET /model/info` (or `/v1/model/info`)
  endpoint — rather than an in-process `litellm.Router`/SDK call. This matches the shape of the dicts
  the test suite mocks (`model_name`/`litellm_params`/`model_info` — see `tests/enterprise/litellm/test_models.py`),
  which is exactly the response shape of that proxy endpoint (confirmed by reading the open-source
  `litellm` package's own copy of this endpoint below). This HTTP-vs-SDK claim is not directly
  confirmable without the `codemie-enterprise` source.
- The bundled open-source `litellm` package (installed, v1.99.2) **does** ship its own copy of the
  proxy server, so its `/model/info` handling was read directly as the best available proxy for how
  the deployed proxy likely behaves: `GET /model/info` → `model_info_v1()` →
  `_get_proxy_model_info()` (`litellm/proxy/proxy_server.py:13918-13954`), which merges
  config-declared `model_info:` with `litellm.get_model_info()`'s computed dict (precedence rule
  below). For proxy-only models like `claude-sonnet-5`, this is exactly the mechanism that supplies
  `supported_openai_params` to `map_litellm_to_llm_model`.

### 9.2 Can CodeMie hand-declare `effort`/`reasoning_effort` support per model?

Yes — confirmed directly in the installed `litellm` package's proxy code, and the precedence rule is
unambiguous:

```python
# litellm/proxy/proxy_server.py:13918  _get_proxy_model_info()
model_info: Final = model.get("model_info", {})          # <- config.yaml's model_info: block
litellm_model_info = get_litellm_model_info(model=model)  # <- SDK-computed (cost map + dynamic detection)
...
for k, v in litellm_model_info.items():
    if k not in model_info:        # config-declared keys are NEVER overwritten
        model_info[k] = v
```

Any key already present under a model's `model_info:` block in `litellm_config.yaml` wins over the
SDK-computed value for that same key — identical precedence to the existing `top_p: false` /
`supports_native_streaming: false` overrides already used in the file (e.g.
`litellm_config.yaml` gpt-5-2025-08-07, o3-mini entries). So CodeMie **can** add, e.g.:
```yaml
model_info:
  id: claude-sonnet-5-us-east-2
  supported_openai_params: ["reasoning_effort", "temperature", ...]
```
and it will be honored over whatever `litellm.get_model_info()` would otherwise compute — this is the
override mechanism the ticket asked about. `litellm`'s internal model-cost-map data does **not**
silently take precedence; it is only a *fallback* for keys the config doesn't set.

`Inferred`: this exact merge function belongs to the open-source `litellm` proxy server bundled in
this venv, used here as the best available stand-in for the actual deployed proxy's behavior (version
and vendor of the real running proxy were not independently confirmed, and `codemie-enterprise`'s own
HTTP client code could theoretically reshape the response before it reaches `map_litellm_to_llm_model`).

### 9.3 Does the pinned `litellm==1.99.2` know about these specific forward-dated models?

Checked directly against `litellm/model_prices_and_context_window_backup.json` inside the installed
package (`$VENV/lib/python3.12/site-packages/litellm/model_prices_and_context_window_backup.json`,
3179 entries) and via live calls to `litellm.get_supported_openai_params()` /
`litellm.get_model_info()` in this venv (pinned version confirmed: `importlib.metadata.version("litellm") == "1.99.2"`).

**All three identifiers are already known to this pinned version**, contrary to the task's concern
that forward-dated names might silently resolve to "no effort support":

| Identifier (as used in `litellm_config.yaml`) | Cost-map key(s) found | `supports_reasoning` | `reasoning_effort` in computed `supported_openai_params`? |
|---|---|---|---|
| `bedrock/us.anthropic.claude-sonnet-5` | `us.anthropic.claude-sonnet-5`, `claude-sonnet-5` | `true` (+ `supports_xhigh_reasoning_effort`, `supports_max_reasoning_effort`, `supports_adaptive_thinking`) | **Yes** |
| `bedrock/eu.anthropic.claude-opus-4-7` | `eu.anthropic.claude-opus-4-7`, `claude-opus-4-7` | `true` (same flags) | **Yes** |
| `vertex_ai/claude-sonnet-4-5` | `vertex_ai/claude-sonnet-4-5`, `vertex_ai/claude-sonnet-4-5@20250929` | `true` | **Yes** |

Live confirmation (`litellm.get_model_info(model="bedrock/us.anthropic.claude-sonnet-5")`):
`supported_openai_params` = `[..., "temperature", "top_p", ..., "thinking", "reasoning_effort", "output_config", ...]`
— `reasoning_effort` and `temperature` are **both** present simultaneously for this model today.

The dynamic-detection code path (`AnthropicConfig.get_supported_openai_params` in
`litellm/llms/anthropic/chat/transformation.py:436-467`, and the identical logic feeding
`AmazonConverseConfig.get_supported_openai_params` for `bedrock`/`bedrock_converse`) appends
`"reasoning_effort"` + `"thinking"` to the params list whenever the model's cost-map entry has
`supports_reasoning: true` (or is an adaptive-thinking Claude 3.7+ family model) — i.e. the SDK
already has native, versioned concepts for `reasoning_effort` values (`AnthropicConfig._validate_effort_for_model`,
`_map_reasoning_effort`, values `none/minimal/low/medium/high/xhigh/max`) and per-model effort-ceiling
flags (`supports_xhigh_reasoning_effort`, `supports_max_reasoning_effort`,
`bedrock_output_config_effort_ceiling: "xhigh"`) that line up closely with the ticket's own value set.

Practical implication: **CodeMie's `map_litellm_to_llm_model()` does not need the override from §9.2
for these three specific models** to detect effort support correctly today — the SDK already reports
`reasoning_effort` in `supported_openai_params`. The override mechanism remains relevant as a safety
net for genuinely unmapped future models. The real gap is on CodeMie's own side: `map_litellm_to_llm_model`
(`src/codemie/enterprise/litellm/models.py:93-100`) only reads `temperature`, `max_tokens`,
`top_p`, `parallel_tool_calls` out of `supported_params` into `LLMFeatures` — it has no `effort`/
`reasoning_effort` field to populate even though the signal is already present in the data it receives.

### 9.4 Is `llm-aws-config.yaml` missing these models drift, or a deliberate split?

Evidence points to **drift/staleness**, not a documented architectural decision — no ADR, comment, or
guide was found describing an intended divergence between the two catalogs:

- `git log --oneline -- config/llms/llm-aws-config.yaml` → **one commit only**:
  `afc61fa125 EPMCDME-9519: Initial commit with all project files` (2026-03-12). It has never been
  touched since the repository's initial commit.
- `config/llms/llm-gcp-config.yaml` has **no Claude entries at all** (`grep -n claude` → no output),
  consistent with the static GCP/Vertex catalog never having tracked any Anthropic-on-Vertex model.
- By contrast, `git log --oneline -- litellm_config.yaml` shows active, ongoing maintenance:
  `eb87dcb50 EPMCDME-15177: Add Bedrock MoonshotAI Kimi K3 model and bump litellm image to 1.96.2`,
  `edb44ccd1 EPMCDME-14460: Add Bedrock GPT-5.x and Grok 4.6 LiteLLM models`, latest change
  2026-09-25 — i.e. this is the catalog actually being kept current with new model releases.
- No comment in `litellm_config.yaml`, `llm-aws-config.yaml`, `src/codemie/configs/llm_config.py`, or
  any `.ai-run/guides/` file explains or justifies a two-catalog split; `.ai-run/guides/integration/llm-providers.md`
  only says to keep "AWS/Azure/GCP/Anthropic/LiteLLM paths pluggable" and not to mix their tagging
  mechanisms — it does not address catalog content parity.

Conclusion: the missing `claude-sonnet-5`/`claude-opus-4-7` entries in `llm-aws-config.yaml` look like
**simple catalog drift** — the static direct-provider YAMLs are not being updated in step with new
Anthropic model releases, while `litellm_config.yaml` is. Whether this should be fixed (back-filling
`llm-aws-config.yaml`) is a design/scoping call for the plan stage, not something this research found
a documented rationale against.

---

## 10. Addendum (2026-10-08): Scoped Research Pass #3 — Per-Model Effort Validation and Temperature-Exclusion

Live introspection of the pinned `litellm==1.99.2` package (`poetry run python3`, this worktree) and
direct reads of its source. All model identifiers below are the exact strings used in
`config/llms/llm-aws-config.yaml`, `config/llms/llm-azure-config.yaml`, and `litellm_config.yaml`.

### 10.1 Critical discovery not in the task brief: `litellm.get_model_info()` silently drops 4 of the flags this design needs

`litellm.model_cost` (raw dict, keyed by model id) carries `supports_sampling_params`,
`supports_output_config`, `supports_medium_reasoning_effort`, and `supports_high_reasoning_effort`
correctly. But `litellm.get_model_info()` — the only entry point external callers normally use —
builds its return value via a fully-enumerated keyword-argument call in
`_get_model_info_helper()` (`litellm/utils.py:5600-5760`) that **does not include these four keys at
all**. Confirmed by direct comparison: `litellm.model_cost["claude-opus-4-7"]["supports_sampling_params"]
== False` and `["supports_output_config"] == True`, but `litellm.get_model_info(model="bedrock/eu.anthropic.claude-opus-4-7")`
has neither key present in its returned dict. The trailing fallback loop in that same function
(`for cost_key, cost_value in _model_info.items(): if cost_key not in returned_model_info and
_ABOVE_THRESHOLD_COST_KEY.search(cost_key) ...`) only rescues cost-related keys matching a
tokens-threshold regex, not capability flags.

This matters for **both** CodeMie code paths named in the task:
- **Direct-provider path** (`core/dependecies.py`): `litellm` is a core, unconditional dependency
  (`pyproject.toml:119`, not behind the `codemie-enterprise` optional extra), so this path *can*
  import `litellm` directly and read `litellm.model_cost[key]` to get the full flag set — but **must
  not** use `litellm.get_model_info()` for `supports_sampling_params`/`supports_output_config`, since
  that call silently returns them as absent for every model checked.
- **Enterprise LiteLLM path**: the proxy's own `/model/info` handler calls
  `get_litellm_model_info()` (`litellm/proxy/proxy_server.py:8479-8490`), which itself calls
  `litellm.get_model_info(model_to_lookup)` — i.e. it inherits the exact same drop. Per Section 9.2's
  already-confirmed override mechanism, the **only** reliable way for the enterprise path to see
  `supports_sampling_params`/`supports_output_config` is a hand-declared `model_info:` block in
  `litellm_config.yaml` (config-declared keys bypass `get_model_info()` entirely and win over it).
  `supported_openai_params` (already used by `map_litellm_to_llm_model`) and `supports_reasoning`/
  `supports_minimal_low_xhigh_max_reasoning_effort` (but not medium/high) do survive the whitelist and
  remain usable as-is from `get_model_info()`.

### 10.2 Full capability table (`litellm.model_cost[key]`, direct raw-dict reads; "absent" ≠ `False`)

| Model (catalog id) | supports_sampling_params | supports_output_config | supports_reasoning | minimal | low | medium | high | xhigh | max |
|---|---|---|---|---|---|---|---|---|---|
| claude-4-5-haiku / `us.anthropic.claude-haiku-4-5-...` | absent | **absent** | True | absent | absent | absent | absent | absent | absent |
| claude-4-5-sonnet / `us.anthropic.claude-sonnet-4-5-...` | absent | **absent** | True | absent | absent | absent | absent | absent | absent |
| claude-sonnet-4-6 / `us.anthropic.claude-sonnet-4-6` | absent | **True** | True | absent | absent | absent | absent | absent | **True** |
| claude-opus-4-5-20251101 / `us.anthropic.claude-opus-4-5-...` | absent | **True** | True | absent | absent | absent | absent | absent | absent |
| claude-opus-4-6-20260205 / `us.anthropic.claude-opus-4-6-v1` | absent | **True** | True | absent | absent | absent | absent | absent | **True** |
| claude-sonnet-5 (proxy, `us.anthropic.claude-sonnet-5`) | **False** | **True** | True | absent | absent | absent | absent | **True** | **True** |
| claude-opus-4-7 (proxy, `eu.anthropic.claude-opus-4-7`) | **False** | **True** | True | absent | absent | absent | absent | **True** | **True** |
| claude-4-5-sonnet vertex eu-west1 (`vertex_ai/claude-sonnet-4-5@20250929`) | absent | absent | True | absent | absent | absent | absent | absent | absent |
| o3-2025-04-16 (azure) | absent | absent | True | absent | absent | absent | absent | absent | absent |
| o4-mini-2025-04-16 (azure) | absent | absent | True | absent | absent | absent | absent | absent | absent |
| o3-mini-2025-01-31 (azure) | absent | absent | True | absent | absent | absent | absent | absent | absent |
| o1-2024-12-17 (azure) | absent | absent | True | absent | absent | absent | absent | absent | absent |
| gpt-5-2025-08-07 / -mini / -nano (azure) | absent | absent | True | True | absent | absent | absent | **False** | absent |
| gpt-5.2-2025-12-11 (azure) | absent | absent | True | **False** | absent | absent | absent | **True** | absent |
| gpt-5.4 (bedrock_mantle/openai.gpt-5.4) | absent | absent | True | **False** | absent | absent | absent | **True** | absent |
| gpt-5.5 (bedrock_mantle) | absent | absent | True | **False** | absent | absent | absent | **True** | absent |
| gpt-5.6-luna (bedrock_mantle) | absent | absent | True | **False** | absent | absent | absent | **True** | **True** |
| gpt-5.1-codex-2025-11-13 (azure) | absent | absent | True | absent | absent | absent | absent | absent | absent |

Additionally, `supports_none_reasoning_effort` (governs whether `temperature` is ever usable, see
10.3): `gpt-5-2025-08-07/-mini/-nano` → explicit `False`; `gpt-5.2-2025-12-11` → explicit `True`;
`gpt-5.4/5.5/5.6-luna`, `gpt-5.1-codex`, `o3`/`o4-mini`/`o3-mini`/`o1` → **absent** (treated as `False`
by the lookup helper's own documented fallback — "Returns False for unknown models").

`medium`/`high` per-level flags are absent for **every** model checked — the `ProviderSpecificModelInfo`
TypedDict (`litellm/types/utils.py:162-166`) itself only declares `minimal`/`low`/`xhigh`/`max`
reasoning-effort flags; `medium` and `high` have no corresponding capability flag anywhere in this
pinned version, for any provider. Treat `medium`/`high` as always-allowed-if-reasoning-is-supported-
at-all, never individually gated.

**Out of scope, one line each (per task instruction, not deeply investigated):** `grok-4.6`,
`moonshotai.kimi-k2.5/k2.6/k2.7`, `qwen.qwen3-coder-*` have **no entry at all** in this pinned
version's `litellm.model_cost` (confirmed by direct lookup — not silently-false, genuinely absent);
`vertex_ai/gemini-3.6-flash` has `supports_reasoning: True`, `supports_sampling_params: None` (present
key, explicit `None`, distinct from "absent").

### 10.3 Exact gating logic — when does temperature get dropped, and is it reasoning-dependent?

**Anthropic (`AnthropicConfig`, `litellm/llms/anthropic/`):** Unconditional, not interaction-gated.
`map_openai_params` (`chat/transformation.py:1396`) handles `temperature`/`top_p` in a dedicated
`elif` branch (line ~1436) that always calls `AnthropicConfig._apply_sampling_param`
(`common_utils.py:278-301`), which checks `_supports_sampling_params(model)`
(`common_utils.py:251-276`: reads `supports_sampling_params` from the model map, falling back to a
name-based `"opus-4-7"`/`"opus-4-8"`/`"fable"` heuristic only when the flag is absent) **regardless of
whether `reasoning_effort`/`thinking` is present in the same call**. The one exception baked into the
same check: `temperature == 1` is always allowed even when the flag is `False`. If unsupported and
neither `litellm.drop_params` nor the per-call `drop_params` kwarg is set, it raises
`litellm.utils.UnsupportedParamsError` (a `BadRequestError` subclass, `exceptions.py:911`).

**GPT-5 family (`GPT5ModelInfo`/`OpenAIGPT5Config`, `litellm/llms/openai/chat/gpt_5_transformation.py:191-300`):**
Reasoning-dependent, and the dependency is explicit in the code (lines 277-294): `temperature` is kept
only if `supports_none_reasoning_effort(model)` is `True` **and** the request's effective
`reasoning_effort` is `"none"` or unset, **or** `temperature == 1` unconditionally. Any other
combination — including a model with `supports_none=False` (base `gpt-5-2025-08-07`/-mini/-nano) no
matter what `reasoning_effort` is sent — drops/raises. So: for `gpt-5.2-2025-12-11`/`gpt-5.4`+ family
(all of which have `supports_none_reasoning_effort` either `True` or at least present), sending both
`temperature` and a non-`"none"` `reasoning_effort` in the same request is what triggers the drop;
for base `gpt-5-2025-08-07` the drop is unconditional regardless of `reasoning_effort`, since
`supports_none` is hard-`False` there. This same function raises `litellm.utils.UnsupportedParamsError`
when unsupported and respects `litellm.drop_params`/`drop_params` (silent-drop path exists here,
unlike the Anthropic effort-level gate below).

**O-series (`o1`/`o3`/`o3-mini`/`o4-mini`, `litellm/llms/openai/chat/o_series_transformation.py:91-113`):**
Unconditional, like Anthropic — `temperature` is always rejected unless `== 1`, independent of any
reasoning-effort kwarg (o-series models have no opt-out "none" level at all).

### 10.4 Does LiteLLM ever raise for an unsupported *effort value* even with `drop_params` set?

**Yes, for Anthropic's `xhigh`/`max` gate — this is the critical asymmetry CodeMie must design
around.** `AnthropicConfig._apply_output_config` (`chat/transformation.py:1955-1988`) is the final
validation before serialization. Its *first* check (line 1965, "model doesn't support `output_config`
at all") does respect `litellm.drop_params`. But its *second* check — `_validate_effort_for_model`
(`chat/transformation.py:345-355`, only gates `effort in ("max","xhigh")` against
`supports_{level}_reasoning_effort`/adaptive-thinking status) — **raises `BadRequestError`
unconditionally, with no `drop_params` escape hatch at all** (line 1981-1987). `low`/`medium`/`high`
are never gated by this function. The GPT-5 path's equivalent check (`xhigh`/`minimal`/`low` gates
inside `map_openai_params`, lines 225-249) **does** respect `drop_params` and silently pops the key
instead. So: Anthropic silently-drops only when the model has zero effort support; it always raises
for a too-high level on a model that has partial effort support. GPT-5 silently drops in both cases
when `drop_params` is set. CodeMie's pinned usage never sets `litellm.drop_params = True` anywhere
(confirmed: no match for `litellm.drop_params\s*=` under `src/`), and no call site inspected passes a
per-call `drop_params=True` kwarg either — so today, every one of these raise paths is live and
reachable, not already neutralized.

### 10.5 Design implications for the two code paths

**Enterprise LiteLLM path** (`src/codemie/enterprise/litellm/models.py:93-100`,
`llm_factory.py:1035-1133`): `LLMFeatures.effort` as a single boolean (current Task 1 design) cannot
express "this model supports `effort` but only up to `high`" (sonnet-4-6/opus-4-5/opus-4-6: `max` only
via `supports_output_config` with no `xhigh`; sonnet-5/opus-4-7: both `xhigh` and `max`). Recommend
widening `LLMFeatures.effort` from `Optional[bool]` to something that preserves a ceiling — e.g. an
`Optional[Literal["low","medium","high","xhigh","max"]]` "max effort level" field (or a
`frozenset`/`list` of allowed levels), populated from `supports_output_config` (gate: must be truthy
for *any* effort support at all — note the model's own `_model_supports_effort_param` treats
`supports_output_config` OR any `supports_*_reasoning_effort` flag as sufficient) plus
`supports_xhigh_reasoning_effort`/`supports_max_reasoning_effort` for the ceiling, sourced from
`get_model_info()`'s surviving fields (`xhigh`/`max` do survive the whitelist per 10.1) — not from
`supported_openai_params` alone, which only proves `reasoning_effort` is forwarded, not which values
succeed. Before calling `get_litellm_chat_model`, clamp/drop the requested `effort` value against this
ceiling in CodeMie's own filter function (mirroring where `_filter_litellm_params` already drops
`temperature`) — this is required to honor "silent filter, never raise" given 10.4's finding that
Anthropic's own unconditional-raise gate for `xhigh`/`max` offers no `drop_params` escape. Also add a
temperature-drop rule in the same filter: when `effort`/`reasoning_effort` is being sent for a model
whose capability data (once sourced per 10.1 — override YAML or a widened detection) shows
`supports_sampling_params is False`, drop `temperature` unconditionally (10.3: Anthropic's drop is
unconditional, not reasoning-presence-gated, so this can be a static per-model rule, not a per-request
interaction check) — same unconditional treatment for o-series. For the GPT-5 family specifically, if
ever wired here, the temperature drop would need to be conditional on `reasoning_effort` actually being
non-`"none"` per 10.3 — a materially different rule from Anthropic's.

**Direct-provider path** (`core/dependecies.py` `get_bedrock_llm`/`get_anthropic_llm`): confirms the
task's flagged Task 3 inaccuracy — **`claude-4-5-haiku` and `claude-4-5-sonnet` must not receive
`features.effort: true`** as currently planned; §10.2 shows both lack `supports_output_config` (absent,
not even `False`) while having `supports_reasoning: True` — i.e. they accept the legacy
`thinking: {type: "enabled", budget_tokens: N}` shape only, never `output_config.effort`, confirming
finding #2 in the task brief exactly. Recommend Task 3 either (a) excludes these two models from the
`effort: true` set entirely (simplest, in-scope fix — they keep working via `temperature` as before,
with no regression), or (b) if "effort" is meant to also mean "configure a thinking budget," give them
a distinct flag name (e.g. `features.thinking_budget: true`) rather than overloading `effort`, since
they share none of the real `output_config.effort` validation/value-set behavior the other three
AWS-catalog Claude models (`claude-sonnet-4-6`, `claude-opus-4-5-20251101`, `claude-opus-4-6-20260205`)
have. The latter three (and the two proxy-only Claude models) do qualify for `features.effort: true`
with the ceiling noted in §10.2 (`claude-opus-4-6-20260205`/`claude-sonnet-4-6`: `max` only, no
`xhigh`; the two proxy models: both `xhigh` and `max`) — if the single-boolean design is kept for
scope reasons rather than widened, document explicitly that the boolean conflates these different
ceilings rather than silently assuming `max`-only. Since this path also constructs the LangChain SDK
client directly (`ChatAnthropic`/`ChatBedrockConverse`), it does not go through
`AnthropicConfig.map_openai_params`'s raise paths at all for the Anthropic SDK branch — but
`ChatBedrockConverse`'s Claude-shape passthrough (per plan Task 3's existing research) reaches the
actual Bedrock/Anthropic API directly, so an out-of-range `effort` value would surface as a live
provider-side 400 rather than a `litellm` exception; the same per-model ceiling clamp recommended above
for the LiteLLM path should also gate this path's `effort` kwarg before constructing the client.
